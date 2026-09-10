@echo off
chcp 65001 >nul
title 今日事 - 日历待办清单
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10+ 并勾选 "Add to PATH"
    pause
    exit /b 1
)

if not exist "venv\Scripts\python.exe" (
    echo [初始化] 首次运行，正在创建虚拟环境...
    python -m venv venv
)

call "venv\Scripts\activate.bat"
python -m pip install -q -r requirements.txt

python run.py
pause
