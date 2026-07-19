; Inno Setup script for Stellar Clipboard
; Built in CI with: ISCC.exe /DAppVersion=<version> windows_installer.iss

#ifndef AppVersion
#define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8E0A2F0C-5B7C-4D8B-9C7E-A1B2C3D4E5F6}
AppName=Stellar Clipboard
AppVersion={#AppVersion}
AppPublisher=StellarStar255
AppPublisherURL=https://github.com/StellarStar255/stellar_smart_share_clipboard
DefaultDirName={autopf}\StellarClipboard
DefaultGroupName=Stellar Clipboard
DisableProgramGroupPage=yes
; 免管理员权限, 安装到当前用户目录, 实现一键安装
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=StellarClipboard-v{#AppVersion}-windows-setup
SetupIconFile=..\assets\icon.ico
Compression=lzma
SolidCompression=yes
CloseApplications=yes

[Files]
Source: "..\dist\StellarClipboard.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Stellar Clipboard"; Filename: "{app}\StellarClipboard.exe"
Name: "{autodesktop}\Stellar Clipboard"; Filename: "{app}\StellarClipboard.exe"

; 不加 skipifsilent: 应用内静默升级 (/SILENT) 完成后也要自动重启程序
[Run]
Filename: "{app}\StellarClipboard.exe"; Description: "Launch Stellar Clipboard"; Flags: nowait postinstall
