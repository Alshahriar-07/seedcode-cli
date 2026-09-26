#Requires -Version 5.1
<#
.SYNOPSIS
    Seed Code CLI v8.2.5 - official Windows remote installer.

.DESCRIPTION
    Installs Seed Code CLI for the CURRENT USER. No administrator rights, no
    Python, no package manager, and no cloned repository are required: the
    official standalone release binary is downloaded from GitHub Releases,
    its SHA256 is verified, and `seedcode` is added to your user PATH.

    Verification model (verification is NEVER bypassed):

      1. The release's SHA256SUMS.txt is the authoritative expected value.
      2. The installer also carries pinned digests, verified against the
         actual published v8.2.5 artifacts at release time.
      3. If the release file and the pinned digest both exist but disagree,
         the install aborts (possible tampering/partial publish).
      4. If SHA256SUMS.txt cannot be fetched at all, the pinned digest is
         used as the expected value and is reported as such.

    Usage (exactly as documented for remote install):

        irm https://seedcode-cli.vercel.app/install.ps1 | iex

    Or, to pass options, download-then-run:

        & ([scriptblock]::Create((irm https://seedcode-cli.vercel.app/install.ps1))) -Version 8.2.5

.PARAMETER Version
    Release version to install. Defaults to 8.2.5 (the current stable release).

.PARAMETER InstallDir
    Install directory. Defaults to %LOCALAPPDATA%\Programs\SeedCode.

.PARAMETER NoPathUpdate
    Install without modifying PATH. Use `seedcode` via the full path instead.

.PARAMETER Force
    Reinstall even when the requested version is already installed.

.PARAMETER Plain
    Disable the ANSI/Unicode UI (also happens automatically on unsupported
    terminals and when output is redirected).

.EXAMPLE
    irm https://seedcode-cli.vercel.app/install.ps1 | iex

.NOTES
    Requires PowerShell 5.1 or newer and an HTTPS-capable connection.
    This script never sees, stores, or prints an API key; credentials are set
    up inside the app on first run.
#>
[CmdletBinding()]
param(
    [string] $Version = "8.2.5",
    [string] $InstallDir = "",
    [switch] $NoPathUpdate,
    [switch] $Force,
    [switch] $Plain
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

# --- constants ---------------------------------------------------------------
$Repo        = "Alshahriar-07/seedcode-cli"
$ReleaseBase = "https://github.com/$Repo/releases/download/v$Version"
$SumsUrl     = "$ReleaseBase/SHA256SUMS.txt"
$ExeName     = "seedcode.exe"
$UserAgent   = "seedcode-cli-installer/$Version"

# Pinned SHA256 digests for the official v8.2.5 release artifacts. Each value
# is COMPUTED from the real published artifact (never invented): the release
# pipeline fills this map in after the artifacts are built. It is a cross-check
# against, and a fallback for, SHA256SUMS.txt - never a substitute that bypasses
# verification. When the release checksum file is unreachable AND nothing is
# pinned here, the install refuses rather than accepting an unverified binary.
$PinnedChecksums = @{
    "SeedCode-CLI-8.2.5-windows-x64.exe" = "8ae8b70282d96d81f55441bee5792c1614273a5fb9895582544f0871c10d95bc"
}

# --- terminal capabilities (ANSI / Unicode with graceful fallback) ------------
$UiRedirected = $true
try { $UiRedirected = [Console]::IsOutputRedirected } catch { }
$UiAnsi = $false
if (-not ($UiRedirected -or $Plain -or $env:NO_COLOR)) {
    $supportsVt = $false
    try { $supportsVt = [bool] $Host.UI.SupportsVirtualTerminal } catch { }
    $UiAnsi = $supportsVt -or ($env:WT_SESSION) -or ($PSVersionTable.PSVersion.Major -ge 7)
}
$UiUnicode = $false
if (-not ($Plain -or $env:NO_COLOR)) {
    $codepage = 0
    try { $codepage = [Console]::OutputEncoding.CodePage } catch { }
    $UiUnicode = ($codepage -eq 65001) -or ($env:WT_SESSION) -or ($PSVersionTable.PSVersion.Major -ge 7)
}

# PowerShell hash-literal values must be expressions, not statements, so the
# symbols are computed as assignments first and collected afterwards.
$symRule   = $(if ($UiUnicode) { [string][char]0x2500 } else { "-" })
$symTick   = $(if ($UiUnicode) { [string][char]0x2713 } else { "OK" })
$symNode   = $(if ($UiUnicode) { [string][char]0x25CF } else { "*" })
$symBranch = $(if ($UiUnicode) { [string][char]0x251C } else { "|" })
$symLast   = $(if ($UiUnicode) { [string][char]0x2514 } else { '`' })
$symDot    = $(if ($UiUnicode) { [string][char]0x2022 } else { "|" })
$symFull   = $(if ($UiUnicode) { [string][char]0x2588 } else { "#" })
$symEmpty  = $(if ($UiUnicode) { [string][char]0x2591 } else { "." })
$Sym = @{
    Rule = $symRule; Tick = $symTick; Node = $symNode; Branch = $symBranch
    Last = $symLast; Dot = $symDot; Full = $symFull; Empty = $symEmpty
}
$RuleText = ($Sym.Rule * 38)

$AnsiFg = @{ Gray = "90"; Green = "92"; Yellow = "93"; Red = "91"; Cyan = "96"; DarkGray = "37" }

function Write-Ui {
    param([string] $Text, [string] $Color = "Gray", [switch] $NoNewline)
    if ($UiRedirected) {
        if ($NoNewline) { [Console]::Write($Text) } else { [Console]::WriteLine($Text) }
    } elseif ($UiAnsi) {
        $code = if ($AnsiFg.ContainsKey($Color)) { $AnsiFg[$Color] } else { "0" }
        [Console]::Write("$([char]27)[$code" + "m$Text$([char]27)[0m")
        if (-not $NoNewline) { [Console]::WriteLine("") }
    } else {
        $mapped = @{ Gray = "Gray"; Green = "Green"; Yellow = "Yellow"; Red = "Red"; Cyan = "Cyan"; DarkGray = "DarkGray" }
        Write-Host $Text -ForegroundColor $mapped[$Color]
    }
}

function Write-Title([string] $Text) { Write-Ui "  $Text" "Green" }
function Write-Section([string] $Text) { Write-Ui "" ; Write-Ui "  $Text" "Cyan" }
function Write-Ok([string] $Text)      { Write-Ui "  $($Sym.Tick) $Text" "Green" }
function Write-Step([string] $Text)    { Write-Ui "  $($Sym.Branch) $Text" "Gray" }
function Write-Note([string] $Text)    { Write-Ui "  $Text" "DarkGray" }

function Fail([string] $Text) {
    try { [Console]::CursorVisible = $true } catch { }
    Write-Ui ""
    Write-Ui "  Installer error: $Text" "Red"
    Write-Ui ""
    Write-Ui "  Download manually from: https://github.com/$Repo/releases" "DarkGray"
    Write-Ui ""
    exit 1
}

function Draw-Progress {
    param([long] $Received, [long] $Total)
    if ($UiRedirected -or -not $UiAnsi) { return }
    $pct = if ($Total -gt 0) { [int](100 * $Received / $Total) } else { 0 }
    $width = 24
    $filled = if ($Total -gt 0) { [int]($width * $Received / $Total) } else { 0 }
    $bar = ($Sym.Full * $filled) + ($Sym.Empty * ($width - $filled))
    try { [Console]::CursorVisible = $false } catch { }
    Write-Ui ("  $($Sym.Branch) $bar  {0,3}%  ({1}/{2})" -f $pct, (Format-MB $Received), (Format-MB $Total)) -NoNewline
    Write-Ui "`r" "Gray" -NoNewline
}

function Format-MB([long] $Bytes) {
    if ($Bytes -ge 1MB) { return ("{0:N1} MB" -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ("{0:N0} KB" -f ($Bytes / 1KB)) }
    return "$Bytes B"
}

function Clear-ProgressLine {
    if ($UiRedirected -or -not $UiAnsi) { return }
    Write-Ui (" " * 100) -NoNewline
    Write-Ui "`r" -NoNewline
    try { [Console]::CursorVisible = $true } catch { }
}

# --- environment --------------------------------------------------------------
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

function Save-RemoteFileWithProgress([string] $Url, [string] $Destination, [string] $DisplayName) {
    # Real, byte-driven download progress (HTTP streaming - no fake animation,
    # no artificial delays). Falls back to a plain single-line download when
    # the terminal cannot render it (redirected output, no ANSI).
    $request = [Net.HttpWebRequest]::Create($Url)
    $request.UserAgent = $UserAgent
    $request.Timeout = 60000
    $request.ReadWriteTimeout = 300000
    $request.AllowAutoRedirect = $true

    try {
        $response = $request.GetResponse()
    } catch {
        Fail "Could not download $Url`n      $($_.Exception.Message)"
    }

    try {
        $stream = $response.GetResponseStream()
        $output = [IO.File]::Open($Destination, [IO.FileMode]::Create, [IO.FileAccess]::Write)
        try {
            $buffer = New-Object byte[] 262144
            $received = [long] 0
            $total = $response.ContentLength
            $lastPct = -1
            while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $output.Write($buffer, 0, $read)
                $received += $read
                if (($total -gt 0) -and ($UiAnsi -and -not $UiRedirected)) {
                    $pct = [int](100 * $received / $total)
                    if ($pct -ne $lastPct) {
                        $lastPct = $pct
                        Draw-Progress $received $total
                    }
                }
            }
        } finally {
            $stream.Dispose()
            $output.Dispose()
        }
    } finally {
        $response.Close()
    }
    Clear-ProgressLine
    if (-not (Test-Path -LiteralPath $Destination)) { Fail "Download produced no file: $Url" }
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
            Write-Ok "PATH already contains $Directory"
            return
        }
    }
    $updated = (@($entries) + $Directory) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $updated, "User")
    # Make the current session usable immediately too.
    $env:Path = "$env:Path;$Directory"
    Write-Ok "PATH configured: $Directory (new terminals)"
}

# --- main --------------------------------------------------------------------
$Arch        = Get-Arch
$AssetName   = "SeedCode-CLI-$Version-windows-$Arch.exe"
$AssetUrl    = "$ReleaseBase/$AssetName"
if (-not $InstallDir) { $InstallDir = Get-DefaultInstallDir }
$TargetExe   = Join-Path $InstallDir $ExeName

Write-Ui ""
Write-Title "Seed Code CLI"
Write-Ui "  $RuleText" "DarkGray"
Write-Ui "  Version $Version  $($Sym.Dot)  Windows $Arch" "Gray"
Write-Ui "  Per-user installation" "DarkGray"

Write-Section "$($Sym.Node) Checking system"
Write-Ok "PowerShell $($PSVersionTable.PSVersion) detected"
Enable-Tls12
Write-Ok "TLS 1.2 enabled"

# Existing installation: upgrade in place, never a second copy.
$already = Test-InstalledVersion $TargetExe $Version
if ($already -and -not $Force) {
    Write-Section "$($Sym.Tick) Already installed"
    Write-Ok "Seed Code CLI $Version is already installed at $TargetExe"
    Write-Ui ""
    Write-Ui "  $RuleText" "DarkGray"
    Write-Ui "  $($Sym.Tick) Nothing to do" "Green"
    Write-Ui ""
    Write-Ui "  Run:" "Gray"
    Write-Ui "      seedcode" "Gray"
    Write-Ui ""
    exit 0
}
if (Test-Path -LiteralPath $TargetExe) {
    Write-Ok "Existing installation found - upgrading in place"
}

Write-Section "Downloading"
Write-Step $AssetName
$tempDir = Join-Path ([IO.Path]::GetTempPath()) ("seedcode-install-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
$tempExe = Join-Path $tempDir $AssetName

try {
    Save-RemoteFileWithProgress $AssetUrl $tempExe $AssetName
    $sizeMb = [math]::Round((Get-Item -LiteralPath $tempExe).Length / 1MB, 1)
    Write-Ui "  $($Sym.Last) Download complete ($sizeMb MB)" "Gray"

    Write-Section "Verifying"
    # 1) The release's SHA256SUMS.txt is authoritative. 2) The pinned digest
    # (verified against the published artifact) cross-checks it, and acts as
    # the expected value only when the release file is unreachable. Mismatch
    # = abort; missing = abort. Verification is never bypassed.
    $sums = Get-RemoteText $SumsUrl
    $expected = Get-ExpectedHash $sums $AssetName
    $pinned = $null
    if ($PinnedChecksums.ContainsKey($AssetName)) { $pinned = $PinnedChecksums[$AssetName].ToLowerInvariant() }

    if ($expected -and $pinned -and ($expected -cne $pinned)) {
        Fail "The release's SHA256SUMS.txt and this installer's pinned checksum disagree for $AssetName. Aborting rather than installing a possibly tampered binary."
    }
    if (-not $expected) {
        if ($pinned) {
            $expected = $pinned
            Write-Note "  (release SHA256SUMS.txt unreachable - using the installer's pinned checksum)"
        } else {
            Fail "No SHA256 checksum for $AssetName in the release's SHA256SUMS.txt. Refusing to install an unverified binary."
        }
    }
    $actual = (Get-FileHash -LiteralPath $tempExe -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        Fail "Checksum mismatch for $AssetName.`n      expected $expected`n      actual   $actual"
    }
    Write-Ok "SHA-256 checksum verified ($($actual.Substring(0, 12))...)"

    Write-Section "Installing"
    if (-not (Test-Path -LiteralPath $InstallDir)) {
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    }
    try {
        Copy-Item -LiteralPath $tempExe -Destination $TargetExe -Force
    } catch {
        Fail "Could not write $TargetExe. If Seed Code is running, close it and try again.`n      $($_.Exception.Message)"
    }
    Write-Ok "Seed Code CLI installed"
} finally {
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}

if (-not $NoPathUpdate) { Add-ToUserPath $InstallDir }

Write-Section "Verifying"
# Verify the file THIS run installed, by absolute path - a `seedcode` already
# on PATH could be an older copy, and a stale binary must never be reported as
# this install's success.
$reported = ""
try { $reported = (& $TargetExe --version 2>&1 | Out-String).Trim() } catch { $reported = "(failed to run)" }
if ($reported -notlike "*$Version*") {
    Fail "The installed binary did not report version $Version (it said: '$reported')."
}
Write-Ok "seedcode --version  ->  $reported"

$onPath = $false
$resolvedTo = ""
try {
    $found = Get-Command seedcode -ErrorAction SilentlyContinue
    if ($found) {
        $onPath = $true
        $resolvedTo = [string] $found.Source
    }
} catch {
    $onPath = $false
}

# A different `seedcode` earlier on PATH keeps being invoked instead of the one
# just installed (the "old executable still runs" failure mode). Say so out
# loud instead of letting the user believe the upgrade took effect.
if ($onPath -and $resolvedTo -and -not ($resolvedTo -like "$InstallDir\*")) {
    Write-Ui ""
    Write-Ui "  ! Another seedcode is earlier on PATH:" "Yellow"
    Write-Ui "      $resolvedTo" "DarkGray"
    Write-Ui "      (this install: $TargetExe)" "DarkGray"
    Write-Ui "    Remove the older copy, or run this one directly:" "DarkGray"
    Write-Ui "      & `"$TargetExe`" --version" "DarkGray"
}

Write-Ui ""
Write-Ui "  $RuleText" "DarkGray"
Write-Ui "  $($Sym.Tick) Installation complete" "Green"
Write-Ui ""
if ($NoPathUpdate) {
    Write-Ui "  Run:" "Gray"
    Write-Ui "      & `"$TargetExe`"" "Gray"
} elseif ($onPath) {
    Write-Ui "  Run:" "Gray"
    Write-Ui "      seedcode" "Gray"
} else {
    Write-Ui "  Restart your terminal, then run:" "Gray"
    Write-Ui "      seedcode" "Gray"
    Write-Ui "  (PATH changes only apply to new sessions)" "DarkGray"
}
Write-Ui "  License: PolyForm Noncommercial License 1.0.0" "DarkGray"
Write-Ui ""
