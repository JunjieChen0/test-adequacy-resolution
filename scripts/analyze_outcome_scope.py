#!/usr/bin/env python3
"""
outcome 的作用域稳健性：分母用整个项目的缺陷集，还是只用该 revision 可用的缺陷？

问题
----
主分析里的 outcome 是

    detect(S) = |{ f in F_P : trigger(f) in S }| / |F_P|

其中 F_P 是项目 P 的**全部**缺陷。但每个观测是在某一个 fault version v 的
fixed revision 上取的测试子集。一个在更晚 revision 才被修复的缺陷，其触发
测试在 v 上根本不存在——把它算进分母，等于要求 v 上的套件去检测一个当时还
没有测试能暴露的缺陷。

本脚本量化这一点，并给出 revision 匹配的对照定义：

    detect_v(S) = |{ f in F_{P,v} : trigger(f) in S }| / |F_{P,v} |
    F_{P,v} = { f in F_P : trigger(f) 属于 v 的完整测试清单 }

判据是：两种定义下，"汇总分析偏向更大模型、控制项目后翻转" 这条结论是否都成立。

用法：python3 scripts/analyze_outcome_scope.py
"""
import collections
import glob
import json
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from robust_lib import L2, Panel  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "results", "outcome-scope.json")

MODELS = {
    "size": ["log_size"],
    "classical": ["log_size", "coverage", "mutation"],
    "size+behaviour": ["log_size"] + L2,
    "full": ["log_size", "coverage", "mutation"] + L2,
}


def cls_of_path(r):
    """与 analyze_fixed_effects.cls_of_path 保持一致：路径 -> 点号类名。"""
    return r[:-5].replace("/", ".") if r.endswith(".java") else r


def instance_meta():
    """每个 (project, bug) 的完整测试清单与触发测试。"""
    out = collections.defaultdict(dict)
    for f in glob.glob(os.path.join(DATA, "instance-*.json")):
        d = json.load(open(f))
        out[d["project"]][d["bug"]] = {
            "all": {c.strip() for c in d.get("all.txt", [])},
            "trig": {t.split("::")[0] for t in d.get("trigger.txt", []) if "::" in t},
        }
    return out


def build(meta):
    rows = []
    for f in glob.glob(os.path.join(DATA, "subsets", "*.json")):
        d = json.load(open(f))
        if d.get("status") != "ok":
            continue
        cov = (d.get("steps", {}) or {}).get("coverage", {}) or {}
        mut = (d.get("steps", {}) or {}).get("mutation", {}) or {}
        if cov.get("line_cov_pct") is None or mut.get("mutation_score_pct") is None:
            continue
        p, b = d["project"], d["bug"]
        if p not in meta or b not in meta[p]:
            continue
        af = os.path.join(DATA, "assertions",
                          f"{p}-{b}-f{d['frac']}-r{d['rep']}.json")
        if not os.path.exists(af):
            continue
        kept = {cls_of_path(t) for t in
                (d.get("kept_tests_final") or d.get("kept_tests") or [])}
        bp = meta[p]
        this_revision = bp[b]["all"]
        eligible = [x for x in bp if bp[x]["trig"] & this_revision]
        if not eligible:
            continue
        dv = json.load(open(af)).get("derived", {})
        size = d.get("n_test_final") or d.get("n_test_kept")
        if any(dv.get(k) is None for k in L2) or not size:
            continue
        rows.append({
            "project": p, "version": f"{p}-{b}", "size": size,
            "coverage": cov["line_cov_pct"], "mutation": mut["mutation_score_pct"],
            "detect_project": sum(1 for x in bp if bp[x]["trig"] & kept) / len(bp),
            "detect_revision": sum(1 for x in eligible if bp[x]["trig"] & kept) / len(eligible),
            "n_faults_project": len(bp), "n_faults_revision": len(eligible),
            **{k: dv[k] for k in L2},
        })
    df = pd.DataFrame(rows)
    df["log_size"] = np.log(df["size"].astype(float))
    return df


def main():
    meta = instance_meta()
    df = build(meta)
    if df.empty:
        raise SystemExit("没有可用记录")

    print(f"样本 {df['version'].nunique()} 版本 / {len(df)} 实例")
    print(f"分母：整个项目的缺陷集 平均 {df['n_faults_project'].mean():.1f}")
    print(f"      该 revision 可用的缺陷 平均 {df['n_faults_revision'].mean():.1f}"
          f"  ({df['n_faults_revision'].mean()/df['n_faults_project'].mean()*100:.0f}%)")
    for c, lab in (("detect_project", "整个项目"), ("detect_revision", "该 revision")):
        print(f"  detect({lab:9s}) 均值 {df[c].mean():.3f}  SD {df[c].std():.3f}")
    print(f"  两者相关 r = {df['detect_project'].corr(df['detect_revision']):.3f}\n")

    print(f"{'分母':>14s}{'FE':>4s}" +
          "".join(f"{n:>14s}" for n in MODELS) + f"{'ΔR²':>9s}{'最优':>16s}")
    rec = {}
    for col, label in (("detect_project", "整个项目"), ("detect_revision", "该 revision")):
        d = df.rename(columns={col: "detect_rate"})
        rec[col] = {}
        for fe in (False, True):
            v = {n: float(Panel(d, cs, with_fe=fe).cv_r2(5, 7))
                 for n, cs in MODELS.items()}
            delta = v["full"] - v["classical"]
            rec[col][f"fe={int(fe)}"] = {"cv": v, "delta": delta,
                                         "best": max(v, key=v.get)}
            print(f"{label:>14s}{int(fe):>4d}" +
                  "".join(f"{v[n]:>14.3f}" for n in MODELS) +
                  f"{delta:>+9.3f}{max(v, key=v.get):>16s}")

    summary = {
        "n_ver": int(df["version"].nunique()), "n_inst": int(len(df)),
        "mean_faults_project": float(df["n_faults_project"].mean()),
        "mean_faults_revision": float(df["n_faults_revision"].mean()),
        "detect_correlation": float(df["detect_project"].corr(df["detect_revision"])),
        "cv": rec,
    }
    json.dump(summary, open(OUT, "w"), indent=1)
    print(f"\n结果已写入 {OUT}")

    fp = rec["detect_project"]
    rv = rec["detect_revision"]
    ok = (all(fp[k]["best"] == rv[k]["best"] for k in fp)
          and all((fp[k]["delta"] > 0) == (rv[k]["delta"] > 0) for k in fp))
    print("判据（两种定义下最优模型与 ΔR² 符号是否一致）：" + ("通过" if ok else "未通过"))


if __name__ == "__main__":
    main()
