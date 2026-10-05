@echo off
setlocal
echo [*] 正在设置开机自启动...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$WshShell = New-Object -ComObject WScript.Shell; $StartupPath = [System.Environment]::GetFolderPath('Startup'); $ShortcutPath = Join-Path $StartupPath 'BiliSummaryStream.lnk'; $Shortcut = $WshShell.CreateShortcut($ShortcutPath); $Shortcut.TargetPath = 'wscript.exe'; $Shortcut.Arguments = '\"%~dp0run_silent.vbs\"'; $Shortcut.WorkingDirectory = '%~dp0'; $Shortcut.Description = 'Bilibili 视频深度总结守护进程'; $Shortcut.Save(); Write-Host '[OK] 开机自启动设置成功！'"
pause
