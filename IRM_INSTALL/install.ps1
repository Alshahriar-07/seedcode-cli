#Requires -Version 5.1
<#
.SYNOPSIS
    Seed Code CLI v6.2.5 - official Windows remote installer.

.DESCRIPTION
    Installs Seed Code CLI for the CURRENT USER. No administrator rights, no
    Python, no package manager, and no cloned repository are required: the
    official standalone release binary is downloaded from GitHub Releases,
    its SHA256 is verified, and `seedcode` is added to your user PATH.

    Usage (exactly as documented for remote install):

        irm https://seedcode-cli.vercel.app/install.ps1 | iex

    Or, to pass options, download-then-run:

        & ([scriptblock]::Create((irm https://seedcode-cli.vercel.app/install.ps1))) -Version 6.2.5

.PARAMETER Version
    Release version to install. Defaults to 6.2.5 (the current stable release).

.PARAMETER InstallDir
    Install directory. Defaults to %LOCALAPPDATA%\Programs\SeedCode.

.PARAMETER NoPathUpdate
    Install without modifying PATH. Use `seedcode` via the full path instead.

.PARAMETER Force
    Reinstall even when the requested version is already installed.

.EXAMPLE
    irm https://seedcode-cli.vercel.app/install.ps1 | iex

.NOTES
    Requires PowerShell 5.1 or newer and an HTTPS-capable connection.
    This script never sees, stores, or prints an API key; credentials are set
    up inside the app on first run.
#>
[CmdletBinding()]
param(
    [string] $Version = "6.2.5",
    [string] $InstallDir = "",
    [switch] $NoPathUpdate,
    [switch] $Force
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

# --- constants ---------------------------------------------------------------
$Repo        = "Alshahriar-07/seedcode-cli"
$ReleaseBase = "https://github.com/$Repo/releases/download/v$Version"
$SumsUrl     = "$ReleaseBase/SHA256SUMS.txt"
$ExeName     = "seedcode.exe"

# --- tiny console helpers ----------------------------------------------------
function Write-Head([string] $Text) {
    Write-Host ""
    Write-Host "  $Text" -ForegroundColor Green
    Write-Host ("  " + ("-" * $Text.Length)) -ForegroundColor DarkGray
}

function Write-Step([string] $Text) { Write-Host "  - $Text" -ForegroundColor Gray }
function Write-Ok([string] $Text)   { Write-Host "  OK  $Text" -ForegroundColor Green }

function Fail([string] $Text) {
    Write-Host ""
    Write-Host "  Installer error: $Text" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Download manually from: https://github.com/$Repo/releases" -ForegroundColor DarkGray
    Write-Host ""
    exit 1
}

# --- environment -------------------------------------------------------------
function Enable-Tls12 {
    # Windows PowerShell 5.1 defaults to TLS 1.0 on some hosts; GitHub needs 1.2+.
    try {
        [Net.ServicePointManager]::SecurityProtocol =
            [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    } catch {
        Fail "Could not enable TLS 1.2 on this host: $($_.Exception.Message)"
    }
}

function Get-Arch {
    # PROCESSOR_ARCHITEW6432 is set when a 32-bit host reports a 64-bit machine.
    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }
    switch ($arch) {
        "AMD64" { return "x64" }
        "ARM64" { return "arm64" }
        default { Fail "Unsupported processor architecture '$arch'. Seed Code ships x64 and arm64 Windows builds." }
    }
}

function Get-DefaultInstallDir {
    $base = $env:LOCALAPPDATA
    if (-not $base) { $base = [Environment]::GetFolderPath("LocalApplicationData") }
    if (-not $base) { Fail "Could not resolve %LOCALAPPDATA% for a per-user installation." }
    return (Join-Path $base "Programs\SeedCode")
}

# One SHA256SUMS.txt entry, as a regex:
#   ^<64 hex digits><one or more spaces/tabs>[*]<file name><optional trailing ws>$
# The separator is deliberately NOT assumed to be a single space: GNU
# sha256sum writes two spaces, other tools emit tabs, and a checksum file
# staged on Windows can arrive with CRLF line endings. '*' is the binary-mode
# marker some sha256sum builds write; it is not part of the file name.
$SumsEntryPattern = '^([0-9A-Fa-f]{64})[ \t]+[*]?(.+?)[ \t]*$'

function Get-RemoteText([string] $Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 60
        $content = $response.Content
        # GitHub serves release assets as application/octet-stream, and on
        # Windows PowerShell 5.1 Invoke-WebRequest then hands back a byte[]
        # instead of text. Decoding it here is what makes the checksum lookup
        # see real lines instead of a string of byte values.
        if ($content -is [byte[]]) {
            $content = [Text.Encoding]::UTF8.GetString($content)
        }
        return [string] $content
    } catch {
        return $null
    }
}

function Save-RemoteFile([string] $Url, [string] $Destination) {
    # -UseBasicParsing keeps this working on PS 5.1 without an IE engine.
    try {
        Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing -TimeoutSec 300
    } catch {
        Fail "Could not download $Url`n      $($_.Exception.Message)"
    }
    if (-not (Test-Path -LiteralPath $Destination)) { Fail "Download produced no file: $Url" }
}

function Get-ExpectedHash($SumsText, [string] $FileName) {
    # $SumsText is untyped on purpose: Windows PowerShell 5.1 hands back a
    # byte[] for a GitHub release asset (served as application/octet-stream),
    # and a [string] parameter would coerce it to "System.Byte[]" before the
    # check below could ever run.
    # Returns the expected lowercase SHA256 for $FileName, or $null when the
    # release does not list it. Parsing is whitespace-tolerant (spaces, tabs,
    # CRLF) and matches the file name exactly, case-insensitively; the hash
    # itself must still be 64 hex digits or the line is ignored.
    if (-not $SumsText) { return $null }
    if ($SumsText -is [byte[]]) {
        $SumsText = [Text.Encoding]::UTF8.GetString($SumsText)
    }
    $want = "$FileName".Trim()
    if (-not $want) { return $null }
    foreach ($line in ([string] $SumsText -split "\r?\n")) {
        $trimmed = $line.Trim()
        if (-not $trimmed) { continue }
        if ($trimmed.StartsWith("#")) { continue }  # comment/digest header lines
        $match = [regex]::Match($trimmed, $SumsEntryPattern)
        if (-not $match.Success) { continue }
        $listed = $match.Groups[2].Value.Trim()
        if ($listed -ieq $want) {
            return $match.Groups[1].Value.ToLowerInvariant()
        }
    }
    return $null
}

function Test-InstalledVersion([string] $ExePath, [string] $Expected) {
    if (-not (Test-Path -LiteralPath $ExePath)) { return $false }
    try {
        $reported = (& $ExePath --version 2>&1 | Out-String).Trim()
    } catch {
        return $false
    }
    return ($reported -like "*v$Expected*") -or ($reported -like "* $Expected*")
}

function Add-ToUserPath([string] $Directory) {
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $current) { $current = "" }
    $entries = @($current -split ";" | Where-Object { $_ -ne "" })
    foreach ($entry in $entries) {
        if ($entry.TrimEnd("\") -ieq $Directory.TrimEnd("\")) {
            Write-Step "PATH already contains $Directory"
            return
        }
    }
    $updated = (@($entries) + $Directory) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $updated, "User")
    # Make the current session usable immediately too.
    $env:Path = "$env:Path;$Directory"
    Write-Ok "Added to your user PATH: $Directory"
}

# --- main --------------------------------------------------------------------
$Arch        = Get-Arch
$AssetName   = "SeedCode-CLI-$Version-windows-$Arch.exe"
$AssetUrl    = "$ReleaseBase/$AssetName"
if (-not $InstallDir) { $InstallDir = Get-DefaultInstallDir }
$TargetExe   = Join-Path $InstallDir $ExeName

Write-Host ""
Write-Host "  Seed Code CLI installer" -ForegroundColor Green
Write-Host "  Version $Version  |  windows-$Arch  |  per-user install" -ForegroundColor DarkGray

Enable-Tls12
Write-Ok "PowerShell $($PSVersionTable.PSVersion)  |  TLS 1.2 enabled"

# Existing installation: upgrade in place, never a second copy.
$already = Test-InstalledVersion $TargetExe $Version
if ($already -and -not $Force) {
    Write-Ok "Seed Code CLI $Version is already installed at $TargetExe"
    Write-Host ""
    Write-Host "  Run:  seedcode" -ForegroundColor Gray
    Write-Host ""
    exit 0
}
if (Test-Path -LiteralPath $TargetExe) {
    Write-Step "Existing installation found - upgrading in place"
}

Write-Head "Downloading"
Write-Step $AssetUrl
$tempDir = Join-Path ([IO.Path]::GetTempPath()) ("seedcode-install-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
$tempExe = Join-Path $tempDir $AssetName

try {
    Save-RemoteFile $AssetUrl $tempExe
    Write-Ok "Downloaded $AssetName ($([math]::Round((Get-Item -LiteralPath $tempExe).Length / 1MB, 1)) MB)"

    # Verify the artifact before it ever reaches PATH.
    $sums = Get-RemoteText $SumsUrl
    $expected = Get-ExpectedHash $sums $AssetName
    if (-not $expected) {
        Fail "No SHA256 checksum for $AssetName in the release's SHA256SUMS.txt. Refusing to install an unverified binary."
    }
    $actual = (Get-FileHash -LiteralPath $tempExe -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        Fail "Checksum mismatch for $AssetName.`n      expected $expected`n      actual   $actual"
    }
    Write-Ok "SHA256 verified ($($actual.Substring(0, 12))...)"

    Write-Head "Installing"
    if (-not (Test-Path -LiteralPath $InstallDir)) {
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    }
    try {
        Copy-Item -LiteralPath $tempExe -Destination $TargetExe -Force
    } catch {
        Fail "Could not write $TargetExe. If Seed Code is running, close it and try again.`n      $($_.Exception.Message)"
    }
    Write-Ok "Installed to $TargetExe"
} finally {
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}

if (-not $NoPathUpdate) { Add-ToUserPath $InstallDir }

Write-Head "Verifying"
$reported = ""
try { $reported = (& $TargetExe --version 2>&1 | Out-String).Trim() } catch { $reported = "(failed to run)" }
if ($reported -notlike "*$Version*") {
    Fail "The installed binary did not report version $Version (it said: '$reported')."
}
Write-Ok "seedcode --version  ->  $reported"

$onPath = $false
try { $onPath = [bool](Get-Command seedcode -ErrorAction SilentlyContinue) } catch { $onPath = $false }

Write-Head "Done"
Write-Host "  Seed Code CLI $Version is installed." -ForegroundColor Green
Write-Host ""
if ($NoPathUpdate) {
    Write-Host "  Run:  & `"$TargetExe`"" -ForegroundColor Gray
} elseif ($onPath) {
    Write-Host "  Run:  seedcode" -ForegroundColor Gray
} else {
    Write-Host "  Open a NEW terminal, then run:  seedcode" -ForegroundColor Gray
    Write-Host "  (PATH changes only apply to new sessions)" -ForegroundColor DarkGray
}
Write-Host ""
