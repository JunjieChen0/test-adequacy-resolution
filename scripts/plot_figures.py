#!/usr/bin/env python3
"""
论文用图：从 results/analysis.json 与 results/simulation-confounding.json 生成

图 1（fig-identifiability）：(a) 最小可检测效应曲线 (b) 项目混淆的假阳性率
图 2（fig-crossbench）    ：(a) 两基准的最小可检测效应曲线 (b) 项目判别力

所有数字都取自分析脚本的落盘结果，避免图文不一致。
用法：python3 scripts/plot_figures.py [--lang en|zh|both]
"""
import argparse
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RES = os.path.join(ROOT, "results")

AN = json.load(open(os.path.join(RES, "analysis.json")))
SIM = json.load(open(os.path.join(RES, "simulation-confounding.json")))

TXT = {
    "en": {"x": "Independent fault versions", "y": "Interval half-width (resolution)",
           "eff": "Typical claimed magnitude ≈0.05",
           "ta1": "(a) Resolution of the estimate (Defects4J)",
           "ta2": "(a) Resolution of the estimate",
           "ty": "P(reporting a material increment)",
           "tx": "Project-level confounding $\\rho$",
           "tp": "Pooled analysis", "tf": "Project fixed effects",
           "tb": "(b) False increments under confounding",
           "yb": "Project-classification accuracy",
           "tb2": "(b) Project fingerprint",
           "labels": ["Behavioural\nonly", "Classical\nmetrics only",
                      "All\nfeatures", "Random\nbaseline"],
           "keys": ["behaviour", "classical", "all"],
           "vleg": "version-level bootstrap", "pleg": "project-level bootstrap"},
    "zh": {"x": "独立缺陷版本数", "y": "区间半宽（分辨力）",
           "eff": "常见主张量级 ≈0.05",
           "ta1": "(a) 估计的分辨力随样本量提高",
           "ta2": "(a) 两个基准的分辨力",
           "ty": "报告出实质性增量的概率",
           "tx": "项目级混淆强度 $\\rho$",
           "tp": "汇总分析", "tf": "项目内分析（固定效应）",
           "tb": "(b) 混杂下的虚假增量比例",
           "yb": "识别项目的准确率",
           "tb2": "(b) 项目指纹：特征能否识别项目",
           "labels": ["仅行为匹配", "仅传统指标", "全部", "随机基线"],
           "keys": ["behaviour", "classical", "all"],
           "vleg": "按版本重采样", "pleg": "按项目重采样"},
}


def style(lang):
    plt.rcParams.update({
        "font.size": 9, "axes.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False,
        "figure.dpi": 300, "savefig.bbox": "tight",
        "font.family": "DejaVu Sans" if lang == "en" else "sans-serif"})


def fig_identifiability(lang):
    t = TXT[lang]
    d4j = AN["Defects4J"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.2, 2.9))

    for scheme, color, marker, lab in (("version", "#1f4e79", "o", t["vleg"]),
                                       ("project", "#7a9cc6", "s", t["pleg"])):
        c = d4j["mde"][scheme]["curve"]
        c = [r for r in c if r["mde"] < 1.0]
        ax1.plot([r["n_ver"] for r in c], [r["mde"] for r in c],
                 marker + "-", color=color, lw=1.4, ms=4, label=lab)
    ax1.axhline(0.05, ls="--", lw=1.0, color="#c00000")
    ax1.text(0.98, 0.055, t["eff"], ha="right", va="bottom", fontsize=7.5,
             color="#c00000", transform=ax1.get_yaxis_transform())
    ax1.set_xscale("log"); ax1.set_yscale("log")
    ticks = [r["n_ver"] for r in d4j["mde"]["version"]["curve"] if r["mde"] < 1.0]
    ax1.set_xticks(ticks); ax1.set_xticklabels([str(x) for x in ticks], fontsize=8)
    ax1.minorticks_off(); ax1.set_xlim(min(ticks) * 0.85, max(ticks) * 1.15)
    ax1.set_xlabel(t["x"]); ax1.set_ylabel(t["y"])
    ax1.legend(frameon=False, fontsize=7.5, loc="upper right")
    ax1.set_title(t["ta1"], fontsize=9, pad=6)

    sub = [r for r in SIM if r["n_inst"] == 400]
    rhos = [r["rho"] for r in sub]
    ax2.plot(range(len(rhos)), [r["pool"] for r in sub], "o-",
             color="#c00000", lw=1.4, ms=4, label=t["tp"])
    ax2.plot(range(len(rhos)), [r["fe"] for r in sub], "s-",
             color="#1f4e79", lw=1.4, ms=4, label=t["tf"])
    ax2.set_xticks(range(len(rhos)))
    ax2.set_xticklabels([f"{r:.1f}" for r in rhos])
    ax2.set_xlabel(t["tx"]); ax2.set_ylabel(t["ty"]); ax2.set_ylim(-0.03, 1.0)
    ax2.legend(frameon=False, fontsize=7.5, loc="upper left")
    ax2.set_title(t["tb"], fontsize=9, pad=6)

    fig.tight_layout()
    out = os.path.join(RES, f"fig-identifiability{'-en' if lang=='en' else ''}.png")
    fig.savefig(out); plt.close(fig); print("已写入", out)


