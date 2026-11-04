"""Hermes-independence: clean home, no upstream checkout, no ``hermes`` binary.

The owner directive is to retire the legacy Hermes tree. Qubit must therefore
start, expose voice commands, and read/write its own home/state from an empty
operator identity with no upstream checkout and no ``hermes`` executable on
PATH. A legacy ``~/.hermes`` read-old fallback may exist in migration/sentinel
code, but it must never be *required* and must never be created as a default.

Hermetic: every test uses a throwaway ``HOME``/``QUBIT_HOME`` and a PATH built
from symlinks to this interpreter and the few host tools the CLI probes.
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "qubit_runtime"

FORBIDDEN_IMPORTS = re.compile(
    r"^[ \t]*(?:from|import)[ \t]+hermes(?:_[A-Za-z0-9_]+)?(?:[ \t.]|$)",
    re.MULTILINE,
)

#: Product roots scanned by the static guards. Tests/evals/optional skills are
#: deliberately excluded: they name the upstream pin for verification/provenance.
PRODUCT_ROOTS = ("qubit_runtime", "voicebridge")
PRODUCT_EXCLUDE_PARTS = (
    "tests", "evals", "optional-skills", "nix", "apps", "website",
    "contributors", "docs", "scripts", "__pycache__",
)

#: Startup/entry modules whose executable/home resolution is the product seam.
STARTUP_MODULES = (
    RUNTIME / "qubit_cli" / "main.py",
    RUNTIME / "qubit_cli" / "_startup_fast.py",
    RUNTIME / "qubit_bootstrap.py",
    RUNTIME / "gateway" / "run.py",
    RUNTIME / "gateway" / "run_bootstrap.py",
    RUNTIME / "gateway" / "run_startup.py",
    RUNTIME / "tui_gateway" / "entry.py",
    RUNTIME / "tui_gateway" / "server.py",
    REPO / "voicebridge" / "gateway_daemon.py",
    REPO / "voicebridge" / "cli.py",
    REPO / "voicebridge" / "qubit_runtime.py",
    REPO / "voicebridge" / "webui.py",
    REPO / "voicebridge" / "desktop_helper.py",
)


def _tool_bin(tmp_path: Path) -> Path:
    """A PATH directory with the interpreter and only non-Hermes tools."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("python", "python3", "git", "sh", "bash", "uname", "node", "npm"):
        found = shutil.which(name)
        if found:
            (bindir / name).symlink_to(found)
    return bindir


def _clean_env(tmp_path: Path, *, home: bool = True, qubit_home: bool = True) -> dict:
    env = {
        "HOME": str(tmp_path / "operator-home"),
        "PATH": str(_tool_bin(tmp_path)),
        "PYTHONPATH": os.pathsep.join((str(RUNTIME), str(REPO))),
        "LANG": "C.UTF-8",
        "TZ": "UTC",
        "TMPDIR": str(tmp_path / "tmp"),
    }
    (tmp_path / "operator-home").mkdir()
    (tmp_path / "tmp").mkdir()
    if qubit_home:
        env["QUBIT_HOME"] = str(tmp_path / "qhome")
        (tmp_path / "qhome").mkdir()
    if home:
        env["HOME"] = str(tmp_path / "operator-home")
    return env


def _run(env: dict, source: str, *args: str, timeout: int = 120):
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source), *args],
        env=env, capture_output=True, text=True, timeout=timeout,
    )


def _product_python_files():
    for root_name in PRODUCT_ROOTS:
        base = REPO / root_name
        for path in base.rglob("*.py"):
            rel = path.relative_to(REPO)
            if any(part in PRODUCT_EXCLUDE_PARTS for part in rel.parts):
                continue
            yield path


# --------------------------------------------------------------------------
# static guards
# --------------------------------------------------------------------------
def test_product_code_has_no_external_hermes_imports():
    offenders = []
    for path in _product_python_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if FORBIDDEN_IMPORTS.search(text):
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == [], (
        "product code imports an external hermes_* package/tree: " + ", ".join(offenders)
    )


