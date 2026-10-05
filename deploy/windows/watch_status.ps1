# BiliSummaryStream watch status / health snapshot
#   live check :  powershell -NoProfile -ExecutionPolicy Bypass -File watch_status.ps1 -SampleSec 5
#   snapshot   :  ... -File watch_status.ps1 -SampleSec 0 -Snapshot     (appends one line to logs\health_YYYYMMDD.log)
# Exit code 0 = PASS, 1 = FAIL  (ASCII-only on purpose: PS 5.1 mis-decodes BOM-less UTF-8.)
param(
    [int]$SampleSec = 5,
    [switch]$Snapshot,
    [int]$KeepDays = 14,
    [int]$LogStaleMinutes = 120
)

$ErrorActionPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $proj) { $proj = 'C:\Projects\bili-summary-stream' }
$logDir = Join-Path $proj 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

# ---- process facts -------------------------------------------------------
$watch = @(Get-CimInstance Win32_Process |
         Where-Object { $_ -and $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -like '*main.py watch*' })
$shim = @($watch | Where-Object { $_ -and $_.ExecutablePath -like '*\.venv\*' })
$worker = @($watch | Where-Object { $_ -and $_.ExecutablePath -notlike '*\.venv\*' })
if ($worker.Count -eq 0 -and $watch.Count -gt 0) { $worker = @($watch[0]) }   # be generous

$wpid = 0; $cpu0 = 0.0; $cpu1 = 0.0; $wstart = $null; $wthr = 0; $wws = 0
if ($worker.Count -gt 0) {
    $wpid = $worker[0].ProcessId
    $wp = Get-Process -Id $wpid
    if ($wp) { $cpu0 = $wp.CPU; $wthr = $wp.Threads.Count; $wws = [math]::Round($wp.WorkingSet64 / 1MB, 1) }
    $wstart = $worker[0].CreationDate
}
if ($SampleSec -gt 0 -and $wpid) { Start-Sleep -Seconds $SampleSec }
if ($wpid) { $wp = Get-Process -Id $wpid; if ($wp) { $cpu1 = $wp.CPU } }
$cpuDelta = [math]::Round($cpu1 - $cpu0, 2)

$conns = @()
if ($wpid) { $conns = @(Get-NetTCPConnection -OwningProcess $wpid -State Established) }

# ---- log facts -----------------------------------------------------------
$log = Get-ChildItem (Join-Path $logDir 'watch_*.log') | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$logAge = -1; $logSize = 0; $tail = '(none)'; $errHits = 0
if ($log) {
    $logAge = [math]::Round(((Get-Date) - $log.LastWriteTime).TotalMinutes, 1)
    $logSize = [math]::Round($log.Length / 1KB, 1)
    $t = Get-Content $log.FullName -Tail 1 -Encoding UTF8
    if ($t) { $tail = ($t -join ' ').Trim() }
    $errHits = @(Get-Content $log.FullName -Tail 80 -Encoding UTF8 | Select-String -Pattern 'Traceback|ERROR|FAIL|Exception').Count
}

$db = Get-Item (Join-Path $proj 'data\history.db')
$dbAge = -1
if ($db) { $dbAge = [math]::Round(((Get-Date) - $db.LastWriteTime).TotalMinutes, 0) }

$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
$sinceBoot = [math]::Round(((Get-Date) - $boot).TotalMinutes, 0)

# ---- verdict -------------------------------------------------------------
$issues = @()
if ($wpid -eq 0)                        { $issues += 'worker-not-running' }
if ($logAge -lt 0)                      { $issues += 'no-watch-log' }
elseif ($logAge -gt $LogStaleMinutes)   { $issues += ('log-stale={0}min' -f $logAge) }
if ($wpid -and $cpuDelta -le 0 -and $logAge -gt 90) { $issues += 'worker-idle' }
if ($errHits -gt 0)                     { $issues += ('log-errors={0}' -f $errHits) }
$verdict = 'PASS'; if ($issues.Count -gt 0) { $verdict = 'FAIL' }
$issueTxt = 'none'; if ($issues.Count -gt 0) { $issueTxt = ($issues -join ',') }

# ---- report --------------------------------------------------------------
$now = Get-Date
$lines = New-Object System.Collections.Generic.List[string]
$lines.Add(('=== BiliSummaryStream watch status @ {0} ===' -f $now.ToString('yyyy-MM-dd HH:mm:ss')))
$lines.Add(('boot        {0}  (uptime {1} min)' -f $boot, $sinceBoot))
if ($wpid) {
    $lines.Add(('worker      pid={0} cpu={1}s (+{2}s/{3}s) threads={4} ws={5}MB started={6}' -f `
        $wpid, [math]::Round($cpu1,1), $cpuDelta, $SampleSec, $wthr, $wws, $wstart))
} else {
    $lines.Add('worker      NONE - main.py watch is not running')
}
if ($shim.Count -gt 0) { $lines.Add(('venv-shim   pid={0} (launcher stub, expected)' -f ($shim | ForEach-Object { $_.ProcessId } -join ','))) }
if ($log) { $lines.Add(('log         {0}  size={1}KB  age={2}min  tail: {3}' -f $log.Name, $logSize, $logAge, $tail)) }
else      { $lines.Add('log         none') }
$lines.Add(('bili-conns  {0} established' -f $conns.Count))
$lines.Add(('history.db  age={0}min' -f $dbAge))
$lines.Add(('issues      {0}' -f $issueTxt))
$lines.Add(('VERDICT     {0}' -f $verdict))

foreach ($l in $lines) { Write-Output $l }

# ---- snapshot mode -------------------------------------------------------
if ($Snapshot) {
    $stamp = $now.ToString('yyyyMMdd')
    $health = Join-Path $logDir ('health_{0}.log' -f $stamp)
    $rec = '{0} | verdict={1} | workerPid={2} | cpuDelta={3} | conns={4} | logAgeMin={5} | dbAgeMin={6} | sinceBootMin={7} | issues={8}' -f `
        $now.ToString('yyyy-MM-dd HH:mm:ss'), $verdict, $wpid, $cpuDelta, $conns.Count, $logAge, $dbAge, $sinceBoot, $issueTxt
    Add-Content -Path $health -Value $rec -Encoding ASCII
    $cut = (Get-Date).AddDays(-$KeepDays)
    Get-ChildItem (Join-Path $logDir 'health_*.log') | Where-Object { $_.LastWriteTime -lt $cut } | Remove-Item -Force
}

if ($verdict -eq 'PASS') { exit 0 } else { exit 1 }
