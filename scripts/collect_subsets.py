#!/usr/bin/env python3
"""
子集采集器 —— 构造不同规模的测试子集，逐个测量指标与检出能力

关键机制（本脚本的存在理由）：
  Defects4J 的 coverage / mutation 都不支持传入测试清单，且 all_tests 会被框架重写。
  但项目构建文件里的 fileset `all.manual.tests` 是对**测试源码目录**的
  <include name="**/*Test.java"/>。因此把不参与子集的测试源码移出测试目录，
  即可让框架只运行子集 —— 实测有效（Lang-11b：10% 子集覆盖率 20.6% vs 全量 94.5%）。

三处必须正确处理的细节（均由实测踩坑得出）：
  1. `Abstract*Test.java` 等基类文件名同样以 Test.java 结尾，但会被其它测试继承，
     移走会导致残留测试编译失败 → 需读文件内容排除 abstract class / interface。
  2. 测试类之间会互相引用（如 XxxTest 使用 YyyTest 的常量）→ 抽样后必须做
     **依赖闭包**，把被引用到的测试类一并保留，否则编译失败。闭包会让实际保留数
     超过目标比例，故记录实际数量而非目标数量。
  3. 缺陷版本上若子集包含触发测试，测试会失败导致 Major 中止（haltonfailure=true）
     → 变异分数一律在**修复版本**上、用**同一个子集**测量。

每个实例 = (项目, 基准版本, 子集比例, 重复编号)。

用法：
    python3 collect_subsets.py --project Lang --bugs 1 16 32 --fracs 0.05 0.1 0.25 0.5 1.0 --reps 2 --workers 2
"""
import argparse
import json
import os
import random
import re
import shutil
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

D4J_HOME = "/home/user/tools/defects4j"
# 工作目录可用环境变量覆盖，便于在其他机器上复现
WORK = os.environ.get("D4J_WORK", os.path.expanduser("~/d4j-work"))
SUBSET_WORK = "/home/user/d4j-work/subsets"
STASH = "/home/user/d4j-work/stash"
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DATA = os.path.join(_ROOT, "data", "subsets")
JAVA11 = "/usr/lib/jvm/java-11-openjdk-amd64"

sys_env = os.environ.copy()
sys_env.update({
    "JAVA_HOME": JAVA11,
    "JAVA_TOOL_OPTIONS": "-Djava.awt.headless=true -Xmx1400m",  # Chart 的 AWT + 限制堆大小防 OOM
    "MAJOR_ROOT": f"{D4J_HOME}/framework/lib/major",
    "TESTGEN_LIB_DIR": f"{D4J_HOME}/framework/lib/test_generation/generation",
    "BUILD_SYSTEMS_LIB_DIR": f"{D4J_HOME}/framework/lib/build_systems",
})
sys_env["PATH"] = f"{JAVA11}/bin:{D4J_HOME}/framework/bin:" + sys_env.get("PATH", "")


def sh(cmd, timeout):
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=sys_env)
        return p.returncode, p.stdout, p.stderr, round(time.time() - t0, 1)
    except subprocess.TimeoutExpired:
        return -9, "", "TIMEOUT", round(time.time() - t0, 1)


def read_lines(p):
    return [l.strip() for l in open(p, errors="replace")] if os.path.exists(p) else []


PROJ = f"{D4J_HOME}/framework/projects"


def layout_of(project, bug, which="b"):
    """返回该版本的主源码目录与测试目录（相对 checkout 根）。

    不同项目、甚至同一项目的不同版本，目录布局都不同：
      Lang  : src/main/java, src/test/java
      Chart : source,        tests
    代码里写死布局会在 Chart 上直接失败（测试类数 0）。Defects4J 提供了
    commit-db（bug -> buggy/fixed commit）与 dir-layout.csv（commit -> 目录），
    必须据此查表。
    """
    default = ("src/main/java", "src/test/java")
    try:
        h = {}
        p = os.path.join(PROJ, project, "commit-db")
        if os.path.exists(p):
            for line in open(p):
                parts = [x.strip() for x in line.split(",")]
                if len(parts) >= 3 and parts[0].isdigit():
                    h[parts[0]] = (parts[1], parts[2])
        dl = {}
        p = os.path.join(PROJ, project, "dir-layout.csv")
        if os.path.exists(p):
            for line in open(p):
                parts = [x.strip() for x in line.split(",")]
                if len(parts) >= 3:
                    dl[parts[0]] = (parts[1], parts[2])
        commit = h.get(str(bug), ("", ""))[0 if which == "b" else 1]
        return dl.get(commit, default)
    except OSError:
        return default


