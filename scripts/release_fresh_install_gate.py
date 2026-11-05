#!/usr/bin/env python3
"""Release gate: a Qubit release must satisfy the fresh-install contract.

This is the gate that makes the v0.0.6+ production release impossible to
prepare or verify without the assets a fresh install actually needs.  It is
read-only with respect to the checkout, services and remotes, and hermetic
apart from the explicitly requested ``--fresh-install`` probe (which installs
into a throwaway prefix/HOME and may use the network for Python dependencies).

Checks (each one typed PASS / FAIL / DEGRADED):

* the dashboard bundle and ``qubit_runtime/ui-tui/dist/entry.js`` exist (in the
  checkout, in a wheel, or in the release directory);
* the packaged TUI launcher resolves the bundled ``dist/entry.js`` without
  calling ``scripts/build/node-deps.mjs`` or rebuilding from source;
* the shipped ``cli-config.yaml.example`` names a working local Ollama default,
  and the POSIX/Windows installers seed ``<QUBIT_HOME>/config.yaml`` from it;
* the POSIX assets (``bootstrap.sh``, ``install.sh``, ``qubit-release.tar.gz``,
  ``SHA256SUMS``) and the Windows assets (``bootstrap.ps1``, ``install.ps1``,
  ``qubit-release.zip``) are present and checksum-consistent;
* ``scripts/verify_public_release.py`` passes against the built release;
* ``--fresh-install`` performs a real install into a throwaway prefix and
  asserts the root ``config.yaml`` exists with the Ollama provider.  Without
  ``--fresh-install`` that check is recorded as DEGRADED, never as a false
  PASS.  Release preparation runs it with ``--require-fresh-install`` so a
  DEGRADED result blocks the release.

Usage::

    python3 scripts/release_fresh_install_gate.py --repo .
    python3 scripts/release_fresh_install_gate.py \
        --release-dir /tmp/qubit-production-release --fresh-install --json

Exit codes: 0 PASS, 2 FAIL, 4 DEGRADED, 8 BLOCKED (missing required input).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SCHEMA = "qubit.release-fresh-install-gate/1"

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_DEGRADED = 4
EXIT_BLOCKED = 8

POSIX_ASSETS = ("bootstrap.sh", "install.sh", "qubit-release.tar.gz", "SHA256SUMS")
WINDOWS_ASSETS = ("bootstrap.ps1", "install.ps1", "qubit-release.zip")
REQUIRED_ASSETS = POSIX_ASSETS + tuple(
    name for name in WINDOWS_ASSETS if name not in POSIX_ASSETS
)
CHECKSUM_RE = re.compile(r"^([0-9a-fA-F]{64}) [ *](.+)$")
DASHBOARD_SUFFIX = "frontend/qubit-dist/index.html"
TUI_DIST_SUFFIX = "qubit_runtime/ui-tui/dist/entry.js"


class GateError(SystemExit):
    """Typed refusal; the message is printed verbatim."""


@dataclass
class Check:
    name: str
    state: str
    detail: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def add(self, name: str, state: str, detail: str = "") -> None:
        if state not in ("OK", "FAIL", "DEGRADED"):
            raise GateError("GATE_BAD_STATE: %s=%s" % (name, state))
        self.checks.append(Check(name=name, state=state, detail=detail))

    @property
    def failed(self) -> list[Check]:
        return [check for check in self.checks if check.state == "FAIL"]

    @property
    def degraded(self) -> list[Check]:
        return [check for check in self.checks if check.state == "DEGRADED"]

    @property
    def verdict(self) -> str:
        if self.failed:
            return "FAIL"
        if self.degraded:
            return "DEGRADED"
        return "PASS"

    def as_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "verdict": self.verdict,
            "context": self.context,
            "checks": [
                {"name": check.name, "state": check.state, "detail": check.detail}
                for check in self.checks
            ],
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise GateError("GATE_IMPORT_FAILED: %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001 - typed refusal
        raise GateError("GATE_YAML_UNREADABLE: %s: %s" % (path, exc)) from exc


def _config_contract(example: Path) -> tuple[bool, str]:
    """Return (ok, detail) for the default local Ollama provider contract."""
    if not example.is_file():
        return False, "missing %s" % example
    data = _load_yaml(example)
    model = data.get("model") or {}
    if not isinstance(model, dict):
        return False, "model section is not a mapping"
    provider = str(model.get("provider") or "").strip().lower()
    base_url = str(model.get("base_url") or "").strip().lower()
    default = str(model.get("default") or model.get("model") or "").strip()
    if not default:
        return False, "model.default is empty"
    local_ollama = (
        provider == "ollama"
        or provider == "local"
        or (provider == "custom" and "11434" in base_url)
    )
    if not local_ollama:
        return (
            False,
            "model.provider=%r base_url=%r is not a working local Ollama default"
            % (provider, base_url),
        )
    return True, "provider=%s model=%s" % (provider, default)


def _installer_seeds_root_config(installer: Path) -> tuple[bool, str]:
    if not installer.is_file():
        return False, "missing %s" % installer
    text = installer.read_text(encoding="utf-8", errors="replace")
    mentions_template = (
        "cli-config.yaml.example" in text
        or "cli-config.yaml" in text
    )
    mentions_root = "config.yaml" in text
    if mentions_template and mentions_root:
        return True, "seeds root config.yaml from cli-config.yaml.example"
    missing = []
    if not mentions_template:
        missing.append("cli-config.yaml.example")
    if not mentions_root:
        missing.append("config.yaml")
    return False, "installer never creates the root config.yaml (missing %s)" % (
        ", ".join(missing)
    )


def _find_dashboard(root: Path) -> Path | None:
    candidate = root / DASHBOARD_SUFFIX
    return candidate if candidate.is_file() else None


def _find_tui_dist(root: Path) -> Path | None:
    candidate = root / TUI_DIST_SUFFIX
    return candidate if candidate.is_file() else None


def _load_tui_launcher(runtime_root: Path):
    """Load ``qubit_cli.main_tui_launch`` from a runtime root without polluting sys.path."""
    previous = list(sys.path)
    previous_bytecode = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(runtime_root))
        from qubit_cli import main_tui_launch as module

        return module
    finally:
        sys.path[:] = previous
        sys.dont_write_bytecode = previous_bytecode


def _packaged_tui_decision(module, tmp: Path) -> tuple[bool, str]:
    """Prove a packaged tree resolves dist/entry.js without the source build."""
    tui_dir = tmp / "packaged" / "ui-tui"
    dist = tui_dir / "dist"
    dist.mkdir(parents=True)
    entry = dist / "entry.js"
    entry.write_text("// packaged TUI entry\n", encoding="utf-8")

    def _forbidden_workspace(*_args, **_kwargs):
        raise AssertionError("packaged launch required the source TUI workspace")

    def _forbidden_rebuild(*_args, **_kwargs):
        raise AssertionError("packaged launch tried to rebuild the TUI from source")

    def _no_bundled(*_args, **_kwargs):
        return None

    previous = os.environ.get("HERMES_TUI_DIR")
    try:
        for key in list(os.environ):
            if key == "HERMES_TUI_DIR":
                del os.environ[key]
        with mock.patch.object(module, "_ensure_tui_workspace", _forbidden_workspace), \
                mock.patch.object(module, "_tui_need_rebuild", _forbidden_rebuild), \
                mock.patch.object(module, "_find_bundled_tui", _no_bundled), \
                mock.patch.object(module, "_tui_node_bin", lambda _bin: "/nonexistent/node"):
            argv, cwd = module._make_tui_argv(tui_dir, False)
    finally:
        if previous is not None:
            os.environ["HERMES_TUI_DIR"] = previous
    joined = " ".join(str(item) for item in argv)
    if str(entry) not in joined or str(entry.parent) not in joined:
        return False, "packaged launch did not select %s: %r" % (entry, argv)
    if "node-deps.mjs" in joined:
        return False, "packaged launch still requires scripts/build/node-deps.mjs"
    if cwd != tui_dir:
        return False, "packaged launch cwd=%s expected=%s" % (cwd, tui_dir)
    return True, "packaged TUI runs dist/entry.js directly"


def check_runtime_tree(repo: Path, report: Report, *, require_dashboard: bool = True) -> None:
    """Check the checkout/seed tree's product assets and config contract."""
    if require_dashboard:
        dashboard = _find_dashboard(repo)
        report.add(
            "dashboard_assets",
            "OK" if dashboard else "FAIL",
            str(dashboard) if dashboard else "missing %s" % DASHBOARD_SUFFIX,
        )
    # A source/seed tree does not carry frontend/qubit-dist by design; the
    # wheel built from it is checked separately.

    tui = _find_tui_dist(repo)
    report.add(
        "tui_dist_entry",
        "OK" if tui else "FAIL",
        str(tui) if tui else "missing %s" % TUI_DIST_SUFFIX,
    )

    if tui:
        try:
            module = _load_tui_launcher(repo / "qubit_runtime")
            with tempfile.TemporaryDirectory(prefix="qubit-tui-gate-") as tmp:
                ok, detail = _packaged_tui_decision(module, Path(tmp))
            report.add("packaged_tui_no_source_build", "OK" if ok else "FAIL", detail)
        except Exception as exc:  # noqa: BLE001 - typed gate result
            report.add("packaged_tui_no_source_build", "DEGRADED",
                       "could not exercise the launcher: %s" % exc)
    else:
        report.add("packaged_tui_no_source_build", "FAIL",
                   "no TUI dist to exercise")

    example = repo / "qubit_runtime" / "cli-config.yaml.example"
    ok, detail = _config_contract(example)
    report.add("config_default_ollama", "OK" if ok else "FAIL", detail)

    for name in ("install.sh", "install.ps1"):
        installer = repo / "scripts" / name
        ok, detail = _installer_seeds_root_config(installer)
        report.add(
            "installer_%s_seeds_config" % name.replace(".", "_"),
            "OK" if ok else "FAIL",
            detail,
        )

    for name in ("bootstrap.sh", "bootstrap.ps1", "install.sh", "install.ps1"):
        path = repo / "scripts" / name
        report.add(
            "asset_script_%s" % name.replace(".", "_"),
            "OK" if path.is_file() else "FAIL",
            str(path) if path.is_file() else "missing %s" % path,
        )


