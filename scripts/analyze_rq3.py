#!/usr/bin/env python3
"""
RQ3 分析：L2 行为匹配特征是否在覆盖率与变异分数之外提供增量预测力

这是本项目的核心贡献点 —— 对抗 bFinder (2026) 的位置：
  该文证明了行为验证缺口存在且不为结构指标所预测，但**未检验缺口能否预测真实缺陷检测**。
  本脚本检验：把断言层面的行为匹配特征加入模型后，对真实缺陷检出率的解释力是否显著提升。

模型（全部以 log(实际测试数) 为规模控制项）：
  M0  规模
  M1  规模 + 覆盖率 + 变异分数            （现有指标）
  M2  规模 + L2 行为匹配特征              （仅行为层）
  M3  规模 + 覆盖率 + 变异分数 + L2 特征  （完整模型）

报告：
  * 各模型的 R² / 调整 R² / 5 折交叉验证 R²
  * 嵌套模型的 ΔR² 与 F 检验（statsmodels，HC1 稳健标准误）
  * 各特征在同规模层内的 Spearman 相关（分层比较，避免规模共线）
  * 各 L2 特征的偏贡献

用法：python3 scripts/analyze_rq3.py
"""
import glob
import json
import os
import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DATA = os.path.join(_ROOT, "data")
OUT = os.path.join(_ROOT, "results")
warnings.filterwarnings("ignore")

L2 = ["assertions_per_method", "strong_ratio", "weak_ratio", "predicate_ratio",
      "calls_per_assertion"]


def cls_of_path(rel):
    return rel[:-5].replace("/", ".") if rel.endswith(".java") else rel


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
        suite = d.get("kept_tests_final") or d.get("kept_tests") or []
        kept = {cls_of_path(t) for t in suite}
        bugs = trig[p]
        det = sum(1 for b, c in bugs.items() if c & kept)
        cov = d["steps"].get("coverage", {})
        mut = d["steps"].get("mutation", {})
        tag = f"{p}-{d['bug']}-f{d['frac']}-r{d['rep']}"
        af = os.path.join(DATA, "assertions", f"{tag}.json")
        if not os.path.exists(af) or cov.get("line_cov_pct") is None \
                or mut.get("mutation_score_pct") is None:
            continue
        a = json.load(open(af))
        dv = a.get("derived", {})
        rows.append({
            "tag": tag, "project": p, "bug": d["bug"], "frac": d["frac"],
            "size": d.get("n_test_final") or d.get("n_test_kept"),
            "coverage": cov["line_cov_pct"], "mutation": mut["mutation_score_pct"],
            "detect_rate": det / len(bugs) if bugs else None,
            **{k: dv.get(k) for k in L2},
        })
    return pd.DataFrame([r for r in rows if r["detect_rate"] is not None
                         and r["size"] and all(r[k] is not None for k in L2)])


def fit(df, cols):
    X = sm.add_constant(df[cols].astype(float), has_constant="add")
    return sm.OLS(df["detect_rate"].astype(float), X).fit(cov_type="HC1")


def cv_r2(df, cols, k=5, seed=20261015):
    """5 折交叉验证 R²，防止过拟合造成的虚高。"""
    rng = np.random.RandomState(seed)
    idx = rng.permutation(len(df))
    folds = np.array_split(idx, k)
    y = df["detect_rate"].astype(float).values
    pred = np.full(len(df), np.nan)
    for i in range(k):
        te = folds[i]
        tr = np.concatenate([folds[j] for j in range(k) if j != i])
        X = sm.add_constant(df.iloc[tr][cols].astype(float), has_constant="add")
        m = sm.OLS(y[tr], X).fit()
        Xt = sm.add_constant(df.iloc[te][cols].astype(float), has_constant="add")
        pred[te] = m.predict(Xt)
    ss_res = ((y - pred) ** 2).sum()
    ss_tot = ((y - y.mean()) ** 2).sum()
    return 1 - ss_res / ss_tot