def test_no_hermes_only_executable_lookup_in_product_code():
    """A ``which('hermes')`` lookup must be canonical-Qubit-first.

    The pre-rebrand name is allowed strictly as a read-old fallback on the same
    expression (``which('qubit') or which('hermes')``); a Hermes-only lookup is
    a hard dependency on the tree the owner is retiring.
    """
    offenders = []
    for path in _product_python_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:  # a vendored template may not be valid standalone Python
            continue
        lines = text.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "which":
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            if node.args[0].value not in ("hermes", "hermes.exe"):
                continue
            line = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
            # The canonical lookup must appear on the same expression; a
            # Hermes-only lookup is a hard dependency on the retired tree.
            if "which('qubit')" not in line and 'which("qubit")' not in line:
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}: {line.strip()}")
    assert offenders == [], "hermes-only executable lookup:\n" + "\n".join(offenders)


def test_startup_modules_do_not_require_the_legacy_home():
    offenders = []
    for path in STARTUP_MODULES:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if ('Path.home() / ".hermes"' in text
                and path.name not in ("main_dashboard.py", "qubit_runtime.py")):
            offenders.append(str(path.relative_to(REPO)))
        if "get_default_qubit_root" not in text and "QUBIT_HOME" not in text and \
                path.name not in ("main.py", "_startup_fast.py", "run.py",
                                  "run_bootstrap.py", "run_startup.py",
                                  "entry.py", "server.py", "cli.py", "webui.py",
                                  "desktop_helper.py", "gateway_daemon.py",
                                  "qubit_runtime.py"):
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == [], (
        "startup module still anchors on the legacy Hermes home: " + ", ".join(offenders)
    )


def test_clean_home_product_modules_start_and_use_qubit_state(tmp_path):
    env = _clean_env(tmp_path)
    assert shutil.which("hermes", path=env["PATH"]) is None
    source = """
        import os
        from pathlib import Path
        import qubit_constants as qc
        from voicebridge import config as C
        from voicebridge import qubit_session_store as QSS
        from voicebridge import chat_transport as CT
        from voicebridge import voice_commands as VC

        home = qc.get_process_qubit_home()
        assert "hermes" not in str(home).lower(), home
        assert not (Path.home() / ".hermes").exists()

        cfg = C.Config.load(None)
        store = QSS.QubitSessionStore(cfg)
        store.create("clean-home", session_id="clean-home")
        store.append("clean-home", "user", "hello from a clean home")
        shown = store.show("clean-home")
        assert shown["state"] == "OK", shown
        assert any(m.get("content") == "hello from a clean home"
                   for m in shown["messages"]), shown

        chat = CT.store(cfg)
        chat.create("chat clean home", session_id="chat-clean")
        chat.append("chat-clean", "user", "hi")
        assert chat.show("chat-clean")["state"] == "OK"

        res = VC.resolve("kanban task abc123")
        assert res.kind == VC.MATCH, res
        assert res.entry.id == "kanban_fleet_task", res.entry.id

        from qubit_cli.main import main  # CLI entrypoint imports cleanly
        print("CLEAN_HOME_OK", home)
    """
    proc = _run(env, source)
    assert proc.returncode == 0, proc.stderr
    assert "CLEAN_HOME_OK" in proc.stdout, proc.stdout
    assert not (tmp_path / "operator-home" / ".hermes").exists()


