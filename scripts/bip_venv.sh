#!/usr/bin/env bash
# 为某个 BugsInPy 项目建独立的 Python 3.8 环境
#
# 为什么需要：
#   BugsInPy 每个项目的 requirements.txt 是一份完整冻结清单，彼此冲突。
#   共用环境装不下，只能按项目隔离。
# 注意：
#   requirements.txt 多为 UTF-16LE 编码，框架自带的 compile 脚本会因空行报
#   "Invalid requirement: ''"，这里先转成 UTF-8/LF。
#   同时剔除以 `-e git+...#egg=<项目>` 形式自引用的那一行——否则 pip 会去拉
#   远端仓库，覆盖掉我们要测的检出源码。
set -eu
P="$1"; BUG="${2:-1}"
BASE=/home/user/tools/bip38
VENV="data-bip/venv/$P"
REQ="/home/user/tools/bugsinpy/projects/$P/bugs/$BUG/requirements.txt"
[ -d "$VENV" ] && { echo "$P: 环境已存在"; exit 0; }
"$BASE/bin/python" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip 2>&1 | tail -1
if [ -f "$REQ" ]; then
  "$BASE/bin/python" - "$REQ" > /tmp/req-$P.txt <<'PY'
import sys
d = open(sys.argv[1], 'rb').read()
for enc in ('utf-16', 'utf-8-sig', 'utf-8'):
    try:
        t = d.decode(enc); break
    except Exception: pass
for line in t.splitlines():
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    if 'egg=' in line:      # 自引用，跳过
        continue
    print(line)
PY
  echo "  依赖 $(wc -l < /tmp/req-$P.txt) 条"
  "$VENV/bin/pip" install -q -r /tmp/req-$P.txt 2>&1 | tail -3 || echo "  （部分依赖安装失败，继续）"
fi
"$VENV/bin/pip" install -q -U pytest coverage pytest-timeout 2>&1 | tail -1
echo "$P: 环境就绪 $VENV"
