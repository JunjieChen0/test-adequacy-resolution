#!/usr/bin/env bash
# BugsInPy 跨基准采集驱动
#
# 两处与第一版不同：
#  1. 用 --only 指定测试文件白名单，而不是 --ignore 排除。
#     原因：youtube-dl 的完整套件含大量联网用例，靠 --timeout 逐个杀会拖到每缺陷 10 分钟以上。
#  2. 检出率的分母改为"触发测试确实落在可运行池内"的缺陷数，
#     否则联网用例承载的触发测试永远不可能被检出，会系统性压低检出率。
set -u
cd /home/user/ClawsGO/project-20261015-1
PY=/home/user/tools/bip38/bin/python
FRACS="${FRACS:-0.1 0.3 0.6}"

run () {
  local P=$1; shift
  echo "########## $P $(date +%H:%M) ##########"
  $PY scripts/bip_collect.py --project "$P" --fracs $FRACS --only "$@" \
      2>&1 | grep -v SyntaxWarning | tee -a "data-bip/$P.log" | tail -3
}

run youtube-dl test/test_utils.py test/test_jsinterp.py test/test_YoutubeDL.py \
    test/test_InfoExtractor.py test/test_aes.py test/test_compat.py \
    test/test_cache.py test/test_execution.py test/test_unicode_literals.py
run tornado tornado/test
run tqdm tqdm/tests
run PySnooper tests
echo "=== 全部完成 $(date +%H:%M) ==="
