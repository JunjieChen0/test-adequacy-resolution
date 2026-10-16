#!/usr/bin/env python3
"""
BugsInPy 测试子集采集器（跨基准复现用）

与 Defects4J 那套（scripts/collect_subsets.py）的对应关系
--------------------------------------------------------
| 要素     | Defects4J                      | BugsInPy（本脚本）                |
|---------|--------------------------------|----------------------------------|
| 套件定义  | 项目 test 目录下的全部 .java      | pytest 收集到的全部测试节点          |
| 子集构造  | 把不参与的测试源码移出测试目录       | 直接把选中的节点列表传给 pytest      |
| 覆盖率    | defects4j coverage -i <类列表>   | coverage run --source=<包>        |
| 检出率    | 触发测试是否落在子集内             | 同左（触发测试来自 run_test.sh）      |
| 基准版本  | fixed（缺陷版上变异测试会中止）      | fixed（保证套件可通过）              |

两项工程决定
------------
1. **不调用框架的 checkout**。`bugsinpy-checkout` 每个缺陷都做一次完整 `git clone`，
   实测 youtube-dl 单缺陷约 10 分钟。本脚本改为每项目只克隆一次，
   之后用 `git reset --hard <fixed_commit>` 在同一个工作树上切换，单缺陷降到秒级。
2. **断言特征在采集时就地计算**，避免事后依赖每个缺陷的工作树。

用法：
    python3 scripts/bip_collect.py --project youtube-dl --fracs 0.1 0.25
"""
import argparse
import glob
import json
import os
import random
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
BIP = "/home/user/tools/bugsinpy"
PY = "/home/user/tools/bip38/bin/python"
COV = "/home/user/tools/bip38/bin/coverage"
OUT = os.path.join(ROOT, "data-bip")
META = os.path.join(OUT, "meta")

sys.path.insert(0, os.path.dirname(__file__))
from bip_assertions import scan_file  # noqa: E402


PYTHON = PY          # 可被 --python 覆盖（用于依赖冲突的项目各自建 venv）
COVERAGE = COV


def sh(cmd, cwd=None, timeout=1800):
    e = dict(os.environ)
    e["PATH"] = f"{os.path.dirname(PYTHON)}:{e.get('PATH','')}"
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        return subprocess.run(cmd, cwd=cwd, timeout=timeout, env=e,
                              capture_output=True, text=True, errors="ignore")
    except subprocess.TimeoutExpired:
        return None


# ---------------------------------------------------------------- 工作树

def github_url(project):
    p = os.path.join(BIP, "projects", project, "project.info")
    for line in open(p, errors="ignore"):
        m = re.match(r'github_url\s*=\s*"([^"]*)"', line.strip())
        if m:
            return m.group(1)
    return None


def ensure_repo(project):
    repo = os.path.join(OUT, "repo", project)
    if os.path.isdir(os.path.join(repo, ".git")):
        return repo
    url = github_url(project)
    os.makedirs(os.path.dirname(repo), exist_ok=True)
    r = sh(["git", "clone", url, repo], timeout=3600)
    if r is None or not os.path.isdir(os.path.join(repo, ".git")):
        return None
    return repo


def prepare_tree(project, bug, commit, test_files):
    """把共享工作树重置到指定提交，并写回该缺陷的测试文件。"""
    wt = os.path.join(OUT, "work", project)
    if not os.path.isdir(wt):
        r = sh(["git", "clone", os.path.join(OUT, "repo", project), wt], timeout=1800)
        if r is None or not os.path.isdir(os.path.join(wt, ".git")):
            return None
    sh(["git", "reset", "--hard", commit], cwd=wt, timeout=600)
    sh(["git", "clean", "-fdq", "-e", ".bip_*"], cwd=wt, timeout=600)
    for tf in test_files:
        src = os.path.join(BIP, "projects", project, "bugs", str(bug), tf)
        if os.path.exists(src):
            dst = os.path.join(wt, tf)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil_copy(src, dst)
    return wt


def shutil_copy(src, dst):
    import shutil
    shutil.copyfile(src, dst)


def source_packages(root):
    """覆盖率统计范围：项目自身的包目录 + 顶层模块。

    顶层模块不能漏：black 的主体就是 `black.py` 这个顶层模块（不是包目录），
    只按目录取会导致覆盖率恒为 0。
    """
    pkgs, skip = [], {"test", "tests", "docs", "devscripts", "examples",
                      "example", "benchmarks", "scripts", "bin", "build"}
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if d.startswith(".") or d.lower() in skip:
            continue
        if os.path.isdir(p):
            if os.path.exists(os.path.join(p, "__init__.py")) or \
               glob.glob(os.path.join(p, "*.py")):
                pkgs.append(d)
        elif d.endswith(".py") and d not in ("setup.py", "conftest.py"):
            pkgs.append(d[:-3])
    return pkgs


# ---------------------------------------------------------------- pytest

NODE_RE = re.compile(r"^(\S+\.py)::(\S+?)\s+(PASSED|FAILED|ERROR|XFAIL|XPASS|SKIPPED)")


