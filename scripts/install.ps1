# Qubit Agent Windows installer.
#
# Windows twin of scripts/install.sh: it bootstraps the Qubit runtime from a
# source checkout (or clones the Qubit repository when fetched standalone) and
# keeps every path under the Qubit-owned root:
#
#   QUBIT_HOME  = %LOCALAPPDATA%\qubit by default
#   data/config/state/cache/bin/venv all live below that root, and a fresh
#   VoiceBridge tree is initialized at <QUBIT_HOME>\voice\{config,data,...}
#   (an existing XDG-style qubit-voicebridge tree is preserved read-old).
#
# It never clones, installs, or points at the upstream Hermes/OpenClaw tree.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Help
#   powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -DryRun
#   powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
#
# Fetched form:
#   iex (irm https://github.com/cgraph-dev/Qubit/raw/production/scripts/install.ps1)
#
# Compatibility aliases are kept for wrappers written against older spellings:
#   -WithoutModels / -SkipModels     = -NoModels
#   -WithoutDashboard / -SkipDashboard = -NoDashboard
#   -SkipSetup                       = -NonInteractive
#   -Branch                          = -GitBranch
#   -WhatIf                          = -DryRun
#
# This file is fetched as a string by `irm | iex`, so it MUST NOT start with a
# UTF-8 BOM (Windows PowerShell 5.1 keeps the BOM as a literal U+FEFF and then
# refuses to parse `param(`). Keep the file ASCII-only for the same reason.

[CmdletBinding(PositionalBinding = $false)]
param(
    [Alias('Home', 'HermesHome')]
    [string]$QubitHome = $(if ($env:QUBIT_HOME) {
            $env:QUBIT_HOME
        } elseif ($env:LOCALAPPDATA) {
            Join-Path $env:LOCALAPPDATA 'qubit'
        } else {
            Join-Path $HOME '.qubit'
        }),

    # stable: the latest published release tag; main: the main branch tip.
    # Only used when this installer has to clone the Qubit repository.
    [string]$Channel = $(if ($env:QUBIT_INSTALL_CHANNEL) { $env:QUBIT_INSTALL_CHANNEL } else { 'stable' }),

    [Alias('Branch')]
    [string]$GitBranch = 'main',

    [Alias('WhatIf')]
    [switch]$DryRun,

    [Alias('h', '?')]
    [switch]$Help,

    [Alias('WithoutModels', 'SkipModels')]
    [switch]$NoModels,

    [Alias('WithoutDashboard', 'SkipDashboard')]
    [switch]$NoDashboard,

    [Alias('SkipSetup')]
    [switch]$NonInteractive,

    # Explicit source checkout. When empty the installer uses the checkout this
    # script lives in, or clones into <QubitHome>\qubit when fetched standalone.
    [string]$InstallDir = '',

    # Qubit-owned source only. Override with QUBIT_REPO_URL for a mirror/fork.
    [string]$RepoUrl = $(if ($env:QUBIT_REPO_URL) { $env:QUBIT_REPO_URL } else { 'https://github.com/cgraph-dev/Qubit.git' }),

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Remaining = @()
)

$ErrorActionPreference = 'Stop'

# --- Dot-source guard -------------------------------------------------------
# Tests and embedding hosts dot-source this file to reach its functions. Only
# definitions must load; the installer itself must not run.
$script:IsDotSourced = $MyInvocation.InvocationName -eq '.'
# A scriptblock literal parsed from a file records that file; iex'd text does
# not. Only a real file may `exit` (an iex'd exit would close the user's shell).
$script:RunAsFile = [bool]{}.File
$script:BoundParams = $PSBoundParameters
$script:Layout = $null
$script:DashboardState = 'present'

# Model assets are pinned exactly as scripts/install.sh pins them. A mismatch
# (including a truncated download) is a typed exit-9 failure, never a silent
# install of unverified bytes.
$script:VoiceSha256 = 'b3a6e47b57b8c7fbe6a0ce2518161a50f59a9cdd8a50835c02cb02bdd6206c18'
$script:VoiceJsonSha256 = '95a23eb4d42909d38df73bb9ac7f45f597dbfcde2d1bf9526fdeaf5466977d77'
$script:MoonSha256 = '43232c1d13013d37317163baec3135bd771a186a4356f28c889bab453bb0e891'
$script:ParakeetSha256 = '5793d0fd397c5778d2cf2126994d58e9d56b1be7c04d13c7a15bb1b4eafb16bf'
$script:SileroV5Sha256 = '6b99cbfd39246b6706f98ec13c7c50c6b299181f2474fa05cbc8046acc274396'
$script:SileroV6Sha256 = '1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3'
$script:Eres2netSha256 = 'c59158379255ad66e161679cca6af8d52d51e389e3224ab7d7a7baae295c2db5'
$script:SherpaRelease = 'https://github.com/k2-fsa/sherpa-onnx/releases/download'

# --- BEGIN GENERATED: bootstrap pins (scripts/gen-bootstrap-pins.py) ---
# Derived from pm/lock.json. DO NOT EDIT BY HAND:
# run scripts/gen-bootstrap-pins.py after a pin bump.
$script:UvPinVersion = "0.13.5"
$script:UvPinFiles = @{
    "win32-x64" = @{
        Url    = "https://github.com/astral-sh/uv/releases/download/0.13.5/uv-x86_64-pc-windows-msvc.zip"
        MirrorUrl = "https://hermes-assets.nousresearch.com/upstream/sha256/a38b8d1902b7cfbc76b423ba1bb456f623f42064cdc72628b6075b89afd1019a"
        Sha256 = "a38b8d1902b7cfbc76b423ba1bb456f623f42064cdc72628b6075b89afd1019a"
    }
    "win32-arm64" = @{
        Url    = "https://github.com/astral-sh/uv/releases/download/0.13.5/uv-aarch64-pc-windows-msvc.zip"
        MirrorUrl = "https://hermes-assets.nousresearch.com/upstream/sha256/09f47edf90726084a70f9426f6e3ca4f46e9bf7cbf23b1716ec64d60b055fae3"
        Sha256 = "09f47edf90726084a70f9426f6e3ca4f46e9bf7cbf23b1716ec64d60b055fae3"
    }
}

