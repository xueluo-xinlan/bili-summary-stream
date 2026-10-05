@echo off
setlocal
echo [*] 正在移除开机自启动...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$StartupPath = [System.Environment]::GetFolderPath('Startup'); $ShortcutPath = Join-Path $StartupPath 'BiliSummaryStream.lnk'; if (Test-Path $ShortcutPath) { Remove-Item $ShortcutPath -Force; Write-Host '[OK] 开机自启动快捷方式已成功删除！' } else { Write-Host '[i] 未发现开机自启动快捷方式。' }"
pause
