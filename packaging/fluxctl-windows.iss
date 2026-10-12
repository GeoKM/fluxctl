#define AppVersion GetEnv('FLUXCTL_VERSION')
#if AppVersion == ""
  #error FLUXCTL_VERSION must be set before compiling the installer
#endif

[Setup]
AppId={{2D8C1E3B-058B-4B47-90B0-D5DF0EDE4B2A}
AppName=Fluxctl
AppVersion={#AppVersion}
AppPublisher=Fluxctl Maintainers
AppPublisherURL=https://github.com/GeoKM/fluxctl
DefaultDirName={localappdata}\Programs\Fluxctl
DefaultGroupName=Fluxctl
PrivilegesRequired=lowest
OutputDir=..\dist\release
OutputBaseFilename=Fluxctl-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\studio\fluxctl-studio.exe

[Files]
Source: "..\dist\fluxctl\*"; DestDir: "{app}\cli"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\dist\fluxctl-studio\*"; DestDir: "{app}\studio"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Fluxctl Studio"; Filename: "{app}\studio\fluxctl-studio.exe"
Name: "{group}\Fluxctl Doctor"; Filename: "{app}\cli\fluxctl.exe"; Parameters: "doctor"; WorkingDir: "{app}"
Name: "{group}\Uninstall Fluxctl"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\studio\fluxctl-studio.exe"; Description: "Launch Fluxctl Studio"; Flags: nowait postinstall skipifsilent
