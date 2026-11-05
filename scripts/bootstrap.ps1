# Qubit Windows release bootstrap installer.
#
# Windows twin of scripts/bootstrap.sh. It downloads a release installer and a
# release source archive plus their SHA-256 checksums into a temporary
# directory, verifies BOTH hashes, extracts the archive safely, verifies that
# the extracted scripts/install.ps1 is byte-for-byte the verified installer,
# and executes ONLY that extracted copy. It never reads a source checkout.
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1 -Help
#   powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1 -DryRun C:\releases\v0.0.5
#   powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1 -QubitHome "$env:LOCALAPPDATA\qubit" C:\releases\v0.0.5
#
# Release asset contract (see INSTALL.md):
#   install.ps1            verified bootstrap copy of the Windows installer
#   qubit-release.zip      source tree containing scripts/install.ps1
#   SHA256SUMS             sha256 of both assets, named
#
# The archive format is ZIP because Windows PowerShell 5.1 can read it without
# an external tar program. The POSIX bootstrap keeps using qubit-release.tar.gz.
#
# A release base is required, so the fetched form passes it to a scriptblock:
#   powershell -Command "& ([scriptblock]::Create((irm https://.../bootstrap.ps1))) -Help"
# The file MUST NOT start with a UTF-8 BOM and MUST stay ASCII-only for
# Windows PowerShell 5.1.

[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Position = 0)]
    [string]$ReleaseBase = '',

    [Alias('h', '?')]
    [switch]$Help,

    [switch]$DryRun,

    [Alias('Profile')]
    [string]$ProfileName = '',

    # -Prefix and -QubitHome are the same Windows concept: the Qubit-owned
    # root. Supplying both is accepted only when they agree.
    [string]$Prefix = '',
    [string]$QubitHome = '',

    [Alias('WithoutModels', 'SkipModels')]
    [switch]$NoModels,

    [Alias('WithoutDashboard', 'SkipDashboard')]
    [switch]$NoDashboard,

    [Alias('y')]
    [switch]$Yes,

    # Accepted so a shared caller can be honest, then refused: Windows has no
    # systemd/launchd equivalent that this installer can install.
    [switch]$WithSystemd,

    [string]$ArchiveName = $(if ($env:QUBIT_RELEASE_ARCHIVE) {
            $env:QUBIT_RELEASE_ARCHIVE
        } else {
            'qubit-release.zip'
        }),

    [string]$AssetName = $(if ($env:QUBIT_RELEASE_ASSET) {
            $env:QUBIT_RELEASE_ASSET
        } else {
            'install.ps1'
        }),

    [string]$ChecksumFile = '',

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Remaining = @()
)

$ErrorActionPreference = 'Stop'

$script:IsDotSourced = $MyInvocation.InvocationName -eq '.'
$script:RunAsFile = [bool]{}.File
$script:RootValue = ''

