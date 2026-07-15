; ==========================================================================
;  Seed Code - Inno Setup script (public-release installer)
;
;  Packages the standalone dist\seedcode.exe (built by build.bat stage 1) and:
;    * installs into Program Files
;    * adds the install directory to the system PATH (removed on uninstall)
;    * creates Start Menu shortcuts and an optional Desktop shortcut
;    * if Python 3.12+ is missing, downloads and installs it automatically
;    * verifies the install by executing `seedcode --version`; a failure
;      aborts with an error instead of reporting success
;
;  Compile:  build.bat   (or:  iscc /ORelease setup.iss  from scripts\windows)
;  Output:   Release\SeedCodeSetup.exe
; ==========================================================================

#define MyAppName "Seed Code"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Al shahriar sowan"
#define MyAppURL "https://github.com/Alshahriar-07/seedbot-cli"
#define MyAppExeName "seedcode.exe"
; Official python.org installer used for the automatic Python bootstrap.
#define PythonInstallerURL "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"

[Setup]
; A stable AppId ties installs/upgrades/uninstalls together. Keep it constant.
AppId={{7B2C4E10-9F3A-4B6D-8C21-3E5A1D9F0C77}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\SeedCode
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Default output; build.bat overrides with /O to target the repo-root Release\.
OutputDir=..\..\Release
OutputBaseFilename=SeedCodeSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
; Declaring PATH changes makes Windows broadcast WM_SETTINGCHANGE when setup
; finishes, so NEW terminal sessions see the updated PATH immediately.
ChangesEnvironment=yes
LicenseFile=..\..\LICENSE
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; The self-contained CLI produced by PyInstaller - all Python dependencies
; are already bundled inside it, so end users need nothing else to RUN it.
Source: "..\..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Append {app} to the system PATH only when it is not already present, so the
; `seedcode` command resolves from any directory in any new shell.
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; \
    ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; \
    Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent

[Code]
var
  PythonWasInstalled: Boolean;

// ---------------------------------------------------------------------------
// PATH management
// ---------------------------------------------------------------------------
function NeedsAddPath(Param: string): Boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKLM,
    'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
    'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  // Wrap in semicolons so partial directory names never match by accident.
  Result := Pos(';' + Uppercase(Param) + ';', ';' + Uppercase(OrigPath) + ';') = 0;
end;

// On uninstall, strip the app directory back out of the system PATH.
procedure RemovePath(Param: string);
var
  OrigPath: string;
  NewPath: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKLM,
    'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
    'Path', OrigPath) then
    exit;

  NewPath := ';' + OrigPath + ';';
  P := Pos(';' + Uppercase(Param) + ';', Uppercase(NewPath));
  if P > 0 then
  begin
    Delete(NewPath, P, Length(Param) + 1);
    if (Length(NewPath) > 0) and (NewPath[1] = ';') then
      Delete(NewPath, 1, 1);
    if (Length(NewPath) > 0) and (NewPath[Length(NewPath)] = ';') then
      Delete(NewPath, Length(NewPath), 1);
    RegWriteExpandStringValue(HKLM,
      'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
      'Path', NewPath);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemovePath(ExpandConstant('{app}'));
end;

// ---------------------------------------------------------------------------
// Python 3.12+ bootstrap
// The packaged seedcode.exe is fully self-contained, but Seed Code's dev
// workflow (editable installs, pip) targets Python 3.12+, so the installer
// provisions it when absent - without failing the app install if the
// download is declined or offline.
// ---------------------------------------------------------------------------
function PythonSatisfies(const Cmd, Args: string): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(Cmd, Args +
    ' -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 12) else 1)"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function PythonPresent: Boolean;
begin
  Result := PythonSatisfies(ExpandConstant('{sys}\cmd.exe'), '/C py -3.13') or
            PythonSatisfies(ExpandConstant('{sys}\cmd.exe'), '/C py -3.12') or
            PythonSatisfies(ExpandConstant('{sys}\cmd.exe'), '/C python');
end;

procedure InstallPython;
var
  Installer: string;
  ResultCode: Integer;
begin
  Installer := ExpandConstant('{tmp}\python-installer.exe');
  WizardForm.StatusLabel.Caption := 'Downloading Python 3.12 (one-time setup)...';
  try
    DownloadTemporaryFile('{#PythonInstallerURL}', 'python-installer.exe', '', nil);
  except
    Log('Python download failed: ' + GetExceptionMessage);
    MsgBox('Python 3.12 could not be downloaded (offline?). Seed Code itself ' +
           'will still work; install Python later from python.org if you plan ' +
           'to use the development workflow.', mbInformation, MB_OK);
    exit;
  end;

  WizardForm.StatusLabel.Caption := 'Installing Python 3.12 (this may take a minute)...';
  // Quiet, all-users install that also puts python on PATH and installs pip -
  // the officially documented unattended options for the python.org installer.
  if not Exec(Installer,
      '/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
  begin
    Log(Format('Python installer exit code: %d', [ResultCode]));
    MsgBox('The Python 3.12 installer did not complete (code ' +
           IntToStr(ResultCode) + '). Seed Code itself will still work.',
           mbInformation, MB_OK);
    exit;
  end;
  PythonWasInstalled := True;

  // Upgrade pip on the freshly installed interpreter (best-effort).
  WizardForm.StatusLabel.Caption := 'Upgrading pip...';
  Exec(ExpandConstant('{sys}\cmd.exe'),
       '/C py -3.12 -m pip install --upgrade pip',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// ---------------------------------------------------------------------------
// Install-time hooks
// ---------------------------------------------------------------------------
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
  begin
    // Provision Python before files are copied so the status flow reads well.
    if not PythonPresent then
      InstallPython
    else
      Log('Python 3.12+ already present; skipping bootstrap.');
  end;

  if CurStep = ssPostInstall then
  begin
    // MANDATORY verification: run the installed exe. If `seedcode --version`
    // fails, report installation FAILURE (abort) instead of success.
    WizardForm.StatusLabel.Caption := 'Verifying installation...';
    if not Exec(ExpandConstant('{app}\{#MyAppExeName}'), '--version', '',
                SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
    begin
      Log(Format('Verification failed, exit code %d', [ResultCode]));
      SuppressibleMsgBox(
        'Installation verification FAILED: "seedcode --version" did not run ' +
        'correctly (exit code ' + IntToStr(ResultCode) + '). ' +
        'The installation is incomplete - please report this issue.',
        mbCriticalError, MB_OK, IDOK);
      Abort;
    end;
    Log('Verification passed: seedcode --version exit code 0.');
  end;
end;
