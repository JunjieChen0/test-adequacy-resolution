#!/usr/bin/env bash
# 第二轮扩样：挑各项目中**尚未使用**的缺陷，继续增加独立版本数
#
# 目的（方案 A）：结论的稳健性取决于独立缺陷版本数，而非实例总数。
# 第一轮已覆盖 87 个版本；本轮从剩余缺陷中继续抽取，进一步收紧置信区间。
#
# 幂等：已完成的实例会被跳过。

set -u
cd /home/user/ClawsGO/project-20261015-1
source scripts/d4j-env.sh
WORKERS=${WORKERS:-4}
mkdir -p data

# 要扩充的项目及其目标新增缺陷数
declare -A WANT=(
  [Math]=18 [Lang]=14 [Compress]=14 [Cli]=12 [Collections]=10
  [Time]=8 [Jsoup]=12 [Codec]=8 [Gson]=8 [Csv]=5 [Chart]=6
)

for proj in Math Lang Compress Cli Collections Time Jsoup Codec Gson Csv Chart; do
    n=${WANT[$proj]}
    all=$(defects4j bids -p "$proj" 2>/dev/null)
    pick=$(python3 - "$proj" "$n" $all <<'EOF'
import json, glob, sys
proj, n = sys.argv[1], int(sys.argv[2])
allb = sys.argv[3:]
used = set()
for f in glob.glob('data/subsets/*.json'):
    d = json.load(open(f))
    if d['project'] == proj:
        used.add(d['bug'].rstrip('b'))
cand = [b for b in allb if b not in used]
if not cand:
    print(''); raise SystemExit
step = max(1, len(cand) // n)
print(' '.join(cand[::step][:n]))
EOF
)
    if [ -z "$pick" ]; then echo "$proj: 无剩余缺陷，跳过"; continue; fi
    echo "=== $(date +%H:%M) $proj 新增: $pick ==="
    python3 scripts/collect_subsets.py --project "$proj" \
        --bugs $pick --fracs 0.1 0.25 --reps 1 --workers "$WORKERS" --mut-classes 15 \
        2>&1 | tee -a "data/round2-$proj.log" | tail -1
done
echo "=== 第二轮扩样完成 ==="