def expand_only(root, only):
    """把目录展开成显式 .py 文件路径。

    必要性：pytest 递归目录时按 python_files（默认 test_*.py / *_test.py）过滤，
    tqdm 的测试文件叫 tests_tqdm.py 之类，直接传目录会一个都收不到。
    显式传文件则不受该过滤影响。
    """
    files = []
    for p in only:
        ap = os.path.join(root, p)
        if os.path.isdir(ap):
            for dirpath, _, names in os.walk(ap):
                for n in names:
                    # conftest/__init__ 这类文件不是测试模块，显式传给 pytest
                    # 会改变它的加载方式，因此排除在外
                    if n.endswith(".py") and not n.startswith("__") \
                            and not n.startswith("conftest"):
                        files.append(os.path.relpath(os.path.join(dirpath, n), root))
        else:
            files.append(p)
    return sorted(set(files))


def collect_nodes(root, ignore=(), only=()):
    cmd = [PYTHON, "-m", "pytest", "--collect-only", "-q", "--no-header",
           "-p", "no:cacheprovider"]
    cmd += list(only) if only else [f"--ignore={i}" for i in ignore]
    r = sh(cmd, cwd=root, timeout=900)
    if r is None:
        return []
    return [l.strip() for l in (r.stdout + r.stderr).splitlines()
            if "::" in l and not l.startswith(("<", "=", "!")) and " " not in l.strip()]


def passing_nodes(root, nodes, per_test_timeout=15):
    if not nodes:
        return []
    nf = os.path.join(root, ".bip_nodes.txt")
    open(nf, "w").write("\n".join(nodes))
    cmd = [PYTHON, "-m", "pytest", "-v", "--tb=no", "-p", "no:cacheprovider",
           f"--timeout={per_test_timeout}", f"@{nf}"]
    r = sh(cmd, cwd=root, timeout=5400)
    if r is None:
        return []
    out = []
    for line in (r.stdout + r.stderr).splitlines():
        m = NODE_RE.match(line.strip())
        if m and m.group(3) in ("PASSED", "XFAIL", "XPASS"):
            out.append(f"{m.group(1)}::{m.group(2)}")
    return out


def run_subset(root, sample, frac, rep, src):
    nf = os.path.join(root, f".bip_sub_{frac}_{rep}.txt")
    open(nf, "w").write("\n".join(sample))
    covfile = os.path.join(root, f".bip_cov_{frac}_{rep}")
    rm = sh([COVERAGE, "run", f"--data-file={covfile}", f"--source={src}",
             "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider",
             "--timeout=30", f"@{nf}"], cwd=root, timeout=5400)
    if rm is None:
        return {"status": "timeout"}
    cj = os.path.join(root, f".bip_cov_{frac}_{rep}.json")
    sh([COVERAGE, "json", f"--data-file={covfile}", "-o", cj], cwd=root, timeout=900)
    line_cov = None
    if os.path.exists(cj):
        d = json.load(open(cj))
        tot = sum(v["summary"]["num_statements"] for v in d["files"].values())
        cov = sum(v["summary"]["covered_lines"] for v in d["files"].values())
        line_cov = 100.0 * cov / tot if tot else None
    tail = (rm.stdout + rm.stderr).strip().splitlines()
    tail = tail[-1] if tail else ""
    m = re.search(r"(\d+) passed", tail)
    return {"status": "ok", "line_cov_pct": line_cov,
            "n_pass": int(m.group(1)) if m else None, "tail": tail[:160]}


# ---------------------------------------------------------------- 特征

