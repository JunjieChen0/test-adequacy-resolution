#!/usr/bin/env python3
"""
P1 稳健性实验：五个针对"这篇论文的结论会不会因为某个设计选择而翻转"的检验

A. 逐项目留一      —— 去掉任一项目后，模型排序与 ΔR² 是否改变
B. 去 youtube-dl   —— BugsInPy 的专项检验（它占 43/82 个版本）
C. 项目规模门槛    —— 只保留 ≥1/≥2/≥5/≥10 个版本的项目
D. 自助力层级      —— 按版本重采样 vs 按项目整群重采样
E. 最小可检测效应  —— 把"需要 145 个版本"改成 MDE(n) 曲线，对
                      ΔR² = 0.02/0.05/0.10 分别给出所需版本数

用法：
    python3 scripts/robustness.py                 # 两个基准都跑
    python3 scripts/robustness.py --benchmark d4j
输出：results/robustness.json
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from robust_lib import (ROOT, Panel, available_models, load_bip, load_d4j,   # noqa: E402
                        resample_projects, resample_versions)

B = 150          # 自助次数（每格）
K_FOLD = 5


def delta(df, models, with_fe=True, k=K_FOLD, seed=0):
    """ΔR² = R²(完整) − R²(经典指标) 的全样本点估计。"""
    p_full = Panel(df, models["full"], with_fe)
    p_base = Panel(df, models["classical"], with_fe)
    a, b = p_full.cv_r2(k, seed), p_base.cv_r2(k, seed)
    return (a - b) if np.isfinite(a) and np.isfinite(b) else np.nan


def bootstrap_delta(df, models, with_fe=True, scheme="version", B=B, seed=0):
    """按指定层级重采样的 ΔR² 分布。"""
    rng = np.random.default_rng(seed)
    out = []
    fn = resample_versions if scheme == "version" else resample_projects
    for _ in range(B):
        b = fn(df, rng)
        if b["version"].nunique() < 6:
            continue
        d = delta(b, models, with_fe, k=min(K_FOLD, max(3, b["version"].nunique() // 3)),
                  seed=int(rng.integers(1 << 30)))
        if np.isfinite(d):
            out.append(d)
    return np.array(out)


def summarise(arr):
    if len(arr) < 10:
        return None
    lo, hi = np.percentile(arr, [2.5, 97.5])
    return {"n_boot": int(len(arr)), "mean": float(arr.mean()),
            "lo": float(lo), "hi": float(hi), "width": float(hi - lo),
            "cross_zero": float(np.mean(arr <= 0))}


def ranking(df, models, with_fe, k=K_FOLD, seed=0):
    return {name: Panel(df, cols, with_fe).cv_r2(k, seed)
            for name, cols in models.items()}


def run_one(name, df):
    models, _ = available_models(df)
    res = {"n_inst": int(len(df)), "n_ver": int(df["version"].nunique()),
           "n_proj": int(df["project"].nunique())}
    print(f"\n########## {name}：{res['n_proj']} 项目 / {res['n_ver']} 版本 / "
          f"{res['n_inst']} 实例 ##########")

    # ---- 基线 ----
    print("\n=== 基线 ===")
    res["baseline"] = {}
    for fe in (False, True):
        tag = "with_fe" if fe else "pooled"
        r = ranking(df, models, fe)
        point = delta(df, models, fe)
        bs = bootstrap_delta(df, models, fe, "version", B=B)
        res["baseline"][tag] = {"cv": {k: float(v) for k, v in r.items()},
                                "delta_point": float(point),
                                "delta_boot": summarise(bs)}
        print(f"  {'含项目控制' if fe else '无项目控制'}: "
              + "  ".join(f"{k}={v:.3f}" for k, v in r.items())
              + f"   ΔR²点估计={point:+.3f}  自助均值={bs.mean():+.3f}"
              f"  宽度={np.percentile(bs,97.5)-np.percentile(bs,2.5):.3f}")

    # ---- A. 逐项目留一 ----
    print("\n=== A. 逐项目留一（含项目控制）===")
    res["lopo"] = {}
    for p in sorted(df["project"].unique()):
        sub = df[df["project"] != p]
        if sub["version"].nunique() < K_FOLD or sub["project"].nunique() < 3:
            res["lopo"][p] = {"skipped": "样本不足"}
            continue
        r = ranking(sub, models, True)
        pt = delta(sub, models, True)
        bs = bootstrap_delta(sub, models, True, "version", B=60)
        best = max(r, key=lambda kk: r[kk] if np.isfinite(r[kk]) else -9)
        res["lopo"][p] = {"cv": {k: float(v) for k, v in r.items()},
                          "delta_point": float(pt), "best_model": best,
                          "delta_boot": summarise(bs)}
        print(f"  去掉 {p:<11} 最优={best:<14} ΔR²={pt:+.3f}  "
              f"自助均值={bs.mean():+.3f}" if len(bs) else f"  去掉 {p}: 自助失败")

    # ---- B/C. 子集筛选 ----
    print("\n=== B/C. 项目筛选 ===")
    res["subsets"] = {}
    variants = {"all": df}
    if "youtube-dl" in set(df["project"]):
        variants["no_youtube-dl"] = df[df["project"] != "youtube-dl"]
    cnt = df.groupby("project")["version"].nunique()
    for th in (2, 5, 10):
        keep = cnt[cnt >= th].index
        if len(keep) >= 3:
            variants[f"projects_ge{th}ver"] = df[df["project"].isin(keep)]
    for vname, sub in variants.items():
        if sub["version"].nunique() < K_FOLD:
            continue
        r = ranking(sub, models, True)
        rp = ranking(sub, models, False)
        pt = delta(sub, models, True)
        bs = bootstrap_delta(sub, models, True, "version", B=60)
        res["subsets"][vname] = {
            "n_proj": int(sub["project"].nunique()),
            "n_ver": int(sub["version"].nunique()),
            "cv_fe": {k: float(v) for k, v in r.items()},
            "cv_pooled": {k: float(v) for k, v in rp.items()},
            "delta_point": float(pt), "delta_boot": summarise(bs)}
        best_fe = max(r, key=lambda kk: r[kk] if np.isfinite(r[kk]) else -9)
        best_pool = max(rp, key=lambda kk: rp[kk] if np.isfinite(rp[kk]) else -9)
        print(f"  {vname:<18} {sub['project'].nunique()}项目/{sub['version'].nunique()}版本  "
              f"项目内最优={best_fe:<14} 汇总最优={best_pool:<14} ΔR²={pt:+.3f}")

    # ---- D. 自助力层级 ----
    print("\n=== D. 自助力层级（含项目控制）===")
    res["bootstrap_hierarchy"] = {}
    for scheme in ("version", "project"):
        bs = bootstrap_delta(df, models, True, scheme, B=B)
        s = summarise(bs)
        res["bootstrap_hierarchy"][scheme] = s
        if s:
            print(f"  按{'版本' if scheme=='version' else '项目'}重采样: "
                  f"均值={s['mean']:+.3f}  95%CI [{s['lo']:+.3f},{s['hi']:+.3f}]  "
                  f"宽度={s['width']:.3f}  跨零={s['cross_zero']:.2f}")

    # ---- E. 最小可检测效应曲线 ----
    print("\n=== E. 最小可检测效应 MDE(n) = 区间半宽 ===")
    curve = []
    vers = df["version"].unique()
    for n in (10, 15, 20, 30, 40, 50, 70, 100):
        if n > len(vers):
            continue
        ds = []
        rng = np.random.default_rng(20261016 + n)
        for _ in range(80):
            pick = rng.choice(vers, size=n, replace=True)
            sub = df[df["version"].isin(pick)]
            if sub["version"].nunique() < 5:
                continue
            d = delta(sub, models, True, k=min(K_FOLD, max(3, sub["version"].nunique() // 3)))
            if np.isfinite(d):
                ds.append(d)
        ds = np.array(ds)
        if len(ds) < 8:
            continue
        half = (np.percentile(ds, 97.5) - np.percentile(ds, 2.5)) / 2
        curve.append({"n_ver": int(n), "mde": float(half),
                      "width": float(2 * half)})
        print(f"  {n:>4} 个版本 → MDE = {half:.3f}")
    res["mde_curve"] = curve
    # 对目标效应量给出所需版本数（对数-对数外推，标注为外推值）
    if len(curve) >= 3:
        x = np.log([c["n_ver"] for c in curve])
        y = np.log([c["mde"] for c in curve])
        a, b = np.polyfit(x, y, 1)
        res["required_versions"] = {}
        for target in (0.02, 0.05, 0.10):
            n_req = float(np.exp((np.log(target) - b) / a))
            inside = max((c["n_ver"] for c in curve), default=0)
            res["required_versions"][f"{target:.2f}"] = {
                "n": n_req, "extrapolated": bool(n_req > inside)}
            print(f"  目标 MDE={target:.2f} → 需要 {n_req:.0f} 个独立版本"
                  + ("（外推）" if n_req > inside else "（实测范围内）"))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark", choices=["d4j", "bip", "both"], default="both")
    a = ap.parse_args()
    out = {}
    if a.benchmark in ("d4j", "both"):
        out["Defects4J"] = run_one("Defects4J (Java)", load_d4j())
    if a.benchmark in ("bip", "both"):
        out["BugsInPy"] = run_one("BugsInPy (Python)", load_bip())
    p = os.path.join(ROOT, "results", "robustness.json")
    json.dump(out, open(p, "w"), indent=1)
    print(f"\n结果已写入 {p}")


if __name__ == "__main__":
    main()
