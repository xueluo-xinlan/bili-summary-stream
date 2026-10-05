' BiliSummaryStream launcher: hidden, single-instance, daily-rotated log, retention pruning.
' Called by scheduled task \BiliSummaryStream (logon) - keeps the previous behaviour,
' adds: daily watch_YYYYMMDD.log capture (via watch_logged.cmd) + 14-day retention.
Option Explicit
Dim sh, fso, wmi, procs, dir, exe, cmd, logDir, logFile, stamp, d, f, cutoff, n

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
exe = dir & "\.venv\Scripts\python.exe"
logDir = dir & "\logs"

' single-instance guard: quit if a main.py watch process already exists
On Error Resume Next
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE CommandLine LIKE '%main.py watch%'")
If Err.Number = 0 Then
    If procs.Count > 0 Then WScript.Quit 0
End If
On Error GoTo 0

If Not fso.FolderExists(logDir) Then fso.CreateFolder(logDir)

' daily log file name (yyyyMMdd)
d = Now
stamp = Year(d) & Right("0" & Month(d), 2) & Right("0" & Day(d), 2)
logFile = logDir & "\watch_" & stamp & ".log"

' retention: drop watch_*.log not modified within 14 days
cutoff = DateAdd("d", -14, Now)
n = 0
For Each f In fso.GetFolder(logDir).Files
    If LCase(Left(f.Name, 6)) = "watch_" And f.DateLastModified < cutoff Then
        f.Delete True
        n = n + 1
    End If
Next

If fso.FileExists(exe) Then
    ' NOTE: the extra outer quote pair matters - cmd /c strips the first/last quote of its
    ' argument when it both starts and ends with one, which silently breaks the call.
    ' Proven locally: "cmd /c ""script"" ""arg""" works, "cmd /c "script" "arg"" does not.
    cmd = "cmd.exe /d /c " & Chr(34) & Chr(34) & dir & "\watch_logged.cmd" & Chr(34) & " " & Chr(34) & logFile & Chr(34) & Chr(34)
    sh.Run cmd, 0, False
End If
