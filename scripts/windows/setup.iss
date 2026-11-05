; ==========================================================================
;  Seed Code - Inno Setup script (one-click public-release installer)
;
;  Packages the standalone dist\seedcode.exe (built by build.bat stage 1).
;  The exe is fully self-contained (PyInstaller bundles Python + every
;  dependency), so the end user needs NOTHING pre-installed: download
;  SeedCodeSetup.exe, run it, done.
;
;    * modern branded wizard (Seed Code icon, wizard art, license page)
;    * no Python required, detected, or downloaded on the user's PC
;    * installs into Program Files, adds the folder to the system PATH
;    * Start Menu shortcut always, Desktop shortcut by DEFAULT (still
;      deselectable) - all with the Seed Code icon (never the Python icon)
;    * verifies TWICE before claiming success: the installed exe reports
;      exactly this installer's version, and `seedcode` resolves through
;      PATH the way a NEW terminal will see it; failure aborts loudly
;    * offers to launch Seed Code when setup finishes
;    * uninstaller removes exe/shortcuts/PATH and ASKS before deleting user
;      data (settings, API keys, chat history, logs)
;
;  Compile:  build.bat   (or:  iscc /ORelease setup.iss  from scripts\windows)
;  Output:   Release\SeedCodeSetup.exe
; ==========================================================================

#define MyAppName "Seed Code"
; build.bat injects the version it verified against the freshly built exe
; (/DAppVersionFromBuild=...), so installer metadata can never disagree with
; the packaged binary. The fallback below is only for manual ISCC runs.
#ifdef AppVersionFromBuild
  #define MyAppVersion AppVersionFromBuild
#else
  #define MyAppVersion "10.2.1"
#endif
; Refuse to compile without the build outputs - packaging nothing (or a
; leftover) must fail loudly, not "succeed".
#if !FileExists(AddBackslash(SourcePath) + "..\..\dist\seedcode.exe")
  #error "dist\seedcode.exe not found - run scripts\windows\build.bat"
#endif
#if !FileExists(AddBackslash(SourcePath) + "..\..\assets\windows\seedcode.ico")
  #error "assets\windows\seedcode.ico not found - run: python scripts\windows\build_assets.py"
#endif
#define MyAppPublisher "Eagox Studio"
#define MyAppURL "https://github.com/Alshahriar-07/seedcode-cli"
#define MyAppExeName "seedcode.exe"

