#define MyAppName "SIEKACZ 9000"
#define MyAppExeName "SIEKACZ9000.exe"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "Kewin"

[Setup]
AppId={{C3B16626-EC6F-4AC9-97C9-7DA3B5F2F12B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=SIEKACZ9000_Setup
SetupIconFile=..\assets\app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"

[Tasks]
Name: "desktopicon"; Description: "Utworz skrot na pulpicie"; GroupDescription: "Skroty:"; Flags: checkedonce

[Files]
Source: "..\dist\portable\SIEKACZ9000\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\SIEKACZ 9000"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\_internal\assets\app_icon.ico"
Name: "{commondesktop}\SIEKACZ 9000"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\_internal\assets\app_icon.ico"; Tasks: desktopicon; Check: IsAdminInstallMode
Name: "{userdesktop}\SIEKACZ 9000"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\_internal\assets\app_icon.ico"; Tasks: desktopicon; Check: not IsAdminInstallMode

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Uruchom SIEKACZ 9000"; Flags: nowait postinstall skipifsilent
