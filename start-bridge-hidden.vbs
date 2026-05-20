Set WshShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")
Set objWMI = GetObject("winmgmts:\\.\root\cimv2")

' Start bridge hidden using compiled exe (no Python required)
Set bridgeProcesses = objWMI.ExecQuery("SELECT * FROM Win32_Process WHERE Name = 'zenlink-bridge.exe'")
If bridgeProcesses.Count = 0 Then
    bridgeExe = "D:\.LLMTools\ZenLink\native\dist\zenlink-bridge.exe"
    If Not objFSO.FileExists(bridgeExe) Then
        bridgeExe = WshShell.ExpandEnvironmentStrings("%APPDATA%") & "\ZenLink\zenlink-bridge.exe"
    End If
    If objFSO.FileExists(bridgeExe) Then
        WshShell.Run """" & bridgeExe & """", 0, False
    Else
        MsgBox "ZenLink bridge executable not found." & vbCrLf & bridgeExe, vbCritical, "ZenLink Bridge"
        WScript.Quit 1
    End If
End If

' Wait for bridge to start
WScript.Sleep 2000

' Launch Zen Browser (only if not already running)
Set colProcesses = objWMI.ExecQuery("SELECT * FROM Win32_Process WHERE Name = 'zen.exe'")
If colProcesses.Count = 0 Then
    WshShell.Run """D:\Zen Browser\zen.exe""", 1, False
End If
