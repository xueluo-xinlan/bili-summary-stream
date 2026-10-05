# Register \BiliSummaryStreamHealth: SYSTEM, every 15 min, appends a health line to logs\health_YYYYMMDD.log
$ErrorActionPreference = 'Stop'
$proj = 'C:\Projects\bili-summary-stream'
$ps1  = Join-Path $proj 'watch_status.ps1'
$name = 'BiliSummaryStreamHealth'

if (-not (Test-Path $ps1)) { throw "missing $ps1" }

$action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
             -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}" -SampleSec 0 -Snapshot' -f $ps1)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
             -RepetitionInterval (New-TimeSpan -Minutes 15) `
             -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

$t = Get-ScheduledTask -TaskName $name
"TASK REGISTERED: {0}  State={1}  RunAs={2}" -f $t.TaskName, $t.State, $t.Principal.UserId
$t.Triggers | ForEach-Object { "  TRIGGER {0} repeat={1}/{2} enabled={3}" -f $_.CimClass.CimClassName, $_.Repetition.Interval, $_.Repetition.Duration, $_.Enabled }
"  ACTION  {0} {1}" -f $t.Actions[0].Execute, $t.Actions[0].Arguments
"  SETTINGS StartWhenAvailable={0} MultiInstance={1} ExecLimit={2}" -f $t.Settings.StartWhenAvailable, $t.Settings.MultipleInstances, $t.Settings.ExecutionTimeLimit
