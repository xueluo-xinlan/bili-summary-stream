@echo off
setlocal
cd /d "%~dp0"
if not exist logs mkdir logs

wmic process where "commandline like '%%main.py watch%%' and name='python.exe'" get processid 2>nul | findstr /r "[0-9]" >nul
if %errorlevel% equ 0 (
    echo [i] 守护服务已在运行中，无需重复启动。
    exit /b 0
)

echo [*] 正在启动 BiliSummaryStream 后台守护服务...
start /b "" "%~dp0.venv\Scripts\python.exe" -u main.py watch > logs\daemon.log 2>&1
echo [OK] 后台守护服务已启动！
echo     日志输出文件: logs\daemon.log
echo     如需停止服务请运行: stop_service.bat