[Setup]
; A stable AppId ties installs/upgrades/uninstalls together. Keep it constant.
AppId={{7B2C4E10-9F3A-4B6D-8C21-3E5A1D9F0C77}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Installer
DefaultDirName={autopf}\SeedCode
DefaultGroupName={#MyAppName}
; One-click flow: skip the Start Menu group page (sane default is used) so a
; user who accepts defaults clicks License -> Install -> Finish and is done.
DisableProgramGroupPage=yes
; Write an installer log so silent/unattended runs (WinGet, scripts) can
; inspect what happened via the /LOG= switch and SetupLog.
SetupLogging=yes
; Branding: the setup.exe itself, the wizard pages, and Add/Remove Programs
; all use the Seed Code artwork - the default icon never appears.
SetupIconFile=..\..\assets\windows\seedcode.ico
WizardImageFile=..\..\assets\windows\wizard.bmp
WizardSmallImageFile=..\..\assets\windows\wizard-small.bmp
WizardStyle=modern
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\seedcode.ico
; Default output; build.bat overrides with /O to target the repo-root Release\.
; and /DOutputBaseName to stamp the versioned installer filename.
OutputDir=..\..\Release
#ifdef OutputBaseName
  #define OutBase OutputBaseName
#else
  #define OutBase "SeedCodeSetup"
#endif
OutputBaseFilename={#OutBase}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
; Declaring PATH changes makes Windows broadcast WM_SETTINGCHANGE when setup
; finishes, so NEW terminal sessions see the updated PATH immediately.
ChangesEnvironment=yes
LicenseFile=..\..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; v9.3.1: checked by default. The installer must leave a desktop icon behind
; without the user having to find an unchecked "additional icons" task.
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Additional icons:"

[Files]
; The self-contained CLI produced by PyInstaller - all Python dependencies
; are already bundled inside it, so end users need nothing else to RUN it.
Source: "..\..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; The icon ships alongside the exe so every shortcut and the Add/Remove
; Programs entry reference the same authoritative .ico file.
Source: "..\..\assets\windows\seedcode.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
; The Tesseract OCR engine, shipped so the detection ladder's OCR tier works on
; a fresh machine with NO manual setup - the user is never told to go install
; OCR. seedcode\computer\ocr.py finds it at {app}\tesseract\ automatically.
; skipifsourcedoesntexist keeps a build without a vendored engine compilable;
; the postinstall check below reports the resulting capability honestly.
Source: "..\..\vendor\tesseract\*"; DestDir: "{app}\tesseract"; \
    Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
; The VS Code extension VSIX bundled for automatic post-install integration.
; Extracted to {tmp}\vsce so the installer does not depend on the source tree.
Source: "..\..\vsce\Release\*.vsix"; DestDir: "{tmp}\vsce"; Flags: ignoreversion

[Icons]
; IconFilename pins every shortcut to the Seed Code .ico explicitly (belt and
; braces on top of the icon PyInstaller embeds into the exe itself).
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{app}\seedcode.ico"; Comment: "Plant ideas. Grow code."
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{app}\seedcode.ico"; Tasks: desktopicon

[Registry]
; Append {app} to the system PATH only when it is not already present, so the
; `seedcode` command resolves from any directory in any new shell.
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; \
    ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; \
    Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
; postinstall entries run as the ORIGINAL (non-elevated) user by default, so
; Seed Code starts under the logged-in account and onboarding writes to THAT
; user's ~\.seedcode - a fresh profile gets the full first-run wizard
; (provider -> API key -> model), with no provider/model/history preset.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent

[Code]
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

// ---------------------------------------------------------------------------
// Install verification (mandatory - a failure aborts, never false success)
// ---------------------------------------------------------------------------
// Two independent probes, both required:
//   1. The INSTALLED exe reports exactly this installer's version (exit code
//      0 alone would also pass for a stale binary; the version string proves
//      the packaged exe is the one this setup was built against).
//   2. Bare `seedcode --version` resolves through PATH exactly as a NEW
//      terminal will see it (the fresh {app} entry is appended for the probe
//      because this process still holds the pre-install environment).
// ---------------------------------------------------------------------------
// Version boundary matching (v9.3.1)
// ---------------------------------------------------------------------------
// The CLI reports "Seed Code CLI 9.3.2" — no 'v' prefix. Matching the version
// as a whole token accepts that output, still accepts "Seed Code CLI v9.3.2",
// and rejects a longer build number: '19.3.2' does not pass for '9.3.2'
// (version preceded by a digit) and '9.3.2' does not pass for '9.3.21' /
// '9.3.2.1' (version followed by a digit or a dot). The optional v/V version
// marker must stand alone, not inside a longer word ("rev9.3.2" is refused).
// The same rules are implemented in install.ps1 (Test-InstalledVersion) and
// install.sh (version_ok).
function VersionInOutput(const Reported, Version: string): Boolean;
var
  I, L, N: Integer;
  Before, After, Before2: Char;
begin
  Result := False;
  L := Length(Version);
  N := Length(Reported);
  if (L = 0) or (N = 0) or (L > N) then
    exit;
  for I := 1 to N - L + 1 do
  begin
    if Copy(Reported, I, L) <> Version then
      continue;
    // Boundary BEFORE the version: a separator, the start of the text, or a
    // lone v/V marker. A digit ('19.3.1') or a dot ('...9.3.1') never is.
    if I > 1 then
    begin
      Before := Reported[I - 1];
      if ((Before >= '0') and (Before <= '9')) or (Before = '.') then
        continue;
      if ((Before >= 'A') and (Before <= 'Z')) or
         ((Before >= 'a') and (Before <= 'z')) then
      begin
        if (Before <> 'v') and (Before <> 'V') then
          continue;
        // The v/V marker itself must not be part of a longer word.
        Before2 := ' ';
        if I > 2 then
          Before2 := Reported[I - 2];
        if ((Before2 >= 'A') and (Before2 <= 'Z')) or
           ((Before2 >= 'a') and (Before2 <= 'z')) then
          continue;
      end;
    end;
    // Boundary AFTER the version: end of text or a non-digit, non-dot char
    // ('9.3.1' is not '9.3.11' nor '9.3.1.1').
    After := ' ';
    if I + L <= N then
      After := Reported[I + L];
    if ((After >= '0') and (After <= '9')) or (After = '.') then
      continue;
    Result := True;
    exit;
  end;
end;

function VersionReportOk(const Command: string; var Reported: string): Boolean;
var
  ResultCode: Integer;
  VerFile: string;
  Output: AnsiString;
begin
  Result := False;
  VerFile := ExpandConstant('{tmp}\seedcode-version.txt');
  // cmd /C "<command> > <file> 2>&1" - one outer quote pair; inner quotes
  // (around the exe path) are preserved by cmd's quote-stripping rules.
  if not Exec(ExpandConstant('{cmd}'),
      '/C "' + Command + ' > "' + VerFile + '" 2>&1"',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
  begin
    Log(Format('"%s" exited with code %d', [Command, ResultCode]));
    exit;
  end;
  if not LoadStringFromFile(VerFile, Output) then
    exit;
  Reported := Trim(String(Output));
  Result := VersionInOutput(Reported, '{#MyAppVersion}');
end;

// ---------------------------------------------------------------------------
// VS Code Extension Auto-Installation
// ---------------------------------------------------------------------------
function FindVsixFile(const SearchDir: string; var VsixPath: string): Boolean;
var
  FindRec: TFindRec;
begin
  Result := False;
  VsixPath := '';
  if DirExists(SearchDir) and FindFirst(AddBackslash(SearchDir) + '*.vsix', FindRec) then
  begin
    try
      repeat
        if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) = 0 then
        begin
          VsixPath := AddBackslash(SearchDir) + FindRec.Name;
          Result := True;
          break;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

function FindVsCodeCli(var CodeExe: string): Boolean;
var
  Candidate: string;
  ResultCode: Integer;
  TempFile: string;
  Output: AnsiString;
begin
  Result := False;
  CodeExe := '';

  // 1. Check PATH via where code.cmd
  TempFile := ExpandConstant('{tmp}\vscode-where.txt');
  if Exec(ExpandConstant('{cmd}'), '/C where code.cmd > "' + TempFile + '" 2>&1',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
  begin
    if LoadStringFromFile(TempFile, Output) then
    begin
      Candidate := Trim(String(Output));
      if Pos(#13, Candidate) > 0 then
        Candidate := Trim(Copy(Candidate, 1, Pos(#13, Candidate) - 1));
      if Pos(#10, Candidate) > 0 then
        Candidate := Trim(Copy(Candidate, 1, Pos(#10, Candidate) - 1));
      if (Candidate <> '') and FileExists(Candidate) then
      begin
        CodeExe := Candidate;
        Result := True;
        if FileExists(TempFile) then DeleteFile(TempFile);
        exit;
      end;
    end;
  end;

  // 1b. Check PATH via where code
  if Exec(ExpandConstant('{cmd}'), '/C where code > "' + TempFile + '" 2>&1',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
  begin
    if LoadStringFromFile(TempFile, Output) then
    begin
      Candidate := Trim(String(Output));
      if Pos(#13, Candidate) > 0 then
        Candidate := Trim(Copy(Candidate, 1, Pos(#13, Candidate) - 1));
      if Pos(#10, Candidate) > 0 then
        Candidate := Trim(Copy(Candidate, 1, Pos(#10, Candidate) - 1));
      if (Candidate <> '') and FileExists(Candidate) then
      begin
        CodeExe := Candidate;
        Result := True;
        if FileExists(TempFile) then DeleteFile(TempFile);
        exit;
      end;
    end;
  end;
  if FileExists(TempFile) then DeleteFile(TempFile);

  // 2. Common User installation: %LOCALAPPDATA%\Programs\Microsoft VS Code\bin\code.cmd
  Candidate := ExpandConstant('{localappdata}\Programs\Microsoft VS Code\bin\code.cmd');
  if FileExists(Candidate) then
  begin
    CodeExe := Candidate;
    Result := True;
    exit;
  end;

  // 3. Common System installation (64-bit and 32-bit Program Files)
  Candidate := ExpandConstant('{commonpf}\Microsoft VS Code\bin\code.cmd');
  if FileExists(Candidate) then
  begin
    CodeExe := Candidate;
    Result := True;
    exit;
  end;

  Candidate := ExpandConstant('{commonpf64}\Microsoft VS Code\bin\code.cmd');
  if FileExists(Candidate) then
  begin
    CodeExe := Candidate;
    Result := True;
    exit;
  end;

  Candidate := ExpandConstant('{commonpf32}\Microsoft VS Code\bin\code.cmd');
  if FileExists(Candidate) then
  begin
    CodeExe := Candidate;
    Result := True;
    exit;
  end;

  // 4. Registry check (User install)
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{77193022-444F-4E77-807B-DD99DD544C86}_is1', 'InstallLocation', Candidate) then
  begin
    if (Candidate <> '') and FileExists(AddBackslash(Candidate) + 'bin\code.cmd') then
    begin
      CodeExe := AddBackslash(Candidate) + 'bin\code.cmd';
      Result := True;
      exit;
    end;
  end;

  // 5. Registry check (System install)
  if RegQueryStringValue(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{EA457770-0588-424C-AAD2-fa6132f39a88}_is1', 'InstallLocation', Candidate) or
     RegQueryStringValue(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{F8A2A208-72B3-4D61-95FC-8A65D340C2B4}_is1', 'InstallLocation', Candidate) or
     RegQueryStringValue(HKLM, 'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{EA457770-0588-424C-AAD2-fa6132f39a88}_is1', 'InstallLocation', Candidate) or
     RegQueryStringValue(HKLM, 'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{F8A2A208-72B3-4D61-95FC-8A65D340C2B4}_is1', 'InstallLocation', Candidate) then
  begin
    if (Candidate <> '') and FileExists(AddBackslash(Candidate) + 'bin\code.cmd') then
    begin
      CodeExe := AddBackslash(Candidate) + 'bin\code.cmd';
      Result := True;
      exit;
    end;
  end;

  // 6. User profile fallback
  Candidate := ExpandConstant('{%USERPROFILE}\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd');
  if FileExists(Candidate) then
  begin
    CodeExe := Candidate;
    Result := True;
    exit;
  end;
end;

procedure InstallVsCodeExtension();
var
  CodeCli: string;
  VsixFile: string;
  VsixDir: string;
  ResultCode: Integer;
  InstallCmd: string;
  InstallOutFile: string;
  InstallOutput: AnsiString;
begin
  VsixDir := ExpandConstant('{tmp}\vsce');
  if not FindVsixFile(VsixDir, VsixFile) then
  begin
    VsixDir := ExpandConstant('{tmp}');
    FindVsixFile(VsixDir, VsixFile);
  end;

  if (VsixFile = '') or not FileExists(VsixFile) then
  begin
    Log('VS Code extension VSIX not found in installer temporary directory.');
    exit;
  end;

  Log('Found bundled VS Code extension VSIX at: ' + VsixFile);

  if not FindVsCodeCli(CodeCli) then
  begin
    WizardForm.StatusLabel.Caption := 'VS Code not detected (Seed Code extension skipped).';
    Log('VS Code is not installed or "code" command was not found. Seed Code VS Code extension installation skipped (non-fatal).');
    if FileExists(VsixFile) then
      DeleteFile(VsixFile);
    exit;
  end;

  WizardForm.StatusLabel.Caption := 'VS Code detected.';
  Log('VS Code detected at: ' + CodeCli);

  WizardForm.StatusLabel.Caption := 'Installing Seed Code VS Code Extension...';
  Log('Installing Seed Code VS Code Extension using: ' + CodeCli + ' --install-extension "' + VsixFile + '" --force');

  InstallOutFile := ExpandConstant('{tmp}\vsce-install.log');
  InstallCmd := '/C ""' + CodeCli + '" --install-extension "' + VsixFile + '" --force > "' + InstallOutFile + '" 2>&1"';

  if Exec(ExpandConstant('{cmd}'), InstallCmd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
  begin
    WizardForm.StatusLabel.Caption := 'Extension installed successfully.';
    Log('Seed Code VS Code Extension installed successfully.');
    if LoadStringFromFile(InstallOutFile, InstallOutput) then
      Log('code CLI output: ' + Trim(String(InstallOutput)));
  end
  else
  begin
    WizardForm.StatusLabel.Caption := 'Warning: Seed Code VS Code Extension could not be installed.';
    Log(Format('VS Code extension installation returned code %d (non-fatal).', [ResultCode]));
    if LoadStringFromFile(InstallOutFile, InstallOutput) then
      Log('code CLI output: ' + Trim(String(InstallOutput)));
  end;

  if FileExists(InstallOutFile) then
    DeleteFile(InstallOutFile);
  if FileExists(VsixFile) then
    DeleteFile(VsixFile);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Reported: string;
begin
  if CurStep = ssPostInstall then
  begin
    // Probe 1: the installed binary itself.
    WizardForm.StatusLabel.Caption := 'Verifying the installed program...';
    Reported := '(no output)';
    if not VersionReportOk(
        '"' + ExpandConstant('{app}\{#MyAppExeName}') + '" --version', Reported) then
    begin
      Log('Exe verification FAILED. Output: ' + Reported);
      SuppressibleMsgBox(
        'Installation verification FAILED.' + #13#10#13#10 +
        'The installed seedcode.exe did not report version {#MyAppVersion} ' +
        '(it said: "' + Reported + '"). A version like "Seed Code CLI ' +
        '{#MyAppVersion}" IS accepted; a different build number, an empty or ' +
        'malformed output is not. ' + #13#10#13#10 +
        'The copied program is incomplete or damaged. Try running the ' +
        'installer again; if it keeps failing, report it at {#MyAppURL}.',
        mbCriticalError, MB_OK, IDOK);
      Abort;
    end;
    Log('Exe verification passed: ' + Reported);

    // Probe 2: the `seedcode` command as a NEW terminal will resolve it.
    // This process still has the old PATH, so append {app} for the probe -
    // the registry entry written above gives new shells the same view.
    WizardForm.StatusLabel.Caption := 'Verifying the seedcode command...';
    Reported := '(no output)';
    if not VersionReportOk(
        'set "PATH=%PATH%;' + ExpandConstant('{app}') + '" && seedcode --version',
        Reported) then
    begin
      Log('PATH verification FAILED. Output: ' + Reported);
      SuppressibleMsgBox(
        'Seed Code was installed, but the "seedcode" command could not be ' +
        'verified (output: "' + Reported + '").' + #13#10#13#10 +
        'Another program named seedcode may be shadowing it on PATH. ' +
        'You can still start Seed Code from the Start Menu shortcut. ' +
        'If the command does not work in a new terminal, report it at ' +
        '{#MyAppURL}.',
        mbCriticalError, MB_OK, IDOK);
      Abort;
    end;
    Log('PATH verification passed: ' + Reported);

    // Probe 3: the OCR engine. Informational only - OCR is one tier of the
    // detection ladder, so its absence must never abort an otherwise good
    // install. Logged either way so a support request can tell them apart.
    if FileExists(ExpandConstant('{app}\tesseract\tesseract.exe')) then
      Log('OCR engine installed: ' + ExpandConstant('{app}\tesseract\tesseract.exe'))
    else
      Log('OCR engine NOT bundled - the OCR tier will be unavailable. ' +
          'Vendor it at vendor\tesseract\ and rebuild to include it.');

    // Auto-install bundled Seed Code VS Code extension if VS Code is present.
    InstallVsCodeExtension();
  end;
end;

// ---------------------------------------------------------------------------
// Uninstall: PATH cleanup always; user data only after explicit consent
// ---------------------------------------------------------------------------
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    RemovePath(ExpandConstant('{app}'));

    // Settings, API keys, chat history, and logs all live in ~\.seedcode.
    // Never delete silently: they may be shared with a pip-installed copy.
    DataDir := ExpandConstant('{%USERPROFILE}\.seedcode');
    if DirExists(DataDir) then
    begin
      // A silent uninstall (/VERYSILENT) must never delete user data: there is
      // nobody to answer the prompt, so keep it unconditionally and log that
      // decision. Only an interactive uninstall may ask.
      if UninstallSilent then
        Log('User data kept at ' + DataDir + ' (silent uninstall)')
      else if SuppressibleMsgBox(
        'Also remove your Seed Code data?' + #13#10#13#10 +
        'This deletes settings, API keys, chat history, and logs at:' + #13#10 +
        DataDir + #13#10#13#10 +
        'Choose No to keep them for a future installation.',
        mbConfirmation, MB_YESNO, IDNO) = IDYES then
      begin
        if DelTree(DataDir, True, True, True) then
          Log('User data removed: ' + DataDir)
        else
          SuppressibleMsgBox(
            'Some Seed Code data could not be removed. Delete this folder ' +
            'manually if desired: ' + DataDir, mbInformation, MB_OK, IDOK);
      end
      else
        Log('User data kept at ' + DataDir);
    end;
  end;
end;
