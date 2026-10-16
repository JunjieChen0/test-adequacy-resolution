#!/usr/bin/env python3
"""
within-project 固定效应的可识别性：单版本 project 在分组交叉验证中如何处理

问题
----
模型用 drop_first 的 project 哑变量估计项目固定效应。一个只贡献**一个**缺陷版本
的 project 没有任何 within-project 对比可言：它只能提供一个水平，不提供水平差。
更具体地，当按缺陷版本分组切折、且该项目恰好落在测试折时，训练折中不含该项目的
任何一行，其哑变量列在训练集中恒为 0。np.linalg.lstsq 在 rcond=None 下返回最小
范数解，该列系数恰为 0，于是这些测试行是按**参考类别**（drop_first 丢掉的第一个
项目）的水平预测的。

本脚本把这件事量化，而不是留给读者去猜：

  1. 每个 project 贡献多少缺陷版本；
  2. 分组 5 折下，有多少条测试行的 project 在训练折中完全缺席（"孤立行"）；
  3. 这些孤立行对应的哑变量系数实际被拟合成多少；
  4. 敏感性：把单版本 project 整体剔除后，主比较（full - classical，含固定效应）
     是否变号。

判据：剔除后 ΔR² 符号不变，则结论不依赖单版本 project。

在 Defects4J 上每个 project 都有 >= 5 个版本，本问题不出现；它只影响 BugsInPy
的 sanic（1 版本 / 3 实例）。

用法：python3 scripts/analyze_fe_identification.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from robust_lib import L2, Panel, load_bip, load_d4j  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT = os.path.join(ROOT, "results", "fe-identification.json")

K, SEED = 5, 7


def classical_cols(df):
    return ["log_size", "coverage"] + (["mutation"] if df["mutation"].notna().any() else [])


def orphan_report(P):
    """复刻 Panel.cv_r2 的折划分，统计训练折中缺席的 project，并读出其哑变量系数。"""
    off = 1 + len(P.cols)                       # 哑变量在 X 中的起始列
    proj = P.df["project"].values
    keys = list(P.folds)
    rng = np.random.default_rng(SEED)
    assign = np.array_split(rng.permutation(len(keys)), K)
    orphan_total = 0
    detail = {}
    for part in assign:
        te = np.concatenate([P.folds[keys[i]] for i in part])
        mask = np.ones(len(P.y), bool)
        mask[te] = False
        tr = np.where(mask)[0]
        tr_proj = set(proj[tr])
        orph = np.array([i for i in te if proj[i] not in tr_proj], dtype=int)
        orphan_total += len(orph)
        if len(orph):
            beta, *_ = np.linalg.lstsq(P.X[tr], P.y[tr], rcond=None)
            for name in sorted({proj[i] for i in orph}):
                if f"p_{name}" not in P.dummy_cols:
                    # 参考类别：本来就是被 drop 的基准，不构成问题
                    detail.setdefault(name, {"reference_category": True,
                                             "orphan_rows": 0, "mean_abs_coef": None})
                    continue
                c = off + P.dummy_cols.index(f"p_{name}")
                d = detail.setdefault(name, {"reference_category": False,
                                             "orphan_rows": 0, "coefs": []})
                d["orphan_rows"] += int(sum(1 for i in orph if proj[i] == name))
                d["coefs"].append(float(beta[c]))
    for name, d in detail.items():
        if d.get("coefs"):
            d["mean_abs_coef"] = float(np.mean(np.abs(d["coefs"])))
            d["max_abs_coef"] = float(np.max(np.abs(d["coefs"])))
            del d["coefs"]
    return orphan_total, detail


def main():
    out = {}
    for name, df in (("Defects4J", load_d4j()), ("BugsInPy", load_bip())):
        cls = classical_cols(df)
        full = cls + L2
        g = df.groupby("project").agg(instances=("version", "size"),
                                      versions=("version", "nunique"))
        single = sorted(g.index[g["versions"] < 2])
        print(f"\n### {name}: {len(df)} 实例 / {df['version'].nunique()} 版本 / "
              f"{df['project'].nunique()} 项目")
        print(f"  drop_first 参考类别（排序第一个）= {sorted(df['project'].unique())[0]}")
        print(f"  版本数最少的 project: {g['versions'].min()}；"
              f"单版本 project = {single if single else '无'}")
        print("  " + g.sort_values("versions").to_string().replace("\n", "\n  "))

        P = Panel(df, full, True)
        orphan, detail = orphan_report(P)
        print(f"\n  分组 {K} 折下的孤立测试行：{orphan}/{len(P.y)} = {orphan/len(P.y):.2%}")
        for nm, d in sorted(detail.items()):
            if d["reference_category"]:
                print(f"    {nm}: 参考类别（哑变量已被 drop），不作判断")
            else:
                print(f"    {nm}: 孤立行 {d['orphan_rows']} 条，"
                      f"该列系数 |β| 最大 {d['max_abs_coef']:.2e}"
                      f"（全零列 -> 最小范数解给 0）")

        # 敏感性：剔除单版本 project
        d2 = df[~df["project"].isin(single)] if single else df
        rows = []
        for lab, d in (("全部 project", df), ("剔除单版本 project", d2)):
            a = Panel(d, full, True).cv_r2(K, SEED)
            b = Panel(d, cls, True).cv_r2(K, SEED)
            rows.append({"label": lab, "n_proj": int(d["project"].nunique()),
                         "n_inst": int(len(d)), "full": float(a),
                         "classical": float(b), "delta": float(a - b)})
            print(f"    {lab:22s} {d['project'].nunique()} 项目  "
                  f"full={a:.3f} classical={b:.3f} ΔR²={a-b:+.3f}")
        sign_ok = all((r["delta"] > 0) == (rows[0]["delta"] > 0) for r in rows)

        out[name] = {
            "n_proj": int(df["project"].nunique()),
            "n_ver": int(df["version"].nunique()),
            "n_inst": int(len(df)),
            "reference_category": sorted(df["project"].unique())[0],
            "min_versions_per_project": int(g["versions"].min()),
            "single_version_projects": single,
            "orphan_rows": int(orphan),
            "orphan_fraction": float(orphan / len(P.y)),
            "orphan_detail": detail,
            "sensitivity": rows,
            "sign_preserved": bool(sign_ok),
        }
        print(f"  判据（剔除单版本 project 后 ΔR² 符号不变）：" +
              ("通过" if sign_ok else "未通过"))

    json.dump(out, open(OUT, "w"), indent=1)
    print(f"\n结果已写入 {OUT}")


if __name__ == "__main__":
    main()
