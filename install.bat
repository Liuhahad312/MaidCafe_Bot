@echo off
chcp 65001 >nul
title 茉晓mox - 安装程序

echo ================================================
echo  茉晓mox - 咖啡店打工女仆 Bot 安装脚本 (Windows^)
echo ================================================
echo.

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python！请先安装 Python 3.10+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [检测] Python 已安装:
python --version
echo.

:: 创建虚拟环境
echo [1/4] 创建虚拟环境...
if exist venv (
    echo       虚拟环境已存在，跳过创建
) else (
    python -m venv venv
    echo       虚拟环境创建成功~
)

:: 激活虚拟环境
echo [2/4] 激活虚拟环境...
call venv\Scripts\activate.bat

:: 升级 pip 并安装依赖
echo [3/4] 安装项目依赖...
pip install --upgrade pip --quiet
pip install -e . --quiet
echo       依赖安装完成~

:: 初始化数据库
echo [4/4] 初始化数据库...
python -c "import asyncio; from mox.database import init_db; asyncio.run(init_db())"
echo       数据库初始化完成~

echo.
echo ================================================
echo  安装完成！(◕‿◕)ﾉ
echo.
echo  下一步:
echo   1. 编辑 .env        - 检查 NoneBot2 配置
echo   2. 编辑 config.yaml - 填入 DeepSeek / Grok API Key
echo   3. 双击 run.bat     - 启动茉晓!
echo ================================================
pause
