#!/usr/bin/env python3
"""
模拟研究：项目混淆能否凭空造出"指标有增量价值"的结论？

问题
----
设某指标在**项目内部**对缺陷检出率毫无作用（真实效应 = 0），但它的**项目均值**
与项目的基准检出率相关（记为 ρ）。这类研究通常按实例汇总分析、不控制项目身份。
问：在多大的 ρ 和多大的样本量下，这种分析会**稳定地**报告出显著的增量预测力？

这不是假想。本项目在 Defects4J 上就得到过这样的结果：断言层面的行为匹配特征
在项目内无效应（项目内 ρ=−0.075, p=0.266），在跨项目汇总下却显著
（ρ=−0.254, p=0.033）。

设计
----
参数按 Defects4J 实测标定（跨项目与组内标准差，见下表），只把 ρ 当作自变量扫描。
数据生成过程里**行为特征对结果变量的系数恒为 0**——任何被"检出"的增量都是伪影。

标定值（Defects4J，7 项目 / 139 独立版本 / 352 实例，2026-10-16 按扩样后样本重算）：
    跨项目 SD：检出率 0.071，log 套件规模 0.86，覆盖率 5.04，强断言占比 0.121
    组内 SD：  检出率 0.125，log 套件规模 0.68，覆盖率 16.79，强断言占比 0.104
    组内斜率：规模 +0.062，覆盖率 +0.0031
    拟合残差 SD：0.106（调至使组内检出率 SD ≈ 0.125）

用法：python3 scripts/simulate_confounding.py
"""
import os

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import GroupKFold

# ---- 标定常数 ----
SD_PROJ_DET = 0.0707      # 检出错率跨项目 SD
SD_WITHIN_DET = 0.1251    # 检出率组内 SD
SD_PROJ_SIZE = 0.8583
SD_WITHIN_SIZE = 0.6776
SD_PROJ_COV = 5.0407
SD_WITHIN_COV = 16.7927
SD_PROJ_BEHAV = 0.1212
SD_WITHIN_BEHAV = 0.1036
RESID_SD = 0.106          # 调至使组内检出率 SD ≈ 0.125

B_SIZE = 0.062            # 组内规模效应（按实测斜率）
B_COV = 0.0031            # 组内覆盖率效应
B_BEHAV = 0.0             # **真实效应为零**——本模拟的核心设定


def simulate(n_proj, n_ver, rho, n_sub=2, seed=0):
    rng = np.random.default_rng(seed)
    # 项目级：行为特征均值与基准检出率按 rho 相关，其余独立
    u = rng.normal(size=n_proj)
    v = rng.normal(size=n_proj)
    z_behav = u
    z_det = rho * u + np.sqrt(max(1 - rho ** 2, 0)) * v
    mu_det = 0.18 + SD_PROJ_DET * z_det
    mu_behav = 0.55 + SD_PROJ_BEHAV * z_behav
    mu_size = 2.5 + SD_PROJ_SIZE * rng.normal(size=n_proj)
    mu_cov = 35 + SD_PROJ_COV * rng.normal(size=n_proj)

    rows = []
    for p in range(n_proj):
        for k in range(n_ver):
            size = mu_size[p] + rng.normal(0, SD_WITHIN_SIZE)
            cov = mu_cov[p] + rng.normal(0, SD_WITHIN_COV)
            behav = np.clip(mu_behav[p] + rng.normal(0, SD_WITHIN_BEHAV), 0, 1)
            y = (mu_det[p]
                 + B_SIZE * (size - mu_size[p])
                 + B_COV * (cov - mu_cov[p])
                 + B_BEHAV * (behav - mu_behav[p])
                 + rng.normal(0, RESID_SD))
            for s in range(n_sub):
                rows.append({"project": f"P{p}", "version": f"P{p}-{k}",
                             "log_size": size, "coverage": cov,
                             "behav": behav, "detect_rate": y})
    return pd.DataFrame(rows)


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
    BASE = ["log_size", "coverage"]
    R = int(os.environ.get("SIM_R", "2000"))
    print("模拟设定：行为特征的真实（项目内）效应恒为 0")
    print(f"每个单元 {R} 次重复（蒙特卡洛标准误在 p≈0.8 处约 "
          f"{np.sqrt(0.8*0.2/R):.3f}）\n")
    print(f"{'ρ':>5}{'项目数':>7}{'每项目版本':>10}{'实例数':>8}"
          f"{'汇总分析阳性率':>16}{'项目内分析阳性率':>18}")
    out = []
    for rho in (0.0, 0.3, 0.6, 0.9):
        for n_proj, n_ver in ((7, 5), (10, 10), (10, 20)):
            # 阈值是研究者的判断，因此同一次模拟里把所有阈值一起统计，
            # 而不是按阈值重跑——重新模拟是纯粹的重复计算。
            ths = (0.02, 0.05, 0.10)
            hits = {("pool", t): 0 for t in ths} | {("fe", t): 0 for t in ths}
            ok = 0
            for r in range(R):
                d = simulate(n_proj, n_ver, rho, seed=r * 1000 + int(rho * 10))
                try:
                    a = cvr2(d, BASE + ["behav"], False) - cvr2(d, BASE, False)
                    b = cvr2(d, BASE + ["behav"], True) - cvr2(d, BASE, True)
                except Exception:
                    continue
                ok += 1
                for t in ths:
                    hits[("pool", t)] += (a > t)
                    hits[("fe", t)] += (b > t)
            if not ok:
                continue
            rec = {"rho": rho, "n_proj": n_proj, "n_ver": n_ver,
                   "n_sub": 2, "n_inst": n_proj * n_ver * 2,
                   "pool": hits[("pool", 0.02)] / ok,
                   "fe": hits[("fe", 0.02)] / ok, "R": ok,
                   "mc_se": float(np.sqrt(0.25 / ok))}
            rec["by_threshold"] = {
                f"{t:.2f}": {"pooled": hits[("pool", t)] / ok,
                             "fe": hits[("fe", t)] / ok} for t in ths}
            out.append(rec)
            print(f"{rho:>5.1f}{n_proj:>7}{n_ver:>10}{rec['n_inst']:>8}"
                  f"{rec['pool']:>16.3f}{rec['fe']:>18.3f}", flush=True)
    import json
    path = os.path.join(os.path.dirname(__file__), "..",
                        "results", "simulation-confounding.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print(f"\n结果已写入 {os.path.normpath(path)}")
    print("理想值应接近 0（真实效应为零）。汇总分析一列越高，说明该研究设计越不可信。")


if __name__ == "__main__":
    main()
