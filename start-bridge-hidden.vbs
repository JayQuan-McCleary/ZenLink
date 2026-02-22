Set WshShell = CreateObject("WScript.Shell")

' Start bridge hidden
WshShell.Run "pythonw D:\ZenLink\native\bridge.py", 0, False

' Wait for bridge to start
WScript.Sleep 2000

' Launch Zen Browser (only if not already running)
Set objWMI = GetObject("winmgmts:\\.\root\cimv2")
Set colProcesses = objWMI.ExecQuery("SELECT * FROM Win32_Process WHERE Name = 'zen.exe'")
If colProcesses.Count = 0 Then
    WshShell.Run """D:\Zen Browser\zen.exe""", 1, False
End If