$script:GitPinVersion = "2.53.0+3"
$script:GitPinFiles = @{
    "win32-x64" = @{
        Url    = "https://github.com/git-for-windows/git/releases/download/v2.53.0.windows.3/PortableGit-2.53.0.3-64-bit.7z.exe"
        MirrorUrl = "https://hermes-assets.nousresearch.com/upstream/sha256/b365da794b1d2225eb24d5f5e09ef7792cfd5fa26c3a3586210280c80dff3a2a"
        Sha256 = "b365da794b1d2225eb24d5f5e09ef7792cfd5fa26c3a3586210280c80dff3a2a"
    }
    "win32-arm64" = @{
        Url    = "https://github.com/git-for-windows/git/releases/download/v2.53.0.windows.3/PortableGit-2.53.0.3-arm64.7z.exe"
        MirrorUrl = "https://hermes-assets.nousresearch.com/upstream/sha256/0db54010054c01f35501cf69e1e32d3710138ecb934d188bd77093afed24300e"
        Sha256 = "0db54010054c01f35501cf69e1e32d3710138ecb934d188bd77093afed24300e"
    }
}
# --- END GENERATED: bootstrap pins ---

function Write-Usage {
    @"
Qubit Agent installer (Windows)

Usage:
  powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 [options]

Options:
  -Help                     show this help without changing the machine
  -DryRun                   run preflight and print the resolved layout; make no changes
  -QubitHome DIR            Qubit-owned root for data/config/state/bin/venv
                            and the voice tree (default: %LOCALAPPDATA%\qubit,
                            or QUBIT_HOME); also drives -InstallDir
  -Channel stable|main      source channel when this installer must clone:
                            stable = latest published release (default),
                            main   = main branch tip
  -NoModels                 skip model asset downloads and ollama provisioning
  -NoDashboard              skip the dashboard bundle check/build (headless)
  -NonInteractive           do not prompt (compatibility alias: -SkipSetup)
  -InstallDir DIR           use this checkout instead of the one beside the script
  -RepoUrl URL              Qubit-owned source URL (default: the Qubit repository)

Environment:
  QUBIT_HOME                same as -QubitHome when the flag is absent
  QUBIT_INSTALL_CHANNEL     same as -Channel when the flag is absent
  QUBIT_REPO_URL            same as -RepoUrl when the flag is absent

Requires: Windows PowerShell 5.1+. uv and git are provisioned from Qubit's
pinned, sha256-verified toolchain when the host has none. Optional: node/npm +
bash (dashboard build), ollama (local brain), tar (model archives).

The canonical launcher is qubit; voicebridge remains a compatibility alias.
"@
}

function Fail-Typed {
    param(
        [int]$Code,
        [string]$Token,
        [string]$Message
    )
    $errorRecord = New-Object System.Exception("${Token}: $Message")
    $errorRecord.Data['QubitExitCode'] = $Code
    throw $errorRecord
}

function Fail-Prerequisite {
    param([string]$Class, [string]$Message)
    Fail-Typed 1 "QUBIT_INSTALL_PREREQUISITE_$Class" $Message
}

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    Write-Host "[!] $Message" -ForegroundColor Yellow
}

function Write-Fail([string]$Message) {
    [Console]::Error.WriteLine("[X] $Message")
}

function Convert-ToFullPath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $Path }
    $expanded = [Environment]::ExpandEnvironmentVariables($Path)
    if ($expanded -eq '~') { $expanded = $HOME }
    elseif ($expanded.StartsWith('~/') -or $expanded.StartsWith('~\')) {
        $expanded = Join-Path $HOME $expanded.Substring(2)
    }
    return [IO.Path]::GetFullPath($expanded)
}

function Resolve-VoiceLayout {
    param([string]$HomePath)
    # Mirrors voicebridge/paths.py. A fresh install gets $QUBIT_HOME\voice;
    # a checkout that still carries the previous Windows XDG-style tree is
    # preserved read-old (never migrated, copied, or deleted).
    $homePath = $HomePath
    $voiceRoot = Join-Path $homePath 'voice'
    $new = [pscustomobject]@{
        Root   = $voiceRoot
        Config = Join-Path $voiceRoot 'config'
        Data   = Join-Path $voiceRoot 'data'
        State  = Join-Path $voiceRoot 'state'
        Cache  = Join-Path $voiceRoot 'cache'
        Models = Join-Path $voiceRoot 'models'
    }

    $bases = [ordered]@{
        Config = (Join-Path $HomePath 'config')
        Data   = (Join-Path $HomePath 'data')
        State  = (Join-Path $HomePath 'state')
        Cache  = (Join-Path $HomePath 'cache')
    }
    foreach ($entry in @(
            @('Config', 'XDG_CONFIG_HOME'),
            @('Data', 'XDG_DATA_HOME'),
            @('State', 'XDG_STATE_HOME'),
            @('Cache', 'XDG_CACHE_HOME'))) {
        $value = [Environment]::GetEnvironmentVariable($entry[1])
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            $bases[$entry[0]] = $value
        }
    }
    $legacyName = 'qubit-voicebridge'
    $legacy = [pscustomobject]@{
        Root   = (Join-Path $bases.Config $legacyName)
        Config = (Join-Path $bases.Config $legacyName)
        Data   = (Join-Path $bases.Data $legacyName)
        State  = (Join-Path $bases.State $legacyName)
        Cache  = (Join-Path $bases.Cache $legacyName)
        Models = (Join-Path (Join-Path $bases.Data $legacyName) 'models')
    }

    $newReady = Test-Path -LiteralPath $voiceRoot
    foreach ($path in @($new.Config, $new.Data, $new.State, $new.Cache, $new.Models)) {
        if ($newReady) { break }
        $newReady = Test-Path -LiteralPath $path
    }
    $legacyReady = $false
    foreach ($path in @($legacy.Config, $legacy.Data, $legacy.State, $legacy.Cache)) {
        if ($legacyReady) { break }
        $legacyReady = Test-Path -LiteralPath $path
    }

    if ($legacyReady -and -not $newReady) {
        $mode = 'read-old'
        $chosen = $legacy
        $xdg = @{
            Config = $bases.Config
            Data   = $bases.Data
            State  = $bases.State
            Cache  = $bases.Cache
        }
    } else {
        $mode = 'new'
        $chosen = $new
        $xdg = @{
            Config = $new.Config
            Data   = $new.Data
            State  = $new.State
            Cache  = $new.Cache
        }
    }
    return [pscustomobject]@{
        Mode      = $mode
        Root      = $chosen.Root
        Config    = $chosen.Config
        Data      = $chosen.Data
        State     = $chosen.State
        Cache     = $chosen.Cache
        Models    = $chosen.Models
        XdgConfig = $xdg.Config
        XdgData   = $xdg.Data
        XdgState  = $xdg.State
        XdgCache  = $xdg.Cache
    }
}

