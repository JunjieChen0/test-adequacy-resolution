#!/usr/bin/env python3
"""
BugsInPy 元数据采集：每个缺陷的触发测试

为什么先做这一步
----------------
结果变量（"这套测试能检出本项目多少个真实缺陷"）不依赖执行任何测试，
只需知道每个缺陷的**触发测试**是哪一个。Defects4J 由框架提供 trigger tests，
BugsInPy 则写在每个缺陷的 `run_test.sh` 里——这是两者的接口等价点，
也是跨基准复现能成立的前提。

本脚本把 17 个项目的触发测试统一解析成
    (相对项目根的测试文件路径, 测试节点名)
并汇总每个项目的缺陷清单与所需 Python 版本。

用法：python3 scripts/bip_meta.py [--root /home/user/tools/bugsinpy]
输出：data-bip/meta/<project>.json 与 data-bip/meta-summary.json
"""
import argparse
import json
import os
import re
import shlex

DEFAULT_ROOT = "/home/user/tools/bugsinpy"
OUT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data-bip"))

# unittest 形式：python -m unittest -q tests.test_black.BlackTestCase.test_x
RE_UNITTEST = re.compile(r"^([\w.]+)\.(\w+)\.(\w+)$")
# pytest 形式：path/to/test_x.py::Node::test_y
RE_PATH = re.compile(r"^([\w./\-]+\.py)(?:::([\w:\[\]\-]+))?$")


def parse_trigger(cmd):
    """把一条 run_test.sh 命令解析成 {file, node, raw}。失败时 node 为 None。"""
    cmd = cmd.strip()
    if not cmd:
        return None
    try:
        toks = shlex.split(cmd)
    except ValueError:
        toks = cmd.split()
    # 去掉解释器与框架开关
    args = [t for t in toks
            if not t.startswith("-") and t not in
            ("python", "python3", "py.test", "pytest", "-m", "unittest")]
    if not args:
        return None
    target = args[0]
    if target in ("unittest", "pytest"):
        target = args[1] if len(args) > 1 else ""

    m = RE_PATH.match(target)
    if m:                                   # pytest 路径形式
        return {"file": m.group(1), "node": m.group(2) or "", "raw": cmd}
    if "." in target and "/" not in target:   # unittest 点分形式
        parts = target.split(".")
        # 不能靠正则切分：`a.b.C.m` 与 `a.b.C` 都是合法输入。
        # 规则：最后一段以 test 开头则视为方法名，其前一段为类名（BugsInPy 里
        # 方法名一律以 test 开头）；否则最后一段是类名，无方法（整个类）。
        if len(parts) >= 3 and parts[-1].startswith("test"):
            method, cls, mod = parts[-1], parts[-2], parts[:-2]
        elif len(parts) >= 2:
            method, cls, mod = None, parts[-1], parts[:-1]
        else:
            return {"file": None, "node": None, "raw": cmd}
        path = "/".join(mod) + ".py"
        node = f"{cls}::{method}" if method else cls
        return {"file": path, "node": node, "raw": cmd}
    return {"file": None, "node": None, "raw": cmd}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=DEFAULT_ROOT)
    a = ap.parse_args()
    proj_dir = os.path.join(a.root, "projects")
    os.makedirs(os.path.join(OUT, "meta"), exist_ok=True)

    summary = {}
    for proj in sorted(os.listdir(proj_dir)):
        bdir = os.path.join(proj_dir, proj, "bugs")
        if not os.path.isdir(bdir):
            continue
        bugs = {}
        for bid in sorted(os.listdir(bdir), key=lambda x: int(x) if x.isdigit() else 0):
            d = os.path.join(bdir, bid)
            info = os.path.join(d, "bug.info")
            rt = os.path.join(d, "run_test.sh")
            if not (os.path.exists(info) and os.path.exists(rt)):
                continue
            meta = {}
            for line in open(info, errors="ignore"):
                m = re.match(r'(\w+)\s*=\s*"([^"]*)"', line.strip())
                if m:
                    meta[m.group(1)] = m.group(2)
            cmds = [l for l in open(rt, errors="ignore").read().splitlines() if l.strip()]
            trig = [t for t in (parse_trigger(c) for c in cmds) if t]
            bugs[bid] = {
                "bug": bid,
                "project": proj,
                "python_version": meta.get("python_version"),
                "test_file": meta.get("test_file"),
                "buggy_commit": meta.get("buggy_commit_id"),
                "fixed_commit": meta.get("fixed_commit_id"),
                "github_url": None,
                "triggers": trig,
            }
        if not bugs:
            continue
        ok = sum(1 for b in bugs.values()
                 if b["triggers"] and b["triggers"][0]["file"])
        pys = sorted({b["python_version"] for b in bugs.values() if b["python_version"]})
        files = sorted({t["file"] for b in bugs.values() for t in b["triggers"] if t["file"]})
        summary[proj] = {
            "n_bugs": len(bugs),
            "n_trigger_parsed": ok,
            "python_versions": pys,
            "n_trigger_files": len(files),
            "trigger_files": files,
        }
        json.dump({"project": proj, "bugs": bugs},
                  open(os.path.join(OUT, "meta", f"{proj}.json"), "w"),
                  indent=1, ensure_ascii=False)
        print(f"{proj}: {len(bugs)} 缺陷, 解析出触发测试 {ok}, "
              f"分布于 {len(files)} 个文件, python {','.join(pys)}")

    json.dump(summary, open(os.path.join(OUT, "meta-summary.json"), "w"),
              indent=1, ensure_ascii=False)
    total = sum(v["n_bugs"] for v in summary.values())
    ok = sum(v["n_trigger_parsed"] for v in summary.values())
    print(f"\n合计 {len(summary)} 个项目 / {total} 个缺陷，触发测试解析成功 {ok} "
          f"({ok/total:.1%})")


if __name__ == "__main__":
    main()
