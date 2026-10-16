#!/usr/bin/env python3
"""
两基准共用的建模与重采样工具

为什么要单独抽出来
------------------
稳健性实验要在同一套定义下反复重算（逐项目留一 14 次、门槛 4 档 × 2 基准、
两种自助力层级），如果每次都走 pandas 的 get_dummies + GroupKFold，耗时会到
小时级。这里把设计矩阵预先构造成 numpy 数组，按行索引切折，
把单次交叉验证的常数开销压到最低。

两个基准在此统一成同一张表：
    project, version, size, coverage, [mutation], <L2 特征>, detect_rate
Defects4J 有 mutation，BugsInPy 没有（该列全为 NaN，建模时自动剔除）。
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

L2 = ["assertions_per_method", "strong_ratio", "predicate_ratio", "calls_per_assertion"]


def load_d4j():
    from analyze_fixed_effects import load as _load
    df = _load()
    df["log_size"] = np.log(df["size"].astype(float))
    df["version"] = df["project"] + "-" + df["bug"]
    return df[["project", "version", "size", "log_size", "coverage",
               "mutation"] + L2 + ["detect_rate"]]


def load_bip():
    rows = []
    for f in glob.glob(os.path.join(ROOT, "data-bip", "subsets", "*.json")):
        d = json.load(open(f))
        if d.get("status") != "ok":
            continue
        for i in d["instances"]:
            fe = i.get("features")
            if not fe or i.get("line_cov_pct") is None or i.get("detect_rate") is None:
                continue
            rows.append({"project": d["project"], "version": f"{d['project']}-{d['bug']}",
                         "size": i["k"], "coverage": i["line_cov_pct"],
                         "detect_rate": i["detect_rate"],
                         **{k: fe[k] for k in L2}})
    df = pd.DataFrame(rows)
    df["log_size"] = np.log(df["size"].astype(float))
    df["mutation"] = np.nan
    return df[["project", "version", "size", "log_size", "coverage",
               "mutation"] + L2 + ["detect_rate"]]


def available_models(df):
    """按该基准实际拥有的列返回模型定义（BugsInPy 无 mutation）。"""
    base_classical = ["log_size", "coverage"]
    full_classical = ["log_size", "coverage"] + (["mutation"] if df["mutation"].notna().any() else [])
    return {
        "size":             ["log_size"],
        "classical":        full_classical,
        "size+behaviour":   ["log_size"] + L2,
        "full":             full_classical + L2,
    }, base_classical


class Panel:
    """把一张表编译成可直接切折的设计矩阵。"""

    def __init__(self, df, cols, with_fe):
        self.df = df.reset_index(drop=True)
        self.cols = list(cols)
        self.with_fe = with_fe
        parts = [df[c].astype(float).values[:, None] for c in self.cols]
        if with_fe:
            dum = pd.get_dummies(df["project"], prefix="p", drop_first=True).astype(float)
            self.dummy_cols = list(dum.columns)
            parts.append(dum.values)
        else:
            self.dummy_cols = []
        self.X = np.hstack(parts)
        self.X = np.hstack([np.ones((len(df), 1)), self.X])   # 截距
        self.y = df["detect_rate"].astype(float).values
        self.groups = df["version"].values
        self.projects = df["project"].values
        # 项目固定效应的可识别性。一个 project 至少要贡献两个缺陷版本，才谈得上
        # within-project 对比；只贡献一个版本的 project 只能给出水平，给不出水平差。
        # 当这样的 project 整体落入测试折时，它的哑变量列在训练集中恒为 0，
        # np.linalg.lstsq 在 rcond=None 下返回最小范数解，把该列系数定为 0，
        # 于是这些测试行按参考类别（drop_first 丢掉的第一个项目）的水平预测。
        # 这里只做记录，不改变设计矩阵，以免动到主分析的数字。
        # 量化见 scripts/analyze_fe_identification.py。
        _vpp = self.df.groupby("project")["version"].nunique()
        self.fe_identifiable = sorted(_vpp.index[_vpp >= 2])
        self.fe_single_version = sorted(_vpp.index[_vpp < 2])
        # 分组 -> 行索引
        self.folds = {}
        for g in pd.unique(self.groups):
            self.folds[g] = np.where(self.groups == g)[0]

    def cv_r2(self, k=5, seed=0):
        """按版本分组的 K 折交叉验证 R²。"""
        keys = list(self.folds)
        if len(keys) < k:
            return np.nan
        rng = np.random.default_rng(seed)
        order = rng.permutation(len(keys))
        assign = np.array_split(order, k)
        sse = sst = 0.0
        mu = self.y.mean()
        for part in assign:
            te = np.concatenate([self.folds[keys[i]] for i in part])
            mask = np.ones(len(self.y), bool)
            mask[te] = False
            tr = np.where(mask)[0]
            beta, *_ = np.linalg.lstsq(self.X[tr], self.y[tr], rcond=None)
            sse += ((self.X[te] @ beta - self.y[te]) ** 2).sum()
            sst += ((self.y[te] - mu) ** 2).sum()
        return 1 - sse / sst if sst else np.nan

    def lovo_r2(self):
        """留一版本交叉验证 R²（确定性方案，用于对照分组 K 折）。"""
        sse = sst = 0.0
        mu = self.y.mean()
        for te in self.folds.values():
            mask = np.ones(len(self.y), bool)
            mask[te] = False
            tr = np.where(mask)[0]
            beta, *_ = np.linalg.lstsq(self.X[tr], self.y[tr], rcond=None)
            if not np.all(np.isfinite(beta)):
                return np.nan
            sse += ((self.X[te] @ beta - self.y[te]) ** 2).sum()
            sst += ((self.y[te] - mu) ** 2).sum()
        return 1 - sse / sst if sst else np.nan


def resample_versions(df, rng):
    """按版本重采样（实例层级自助力）。"""
    v = df["version"].unique()
    pick = rng.choice(v, size=len(v), replace=True)
    return df[df["version"].isin(pick)]


def resample_projects(df, rng):
    """按项目整群重采样（项目层级自助力）——保留被抽中项目的全部版本。

    与按版本重采样的区别很关键：前者把同一项目的多个版本当独立单元，
    会在 youtube-dl 这类版本数极多的项目上系统性低估不确定性。
    """
    p = df["project"].unique()
    pick = rng.choice(p, size=len(p), replace=True)
    out = []
    for i, name in enumerate(pick):
        sub = df[df["project"] == name].copy()
        sub["project"] = f"{name}#{i}"      # 让重复抽中的同一项目成为不同簇
        sub["version"] = sub["project"] + "-" + sub["version"].str.split("-", n=1).str[-1]
        out.append(sub)
    return pd.concat(out, ignore_index=True)
