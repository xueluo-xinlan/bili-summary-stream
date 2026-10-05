$ErrorActionPreference = 'SilentlyContinue'
$proj = 'C:\Projects\bili-summary-stream'

"=== processes on the launcher chain ==="
Get-CimInstance Win32_Process | Where-Object {
  $_ -and ($_.CommandLine -like '*main.py watch*' -or $_.CommandLine -like '*watch_logged*')
} | ForEach-Object {
  "pid=$($_.ProcessId) ppid=$($_.ParentProcessId) name=$($_.Name) start=$($_.CreationDate)"
  "    cmd=$($_.CommandLine)"
}

"`n=== logs dir ==="
Get-ChildItem "$proj\logs" -Force | Sort-Object LastWriteTime -Descending |
  ForEach-Object { "  {0}  {1}  {2}" -f $_.Name, $_.Length, $_.LastWriteTime }

$log = Get-ChildItem "$proj\logs\watch_*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($log) {
  "`n=== tail 25 of $($log.Name) ==="
  Get-Content $log.FullName -Tail 25
} else { "`nNO watch_*.log" }