def check_wheel(wheel: Path, report: Report) -> None:
    """Check a built wheel carries the dashboard, TUI dist, and packaged launcher."""
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
    except Exception as exc:  # noqa: BLE001 - typed refusal
        report.add("wheel_readable", "FAIL", "%s: %s" % (wheel, exc))
        return
    report.add("wheel_readable", "OK", str(wheel))

    dashboard = [name for name in names if name.endswith(DASHBOARD_SUFFIX)]
    report.add(
        "wheel_dashboard_assets",
        "OK" if dashboard else "FAIL",
        dashboard[0] if dashboard else "missing %s" % DASHBOARD_SUFFIX,
    )
    tui = [name for name in names if name.endswith(TUI_DIST_SUFFIX)]
    report.add(
        "wheel_tui_dist_entry",
        "OK" if tui else "FAIL",
        tui[0] if tui else "missing %s" % TUI_DIST_SUFFIX,
    )
    launcher = [name for name in names if name.endswith("qubit_cli/main_tui_launch.py")]
    report.add(
        "wheel_tui_launcher",
        "OK" if launcher else "FAIL",
        launcher[0] if launcher else "missing qubit_cli/main_tui_launch.py",
    )
    if not (dashboard and tui and launcher):
        return
    with tempfile.TemporaryDirectory(prefix="qubit-wheel-gate-") as tmp:
        root = Path(tmp)
        try:
            with zipfile.ZipFile(wheel) as archive:
                archive.extractall(root)
        except Exception as exc:  # noqa: BLE001 - typed refusal
            report.add("wheel_packaged_tui", "DEGRADED",
                       "could not extract wheel: %s" % exc)
            return
        runtime_root = root / "voicebridge" / "_data" / "qubit_runtime"
        try:
            module = _load_tui_launcher(runtime_root)
            ok, detail = _packaged_tui_decision(module, root / "sim")
        except Exception as exc:  # noqa: BLE001 - typed gate result
            report.add("wheel_packaged_tui", "DEGRADED",
                       "could not exercise the packaged launcher: %s" % exc)
            return
        report.add("wheel_packaged_tui", "OK" if ok else "FAIL", detail)