def fileset_patterns(project):
    """从项目构建文件里解析 all.manual.tests 的 include / exclude 模式。

    各项目模式完全不同，写死任何一个都会在别的项目上失败：
      Lang  **/*Test.java
      Chart **/*Tests.java          （复数）且排除 **/*Package*.java
      Math  **/*Test.java + **/*TestBinary.java + **/*TestPermutations.java
      Time  **/Test*.java
    解析不到时退回宽松模式，再由内容检查兜底。
    """
    inc, exc = [], []
    p = os.path.join(PROJ, project, f"{project}.build.xml")
    if not os.path.exists(p):
        return ["**/*Test*.java"], []
    txt = open(p, errors="replace").read()
    m = re.search(r'<fileset\s+id="all\.manual\.tests".*?</fileset>', txt, re.S)
    block = m.group(0) if m else ""
    for tag, lst in (("include", inc), ("exclude", exc)):
        # 注意引号风格不统一：Collections 用单引号 <include name='...'/>，
        # 只匹配双引号会让它的模式解析失败并退回宽松模式（已踩过）。
        for im in re.finditer(rf"<{tag}\s+name=['\"]([^'\"]+)['\"]", block):
            v = im.group(1)
            # 跳过含 Ant 变量或文件后缀的条目（如 '${test.entry}'、'**/*$*'）
            if "$" not in v:
                lst.append(v)
    return (inc or ["**/*Test*.java"]), exc


def _match(rel, pats):
    import fnmatch
    rel = rel.replace(os.sep, "/")
    base = rel.rsplit("/", 1)[-1]
    for pat in pats:
        q = pat.replace("**/", "")
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, q) or fnmatch.fnmatch(base, q):
            return True
    return False


def test_sources(wd, test_dir="src/test/java", project=None):
    """可独立运行的测试类源码相对路径。

    排除抽象基类与接口：它们会被其它测试继承，移走会导致残留测试编译失败
    （实测：Lang 的 Abstract*Test.java 只按文件名筛会误移，必须读内容）。
    """
    base = os.path.join(wd, test_dir)
    out = []
    if not os.path.isdir(base):
        return out
    inc, exc = fileset_patterns(project) if project else (["**/*Test.java"], [])
    for root, _, files in os.walk(base):
        for f in files:
            if not f.endswith(".java"):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, base)
            if not _match(rel, inc) or _match(rel, exc):
                continue
            try:
                head = open(full, errors="replace").read(4000)
            except OSError:
                continue
            if re.search(r"\babstract\s+class\b", head) or re.search(r"\binterface\s+\w+", head):
                continue
            out.append(rel)
    return sorted(out)


def all_test_java(wd, test_dir="src/test/java"):
    """测试目录下所有 .java（含被 fileset 排除的聚合类与基类）。

    必须与之打交道的原因：Chart 的 `*PackageTests.java` 被构建文件的 fileset
    排除（因此不会被运行），但**仍然会被编译**，且引用了同包内的全部测试。
    只移动 fileset 内的文件，这些聚合类就会因找不到被移走的类而编译失败。
    """
    base = os.path.join(wd, test_dir)
    out = []
    if not os.path.isdir(base):
        return out
    for root, _, files in os.walk(base):
        for f in files:
            if f.endswith(".java"):
                out.append(os.path.relpath(os.path.join(root, f), base))
    return sorted(out)


def dep_closure(wd, keep, test_dir="src/test/java"):
    """把 keep 扩充为对"测试类引用"封闭的集合（在全部 .java 上计算）。

    注意：必须用**实际类名**去匹配，不能用固定正则。Chart 的测试类命名是复数
    *Tests，而 Lang 是单数 *Test —— 固定正则在 Chart 上一个引用都抓不到。
    """
    base = os.path.join(wd, test_dir)
    allj = all_test_java(wd, test_dir)
    simple = {os.path.basename(p)[:-5]: p for p in allj}
    refs = {}
    for p in allj:
        try:
            txt = open(os.path.join(base, p), errors="replace").read()
        except OSError:
            refs[p] = set()
            continue
        me = os.path.basename(p)[:-5]
        refs[p] = {simple[n] for n in simple
                   if n != me and re.search(r"\b" + re.escape(n) + r"\b", txt)}
    keep = set(keep)
    changed = True
    while changed:
        changed = False
        for p in list(keep):
            for q in refs.get(p, ()):
                if q not in keep:
                    keep.add(q)
                    changed = True
    return keep


