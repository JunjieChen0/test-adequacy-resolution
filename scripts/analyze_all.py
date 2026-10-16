#!/usr/bin/env python3
"""
两基准的统一分析脚本——论文中所有数字的唯一来源

为什么要把分析集中到一个脚本
----------------------------
早期版本里 Defects4J 与 BugsInPy 各有一套分析脚本，交叉验证的实现细节
（折的划分方式、哑变量的构造）不一致，导致同一份数据算出的小数位对不上，
正文里的 ΔR² 与表格里的交叉验证 R² 也无法互相推出。审稿人据此即可质疑
结果的可复现性。本脚本把两边的分析统一到 `robust_lib` 的同一套实现上，
并显式报告点估计与自助分布均值之差（两者本就不相等，必须分别给出）。

产出（results/analysis.json）：
  sample        规模与项目构成
  cv            三种评估方案下的模型 R²（汇总 / 项目固定效应 / 留一版本）
  delta         ΔR² 的点估计，以及按版本、按项目两种层级的自助区间
  fingerprint   项目判别力
  lopo          逐项目留一
  subsets       项目规模门槛与去 youtube-dl
  mde           最小可检测效应曲线（两种自助力层级）与目标效应所需版本数

用法：python3 scripts/analyze_all.py [--benchmark d4j|bip|both] [--boot 200]
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import GroupKFold, cross_val_score

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from robust_lib import (L2, ROOT, Panel, available_models, load_bip, load_d4j,  # noqa: E402
                        resample_projects, resample_versions)

K = 5
MDE_B = 1000        # 分辨力曲线每点用的重采样次数


def models_of(df):
    m, _ = available_models(df)
    return m


def r2_table(df, models, with_fe, k=K, seed=7):
    return {name: float(Panel(df, cols, with_fe).cv_r2(k, seed))
            for name, cols in models.items()}


def r2_lovo(df, models, with_fe):
    return {name: float(Panel(df, cols, with_fe).lovo_r2())
            for name, cols in models.items()}


def delta_point(df, models, with_fe, k=K, seed=7):
    a = Panel(df, models["full"], with_fe).cv_r2(k, seed)
    b = Panel(df, models["classical"], with_fe).cv_r2(k, seed)
    return float(a - b) if np.isfinite(a) and np.isfinite(b) else np.nan


def boot_delta(df, models, with_fe, scheme, B, seed):
    rng = np.random.default_rng(seed)
    fn = resample_versions if scheme == "version" else resample_projects
    out = []
    for _ in range(B):
        b = fn(df, rng)
        g = b["version"].nunique()
        if g < 6:
            continue
        d = delta_point(b, models, with_fe, k=min(K, max(3, g // 3)),
                        seed=int(rng.integers(1 << 30)))
        if np.isfinite(d):
            out.append(d)
    return np.array(out)


def summarise(a):
    if len(a) < 10:
        return None
    lo, hi = np.percentile(a, [2.5, 97.5])
    return {"n_boot": int(len(a)), "mean": float(a.mean()), "lo": float(lo),
            "hi": float(hi), "width": float(hi - lo),
            "cross_zero": float(np.mean(a <= 0))}


def fingerprint(df):
    """项目判别力。

    两种划分方式都要报：
      - 按实例随机 5 折：回答"特征里有没有项目信息"；
      - 按缺陷版本分组的 5 折：回答"这份信息能否推广到**没见过的版本**"。
    只报前者会被质疑版本泄漏（同一版本的多个子集同时进训练与测试）。
    """
    clf = RandomForestClassifier(n_estimators=300, random_state=0)
    classical = ["log_size", "coverage"] + (
        ["mutation"] if df["mutation"].notna().any() else [])
    out = {}
    for nm, cols in (("behaviour", L2), ("classical", classical),
                     ("all", classical + L2)):
        X, y = df[cols].values, df["project"]
        rand = float(cross_val_score(clf, X, y, cv=5).mean())
        gk = GroupKFold(n_splits=5)
        grp = float(np.mean([clf.fit(X[tr], y.iloc[tr]).score(X[te], y.iloc[te])
                             for tr, te in gk.split(X, y, groups=df["version"])]))
        out[nm] = rand
        out[nm + "_grouped"] = grp
    out["baseline"] = float(1 / df["project"].nunique())
    return out


FIT_MIN_N = 30      # required_n 的拟合区间起点。正文已说明低于约 30 个版本时估计量
                    # 不稳定（半宽 0.2~1.5，最小样本处甚至超过 100）。把这些点并进
                    # log-log 最小二乘会把斜率显著拉陡（Defects4J 版本层级 −2.97 对
                    # −1.79，BugsInPy 项目层级 −2.54 对 −1.23），从而系统性低估所需
                    # 版本数，甚至算出低于已评估样本量的"需求"。


def mde_curve(df, models, scheme, B=MDE_B, seed=99):
    rng = np.random.default_rng(seed)
    fn = resample_versions if scheme == "version" else resample_projects
    vers = df["version"].unique()
    curve = []
    for n in (10, 15, 20, 30, 40, 50, 70, 100, 130):
        if n > len(vers):
            continue
        ds = []
        for _ in range(B):
            if scheme == "version":
                pick = rng.choice(vers, size=n, replace=True)
                sub = df[df["version"].isin(pick)]
            else:
                sub = resample_projects(df, rng)          # 整群重采样后再抽 n 个版本
                sub = sub[sub["version"].isin(
                    rng.choice(sub["version"].unique(),
                               size=min(n, sub["version"].nunique()), replace=False))]
            g = sub["version"].nunique()
            if g < 5:
                continue
            d = delta_point(sub, models, True, k=min(K, max(3, g // 3)))
            if np.isfinite(d):
                ds.append(d)
        ds = np.array(ds)
        if len(ds) < 8:
            continue
        half = float((np.percentile(ds, 97.5) - np.percentile(ds, 2.5)) / 2)
        curve.append({"n_ver": int(n), "mde": half})
    return curve


def required_n(curve, targets=(0.02, 0.05, 0.10)):
    """由分辨力曲线反解达到目标半宽所需的版本数。

    做法：在 n >= FIT_MIN_N 的稳定区间上对 log(n) 与 log(半宽) 做最小二乘直线，
    再对目标半宽求逆。

    判"外推"的依据是**目标半宽是否低于直接评估到的最小半宽**——也就是所需的
    分辨力是否超出了实测曲线的量程。等价的判据是所需 n 是否超出直接评估过的最大
    n；两者在单调下降的曲线上一致，若出现分歧以"目标低于最小观测半宽"为准，
    两个字段都记录下来以便核对。
    """
    pts = [c for c in curve if c["n_ver"] >= FIT_MIN_N]
    if len(pts) < 3:
        return {}
    x = np.log([c["n_ver"] for c in pts])
    y = np.log([c["mde"] for c in pts])
    a, b = np.polyfit(x, y, 1)
    if a >= 0:                       # 半宽必须随样本量下降，否则拟合不可用
        return {}
    inside = max(c["n_ver"] for c in curve)
    min_obs = min(c["mde"] for c in curve)
    out = {}
    for t in targets:
        n = float(np.exp((np.log(t) - b) / a))
        below = bool(t < min_obs)
        exceeded = bool(n > inside)
        out[f"{t:.2f}"] = {
            "n": n,
            "extrapolated": bool(below or exceeded),
            "target_below_min_observed": below,
            "n_exceeds_evaluated": exceeded,
            "slope": float(a),
            "fit_n_min": FIT_MIN_N,
            "n_evaluated_max": int(inside),
            "mde_min_observed": float(min_obs),
        }
    return out


def run(bench, df, B):
    models = models_of(df)
    if "mutation" not in models["classical"]:
        print(f"  注意：{bench} 无变异分数，经典指标块为 规模+覆盖率")
    res = {"n_inst": int(len(df)), "n_ver": int(df["version"].nunique()),
           "n_proj": int(df["project"].nunique()),
           "B_baseline": B, "B_resolution": MDE_B}
    print(f"\n########## {bench}：{res['n_proj']} 项目 / {res['n_ver']} 版本 / "
          f"{res['n_inst']} 实例 ##########")

    # ---- 1. 三种评估方案的模型 R² ----
    print("\n=== 模型 R²（三种评估方案）===")
    res["cv"] = {"pooled": r2_table(df, models, False),
                 "with_fe": r2_table(df, models, True),
                 "lovo_fe": r2_lovo(df, models, True)}
    for tag, r in res["cv"].items():
        best = max(r, key=lambda k: r[k] if np.isfinite(r[k]) else -9)
        print(f"  {tag:<9} " + "  ".join(f"{k}={v:.3f}" for k, v in r.items())
              + f"   ← 最优 {best}")

    # ---- 2. ΔR² 点估计与两种自助力层级 ----
    print("\n=== ΔR² = R²(完整) − R²(经典指标) ===")
    res["delta"] = {"point_with_fe": delta_point(df, models, True),
                    "point_pooled": delta_point(df, models, False)}
    print(f"  全样本点估计：含项目控制 {res['delta']['point_with_fe']:+.3f}"
          f"   无项目控制 {res['delta']['point_pooled']:+.3f}")
    for scheme in ("version", "project"):
        bs = boot_delta(df, models, True, scheme, B, seed=11)
        s = summarise(bs)
        res["delta"][f"boot_{scheme}"] = s
        if s:
            print(f"  按{'版本' if scheme=='version' else '项目'}重采样自助："
                  f"均值 {s['mean']:+.3f}  95%CI [{s['lo']:+.3f}, {s['hi']:+.3f}]  "
                  f"宽度 {s['width']:.3f}  跨零 {s['cross_zero']:.2f}")
    print("  （点估计与自助均值本就不相等：前者是全样本统计量，"
          "后者是重采样分布的均值，正文必须分别报告）")

    # ---- 3. 项目判别力 ----
    print("\n=== 项目判别力（5 折交叉验证）===")
    res["fingerprint"] = fingerprint(df)
    for k, v in res["fingerprint"].items():
        print(f"  {k:<10} {v:.3f}")

    # ---- 4. 逐项目留一 ----
    print("\n=== 逐项目留一（含项目控制）===")
    res["lopo"] = {}
    for p in sorted(df["project"].unique()):
        sub = df[df["project"] != p]
        if sub["version"].nunique() < K or sub["project"].nunique() < 3:
            res["lopo"][p] = {"skipped": True}
            continue
        r = r2_table(sub, models, True)
        pt = delta_point(sub, models, True)
        best = max(r, key=lambda k: r[k] if np.isfinite(r[k]) else -9)
        res["lopo"][p] = {"cv": r, "delta_point": pt, "best_model": best}
        print(f"  去掉 {p:<11} 最优={best:<12} ΔR²={pt:+.3f}")

    # ---- 5. 子集筛选 ----
    print("\n=== 项目规模门槛与去 youtube-dl ===")
    res["subsets"] = {}
    variants = {"all": df}
    if "youtube-dl" in set(df["project"]):
        variants["no_youtube-dl"] = df[df["project"] != "youtube-dl"]
    cnt = df.groupby("project")["version"].nunique()
    for th in (2, 5, 10):
        keep = cnt[cnt >= th].index
        if len(keep) >= 3:
            variants[f"ge{th}versions"] = df[df["project"].isin(keep)]
    for vname, sub in variants.items():
        if sub["version"].nunique() < K:
            continue
        rfe = r2_table(sub, models, True)
        rpo = r2_table(sub, models, False)
        pt = delta_point(sub, models, True)
        res["subsets"][vname] = {
            "n_proj": int(sub["project"].nunique()),
            "n_ver": int(sub["version"].nunique()),
            "cv_with_fe": rfe, "cv_pooled": rpo, "delta_point": pt}
        bfe = max(rfe, key=lambda k: rfe[k] if np.isfinite(rfe[k]) else -9)
        bpo = max(rpo, key=lambda k: rpo[k] if np.isfinite(rpo[k]) else -9)
        print(f"  {vname:<14} {sub['project'].nunique()}项目/{sub['version'].nunique()}版本  "
              f"项目内最优={bfe:<12} 汇总最优={bpo:<12} ΔR²={pt:+.3f}")

    # ---- 6. 最小可检测效应 ----
    print("\n=== 分辨力：自助区间半宽随独立版本数的变化 ===")
    res["mde"] = {}
    for scheme in ("version", "project"):
        c = mde_curve(df, models, scheme)
        res["mde"][scheme] = {"curve": c, "required": required_n(c)}
        print(f"  自助力层级={scheme}:")
        for row in c:
            print(f"    {row['n_ver']:>4} 版本 → 半宽 = {row['mde']:.3f}")
        for t, v in res["mde"][scheme]["required"].items():
            print(f"    目标半宽={t} → {v['n']:.0f} 个版本"
                  + ("（外推）" if v["extrapolated"] else ""))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark", choices=["d4j", "bip", "both"], default="both")
    ap.add_argument("--boot", type=int, default=2000,
                    help="自助次数；95%% 百分位区间在每侧需要足够多的重采样，200 次时每侧仅约 5 个点，区间宽度本身就有可观的蒙特卡洛噪声")
    ap.add_argument("--recompute-required", action="store_true",
                    help="只由现有 results/analysis.json 里已存的分辨力曲线重算目标版本数，"
                         "不重跑自助力。required 是 curve 的纯函数，因此结果与整脚本重跑一致；"
                         "用于修正 required_n 的实现而不必付出整轮自助的开销")
    a = ap.parse_args()

    p = os.path.join(ROOT, "results", "analysis.json")

    if a.recompute_required:
        data = json.load(open(p))
        for bench, b in data.items():
            for scheme, blk in b.get("mde", {}).items():
                blk["required"] = required_n(blk["curve"])
                print(f"{bench} / {scheme}:")
                for t, v in blk["required"].items():
                    print(f"  目标半宽={t} → {v['n']:.1f} 个版本"
                          + ("（外推）" if v["extrapolated"] else ""))
        json.dump(data, open(p, "w"), indent=1)
        print(f"\nrequired 已重算并写回 {p}")
        return

    out = {}
    if a.benchmark in ("d4j", "both"):
        out["Defects4J"] = run("Defects4J (Java)", load_d4j(), a.boot)
    if a.benchmark in ("bip", "both"):
        out["BugsInPy"] = run("BugsInPy (Python)", load_bip(), a.boot)
    json.dump(out, open(p, "w"), indent=1)
    print(f"\n结果已写入 {p}")


if __name__ == "__main__":
    main()
