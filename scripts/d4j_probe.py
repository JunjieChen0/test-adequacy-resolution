#!/usr/bin/env python3
"""
Defects4J 单实例采集器 —— A1 可行性实验

用途：对一个 (项目, 缺陷版本) 完成 checkout → compile → 覆盖率 → 变异测试 → 导出测试清单，
并把结果与耗时写入 JSON，用于 Phase 0 的单实例端到端计时。

设计原则：
  * 每一步独立记录成功/失败与耗时，任何一步失败都不中断整体流程（失败本身是数据）。
  * 输出结构化 JSON，便于后续统计分析。
  * 不依赖外部 Python 包，只用标准库。

用法：
    python3 d4j_probe.py --project Lang --bug 1 --workdir /home/user/d4j-work
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

# ---- 环境：Defects4J 要求 Java 8（旧项目使用 source/target 1.5） ----
D4J_HOME = "/home/user/tools/defects4j"
JAVA8 = "/usr/lib/jvm/java-8-openjdk-amd64"


def d4j_env():
    env = os.environ.copy()
    env["JAVA_HOME"] = JAVA8
    env["PATH"] = f"{JAVA8}/bin:{D4J_HOME}/framework/bin:" + env.get("PATH", "")
    return env


def run(cmd, timeout=None, env=None):
    """执行命令，返回 (returncode, stdout, stderr, 秒数)。不抛异常。"""
    t0 = time.time()
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            env=env or d4j_env(), cwd=os.path.dirname(D4J_HOME) or None,
        )
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        rc, out, err = -9, (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""), "TIMEOUT"
    return rc, out, err, round(time.time() - t0, 1)


def parse_coverage(text):
    """从 defects4j coverage 输出中提取语句/分支覆盖率。"""
    res = {}
    m = re.search(r"Statements:\s*([\d.]+)\s*%?\s*\((\d+)/(\d+)\)", text)
    if m:
        res["stmt_cov_pct"], res["stmt_covered"], res["stmt_total"] = float(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m2 = re.search(r"Statements percent covered:\s*([\d.]+)", text)
        if m2:
            res["stmt_cov_pct"] = float(m2.group(1))
    m = re.search(r"Branches:\s*([\d.]+)\s*%?\s*\((\d+)/(\d+)\)", text)
    if m:
        res["branch_cov_pct"], res["branch_covered"], res["branch_total"] = float(m.group(1)), int(m.group(2)), int(m.group(3))
    return res


def parse_mutation(text):
    """从 defects4j mutation 输出中提取变异分数。"""
    res = {}
    for key, pat in [
        ("mutants_total", r"(?:Number of|Total).*?mutants?.*?(\d+)"),
        ("mutants_killed", r"(?:killed|Killed).*?(\d+)"),
        ("mutants_alive", r"(?:alive|Alive|living).*?(\d+)"),
    ]:
        m = re.search(pat, text, re.I)
        if m:
            res[key] = int(m.group(1))
    m = re.search(r"[Mm]utation score.*?([\d.]+)\s*%", text)
    if m:
        res["mutation_score_pct"] = float(m.group(1))
    res["_raw_tail"] = text.strip().splitlines()[-12:]
    return res


def parse_test_results(text):
    """从 defects4j test 输出中提取失败/通过测试数。"""
    res = {}
    m = re.search(r"Failing tests:\s*(\d+)", text)
    if m:
        res["failing_tests"] = int(m.group(1))
    m = re.search(r"Passing tests:\s*(\d+)", m and text or text)
    if m:
        res["passing_tests"] = int(m.group(1))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--bug", required=True, help="缺陷编号，如 1（脚本会自动加 b 后缀）")
    ap.add_argument("--workdir", default="/home/user/d4j-work")
    ap.add_argument("--outdir", default=os.path.join(os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")), "data"))
    ap.add_argument("--timeout", type=int, default=7200, help="单步超时（秒）")
    args = ap.parse_args()

    bug_id = f"{args.bug}b"
    wd = os.path.join(args.workdir, f"{args.project}-{bug_id}")
    if os.path.exists(wd):
        shutil.rmtree(wd)

    rec = {
        "project": args.project, "bug": bug_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "steps": {},
    }

    def step(name, cmd, timeout=None, parse=None, **kw):
        rc, out, err, secs = run(cmd, timeout=timeout or args.timeout, **kw)
        entry = {"rc": rc, "seconds": secs, "cmd": " ".join(cmd)}
        if rc != 0:
            entry["error"] = (err or out).strip().splitlines()[-3:]
        if parse:
            entry["parsed"] = parse(out)
        rec["steps"][name] = entry
        print(f"  [{name}] rc={rc} {secs}s", flush=True)
        return out

    print(f"=== {args.project}-{bug_id} ===", flush=True)
    step("checkout", ["defects4j", "checkout", "-p", args.project, "-v", bug_id, "-w", wd], timeout=900)
    if not os.path.exists(wd):
        print("  checkout 失败，终止", flush=True)
        _save(rec, args.outdir); return
    step("compile", ["defects4j", "compile", "-w", wd], timeout=1800)
    step("export_tests_trigger", ["defects4j", "export", "-w", wd, "-o", os.path.join(wd, "trigger.txt"), "-f", "tests.trigger"],
         timeout=300)
    step("export_tests_relevant", ["defects4j", "export", "-w", wd, "-o", os.path.join(wd, "relevant.txt"), "-f", "tests.relevant"],
         timeout=300)
    step("export_classes_modified", ["defects4j", "export", "-w", wd, "-o", os.path.join(wd, "modified.txt"), "-f", "classes.modified"],
         timeout=300)
    step("test", ["defects4j", "test", "-w", wd], timeout=args.timeout, parse=parse_test_results)
    step("coverage", ["defects4j", "coverage", "-w", wd], timeout=args.timeout, parse=parse_coverage)
    step("mutation", ["defects4j", "mutation", "-w", wd], timeout=max(args.timeout, 14400), parse=parse_mutation)

    for f in ("trigger.txt", "relevant.txt", "modified.txt"):
        p = os.path.join(wd, f)
        if os.path.exists(p):
            rec[f] = open(p).read().split()
            rec[f + "_count"] = len(rec[f])

    _save(rec, args.outdir)


def _save(rec, outdir):
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"probe-{rec['project']}-{rec['bug']}.json")
    with open(path, "w") as f:
        json.dump(rec, f, indent=2, ensure_ascii=False)
    print(f"结果已写入 {path}", flush=True)


if __name__ == "__main__":
    main()
