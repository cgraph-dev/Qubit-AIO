"""Fresh Windows install from the public (umbrella) layout.

The public seed nests the vendored runtime at ``qubit_runtime/`` inside the
umbrella checkout, and the public Windows entry point is the umbrella's
``scripts/install.ps1``. These are static contracts (the real install runs on a
Windows host), pinned so the two failure classes stay closed:

* the root installer must not require a ``uv.exe``/``git.exe`` already on PATH
  (the E2E's clean-machine premise removes them): it stages Qubit's pinned,
  sha256-verified toolchain instead, with the same pins the vendored runtime
  installer carries;
* the vendored runtime installer must resolve the nested runtime root
  (``qubit_runtime/pm/lock.json``) instead of assuming the checkout *is* the
  runtime.

No Windows host, network, service or live config is touched.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ROOT_PS1 = REPO / "scripts" / "install.ps1"
RUNTIME_PS1 = REPO / "qubit_runtime" / "scripts" / "install.ps1"
MACHINE = REPO / "qubit_runtime" / "tests" / "e2e" / "core" / "windows_update" / "_machine.py"


def _root() -> str:
    return ROOT_PS1.read_text(encoding="utf-8")


def _runtime() -> str:
    return RUNTIME_PS1.read_text(encoding="utf-8")


def _machine() -> str:
    return MACHINE.read_text(encoding="utf-8")


def _generated_pins(src: str) -> str:
    start = src.index("# --- BEGIN GENERATED: bootstrap pins")
    end = src.index("# --- END GENERATED: bootstrap pins") + len("# --- END GENERATED: bootstrap pins")
    block = src[start:end]
    # Normalise quoting/comments so the two generated blocks compare structurally.
    lines = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(re.sub(r"\s+", " ", stripped.replace('"', "'")))
    return "\n".join(lines)


def test_root_installer_carries_the_same_pinned_toolchain_as_the_runtime():
    assert _generated_pins(_root()) == _generated_pins(_runtime())


def test_root_installer_provisions_uv_and_git_from_the_pins():
    src = _root()
    assert "function Get-Uv" in src
    assert "function Get-PinnedGit" in src
    assert "function Ensure-Git" in src
    assert "$script:UvPinFiles" in src and "$script:GitPinFiles" in src
    # The pinned release URL, never astral-latest.
    assert "astral-sh/uv/releases/download/0.13.5" in src
    assert "git-for-windows/git/releases/download/v2.53.0.windows.3" in src
    assert "astral.sh/uv/install.ps1" not in src


def test_root_installer_does_not_require_uv_on_path():
    src = _root()
    assert "if (-not (Get-CommandPath 'uv'))" not in src, (
        "the root installer must stage the pinned uv instead of failing on a "
        "clean machine with no uv.exe"
    )
    # The typed UV class is preserved for a host with no pinned artifact.
    assert "Fail-Prerequisite 'UV'" in src
    assert "$script:UvPinFiles[\"win32-$(Get-WindowsArch)\"]" in src


def test_root_installer_dry_run_never_stages_the_toolchain():
    src = _root()
    for name in ("Get-Uv", "Get-PinnedGit"):
        start = src.index(f"function {name}")
        end = src.find("\nfunction ", start + 1)
        body = src[start:] if end == -1 else src[start:end]
        assert "if ($DryRun) { return" in body, f"{name} must not download during -DryRun"


def test_root_installer_uses_the_resolved_uv_for_the_venv():
    src = _root()
    install = src[src.index("function Install-Venv"):]
    install = install[:install.index("function Install-Dashboard")]
    assert "$uv = Get-Uv" in install
    assert "& $uv venv" in install
    assert "& $uv pip install" in install
    assert re.search(r"^\s*& uv ", install, re.M) is None, (
        "Install-Venv still invokes a bare `uv` from PATH"
    )


def test_root_installer_writes_the_bootstrap_receipt():
    src = _root()
    assert "function Write-CompletionMarker" in src
    assert ".qubit-bootstrap-complete" in src
    assert ".hermes-bootstrap-complete" in src  # legacy read-old alias
    assert "Write-CompletionMarker" in src[src.index("function Invoke-QubitInstall"):]


def test_root_installer_parks_a_broken_checkout_instead_of_reusing_it():
    src = _root()
    init = src[src.index("function Initialize-Checkout"):]
    init = init[:init.index("function Resolve-Source")]
    assert "rev-parse --verify HEAD" in init
    assert "$broken = \"$Target.broken-" in init


def test_runtime_installer_resolves_the_nested_runtime_root():
    src = _runtime()
    assert "function Get-RuntimeRoot" in src
    assert "Join-Path $InstallDir 'qubit_runtime'" in src
    assert "pm\\lock.json" in src
    bootstrap = src[src.index("function Get-BootstrapPython"):]
    bootstrap = bootstrap[:bootstrap.index("function Invoke-BootstrapPm")]
    assert "$runtimeRoot = Get-RuntimeRoot" in bootstrap
    assert "$lockPath = Join-Path $runtimeRoot \"pm\\lock.json\"" in bootstrap


def test_runtime_installer_takes_runtime_relative_work_through_the_nested_root():
    src = _runtime()
    # Completion, launcher publication, pm invocation and the desktop product all
    # live in the runtime subtree, not the umbrella root.
    assert "'--source', $runtimeRoot" in src
    assert "Push-Location (Get-RuntimeRoot)" in src
    assert "Join-Path (Get-RuntimeRoot) \"apps/desktop/release\"" in src


def test_windows_e2e_harness_selects_one_installer_family():
    src = _machine()
    # The public seed nests the runtime; the harness installs with the runtime
    # installer and keeps the git history on the umbrella checkout.
    assert "INSTALL_ROOT = Path(__file__).resolve().parents[4]" in src
    assert 'INSTALL_ROOT / "scripts" / "install.ps1"' in src
    assert 'runtime_installer = "qubit_runtime/scripts/install.ps1"' in src
    assert 'f"{self.head}:{rel}"' in src
    # Both the current tree and the historical upgrade use that same family.
    assert '"QUBIT_INSTALL_CHANNEL": "main"' in src
    assert '"HERMES_INSTALL_CHANNEL": "main"' in src  # vendored installer reads the legacy name


def test_windows_e2e_harness_redirects_the_public_qubit_origin():
    src = _machine()
    assert '"https://github.com/cgraph-dev/Qubit.git"' in src
    assert "NousResearch" not in src, (
        "the installers clone cgraph-dev/Qubit; an unrewritten upstream URL "
        "would leak a real network clone (or hit the source refusal)"
    )


def test_windows_e2e_harness_models_the_qubit_public_home():
    src = _machine()
    assert 'return self.local / "qubit"' in src
    assert 'return self.qubit_home / "qubit"' in src
    assert '"venv" / "Scripts" / "qubit.exe"' in src
    assert '"hermes.exe"' not in src


def test_windows_e2e_harness_preserves_the_clean_machine_premise():
    src = _machine()
    assert '("python.exe", "python3.exe", "uv.exe")' in src
    assert '("git.exe",)' in src
    # QUBIT_HOME is not injected, so the installer resolves its own default.
    assert '"QUBIT_HOME":' not in src


def _powershell_host() -> str | None:
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


def test_root_installer_picks_the_pinned_uv_when_path_has_none(tmp_path: Path):
    host = _powershell_host()
    if not host:
        pytest.skip("no PowerShell host (pwsh/powershell) on this runner")

    home = tmp_path / "home"
    empty = tmp_path / "empty-path"
    empty.mkdir()
    harness = tmp_path / "resolve-uv.ps1"
    harness.write_text(
        f"$env:PATH = '{empty}'\n"
        f". '{ROOT_PS1}' -QubitHome '{home}' -DryRun\n"
        "Resolve-Layout | Out-Null\n"
        "Write-Output (Get-Uv)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        capture_output=True, text=True, timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    resolved = result.stdout.strip()
    assert "uv-0.13.5-win32-x64" in resolved
    assert resolved.endswith("uv.exe")
    assert not Path(resolved).exists(), "-DryRun must not download the pinned uv"


def test_root_installer_keeps_a_usable_uv_already_on_path(tmp_path: Path):
    host = _powershell_host()
    if not host:
        pytest.skip("no PowerShell host (pwsh/powershell) on this runner")

    bindir = tmp_path / "bindir"
    bindir.mkdir()
    fake = bindir / "uv"
    fake.write_text("#!/bin/sh\necho 'uv 9.9.9'\n", encoding="utf-8")
    fake.chmod(0o755)
    home = tmp_path / "home"
    harness = tmp_path / "resolve-uv-path.ps1"
    harness.write_text(
        f"$env:PATH = '{bindir}'\n"
        f". '{ROOT_PS1}' -QubitHome '{home}' -DryRun\n"
        "Resolve-Layout | Out-Null\n"
        "Write-Output (Get-Uv)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        capture_output=True, text=True, timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(result.stdout.strip()) == fake


def test_runtime_installer_resolves_the_nested_root_at_runtime(tmp_path: Path):
    host = _powershell_host()
    if not host:
        pytest.skip("no PowerShell host (pwsh/powershell) on this runner")

    def resolve(checkout: Path) -> str:
        harness = tmp_path / f"resolve-{checkout.name}.ps1"
        harness.write_text(
            f". '{RUNTIME_PS1}' -InstallDir '{checkout}'\n"
            "Write-Output (Get-RuntimeRoot)\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
            capture_output=True, text=True, timeout=120,
            stdin=subprocess.DEVNULL,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    umbrella = tmp_path / "umbrella"
    (umbrella / "qubit_runtime" / "pm").mkdir(parents=True)
    (umbrella / "qubit_runtime" / "pm" / "lock.json").write_text("{}", encoding="utf-8")
    assert Path(resolve(umbrella)) == umbrella / "qubit_runtime"

    upstream = tmp_path / "upstream"
    (upstream / "pm").mkdir(parents=True)
    (upstream / "pm" / "lock.json").write_text("{}", encoding="utf-8")
    assert Path(resolve(upstream)) == upstream


def test_both_installers_parse_with_the_real_powershell_parser(tmp_path: Path):
    host = _powershell_host()
    if not host:
        pytest.skip("no PowerShell host (pwsh/powershell) on this runner")
    harness = tmp_path / "parse-windows-installers.ps1"
    harness.write_text(
        "param([string[]]$Paths)\n"
        "$failed = 0\n"
        "foreach ($p in $Paths) {\n"
        "  $tokens = $null; $errors = $null\n"
        "  [System.Management.Automation.Language.Parser]::ParseFile("
        "$p, [ref]$tokens, [ref]$errors) | Out-Null\n"
        "  if ($errors.Count) {\n"
        "    $failed = 1\n"
        "    $errors | Select-Object -First 8 | ForEach-Object {\n"
        "      Write-Output ('  {0}: {1}' -f $_.Extent.StartLineNumber, $_.Message)\n"
        "    }\n"
        "  }\n"
        "}\n"
        "if ($failed) { exit 1 }\n"
        "Write-Output 'PS SYNTAX OK'\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(harness), str(ROOT_PS1), str(RUNTIME_PS1)],
        capture_output=True, text=True, timeout=180,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PS SYNTAX OK" in result.stdout