def parse_checksums(text: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = CHECKSUM_RE.match(line)
        if not match:
            raise GateError("GATE_BAD_CHECKSUM_LINE: %r" % raw)
        digest, name = match.group(1).lower(), match.group(2).strip()
        if name in entries:
            raise GateError("GATE_DUPLICATE_CHECKSUM: %s" % name)
        entries[name] = digest
    if not entries:
        raise GateError("GATE_EMPTY_SHA256SUMS")
    return entries


def _archive_installer_sha(archive: Path, suffix: str) -> str:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            matches = [name for name in bundle.namelist()
                       if name == suffix or name.endswith("/" + suffix)]
            if not matches:
                raise GateError("GATE_ARCHIVE_INSTALLER_MISSING: %s" % archive.name)
            name = sorted(matches, key=lambda item: (len(item), item))[0]
            return hashlib.sha256(bundle.read(name)).hexdigest()
    with tarfile.open(archive, "r:gz") as bundle:
        matches = [member for member in bundle.getmembers()
                   if member.name == suffix or member.name.endswith("/" + suffix)]
        if not matches:
            raise GateError("GATE_ARCHIVE_INSTALLER_MISSING: %s" % archive.name)
        member = sorted(matches, key=lambda item: (len(item.name), item.name))[0]
        handle = bundle.extractfile(member)
        if handle is None:
            raise GateError("GATE_ARCHIVE_MEMBER_UNREADABLE: %s" % member.name)
        return hashlib.sha256(handle.read()).hexdigest()


def check_release_dir(release_dir: Path, repo: Path | None, report: Report,
                      *, timeout: float = 120.0) -> None:
    if not release_dir.is_dir():
        report.add("release_dir", "FAIL", "missing %s" % release_dir)
        return
    report.add("release_dir", "OK", str(release_dir))

    missing = [name for name in REQUIRED_ASSETS if not (release_dir / name).is_file()]
    report.add(
        "release_assets",
        "OK" if not missing else "FAIL",
        "missing %s" % ", ".join(missing) if missing else
        "POSIX and Windows assets present",
    )
    if missing:
        return

    try:
        checksums = parse_checksums(
            (release_dir / "SHA256SUMS").read_text(encoding="utf-8")
        )
    except GateError as exc:
        report.add("release_checksums", "FAIL", str(exc))
        return
    # SHA256SUMS cannot name itself; existence is already checked above.
    uncovered = [
        name for name in REQUIRED_ASSETS
        if name != "SHA256SUMS" and name not in checksums
    ]
    bad = []
    for name, expected in checksums.items():
        path = release_dir / name
        if not path.is_file():
            bad.append("%s missing" % name)
            continue
        actual = _sha256(path)
        if actual != expected:
            bad.append("%s sha256 mismatch" % name)
    detail = []
    if uncovered:
        detail.append("uncovered=%s" % ", ".join(uncovered))
    if bad:
        detail.append("bad=%s" % ", ".join(bad))
    report.add(
        "release_checksums",
        "OK" if not detail else "FAIL",
        "; ".join(detail) if detail else "all assets checksum-verified",
    )
    if detail:
        return

    for label, installer, archive, suffix in (
        ("posix", "install.sh", "qubit-release.tar.gz", "scripts/install.sh"),
        ("windows", "install.ps1", "qubit-release.zip", "scripts/install.ps1"),
    ):
        try:
            embedded = _archive_installer_sha(release_dir / archive, suffix)
            expected = _sha256(release_dir / installer)
            report.add(
                "archive_%s_installer" % label,
                "OK" if embedded == expected else "FAIL",
                "%s %s=%s asset=%s" % (archive, suffix, embedded, expected),
            )
        except GateError as exc:
            report.add("archive_%s_installer" % label, "FAIL", str(exc))

    if repo is not None:
        check_runtime_tree(repo, report, require_dashboard=False)

    wheel = sorted(release_dir.glob("*.whl"))
    if wheel:
        check_wheel(wheel[0], report)
    else:
        report.add("wheel_present", "FAIL", "no .whl in %s" % release_dir)

    try:
        verifier = _load_module(
            "release_public_verifier", REPO / "scripts" / "verify_public_release.py"
        )
        result = verifier.verify(
            repo=None,
            tag=None,
            latest=False,
            release_base=str(release_dir),
            api_base=verifier.DEFAULT_API_BASE,
            version=None,
            expect_source_commit=None,
            timeout=timeout,
        )
        failed = result.failed
        report.add(
            "verify_public_release",
            "OK" if not failed else "FAIL",
            "PASS (%d checks)" % len(result.checks) if not failed else
            "failed: " + ", ".join(check.name for check in failed[:10]),
        )
    except Exception as exc:  # noqa: BLE001 - typed gate result
        report.add("verify_public_release", "FAIL", str(exc))


def _safe_extract_tar(archive: Path, dest: Path) -> None:
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest.resolve()) + os.sep):
                raise GateError("GATE_UNSAFE_ARCHIVE_PATH: %s" % member.name)
        bundle.extractall(dest)


