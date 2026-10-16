#!/usr/bin/env python3
"""
A1 分析：覆盖率/变异分数能否反映真实缺陷检出能力

数据来源：
  data/instance-*.json   每个缺陷版本的触发测试清单（trigger.txt）与全量套件指标
  data/subsets/*.json    每个测试子集的保留测试类清单与指标

结果变量（核心定义）：
  detection(S) = |{ b : S 含 b 的触发测试类 }| / |bugs(P)|

  注意分母有两种：
    n_bugs_total      项目全部缺陷（含其触发测试类不在本版本测试集中的缺陷）
    n_bugs_detectable 触发测试类存在于本版本测试集里的缺陷（公允分母）

分析方法：
  1. 各子集规模的均值表
  2. Spearman 相关（覆盖率、变异分数 对 检出率）
  3. 控制套件规模后的偏相关（预先约定的 go/no-go 判据）

用法：python3 analyze_a1.py
"""
import glob
import json
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DATA = os.path.join(_ROOT, "data")
OUT = os.path.join(_ROOT, "results")


def cls_of_path(rel):
    """'a/b/FooTest.java' -> 'a.b.FooTest'"""
    return rel[:-5].replace("/", ".") if rel.endswith(".java") else rel


def spearman(x, y):
    n = len(x)
    if n < 3:
        return float("nan")
    def rank(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = rank(x), rank(y)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return num / (dx * dy) if dx and dy else float("nan")


def partial_spearman(x, y, z):
    rxy, rxz, ryz = spearman(x, y), spearman(x, z), spearman(y, z)
    den = ((1 - rxz ** 2) * (1 - ryz ** 2)) ** 0.5
    return (rxy - rxz * ryz) / den if den else float("nan")


def load_trigger_map():
    """项目 -> {bug: 触发测试类集合}"""
    trig = {}
    for f in glob.glob(os.path.join(DATA, "instance-*.json")):
        d = json.load(open(f))
        p, bug = d["project"], d["bug"]
        cls = {t.split("::")[0] for t in d.get("trigger.txt", []) if "::" in t}
        trig.setdefault(p, {})[bug] = cls
        trig[p].setdefault("_all_classes", set()).update(cls_of_path(t) for t in d.get("all.txt", []))
    return trig


def main():
    os.makedirs(OUT, exist_ok=True)
    trig = load_trigger_map()

    rows = []
    for f in glob.glob(os.path.join(DATA, "subsets", "*.json")):
        d = json.load(open(f))
        if d.get("status") != "ok":
            continue
        p, bug = d["project"], d["bug"]
        if p not in trig:
            continue
        # 用**实际运行的测试集**：自适应修复会补回部分被移走的测试类，
        # 这些类同样会被执行，必须计入套件。早期记录没有 kept_tests_final 字段，
        # 此时回退到 kept_tests（对这些记录而言两者相同，其编译一次通过、未触发修复）。
        suite = d.get("kept_tests_final") or d.get("kept_tests", [])
        kept = {cls_of_path(t) for t in suite}
        bugs = {b: c for b, c in trig[p].items() if not b.startswith("_")}
        det = [b for b, c in bugs.items() if c & kept]
        cov = d["steps"].get("coverage", {})
        mut = d["steps"].get("mutation", {})
        rows.append({
            "project": p, "base_bug": bug, "frac": d["frac"], "rep": d["rep"],
            "n_kept": d.get("n_test_final") or d.get("n_test_kept"),
            "n_total": d.get("n_test_total"),
            "coverage": cov.get("line_cov_pct"), "cond_cov": cov.get("cond_cov_pct"),
            "mutation": mut.get("mutation_score_pct"),
            "detected": len(det), "n_bugs_total": len(bugs),
            "detect_rate": len(det) / len(bugs) if bugs else None,
        })

    if not rows:
        print("尚无完整的子集实例，等待采集。")
        return

    json.dump(rows, open(os.path.join(OUT, "a1_rows.json"), "w"), indent=2)

    print(f"\n共 {len(rows)} 个子集实例\n")
    print("=== 按子集比例汇总（各项目合并） ===")
    print(f"{'比例':>6} {'实例':>4} {'平均保留测试类':>14} {'平均覆盖率':>10} {'平均变异分数':>12} {'平均检出率':>10}")
    for frac in sorted({r["frac"] for r in rows}):
        g = [r for r in rows if r["frac"] == frac]
        f2 = lambda k: [r[k] for r in g if r[k] is not None]
        avg = lambda k: (sum(f2(k)) / len(f2(k))) if f2(k) else float("nan")
        print(f"{frac:>6} {len(g):>4} {avg('n_kept'):>14.1f} {avg('coverage'):>9.1f}% "
              f"{avg('mutation'):>11.1f}% {avg('detect_rate'):>9.3f}")

    print("\n=== 相关性分析（预设 go/no-go 判据） ===")
    for proj in sorted({r["project"] for r in rows}):
        g = [r for r in rows if r["project"] == proj and r["coverage"] is not None and r["mutation"] is not None]
        if len(g) < 4:
            print(f"\n[{proj}] 样本仅 {len(g)}，暂不分析")
            continue
        size = [r["n_kept"] for r in g]
        det = [r["detect_rate"] for r in g]
        cov = [r["coverage"] for r in g]
        mut = [r["mutation"] for r in g]
        print(f"\n[{proj}]  n={len(g)}")
        print(f"  覆盖率  ~ 检出率 : Spearman ρ={spearman(cov, det):+.3f}   控制规模后偏相关 ρ={partial_spearman(cov, det, size):+.3f}")
        print(f"  变异分数~ 检出率 : Spearman ρ={spearman(mut, det):+.3f}   控制规模后偏相关 ρ={partial_spearman(mut, det, size):+.3f}")
        print(f"  覆盖率  ~ 变异分数: Spearman ρ={spearman(cov, mut):+.3f}  ← 两个指标彼此一致性")
        print(f"  套件规模~ 覆盖率 : Spearman ρ={spearman(size, cov):+.3f}  ← 共线性有多强")

        # 同规模内比较 —— 这才是真正决定性的分析：
        # 大小相同的子集之间，检出率是否仍有差异？指标能否解释它？
        print(f"\n  --- 同规模内比较（规模与覆盖率高度共线，故必须分层） ---")
        print(f"  {'比例':>6} {'实例':>4} {'检出率区间':>16} {'极差':>7} {'覆盖率ρ':>9} {'变异分数ρ':>10}")
        for frac in sorted({r["frac"] for r in g}):
            gg = [r for r in g if r["frac"] == frac]
            if len(gg) < 3:
                continue
            d = [r["detect_rate"] for r in gg]
            c = [r["coverage"] for r in gg]
            m = [r["mutation"] for r in gg]
            print(f"  {frac:>6} {len(gg):>4} {min(d):>7.3f}–{max(d):<7.3f} {max(d)-min(d):>7.3f} "
                  f"{spearman(c, d):>9.3f} {spearman(m, d):>10.3f}")


if __name__ == "__main__":
    main()
