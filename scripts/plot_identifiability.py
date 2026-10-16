#!/usr/bin/env python3
"""
生成"增量效度可识别性"论文图（两联图）

左：分辨力曲线——ΔR² 的 95% 区间宽度随独立版本数变化
右：模拟阳性率——真实效应为零时，两种分析方案报告"有增量"的概率

用法：python3 scripts/plot_identifiability.py
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
RES = os.path.join(ROOT, "results")

ident = json.load(open(os.path.join(RES, "identifiability.json")))
sim = json.load(open(os.path.join(RES, "simulation-confounding.json")))

import sys
LANG = "en" if "--lang=en" in sys.argv else "zh"
L = {
    "zh": {"y": "ΔR² 的 95% 区间宽度", "x": "独立缺陷版本数",
           "leg": "实测（自助法 95% 区间宽度）", "eff": "本领域常见效应量级 ≈0.05",
           "ext": "外推：需 ≈{n:.0f}\n个独立版本",
           "ta": "(a) 分辨力：区间宽度随样本量收缩",
           "ty": "报告“有增量”的概率", "tx": "项目级混淆强度 ρ（特征均值与基准检出率的相关）",
           "tp": "汇总分析（不控制项目）", "tf": "项目内分析（固定效应）",
           "nom": "名义水平 0.05",
           "tb": "(b) 真实效应为零时的假阳性率（400 实例）"},
    "en": {"y": "Width of 95% interval for $\\Delta R^2$", "x": "Independent fault versions",
           "leg": "Observed (bootstrap 95% interval)", "eff": "Typical effect magnitude ≈0.05",
           "ext": "Extrapolated:\n≈{n:.0f} versions",
           "ta": "(a) Resolution: interval width vs. sample size",
           "ty": "P(reporting an increment)", "tx": "Project-level confounding $\\rho$",
           "tp": "Pooled analysis", "tf": "Project fixed effects",
           "nom": "Nominal level 0.05",
           "tb": "(b) False-positive rate when the true effect is zero (400 instances)"},
}[LANG]

plt.rcParams.update({
    "font.size": 9, "axes.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.dpi": 300, "savefig.bbox": "tight",
    "font.family": "DejaVu Sans" if LANG == "en" else "sans-serif",
})

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.2, 2.9))

# ---------- 左：分辨力曲线 ----------
c = [r for r in ident["resolution_curve"] if r["n_ver"] >= 40]
ns = [r["n_ver"] for r in c]
ws = [r["width"] for r in c]
ax1.plot(ns, ws, "o-", color="#1f4e79", lw=1.4, ms=4, label=L["leg"])
ax1.axhline(0.05, ls="--", lw=1.0, color="#c00000")
ax1.text(max(ns), 0.055, L["eff"], ha="right", va="bottom",
         fontsize=7.5, color="#c00000")
# 外推趋势
z = np.polyfit(np.log(ns), np.log(ws), 1)
xx = np.linspace(40, 200, 50)
ax1.plot(xx, np.exp(np.polyval(z, np.log(xx))), ":", color="#888888", lw=1.0)
n_req = np.exp((np.log(0.05) - z[1]) / z[0])
ax1.axvline(n_req, ls=":", lw=1.0, color="#888888")
ax1.text(n_req * 1.04, 0.19, L["ext"].format(n=n_req), fontsize=7.5,
         color="#555555", va="top")
ax1.set_xlabel(L["x"])
ax1.set_ylabel(L["y"])
ax1.set_xlim(35, 205)
ax1.set_ylim(0, 0.32)
ax1.legend(frameon=False, fontsize=7.5, loc="upper right")
ax1.set_title(L["ta"], fontsize=9, pad=6)

# ---------- 右：模拟阳性率 ----------
sub = [r for r in sim if r["n_inst"] == 400]
rhos = [r["rho"] for r in sub]
pool = [r["pool"] for r in sub]
fe = [r["fe"] for r in sub]
ax2.plot(range(len(rhos)), pool, "o-", color="#c00000", lw=1.4, ms=4,
         label=L["tp"])
ax2.plot(range(len(rhos)), fe, "s-", color="#1f4e79", lw=1.4, ms=4,
         label=L["tf"])
ax2.axhline(0.05, ls="--", lw=1.0, color="#888888")
ax2.text(-0.1, 0.075, L["nom"], fontsize=7.5, color="#666666")
ax2.set_xticks(range(len(rhos)))
ax2.set_xticklabels([f"{r:.1f}" for r in rhos])
ax2.set_xlabel(L["tx"])
ax2.set_ylabel(L["ty"])
ax2.set_ylim(-0.03, 1.0)
ax2.legend(frameon=False, fontsize=7.5, loc="upper left")
ax2.set_title(L["tb"], fontsize=9, pad=6)

out = os.path.join(RES, "fig-identifiability-en.png" if LANG == "en" else "fig-identifiability.png")
fig.savefig(out)
print(f"已写入 {out}")
print(f"外推所需独立版本数 ≈ {n_req:.0f}")
