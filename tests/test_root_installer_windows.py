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

import re
from pathlib import Path

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
