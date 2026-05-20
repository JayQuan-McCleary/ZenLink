; ZenLink Installer — Inno Setup Script
; Compiles with Inno Setup 6.x (https://jrsoftware.org/isinfo.php)

#define MyAppName "ZenLink"
#define MyAppVersion "1.1.2"
#define MyAppPublisher "ZenLink"
#define MyAppExeName "zenlink-bridge.exe"
#define MyAppURL "https://addons.mozilla.org/en-US/firefox/addon/d459006ef1504bdc8d2f/"

[Setup]
AppId={{B7E3F1A2-9D4C-4E8B-A1F6-3C5D7E9B2A4F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={userappdata}\ZenLink
DisableProgramGroupPage=yes
DefaultGroupName={#MyAppName}
OutputDir=..\installer\output
OutputBaseFilename=ZenLink-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
SetupIconFile=
UninstallDisplayName={#MyAppName}
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\native\dist\zenlink-bridge.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\ZenLink Bridge"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall ZenLink"; Filename: "{uninstallexe}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "ZenLink"; \
  ValueData: """{app}\{#MyAppExeName}"""; \
  Flags: uninsdeletevalue

[Run]
; Launch the bridge silently after install
Filename: "{app}\{#MyAppExeName}"; Description: "Start ZenLink Bridge"; \
  Flags: nowait postinstall skipifsilent runhidden

; Open AMO page so user can install the extension
Filename: "https://addons.mozilla.org/en-US/firefox/addon/d459006ef1504bdc8d2f/"; \
  Description: "Install ZenLink browser extension"; \
  Flags: nowait postinstall skipifsilent shellexec

[UninstallRun]
; Kill the bridge process before uninstalling
Filename: "taskkill"; Parameters: "/F /IM {#MyAppExeName}"; \
  Flags: runhidden; RunOnceId: "KillBridge"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