def fig_crossbench(lang):
    t = TXT[lang]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.2, 2.9))
    for bench, name, color, marker in (("Defects4J", "Defects4J (Java)", "#1f4e79", "o"),
                                       ("BugsInPy", "BugsInPy (Python)", "#c00000", "s")):
        c = [r for r in AN[bench]["mde"]["version"]["curve"] if 0.02 < r["mde"] < 1.2]
        ax1.plot([r["n_ver"] for r in c], [r["mde"] for r in c],
                 marker + "-", color=color, lw=1.4, ms=4, label=name)
    ax1.axhline(0.05, ls="--", lw=1.0, color="#888888")
    ax1.text(0.02, 0.055, t["eff"], ha="left", va="bottom", fontsize=7.5,
             color="#555555", transform=ax1.get_yaxis_transform())
    ax1.set_xscale("log"); ax1.set_yscale("log")
    ticks = sorted({r["n_ver"] for b in AN.values()
                    for r in b["mde"]["version"]["curve"] if 0.02 < r["mde"] < 1.2})
    ax1.set_xticks(ticks); ax1.set_xticklabels([str(x) for x in ticks], fontsize=8)
    ax1.minorticks_off(); ax1.set_xlim(min(ticks) * 0.85, max(ticks) * 1.15)
    ax1.set_xlabel(t["x"]); ax1.set_ylabel(t["y"])
    ax1.legend(frameon=False, fontsize=7.5, loc="upper right")
    ax1.set_title(t["ta2"], fontsize=9, pad=6)

    xs = np.arange(4); w = 0.36
    for i, (bench, name, color) in enumerate((("Defects4J", "Defects4J", "#1f4e79"),
                                              ("BugsInPy", "BugsInPy", "#c00000"))):
        fp = AN[bench]["fingerprint"]
        # 画版本分组口径，使图与 Table 6 及摘要的准确率一致；实例级数值
        # 记载于 *_grouped 之外的键，正文两者都报告
        vals = [fp[f"{k}_grouped"] for k in t["keys"]] + [fp["baseline"]]
        ax2.bar(xs + (i - 0.5) * w, vals, w, color=color, label=name)
    ax2.set_xticks(xs); ax2.set_xticklabels(t["labels"], fontsize=8)
    ax2.set_ylabel(t["yb"]); ax2.set_ylim(0, 1.32)
    ax2.axhline(1 / 7, ls="--", lw=0.9, color="#888888")
    ax2.legend(frameon=False, fontsize=7.5, loc="upper center", ncol=2,
               handlelength=1.2, columnspacing=1.2)
    ax2.set_title(t["tb2"], fontsize=9, pad=6)

    fig.tight_layout()
    out = os.path.join(RES, f"fig-crossbench{'-en' if lang=='en' else ''}.png")
    fig.savefig(out); plt.close(fig); print("已写入", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["en", "zh", "both"], default="both")
    a = ap.parse_args()
    for lang in (["en", "zh"] if a.lang == "both" else [a.lang]):
        style(lang)
        fig_identifiability(lang)
        fig_crossbench(lang)


if __name__ == "__main__":
    main()
