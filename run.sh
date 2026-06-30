#!/bin/bash
# ============================================================
# android-ai-driven-test 启动脚本
# 清除 Git Bash 遗留的环境变量，避免干扰 conda Python
# ============================================================
set -e

# 定位脚本自身所在目录（项目根目录）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 清除 Git Bash / MSYS2 遗留的 SSL 相关环境变量，避免 conda Python 找不到证书
unset SSL_CERT_FILE
unset SSL_CERT_DIR

# 检查配置文件
CONFIG="config/settings.yaml"
if [ ! -f "$CONFIG" ]; then
    echo "错误: 找不到 $CONFIG" >&2
    echo "请复制 config/settings.example.yaml 为 config/settings.yaml 并配置" >&2
    exit 2
fi

# 检查 APK 下载链接
APKS_TXT="apk_cache/apks.txt"
if [ ! -f "$APKS_TXT" ]; then
    echo "警告: 找不到 $APKS_TXT，预处理阶段将跳过 APK 下载" >&2
fi

# 启动
echo "=========================================="
echo " Android AI Driven Test Framework"
echo "=========================================="
echo ""

exec python -m game_agent.main "$@"