def _install_root(tree: Path) -> Path | None:
    if (tree / "scripts" / "install.sh").is_file():
        return tree
    children = [child for child in tree.iterdir() if child.is_dir()]
    for child in children:
        if (child / "scripts" / "install.sh").is_file():
            return child
    return None


def _provider_from_config(path: Path) -> tuple[bool, str]:
    data = _load_yaml(path)
    model = data.get("model") or {}
    if not isinstance(model, dict):
        return False, "model section missing"
    provider = str(model.get("provider") or "").strip().lower()
    base_url = str(model.get("base_url") or "").strip().lower()
    default = str(model.get("default") or model.get("model") or "").strip()
    if provider == "ollama" or provider == "local" or (
        provider == "custom" and "11434" in base_url
    ):
        return True, "provider=%s model=%s" % (provider, default or "(unset)")
    return False, "provider=%r base_url=%r" % (provider, base_url)


def check_fresh_install(source: Path | None, report: Report,
                        *, require: bool) -> None:
    if source is None:
        state = "FAIL" if require else "DEGRADED"
        report.add("fresh_install", state,
                   "no release archive or --repo source to install")
        return
    missing_tools = [tool for tool in ("bash", "curl", "uv")
                     if shutil.which(tool) is None]
    if missing_tools:
        state = "FAIL" if require else "DEGRADED"
        report.add("fresh_install", state,
                   "missing prerequisite tools: %s" % ", ".join(missing_tools))
        return

    with tempfile.TemporaryDirectory(prefix="qubit-fresh-install-") as tmp:
        root = Path(tmp)
        tree = source
        if source.is_file():
            extracted = root / "src"
            extracted.mkdir()
            if source.suffix == ".zip":
                with zipfile.ZipFile(source) as bundle:
                    bundle.extractall(extracted)
            else:
                _safe_extract_tar(source, extracted)
            found = _install_root(extracted)
            if found is None:
                report.add("fresh_install", "FAIL",
                           "archive does not contain scripts/install.sh")
                return
            tree = found
        elif not (source / "scripts" / "install.sh").is_file():
            report.add("fresh_install", "FAIL",
                       "%s has no scripts/install.sh" % source)
            return

        home = root / "home"
        prefix = root / "prefix"
        home.mkdir()
        env = {
            **os.environ,
            "HOME": str(home),
            "QUBIT_HOME": str(prefix),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "CI": "1",
        }
        command = [
            "bash",
            str(tree / "scripts" / "install.sh"),
            "--prefix",
            str(prefix),
            "--without-models",
            "--without-dashboard",
            "--yes",
        ]
        try:
            proc = subprocess.run(
                command,
                cwd=str(tree),
                env=env,
                capture_output=True,
                text=True,
                timeout=3600,
            )
        except subprocess.TimeoutExpired:
            report.add("fresh_install", "FAIL" if require else "DEGRADED",
                       "install timed out")
            return
        if proc.returncode != 0:
            tail = ((proc.stdout or "") + (proc.stderr or "")).strip()[-1200:]
            report.add(
                "fresh_install",
                "FAIL" if require else "DEGRADED",
                "installer exit %d: %s" % (proc.returncode, tail),
            )
            return
        config = prefix / "config.yaml"
        report.add(
            "fresh_install_root_config",
            "OK" if config.is_file() else "FAIL",
            str(config) if config.is_file() else
            "fresh install did not create %s" % config,
        )
        if not config.is_file():
            return
        ok, detail = _provider_from_config(config)
        report.add("fresh_install_ollama_provider", "OK" if ok else "FAIL", detail)


