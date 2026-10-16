#!/usr/bin/env python3
"""
项目指纹分类器的类别不平衡对照

主分析报告的是项目判别的准确率，并把它与 1/k（k 个项目）的均匀猜测基线相比。
两个基准的项目规模都高度不平衡（BugsInPy 的 youtube-dl 占 43/82 个版本，
Defects4J 的 Lang 占 131/352 个实例），因此单看准确率会被多数类抬高。本脚本
补三类对照指标：

  accuracy          总体准确率（与主分析一致）
  balanced accuracy 各类召回率的宏平均，对类别不平衡不敏感
  macro F1          各类 F1 的宏平均
  majority          恒预测多数类的准确率（不平衡下的"懒惰"基线）

划分方式与主分析相同：实例级分层 5 折，以及按缺陷版本分组的 5 折。

用法：python3 scripts/analyze_fingerprint_baselines.py
"""
import json
import os
import sys
import warnings

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (accuracy_score, balanced_accuracy_score, f1_score)
from sklearn.model_selection import GroupKFold, StratifiedKFold

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from robust_lib import L2, load_bip, load_d4j  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT = os.path.join(ROOT, "results", "fingerprint-baselines.json")


def metrics(y_true, y_pred):
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)),
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
    }


def evaluate(df, cols, grouped):
    X, y = df[cols].values, df["project"].values
    clf = RandomForestClassifier(n_estimators=300, random_state=0)
    if grouped:
        split = list(GroupKFold(n_splits=5).split(X, y, groups=df["version"]))
    else:
        # 与 analyze_all.fingerprint 的 cross_val_score(..., cv=5) 保持一致：
        # 不分层不打乱，否则报告出的准确率会与主分析差千分之几。
        split = list(StratifiedKFold(n_splits=5).split(X, y))
    # 逐折计算再平均——主分析就是这样做的，汇总预测再算会差千分之几
    per_fold = []
    for tr, te in split:
        clf.fit(X[tr], y[tr])
        per_fold.append(metrics(y[te], clf.predict(X[te])))
    return {k: float(np.mean([f[k] for f in per_fold])) for k in per_fold[0]}


def main():
    out = {}
    for name, df in (("Defects4J", load_d4j()), ("BugsInPy", load_bip())):
        classical = ["log_size", "coverage"] + (
            ["mutation"] if df["mutation"].notna().any() else [])
        counts = df["project"].value_counts()
        maj = float(counts.iloc[0] / counts.sum())
        print(f"\n### {name}  ({df['project'].nunique()} 个项目 / "
              f"{df['version'].nunique()} 版本 / {len(df)} 实例)")
        print(f"  最多项目的实例占比（多数类基线）: {maj:.3f}"
              f"   [1/k = {1/df['project'].nunique():.3f}]")
        print(f"  {'特征块':<12}{'划分':<10}{'accuracy':>10}{'balanced':>10}{'macroF1':>10}")
        rec = {"n_proj": int(df["project"].nunique()), "n_inst": int(len(df)),
               "uniform_baseline": float(1 / df["project"].nunique()),
               "majority_baseline": maj, "models": {}}
        for nm, cols in (("behaviour", L2), ("classical", classical),
                         ("all", classical + L2)):
            rec["models"][nm] = {}
            for grp, glab in ((False, "instance"), (True, "version-grouped")):
                m = evaluate(df, cols, grp)
                rec["models"][nm][glab] = m
                print(f"  {nm:<12}{glab:<10}{m['accuracy']:>10.3f}"
                      f"{m['balanced_accuracy']:>10.3f}{m['macro_f1']:>10.3f}")
        out[name] = rec

    json.dump(out, open(OUT, "w"), indent=1)
    print(f"\n结果已写入 {OUT}")


if __name__ == "__main__":
    main()