def main():
    os.makedirs(OUT, exist_ok=True)
    df = load()
    if len(df) < 12:
        print(f"样本不足（{len(df)}），等待采集。")
        return
    df["log_size"] = np.log(df["size"].astype(float))
    print(f"样本 {len(df)} 个实例，项目分布 {df['project'].value_counts().to_dict()}\n")

    models = {
        "M0 规模": ["log_size"],
        "M1 规模+覆盖率+变异": ["log_size", "coverage", "mutation"],
        "M2 规模+行为匹配": ["log_size"] + L2,
        "M3 完整": ["log_size", "coverage", "mutation"] + L2,
    }
    print("=== 模型比较 ===")
    print(f"{'模型':<22}{'R²':>8}{'调整R²':>9}{'CV R²':>9}{'AIC':>9}")
    fitted = {}
    for name, cols in models.items():
        m = fit(df, cols)
        fitted[name] = (m, cols)
        print(f"{name:<22}{m.rsquared:>8.3f}{m.rsquared_adj:>9.3f}"
              f"{cv_r2(df, cols):>9.3f}{m.aic:>9.1f}")

    print("\n=== 嵌套模型检验（增量预测力） ===")
    pairs = [("M0 规模", "M1 规模+覆盖率+变异"),
             ("M0 规模", "M2 规模+行为匹配"),
             ("M1 规模+覆盖率+变异", "M3 完整")]
    for a, b in pairs:
        ma, _ = fitted[a]
        mb, _ = fitted[b]
        d_r2 = mb.rsquared - ma.rsquared
        f_stat, p_val, _ = mb.compare_f_test(ma)
        print(f"  {a} → {b}:  ΔR² = {d_r2:+.4f}   F = {f_stat:.2f}   p = {p_val:.4f}"
              f"   {'显著' if p_val < 0.05 else '不显著'}")

    print("\n=== 逐特征（M3 完整模型，HC1 稳健标准误） ===")
    m3, _ = fitted["M3 完整"]
    for name in ["log_size", "coverage", "mutation"] + L2:
        if name in m3.params.index:
            print(f"  {name:<22} β={m3.params[name]:>+9.4f}  p={m3.pvalues[name]:.4f}")

    print("\n=== 同规模层内相关（避免规模共线） ===")
    cols_show = ["coverage", "mutation"] + L2
    hdr = "  " + f"{'规模档':<8}{'n':>4}" + "".join(f"{c[:12]:>14}" for c in cols_show)
    print(hdr)
    for frac in sorted(df["frac"].unique()):
        g = df[df["frac"] == frac]
        if len(g) < 4:
            continue
        line = f"  {frac:<8}{len(g):>4}"
        for c in cols_show:
            r, _ = stats.spearmanr(g[c], g["detect_rate"])
            line += f"{r:>14.3f}"
        print(line)

    # ---- 伪重复检查：同一基准版本的多个子集共享同一批测试源码，实例间不独立 ----
    print("\n=== 伪重复检查与按版本聚类 ===")
    df["bugkey"] = df["project"] + "-" + df["bug"]
    n_bug = df["bugkey"].nunique()
    print(f"  实例数 {len(df)}，但基准版本只有 {n_bug} 个 → 有效样本量接近 {n_bug} 而非 {len(df)}")
    print("  同一缺陷的多个子集共享测试源码风格，其断言特征高度相关，构成伪重复。\n")

    print("  聚类稳健标准误（按基准版本聚类）：")
    for name in ["M1 规模+覆盖率+变异", "M2 规模+行为匹配"]:
        cols = models[name]
        X = sm.add_constant(df[cols].astype(float), has_constant="add")
        m = sm.OLS(df["detect_rate"].astype(float), X).fit(
            cov_type="cluster", cov_kwds={"groups": df["bugkey"]})
        sig = [c for c in cols if m.pvalues.get(c, 1) < 0.05]
        print(f"    {name}: R²={m.rsquared:.3f}  显著特征={sig or '无'}")

    print("\n  按基准版本聚合后（每版本取均值，n=%d）的特征相关：" % n_bug)
    agg = df.groupby("bugkey").agg({**{c: "mean" for c in cols_show},
                                    "detect_rate": "mean", "frac": "mean", "size": "mean"}).reset_index()
    for c in cols_show:
        r, p = stats.spearmanr(agg[c], agg["detect_rate"])
        star = "*" if p < 0.05 else " "
        print(f"    {c:<22} ρ={r:>+7.3f}{star}  p={p:.3f}")

    df.to_json(os.path.join(OUT, "rq3_dataset.json"), orient="records")


if __name__ == "__main__":
    main()