def run_gate(
    *,
    repo: Path,
    release_dir: Path | None = None,
    wheel: Path | None = None,
    fresh_install: bool = False,
    require_fresh_install: bool = False,
    require_dashboard: bool = True,
    timeout: float = 120.0,
) -> Report:
    report = Report(context={
        "repo": str(repo),
        "release_dir": str(release_dir) if release_dir else None,
        "wheel": str(wheel) if wheel else None,
        "fresh_install": bool(fresh_install or require_fresh_install),
        "require_fresh_install": bool(require_fresh_install),
    })
    check_runtime_tree(repo, report, require_dashboard=require_dashboard)

    if wheel is None and release_dir is not None:
        wheels = sorted(release_dir.glob("*.whl"))
        wheel = wheels[0] if wheels else None
    if wheel is not None:
        check_wheel(wheel, report)
    elif release_dir is not None:
        report.add("wheel_present", "FAIL", "no .whl in %s" % release_dir)

    if release_dir is not None:
        check_release_dir(release_dir, None if wheel is not None else repo,
                          report, timeout=timeout)

    if fresh_install or require_fresh_install:
        if release_dir is not None and (release_dir / "qubit-release.tar.gz").is_file():
            source: Path | None = release_dir / "qubit-release.tar.gz"
        else:
            source = repo
        check_fresh_install(source, report, require=require_fresh_install)
    else:
        report.add("fresh_install", "DEGRADED",
                   "not exercised (pass --fresh-install)")
    return report


