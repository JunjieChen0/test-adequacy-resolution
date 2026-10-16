#!/usr/bin/env bash
# 顺序项目驱动的采集器
#
# 为什么不再并行跑多个项目：
#   8 核 / 16 GB 的机器上同时跑 8 个项目 × 2 并发 = 16 个 JVM，内存耗尽导致变异步骤
#   失败（实测 Csv 的变异在中途异常退出）。改为逐项目串行、项目内 worker 数固定，
#   把并发 JVM 数控制在内存可承受的范围。
#
# 幂等：已成功完成的实例会被跳过，重复运行安全。

set -u
cd /home/user/ClawsGO/project-20261015-1
source scripts/d4j-env.sh
WORKERS=${WORKERS:-3}
FRACS=${FRACS:-"0.1 0.25"}
mkdir -p data

# 各项目要覆盖的缺陷（均匀抽取未用过的版本，见 launch_broad.sh 的说明）
declare -A BUGS=(
  [Math]="2 8 14 20 26 32 38 44 50 56 62 68 74 80 86 92"
  [Lang]="3 7 12 17 22 28 33 37 42 46 51 55"
  [Chart]="2 5 7 10 13 15 17 19 21 23"
  [Cli]="1 8 14 20 26 32"
  [Time]="1 5 9 13 17 22"
  [Csv]="1 4 7 10 13"
  [Codec]="1 4 7 10 13"
  [Collections]="1 8 15 22"
  [Compress]="1 10 20 30 40"
  [Gson]="1 5 9 13 17"
)

ORDER="Math Lang Chart Cli Time Csv Codec Collections Compress Gson"

# 先跑小规模档，再跑大规模档。
# 原因：覆盖率要对**全部主类**插桩，成本随套件规模上升；Math 有 751 个主类，
# f0.1 约 7 分钟而 f0.25 约 22 分钟。先跑 0.1 档可以更快拿到"独立缺陷版本"样本，
# 而这正是统计推断真正需要的量（见 results/pseudo-replication-finding.md）。
for frac in $FRACS; do
    echo "########## 规模档 $frac ##########"
    for proj in $ORDER; do
        bugs=${BUGS[$proj]:-""}
        [ -z "$bugs" ] && continue
        echo "=== $(date +%H:%M) 开始 $proj (frac=$frac): $bugs ==="
        python3 scripts/collect_subsets.py --project "$proj" \
            --bugs $bugs --fracs "$frac" --reps 1 --workers "$WORKERS" --mut-classes 15 \
            2>&1 | tee -a "data/pipeline-$proj.log" | tail -2
    done
done
echo "全部项目完成"