def aggregate_features(nodes, root, cache):
    """把逐测试方法的断言特征按子集聚合。"""
    tot_a = tot_c = tot_p = strong = weak = 0
    n_methods = 0
    for node in nodes:
        f, _, tail = node.partition("::")
        if f not in cache:
            cache[f] = scan_file(os.path.join(root, f), f)
        fd = cache[f].get(tail)
        if not fd or fd["n_assert"] == 0:
            continue
        n_methods += 1
        tot_a += fd["n_assert"]
        tot_c += fd["calls"]
        tot_p += fd["pred"]
        strong += fd["strong"]
        weak += fd["weak"]
    if not tot_a:
        return None
    return {
        "assertions_per_method": tot_a / len(nodes) if nodes else 0.0,
        "strong_ratio": strong / tot_a,
        "weak_ratio": weak / tot_a,
        "predicate_ratio": tot_p / tot_a,
        "calls_per_assertion": tot_c / tot_a,
        "n_assert_total": tot_a,
        "n_methods_with_assert": n_methods,
    }


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--bugs", nargs="+")
    ap.add_argument("--fracs", nargs="+", type=float, default=[0.1, 0.25])
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--ignore", nargs="*", default=[])
    ap.add_argument("--only", nargs="*", default=[],
                    help="只用这些测试路径（比 --ignore 更快，且避免网络用例拖慢池筛选）")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--python", default=PY, help="该项目使用的解释器（可指向独立 venv）")
    a = ap.parse_args()

    global PYTHON, COVERAGE
    PYTHON = a.python
    COVERAGE = os.path.join(os.path.dirname(PYTHON), "coverage")
    if not os.path.exists(COVERAGE):
        COVERAGE = "/home/user/tools/bip38/bin/coverage"
    proj = a.project
    meta = json.load(open(os.path.join(META, f"{proj}.json")))
    trig = {}
    for bid, b in meta["bugs"].items():
        keys = {f"{t['file'].lstrip('./')}::{t['node']}" if t["node"]
                else t["file"].lstrip("./")
                for t in b["triggers"] if t["file"]}
        if keys:
            trig[bid] = keys
    bugs = a.bugs or sorted(trig, key=int)

    if not ensure_repo(proj):
        print(f"{proj}: 仓库克隆失败")
        return
    os.makedirs(os.path.join(OUT, "subsets"), exist_ok=True)

    for bug in bugs:
        base = os.path.join(OUT, "subsets", f"{proj}-{bug}.json")
        if os.path.exists(base) and not a.force:
            if json.load(open(base)).get("status") == "ok":
                print(f"{proj}-{bug}: 已完成，跳过")
                continue
        b = meta["bugs"][bug]
        commit = b.get("fixed_commit")
        if not commit:
            print(f"{proj}-{bug}: 缺少 fixed_commit")
            continue
        tfs = [t for t in (b.get("test_file") or "").split(";") if t]
        root = prepare_tree(proj, bug, commit, tfs)
        if not root:
            print(f"{proj}-{bug}: 工作树准备失败")
            continue

        # 白名单里可能有在该提交上尚不存在的文件（BugsInPy 的编号越早提交越大），
        # 直接传给 pytest 会让整次收集失败，因此先按存在性过滤。
        only = expand_only(root, [x for x in a.only
                                  if os.path.exists(os.path.join(root, x))])
        want = len(a.only)
        if want and len(only) < max(1, want // 3):
            only = []          # 白名单基本不存在，退回"全收集 + 排除"模式
        nodes = collect_nodes(root, a.ignore, only)
        pool = passing_nodes(root, nodes)
        if len(pool) < 10:
            print(f"{proj}-{bug}: 可用测试过少（{len(pool)}）")
            continue
        # 只把触发测试确实落在可运行池内的缺陷计入总体：
        # 池外的触发测试（例如需要联网的用例）在本次运行中无法被任何子集检出，
        # 计入分母只会系统性地压低检出率。
        pool_set = set(pool)
        # unittest 的触发测试可能写在一个**基类**上（如 WebSocketTest），
        # 而 pytest 只收集其子类（TestWebSocket）——节点名对不上会漏检。
        # 因此把匹配放宽到"同文件 + 同方法名"，并记录放宽了多少。
        by_file_method = {}
        for n in pool:
            f, _, tail = n.partition("::")
            by_file_method.setdefault((f, tail.split("::")[-1]), set()).add(n)
        expanded = {}
        for b, keys in trig.items():
            s2 = set()
            for k in keys:
                f, _, tail = k.partition("::")
                meth = tail.split("::")[-1]
                s2 |= by_file_method.get((f, meth), set())
                if k in pool_set:
                    s2.add(k)
            if s2:
                expanded[b] = s2
        active = expanded
        if len(active) < 3:
            print(f"{proj}-{bug}: 池内可检出缺陷过少（{len(active)}）")
            continue
        pkgs = source_packages(root)
        src = ",".join(pkgs)
        cache = {}
        rec = {"project": proj, "bug": bug, "n_collected": len(nodes),
               "n_pool": len(pool), "source_pkgs": pkgs,
               "total_asserts_in_pool": sum(
                   (cache_file(root, cache, n) or {}).get("n_assert", 0)
                   for n in pool) if False else None,
               "pool": pool, "instances": []}
        rec["pool_features"] = aggregate_features(pool, root, cache)
        rec["n_bugs_active"] = len(active)

        for frac in a.fracs:
            k = max(5, int(round(len(pool) * frac)))
            for rep in range(1, a.reps + 1):
                rng = random.Random(abs(hash((proj, bug, frac, rep))) % 10**8)
                sample = sorted(rng.sample(pool, k))
                sset = set(sample)
                det = sum(1 for keys in active.values() if keys & sset)
                m = run_subset(root, sample, frac, rep, src)
                inst = {"frac": frac, "rep": rep, "k": k,
                        "detect_n": det, "detect_rate": det / len(active),
                        "n_bugs_project": len(trig),
                        "n_bugs_active": len(active),
                        "features": aggregate_features(sample, root, cache)}
                inst.update(m)
                rec["instances"].append(inst)
                cov = m.get("line_cov_pct")
                print(f"  {proj}-{bug} f{frac} r{rep}: k={k} "
                      f"cov={cov:.1f} 检出 {det}/{len(active)}"
                      if cov is not None else
                      f"  {proj}-{bug} f{frac} r{rep}: 覆盖率缺失")

        rec["status"] = "ok"
        json.dump(rec, open(base, "w"), indent=1)
    print("完成")


def cache_file(root, cache, node):
    f, _, tail = node.partition("::")
    if f not in cache:
        cache[f] = scan_file(os.path.join(root, f), f)
    return cache[f].get(tail)


if __name__ == "__main__":
    main()
