@echo off
REM ==========================================================================
REM  Seed Code - complete Windows build pipeline
REM    Stage 1: PyInstaller  -> dist\seedcode.exe
REM    Stage 2: Inno Setup   -> Release\SeedCodeSetup.exe
REM  The result is a public-release installer; no manual steps in between.
REM ==========================================================================
setlocal EnableDelayedExpansion

echo ============================================================
echo   Seed Code Build Pipeline (Windows)
echo ============================================================
echo.

REM --- Locate the repository root (two levels up from this script) ---------
set "REPO_ROOT=%~dp0..\.."
for %%I in ("%REPO_ROOT%") do set "REPO_ROOT=%%~fI"
if not exist "%REPO_ROOT%\pyproject.toml" (
    echo [ERROR] Could not find pyproject.toml at "%REPO_ROOT%".
    exit /b 3
)
echo [INFO] Repository: %REPO_ROOT%

set "LOG_DIR=%USERPROFILE%\.seedcode\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
set "LOG_FILE=%LOG_DIR%\build.log"
echo [INFO] Log file:   %LOG_FILE%
echo ===== build.bat run: %DATE% %TIME% ===== >> "%LOG_FILE%"

REM --- Find a Python 3.12+ interpreter --------------------------------------
set "PY_CMD="
call :try_python py -3.13
if not defined PY_CMD call :try_python py -3.12
if not defined PY_CMD call :try_python py -3
if not defined PY_CMD call :try_python python
if not defined PY_CMD call :try_python python3
if not defined PY_CMD (
    echo [ERROR] Python 3.12+ is required to build. Run install.bat first.
    exit /b 2
)
echo [INFO] Using interpreter: %PY_CMD%

REM ==========================================================================
REM  STAGE 1 - standalone executable
REM ==========================================================================
echo.
echo [STAGE 1/2] Building dist\seedcode.exe with PyInstaller...

%PY_CMD% -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [STEP] Installing PyInstaller...
    %PY_CMD% -m pip install --upgrade pyinstaller >> "%LOG_FILE%" 2>&1
    if errorlevel 1 (
        echo [ERROR] Could not install PyInstaller. See "%LOG_FILE%".
        exit /b 1
    )
)
echo [STEP] Ensuring project dependencies are installed...
%PY_CMD% -m pip install -e "%REPO_ROOT%" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo [ERROR] Dependency installation failed. See "%LOG_FILE%".
    exit /b 1
)

REM --collect-all bundles rich/prompt_toolkit data files that static analysis
REM misses; build\ is a disposable work directory.
echo [STEP] Running PyInstaller ^(this can take a few minutes^)...
%PY_CMD% -m PyInstaller --noconfirm --onefile --console ^
    --name seedcode ^
    --distpath "%REPO_ROOT%\dist" ^
    --workpath "%REPO_ROOT%\build" ^
    --specpath "%REPO_ROOT%\build" ^
    --collect-all rich ^
    --collect-all prompt_toolkit ^
    --collect-submodules pydantic ^
    --collect-submodules openai ^
    "%REPO_ROOT%\seedcode\__main__.py" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed. See "%LOG_FILE%".
    exit /b 1
)
if not exist "%REPO_ROOT%\dist\seedcode.exe" (
    echo [ERROR] Build finished but dist\seedcode.exe is missing.
    exit /b 1
)

REM Sanity-check the freshly built exe before packaging it.
echo [STEP] Smoke-testing the executable...
"%REPO_ROOT%\dist\seedcode.exe" --version
if errorlevel 1 (
    echo [ERROR] dist\seedcode.exe failed its --version smoke test.
    exit /b 1
)
echo [OK] Stage 1 complete: %REPO_ROOT%\dist\seedcode.exe

REM ==========================================================================
REM  STAGE 2 - installer
REM ==========================================================================
echo.
echo [STAGE 2/2] Compiling Release\SeedCodeSetup.exe with Inno Setup...

REM Locate ISCC.exe: PATH first, then the standard install locations.
set "ISCC="
where iscc >nul 2>&1 && set "ISCC=iscc"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo [ERROR] Inno Setup 6 ^(ISCC.exe^) was not found.
    echo         Install it from https://jrsoftware.org/isdl.php and re-run.
    exit /b 4
)
echo [INFO] Using ISCC: %ISCC%

if not exist "%REPO_ROOT%\Release" mkdir "%REPO_ROOT%\Release"
"%ISCC%" /O"%REPO_ROOT%\Release" "%~dp0setup.iss" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo [ERROR] Inno Setup compilation failed. See "%LOG_FILE%".
    exit /b 1
)
if not exist "%REPO_ROOT%\Release\SeedCodeSetup.exe" (
    echo [ERROR] ISCC succeeded but Release\SeedCodeSetup.exe is missing.
    exit /b 1
)

echo.
echo ============================================================
echo   [SUCCESS] Build pipeline complete.
echo   Standalone exe : %REPO_ROOT%\dist\seedcode.exe
echo   Installer      : %REPO_ROOT%\Release\SeedCodeSetup.exe
echo ============================================================
echo [SUCCESS] pipeline complete >> "%LOG_FILE%"
exit /b 0

:try_python
"%1" %2 -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 12) else 1)" >nul 2>&1
if not errorlevel 1 set "PY_CMD=%1 %2"
exit /b 0
