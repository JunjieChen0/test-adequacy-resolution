#!/usr/bin/env python3
"""
增量效度的可识别性分析

回答两个问题，用于判断"指标 X 有增量预测力"这类结论在给定样本量下是否可信：

问 1（实测）：在 Defects4J 数据上，"经典指标模型"与"再加行为匹配特征"的
             样本外 R² 之差有多大？该差值是否比其自身的不确定度还小？
问 2（分辨力）：不同独立版本数下，该差值的 95% 区间有多宽？

结果写入 results/identifiability.json，供绘图与论文引用。

用法：python3 scripts/analyze_identifiability.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import GroupKFold, cross_val_score

sys.path.insert(0, os.path.dirname(__file__))
from analyze_fixed_effects import load  # noqa: E402

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
L2 = ["assertions_per_method", "strong_ratio", "predicate_ratio", "calls_per_assertion"]
BASE = ["coverage", "mutation"]
MODELS = {
    "size":        ["log_size"],
    "size+cov+mut": ["log_size"] + BASE,
    "size+behav":  ["log_size"] + L2,
    "full":        ["log_size"] + BASE + L2,
}


def design(d, cols, fe):
    parts = [d[c].astype(float) for c in cols]
    if fe:
        parts.append(pd.get_dummies(d["project"], prefix="p", drop_first=True).astype(float))
    return pd.concat(parts, axis=1)


def cvr2_grouped(d, cols, fe, k=5, seed=0):
    """按缺陷版本分组的 K 折交叉验证 R²。

    分组是关键：同一缺陷版本的多个子集不能分处训练集与测试集，
    否则测试集信息经版本泄漏进训练集。
    """
    g = d["gkey"].nunique()
    if g < k:
        return np.nan
    err = tot = 0.0
    y = d["detect_rate"].values
    for tr, te in GroupKFold(n_splits=k).split(d, groups=d["gkey"]):
        A = design(d.iloc[tr], cols, fe)
        B = design(d.iloc[te], cols, fe).reindex(columns=A.columns, fill_value=0.0)
        m = LinearRegression().fit(A.values, d.iloc[tr]["detect_rate"].values)
        err += ((m.predict(B.values) - d.iloc[te]["detect_rate"].values) ** 2).sum()
        tot += ((d.iloc[te]["detect_rate"].values - y.mean()) ** 2).sum()
    return 1 - err / tot


def cvr2_lovo(d, cols, fe):
    """留一版本交叉验证 R²（确定性）。"""
    err = tot = 0.0
    y = d["detect_rate"].values
    for v in d["gkey"].unique():
        te = (d["gkey"] == v).values
        tr = ~te
        A = design(d[tr], cols, fe)
        B = design(d[te], cols, fe).reindex(columns=A.columns, fill_value=0.0)
        m = LinearRegression().fit(A.values, d[tr]["detect_rate"].values)
        err += ((m.predict(B.values) - d[te]["detect_rate"].values) ** 2).sum()
        tot += ((d[te]["detect_rate"].values - y.mean()) ** 2).sum()
    return 1 - err / tot


def main():
    df = load()
    df["log_size"] = np.log(df["size"].astype(float))
    df["gkey"] = df["project"] + "-" + df["bug"]
    n_ver = df["gkey"].nunique()
    print(f"数据：{len(df)} 实例 / {n_ver} 独立版本 / {df['project'].nunique()} 项目\n")

    out = {"n_inst": len(df), "n_ver": n_ver,
           "n_proj": int(df["project"].nunique())}

    # ---- 问 1：四种模型的样本外 R²，两种方案 ----
    print("=== 分组 5 折交叉验证 R² ===")
    res = {}
    for fe in (False, True):
        tag = "with_fe" if fe else "pooled"
        res[tag] = {k: float(cvr2_grouped(df, v, fe)) for k, v in MODELS.items()}
        print(f"  {'含项目控制' if fe else '无项目控制'}: "
              + "  ".join(f"{k}={res[tag][k]:.3f}" for k in MODELS))
    out["cv_grouped"] = res

    print("\n=== 留一版本交叉验证 R²（对照方案）===")
    lovo = {}
    for fe in (False, True):
        tag = "with_fe" if fe else "pooled"
        lovo[tag] = {k: float(cvr2_lovo(df, v, fe)) for k, v in MODELS.items()}
        print(f"  {'含项目控制' if fe else '无项目控制'}: "
              + "  ".join(f"{k}={lovo[tag][k]:.3f}" for k in MODELS))
    out["cv_lovo"] = lovo

    # ---- 诊断二：项目判别力（跨基准对比要用，故一并落盘）----
    print("\n=== 诊断二：项目判别力（5 折交叉验证）===")
    clf = RandomForestClassifier(n_estimators=300, random_state=0)
    fp = {}
    for nm, cols in (("仅行为匹配", L2),
                     ("仅传统指标", ["log_size"] + BASE),
                     ("全部", ["log_size"] + BASE + L2)):
        acc = float(cross_val_score(clf, df[cols].values, df["project"], cv=5).mean())
        fp[nm] = acc
        print(f"  {nm:<10} {acc:.3f}")
    out["fingerprint"] = fp
    out["fingerprint_baseline"] = float(1 / df["project"].nunique())

    # ---- 问 2：自助法区间 + 分辨力曲线 ----
    vers = df["gkey"].unique()
    rng = np.random.default_rng(20261016)
    B = 200

    def boot(n_pick, fe=True, B=B):
        d1, d2 = [], []
        for _ in range(B):
            pick = rng.choice(vers, size=n_pick, replace=True)
            idx = np.concatenate([np.where(df["gkey"].values == v)[0] for v in pick])
            b = df.iloc[idx]
            if b["gkey"].nunique() < 6:
                continue
            try:
                d1.append(cvr2_grouped(b, MODELS["full"], fe)
                          - cvr2_grouped(b, MODELS["size+cov+mut"], fe))
                d2.append(cvr2_grouped(b, MODELS["size+behav"], fe)
                          - cvr2_grouped(b, MODELS["size"], fe))
            except Exception:
                pass
        return np.array(d1), np.array(d2)

    print(f"\n=== 增量 R² 的自助法区间（含项目控制，{B} 次重采样）===")
    d1, d2 = boot(len(vers))
    for nm, v in (("完整 − (规模+覆盖+变异)", d1), ("(规模+行为) − 仅规模", d2)):
        lo, hi = np.percentile(v, [2.5, 97.5])
        print(f"  ΔR² {nm:<24} {v.mean():+.3f}  95%CI [{lo:+.3f}, {hi:+.3f}]  "
              f"宽度 {hi-lo:.3f}  跨零 {np.mean(v <= 0):.2f}")
    out["delta_full_minus_base"] = {
        "mean": float(d1.mean()),
        "lo": float(np.percentile(d1, 2.5)), "hi": float(np.percentile(d1, 97.5)),
        "width": float(np.percentile(d1, 97.5) - np.percentile(d1, 2.5)),
        "cross_zero": float(np.mean(d1 <= 0)), "B": int(len(d1))}

    print(f"\n=== 分辨力曲线：ΔR² 的 95% 区间宽度 vs 独立版本数 ===")
    print(f"{'独立版本数':>10}{'ΔR² 均值':>12}{'区间宽度':>12}{'跨零比例':>10}")
    curve = []
    for n in (10, 15, 20, 30, 40, 50, 70, 100):
        if n > len(vers):
            continue
        a, _ = boot(n, B=100)
        if len(a) < 10:
            continue
        lo, hi = np.percentile(a, [2.5, 97.5])
        curve.append({"n_ver": int(n), "mean": float(a.mean()),
                      "width": float(hi - lo), "cross_zero": float(np.mean(a <= 0))})
        print(f"{n:>10}{a.mean():>+12.3f}{hi-lo:>12.3f}{np.mean(a <= 0):>10.2f}")
    out["resolution_curve"] = curve
    out["observed_project_rho"] = float(
        df.groupby("project")[["strong_ratio", "detect_rate"]].mean()
          .corr(method="spearman").iloc[0, 1])

    path = os.path.join(ROOT, "results", "identifiability.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print(f"\n结果已写入 {path}")


if __name__ == "__main__":
    main()
