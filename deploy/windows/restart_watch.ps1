$ErrorActionPreference = 'SilentlyContinue'
$proj = 'C:\Projects\bili-summary-stream'

function Get-Watch {
  Get-CimInstance Win32_Process | Where-Object { $_ -and $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -like '*main.py watch*' }
}

$before = @(Get-Watch)
"BEFORE: " + (($before | ForEach-Object { "$($_.ProcessId)@$($_.ExecutablePath)" }) -join ' | ')
foreach($p in $before){ Stop-Process -Id $p.ProcessId -Force }
Start-Sleep -Seconds 3
$left = @(Get-Watch)
"AFTER-KILL remaining: " + $left.Count
foreach($p in $left){ Stop-Process -Id $p.ProcessId -Force }
Start-Sleep -Seconds 2

"--- triggering scheduled task BiliSummaryStream (same path as logon) ---"
& schtasks.exe /run /tn "\BiliSummaryStream" 2>&1 | Out-String
Start-Sleep -Seconds 15

$now = @(Get-Watch)
"AFTER-START processes: " + $now.Count
$now | ForEach-Object { "  pid=$($_.ProcessId) ppid=$($_.ParentProcessId) start=$($_.CreationDate)`n    exe=$($_.ExecutablePath)`n    cmd=$($_.CommandLine)" }

$i = Get-ScheduledTask -TaskName 'BiliSummaryStream' | Get-ScheduledTaskInfo
"task LastRunTime=$($i.LastRunTime) LastResult=0x$('{0:X}' -f [int]$i.LastTaskResult)"

"--- logs dir ---"
Get-ChildItem "$proj\logs" -Force | Sort-Object LastWriteTime -Descending | ForEach-Object { "  $($_.Name)  $($_.Length)  $($_.LastWriteTime)" }
$log = Get-ChildItem "$proj\logs\watch_*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if($log){
  "--- tail 20 of $($log.Name) ---"
  Get-Content $log.FullName -Tail 20
} else { "NO watch_*.log CREATED" }
