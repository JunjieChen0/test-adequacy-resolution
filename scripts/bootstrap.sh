#!/usr/bin/env bash
# 一键恢复 Defects4J 运行环境
#
# 为什么需要这个脚本：
#   本沙箱会在运行过程中重置**根文件系统**，并杀掉所有后台进程。/home/user 是独立
#   挂载点、内容持久，但通过 apt 安装到 / 的 Maven 与 JDK 会全部丢失（已实测遇到一次：
#   JDK 11 与 Maven 消失，所有 defects4j 命令报 "Java 11 is required!"，
#   正在跑的采集进程被杀）。因此工具链必须能快速重建。
#
# 用法：bash scripts/bootstrap.sh
set -e

JDK11=/usr/lib/jvm/java-11-openjdk-amd64
PERSIST=/home/user/tools

echo "=== 1/4 检查并安装 apt 包（maven + openjdk-11-jdk + perl 模块） ==="
NEED_APT=0
[ -x "$JDK11/bin/java" ] || NEED_APT=1
command -v mvn >/dev/null || NEED_APT=1
perl -MString::Interpolate -e1 2>/dev/null || NEED_APT=1
if [ "$NEED_APT" = "1" ]; then
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y maven openjdk-11-jdk libstring-interpolate-perl
else
    echo "apt 包已就绪"
fi

echo "=== 1b/4 把 Perl 模块装到持久位置 ==="
# 同理：apt 装的 perl 模块也在根文件系统上，重置后会丢。把 .pm 复制到持久目录，
# 由 d4j-env.sh 通过 PERL5LIB 加载。
PM=libstring-interpolate-perl
if [ ! -f /home/user/tools/perl5/String/Interpolate.pm ] && [ -f /usr/share/perl5/String/Interpolate.pm ]; then
    mkdir -p /home/user/tools/perl5/String
    cp -a /usr/share/perl5/String/Interpolate.pm /home/user/tools/perl5/String/
    echo "String::Interpolate 已持久化"
fi

echo "=== 2/4 把 JDK 与 Maven 复制到持久位置 /home/user/tools ==="
# 复制到 /home/user 是为了避免下次根文件系统重置后需要重新下载（73 MB，网络很慢）
if [ ! -x "$PERSIST/jdk11/bin/java" ]; then
    mkdir -p "$PERSIST"
    cp -a "$JDK11" "$PERSIST/jdk11"
    echo "JDK 已持久化到 $PERSIST/jdk11"
else
    echo "JDK 持久副本已存在"
    # 恢复到系统位置，供依赖 /usr/lib/jvm 的工具使用
    [ -x "$JDK11/bin/java" ] || { sudo mkdir -p /usr/lib/jvm; sudo cp -a "$PERSIST/jdk11" "$JDK11"; }
fi
if [ ! -x "$PERSIST/maven/bin/mvn" ] && [ -d /usr/share/maven ]; then
    cp -a /usr/share/maven "$PERSIST/maven"
    echo "Maven 已持久化到 $PERSIST/maven"
fi

echo "=== 2b/4 恢复 JDK 的 /etc 配置目录 ==="
# 已实测踩到：根文件系统重置会删掉 /etc/java-11-openjdk，但 /home/user/tools/jdk11 里的
# conf/*、lib/security/* 全是指向该目录的符号链接，于是变成 28 个断链，
# 症状是 defects4j compile 报 "java.lang.InternalError: Error loading java.security file"，
# 而 java -version 仍正常——很容易误判成采集脚本的问题。
# 系统源里已经没有 openjdk-11（apt-cache policy 显示 Candidate: none），
# 所以用持久副本恢复；持久副本也不存在时，退而用 java-17 的同名配置顶上。
JDK_ETC=/etc/java-11-openjdk
PERSIST_ETC=/home/user/tools/java11-etc
if [ ! -f "$JDK_ETC/security/java.security" ]; then
    if [ -d "$PERSIST_ETC" ]; then
        sudo cp -r "$PERSIST_ETC" "$JDK_ETC"; echo "  已从持久副本恢复 $JDK_ETC"
    elif [ -d /etc/java-17-openjdk ]; then
        sudo cp -r /etc/java-17-openjdk "$JDK_ETC"; echo "  已用 java-17 配置恢复 $JDK_ETC"
        sudo cp -r "$JDK_ETC" "$PERSIST_ETC"
    else
        echo "  警告：无法恢复 $JDK_ETC，compile 会失败"
    fi
else
    echo "  $JDK_ETC 正常"
fi

echo "=== 3/4 检查 Defects4J 资产 ==="
for p in "$PERSIST/defects4j/framework/bin/defects4j" \
         "$PERSIST/defects4j/framework/lib/major/bin/ant" \
         "$PERSIST/defects4j/project_repos/commons-lang.git"; do
    [ -e "$p" ] && echo "  OK   $p" || echo "  缺失 $p（需重新 clone / 跑 init.sh）"
done

echo "=== 4/4 验证 ==="
JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64 \
MAJOR_ROOT=/home/user/tools/defects4j/framework/lib/major \
TESTGEN_LIB_DIR=/home/user/tools/defects4j/framework/lib/test_generation/generation \
BUILD_SYSTEMS_LIB_DIR=/home/user/tools/defects4j/framework/lib/build_systems \
PATH=/usr/lib/jvm/java-11-openjdk-amd64/bin:/home/user/tools/defects4j/framework/bin:$PATH \
defects4j pids

echo
echo "环境就绪。运行采集前先 source scripts/d4j-env.sh"