function Write-Usage {
    @"
Qubit Windows bootstrap installer

Usage:
  powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1 [options] <release-base>
  scripts\bootstrap.cmd [options] <release-base>

  <release-base>     HTTPS base URL, file:// URL, or a local directory that
                     contains install.ps1, the release source archive, and a
                     SHA256SUMS file naming both.

Options:
  -Help              show this help without changing the machine
  -DryRun            print the plan; do not download, extract, execute, or
                     touch QUBIT_HOME
  -Profile NAME      install profile, exported to the verified installer as
                     QUBIT_PROFILE
  -Prefix DIR        portable Qubit root, forwarded as -QubitHome DIR
  -QubitHome DIR     Qubit-owned root, forwarded as -QubitHome DIR
  -NoModels          skip model downloads (forwarded as -NoModels)
  -NoDashboard       skip the dashboard build (forwarded as -NoDashboard)
  -Yes               non-interactive install (forwarded as -NonInteractive)
  -WithSystemd       refused: Windows has no systemd/launchd equivalent
  -Archive NAME      release source archive file name under <release-base>
                     (default: qubit-release.zip)
  -Asset NAME        release installer asset file name (default: install.ps1)
  -ChecksumFile FILE use FILE instead of <release-base>/SHA256SUMS

Behaviour:
  The release installer (install.ps1) and the source archive are downloaded
  into a temporary directory. Their SHA-256 values are checked against the
  checksum file, the archive is extracted there without writing outside it,
  and the installer is run from the extracted tree (not from the downloaded
  copy). A missing download, missing or invalid checksum, archive mismatch, or
  extraction failure fails closed. This command never reads the local source
  checkout and makes no network calls when <release-base> is a local path or
  file:// URL.

Release assets:
  install.ps1, qubit-release.zip, SHA256SUMS.
  The archive must extract to a tree with scripts/install.ps1 (either at the
  archive root or under exactly one top-level directory).

Supported host: Windows (PowerShell 5.1+); PowerShell 7 on Linux is supported
for hermetic tests of this bootstrap only. The release installer itself still
refuses a non-Windows host.
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

function Test-Remote {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    return [regex]::IsMatch($Value, '^(?i)https?://')
}

function Test-FileUrl {
    param([string]$Value)
    return [regex]::IsMatch([string]$Value, '^(?i)file://')
}

function Convert-FileUrlToPath {
    param([string]$Value)
    $path = $Value.Substring(7)
    # file:///C:/dir is the Windows form; drop the extra slash before a drive.
    if ($path -match '^/[A-Za-z]:[\\/]') {
        $path = $path.Substring(1)
    }
    return $path
}

function Join-Base {
    param([string]$Base, [string]$Relative)
    if (Test-Remote $Base) {
        return $Base.TrimEnd('/') + '/' + $Relative
    }
    $root = $Base
    if (Test-FileUrl $Base) {
        $root = Convert-FileUrlToPath $Base
    }
    return (Join-Path $root $Relative)
}

function Get-AssetSource {
    param([string]$Relative)
    return (Join-Base $ReleaseBase $Relative)
}

function Test-LocalAssetExists {
    param([string]$Relative)
    if (Test-Remote $ReleaseBase) { return $false }
    return (Test-Path -LiteralPath (Get-AssetSource $Relative) -PathType Leaf)
}

function Invoke-Fetch {
    param(
        [string]$Relative,
        [string]$Destination,
        [string]$MissingToken = 'BOOTSTRAP_DOWNLOAD_FAILED'
    )
    $source = Get-AssetSource $Relative
    if (Test-Remote $ReleaseBase) {
        Invoke-RemoteDownload $source $Destination $MissingToken
        return
    }
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        Fail-Typed 5 $MissingToken "release asset not found: $source"
    }
    Copy-Item -LiteralPath $source -Destination $Destination -Force
}

function Invoke-RemoteDownload {
    param(
        [string]$Url,
        [string]$Destination,
        [string]$MissingToken = 'BOOTSTRAP_DOWNLOAD_FAILED'
    )
    if ($PSVersionTable.PSVersion.Major -lt 6) {
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        } catch {
        }
    }
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            if ($PSVersionTable.PSVersion.Major -lt 6) {
                Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing
            } else {
                Invoke-WebRequest -Uri $Url -OutFile $Destination
            }
            return
        } catch {
            if ($attempt -ge 3) {
                Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
                Fail-Typed 5 $MissingToken "could not download $Url"
            }
            Start-Sleep -Seconds 1
        }
    }
}

function Get-ChecksumFor {
    param([string]$Want, [string]$File, [bool]$AllowBare)
    if (-not (Test-Path -LiteralPath $File -PathType Leaf)) { return '' }
    foreach ($raw in [System.IO.File]::ReadAllLines($File)) {
        $line = $raw.TrimEnd("`r")
        $match = [regex]::Match($line, '^\s*([0-9A-Fa-f]{64})(?:\s+\*?(\S+))?\s*$')
        if (-not $match.Success) { continue }
        $hash = $match.Groups[1].Value.ToLowerInvariant()
        $name = ''
        if ($match.Groups[2].Success) { $name = $match.Groups[2].Value }
        $name = ($name -split '[\\/]')[-1]
        if ($name -eq $Want -or ($AllowBare -and [string]::IsNullOrEmpty($name))) {
            return $hash
        }
    }
    return ''
}

