#!/usr/bin/env python3
"""
跨基准对比图：Defects4J（Java）与 BugsInPy（Python）

左：分辨力曲线对比——ΔR² 的 95% 区间宽度随独立缺陷版本数收缩
右：项目判别力对比——各特征集识别项目的准确率 vs 随机基线

两个基准结果分别来自 results/identifiability.json 与 results/bip-crossbench.json。
用法：python3 scripts/plot_crossbench.py
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(ROOT, "results")

import sys
LANG = "en" if "--lang=en" in sys.argv else "zh"
L = {
    "zh": {"x": "独立缺陷版本数", "y": "ΔR² 的 95% 区间宽度",
           "eff": "常见效应量级 ≈0.05", "ta": "(a) 分辨力：两个基准的区间宽度收缩",
           "yb": "识别项目的准确率", "tb": "(b) 项目指纹：特征能否识别项目",
           "labels": ["仅行为匹配", "仅传统指标", "全部", "随机基线"],
           "keys": ["仅行为匹配", "仅传统指标", "全部"]},
    "en": {"x": "Independent fault versions", "y": "Width of 95% interval for $\\Delta R^2$",
           "eff": "Typical effect magnitude ≈0.05",
           "ta": "(a) Resolution across two benchmarks",
           "yb": "Project-classification accuracy",
           "tb": "(b) Project fingerprint: do features identify projects?",
           "labels": ["Behavioural\nonly", "Classical\nmetrics only", "All\nfeatures", "Random\nbaseline"],
           "keys": ["仅行为匹配", "仅传统指标", "全部"]},
}[LANG]

plt.rcParams.update({"font.size": 9, "axes.linewidth": 0.8,
                     "axes.spines.top": False, "axes.spines.right": False,
                     "figure.dpi": 300, "savefig.bbox": "tight",
                     "font.family": "DejaVu Sans" if LANG == "en" else "sans-serif"})

d4j = json.load(open(os.path.join(RES, "identifiability.json")))
bip_path = os.path.join(RES, "bip-crossbench.json")
bip = json.load(open(bip_path)) if os.path.exists(bip_path) else None

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.2, 2.9))

# ---------- 左：分辨力曲线 ----------
for data, name, color, marker in ((d4j, "Defects4J (Java)", "#1f4e79", "o"),
                                  (bip, "BugsInPy (Python)", "#c00000", "s")):
    if not data:
        continue
    c = data.get("resolution_curve", [])
    xs = [r["n_ver"] for r in c]
    ys = [r["width"] for r in c]
    if len(xs) >= 2:
        ax1.plot(xs, ys, marker + "-", color=color, lw=1.4, ms=4, label=name)

ax1.axhline(0.05, ls="--", lw=1.0, color="#888888")
ax1.text(0.02, 0.062, L["eff"], ha="left", va="bottom",
         transform=ax1.get_yaxis_transform(), fontsize=7.5, color="#555555")
ax1.set_yscale("log")
ax1.set_xscale("log")
_ticks = sorted({r["n_ver"] for r in d4j.get("resolution_curve", [])}
                | {r["n_ver"] for r in (bip or {}).get("resolution_curve", [])})
ax1.set_xticks(_ticks)
ax1.set_xticklabels([str(t) for t in _ticks], fontsize=8)
ax1.minorticks_off()
ax1.set_xlim(min(_ticks) * 0.85, max(_ticks) * 1.15)
ax1.set_xlabel(L["x"])
ax1.set_ylabel(L["y"])
ax1.legend(frameon=False, fontsize=7.5, loc="upper right")
ax1.set_title(L["ta"], fontsize=9, pad=6)

# ---------- 右：项目判别力 ----------
labels = L["labels"]
keys = L["keys"]
width = 0.36
xs = np.arange(len(labels))
for i, (data, name, color) in enumerate(((d4j, "Defects4J", "#1f4e79"),
                                         (bip, "BugsInPy", "#c00000"))):
    if not data:
        continue
    fp = data.get("fingerprint") or {}
    vals = [fp.get(k, np.nan) for k in keys] + [data.get("fingerprint_baseline", np.nan)]
    ax2.bar(xs + (i - 0.5) * width, vals, width, color=color, label=name)
ax2.set_xticks(xs)
ax2.set_xticklabels(labels, fontsize=8)
ax2.set_ylabel(L["yb"])
ax2.set_ylim(0, 1.32)
ax2.axhline(1 / 7, ls="--", lw=0.9, color="#888888")
ax2.axhline(0.25, ls=":", lw=0.9, color="#888888")
ax2.legend(frameon=False, fontsize=7.5, loc="upper center", ncol=2,
           handlelength=1.2, columnspacing=1.2)
ax2.set_title(L["tb"], fontsize=9, pad=6)

out = os.path.join(RES, "fig-crossbench-en.png" if LANG == "en" else "fig-crossbench.png")
fig.savefig(out)
print(f"已写入 {out}")
