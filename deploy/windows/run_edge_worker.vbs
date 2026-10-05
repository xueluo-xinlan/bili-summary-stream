' BiliSummaryStream 边缘算力节点启动器：隐藏窗口、单实例、按日轮转日志、14 天保留。
' 由计划任务 \BiliEdgeWorker（登录时）调用，范式与 \BiliSummaryStream 的 run_silent.vbs 一致。
Option Explicit
Dim sh, fso, wmi, procs, dir, exe, cmd, logDir, logFile, stamp, d, f, cutoff, n

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
exe = dir & "\.venv\Scripts\python.exe"
logDir = dir & "\logs"

' 单实例守卫：若已有 edge_worker.py 进程在跑则直接退出
On Error Resume Next
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE CommandLine LIKE '%edge_worker.py%'")
If Err.Number = 0 Then
    If procs.Count > 0 Then WScript.Quit 0
End If
On Error GoTo 0

If Not fso.FolderExists(logDir) Then fso.CreateFolder(logDir)

' 按日日志文件名（yyyyMMdd）
d = Now
stamp = Year(d) & Right("0" & Month(d), 2) & Right("0" & Day(d), 2)
logFile = logDir & "\edge_" & stamp & ".log"

' 保留策略：删除 14 天前的 edge_*.log
cutoff = DateAdd("d", -14, Now)
n = 0
For Each f In fso.GetFolder(logDir).Files
    If LCase(Left(f.Name, 5)) = "edge_" And f.DateLastModified < cutoff Then
        f.Delete True
        n = n + 1
    End If
Next

If fso.FileExists(exe) Then
    ' 外层引号对不可省：cmd /c 会在参数同时以引号开头结尾时剥掉首尾引号，
    ' 从而静默破坏调用。此写法已在 \BiliSummaryStream 上验证可用。
    cmd = "cmd.exe /d /c " & Chr(34) & Chr(34) & dir & "\edge_worker_logged.cmd" & Chr(34) & " " & Chr(34) & logFile & Chr(34) & Chr(34)
    sh.Run cmd, 0, False
End If
