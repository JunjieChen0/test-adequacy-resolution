#!/usr/bin/env python3
"""
BugsInPy 跨基准诊断：两项核心结论在第二个基准上是否重现

复现目标
--------
1. **项目指纹**：断言层特征能否高准确率识别项目（Defects4J 上 0.863，基线 0.143）。
2. **可识别性**：加入行为匹配特征带来的样本外 R² 增量，是否小于其自身的不确定度；
   分辨力曲线是否同样表明"典型样本量不够"。

与 Defects4J 侧的差异（必须在论文中写明）
----------------------------------------
- 无变异分数：Python 侧变异测试工具链成本不可接受，故经典指标只有"规模 + 覆盖率"。
  因此对照的是 (规模+覆盖率) 与 (规模+覆盖率+行为匹配) 之差。
- 覆盖率方差小：youtube-dl 的覆盖率主要来自模块导入（787 个文件被导入即计入），
  子集从 10% 增到 25% 覆盖率仅从 27.4% 升到 29.3%。这本身是跨基准的差异，需报告。

用法：python3 scripts/bip_analyze.py
输出：results/bip-crossbench.json
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import GroupKFold, cross_val_score

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT = os.path.join(ROOT, "data-bip")
L2 = ["assertions_per_method", "strong_ratio", "predicate_ratio", "calls_per_assertion"]
MODELS = {
    "size":       ["log_size"],
    "size+cov":   ["log_size", "coverage"],
    "size+behav": ["log_size"] + L2,
    "full":       ["log_size", "coverage"] + L2,
}


def load():
    rows = []
    for f in glob.glob(os.path.join(OUT, "subsets", "*.json")):
        d = json.load(open(f))
        if d.get("status") != "ok":
            continue
        for i in d["instances"]:
            fe = i.get("features")
            if not fe or i.get("line_cov_pct") is None or i.get("detect_rate") is None:
                continue
            rows.append({"project": d["project"], "bug": d["bug"],
                         "version": f"{d['project']}-{d['bug']}",
                         "size": i["k"], "coverage": i["line_cov_pct"],
                         "detect_rate": i["detect_rate"], **{k: fe[k] for k in L2}})
    df = pd.DataFrame(rows)
    if len(df):
        df["log_size"] = np.log(df["size"].astype(float))
    return df


def design(d, cols, fe):
    parts = [d[c].astype(float) for c in cols]
    if fe:
        parts.append(pd.get_dummies(d["project"], prefix="p", drop_first=True).astype(float))
    return pd.concat(parts, axis=1)


def cvr2(d, cols, fe, k=5):
    if d["version"].nunique() < k:
        return np.nan
    err = tot = 0.0
    y = d["detect_rate"].values
    for tr, te in GroupKFold(n_splits=k).split(d, groups=d["version"]):
        A = design(d.iloc[tr], cols, fe)
        B = design(d.iloc[te], cols, fe).reindex(columns=A.columns, fill_value=0.0)
        m = LinearRegression().fit(A.values, d.iloc[tr]["detect_rate"].values)
        err += ((m.predict(B.values) - d.iloc[te]["detect_rate"].values) ** 2).sum()
        tot += ((d.iloc[te]["detect_rate"].values - y.mean()) ** 2).sum()
    return 1 - err / tot


def main():
    df = load()
    if df.empty:
        print("尚无数据")
        return
    print(f"数据：{len(df)} 实例 / {df['version'].nunique()} 独立版本 / "
          f"{df['project'].nunique()} 项目\n")
    print(df.groupby("project").agg(
        n=("size", "size"), 规模=("size", "mean"),
        覆盖率=("coverage", "mean"), 检出率=("detect_rate", "mean")).round(3).to_string())

    out = {"n_inst": len(df), "n_ver": int(df["version"].nunique()),
           "n_proj": int(df["project"].nunique()),
           "projects": sorted(df["project"].unique())}

    # ---- 诊断二：项目指纹 ----
    print("\n=== 诊断二：项目判别力（5 折交叉验证准确率）===")
    base = 1 / df["project"].nunique()
    clf = RandomForestClassifier(n_estimators=300, random_state=0)
    fp = {}
    for nm, cols in (("仅行为匹配", L2),
                     ("仅传统指标", ["log_size", "coverage"]),
                     ("全部", ["log_size", "coverage"] + L2)):
        acc = cross_val_score(clf, df[cols].values, df["project"], cv=5).mean()
        fp[nm] = float(acc)
        print(f"  {nm:<12} {acc:.3f}")
    print(f"  {'随机基线':<12} {base:.3f}")
    out["fingerprint"] = fp
    out["fingerprint_baseline"] = float(base)

    # ---- 可识别性 ----
    print("\n=== 分组 5 折交叉验证 R² ===")
    res = {}
    for fe in (False, True):
        tag = "with_fe" if fe else "pooled"
        res[tag] = {k: float(cvr2(df, v, fe)) for k, v in MODELS.items()}
        print(f"  {'含项目控制' if fe else '无项目控制'}: "
              + "  ".join(f"{k}={res[tag][k]:.3f}" for k in MODELS))
    out["cv"] = res

    vers = df["version"].unique()
    rng = np.random.default_rng(20261016)
    print(f"\n=== ΔR²（完整 − 规模+覆盖率）的自助法区间（含项目控制）===")
    for label, cmp_cols in (("加行为匹配", MODELS["full"]),):
        ds = []
        for _ in range(200):
            pick = rng.choice(vers, size=len(vers), replace=True)
            idx = np.concatenate([np.where(df["version"].values == v)[0] for v in pick])
            b = df.iloc[idx]
            if b["version"].nunique() < 6:
                continue
            try:
                ds.append(cvr2(b, cmp_cols, True) - cvr2(b, MODELS["size+cov"], True))
            except Exception:
                pass
        ds = np.array(ds)
        if not len(ds):
            continue
        lo, hi = np.percentile(ds, [2.5, 97.5])
        print(f"  ΔR² {label}: {ds.mean():+.3f}  95%CI [{lo:+.3f}, {hi:+.3f}]  "
              f"宽度 {hi-lo:.3f}  跨零 {np.mean(ds <= 0):.2f}")
        out["delta_behav"] = {"mean": float(ds.mean()), "lo": float(lo), "hi": float(hi),
                              "width": float(hi - lo),
                              "cross_zero": float(np.mean(ds <= 0))}

    # ---- 分辨力曲线 ----
    print(f"\n=== 分辨力曲线：ΔR² 的 95% 区间宽度 vs 独立版本数 ===")
    curve = []
    for n in (10, 15, 20, 30, 40, 60, 90):
        if n > len(vers):
            continue
        ds = []
        for _ in range(120):
            pick = rng.choice(vers, size=n, replace=True)
            idx = np.concatenate([np.where(df["version"].values == v)[0] for v in pick])
            b = df.iloc[idx]
            if b["version"].nunique() < 5:
                continue
            try:
                ds.append(cvr2(b, MODELS["full"], True) - cvr2(b, MODELS["size+cov"], True))
            except Exception:
                pass
        ds = np.array(ds)
        if len(ds) < 10:
            continue
        lo, hi = np.percentile(ds, [2.5, 97.5])
        curve.append({"n_ver": int(n), "mean": float(ds.mean()),
                      "width": float(hi - lo), "cross_zero": float(np.mean(ds <= 0))})
        print(f"{n:>10}{ds.mean():>+12.3f}{hi-lo:>12.3f}{np.mean(ds <= 0):>10.2f}")
    out["resolution_curve"] = curve

    p = os.path.join(ROOT, "results", "bip-crossbench.json")
    json.dump(out, open(p, "w"), indent=1)
    print(f"\n结果已写入 {p}")


if __name__ == "__main__":
    main()
