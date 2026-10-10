; Inno Setup script for the Epson waste ink tool. Built by build.ps1, which
; passes the version: ISCC /DAppVersion=1.0.0 installer.iss

#define AppName "Epson Waste Ink Tool"
#define AppExe "EpsonWasteInk.exe"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; Keep this AppId for every release, so new versions upgrade the old one.
AppId={{6E3C2A8B-5D41-4F7A-9C2E-1B8D4A7F0E35}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Intersol
; Installs for the current user only: no administrator rights needed, and the
; program folder stays writable for the backups the tool saves next to the .exe.
PrivilegesRequired=lowest
DefaultDirName={autopf}\Epson Waste Ink Tool
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=dist
OutputBaseFilename=EpsonWasteInk-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\reset-waste-ink.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "THIRD-PARTY-NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "vendor\LICENSE"; DestDir: "{app}\licenses"; DestName: "epson_print_conf-EUPL-1.2.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Backups are deliberately NOT deleted on uninstall: they are the only way to undo a change.
