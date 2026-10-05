# 注册 \BiliEdgeWorker：登录时启动边缘算力节点（隐藏窗口、单实例、崩溃自动重启）
# 范式对齐既有的 \BiliSummaryStream（wscript.exe + VBS 启动器）
$ErrorActionPreference = 'Stop'
$proj = 'C:\Projects\bili-summary-stream'
$vbs  = Join-Path $proj 'run_edge_worker.vbs'
$name = 'BiliEdgeWorker'

if (-not (Test-Path $vbs)) { throw "missing $vbs" }

$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"{0}"' -f $vbs)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

# 常驻服务：允许电池启动、不因切换电源停止、错过则补跑、单实例、
# 无执行时限（默认 PT72H 会在 3 天后杀掉它）、崩溃后 1 分钟重启
$setArgs = @{
    AllowStartIfOnBatteries    = $true
    DontStopIfGoingOnBatteries = $true
    StartWhenAvailable         = $true
    MultipleInstances          = 'IgnoreNew'
    ExecutionTimeLimit         = (New-TimeSpan -Seconds 0)
    RestartCount               = 999
    RestartInterval            = (New-TimeSpan -Minutes 1)
}
$settings = New-ScheduledTaskSettingsSet @setArgs

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

$t = Get-ScheduledTask -TaskName $name
Write-Output ("TASK REGISTERED: {0}  State={1}  RunAs={2}  LogonType={3}" -f $t.TaskName, $t.State, $t.Principal.UserId, $t.Principal.LogonType)
$t.Actions | ForEach-Object { Write-Output ("  ACTION  {0} {1}" -f $_.Execute, $_.Arguments) }
$t.Triggers | ForEach-Object { Write-Output ("  TRIGGER {0} enabled={1}" -f $_.CimClass.CimClassName, $_.Enabled) }
$s = $t.Settings
Write-Output ("  SETTINGS StartWhenAvailable={0} MultiInstance={1} ExecLimit='{2}' RestartCount={3}" -f $s.StartWhenAvailable, $s.MultipleInstances, $s.ExecutionTimeLimit, $s.RestartCount)
