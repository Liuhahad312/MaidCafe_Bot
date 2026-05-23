@echo off
chcp 65001 >nul
title 茉晓mox - 运行中...

echo ================================================
echo  茉晓mox 正在启动...
echo ================================================
echo.

:: 检查虚拟环境
if not exist venv\Scripts\activate.bat (
    echo [错误] 虚拟环境不存在！请先运行 install.bat
    pause
    exit /b 1
)

:: 激活并启动
call venv\Scripts\activate.bat
nb run

pause
