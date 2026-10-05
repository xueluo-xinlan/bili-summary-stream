@echo off
setlocal
echo [*] 正在查找并停止 BiliSummaryStream 守护进程...
wmic process where "commandline like '%%main.py watch%%' and name='python.exe'" call terminate >nul 2>&1
echo [OK] 守护服务已成功停止！
