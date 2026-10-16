#!/usr/bin/env python3
"""
批量采集器 —— 实例化 A：各版本的开发者套件

对每个 (项目, 缺陷版本) 采集：
  缺陷版本 (b)：测试清单、全类覆盖率、失败测试
  修复版本 (f)：全类变异分数
  二者共用：触发测试集合（用于计算缺陷检出率）

输出：data/instances.jsonl，每行一个套件实例。

设计要点：
  * 幂等：已有结果文件的 (项目, 缺陷) 会跳过，可安全重跑
  * 覆盖率与变异都必须显式传 -i 全类清单（默认只作用于被修改的类，会差 30 倍）
  * 变异在修复版本上做（缺陷版本因失败测试会 BUILD FAILED）

用法：
    python3 collect_all.py --projects Lang --bugs 1 3 4 --workers 2
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

D4J_HOME = "/home/user/tools/defects4j"
D4J_WORK = "/home/user/d4j-work"
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DATA = os.path.join(_ROOT, "data")
JAVA11 = "/usr/lib/jvm/java-11-openjdk-amd64"


def env():
    e = os.environ.copy()
    e.update({
        "JAVA_HOME": JAVA11,
        "MAJOR_ROOT": f"{D4J_HOME}/framework/lib/major",
        "TESTGEN_LIB_DIR": f"{D4J_HOME}/framework/lib/test_generation/generation",
        "BUILD_SYSTEMS_LIB_DIR": f"{D4J_HOME}/framework/lib/build_systems",
    })
    e["PATH"] = f"{JAVA11}/bin:{D4J_HOME}/framework/bin:" + e.get("PATH", "")
    return e


def sh(cmd, timeout):
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env())
        return p.returncode, p.stdout, p.stderr, round(time.time() - t0, 1)
    except subprocess.TimeoutExpired:
        return -9, "", "TIMEOUT", round(time.time() - t0, 1)


def class_list(workdir, cache):
    """生成项目全类清单（缓存），返回路径。"""
    if workdir in cache:
        return cache[workdir]
    src = os.path.join(workdir, "src", "main", "java")
    out = os.path.join(D4J_WORK, f"classes-{os.path.basename(workdir)}.txt")
    classes = []
    for root, _, files in os.walk(src):
        for f in files:
            if f.endswith(".java"):
                full = os.path.join(root, f)
                cls = os.path.relpath(full, src)[:-5].replace(os.sep, ".")
                classes.append(cls)
    with open(out, "w") as fh:
        fh.write("\n".join(sorted(classes)))
    cache[workdir] = out
    return out


def all_classes(workdir):
    src = os.path.join(workdir, "src", "main", "java")
    out = []
    for root, _, files in os.walk(src):
        for f in files:
            if f.endswith(".java"):
                out.append(os.path.relpath(os.path.join(root, f), src)[:-5].replace(os.sep, "."))
    return out


def mut_class_file(workdir, project, k):
    """每项目固定的配对抽样类集，用于变异测试。

    理由：全项目变异成本过高（Lang 全类 >20 分钟 vs 单类 97 秒）。
    所有版本使用同一批类，保证跨版本可比（配对设计），并把成本压到可承受范围。
    缺失的类会被过滤掉，因此实际规模记入结果。
    """
    samp = os.path.join(D4J_WORK, f"mutclasses-{project}.txt")
    if not os.path.exists(samp):
        import random
        allc = sorted(set(all_classes(workdir)))
        random.Random(20261015).shuffle(allc)  # 固定种子，可复现
        with open(samp, "w") as fh:
            fh.write("\n".join(allc[:k]))
    wanted = set(read_lines(samp))
    present = [c for c in all_classes(workdir) if c in wanted]
    out = os.path.join(D4J_WORK, f"mutclasses-{os.path.basename(workdir)}.txt")
    with open(out, "w") as fh:
        fh.write("\n".join(present))
    return out, len(present)


def read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, errors="replace") as fh:
        return [l.strip() for l in fh if l.strip()]


def parse_cov(text):
    d = {}
    for key, pat in [("lines_total", r"Lines total:\s*(\d+)"), ("lines_covered", r"Lines covered:\s*(\d+)"),
                     ("cond_total", r"Conditions total:\s*(\d+)"), ("cond_covered", r"Conditions covered:\s*(\d+)")]:
        m = re.search(pat, text)
        if m:
            d[key] = int(m.group(1))
    m = re.search(r"Line coverage:\s*([\d.]+)%", text)
    if m:
        d["line_cov_pct"] = float(m.group(1))
    m = re.search(r"Condition coverage:\s*([\d.]+)%", text)
    if m:
        d["cond_cov_pct"] = float(m.group(1))
    return d


def parse_mut(text):
    d = {}
    for key, pat in [("mutants_generated", r"Mutants generated:\s*(\d+)"),
                     ("mutants_retained", r"Mutants retained:\s*(\d+)"),
                     ("mutants_covered", r"Mutants covered:\s*(\d+)"),
                     ("mutants_killed", r"Mutants killed:\s*(\d+)")]:
        m = re.search(pat, text)
        if m:
            d[key] = int(m.group(1))
    m = re.search(r"Mutation score:\s*([\d.]+)%\s*\(([\d.]+)%\)", text)
    if m:
        d["mutation_score_pct"], d["mutation_score_covered_pct"] = float(m.group(1)), float(m.group(2))
    return d


def collect_one(project, bug, clscache, mut_k=25, cleanup=False, meta_only=False):
    bug_id = f"{bug}b"
    rec = {"project": project, "bug": bug_id, "t": datetime.now(timezone.utc).isoformat(), "steps": {}}
    wb, wf = f"{D4J_WORK}/{project}-{bug_id}", f"{D4J_WORK}/{project}-{bug}f"

    def step(name, cmd, timeout, parse=None, key=None):
        rc, out, err, secs = sh(cmd, timeout)
        e = {"rc": rc, "s": secs}
        if rc != 0:
            e["err"] = (err or out).strip().splitlines()[-2:]
        if parse:
            e.update(parse(out))
        rec["steps"][name] = e
        return rc, out

    # ---- 缺陷版本：测试清单 + 全类覆盖率 ----
    if not os.path.isdir(wb):
        rc, _ = step("checkout_b", ["defects4j", "checkout", "-p", project, "-v", bug_id, "-w", wb], 600)
        if rc != 0:
            return rec
    if not os.path.exists(os.path.join(wb, "coverage.xml")):
        step("compile_b", ["defects4j", "compile", "-w", wb], 900)
        for prop, fn in [("tests.all", "all.txt"), ("tests.trigger", "trigger.txt"),
                         ("tests.relevant", "relevant.txt"), ("classes.modified", "modified.txt")]:
            rc, _ = step(f"export_{fn}", ["defects4j", "export", "-w", wb, "-p", prop,
                                          "-o", os.path.join(wb, fn)], 300)
            rec[fn] = read_lines(os.path.join(wb, fn)) if rc == 0 else []
        if not meta_only:
            cl = class_list(wb, clscache)
            step("coverage", ["defects4j", "coverage", "-i", cl, "-w", wb], 3600, parse_cov)
    if meta_only:
        rec["n_all_tests"] = len(rec.get("all.txt", []))
        out = os.path.join(DATA, f"instance-{project}-{bug_id}.json")
        with open(out, "w") as fh:
            json.dump(rec, fh, indent=2, ensure_ascii=False)
        if cleanup:
            shutil.rmtree(wb, ignore_errors=True)
        return rec

    # ---- 修复版本：全类变异 ----
    if not os.path.isdir(wf):
        rc, _ = step("checkout_f", ["defects4j", "checkout", "-p", project, "-v", f"{bug}f", "-w", wf], 600)
        if rc == 0:
            step("compile_f", ["defects4j", "compile", "-w", wf], 900)
    if os.path.isdir(wf) and not os.path.exists(os.path.join(wf, "kill.csv")):
        # 变异限定在每项目固定的配对抽样类集上（成本控制，见 mut_class_file 说明）
        cl, n_cls = mut_class_file(wf, project, mut_k)
        rec["n_mut_classes"] = n_cls
        step("mutation", ["defects4j", "mutation", "-i", cl, "-w", wf], 14400, parse_mut)

    rec["n_all_tests"] = len(rec.get("all.txt", []))
    out = os.path.join(DATA, f"instance-{project}-{bug_id}.json")
    with open(out, "w") as fh:
        json.dump(rec, fh, indent=2, ensure_ascii=False)
    if cleanup:  # 396 个 checkout 会占用几十 GB，采完即删
        for d in (wb, wf):
            shutil.rmtree(d, ignore_errors=True)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", nargs="+", required=True)
    ap.add_argument("--bugs", nargs="*", default=None, help="限定缺陷号；省略则全部")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--mut-classes", type=int, default=25,
                    help="每项目用于变异测试的抽样类数（配对设计，跨版本固定）")
    ap.add_argument("--cleanup", action="store_true", help="采集完删除 checkout 目录以节省磁盘")
    ap.add_argument("--meta-only", action="store_true",
                    help="只采集测试清单与触发测试元数据（快，用于覆盖全部版本）；跳过覆盖率与变异")
    a = ap.parse_args()
    os.makedirs(DATA, exist_ok=True)

    jobs = []
    for p in a.projects:
        bugs = a.bugs
        if not bugs:
            rc, out, _, _ = sh(["defects4j", "bids", "-p", p], 120)
            bugs = out.split()
        for b in bugs:
            if os.path.exists(os.path.join(DATA, f"instance-{p}-{b}b.json")):
                print(f"跳过 {p}-{b}b（已完成）", flush=True)
                continue
            jobs.append((p, b))
    print(f"待采集 {len(jobs)} 个版本，并行度 {a.workers}", flush=True)

    clscache = {}
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(collect_one, p, b, clscache, a.mut_classes, a.cleanup, a.meta_only): (p, b) for p, b in jobs}
        for i, f in enumerate(as_completed(futs), 1):
            p, b = futs[f]
            try:
                r = f.result()
                s = r.get("steps", {})
                print(f"[{i}/{len(jobs)}] {p}-{b}b 完成  cov={s.get('coverage',{}).get('line_cov_pct')}% "
                      f"mut={s.get('mutation',{}).get('mutation_score_pct')}%  "
                      f"tests={r.get('n_all_tests')}", flush=True)
            except Exception as e:
                print(f"[{i}/{len(jobs)}] {p}-{b}b 异常: {e}", flush=True)
    print("全部完成", flush=True)


if __name__ == "__main__":
    main()
