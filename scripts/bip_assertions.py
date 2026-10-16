#!/usr/bin/env python3
"""
BugsInPy 断言层行为匹配特征（Python 版）

与 Defects4J 侧的 scripts/analyze_assertions.py 保持同一组定义，使两套基准可比：

    assertions_per_method  每个测试方法平均陈述数
    strong_ratio           强断言占比（有具体期望值的断言）
    weak_ratio             弱断言占比（只断言真/假/非空）
    predicate_ratio        断言表达式中含比较/逻辑运算的占比
    calls_per_assertion    每条断言涉及的不同函数调用数

强弱断言的判定沿用 Java 侧的分法（STRONG/WEAK 名单），
裸 `assert expr` 按表达式是否含比较运算归类——这是 Python 特有的写法，
必须在论文中说明它与 JUnit 断言名单的对应关系。

用法：python3 scripts/bip_assertions.py --project youtube-dl
输出：data-bip/assertions/<project>.json（按测试节点索引）
"""
import argparse
import ast
import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT = os.path.join(ROOT, "data-bip")

STRONG = {
    "assertEqual", "assertNotEqual", "assertAlmostEqual", "assertNotAlmostEqual",
    "assertIn", "assertNotIn", "assertIs", "assertIsNot", "assertRaises",
    "assertRaisesRegex", "assertRegex", "assertNotRegex", "assertCountEqual",
    "assertListEqual", "assertDictEqual", "assertSetEqual", "assertTupleEqual",
    "assertSequenceEqual", "assertMultiLineEqual", "assertItemsEqual",
    "assertWarns", "assertWarnsRegex", "assertLogs", "assertNoLogs",
}
WEAK = {"assertTrue", "assertFalse", "assertIsNone", "assertIsNotNone"}


def call_name(node):
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return None


def is_assertion_call(node):
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return None
    return call_name(node.value)


def has_predicate(node):
    """断言表达式里是否含比较或逻辑运算。"""
    for n in ast.walk(node):
        if isinstance(n, (ast.Compare, ast.BoolOp)):
            return True
    return False


def count_calls(node):
    return len({call_name(n) for n in ast.walk(node)
                if isinstance(n, ast.Call) and call_name(n)})


def features_of_function(fn):
    """统计单个测试方法内的断言。"""
    tot = strong = weak = pred = 0
    calls = 0
    fails = 0
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            tot += 1
            if has_predicate(node.test):
                strong += 1
                pred += 1
            else:
                weak += 1
            calls += count_calls(node.test)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            nm = call_name(node.value)
            if nm in STRONG:
                tot += 1
                strong += 1
                pred += 1
                if node.value.args:
                    calls += count_calls(node.value.args[0])
            elif nm in WEAK:
                tot += 1
                weak += 1
                calls += count_calls(node.value)
            elif nm in ("fail", "failUnless", "skip"):
                if nm != "skip":
                    tot += 1
                    fails += 1
    return {"n_assert": tot, "strong": strong, "weak": weak,
            "pred": pred, "calls": calls, "fails": fails}


def scan_file(path, rel):
    """返回 {节点名: 特征}。节点名与 pytest 的 node id 后缀一致（Class::method 或 method）。"""
    try:
        src = open(path, encoding="utf-8", errors="ignore").read()
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return {}
    out = {}

    def walk(cls_prefix, body):
        # 不能按类名是否以 Test 开头来筛选：black 的测试类叫 BlackTestCase，
        # 会被漏掉，导致该类下所有测试方法的断言特征为 0。
        for node in body:
            if isinstance(node, ast.ClassDef):
                walk(node.name, node.body)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name.startswith("test"):
                key = f"{cls_prefix}::{node.name}" if cls_prefix else node.name
                out[key] = features_of_function(node)

    walk("", tree.body)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    a = ap.parse_args()

    os.makedirs(os.path.join(OUT, "assertions"), exist_ok=True)
    sub_dir = os.path.join(OUT, "subsets")
    feats = {}
    for f in sorted(os.listdir(sub_dir)):
        if not f.startswith(a.project + "-"):
            continue
        d = json.load(open(os.path.join(sub_dir, f)))
        root = os.path.join(OUT, "work", f"{a.project}-{d['bug']}",
                            a.project)
        if not os.path.isdir(root):
            sub = [os.path.join(OUT, "work", f"{a.project}-{d['bug']}", x)
                   for x in os.listdir(os.path.join(OUT, "work", f"{a.project}-{d['bug']}"))
                   if os.path.isdir(os.path.join(OUT, "work", f"{a.project}-{d['bug']}", x))]
            root = sub[0] if sub else None
        if not root:
            continue
        cache = {}
        for node in d["pool"]:
            file_rel, _, tail = node.partition("::")
            if file_rel not in cache:
                cache[file_rel] = scan_file(os.path.join(root, file_rel), file_rel)
            if tail in cache[file_rel]:
                feats[node] = cache[file_rel][tail]
    p = os.path.join(OUT, "assertions", f"{a.project}.json")
    json.dump(feats, open(p, "w"), indent=1)
    print(f"{a.project}: 提取到 {len(feats)} 个测试方法的断言特征 → {p}")


if __name__ == "__main__":
    main()