function Resolve-Layout {
    $homePath = Convert-ToFullPath $QubitHome
    # -QubitHome drives InstallDir unless the caller explicitly names a
    # checkout with -InstallDir.
    $installPath = $InstallDir
    if ([string]::IsNullOrWhiteSpace($installPath)) {
        $installPath = Join-Path $homePath 'qubit'
    } else {
        $installPath = Convert-ToFullPath $installPath
    }
    $bin = Join-Path $homePath 'bin'
    $venv = Join-Path $homePath 'venv'
    $voice = Resolve-VoiceLayout $homePath
    $script:Layout = [pscustomobject]@{
        QubitHome   = $homePath
        InstallDir  = $installPath
        SourceDir   = ''
        DataDir     = $voice.Data
        ConfigDir   = $voice.Config
        StateDir    = $voice.State
        CacheDir    = $voice.Cache
        BinDir      = $bin
        VenvDir     = $venv
        ModelsDir   = $voice.Models
        VoiceDir    = $voice.Config
        VoiceRoot   = $voice.Root
        VoiceLayout = $voice.Mode
        XdgConfig   = $voice.XdgConfig
        XdgData     = $voice.XdgData
        XdgState    = $voice.XdgState
        XdgCache    = $voice.XdgCache
    }
    return $script:Layout
}

function Test-Checkout {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    return (Test-Path -LiteralPath (Join-Path $Path 'pyproject.toml') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $Path 'qubit_runtime') -PathType Container)
}

function Get-ProjectDir {
    # Empty under `irm | iex`; the standalone path clones into QUBIT_HOME.
    if ([string]::IsNullOrWhiteSpace($PSScriptRoot)) { return '' }
    return (Split-Path -Parent $PSScriptRoot)
}

function Assert-QubitSource {
    param([string]$Url)
    if ([string]::IsNullOrWhiteSpace($Url)) {
        Fail-Prerequisite 'REPO_URL' 'the source URL is empty; pass -RepoUrl or set QUBIT_REPO_URL'
    }
    if ($Url -match '(?i)hermes|openclaw') {
        Fail-Typed 2 'QUBIT_INSTALL_SOURCE_REFUSED' "refusing a non-Qubit source: $Url (this installer never clones the upstream Hermes/OpenClaw tree)"
    }
}

