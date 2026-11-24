#ifndef AppVersion
  #error "Use scripts/build_windows.py to set AppVersion"
#endif

[Setup]
AppId={{B5609058-6D3B-4167-A23F-F11DD605F826}
AppName=AFDS Studio
AppVersion={#AppVersion}
AppPublisher=AFDS
AppPublisherURL=https://github.com/Git-cat000/AFDS
DefaultDirName={localappdata}\Programs\AFDS
DefaultGroupName=AFDS Studio
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
MinVersion=10.0
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
WizardStyle=modern
SetupIconFile={#LauncherIcon}
UninstallDisplayIcon={app}\AFDS.exe
OutputDir={#ReleaseDir}
OutputBaseFilename=AFDS-Studio-{#AppVersion}-Windows-x64-Setup
Compression=lzma2/fast
SolidCompression=yes
ArchiveExtraction=full
AppMutex=Local\AFDS.Studio.Active
CloseApplications=no
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "media"; Description: "Download FFmpeg for video and audio (112 MB, requires Internet)"

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip"; DestDir: "{app}\media"; DestName: "ffmpeg-9.0.2-essentials_build.zip"; ExternalSize: 350000000; Hash: "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"; Flags: external download extractarchive ignoreversion recursesubdirs createallsubdirs; Tasks: media

[Icons]
Name: "{autoprograms}\AFDS Studio"; Filename: "{app}\AFDS.exe"
Name: "{autodesktop}\AFDS Studio"; Filename: "{app}\AFDS.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AFDS.exe"; Description: "Open AFDS Studio"; Flags: nowait postinstall skipifsilent

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Workspace, InstallDir: String;
begin
  Result := '';
  Workspace := Uppercase(AddBackslash(ExpandConstant('{localappdata}\AFDS\Workspace')));
  InstallDir := Uppercase(AddBackslash(ExpandFileName(WizardDirValue)));
  if (Pos(Workspace, InstallDir) = 1) or (Pos(InstallDir, Workspace) = 1) then
    Result := 'Choose a program folder separate from the AFDS workspace.';
end;
