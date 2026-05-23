#!/usr/bin/env bash
set -e

echo "================================================"
echo " 茉晓mox - 咖啡店打工女仆 Bot 安装脚本 (Linux)"
echo "================================================"
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "[错误] 未检测到 Python3！请先安装 Python 3.10+"
    echo " Ubuntu/Debian: sudo apt install python3 python3-venv python3-pip"
    echo " CentOS/RHEL:   sudo yum install python3 python3-pip"
    exit 1
fi

echo "[检测] Python 已安装:"
python3 --version
echo ""

# 创建虚拟环境
echo "[1/4] 创建虚拟环境..."
if [ -d "venv" ]; then
    echo "      虚拟环境已存在，跳过创建"
else
    python3 -m venv venv
    echo "      虚拟环境创建成功~"
fi

# 激活虚拟环境
echo "[2/4] 激活虚拟环境..."
source venv/bin/activate

# 升级 pip 并安装依赖
echo "[3/4] 安装项目依赖..."
pip install --upgrade pip --quiet
pip install -e . --quiet
echo "      依赖安装完成~"

# 初始化数据库
echo "[4/4] 初始化数据库..."
python3 -c "import asyncio; from mox.database import init_db; asyncio.run(init_db())"
echo "      数据库初始化完成~"

echo ""
echo "================================================"
echo " 安装完成！(◕‿◕)ﾉ"
echo ""
echo " 下一步:"
echo "  1. 编辑 .env        - 检查 NoneBot2 配置"
echo "  2. 编辑 config.yaml - 填入 DeepSeek / Grok API Key"
echo "  3. 运行 ./run.sh    - 启动茉晓!"
echo "================================================"