function Get-CommandPath {
    param([string]$Name)
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Get-WindowsArch {
    # The MACHINE's architecture, not the interpreter's: an x64 PowerShell on
    # Windows-on-ARM must stage the arm64 uv/git artifacts.
    $machineArch = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment' -ErrorAction SilentlyContinue).PROCESSOR_ARCHITECTURE
    if ($machineArch -eq 'ARM64') { return 'arm64' }
    return 'x64'
}

function Get-ToolStoreRoot {
    # Same resolution as pm's store_root(), so the pinned uv/git staged here is
    # the exact slot pm later adopts instead of downloading a second copy.
    if ($env:HERMES_RUNTIME_DIR) { return $env:HERMES_RUNTIME_DIR }
    return (Join-Path $script:Layout.QubitHome 'tools')
}

function Test-UvRuns {
    param([string]$Path)
    $global:LASTEXITCODE = 0
    try { $out = & $Path --version 2>$null } catch { return $false }
    if ($LASTEXITCODE -or -not $out) { return $false }
    $have = ("$out".Trim() -split '\s+')[1] -replace '[^0-9.].*$', ''
    try { return ([version]$have -ge [version]$script:UvPinVersion) } catch { return $false }
}

function Get-Uv {
    # A usable uv already on PATH is kept (a managed host may pin its own);
    # otherwise stage Qubit's own pinned uv, exactly as the vendored runtime
    # installer does. Never astral-latest, never a version this build has not
    # sha256-verified.
    $onPath = Get-CommandPath 'uv'
    if ($onPath -and (Test-UvRuns $onPath)) { return $onPath }
    $target = "win32-$(Get-WindowsArch)"
    $pin = $script:UvPinFiles[$target]
    if (-not $pin) {
        Fail-Prerequisite 'UV' "uv is required and no pinned uv artifact exists for $target"
    }
    $entry = Join-Path (Get-ToolStoreRoot) "uv-$($script:UvPinVersion)-$target"
    $uvExe = Join-Path $entry 'uv.exe'
    if (Test-Path -LiteralPath $uvExe) {
        if (Test-UvRuns $uvExe) { return $uvExe }
        Write-Warn 'cached pinned uv does not run; staging a fresh copy'
        Remove-Item -LiteralPath $uvExe -Force -ErrorAction SilentlyContinue
    }
    if ($DryRun) { return $uvExe }
    Write-Step "staging pinned uv $($script:UvPinVersion) ($target)"
    $tmpDir = Join-Path ([IO.Path]::GetTempPath()) "qubit-uv-bootstrap-$PID"
    try {
        New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
        $zipPath = Join-Path $tmpDir 'uv.zip'
        Invoke-VerifiedDownload -Url $pin.Url -MirrorUrl $pin.MirrorUrl -Sha256 $pin.Sha256 -OutFile $zipPath
        $extractDir = Join-Path $tmpDir 'unpacked'
        Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
        $found = Get-ChildItem -Path $extractDir -Filter 'uv.exe' -Recurse | Select-Object -First 1
        if (-not $found) { Fail-Prerequisite 'UV' 'uv.exe was not found in the pinned archive' }
        New-Item -ItemType Directory -Force -Path $entry | Out-Null
        Move-Item -LiteralPath $found.FullName -Destination $uvExe -Force
        $uvx = Get-ChildItem -Path $extractDir -Filter 'uvx.exe' -Recurse | Select-Object -First 1
        if ($uvx) { Move-Item -LiteralPath $uvx.FullName -Destination (Join-Path $entry 'uvx.exe') -Force }
    } finally {
        Remove-Item -LiteralPath $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (-not (Test-UvRuns $uvExe)) {
        Fail-Prerequisite 'UV' 'the pinned uv was staged but does not run on this host'
    }
    return $uvExe
}

function Get-PinnedGit {
    # Returns the pinned git.exe for this host, or $null when no pin exists.
    $target = "win32-$(Get-WindowsArch)"
    $pin = $script:GitPinFiles[$target]
    if (-not $pin) { return $null }
    $entry = Join-Path (Get-ToolStoreRoot) "git-$($script:GitPinVersion)-$target"
    $gitExe = Join-Path $entry 'cmd\git.exe'
    if (Test-Path -LiteralPath $gitExe) { return $gitExe }
    if ($DryRun) { return $gitExe }
    Write-Step "staging pinned git $($script:GitPinVersion) ($target)"
    $tmpDir = Join-Path ([IO.Path]::GetTempPath()) "qubit-git-bootstrap-$PID"
    try {
        New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
        $sfxPath = Join-Path $tmpDir 'portable-git.7z.exe'
        Invoke-VerifiedDownload -Url $pin.Url -MirrorUrl $pin.MirrorUrl -Sha256 $pin.Sha256 -OutFile $sfxPath
        $extractDir = Join-Path $tmpDir 'unpacked'
        New-Item -ItemType Directory -Force -Path $extractDir | Out-Null
        Unblock-File -Path $sfxPath -ErrorAction SilentlyContinue
        # PortableGit's self-extracting 7z carries its own extractor (stock
        # Windows tar.exe has no bzip2) and is GUI-subsystem, so `&` would not
        # wait for it. Bound the wait and kill the whole tree like pm does.
        $sfx = [System.Diagnostics.Process]::Start($sfxPath, "-o`"$extractDir`" -y")
        if (-not $sfx.WaitForExit(600000)) {
            try { & taskkill.exe /T /F /PID $sfx.Id 2>&1 | Out-Null } catch { }
            $sfx.WaitForExit()
            Fail-Prerequisite 'GIT' 'the pinned git extractor timed out after 600s'
        }
        if ($sfx.ExitCode) {
            Fail-Prerequisite 'GIT' "the pinned git extractor exited $($sfx.ExitCode)"
        }
        if (-not (Test-Path -LiteralPath (Join-Path $extractDir 'cmd\git.exe'))) {
            Fail-Prerequisite 'GIT' 'git.exe was not found in the pinned archive'
        }
        if (Test-Path -LiteralPath $entry) { Remove-Item -LiteralPath $entry -Recurse -Force }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $entry) | Out-Null
        Move-Item -LiteralPath $extractDir -Destination $entry
    } finally {
        Remove-Item -LiteralPath $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    return $gitExe
}

function Ensure-Git {
    # A usable git already on PATH is kept; otherwise stage Qubit's pinned
    # PortableGit so a clean machine (no git.exe) can clone the product.
    $onPath = Get-CommandPath 'git'
    if ($onPath) { return $onPath }
    $g = Get-PinnedGit
    if (-not $g) {
        Fail-Prerequisite 'GIT' 'git is required to bootstrap the Qubit runtime and no pinned Git artifact exists for this host'
    }
    $gitEntry = Split-Path (Split-Path $g -Parent) -Parent
    $env:Path = "$gitEntry\cmd;$gitEntry\usr\bin;$env:Path"
    return $g
}

function Test-Prerequisite {
    if ($env:OS -ne 'Windows_NT') {
        Fail-Prerequisite 'UNSUPPORTED_PLATFORM' 'this installer is for Windows; use scripts/install.sh on Linux/macOS'
    }
    if ($PSVersionTable.PSVersion -lt [version]'5.1') {
        Fail-Prerequisite 'POWERSHELL' "Windows PowerShell 5.1 or newer is required (found $($PSVersionTable.PSVersion))"
    }
    # uv/git are provisioned from the pinned toolchain, so their absence from
    # PATH is not a prerequisite failure. The typed UV class still fires when
    # this host has no pinned artifact at all (an unsupported architecture).
    if (-not (Get-CommandPath 'uv') -and -not $script:UvPinFiles["win32-$(Get-WindowsArch)"]) {
        Fail-Prerequisite 'UV' "uv is required and no pinned uv artifact exists for win32-$(Get-WindowsArch)"
    }
    if (-not $NoModels -and -not (Get-CommandPath 'tar')) {
        Fail-Prerequisite 'TAR' 'tar is required to extract model archives (skip with -NoModels)'
    }
}

function Get-LatestStableTag {
    param([string]$Origin)
    $git = Get-CommandPath 'git'
    if (-not $git) { return $null }
    $output = & $git ls-remote --tags --refs $Origin 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    $versions = @()
    foreach ($line in @($output)) {
        if (-not $line) { continue }
        $parts = $line -split '\s+'
        if ($parts.Count -lt 2) { continue }
        $tag = $parts[1] -replace '^refs/tags/', ''
        $match = [regex]::Match($tag, '^v?(\d+)\.(\d+)\.(\d+)$')
        if (-not $match.Success) { continue }
        $versions += [pscustomobject]@{
            Tag   = $tag
            Major = [int]$match.Groups[1].Value
            Minor = [int]$match.Groups[2].Value
            Patch = [int]$match.Groups[3].Value
        }
    }
    if ($versions.Count -eq 0) { return $null }
    $best = $versions | Sort-Object Major, Minor, Patch | Select-Object -Last 1
    return $best.Tag
}

function Test-ReleaseTag {
    param([string]$Ref)
    return [regex]::IsMatch($Ref, '^v?\d+\.\d+\.\d+$')
}

function Initialize-Checkout {
    param([string]$Target)
    # Defence in depth: a dot-sourced embedding host reaches this directly,
    # not only through Invoke-QubitInstall's assertion.
    Assert-QubitSource $RepoUrl
    if (Test-Checkout $Target) { return $Target }
    $git = Ensure-Git
    # An interrupted clone leaves a .git with no initial commit, where
    # fetch/checkout abort ("You do not have the initial commit yet"). Move it
    # aside -- never delete it: it may hold something the user wants.
    if (Test-Path -LiteralPath (Join-Path $Target '.git') -PathType Container) {
        & $git -C $Target rev-parse --verify HEAD 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $broken = "$Target.broken-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            Write-Warn "$Target has no commits (interrupted clone); moving it aside to $broken"
            Move-Item -LiteralPath $Target -Destination $broken
        }
    }
    $ref = $GitBranch
    $isRelease = $false
    if ($Channel -eq 'stable') {
        $tag = Get-LatestStableTag $RepoUrl
        if (-not $tag) {
            Fail-Typed 1 'QUBIT_INSTALL_RELEASE_UNAVAILABLE' "no stable release tag could be resolved from $RepoUrl; rerun with -Channel main"
        }
        $ref = $tag
        $isRelease = $true
        Write-Step "Installing the latest stable release ($ref)"
    } else {
        Write-Step "Installing from branch $ref"
    }
    if (Test-Path -LiteralPath (Join-Path $Target '.git') -PathType Container) {
        Write-Step "Updating $Target"
        & $git -C $Target fetch --tags origin
        if ($LASTEXITCODE -ne 0) {
            Fail-Typed 1 'QUBIT_INSTALL_GIT_FETCH_FAILED' "git fetch failed in $Target"
        }
        if ($isRelease) {
            # A rerun must never move a checkout backwards past the release it
            # already contains, and a tag checkout is detached: `git pull`
            # there would fail ("not currently on a branch").
            & $git -C $Target merge-base --is-ancestor $ref HEAD 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Step "Already at or past $ref; keeping $Target"
                return $Target
            }
        }
        & $git -C $Target checkout $ref
        if ($LASTEXITCODE -ne 0) {
            Fail-Typed 1 'QUBIT_INSTALL_GIT_CHECKOUT_FAILED' "git checkout $ref failed in $Target"
        }
        if (-not $isRelease) {
            & $git -C $Target pull --ff-only
            if ($LASTEXITCODE -ne 0) {
                Fail-Typed 1 'QUBIT_INSTALL_GIT_FETCH_FAILED' "could not fast-forward $Target to $ref"
            }
        }
        return $Target
    }
    if (Test-Path -LiteralPath $Target) {
        $item = Get-Item -LiteralPath $Target -Force
        $empty = $item.PSIsContainer -and -not (Get-ChildItem -LiteralPath $Target -Force | Select-Object -First 1)
        if (-not $empty) {
            Fail-Typed 1 'QUBIT_INSTALL_DIR_NOT_CHECKOUT' "$Target exists and is not a Qubit checkout; move it aside or pass -InstallDir"
        }
    }
    $parent = Split-Path -Parent $Target
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    Write-Step "Cloning $RepoUrl ($ref) into $Target"
    & $git clone --filter=tree:0 --branch $ref $RepoUrl $Target
    if ($LASTEXITCODE -ne 0) {
        Fail-Typed 1 'QUBIT_INSTALL_GIT_CLONE_FAILED' "git clone failed; no checkout was published"
    }
    return $Target
}

function Resolve-Source {
    param([string]$ProjectDir)
    if (-not [string]::IsNullOrWhiteSpace($InstallDir)) {
        return (Initialize-Checkout (Convert-ToFullPath $InstallDir))
    }
    if (Test-Checkout $ProjectDir) { return $ProjectDir }
    return (Initialize-Checkout $script:Layout.InstallDir)
}

function Invoke-VerifiedDownload {
    param(
        [string]$Url,
        [string]$Sha256,
        [string]$OutFile,
        # Optional pinned mirror (the vendored runtime installer's upstream
        # asset host) tried only when the primary URL fails to download.
        [string]$MirrorUrl = ''
    )
    if (Test-Path -LiteralPath $OutFile -PathType Leaf) {
        $existing = (Get-FileHash -LiteralPath $OutFile -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($existing -eq $Sha256.ToLowerInvariant()) {
            Write-Host "    have   $(Split-Path -Leaf $OutFile) (sha256 verified)"
            return
        }
        Write-Warn "existing $(Split-Path -Leaf $OutFile) failed its pinned sha256; re-downloading"
        Remove-Item -LiteralPath $OutFile -Force
    }
    $dir = Split-Path -Parent $OutFile
    if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $tmp = "$OutFile.tmp.$PID"
    $urls = @($Url)
    if ($MirrorUrl -and $MirrorUrl -ne $Url) { $urls += $MirrorUrl }
    $downloaded = $false
    foreach ($candidate in $urls) {
        Write-Host "==> downloading $(Split-Path -Leaf $OutFile)"
        try {
            if ($PSVersionTable.PSVersion.Major -lt 6) {
                try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }
            }
            Invoke-WebRequest -Uri $candidate -OutFile $tmp -UseBasicParsing
            $downloaded = $true
            break
        } catch {
            Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        }
    }
    if (-not $downloaded) {
        Fail-Typed 9 'ASSET_DOWNLOAD_FAILED' "could not download $(Split-Path -Leaf $OutFile) from $($urls -join ' or ')"
    }
    $actual = (Get-FileHash -LiteralPath $tmp -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256.ToLowerInvariant()) {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        Fail-Typed 9 'ASSET_CHECKSUM_MISMATCH' "refusing to install $(Split-Path -Leaf $OutFile): expected $Sha256, got $actual"
    }
    Move-Item -LiteralPath $tmp -Destination $OutFile -Force
}

function Expand-ModelArchive {
    param([string]$Archive, [string]$Destination)
    $tar = Get-CommandPath 'tar'
    if (-not $tar) {
        Fail-Prerequisite 'TAR' 'tar is required to extract model archives'
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    & $tar -xjf $Archive -C $Destination
    if ($LASTEXITCODE -ne 0) {
        Fail-Typed 1 'QUBIT_INSTALL_MODEL_EXTRACT_FAILED' "could not extract $(Split-Path -Leaf $Archive)"
    }
}

function Install-ModelAssets {
    if ($NoModels) {
        Write-Step 'models skipped (-NoModels)'
        return
    }
    if ($DryRun) {
        Write-Step "[dry-run] would verify/download sha256-pinned model assets under $($script:Layout.ModelsDir)"
        return
    }
    $models = $script:Layout.ModelsDir
    New-Item -ItemType Directory -Force -Path $models | Out-Null

    $voice = 'en_US-amy-medium'
    Invoke-VerifiedDownload `
        -Url "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium/$voice.onnx" `
        -Sha256 $script:VoiceSha256 `
        -OutFile (Join-Path $models "$voice.onnx")
    Invoke-VerifiedDownload `
        -Url "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium/$voice.onnx.json" `
        -Sha256 $script:VoiceJsonSha256 `
        -OutFile (Join-Path $models "$voice.onnx.json")

    $moon = 'sherpa-onnx-moonshine-base-en-quantized-2026-02-27'
    if (-not (Test-Path -LiteralPath (Join-Path $models $moon) -PathType Container)) {
        $archive = Join-Path $models ".$moon.tar.bz2"
        Invoke-VerifiedDownload `
            -Url "$script:SherpaRelease/asr-models/$moon.tar.bz2" `
            -Sha256 $script:MoonSha256 `
            -OutFile $archive
        Expand-ModelArchive -Archive $archive -Destination $models
        Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
    } else {
        Write-Host "    have   $moon"
    }

    $parakeet = 'sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8'
    if (-not (Test-Path -LiteralPath (Join-Path $models $parakeet) -PathType Container)) {
        $archive = Join-Path $models ".$parakeet.tar.bz2"
        Invoke-VerifiedDownload `
            -Url "$script:SherpaRelease/asr-models/$parakeet.tar.bz2" `
            -Sha256 $script:ParakeetSha256 `
            -OutFile $archive
        Expand-ModelArchive -Archive $archive -Destination $models
        Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
    } else {
        Write-Host "    have   $parakeet"
    }

    Invoke-VerifiedDownload `
        -Url "$script:SherpaRelease/asr-models/silero_vad_v5.onnx" `
        -Sha256 $script:SileroV5Sha256 `
        -OutFile (Join-Path $models 'silero_vad_v5.onnx')
    Invoke-VerifiedDownload `
        -Url 'https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.1/src/silero_vad/data/silero_vad.onnx' `
        -Sha256 $script:SileroV6Sha256 `
        -OutFile (Join-Path $models 'silero_vad_v6.onnx')
    Invoke-VerifiedDownload `
        -Url "$script:SherpaRelease/speaker-recongition-models/3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx" `
        -Sha256 $script:Eres2netSha256 `
        -OutFile (Join-Path $models '3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx')
}

function Install-OllamaModels {
    if ($NoModels) { return }
    if ($DryRun) {
        Write-Step '[dry-run] would ensure ollama models: nomic-embed-text, qubit-brain'
        return
    }
    $ollama = Get-CommandPath 'ollama'
    if (-not $ollama) {
        Write-Warn 'ollama not found; local short replies need qubit-brain (DeepSeek API still works)'
        return
    }
    if (-not (& $ollama list 2>$null | Select-String -Pattern 'nomic-embed-text' -Quiet)) {
        Write-Step 'pulling nomic-embed-text (gateway memory embeddings)'
        & $ollama pull nomic-embed-text
    } else {
        Write-Host '    have   nomic-embed-text'
    }
    $source = $script:SourceDir
    $modelfile = Join-Path $source 'Modelfile.qubit'
    if (-not (Test-Path -LiteralPath $modelfile -PathType Leaf)) {
        $modelfile = Join-Path $source 'tests/fixtures/modelfiles/qubit4.sanitized'
    }
    if (-not (& $ollama list 2>$null | Select-String -Pattern '^qubit-brain' -Quiet)) {
        if (Test-Path -LiteralPath $modelfile -PathType Leaf) {
            Write-Step 'creating qubit-brain'
            & $ollama create qubit-brain -f $modelfile
        } else {
            Write-Warn "no Modelfile found for qubit-brain; run: ollama create qubit-brain -f Modelfile.qubit"
        }
    } else {
        Write-Host '    have   qubit-brain'
    }
}

function Install-Venv {
    if ($DryRun) {
        Write-Step "[dry-run] would create the venv at $($script:Layout.VenvDir) and install the Qubit package"
        return
    }
    $uv = Get-Uv
    $py = Join-Path $script:Layout.VenvDir 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $py -PathType Leaf)) {
        Write-Step "creating venv (uv) at $($script:Layout.VenvDir)"
        & $uv venv --python 3.12 $script:Layout.VenvDir
        if ($LASTEXITCODE -ne 0) {
            Fail-Typed 1 'QUBIT_INSTALL_VENV_FAILED' 'uv venv failed'
        }
    }
    if (-not (Test-Path -LiteralPath $py -PathType Leaf)) {
        Fail-Typed 1 'QUBIT_INSTALL_VENV_FAILED' "venv python was not created at $py"
    }
    Write-Step 'installing the Qubit package and declared dependencies'
    & $uv pip install --python $py "$($script:SourceDir)[hotkey,edge,test]"
    if ($LASTEXITCODE -ne 0) {
        Fail-Typed 1 'QUBIT_INSTALL_DEPS_FAILED' 'Qubit package installation failed'
    }
    # webrtcvad needs the legacy pkg_resources API (removed in setuptools 81+).
    & $uv pip install --python $py 'setuptools<81'
    if ($LASTEXITCODE -ne 0) {
        Fail-Typed 1 'QUBIT_INSTALL_DEPS_FAILED' 'setuptools<81 installation failed'
    }
}

function Install-Dashboard {
    if ($NoDashboard) {
        $script:DashboardState = 'skipped'
        Write-Step 'dashboard skipped (-NoDashboard)'
        return
    }
    $index = Join-Path $script:SourceDir 'frontend/qubit-dist/index.html'
    if (Test-Path -LiteralPath $index -PathType Leaf) {
        $script:DashboardState = 'present'
        return
    }
    if ($DryRun) {
        $script:DashboardState = 'would_build'
        Write-Step '[dry-run] would build the dashboard (frontend/qubit-dist missing)'
        return
    }
    $bash = Get-CommandPath 'bash'
    $node = Get-CommandPath 'node'
    $npm = Get-CommandPath 'npm'
    if ($bash -and $node -and $npm) {
        Write-Step 'building the dashboard (frontend/qubit-dist missing)'
        & $bash (Join-Path $script:SourceDir 'scripts/build_dashboard.sh')
        if ($LASTEXITCODE -ne 0) {
            $script:DashboardState = 'build_failed'
            Write-Warn 'dashboard build failed; rerun scripts/build_dashboard.sh once the toolchain/network is fixed'
        } else {
            $script:DashboardState = 'built'
        }
        return
    }
    $script:DashboardState = 'toolchain_missing'
    Write-Warn 'frontend/qubit-dist is missing and node/npm + bash are not available'
}

function Install-Config {
    if ($DryRun) {
        Write-Step "[dry-run] would create runtime and voice config under $($script:Layout.QubitHome)"
        return
    }
    $homePath = $script:Layout.QubitHome
    foreach ($dir in @('cron', 'sessions', 'logs', 'pairing', 'hooks', 'image_cache', 'audio_cache', 'memories', 'skills')) {
        New-Item -ItemType Directory -Force -Path (Join-Path $homePath $dir) | Out-Null
    }
    # Initialize the VoiceBridge tree the runtime actually reads. A fresh
    # install gets $QUBIT_HOME\voice; a read-old tree is preserved in place.
    foreach ($dir in @($script:Layout.VoiceRoot, $script:Layout.VoiceDir,
            $script:Layout.DataDir, $script:Layout.StateDir,
            $script:Layout.CacheDir, $script:Layout.ModelsDir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $envFile = Join-Path $homePath '.env'
    if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
        $example = Join-Path $script:SourceDir '.env.example'
        if (Test-Path -LiteralPath $example -PathType Leaf) {
            Copy-Item -LiteralPath $example -Destination $envFile
        } else {
            New-Item -ItemType File -Path $envFile | Out-Null
        }
    }
    $runtimeConfig = Join-Path $homePath 'config.yaml'
    # The example lives inside the vendored runtime tree in a source checkout;
    # accept a root-level copy too (release layouts may hoist it).
    $runtimeExample = Join-Path $script:SourceDir 'cli-config.yaml.example'
    if (-not (Test-Path -LiteralPath $runtimeExample -PathType Leaf)) {
        $runtimeExample = Join-Path $script:SourceDir 'qubit_runtime/cli-config.yaml.example'
    }
    if (-not (Test-Path -LiteralPath $runtimeConfig -PathType Leaf) -and
        (Test-Path -LiteralPath $runtimeExample -PathType Leaf)) {
        Copy-Item -LiteralPath $runtimeExample -Destination $runtimeConfig
    }

    $voiceDir = $script:Layout.VoiceDir
    New-Item -ItemType Directory -Force -Path $voiceDir | Out-Null
    $voiceConfig = Join-Path $voiceDir 'voice.yaml'
    $voiceExample = Join-Path $script:SourceDir 'config/voice.example.yaml'
    if (-not (Test-Path -LiteralPath $voiceConfig -PathType Leaf) -and
        (Test-Path -LiteralPath $voiceExample -PathType Leaf)) {
        Copy-Item -LiteralPath $voiceExample -Destination $voiceConfig
    }
    $knowledge = Join-Path $voiceDir 'personal-knowledge.md'
    if (-not (Test-Path -LiteralPath $knowledge -PathType Leaf)) {
        $knowledgeExample = Join-Path $script:SourceDir 'config/personal-knowledge.md'
        if (Test-Path -LiteralPath $knowledgeExample -PathType Leaf) {
            Copy-Item -LiteralPath $knowledgeExample -Destination $knowledge
        } else {
            [IO.File]::WriteAllText($knowledge, "# Personal knowledge (optional)`r`n# Add stable facts the assistant may use when answering.`r`n", (New-Object System.Text.UTF8Encoding($false)))
        }
    }
    $templates = Join-Path $voiceDir 'task-templates.yaml'
    $templatesExample = Join-Path $script:SourceDir 'config/task-templates.example.yaml'
    if (-not (Test-Path -LiteralPath $templates -PathType Leaf) -and
        (Test-Path -LiteralPath $templatesExample -PathType Leaf)) {
        Copy-Item -LiteralPath $templatesExample -Destination $templates
    }
    Write-Ok "config prepared under $homePath"
}

function Write-Launcher {
    param([string]$Name)
    $launcher = Join-Path $script:Layout.BinDir "$Name.cmd"
    $exe = Join-Path $script:Layout.VenvDir 'Scripts\qubit.exe'
    $lines = @(
        '@echo off',
        'setlocal',
        "set `"QUBIT_HOME=$($script:Layout.QubitHome)`"",
        "set `"XDG_CONFIG_HOME=$($script:Layout.XdgConfig)`"",
        "set `"XDG_DATA_HOME=$($script:Layout.XdgData)`"",
        "set `"XDG_STATE_HOME=$($script:Layout.XdgState)`"",
        "set `"XDG_CACHE_HOME=$($script:Layout.XdgCache)`"",
        "`"$exe`" %*"
    )
    $text = ($lines -join "`r`n") + "`r`n"
    [IO.File]::WriteAllText($launcher, $text, (New-Object System.Text.UTF8Encoding($false)))
}

function Install-Launcher {
    if ($DryRun) {
        Write-Step "[dry-run] would install the qubit launcher at $($script:Layout.BinDir)\qubit.cmd"
        return
    }
    New-Item -ItemType Directory -Force -Path $script:Layout.BinDir | Out-Null
    Write-Launcher 'qubit'
    # Compatibility alias: `voicebridge` remains the same backend.
    Write-Launcher 'voicebridge'
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not $userPath) { $userPath = '' }
    $entries = @($userPath -split ';' | ForEach-Object { $_.TrimEnd('\') })
    if ($entries -notcontains $script:Layout.BinDir.TrimEnd('\')) {
        $newPath = if ($userPath) { "$($script:Layout.BinDir);$userPath" } else { $script:Layout.BinDir }
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
        Write-Ok "added $($script:Layout.BinDir) to your user PATH (new shells pick it up)"
    }
    if (-not (($env:Path -split ';') -contains $script:Layout.BinDir)) {
        $env:Path = "$($script:Layout.BinDir);$env:Path"
    }
    [Environment]::SetEnvironmentVariable('QUBIT_HOME', $script:Layout.QubitHome, 'User')
    Write-Ok "qubit launcher installed at $($script:Layout.BinDir)\qubit.cmd"
}

function Write-CompletionMarker {
    # The bootstrap receipt `qubit update`/source_stamp read to know this
    # checkout's install finished and what it pinned. The vendored runtime
    # installer already writes it; the public Windows entry must too, or an
    # update treats a fresh install as an unfinished bootstrap.
    if ($DryRun) { return }
    $dir = $script:SourceDir
    if ([string]::IsNullOrWhiteSpace($dir)) { $dir = $script:Layout.InstallDir }
    if ([string]::IsNullOrWhiteSpace($dir) -or -not (Test-Path -LiteralPath $dir)) { return }
    $commit = ''
    $git = Get-CommandPath 'git'
    if ($git) {
        $commit = (& $git -C $dir rev-parse HEAD 2>$null | Select-Object -First 1)
        if ($LASTEXITCODE -ne 0) { $commit = '' }
    }
    if (-not $commit) { return }
    $marker = [ordered]@{
        schemaVersion = 1
        pinnedCommit  = "$commit".Trim()
        pinnedBranch  = $GitBranch
        completedAt   = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
    }
    $legacy = Join-Path $dir '.hermes-bootstrap-complete'
    $marker | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $legacy -Encoding UTF8
    # Dual-publish: the canonical marker beside the one older readers accept.
    Copy-Item -LiteralPath $legacy -Destination (Join-Path $dir '.qubit-bootstrap-complete') -Force
}

function Write-Layout {
    param([string]$Mode)
    Write-Step 'Qubit Agent install layout'
    Write-Host ("    QUBIT_HOME : {0}" -f $script:Layout.QubitHome)
    Write-Host ("    checkout   : {0}" -f $script:Layout.InstallDir)
    if ($script:Layout.SourceDir) {
        Write-Host ("    source     : {0}" -f $script:Layout.SourceDir)
    }
    Write-Host ("    layout     : {0} ({1})" -f $script:Layout.VoiceLayout, $script:Layout.VoiceRoot)
    Write-Host ("    data       : {0}" -f $script:Layout.DataDir)
    Write-Host ("    models     : {0}" -f $script:Layout.ModelsDir)
    Write-Host ("    config     : {0}" -f (Join-Path $script:Layout.VoiceDir 'voice.yaml'))
    Write-Host ("    state      : {0}" -f $script:Layout.StateDir)
    Write-Host ("    launcher   : {0}" -f (Join-Path $script:Layout.BinDir 'qubit.cmd'))
    Write-Host ("    venv       : {0}" -f $script:Layout.VenvDir)
    Write-Host ("    channel    : {0}" -f $Channel)
    if ($NoModels) { Write-Host '    models     : skipped (-NoModels)' }
    if ($NoDashboard) { Write-Host '    dashboard  : skipped (-NoDashboard)' }
    Write-Host ("    mode       : {0}" -f $Mode)
}

function Write-PathReloadHint {
    Write-Host ''
    Write-Host 'Restart your terminal to use qubit, or run:'
    Write-Host '    $env:Path = [Environment]::GetEnvironmentVariable(''Path'',''User'') + '';'' + [Environment]::GetEnvironmentVariable(''Path'',''Machine'')'
}

function Invoke-QubitInstall {
    if ($Help) {
        Write-Usage
        return
    }
    if ($Remaining -and $Remaining.Count -gt 0) {
        Fail-Typed 2 'QUBIT_INSTALL_USAGE' "unexpected argument: $($Remaining -join ' '); run with -Help"
    }
    if ($Channel -ne 'stable' -and $Channel -ne 'main') {
        Fail-Typed 2 'QUBIT_INSTALL_USAGE' "-Channel must be stable or main, not $Channel"
    }
    Assert-QubitSource $RepoUrl
    $layout = Resolve-Layout

    if ($DryRun) {
        Test-Prerequisite
        $project = Get-ProjectDir
        $source = $project
        if (-not (Test-Checkout $project)) {
            $source = $layout.InstallDir
            if ($Channel -eq 'stable') {
                Write-Step "[dry-run] would resolve the latest stable release from $RepoUrl"
            } else {
                Write-Step "[dry-run] would clone $RepoUrl ($GitBranch) into $($layout.InstallDir)"
            }
        }
        $layout.SourceDir = $source
        Write-Layout 'dry-run (no changes)'
        if (-not $NoDashboard) {
            Write-Step '[dry-run] would verify/build frontend/qubit-dist when missing'
        }
        Write-Step "[dry-run] would install the Qubit package into $($layout.VenvDir)"
        if (-not $NoModels) {
            Write-Step "[dry-run] would verify/download pinned model assets under $($layout.ModelsDir)"
        }
        Write-Step "[dry-run] would prepare config under $($layout.QubitHome)"
        Write-Step "[dry-run] would install the qubit launcher at $($layout.BinDir)\qubit.cmd"
        Write-Host ''
        Write-Host '==> dry-run complete: no changes were made.'
        return
    }

    Test-Prerequisite
    $script:SourceDir = Resolve-Source (Get-ProjectDir)
    $script:Layout.SourceDir = $script:SourceDir
    Write-Layout 'install'

    # Build the dashboard BEFORE installing the package, matching
    # scripts/install.sh. The package is installed non-editable and
    # voicebridge/resources.py resolves the bundle from package data first; a
    # package installed before frontend/qubit-dist exists can never see a later
    # checkout build, so a one-pass clean install would serve a STALE/BROKEN
    # dashboard until the installer was run a second time.
    Install-Dashboard
    Install-Venv
    if ($script:DashboardState -eq 'toolchain_missing' -or $script:DashboardState -eq 'build_failed') {
        Fail-Typed 4 'QUBIT_INSTALL_DASHBOARD_UNAVAILABLE' "the dashboard bundle is missing ($($script:DashboardState)); install node/npm + bash and rerun, or pass -NoDashboard"
    }
    Install-ModelAssets
    Install-OllamaModels
    Install-Config
    Install-Launcher
    Write-CompletionMarker
    Write-PathReloadHint
    Write-Host ''
    Write-Ok "Qubit Agent installed. Run: qubit"
    Write-Host '    qubit status        # gateway / ASR / TTS / voice-ID status'
    Write-Host '    qubit run           # hotkey push-to-talk loop'
    Write-Host '    qubit enroll        # enroll YOUR voice (voice-ID)'
}

if ($script:IsDotSourced) {
    return
}

try {
    Invoke-QubitInstall
} catch {
    $code = 1
    try {
        if ($_.Exception.Data.Contains('QubitExitCode')) {
            $code = [int]$_.Exception.Data['QubitExitCode']
        }
    } catch { }
    Write-Fail $_.Exception.Message
    if ($script:RunAsFile) {
        exit $code
    }
    $global:LASTEXITCODE = $code
}
