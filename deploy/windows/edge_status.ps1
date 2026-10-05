# edge_status.ps1 -- health check for the BiliSummaryStream EDGE GPU worker (:18088)
# Replaces the old watch_status.ps1 duty now that the PC no longer runs the polling pipeline.
# Exit code 0 = PASS, 1 = FAIL  (ASCII-only on purpose: PS 5.1 mis-decodes BOM-less UTF-8.)
param(
    [int]$LogStaleMinutes = 30
)

$ErrorActionPreference = 'SilentlyContinue'
$proj   = 'C:\Projects\bili-summary-stream'
$logDir = Join-Path $proj 'logs'
$now    = Get-Date

# ---- 1. edge worker process ------------------------------------------------
$proc = @(Get-CimInstance Win32_Process |
    Where-Object { $_ -and $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -like '*edge_worker.py*' } |
    Sort-Object CreationDate | Select-Object -Last 1)
$epid = 0
if ($proc) { $epid = [int]$proc.ProcessId }

# ---- 2. listening port 18088 ----------------------------------------------
$listening = $false
$listenerPid = 0
if ($epid) {
    $c = @(Get-NetTCPConnection -State Listen -LocalPort 18088 -ErrorAction SilentlyContinue |
           Where-Object { $_.OwningProcess -eq $epid })
    if ($c.Count -gt 0) { $listening = $true; $listenerPid = [int]$c[0].OwningProcess }
}

# ---- 3. self-probe /health -------------------------------------------------
$probeOk = $false
$busy = $false
$jobsDone = -1
$jobsFailed = -1
$probeErr = ''
try {
    $r = Invoke-RestMethod -Uri 'http://127.0.0.1:18088/health' -TimeoutSec 5
    if ($r.status -eq 'ok') { $probeOk = $true }
    $busy = [bool]$r.busy
    $jobsDone = [int]$r.jobs_done
    $jobsFailed = [int]$r.jobs_failed
} catch {
    $probeErr = $_.Exception.Message
}

# ---- 4. edge log freshness -------------------------------------------------
$logAge = -1
$logName = ''
$edgeLog = @(Get-ChildItem (Join-Path $logDir 'edge_*.log') -ErrorAction SilentlyContinue |
             Sort-Object LastWriteTime -Descending)
if ($edgeLog.Count -gt 0) {
    $logAge = [int]($now - $edgeLog[0].LastWriteTime).TotalMinutes
    $logName = $edgeLog[0].Name
}

# ---- 5. recent unhandled-traceback count in the CURRENT run -----------------
# 日志按日追加，文件里含有历史运行的残留堆栈。必须只扫描最近一次
# [EDGE START] 之后的段落，否则重启前的旧错误会让哨兵永久误报 FAIL。
$tb = 0
if ($edgeLog.Count -gt 0) {
    $all = @(Get-Content $edgeLog[0].FullName -Encoding UTF8)
    $startIdx = 0
    for ($i = $all.Count - 1; $i -ge 0; $i--) {
        if ($all[$i].Contains('[EDGE START]')) { $startIdx = $i; break }
    }
    $seg = @($all[$startIdx..($all.Count - 1)])
    $tb = @($seg | Select-String -Pattern 'Traceback|Exception occurred').Count
}

# ---- verdict ---------------------------------------------------------------
$issues = @()
if ($epid -eq 0)                       { $issues += 'edge-worker-not-running' }
if ($epid -ne 0 -and -not $listening)  { $issues += 'port-18088-not-listening' }
if ($epid -ne 0 -and -not $probeOk)    { $issues += ('health-probe-failed={0}' -f $probeErr) }
if ($logAge -lt 0)                     { $issues += 'no-edge-log' }
elseif ($logAge -gt $LogStaleMinutes)  { $issues += ('log-stale={0}min' -f $logAge) }
if ($tb -gt 0)                         { $issues += ('unhandled-tracebacks={0}' -f $tb) }
$verdict = 'PASS'
if ($issues.Count -gt 0) { $verdict = 'FAIL' }
$issueTxt = 'none'
if ($issues.Count -gt 0) { $issueTxt = ($issues -join ',') }

# ---- snapshot block (human readable) --------------------------------------
if ($epid -eq 0) {
    Write-Output 'edge-worker NONE - src/edge_worker.py is not running'
} else {
    Write-Output ('edge-worker pid={0} commit={1}MB ws={2}MB started={3}' -f `
        $epid, [math]::Round($proc.PageFileUsage / 1KB, 1), [math]::Round($proc.WorkingSetSize / 1MB, 1), $proc.CreationDate)
}
Write-Output ('port-18088  {0}' -f $(if ($listening) { 'LISTEN pid=' + $listenerPid } else { 'NOT LISTENING' }))
Write-Output ('health      {0}' -f $(if ($probeOk) { "ok busy=$busy jobs_done=$jobsDone jobs_failed=$jobsFailed" } else { 'PROBE FAILED' }))
Write-Output ('edge-log    {0} age={1}min' -f $logName, $logAge)
Write-Output ('tracebacks  {0} (last 120 lines)' -f $tb)
Write-Output ('issues      {0}' -f $issueTxt)
Write-Output ('VERDICT     {0}' -f $verdict)

# ---- append one summary line ----------------------------------------------
$rec = '{0} | verdict={1} | edgePid={2} | listening={3} | probe={4} | busy={5} | jobs={6}/{7} | logAgeMin={8} | tracebacks={9} | issues={10}' -f `
    $now.ToString('yyyy-MM-dd HH:mm:ss'), $verdict, $epid, $listening, $probeOk, $busy, $jobsDone, $jobsFailed, $logAge, $tb, $issueTxt
$healthLog = Join-Path $logDir ('edge_health_{0}.log' -f $now.ToString('yyyyMMdd'))
Add-Content -Path $healthLog -Value $rec -Encoding UTF8

if ($verdict -eq 'PASS') { exit 0 } else { exit 1 }
