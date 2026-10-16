#!/usr/bin/env bash
# Defects4J 运行环境 —— 每次执行 defects4j 前 source 本文件
#
# 背景：本沙箱会重置**根文件系统**并杀掉后台进程。/home/user 持久，但 apt 安装的
# Maven / JDK / Perl 模块会丢失（已实测遇到一次）。因此这里优先使用 /home/user 下的
# 持久副本，缺失时再退回系统路径；彻底缺失时跑 scripts/bootstrap.sh 重建。

D4J_HOME=/home/user/tools/defects4j
PERSIST=/home/user/tools

# --- JDK 11（框架硬性要求；JDK 17/21/25 会被拒） ---
if [ -x "$PERSIST/jdk11/bin/java" ]; then
    export JAVA_HOME="$PERSIST/jdk11"
else
    export JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64
fi

# --- Maven：优先持久副本 ---
if [ -x "$PERSIST/maven/bin/mvn" ]; then
    export M2_HOME="$PERSIST/maven"
    export PATH="$M2_HOME/bin:$PATH"
fi

# --- Perl 模块持久副本（apt 装的会随根文件系统丢失） ---
export PERL5LIB="$PERSIST/perl5${PERL5LIB:+:$PERL5LIB}"

# --- 框架路径常量：框架默认基于 $SCRIPT_DIR/../ 推导，与实际下载位置不一致，必须显式指定 ---
export MAJOR_ROOT="$D4J_HOME/framework/lib/major"
export TESTGEN_LIB_DIR="$D4J_HOME/framework/lib/test_generation/generation"
export BUILD_SYSTEMS_LIB_DIR="$D4J_HOME/framework/lib/build_systems"

# --- 图形 + 堆上限：Chart (JFreeChart) 的测试需要 AWT，本机 DISPLAY=:99 连不上 X server。
#     不加 headless 会让变异测试以 AWTError 中止（coverage 只警告，容易漏掉）。
#     同时限制堆大小：Major 变异分析内存占用大，若并发数偏高会触发 OOM 使变异步骤失败
#     （实测：8 个任务 × 2 并发 = 16 个 JVM 时出现过）。
export JAVA_TOOL_OPTIONS="-Djava.awt.headless=true -Xmx1400m"

export PATH="$JAVA_HOME/bin:$D4J_HOME/framework/bin:$PATH"

# 工作目录（checkout 出来的项目版本）
export D4J_WORK=/home/user/d4j-work

# 自检
if ! defects4j pids >/dev/null 2>&1; then
    echo "警告：Defects4J 不可用，请运行 scripts/bootstrap.sh 重建环境" >&2
fi
