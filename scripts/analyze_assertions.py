#!/usr/bin/env python3
"""
L2 行为匹配特征提取器 —— RQ3 的自变量

动机（项目的核心问题）：
  原方案里的说明性例子是——两个测试对同一函数的分支覆盖率都是 100%，
  但 test_b 断言 `discount(100, True) == 90`，test_a 只断言 `> 0`。
  覆盖率相同，验证能力不同。**断言强度**是这个差异的可测量代理。

本脚本对每个已采集的子集实例，静态提取其实际运行测试集的断言特征。
纯静态分析（不编译、不执行），因此成本极低。

特征（对应 RQ3 的 L2 层）：
  n_assertions          断言调用总数
  n_test_methods        @Test 标注的方法数
  assertions_per_method 断言密度
  weak_ratio            弱断言占比（assertTrue/assertFalse/assertNotNull/assertNull）
  strong_ratio          强断言占比（assertEquals/assertSame/assertArrayEquals/assertThrows）
  predicate_ratio       谓词型断言占比（断言的内容是布尔表达式而非具体期望值）
  n_distinct_calls      断言中出现的不同方法调用数（断言覆盖到多少种行为）
  test_loc              测试代码行数

用法：python3 scripts/analyze_assertions.py [--workers 4]
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_subsets import layout_of, sh, WORK  # noqa: E402

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DATA = os.path.join(_ROOT, "data")
OUT = os.path.join(DATA, "assertions")
CACHE = os.path.join(WORK, "assertion-cache")

STRONG = ("assertEquals", "assertSame", "assertNotSame", "assertArrayEquals",
          "assertThrows", "assertNotEquals", "assertLinesMatch", "assertIterableEquals")
WEAK = ("assertTrue", "assertFalse", "assertNotNull", "assertNull")
OTHER = ("assertThat", "fail", "assumeTrue", "assumeThat", "assertAll")

ASSERT_RE = re.compile(r"\b(assert\w+|fail)\s*\(")
TEST_ANNOT_RE = re.compile(r"@Test\b")
CALL_IN_ASSERT_RE = re.compile(r"\b([a-zA-Z_$][\w$]*)\s*\(")
# 谓词型断言：断言参数里含比较运算符或逻辑运算符，而非一个具体的期望值
PREDICATE_RE = re.compile(r"[<>]=?|==|!=")


def extract(path):
    """解析单个测试文件，返回特征字典。"""
    try:
        txt = open(path, errors="replace").read()
    except OSError:
        return None
    # 去掉注释与字符串字面量，避免把注释里的 assert 计入
    txt = re.sub(r"//[^\n]*", "", txt)
    txt = re.sub(r"/\*.*?\*/", "", txt, flags=re.S)
    calls = ASSERT_RE.findall(txt)
    if not calls:
        return {"n_assertions": 0, "n_test_methods": len(TEST_ANNOT_RE.findall(txt)),
                "n_strong": 0, "n_weak": 0, "n_other": 0, "n_predicate": 0,
                "n_distinct_calls": 0, "test_loc": txt.count("\n") + 1}

    n_strong = sum(c in STRONG for c in calls)
    n_weak = sum(c in WEAK for c in calls)
    n_other = sum(c in OTHER for c in calls)

    # 统计断言实参中的谓词与不同方法调用
    n_pred, distinct = 0, set()
    for m in re.finditer(r"\b(?:assert\w+|fail)\s*\(([^;]*?)\)\s*;", txt, re.S):
        arg = m.group(1)
        if PREDICATE_RE.search(arg):
            n_pred += 1
        for c in CALL_IN_ASSERT_RE.findall(arg):
            if not c.startswith("assert") and c not in ("new", "if", "for", "while", "return"):
                distinct.add(c)
    return {
        "n_assertions": len(calls),
        "n_test_methods": len(TEST_ANNOT_RE.findall(txt)) or 1,
        "n_strong": n_strong, "n_weak": n_weak, "n_other": n_other,
        "n_predicate": n_pred, "n_distinct_calls": len(distinct),
        "test_loc": txt.count("\n") + 1,
    }


def suite_of(d):
    """实际运行的测试集。

    优先用 kept_tests_final（自适应修复补回后的集合）；早期记录没有该字段，
    此时用 kept_tests（依赖闭包结果）——对这些记录而言两者相同，因为它们的
    编译是一次通过的，未触发修复。见 COLLECTION-STATUS.md。
    """
    return d.get("kept_tests_final") or d.get("kept_tests") or []


def checkout(project, bug, test_dir):
    """缺陷版本的 checkout 缓存（断言特征是静态的，每个基准版本只需检出一次）。"""
    d = os.path.join(CACHE, f"{project}-{bug}")
    marker = os.path.join(d, ".d4j-cache-ok")
    if os.path.exists(marker):
        return d
    shutil.rmtree(d, ignore_errors=True)
    rc, _, _, _ = sh(["defects4j", "checkout", "-p", project, "-v", bug, "-w", d], 900)
    if rc != 0:
        return None
    open(marker, "w").close()
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(CACHE, exist_ok=True)

    records = []
    for f in glob.glob(os.path.join(DATA, "subsets", "*.json")):
        try:
            d = json.load(open(f))
        except ValueError:
            continue
        if d.get("status") == "ok" and suite_of(d):
            records.append(d)

    # 按 (项目, 缺陷) 分组，利用 checkout 缓存避免重复检出
    by_bug = {}
    for d in records:
        by_bug.setdefault((d["project"], d["bug"]), []).append(d)
    print(f"待处理 {len(records)} 个实例，涉及 {len(by_bug)} 个基准版本", flush=True)

    def do_group(key):
        project, bug = key
        src_dir, test_dir = layout_of(project, bug.rstrip("b"), "b")
        wd = checkout(project, bug, test_dir)
        if wd is None:
            return [(d, None) for d in by_bug[key]]
        out = []
        for d in by_bug[key]:
            agg = {k: 0 for k in ("n_assertions", "n_test_methods", "n_strong", "n_weak",
                                  "n_other", "n_predicate", "n_distinct_calls", "test_loc")}
            n_files = 0
            for rel in suite_of(d):
                p = os.path.join(wd, test_dir, rel)
                feats = extract(p)
                if feats:
                    n_files += 1
                    for k in agg:
                        agg[k] += feats[k]
            out.append((d, {**agg, "n_files": n_files}))
        return out

    done = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(do_group, k): k for k in by_bug}
        for fut in as_completed(futs):
            for d, feats in fut.result():
                tag = f"{d['project']}-{d['bug']}-f{d['frac']}-r{d['rep']}"
                rec = {"tag": tag, "project": d["project"], "bug": d["bug"],
                       "frac": d["frac"], "rep": d["rep"],
                       "n_test_final": d.get("n_test_final"), "features": feats}
                if feats:
                    n = feats["n_assertions"]
                    rec["derived"] = {
                        "assertions_per_method": n / feats["n_test_methods"],
                        "strong_ratio": feats["n_strong"] / n if n else 0.0,
                        "weak_ratio": feats["n_weak"] / n if n else 0.0,
                        "predicate_ratio": feats["n_predicate"] / n if n else 0.0,
                        "calls_per_assertion": feats["n_distinct_calls"] / n if n else 0.0,
                    }
                json.dump(rec, open(os.path.join(OUT, f"{tag}.json"), "w"),
                          indent=2, ensure_ascii=False)
                done += 1
            print(f"[{done}/{len(records)}] {futs[fut]}", flush=True)
    print("完成", flush=True)


if __name__ == "__main__":
    main()
