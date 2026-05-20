; ZenLink Installer Script
; Inno Setup 6.x — https://jrsoftware.org/isinfo.php

#define MyAppName "ZenLink"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "JayQuan McCleary"
#define MyAppURL "https://addons.mozilla.org/en-US/firefox/addon/d459006ef1504bdc8d2f/"
#define MyAppExeName "zenlink-bridge.exe"

[Setup]
AppId={{A3F2C1D4-8B6E-4F9A-B2D7-1E5C3A7F0928}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}

DefaultDirName={userappdata}\ZenLink
DisableDirPage=yes
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=no

OutputDir=D:\ZenLink\installer\output
OutputBaseFilename=ZenLink-Setup-{#MyAppVersion}

SetupIconFile=D:\ZenLink\icons\zenlink.ico
UninstallDisplayIcon={userappdata}\ZenLink\{#MyAppExeName}
UninstallDisplayName={#MyAppName} Bridge

PrivilegesRequired=lowest
WizardStyle=modern
WizardSizePercent=120
LicenseFile=D:\ZenLink\LICENSE

Compression=lzma2/ultra64
SolidCompression=yes
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "Start ZenLink bridge automatically when Windows starts"; GroupDescription: "Startup:"; Flags: checked

[Files]
Source: "D:\ZenLink\native\dist\zenlink-bridge.exe"; DestDir: "{userappdata}\ZenLink"; Flags: ignoreversion

[Icons]
Name: "{group}\ZenLink Bridge"; Filename: "{userappdata}\ZenLink\{#MyAppExeName}"; Comment: "ZenLink native bridge for Zen Browser automation"
Name: "{group}\Uninstall ZenLink"; Filename: "{uninstallexe}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ZenLink"; ValueData: """{userappdata}\ZenLink\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{userappdata}\ZenLink\{#MyAppExeName}"; Flags: nowait postinstall skipifsilent runhidden; Description: "Start ZenLink bridge now"
Filename: "{#MyAppURL}"; Flags: nowait postinstall shellexec skipifsilent; Description: "Install ZenLink extension in Zen Browser (opens browser)"

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/F /IM {#MyAppExeName}"; Flags: runhidden waituntilterminated; RunOnceId: "KillBridge"

[UninstallDelete]
Type: dirifempty; Name: "{userappdata}\ZenLink"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ZenLinkDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    ZenLinkDir := ExpandConstant('{userappdata}\ZenLink');
    if DirExists(ZenLinkDir) then
      DelTree(ZenLinkDir, True, True, True);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/F /IM zenlink-bridge.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
