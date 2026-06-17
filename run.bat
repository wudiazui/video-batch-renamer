@echo off
chcp 65001 >nul
REM 一键启动视频批量重命名工具
cd /d "%~dp0"
python src\main.py
if errorlevel 1 (
    echo.
    echo 启动失败。请确认已安装 Python 3.10 或更高版本，并已勾选 Add Python to PATH。
    pause
)
