#!/usr/bin/env bash
set -e

echo "================================================"
echo " 茉晓mox 正在启动..."
echo "================================================"
echo ""

# 检查虚拟环境
if [ ! -f "venv/bin/activate" ]; then
    echo "[错误] 虚拟环境不存在！请先运行 ./install.sh"
    exit 1
fi

# 激活并启动
source venv/bin/activate
nb run
