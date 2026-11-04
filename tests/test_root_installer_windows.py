"""Root Windows installer contract tests.

The Windows entry point is fetched as a string (``irm | iex``) and cannot be
executed on the Linux CI runner, so these are static contract checks plus the
cross-platform facts we can prove here:

* the file exists and is safe to fetch (no UTF-8 BOM, ASCII-only);
* it declares the product flags (``-QubitHome``, ``-Channel``, ``-DryRun``,
  ``-Help``, ``-NoModels``, ``-NoDashboard``) and the compatibility aliases;
* it defaults to the Qubit-owned ``%LOCALAPPDATA%\\qubit`` root and never
  clones the upstream Hermes/OpenClaw tree;
* its pinned model hashes match ``scripts/install.sh`` exactly;
* the CMD wrappers and README point at the same Qubit-owned entry.

No network, no Windows host, no service is touched.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PS1 = REPO / "scripts" / "install.ps1"
CMD = REPO / "scripts" / "install.cmd"
RUNTIME_CMD = REPO / "qubit_runtime" / "scripts" / "install.cmd"
RUNTIME_PS1 = REPO / "qubit_runtime" / "scripts" / "install.ps1"
INSTALL_SH = REPO / "scripts" / "install.sh"

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _ps1() -> str:
    return PS1.read_text(encoding="utf-8")


def _param_block(src: str) -> str:
    start = src.index("param(")
    end = src.index("\n)", start)
    return src[start:end]


def test_windows_entry_points_exist():
    assert PS1.is_file(), "scripts/install.ps1 is the missing root Windows entry"
    assert CMD.is_file(), "scripts/install.cmd must wrap the PowerShell entry"


def test_install_ps1_is_safe_to_fetch_as_a_string():
    raw = PS1.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), (
        "install.ps1 starts with a UTF-8 BOM; irm | iex cannot parse it on "
        "Windows PowerShell 5.1"
    )
    assert all(byte < 0x80 for byte in raw), (
        "install.ps1 must stay ASCII-only: a BOM-less 5.1 script is decoded "
        "with the ANSI code page"
    )
    src = _ps1()
    first_statement = next(
        line.strip()
        for line in src.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert first_statement.startswith("[CmdletBinding"), first_statement


def test_param_block_declares_the_product_options():
    block = _param_block(_ps1())
    for name in (
        "$QubitHome",
        "$Channel",
        "$GitBranch",
        "$DryRun",
        "$Help",
        "$NoModels",
        "$NoDashboard",
        "$NonInteractive",
        "$InstallDir",
        "$RepoUrl",
    ):
        assert name in block, f"{name} missing from the param block"


def test_compatibility_aliases_are_kept():
    block = _param_block(_ps1())
    for alias in (
        "Alias('WithoutModels', 'SkipModels')",
        "Alias('WithoutDashboard', 'SkipDashboard')",
        "Alias('SkipSetup')",
        "Alias('Branch')",
        "Alias('WhatIf')",
    ):
        assert alias in block, f"compatibility alias missing: {alias}"


def test_default_root_is_the_qubit_localappdata_path():
    block = _param_block(_ps1())
    assert "LOCALAPPDATA" in block
    assert "'qubit'" in block or '"qubit"' in block
    assert "QUBIT_HOME" in block
    assert "Join-Path $env:LOCALAPPDATA 'qubit'" in block


def test_voice_bridge_lives_under_the_qubit_home_voice_tree():
    """A new Windows install uses $QUBIT_HOME\\voice, like scripts/install.sh."""
    src = _ps1()
    assert "Join-Path $homePath 'voice'" in src
    assert "Join-Path $voiceRoot 'config'" in src
    assert "Join-Path $voiceRoot 'models'" in src
    # The old per-identity XDG voice tree must not be the install target.
    assert "Join-Path (Join-Path $data 'qubit-voicebridge') 'models'" not in src
    assert "Join-Path $config 'qubit-voicebridge'" not in src
    # All five voice directories are initialized on a real install.
    for kind in ("'data'", "'state'", "'cache'", "'models'"):
        assert kind in src


def test_channel_is_stable_or_main():
    src = _ps1()
    assert "$Channel" in src
    assert "'stable'" in src and "'main'" in src
    assert "QUBIT_INSTALL_CHANNEL" in src
    assert "Get-LatestStableTag" in src
    assert "QUBIT_INSTALL_RELEASE_UNAVAILABLE" in src


def test_help_and_dry_run_do_not_mutate_the_machine():
    src = _ps1()
    assert "function Write-Usage" in src
    assert "if ($Help)" in src
    assert "if ($DryRun)" in src
    assert "dry-run complete: no changes were made" in src

    dry = src[src.index("if ($DryRun) {"):]
    dry = dry[:dry.index("dry-run complete")]
    assert "Initialize-Checkout" not in dry, (
        "the dry-run branch must not clone or mutate a checkout"
    )


def test_never_clones_the_upstream_tree():
    src = _ps1()
    assert "cgraph-dev/Qubit" in src
    assert "QUBIT_REPO_URL" in src
    for banned in (
        "NousResearch",
        "hermes-agent",
        "HERMES_REPO_URL",
        "github.com/NousResearch",
    ):
        assert banned not in src, f"upstream source reference: {banned}"
    assert "QUBIT_INSTALL_SOURCE_REFUSED" in src


def test_typed_prerequisite_and_failure_classes():
    src = _ps1()
    assert "function Fail-Prerequisite" in src
    assert "QUBIT_INSTALL_PREREQUISITE_" in src
    for klass in ("UNSUPPORTED_PLATFORM", "POWERSHELL", "UV", "GIT", "TAR"):
        assert f"'{klass}'" in src, f"typed prerequisite class missing: {klass}"
    assert "QUBIT_INSTALL_DASHBOARD_UNAVAILABLE" in src
    assert "Fail-Typed 4 'QUBIT_INSTALL_DASHBOARD_UNAVAILABLE'" in src
    assert "ASSET_CHECKSUM_MISMATCH" in src
    assert "Fail-Typed 9 'ASSET_CHECKSUM_MISMATCH'" in src


def test_every_download_is_sha256_verified():
    src = _ps1()
    assert "Get-FileHash" in src
    assert "-Algorithm SHA256" in src
    assert "Invoke-VerifiedDownload" in src
    assert "scripts/build_dashboard.sh" in src


def test_pinned_model_hashes_match_the_posix_installer():
    src = _ps1()
    ps1_hashes = {
        name: value
        for name, value in re.findall(
            r"\$script:([A-Za-z0-9]+)Sha256\s*=\s*'([0-9a-f]{64})'", src
        )
    }
    sh_src = INSTALL_SH.read_text(encoding="utf-8")
    sh_hashes = dict(
        re.findall(r'^([A-Z0-9_]+_SHA256)="([0-9a-f]{64})"', sh_src, re.M)
    )
    expected = {
        "Voice": "VOICE_SHA256",
        "VoiceJson": "VOICE_JSON_SHA256",
        "Moon": "MOON_SHA256",
        "Parakeet": "PKT_SHA256",
        "SileroV5": "SILERO_V5_SHA256",
        "SileroV6": "SILERO_V6_SHA256",
        "Eres2net": "ERES2NET_SHA256",
    }
    for ps1_name, sh_name in expected.items():
        assert ps1_name in ps1_hashes, f"missing pinned hash: {ps1_name}"
        assert HEX64.match(ps1_hashes[ps1_name])
        assert ps1_hashes[ps1_name] == sh_hashes[sh_name], (
            f"{ps1_name} disagrees with scripts/install.sh {sh_name}"
        )


def test_launcher_is_qubit_owned_with_a_compatibility_alias():
    src = _ps1()
    assert "qubit.cmd" in src
    assert "Write-Launcher 'voicebridge'" in src
    assert "Scripts\\qubit.exe" in src
    assert "QUBIT_HOME" in src
    assert "SetEnvironmentVariable('Path'" in src


def test_cmd_wrapper_points_at_the_qubit_entry():
    text = CMD.read_text(encoding="utf-8")
    assert "install.ps1" in text
    assert "Qubit" in text
    assert "cgraph-dev/Qubit-AIO" in text
    assert "Hermes Agent Installer" not in text
    assert "NousResearch" not in text
    assert "%*" in text, "the CMD wrapper must forward installer flags"
    assert "EnableDelayedExpansion" in text, (
        "the CMD wrapper must read !ERRORLEVEL! inside its if-block"
    )


def test_runtime_cmd_wrapper_is_qubit_owned():
    text = RUNTIME_CMD.read_text(encoding="utf-8")
    assert "Hermes Agent Installer" not in text
    assert "Qubit" in text
    assert "cgraph-dev/Qubit-AIO" in text
    assert "scripts\\install.ps1" in text or "scripts/install.ps1" in text


def test_runtime_installer_keeps_the_compatibility_parameters():
    src = RUNTIME_PS1.read_text(encoding="utf-8")
    assert "[string]$QubitHome" in src
    assert "[switch]$SkipSetup" in src
    assert "[switch]$NonInteractive" in src
    assert "https://github.com/cgraph-dev/Qubit.git" in src


def test_readme_documents_the_windows_entry_briefly():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert "scripts\\install.ps1" in text or "scripts/install.ps1" in text
    assert "-QubitHome" in text
    assert "-Channel" in text
    assert "%LOCALAPPDATA%\\qubit" in text
    assert "install.cmd" in text


# ---------------------------------------------------------------------------
# Contract additions: flags drive the resolved paths, stable resolves Qubit
# tags, and the VoiceBridge tree is the documented new-install layout.
# ---------------------------------------------------------------------------

def test_qubit_home_drives_install_dir():
    src = _ps1()
    assert "$homePath = Convert-ToFullPath $QubitHome" in src
    assert "if ([string]::IsNullOrWhiteSpace($installPath)) {" in src
    assert "$installPath = Join-Path $homePath 'qubit'" in src
    # An explicit -InstallDir still wins; -QubitHome is the default driver.
    assert "if (-not [string]::IsNullOrWhiteSpace($InstallDir)) {" in src
    assert "return (Initialize-Checkout (Convert-ToFullPath $InstallDir))" in src


def test_stable_channel_resolves_qubit_release_tags_only():
    src = _ps1()
    assert "$tag = Get-LatestStableTag $RepoUrl" in src
    assert "git ls-remote --tags --refs $Origin" in src
    assert r"^v?(\d+)\.(\d+)\.(\d+)$" in src
    assert "QUBIT_INSTALL_RELEASE_UNAVAILABLE" in src
    # The root installer must not inherit the legacy upstream override.
    assert "HERMES_REPO_URL" not in src
    assert "hermes-agent" not in src
    assert "Assert-QubitSource $RepoUrl" in src


def test_release_tag_rerun_does_not_pull_a_detached_head():
    src = _ps1()
    assert "function Test-ReleaseTag" in src
    assert "merge-base --is-ancestor $ref HEAD" in src
    # `git pull` is only for branch refs; a detached tag checkout would fail.
    assert "if (-not $isRelease) {" in src
    branch_guard = src[src.index("if (-not $isRelease) {"):]
    branch_guard = branch_guard[:branch_guard.index("return $Target")]
    assert "pull --ff-only" in branch_guard


def test_runtime_config_example_falls_back_to_the_vendored_tree():
    src = _ps1()
    assert "Join-Path $script:SourceDir 'cli-config.yaml.example'" in src
    assert "Join-Path $script:SourceDir 'qubit_runtime/cli-config.yaml.example'" in src


def test_qubit_home_contains_the_new_voice_tree():
    src = _ps1()
    assert "$voiceRoot = Join-Path $homePath 'voice'" in src
    assert "Join-Path $voiceRoot 'config'" in src
    assert "Join-Path $voiceRoot 'data'" in src
    assert "Join-Path $voiceRoot 'state'" in src
    assert "Join-Path $voiceRoot 'cache'" in src
    assert "Join-Path $voiceRoot 'models'" in src
    assert "VoiceLayout = $voice.Mode" in src
    assert "'read-old'" in src
    # The launcher exports the resolved roots, not the old fixed names.
    assert "$($script:Layout.XdgConfig)" in src
    assert "$($script:Layout.XdgData)" in src


def test_dashboard_is_built_before_the_package_is_installed():
    """A one-pass install must not ship a dashboard-less non-editable package.

    ``scripts/install.sh`` was fixed to build the bundle before installing the
    package; the Windows twin must keep the same order. ``voicebridge``
    resolves the dashboard from package data first, so a package installed
    before ``frontend/qubit-dist`` exists can never see the later build.
    """
    src = _ps1()
    install = src[src.index("function Invoke-QubitInstall"):]
    install = install[:install.index("if ($script:IsDotSourced)")]
    assert "Install-Dashboard" in install
    assert "Install-Venv" in install
    assert install.index("Install-Dashboard") < install.index("Install-Venv"), (
        "scripts/install.ps1 installs the package before building the dashboard; "
        "a clean one-pass install would serve a STALE/BROKEN dashboard"
    )
    # Keep the POSIX installer's contract in the same assertion so the two
    # entry points cannot drift apart silently.
    sh = INSTALL_SH.read_text(encoding="utf-8")
    assert sh.index("install_dashboard\ninstall_venv_and_package") > 0


# ---------------------------------------------------------------------------
# Executed checks. These skip on hosts without a PowerShell parser; the
# Windows installer workflow always has one.
# ---------------------------------------------------------------------------

def _powershell_host() -> str | None:
    """Return a working PowerShell host, or None so the test skips.

    A mise shim can exist while having no configured version; treating that
    as a host would turn an environment problem into a false failure.
    """
    for candidate in (shutil.which("pwsh"), shutil.which("powershell")):
        if not candidate:
            continue
        try:
            probe = subprocess.run(
                [candidate, "-NoProfile", "-Command", "exit 0"],
                capture_output=True, text=True, timeout=20,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return candidate
    return None


def test_install_ps1_parses_with_the_real_powershell_parser(tmp_path: Path):
    host = _powershell_host()
    if not host:
        pytest.skip("no PowerShell host (pwsh/powershell) on this runner")
    harness = tmp_path / "parse-root-installer.ps1"
    harness.write_text(
        "param([string]$Path)\n"
        "$tokens = $null\n"
        "$errors = $null\n"
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "$Path, [ref]$tokens, [ref]$errors) | Out-Null\n"
        "if ($errors.Count) {\n"
        "  $errors | Select-Object -First 8 | ForEach-Object {\n"
        "    Write-Output ('  {0}: {1}' -f $_.Extent.StartLineNumber, $_.Message)\n"
        "  }\n"
        "  exit 1\n"
        "}\n"
        "Write-Output 'PS SYNTAX OK'\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(harness), str(PS1)],
        capture_output=True, text=True, timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PS SYNTAX OK" in result.stdout


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=Fixture",
         "-c", "user.email=fixture@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()


def _run_ps_file(host: str, script: Path, timeout: int = 300):
    return subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=timeout,
    )


def test_stable_channel_resolves_the_tag_and_reruns_cleanly(tmp_path: Path):
    host = _powershell_host()
    if not host:
        pytest.skip("no PowerShell host (pwsh/powershell) on this runner")
    if not shutil.which("git"):
        pytest.skip("git is required for the checkout fixture")

    origin = tmp_path / "origin"
    home = tmp_path / "home"
    origin.mkdir()
    home.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "commit", "-q", "--allow-empty", "-m", "before")
    _git(origin, "tag", "-a", "v1.2.3", "-m", "release")
    release = _git(origin, "rev-parse", "HEAD")
    _git(origin, "commit", "-q", "--allow-empty", "-m", "newer")
    tip = _git(origin, "rev-parse", "HEAD")

    def _wrapper(name: str) -> Path:
        script = tmp_path / name
        script.write_text(
            f". '{PS1}' -QubitHome '{home}' -Channel stable -RepoUrl '{origin.as_uri()}'\n"
            "function Get-LatestStableTag { param([string]$Origin) return 'v1.2.3' }\n"
            "Resolve-Layout | Out-Null\n"
            "Initialize-Checkout $script:Layout.InstallDir | Out-Null\n"
            "Write-Output (& git -C $script:Layout.InstallDir rev-parse HEAD)\n",
            encoding="utf-8",
        )
        return script

    first = _run_ps_file(host, _wrapper("install-first.ps1"))
    assert first.returncode == 0, first.stdout + first.stderr
    install = home / "qubit"
    assert _git(install, "rev-parse", "HEAD") == release

    # A checkout already past the release must not be moved backwards, and a
    # detached tag checkout must not be `git pull`ed on a rerun.
    _git(install, "checkout", "-q", "-B", "main", "origin/main")
    assert _git(install, "rev-parse", "HEAD") == tip
    second = _run_ps_file(host, _wrapper("install-second.ps1"))
    assert second.returncode == 0, second.stdout + second.stderr
    assert _git(install, "rev-parse", "HEAD") == tip
    assert _git(install, "status", "--porcelain") == ""


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 installer host")
def test_help_and_dry_run_make_no_changes(tmp_path: Path):
    host = _powershell_host()
    if not host:
        pytest.skip("no PowerShell host (pwsh/powershell) on this runner")
    if not shutil.which("uv"):
        pytest.skip("the -DryRun preflight requires uv on PATH")

    home = tmp_path / "qubit-home"
    help_result = subprocess.run(
        [host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS1), "-Help"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=120,
    )
    assert help_result.returncode == 0, help_result.stdout + help_result.stderr
    assert "Qubit Agent installer (Windows)" in help_result.stdout
    assert not home.exists()

    dry = subprocess.run(
        [host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS1),
         "-DryRun", "-NoModels", "-NoDashboard", "-NonInteractive",
         "-QubitHome", str(home)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=120,
    )
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "dry-run complete: no changes were made." in dry.stdout
    assert not home.exists(), f"-DryRun created {home}"


def test_install_config_initializes_the_new_voice_tree(tmp_path: Path):
    host = _powershell_host()
    if not host:
        pytest.skip("no PowerShell host (pwsh/powershell) on this runner")
    home = tmp_path / "home"
    wrapper = tmp_path / "config.ps1"
    wrapper.write_text(
        f". '{PS1}' -QubitHome '{home}'\n"
        "Resolve-Layout | Out-Null\n"
        f"$script:SourceDir = '{REPO}'\n"
        "Install-Config\n"
        "Write-Output $script:Layout.VoiceLayout\n",
        encoding="utf-8",
    )
    result = _run_ps_file(host, wrapper, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "new" in result.stdout.splitlines()

    for rel in (
        "voice/config/voice.yaml",
        "voice/data",
        "voice/state",
        "voice/cache",
        "voice/models",
        ".env",
        "config.yaml",
    ):
        assert (home / rel).exists(), f"Install-Config did not create {rel}"


def test_install_phase_order_is_dashboard_then_venv(tmp_path: Path):
    """Execute the real control flow with stubbed phases and record the order.

    This runs on any PowerShell host (Linux pwsh in the focused suite, Windows
    PowerShell in the installer workflow), so the ordering contract is proven
    by execution, not only by a source grep.
    """
    host = _powershell_host()
    if not host:
        pytest.skip("no PowerShell host (pwsh/powershell) on this runner")
    home = tmp_path / "home"
    wrapper = tmp_path / "order.ps1"
    wrapper.write_text(
        f". '{PS1}' -QubitHome '{home}' -NoModels -NoDashboard -NonInteractive\n"
        "$script:order = @()\n"
        "function Write-Layout { param([string]$Mode) }\n"
        "function Write-Step { param([string]$Message) }\n"
        "function Write-Ok { param([string]$Message) }\n"
        "function Write-PathReloadHint { }\n"
        "function Test-Prerequisite { }\n"
        "function Resolve-Source { param([string]$ProjectDir) return $ProjectDir }\n"
        "function Install-Dashboard { $script:order += 'dashboard'; $script:DashboardState = 'skipped' }\n"
        "function Install-Venv { $script:order += 'venv' }\n"
        "function Install-ModelAssets { $script:order += 'models' }\n"
        "function Install-OllamaModels { $script:order += 'ollama' }\n"
        "function Install-Config { $script:order += 'config' }\n"
        "function Install-Launcher { $script:order += 'launcher' }\n"
        "Invoke-QubitInstall\n"
        "Write-Output ($script:order -join ',')\n",
        encoding="utf-8",
    )
    result = _run_ps_file(host, wrapper, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    order = result.stdout.strip().splitlines()[-1].split(",")
    assert order[:2] == ["dashboard", "venv"], order


def test_get_latest_stable_tag_selects_the_latest_semver(tmp_path: Path):
    host = _powershell_host()
    if not host:
        pytest.skip("no PowerShell host (pwsh/powershell) on this runner")
    if not shutil.which("git"):
        pytest.skip("git is required for the tag fixture")
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "commit", "-q", "--allow-empty", "-m", "release")
    for tag in ("v1.2.3", "1.3.0", "v1.10.0", "v2.0.0-rc1", "nightly"):
        _git(origin, "tag", "-a", tag, "-m", tag)

    home = tmp_path / "home"
    wrapper = tmp_path / "tag.ps1"
    wrapper.write_text(
        f". '{PS1}' -QubitHome '{home}'\n"
        f"Write-Output (Get-LatestStableTag '{origin.as_uri()}')\n",
        encoding="utf-8",
    )
    result = _run_ps_file(host, wrapper, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "v1.10.0", result.stdout
