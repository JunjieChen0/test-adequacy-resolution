#!/usr/bin/env bash
# 扩大独立样本的采集驱动
#
# 为什么这样设计（由 RQ3 分析暴露的问题决定）：
#   原设计对少数缺陷反复抽样（13 个基准版本 × 多个子集 = 106 个实例），
#   但同一缺陷的子集共享测试源码，构成**伪重复**，有效样本量只有 13。
#   把样本量堆在重复上，对统计推断毫无帮助。
#
#   正确做法：增加**独立缺陷版本数**，每个版本只采两档规模。
#   本脚本为每个项目挑选若干未用过的缺陷，采 frac ∈ {0.1, 0.25} 各一次。
#
# 用法：bash scripts/launch_broad.sh [每项目缺陷数]

set -u
DIR=/home/user/ClawsGO/project-20261015-1
cd "$DIR"
source scripts/d4j-env.sh

PER=${1:-0}
mkdir -p data

used() {
    python3 - "$1" <<'EOF'
import json, glob, sys
p = sys.argv[1]
s = set()
for f in glob.glob('data/subsets/*.json'):
    d = json.load(open(f))
    if d['project'] == p:
        s.add(d['bug'].rstrip('b'))
print(' '.join(sorted(s, key=int)))
EOF
}

# 项目:要挑的缺陷数（总额按独立样本需求分配，兼顾跨项目外部效度）
declare -A WANT=(
  [Math]=16 [Lang]=12 [Chart]=10 [Cli]=6 [Csv]=5 [Codec]=5 [Time]=6 [Collections]=4
)

for proj in Math Lang Chart Cli Csv Codec Time Collections; do
    n=${WANT[$proj]}
    all=$(defects4j bids -p "$proj" 2>/dev/null)
    have=$(used "$proj")
    # 均匀抽取未用过的缺陷，避免只取序号靠前的版本
    pick=$(python3 - "$n" $all <<EOF
import sys
n = int(sys.argv[1]); allb = sys.argv[2:]
have = set("""$have""".split())
cand = [b for b in allb if b not in have]
if not cand:
    print(''); raise SystemExit
step = max(1, len(cand) // n)
sel = cand[::step][:n]
print(' '.join(sel))
EOF
)
    if [ -z "$pick" ]; then echo "$proj: 无可用缺陷，跳过"; continue; fi
    echo "$proj 新增缺陷: $pick"
    setsid nohup python3 scripts/collect_subsets.py --project "$proj" \
        --bugs $pick --fracs 0.1 0.25 --reps 1 --workers 2 --mut-classes 15 \
        > "data/job-broad-$proj.log" 2>&1 < /dev/null &
    disown
    sleep 4
done

echo "全部任务已启动"