def _print_report(report: Report, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
        return
    for check in report.checks:
        line = "%s %s" % (check.state, check.name)
        if check.detail:
            line += " " + check.detail
        print(line)
    failed = len(report.failed)
    degraded = len(report.degraded)
    print(
        "%s qubit-release-fresh-install-gate checks=%d failed=%d degraded=%d"
        % (report.verdict, len(report.checks), failed, degraded)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gate a Qubit release on the fresh-install contract."
    )
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--release-dir", type=Path, default=None,
                        help="built production release directory to verify")
    parser.add_argument("--wheel", type=Path, default=None,
                        help="built wheel to inspect (defaults to the one in --release-dir)")
    parser.add_argument("--fresh-install", action="store_true",
                        help="install into a throwaway prefix and inspect config.yaml")
    parser.add_argument("--require-fresh-install", action="store_true",
                        help="a fresh install that cannot run is a FAIL, not DEGRADED")
    parser.add_argument("--no-dashboard", action="store_true",
                        help="do not require frontend/qubit-dist in --repo (seed/source tree)")
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="public verifier HTTP/local fetch timeout")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    repo = args.repo.expanduser().resolve()
    release_dir = args.release_dir.expanduser().resolve() if args.release_dir else None
    wheel = args.wheel.expanduser().resolve() if args.wheel else None

    try:
        report = run_gate(
            repo=repo,
            release_dir=release_dir,
            wheel=wheel,
            fresh_install=args.fresh_install,
            require_fresh_install=args.require_fresh_install,
            require_dashboard=not args.no_dashboard,
            timeout=args.timeout,
        )
    except GateError as exc:
        print("BLOCKED qubit-release-fresh-install-gate %s" % exc, file=sys.stderr)
        return EXIT_BLOCKED

    _print_report(report, args.json)
    if report.verdict == "FAIL":
        return EXIT_FAIL
    if report.verdict == "DEGRADED":
        return EXIT_DEGRADED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
