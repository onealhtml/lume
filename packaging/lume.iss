; Instalador do Lume para Windows (Inno Setup 6).
;   iscc /DAppVersion=1.0.0 packaging\lume.iss
; Espera o build do PyInstaller em dist\Lume.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F2B8C1E-5A4D-4E7B-9C3A-2D1F0B8E7A61}
AppName=Lume
AppVersion={#AppVersion}
AppVerName=Lume {#AppVersion}
AppPublisher=Lorenzo Farias
AppPublisherURL=https://github.com/onealhtml/lume
; instala so para o usuario: nao pede administrador
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Lume
DisableProgramGroupPage=yes
DisableDirPage=yes
OutputDir=..\dist
OutputBaseFilename=Lume-{#AppVersion}-Windows-Setup
SetupIconFile=..\assets\lume.ico
UninstallDisplayIcon={app}\Lume.exe
UninstallDisplayName=Lume
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Iniciar o Lume junto com o Windows"

[Files]
Source: "..\dist\Lume\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Lume"; Filename: "{app}\Lume.exe"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Lume"; \
  ValueData: """{app}\Lume.exe"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\Lume.exe"; Description: "Abrir o Lume"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im Lume.exe"; Flags: runhidden; RunOnceId: "FecharLume"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // a opcao "Iniciar com o sistema" do app tambem grava aqui; nao deixa entrada orfa
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Lume');
end;
