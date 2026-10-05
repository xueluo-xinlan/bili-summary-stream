# Register \BiliEdgeWorkerHealth: SYSTEM, every 15 min, appends a health line to logs\edge_health_YYYYMMDD.log
# Mirrors install_health_task.ps1 (the watcher-era equivalent that is being retired).
$ErrorActionPreference = 'Stop'
$proj = 'C:\Projects\bili-summary-stream'
$ps1  = Join-Path $proj 'edge_status.ps1'
$name = 'BiliEdgeWorkerHealth'

if (-not (Test-Path $ps1)) { throw "missing $ps1" }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $ps1)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$setArgs = @{
    AllowStartIfOnBatteries    = $true
    DontStopIfGoingOnBatteries = $true
    StartWhenAvailable         = $true
    MultipleInstances          = 'IgnoreNew'
    ExecutionTimeLimit         = (New-TimeSpan -Minutes 5)
}
$settings = New-ScheduledTaskSettingsSet @setArgs

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

$t = Get-ScheduledTask -TaskName $name
Write-Output ("TASK REGISTERED: {0}  State={1}  RunAs={2}" -f $t.TaskName, $t.State, $t.Principal.UserId)
$t.Triggers | ForEach-Object { Write-Output ("  TRIGGER {0} repeat={1}" -f $_.CimClass.CimClassName, $_.Repetition.Interval) }
Write-Output ("  ACTION  {0} {1}" -f $t.Actions[0].Execute, $t.Actions[0].Arguments)