function Get-Sha256 {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-Checksum {
    param([string]$Destination)
    if (-not [string]::IsNullOrWhiteSpace($ChecksumFile)) {
        if (Test-Remote $ChecksumFile) {
            Invoke-RemoteDownload $ChecksumFile $Destination
        } elseif (Test-FileUrl $ChecksumFile) {
            Copy-Item -LiteralPath (Convert-FileUrlToPath $ChecksumFile) -Destination $Destination -Force
        } else {
            if (-not (Test-Path -LiteralPath $ChecksumFile -PathType Leaf)) {
                return $false
            }
            Copy-Item -LiteralPath $ChecksumFile -Destination $Destination -Force
        }
        return $true
    }
    foreach ($candidate in @('SHA256SUMS', 'install.ps1.sha256', 'install.ps1.sha256sum')) {
        if (Test-Remote $ReleaseBase) {
            try {
                Invoke-RemoteDownload (Get-AssetSource $candidate) $Destination
                return $true
            } catch {
                continue
            }
        } elseif (Test-LocalAssetExists $candidate) {
            Copy-Item -LiteralPath (Get-AssetSource $candidate) -Destination $Destination -Force
            return $true
        }
    }
    return $false
}

function Assert-VerifiedAsset {
    param([string]$Label, [string]$Path, [string]$Expected)
    $actual = Get-Sha256 $Path
    if ($actual -ne $Expected) {
        Fail-Typed 6 'BOOTSTRAP_CHECKSUM_MISMATCH' "refusing to use $Label`n  expected sha256: $Expected`n  actual   sha256: $actual"
    }
    Write-Host "==> verified $Label sha256=$actual" -ForegroundColor Cyan
}

function Expand-ArchiveSafely {
    param([string]$Archive, [string]$Destination)
    Add-Type -AssemblyName System.IO.Compression | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null
    [System.IO.Directory]::CreateDirectory($Destination) | Out-Null
    $destFull = [System.IO.Path]::GetFullPath($Destination)
    $destFull = $destFull.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $zip = $null
    try {
        $zip = [System.IO.Compression.ZipFile]::OpenRead($Archive)
    } catch {
        Fail-Typed 6 'BOOTSTRAP_ARCHIVE_INVALID' "cannot read $ArchiveName"
    }
    try {
        foreach ($entry in $zip.Entries) {
            $name = [string]$entry.FullName
            if ([string]::IsNullOrEmpty($name)) { continue }
            $normalized = $name.Replace('\', '/')
            if ($normalized.StartsWith('/') -or
                $normalized -match '^[A-Za-z]:' -or
                $normalized.Contains(':') -or
                $normalized -match '(^|/)\.\.(/|$)') {
                Fail-Typed 6 'BOOTSTRAP_ARCHIVE_INVALID' "unsafe path in $ArchiveName`: $name"
            }
            $mode = ($entry.ExternalAttributes -shr 16) -band 0xFFFF
            if (($mode -band 0xF000) -eq 0xA000) {
                Fail-Typed 6 'BOOTSTRAP_ARCHIVE_INVALID' "refusing a symlink entry in $ArchiveName`: $name"
            }
            $target = [System.IO.Path]::GetFullPath((Join-Path $Destination $normalized))
            $inside = $target.Equals($destFull, [System.StringComparison]::Ordinal) -or
                $target.StartsWith($destFull + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::Ordinal)
            if (-not $inside) {
                Fail-Typed 6 'BOOTSTRAP_ARCHIVE_INVALID' "unsafe path in $ArchiveName`: $name"
            }
            if ($normalized.EndsWith('/')) {
                [System.IO.Directory]::CreateDirectory($target) | Out-Null
                continue
            }
            $parent = [System.IO.Path]::GetDirectoryName($target)
            if ($parent) { [System.IO.Directory]::CreateDirectory($parent) | Out-Null }
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
        }
    } finally {
        if ($zip) { $zip.Dispose() }
    }
}

function Find-InstallRoot {
    param([string]$Extract)
    if (Test-Path -LiteralPath (Join-Path $Extract 'scripts/install.ps1') -PathType Leaf) {
        return $Extract
    }
    $dirs = @([System.IO.Directory]::GetDirectories($Extract))
    if ($dirs.Count -eq 1 -and
        (Test-Path -LiteralPath (Join-Path $dirs[0] 'scripts/install.ps1') -PathType Leaf)) {
        return $dirs[0]
    }
    return ''
}

function Get-HostExecutable {
    foreach ($candidate in @('pwsh', 'powershell.exe', 'powershell')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source) { return $cmd.Source }
    }
    try {
        return (Get-Process -Id $PID).Path
    } catch {
        return ''
    }
}

function Write-Plan {
    param([string]$ChecksumLabel)
    Write-Host 'Qubit Windows bootstrap plan'
    Write-Host '  host       : Windows (PowerShell)'
    Write-Host "  base       : $ReleaseBase"
    Write-Host "  asset      : $AssetName"
    Write-Host "  archive    : $ArchiveName"
    Write-Host "  checksum   : $ChecksumLabel"
    if ([string]::IsNullOrEmpty($ProfileName)) {
        Write-Host '  profile    : (default)'
    } else {
        Write-Host "  profile    : $ProfileName (QUBIT_PROFILE)"
    }
    if ([string]::IsNullOrEmpty($RootValue)) {
        Write-Host '  qubit home : (installer default)'
    } else {
        Write-Host "  qubit home : $RootValue"
    }
    if ($NoModels) { Write-Host '  models     : skipped (-NoModels)' }
    if ($NoDashboard) { Write-Host '  dashboard  : skipped (-NoDashboard)' }
    Write-Host '  QUBIT_HOME : untouched (dry-run)'
    Write-Host "  steps      : download $AssetName + $ArchiveName -> verify sha256 -> extract -> run the extracted scripts/install.ps1"
    Write-Host 'dry-run complete; nothing downloaded, extracted, executed, or written'
}

function Invoke-QubitBootstrap {
    if ($Help) {
        Write-Usage
        return
    }
    if ($Remaining -and $Remaining.Count -gt 0) {
        Fail-Typed 2 'BOOTSTRAP_USAGE' "unexpected argument: $($Remaining -join ' '); run with -Help"
    }
    if ([string]::IsNullOrWhiteSpace($ReleaseBase)) {
        Fail-Typed 2 'BOOTSTRAP_USAGE' 'missing release base; run with -Help'
    }
    if ($ArchiveName -match '[\\/]' -or $ArchiveName -eq '.' -or $ArchiveName -eq '..' -or [string]::IsNullOrWhiteSpace($ArchiveName)) {
        Fail-Typed 2 'BOOTSTRAP_USAGE' "option -Archive must be a bare file name, got: $ArchiveName"
    }
    if ($AssetName -match '[\\/]' -or [string]::IsNullOrWhiteSpace($AssetName)) {
        Fail-Typed 2 'BOOTSTRAP_USAGE' "option -Asset must be a bare file name, got: $AssetName"
    }
    if ($WithSystemd) {
        Fail-Typed 2 'BOOTSTRAP_OPTION_UNSUPPORTED' '-WithSystemd has no Windows equivalent; the Windows installer does not install systemd/launchd services'
    }
    if (-not [string]::IsNullOrEmpty($Prefix) -and -not [string]::IsNullOrEmpty($QubitHome)) {
        $prefixFull = [System.IO.Path]::GetFullPath($Prefix)
        $homeFull = [System.IO.Path]::GetFullPath($QubitHome)
        if ($prefixFull -ne $homeFull) {
            Fail-Typed 2 'BOOTSTRAP_USAGE' '-Prefix and -QubitHome name different roots; pass only one'
        }
    }
    $script:RootValue = $QubitHome
    if ([string]::IsNullOrEmpty($script:RootValue)) { $script:RootValue = $Prefix }

    $script:ReleaseBase = $ReleaseBase.TrimEnd('/')

    if ($DryRun) {
        $checksumLabel = ''
        if (-not [string]::IsNullOrWhiteSpace($ChecksumFile)) {
            if (Test-Remote $ChecksumFile) {
                $checksumLabel = "$ChecksumFile (not fetched in dry-run)"
            } elseif (Test-FileUrl $ChecksumFile) {
                $path = Convert-FileUrlToPath $ChecksumFile
                if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                    Fail-Typed 7 'BOOTSTRAP_CHECKSUM_UNAVAILABLE' "checksum file not found: $path"
                }
                $checksumLabel = $ChecksumFile
            } else {
                if (-not (Test-Path -LiteralPath $ChecksumFile -PathType Leaf)) {
                    Fail-Typed 7 'BOOTSTRAP_CHECKSUM_UNAVAILABLE' "checksum file not found: $ChecksumFile"
                }
                $checksumLabel = $ChecksumFile
            }
        } elseif (-not (Test-Remote $ReleaseBase)) {
            $checksumPath = ''
            foreach ($candidate in @('SHA256SUMS', 'install.ps1.sha256', 'install.ps1.sha256sum')) {
                if (Test-LocalAssetExists $candidate) {
                    $checksumPath = Get-AssetSource $candidate
                    break
                }
            }
            if ([string]::IsNullOrEmpty($checksumPath)) {
                Fail-Typed 7 'BOOTSTRAP_CHECKSUM_UNAVAILABLE' "no SHA256SUMS/install.ps1.sha256 under $script:ReleaseBase"
            }
            if ([string]::IsNullOrEmpty((Get-ChecksumFor $AssetName $checksumPath $true))) {
                Fail-Typed 7 'BOOTSTRAP_CHECKSUM_INVALID' "$AssetName is not named in $checksumPath"
            }
            if ([string]::IsNullOrEmpty((Get-ChecksumFor $ArchiveName $checksumPath $false))) {
                Fail-Typed 7 'BOOTSTRAP_CHECKSUM_INVALID' "$ArchiveName is not named in $checksumPath"
            }
            $checksumLabel = $checksumPath
        } else {
            $checksumLabel = "<base>/SHA256SUMS (must name $AssetName and $ArchiveName)"
        }

        if (-not (Test-Remote $ReleaseBase)) {
            if (-not (Test-LocalAssetExists $AssetName)) {
                Fail-Typed 5 'BOOTSTRAP_DOWNLOAD_FAILED' "release asset not found: $(Get-AssetSource $AssetName)"
            }
            if (-not (Test-LocalAssetExists $ArchiveName)) {
                Fail-Typed 5 'BOOTSTRAP_ARCHIVE_MISSING' "release archive not found: $(Get-AssetSource $ArchiveName)"
            }
        }
        Write-Plan $checksumLabel
        return
    }

    $tempRoot = [System.IO.Path]::GetTempPath()
    $tempDir = Join-Path $tempRoot ('qubit-bootstrap.' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
    $assetPath = Join-Path $tempDir $AssetName
    $archivePath = Join-Path $tempDir $ArchiveName
    $checksumPath = Join-Path $tempDir 'checksum'
    $extractDir = Join-Path $tempDir 'release'

    try {
        Write-Host '==> downloading checksum' -ForegroundColor Cyan
        if (-not (Get-Checksum $checksumPath)) {
            Fail-Typed 7 'BOOTSTRAP_CHECKSUM_UNAVAILABLE' "no checksum found (use -ChecksumFile FILE or publish SHA256SUMS beside $AssetName and $ArchiveName)"
        }

        $expectedAsset = Get-ChecksumFor $AssetName $checksumPath $true
        if ([string]::IsNullOrEmpty($expectedAsset)) {
            Fail-Typed 7 'BOOTSTRAP_CHECKSUM_INVALID' "$AssetName is not named in the checksum file"
        }
        $expectedArchive = Get-ChecksumFor $ArchiveName $checksumPath $false
        if ([string]::IsNullOrEmpty($expectedArchive)) {
            Fail-Typed 7 'BOOTSTRAP_CHECKSUM_INVALID' "$ArchiveName is not named in the checksum file"
        }

        Write-Host "==> downloading $AssetName from $script:ReleaseBase" -ForegroundColor Cyan
        Invoke-Fetch $AssetName $assetPath 'BOOTSTRAP_DOWNLOAD_FAILED'
        Assert-VerifiedAsset $AssetName $assetPath $expectedAsset
        $actualAsset = Get-Sha256 $assetPath

        Write-Host "==> downloading $ArchiveName from $script:ReleaseBase" -ForegroundColor Cyan
        Invoke-Fetch $ArchiveName $archivePath 'BOOTSTRAP_ARCHIVE_MISSING'
        Assert-VerifiedAsset $ArchiveName $archivePath $expectedArchive

        Write-Host "==> extracting $ArchiveName" -ForegroundColor Cyan
        Expand-ArchiveSafely $archivePath $extractDir

        $installRoot = Find-InstallRoot $extractDir
        if ([string]::IsNullOrEmpty($installRoot)) {
            Fail-Typed 6 'BOOTSTRAP_ARCHIVE_INVALID' "$ArchiveName does not contain scripts/install.ps1"
        }
        $extractFull = [System.IO.Path]::GetFullPath($extractDir)
        $installScriptDir = [System.IO.Path]::GetFullPath((Join-Path $installRoot 'scripts'))
        $inside = $installScriptDir.Equals($extractFull, [System.StringComparison]::Ordinal) -or
            $installScriptDir.StartsWith($extractFull + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::Ordinal)
        if (-not $inside) {
            Fail-Typed 6 'BOOTSTRAP_ARCHIVE_INVALID' 'extracted scripts/install.ps1 escapes the extraction directory'
        }

        $extractedInstaller = Join-Path $installRoot 'scripts/install.ps1'
        $extractedSha = Get-Sha256 $extractedInstaller
        if ($extractedSha -ne $actualAsset) {
            Fail-Typed 6 'BOOTSTRAP_CHECKSUM_MISMATCH' "extracted scripts/install.ps1 does not match the verified $AssetName`n  $AssetName sha256: $actualAsset`n  archive scripts/install.ps1 sha256: $extractedSha"
        }
        Write-Host "==> verified extracted scripts/install.ps1 sha256=$extractedSha" -ForegroundColor Cyan
        Write-Host '==> running verified installer from the extracted release tree' -ForegroundColor Cyan

        $installArgs = @()
        if (-not [string]::IsNullOrEmpty($script:RootValue)) {
            $installArgs += @('-QubitHome', $script:RootValue)
        }
        if ($NoModels) { $installArgs += '-NoModels' }
        if ($NoDashboard) { $installArgs += '-NoDashboard' }
        if ($Yes) { $installArgs += '-NonInteractive' }
        if (-not [string]::IsNullOrEmpty($ProfileName)) {
            $env:QUBIT_PROFILE = $ProfileName
        }

        $hostExe = Get-HostExecutable
        if ([string]::IsNullOrEmpty($hostExe)) {
            Fail-Typed 4 'BOOTSTRAP_MISSING_TOOL' 'cannot locate a PowerShell host to run the extracted installer'
        }
        & $hostExe -NoProfile -ExecutionPolicy Bypass -File $extractedInstaller @installArgs
        $status = $LASTEXITCODE
        if ($null -eq $status) { $status = 0 }
        if ($script:RunAsFile) {
            exit $status
        }
        # Fetched as a string (irm | iex): an exit here would close the caller's
        # shell. Publish the installer status instead.
        $global:LASTEXITCODE = $status
        return
    } finally {
        Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($script:IsDotSourced) {
    return
}

try {
    Invoke-QubitBootstrap
} catch {
    $code = 1
    try {
        if ($_.Exception.Data.Contains('QubitExitCode')) {
            $code = [int]$_.Exception.Data['QubitExitCode']
        }
    } catch {
    }
    [Console]::Error.WriteLine($_.Exception.Message)
    if ($script:RunAsFile) {
        exit $code
    }
    $global:LASTEXITCODE = $code
}