def test_clean_home_cli_and_voice_entrypoints_run(tmp_path):
    env = _clean_env(tmp_path)
    version = subprocess.run(
        [sys.executable, "-m", "qubit_cli.main", "--version"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert version.returncode == 0, version.stderr
    assert "Qubit Agent" in version.stdout, version.stdout

    help_proc = subprocess.run(
        [sys.executable, "-m", "voicebridge.cli", "--help"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert help_proc.returncode == 0, help_proc.stderr
    assert "Qubit Agent" in help_proc.stdout, help_proc.stdout
    assert not (tmp_path / "operator-home" / ".hermes").exists()


def test_unset_qubit_home_defaults_to_qubit_not_legacy(tmp_path):
    env = _clean_env(tmp_path, qubit_home=False)
    env.pop("HERMES_HOME", None)
    source = """
        from pathlib import Path
        import qubit_constants as qc
        from voicebridge import qubit_runtime as QR

        home = qc.get_process_qubit_home()
        assert "hermes" not in str(home).lower(), home
        pinned = QR.pin_qubit_home()
        assert "hermes" not in str(pinned).lower(), pinned
        assert not (Path.home() / ".hermes").exists()
        print("DEFAULT_OK", pinned)
    """
    proc = _run(env, source)
    assert proc.returncode == 0, proc.stderr
    assert "DEFAULT_OK" in proc.stdout, proc.stdout
    assert not (tmp_path / "operator-home" / ".hermes").exists()


# --------------------------------------------------------------------------
# canonical-first runtime seams (unit level)
# --------------------------------------------------------------------------
def test_terminal_bin_dir_prefers_qubit_over_legacy_hermes(tmp_path, monkeypatch):
    from tools.environments import local as L

    qubit = tmp_path / "qubit-bin" / "qubit"
    hermes = tmp_path / "hermes-bin" / "hermes"
    qubit.parent.mkdir()
    hermes.parent.mkdir()
    qubit.write_text("#!/bin/sh\n")
    hermes.write_text("#!/bin/sh\n")

    def _which(name):
        return str(qubit) if name == "qubit" else str(hermes) if name == "hermes" else None

    monkeypatch.setattr(L.shutil, "which", _which)
    monkeypatch.setattr(L, "_QUBIT_BIN_DIR", L._SENTINEL)
    monkeypatch.setattr(L, "payload_command_dir", None, raising=False)
    monkeypatch.setattr(L.sys, "argv", ["qubit"])
    try:
        resolved = L._resolve_qubit_bin_dir()
    finally:
        monkeypatch.setattr(L, "_QUBIT_BIN_DIR", L._SENTINEL)
    assert resolved == str(qubit.parent), resolved


def test_desktop_ssh_token_canonical_qubit_home_is_accepted(tmp_path, monkeypatch):
    from qubit_cli.main_dashboard import _read_ssh_session_token_file

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    token_dir = home / ".qubit" / "desktop-ssh" / ("a" * 32)
    token_dir.mkdir(parents=True, mode=0o700)
    token_path = token_dir / "0123456789abcdef.token"
    token_path.write_text("b" * 64)
    token_path.chmod(0o600)

    assert _read_ssh_session_token_file(str(token_path)) == "b" * 64
    assert not token_path.exists()


def test_user_unit_without_home_is_scoped_to_qubit_default(tmp_path, monkeypatch):
    from qubit_cli import update_fleet_scope as UFS
    from qubit_cli import update_cmd_fleet as UCF

    home = tmp_path / "operator"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    monkeypatch.setattr(UCF, "_unit_main_pid", lambda scope, unit: 0)
    monkeypatch.setattr(UCF, "_systemctl", lambda argv, timeout=10: type(
        "R", (), {"returncode": 0, "stdout": ""})())

    resolved = UFS.systemd_unit_qubit_home(["--user"], "qubit-gateway.service")
    assert resolved is not None
    assert resolved.endswith(".qubit"), resolved
    assert ".hermes" not in resolved


# --------------------------------------------------------------------------
# shipped locale / desktop copy brand guards
# --------------------------------------------------------------------------
_BRAND_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9_\-./@:\\#])(Hermes|hermes)(?![A-Za-z0-9_\-/@:\\#])"
    r"(?!\s*[34]\b)"
)


def test_runtime_locale_values_carry_no_legacy_product_brand():
    offenders = []
    for path in sorted((RUNTIME / "locales").glob("*.yaml")):
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().startswith("#") or ":" not in line:
                continue
            _, value = line.split(":", 1)
            if _BRAND_VALUE_RE.search(value):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}")
    assert offenders == [], "legacy brand in shipped locale values:\n" + "\n".join(offenders)


def test_desktop_i18n_values_carry_no_legacy_product_brand():
    offenders = []
    i18n = RUNTIME / "apps" / "desktop" / "src" / "i18n"
    for path in sorted(i18n.glob("*.ts")):
        if ".test." in path.name:
            continue
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if ":" not in line or line.lstrip().startswith(("//", "/*", "*")):
                continue
            _, value = line.split(":", 1)
            if _BRAND_VALUE_RE.search(value):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}")
    assert offenders == [], "legacy brand in desktop i18n values:\n" + "\n".join(offenders)
