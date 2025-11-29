@echo off
setlocal

REM 切换到脚本所在目录
cd /d "%~dp0"

REM 启动程序
python main.py

endlocal

