@echo off
rem BiliSummaryStream 守护进程带日志启动器 —— 由 run_silent.vbs 隐藏调用
rem 用法: watch_logged.cmd <日志文件绝对路径>
setlocal
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
set "LOG=%~1"
if "%LOG%"=="" set "LOG=%~dp0logs\watch_fallback.log"

rem 强制 Python 以 UTF-8 + 无缓冲输出，保证日志中的中文不乱码、不丢行
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"

echo.>>"%LOG%"
echo ===== [SESSION START] %DATE:~0,10% %TIME:~0,8% =====>>"%LOG%"
".venv\Scripts\python.exe" -u main.py watch >>"%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo ===== [SESSION EXIT] %DATE:~0,10% %TIME:~0,8% exitcode=%RC% =====>>"%LOG%"
exit /b %RC%