def apply_subset(wd, tests, keep, tag, test_dir="src/test/java"):
    """把不参与子集的测试源码移入暂存区，返回实际移走数量。"""
    base = os.path.join(wd, test_dir)
    st = os.path.join(STASH, tag)
    n = 0
    for rel in tests:
        if rel not in keep:
            dst = os.path.join(st, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(os.path.join(base, rel), dst)
            n += 1
    return n


MISSING_RE = re.compile(
    r"(?:symbol:\s*(?:class|variable|method|interface)\s+(\w+)"
    r"|package\s+(\w[\w.]*)\s+does not exist)")


def restore_missing(wd, test_dir, tag, output, max_iter=5):
    """按 javac 报的缺失符号，从暂存区把对应源文件补回，直到编译通过。

    比单纯按引用做依赖闭包更稳：测试类之间的依赖有多种形态，静态分析总会漏
    （实例：Lang-64b 的 ValuedEnumTest 引用了**另一个包**里的 ValuedColorEnum，
    按简单类名匹配不到）。让编译器来告诉我们缺什么，是最可靠的判据。

    返回 (是否编译通过, 额外补回的类数, 每轮缺失符号记录)。
    """
    base = os.path.join(wd, test_dir)
    st = os.path.join(STASH, tag)
    if not os.path.isdir(st):
        return True, 0, []
    added_total, log = 0, []
    out = output if isinstance(output, str) else ""
    for _ in range(max_iter):
        names = set()
        for m in MISSING_RE.finditer(out):
            for g in m.groups():
                if g:
                    names.add(g.split(".")[-1])
        if not names:
            break
        added = 0
        for root, _, files in os.walk(st):
            for f in files:
                if f.endswith(".java") and f[:-5] in names:
                    src_p = os.path.join(root, f)
                    rel = os.path.relpath(src_p, st)
                    dst_p = os.path.join(base, rel)
                    os.makedirs(os.path.dirname(dst_p), exist_ok=True)
                    shutil.move(src_p, dst_p)
                    added += 1
        log.append(sorted(names))
        if added == 0:
            break
        added_total += added
        rc, o, e, _ = sh(["defects4j", "compile", "-w", wd], 900)
        if rc == 0:
            return True, added_total, log
        out = (e or "") + (o or "")
    return False, added_total, log


def all_main_classes(wd, src_dir="src/main/java"):
    src = os.path.join(wd, src_dir)
    out = []
    if not os.path.isdir(src):
        return out
    for root, _, files in os.walk(src):
        for f in files:
            if f.endswith(".java"):
                out.append(os.path.relpath(os.path.join(root, f), src)[:-5].replace(os.sep, "."))
    return sorted(out)


def parse_cov(t):
    d = {}
    for k, p in [("lines_total", r"Lines total:\s*(\d+)"), ("lines_covered", r"Lines covered:\s*(\d+)"),
                 ("cond_total", r"Conditions total:\s*(\d+)"), ("cond_covered", r"Conditions covered:\s*(\d+)")]:
        m = re.search(p, t)
        if m:
            d[k] = int(m.group(1))
    m = re.search(r"Line coverage:\s*([\d.]+)%", t)
    if m:
        d["line_cov_pct"] = float(m.group(1))
    m = re.search(r"Condition coverage:\s*([\d.]+)%", t)
    if m:
        d["cond_cov_pct"] = float(m.group(1))
    return d


def parse_mut(t):
    d = {}
    for k, p in [("mutants_generated", r"Mutants generated:\s*(\d+)"), ("mutants_covered", r"Mutants covered:\s*(\d+)"),
                 ("mutants_killed", r"Mutants killed:\s*(\d+)")]:
        m = re.search(p, t)
        if m:
            d[k] = int(m.group(1))
    m = re.search(r"Mutation score:\s*([\d.]+)%\s*\(([\d.]+)%\)", t)
    if m:
        d["mutation_score_pct"], d["mutation_score_covered_pct"] = float(m.group(1)), float(m.group(2))
    return d


def mut_class_file(wd, project, k, src_dir="src/main/java"):
    """每项目固定的配对抽样类集（与 collect_all.py 同种子，保证跨实例可比）。

    例外：老版本可能整体改名（Lang 的旧版本包名是 org.apache.commons.lang，
    新版本是 org.apache.commons.lang3），与缓存样本交集为空，会导致变异类清单为空、
    报 "Cannot mutate project!"（已踩过）。此时退回"从本版本自身的类里重新抽样"，
    并记录所用来源，便于在威胁效度中说明配对范围。
    """
    samp = os.path.join(WORK, f"mutclasses-{project}.txt")
    allc = all_main_classes(wd, src_dir)
    if not os.path.exists(samp) and allc:
        random.Random(20261015).shuffle(allc)
        with open(samp, "w") as fh:
            fh.write("\n".join(sorted(allc[:k])))
    wanted = set(read_lines(samp))
    present = [c for c in allc if c in wanted]
    source = "cached"
    if len(present) < max(3, k // 3):
        # 交集过小（版本改名/重构）→ 从本版本自身抽样，保证有类可变异
        fresh = list(allc)
        random.Random(f"{project}|{src_dir}").shuffle(fresh)
        present = sorted(fresh[:k])
        source = "fresh"
    f = os.path.join(WORK, f"mutcls-{os.path.basename(wd)}.txt")
    with open(f, "w") as fh:
        fh.write("\n".join(present))
    return f, source


def is_done(out):
    """只有成功完成的实例才算已完成。

    不能只看文件是否存在：失败的实例也会留下记录文件，若按其存在跳过，就永远
    不会被重试（已踩过：Lang 56b/64b 的失败记录阻塞了续跑）。
    """
    if not os.path.exists(out):
        return False
    try:
        return json.load(open(out)).get("status") == "ok"
    except (OSError, ValueError):
        return False


def one_instance(project, bug, frac, rep, mut_k):
    tag = f"{project}-{bug}b-f{frac}-r{rep}"
    out = os.path.join(DATA, f"{tag}.json")
    if is_done(out):
        return tag, "skip"
    # 工作目录名带随机后缀：被强杀（沙箱重置会杀掉所有进程）留下的残留目录会让
    # defects4j checkout 报 "Directory exists but is not a previously used working
    # directory"（已踩过）。用唯一目录名从根上避免冲突。
    token = uuid.uuid4().hex[:6]
    wb = os.path.join(SUBSET_WORK, f"{tag}-wb-{token}")
    wf = os.path.join(SUBSET_WORK, f"{tag}-wf-{token}")
    # 暂存目录名必须与工作目录后缀一致：修复函数要靠它找回被移走的源文件，
    # 两者不一致会让修复静默跳过（repair_added=0 却报成功），进而编译失败。已踩过。
    stab_b = f"{tag}-wb-{token}"
    stab_f = f"{tag}-wf-{token}"
    for d in (wb, wf):
        shutil.rmtree(d, ignore_errors=True)
    rec = {"project": project, "bug": f"{bug}b", "frac": frac, "rep": rep,
           "t": datetime.now(timezone.utc).isoformat(), "steps": {}}

    def step(name, cmd, timeout, parse=None):
        rc, o, e, s = sh(cmd, timeout)
        d = {"rc": rc, "s": s}
        if rc != 0:
            d["err"] = (e or o).strip().splitlines()[-3:]
        if parse:
            d.update(parse(o))
        rec["steps"][name] = d
        return rc

    try:
        # ---------- 缺陷版本：构造子集 + 覆盖率 ----------
        if step("checkout_b", ["defects4j", "checkout", "-p", project, "-v", f"{bug}b", "-w", wb], 600) != 0:
            return tag, "checkout_b失败"
        src_b, test_b = layout_of(project, bug, "b")
        rec["layout_b"] = {"src": src_b, "test": test_b}
        tests_run = test_sources(wb, test_b, project)   # 会被实际运行的测试
        tests_all = all_test_java(wb, test_b)           # 全部 .java（含要编译的聚合类）
        n = len(tests_run)
        rec["n_test_total"] = n
        rec["n_test_java_total"] = len(tests_all)
        if n == 0:
            rec["status"] = "找不到测试源码"
            return tag, "找不到测试源码"
        target = n if frac >= 1.0 else max(1, round(n * frac))
        rng = random.Random(f"{project}{bug}{frac}{rep}")
        keep = dep_closure(wb, rng.sample(tests_run, target), test_b)
        rec.update(n_test_target=target, n_test_kept=len(keep & set(tests_run)),
                   n_keep_java=len(keep), kept_tests=sorted(keep & set(tests_run)))
        rec["n_moved_b"] = apply_subset(wb, tests_all, keep, stab_b, test_b)
        rc_b, out_b, err_b, s_b = sh(["defects4j", "compile", "-w", wb], 900)
        rec["steps"]["compile_b"] = {"rc": rc_b, "s": s_b}
        if rc_b != 0:
            ok, added, log = restore_missing(wb, test_b, stab_b, err_b + out_b)
            rec["steps"]["compile_b"]["repair_added"] = added
            rec["steps"]["compile_b"]["repair_log"] = log
            if not ok:
                rec["steps"]["compile_b"]["err"] = (err_b or out_b).strip().splitlines()[-3:]
                return tag, "编译失败(b)"
            rec["steps"]["compile_b"]["rc_after_repair"] = 0
            rec["n_repaired_b"] = added
        # 修复补回的类里可能包含会被实际运行的测试，必须重新记录最终套件
        final_b = test_sources(wb, test_b, project)
        rec["kept_tests_final"] = final_b
        rec["n_test_final"] = len(final_b)
        cl = os.path.join(WORK, f"allcls-{tag}.txt")
        with open(cl, "w") as fh:
            fh.write("\n".join(all_main_classes(wb, src_b)))
        step("coverage", ["defects4j", "coverage", "-i", cl, "-w", wb], 3600, parse_cov)

        # ---------- 修复版本：同一子集 + 变异分数 ----------
        if step("checkout_f", ["defects4j", "checkout", "-p", project, "-v", f"{bug}f", "-w", wf], 600) != 0:
            return tag, "checkout_f失败"
        src_f, test_f = layout_of(project, bug, "f")
        tests_all_f = all_test_java(wf, test_f)
        keep_f = keep & set(tests_all_f)
        rec["n_keep_java_f"] = len(keep_f)
        rec["n_moved_f"] = apply_subset(wf, tests_all_f, keep_f, stab_f, test_f)
        rc_f, out_f, err_f, s_f = sh(["defects4j", "compile", "-w", wf], 900)
        rec["steps"]["compile_f"] = {"rc": rc_f, "s": s_f}
        if rc_f != 0:
            ok, added, _ = restore_missing(wf, test_f, stab_f, err_f + out_f)
            rec["steps"]["compile_f"]["repair_added"] = added
            if not ok:
                rec["steps"]["compile_f"]["err"] = (err_f or out_f).strip().splitlines()[-3:]
                return tag, "编译失败(f)"
            rec["n_repaired_f"] = added
        rec["kept_tests_final_f"] = test_sources(wf, test_f, project)
        mc, msrc = mut_class_file(wf, project, mut_k, src_f)
        rec["n_mut_classes"] = len(read_lines(mc))
        rec["mut_sample_source"] = msrc
        step("mutation", ["defects4j", "mutation", "-i", mc, "-w", wf], 14400, parse_mut)
        # 只有真正拿到两个指标才算成功；否则标记为不完整，供续跑重试。
        # （教训：早先只判断"流程跑完"，导致无指标的记录被当作完成而永久跳过。）
        cov_pct = rec["steps"].get("coverage", {}).get("line_cov_pct")
        mut_pct = rec["steps"].get("mutation", {}).get("mutation_score_pct")
        rec["status"] = "ok" if (cov_pct is not None and mut_pct is not None) else "incomplete"
        return tag, "完成" if rec["status"] == "ok" else "指标不全"
    finally:
        with open(out, "w") as fh:
            json.dump(rec, fh, indent=2, ensure_ascii=False)
        for d in (wb, wf, os.path.join(STASH, tag + "-wb"), os.path.join(STASH, tag + "-wf")):
            shutil.rmtree(d, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--bugs", nargs="+", required=True)
    ap.add_argument("--fracs", nargs="+", type=float, default=[0.05, 0.1, 0.25, 0.5, 1.0])
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--mut-classes", type=int, default=15)
    a = ap.parse_args()

    os.makedirs(DATA, exist_ok=True)
    jobs = [(b, f, r) for b in a.bugs for f in a.fracs for r in range(1, (1 if f >= 1.0 else a.reps) + 1)]
    print(f"待采集 {len(jobs)} 个子集实例，并行度 {a.workers}", flush=True)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(one_instance, a.project, b, f, r, a.mut_classes): (b, f, r) for b, f, r in jobs}
        for i, fut in enumerate(as_completed(futs), 1):
            b, f, r = futs[fut]
            try:
                tag, st = fut.result()
                print(f"[{i}/{len(jobs)}] {tag}: {st}", flush=True)
            except Exception as e:
                print(f"[{i}/{len(jobs)}] {b}-{f}-{r} 异常: {e}", flush=True)
    print("全部完成", flush=True)


if __name__ == "__main__":
    main()
