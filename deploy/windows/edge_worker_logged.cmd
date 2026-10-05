@echo off
rem BiliSummaryStream 边缘算力节点启动包装：强制 UTF-8 + 无缓冲，日志落盘
rem 用法: edge_worker_logged.cmd <日志文件绝对路径>
setlocal
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
set "LOG=%~1"
if "%LOG%"=="" set "LOG=%~dp0logs\edge_fallback.log"

rem 强制 Python 走 UTF-8 + 无缓冲，避免日志中文乱码、行缓冲丢日志
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"

echo.>>"%LOG%"
echo ===== [EDGE START] %DATE:~0,10% %TIME:~0,8% =====>>"%LOG%"
".venv\Scripts\python.exe" -u "src\edge_worker.py" >>"%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo ===== [EDGE EXIT] %DATE:~0,10% %TIME:~0,8% exitcode=%RC% =====>>"%LOG%"
exit /b %RC%
