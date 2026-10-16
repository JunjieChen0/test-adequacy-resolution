#!/usr/bin/env python3
"""
RQ3 固定效应分析：剔除项目间差异后，行为匹配特征是否仍与检出率相关

为什么必须做这一步：
  断言风格主要是**项目级属性**（实测强断言占比：Codec 0.71、Lang 0.68、Math 0.62，
  而 Chart 0.38、Cli 0.38、Csv 0.00）。直接跨项目求相关，会把"项目差异"误读成
  "断言强度效应"。组内去均值（固定效应）才能分离出项目内部的变化。

  另外，strong_ratio 与 weak_ratio 在项目内几乎完全互补（ρ≈-1），信息冗余，
  两者只应保留其一。

用法：python3 scripts/analyze_fixed_effects.py
"""
import glob
import json
import os

import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.api as sm

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DATA = os.path.join(_ROOT, "data")
L2 = ["assertions_per_method", "strong_ratio", "predicate_ratio", "calls_per_assertion"]
BASE = ["coverage", "mutation"]


def cls_of_path(r):
    return r[:-5].replace("/", ".") if r.endswith(".java") else r


def load():
    trig = {}
    for f in glob.glob(os.path.join(DATA, "instance-*.json")):
        d = json.load(open(f))
        trig.setdefault(d["project"], {})[d["bug"]] = {
            t.split("::")[0] for t in d.get("trigger.txt", []) if "::" in t}
    rows = []
    for f in glob.glob(os.path.join(DATA, "subsets", "*.json")):
        d = json.load(open(f))
        if d.get("status") != "ok":
            continue
        p = d["project"]
        if p not in trig:
            continue
        af = os.path.join(DATA, "assertions",
                          f"{p}-{d['bug']}-f{d['frac']}-r{d['rep']}.json")
        if not os.path.exists(af):
            continue
        cov = d["steps"].get("coverage", {})
        mut = d["steps"].get("mutation", {})
        if cov.get("line_cov_pct") is None or mut.get("mutation_score_pct") is None:
            continue
        suite = d.get("kept_tests_final") or d.get("kept_tests") or []
        kept = {cls_of_path(t) for t in suite}
        bugs = trig[p]
        det = sum(1 for b, c in bugs.items() if c & kept) / len(bugs) if bugs else None
        if det is None:
            continue
        dv = json.load(open(af)).get("derived", {})
        rec = {"project": p, "bug": d["bug"], "frac": d["frac"],
               "size": d.get("n_test_final") or d.get("n_test_kept"),
               "detect_rate": det, "coverage": cov["line_cov_pct"],
               "mutation": mut["mutation_score_pct"]}
        rec.update({k: dv.get(k) for k in L2 + ["weak_ratio"]})
        if any(rec[k] is None for k in L2 + ["weak_ratio"]) or not rec["size"]:
            continue
        rows.append(rec)
    df = pd.DataFrame(rows)
    # 剔除退化记录：断言特征全为 0 说明解析失败（实测 Csv 出现过）
    bad = (df["strong_ratio"] + df["weak_ratio"] <= 0)
    if bad.any():
        print(f"剔除退化记录 {int(bad.sum())} 条（断言特征为 0，解析失败）")
    return df[~bad].copy()


def demean(df, cols):
    """组内去均值（项目固定效应）。"""
    g = df.groupby("project")
    return df[cols] - g[cols].transform("mean")


def main():
    df = load()
    print(f"样本 {len(df)} 个实例，{df['project'].nunique()} 个项目，"
          f"{df.groupby(['project','bug']).ngroups} 个独立基准版本\n")

    df["log_size"] = np.log(df["size"].astype(float))
    cols = ["log_size"] + BASE + L2
    dm = demean(df, cols + ["detect_rate"])

    print("=== 组内去均值后的相关（项目固定效应，剔除项目间差异） ===")
    print(f"{'特征':<24}{'ρ(全部)':>10}{'ρ(项目内)':>12}{'p':>9}")
    for c in cols:
        r_all, _ = stats.spearmanr(df[c], df["detect_rate"])
        r_in, p_in = stats.spearmanr(dm[c], dm["detect_rate"])
        star = "*" if p_in < 0.05 else " "
        print(f"{c:<24}{r_all:>10.3f}{r_in:>11.3f}{star}{p_in:>9.3f}")

    # 版本级聚合后再做固定效应
    agg = df.groupby(["project", "bug"]).mean(numeric_only=True).reset_index()
    dm2 = demean(agg, cols + ["detect_rate"])
    print(f"\n=== 版本级聚合后（n={len(agg)} 个独立版本） ===")
    for c in cols:
        r_all, p_all = stats.spearmanr(agg[c], agg["detect_rate"])
        r_in, p_in = stats.spearmanr(dm2[c], dm2["detect_rate"])
        s1 = "*" if p_all < 0.05 else " "
        s2 = "*" if p_in < 0.05 else " "
        print(f"{c:<24}整体 ρ={r_all:>+6.3f}{s1} p={p_all:.3f}   "
              f"项目内 ρ={r_in:>+6.3f}{s2} p={p_in:.3f}")

    print("\n=== 固定效应回归（项目虚拟变量，因变量=检出率） ===")
    for name, xs in [("仅规模", ["log_size"]),
                     ("规模+覆盖率+变异", ["log_size"] + BASE),
                     ("规模+行为匹配", ["log_size"] + L2),
                     ("完整", ["log_size"] + BASE + L2)]:
        X = pd.concat([df[xs].astype(float),
                       pd.get_dummies(df["project"], prefix="p", drop_first=True).astype(float)],
                      axis=1)
        m = sm.OLS(df["detect_rate"].astype(float),
                   sm.add_constant(X, has_constant="add")).fit(
            cov_type="cluster", cov_kwds={"groups": df["project"] + "-" + df["bug"]})
        r2w = m.rsquared
        sig = [c for c in xs if m.pvalues.get(c, 1) < 0.05]
        print(f"  {name:<20} R²={r2w:.3f}  显著: {sig or '无'}")


if __name__ == "__main__":
    main()
