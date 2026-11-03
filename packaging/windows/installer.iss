; The Windows installer, made with Inno Setup 6 (https://jrsoftware.org/isinfo.php) from the program
; PyInstaller made (reality-later.spec). From the project's folder, on Windows, after PyInstaller:
;
;     iscc /DVersion=0.2.0 /DFileVersion=0.2.0.0 packaging\windows\installer.iss
;
; makes dist\reality-later-setup.exe. (The GitHub workflow does all of it: see README.md.)
;
; It installs for the current user, so it needs no administrator rights, in
; %LOCALAPPDATA%\Programs\Reality Later, with a Start menu entry and an uninstaller (in Settings › Apps).
; The user's data is elsewhere (%APPDATA%\Reality Later): updates and uninstalling leave it alone.

#ifndef Version
  #define Version "0.0.0"
#endif
#ifndef FileVersion
  #define FileVersion "0.0.0.0"
#endif
#define AppName "Reality Later"
#define Program "Reality Later.exe"
#define Console "reality-later.exe"
#define Built "..\..\dist\Reality Later"
#define Repository "https://github.com/MiryanaNovak/reality-later"

[Setup]
; (Never change AppId: it's how Windows knows the next version is the same app, to update it.)
AppId={{B1A503AE-D2E7-41D5-AE6C-38757572AFA9}
AppName={#AppName}
AppVersion={#Version}
AppVerName={#AppName} {#Version}
AppPublisher=MiryanaNovak
AppPublisherURL={#Repository}
AppSupportURL={#Repository}/issues
AppUpdatesURL={#Repository}/releases
VersionInfoVersion={#FileVersion}
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\..\dist
OutputBaseFilename=reality-later-setup
SetupIconFile=..\icons\windows\reality-later.ico
UninstallDisplayIcon={app}\{#Program}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; Files still in use after the running copy was asked to quit (see [Code]): close what holds them.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "A shortcut on the desktop"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "startup"; Description: "Start {#AppName} when I sign in to Windows (quietly: just its icon in the tray)"; GroupDescription: "Starting:"; Flags: unchecked

[InstallDelete]
; The previous version's libraries: the new ones replace them all.
Type: filesandordirs; Name: "{app}\_internal"
; A shortcut whose box was unticked this time
Type: files; Name: "{autodesktop}\{#AppName}.lnk"; Tasks: not desktopicon
Type: files; Name: "{userstartup}\{#AppName}.lnk"; Tasks: not startup

[Files]
Source: "{#Built}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#Program}"; Comment: "Chat with AI characters, in your browser"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#Program}"; Comment: "Chat with AI characters, in your browser"; Tasks: desktopicon
; At sign-in: without opening the browser (the tray icon, or the Start menu entry, opens it)
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#Program}"; Parameters: "--no-browser"; Tasks: startup

[Run]
Filename: "{app}\{#Program}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
// Before installing over a running copy, and before uninstalling: ask it to quit, with the program's
// own `quit` command (it waits until the app has stopped), then give its tray icon's process a moment
// to go too (it notices within 2 seconds). Its files can then be replaced, or removed.
procedure QuitRunningCopy();
var
  Exe: String;
  WasRunning: Boolean;
  ResultCode: Integer;
begin
  Exe := ExpandConstant('{app}\{#Console}');
  if not FileExists(Exe) then
    exit;
  WasRunning := FileExists(ExpandConstant('{userappdata}\{#AppName}\data\running.json'));
  Exec(Exe, 'quit', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if WasRunning then
    Sleep(3000);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  QuitRunningCopy();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  QuitRunningCopy();
  Result := True;
end;
