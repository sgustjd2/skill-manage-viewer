; Inno Setup script for the Windows installer. tools/build_exe.py runs it after PyInstaller (SKILLDESK_VERSION comes from VERSION).
; Installs for the current user only: no administrator rights, no UAC prompt.

#define AppVersion GetEnv("SKILLDESK_VERSION")

[Setup]
AppId={{07EBBDB8-B523-40F2-84C7-72F807E42005}
AppName=SkillDesk
AppVersion={#AppVersion}
AppPublisher=sgustjd2
AppPublisherURL=https://github.com/sgustjd2/skill-manage-viewer
DefaultDirName={autopf}\SkillDesk
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=SkillDesk-Setup-v{#AppVersion}
SetupIconFile=..\skill_viewer.ico
UninstallDisplayIcon={app}\SkillDesk.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕 화면에 바로 가기 만들기"; Flags: unchecked
Name: "startup"; Description: "Windows를 켤 때 트레이에서 자동으로 시작"; Flags: unchecked

[Files]
Source: "..\dist\SkillDesk\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\SkillDesk"; Filename: "{app}\SkillDesk.exe"
Name: "{autodesktop}\SkillDesk"; Filename: "{app}\SkillDesk.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "SkillDesk"; ValueData: """{app}\SkillDesk.exe"" --hidden"; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\SkillDesk.exe"; Description: "SkillDesk 실행"; Flags: nowait postinstall skipifsilent
