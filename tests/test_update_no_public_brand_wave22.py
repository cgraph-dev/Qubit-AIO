"""Wave 22 — no public brand in the Qubit update lifecycle.

Proves the owner directive: update receipts (the in-memory object and the
persisted JSON), ``_audit`` event strings, and the update modules' user-facing
string constants never carry a legacy public brand token. Internal
safety/migration identifiers may remain in code comments/docstrings.

Hermetic: every git command runs against a disposable temp repo under
``tmp_path``; HOME/XDG are isolated; no network, no product checkout, no
``~/.hermes``, no live service, no gateway daemon. The fail-closed test guard is
lifted only by injecting the executor, so nothing real runs.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import update_receipt as UR  # noqa: E402

# The owner's legacy public brand tokens; none may surface publicly.
LEGACY_RE = re.compile(
    r"hermes|openclaw|nous\s*research|nous\s*portal", re.IGNORECASE)

# The classified safety/legacy scrub pattern is the one permitted brand literal
# in update_receipt.py: it is the guard itself, never product copy.
_ALLOWED_LITERALS = {UR._LEGACY_BRAND_RE.pattern}

OWNED_MODULES = (
    "voicebridge/update_receipt.py",
    "voicebridge/update_handoff.py",
    "voicebridge/update_lock.py",
    "voicebridge/update_config_migration.py",
    "voicebridge/update_validation.py",
)

# Brand text a git remote/error path could capture — must be scrubbed.
BRAND_BLOB = ("\nfatal: remote https://hermes-agent.nousresearch.com/"
              "OpenClaw.git unreachable (Nous Research)\n")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
class _Cfg:
    """Nested-dict config implementing the ``.get(*path, default=)`` API."""

    def __init__(self, data=None):
        self._d = data or {}

    def get(self, *path, default=None):
        node = self._d
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def _git_env(tmp_path: Path) -> dict:
    empty = tmp_path / "gitconfig-empty"
    empty.write_text("")
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "Qubit Wave22",
        "GIT_AUTHOR_EMAIL": "wave22@example.invalid",
        "GIT_COMMITTER_NAME": "Qubit Wave22",
        "GIT_COMMITTER_EMAIL": "wave22@example.invalid",
        "GIT_CONFIG_GLOBAL": str(empty),
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def _git(args, cwd: Path, env: dict) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, env=env)
    assert proc.returncode == 0, (
        f"git {' '.join(args)} failed ({proc.returncode}): "
        f"{proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


def _commit(repo: Path, env: dict, text: str, message: str) -> str:
    (repo / "VERSION").write_text(text)
    # A pyproject.toml keeps the dependency-sync step from short-circuiting.
    (repo / "pyproject.toml").write_text(
        "[project]\nname = \"qubit-temp\"\nversion = \"0.0.0\"\n")
    _git(["add", "-A"], repo, env)
    _git(["commit", "-m", message], repo, env)
    return _git(["rev-parse", "HEAD"], repo, env)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Hermetic HOME/XDG and a clear e-stop; no live path is reachable."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / "qhome"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "xdg-state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "xdg-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / "xdg-cache"))
    monkeypatch.delenv("QUBIT_UPDATE_DISABLED", raising=False)
    from voicebridge import update_manager as UM

    monkeypatch.setattr(UM, "_estop", lambda _cfg: False)


@pytest.fixture
def install(tmp_path):
    """A temp install one commit behind its bare remote."""
    env = _git_env(tmp_path)
    bare = tmp_path / "remote.git"
    source = tmp_path / "source"
    checkout = tmp_path / "install"

    _git(["init", "--bare", "-b", "main", str(bare)], tmp_path, env)
    _git(["init", "-b", "main", str(source)], tmp_path, env)
    first = _commit(source, env, "1\n", "first")
    _git(["remote", "add", "origin", str(bare)], source, env)
    _git(["push", "-u", "origin", "main"], source, env)

    _git(["clone", str(bare), str(checkout)], tmp_path, env)
    second = _commit(source, env, "2\n", "second")
    _git(["push", "origin", "main"], source, env)

    assert first != second
    return SimpleNamespace(checkout=checkout, source=source, bare=bare,
                           env=env, first=first, second=second)


class _BrandRunner:
    """Real git in the temp checkout; appends brand text to ``git pull`` output."""

    def __init__(self, env: dict, brand: str = ""):
        self.env = env
        self.brand = brand
        self.calls = []

    def __call__(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        if not argv or argv[0] != "git":
            return 0, "", ""            # deps + import probe: succeed, no brand
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, env=self.env)
        out = proc.stdout
        if self.brand and argv[3:4] == ["pull"]:
            out = (out or "") + self.brand
        return proc.returncode, out, proc.stderr


def _cfg(install: SimpleNamespace, tmp_path: Path, **update) -> _Cfg:
    base = {
        "repo_root": str(install.checkout),
        "remote": "origin",
        "branch": "main",
        "backup_dir": str(tmp_path / "backups"),
        "lock_dir": str(tmp_path / "lock"),
        "handoff": False,
        "skip_validation": True,
    }
    base.update(update)
    return _Cfg({"update": base})


def _assert_brand_clean(text, label):
    m = LEGACY_RE.search(str(text))
    assert m is None, f"legacy brand {m.group(0)!r} in {label}: {text!r}"


# ---------------------------------------------------------------------------
# 1. receipts (in-memory + persisted) are brand-clean on a real full apply
# ---------------------------------------------------------------------------
def test_full_apply_receipt_is_brand_clean_in_memory_and_on_disk(install,
                                                                 tmp_path):
    from voicebridge import update_manager as UM

    runner = _BrandRunner(install.env, brand=BRAND_BLOB)
    receipt = UM.full_apply(_cfg(install, tmp_path), confirm=True, runner=runner,
                            actor="cli:update")

    assert receipt["ok"] is True and receipt["outcome"] == "success", receipt
    # The brand text was genuinely captured from git output and would have
    # leaked without the focused scrub.
    assert "pull_result" in receipt
    assert "qubit-agent" in receipt["pull_result"]["detail"], receipt["pull_result"]

    _assert_brand_clean(json.dumps(receipt), "in-memory receipt")
    persisted = UR.latest_receipt(_cfg(install, tmp_path))
    assert isinstance(persisted, dict), "the receipt must be persisted"
    assert persisted["outcome"] == "success"
    _assert_brand_clean(json.dumps(persisted), "persisted receipt")


def test_foreign_service_unit_receipt_is_brand_clean(install, tmp_path):
    from voicebridge import update_manager as UM

    runner = _BrandRunner(install.env)
    receipt = UM.full_apply(
        _cfg(install, tmp_path, service_unit="hermes-dashboard.service"),
        confirm=True, runner=runner, actor="cli:update")

    # The run refuses the foreign unit and rolls back — but the receipt still
    # must not carry the legacy unit name in any nested field.
    assert receipt["ok"] is False
    assert receipt["failure_step"] == "service_restart", receipt
    assert receipt["service_restart_result"]["unit"] == "qubit-dashboard.service"
    _assert_brand_clean(json.dumps(receipt), "foreign-unit in-memory receipt")
    persisted = UR.latest_receipt(_cfg(install, tmp_path))
    _assert_brand_clean(json.dumps(persisted), "foreign-unit persisted receipt")


def test_redact_receipt_scrubs_nested_step_results_and_preserves_data():
    receipt = {
        "schema": 1, "kind": "qubit-update",
        "update_id": "a" * 32, "outcome": "failed", "rolled_back": True,
        "post_revision": "b" * 40,
        "pull_result": {"detail": BRAND_BLOB},
        "dependency_result": {"detail": "pip failed via NousPortal"},
        "service_restart_result": {"unit": "hermes-dashboard.service"},
        "rollback_info": {"detail": "restored OpenClaw",
                          "restored_revision": "c" * 40},
    }
    safe = UR.redact_receipt(receipt)

    _assert_brand_clean(json.dumps(safe), "redacted nested receipt")
    # Identity + legitimate data survive untouched.
    assert safe["schema"] == 1 and safe["kind"] == "qubit-update"
    assert safe["update_id"] == "a" * 32 and safe["rolled_back"] is True
    assert safe["post_revision"] == "b" * 40
    assert safe["rollback_info"]["restored_revision"] == "c" * 40
    # The source mapping is never mutated.
    assert "hermes" in json.dumps(receipt)


# ---------------------------------------------------------------------------
# 2. audit-logged event strings are brand-clean
# ---------------------------------------------------------------------------
def test_audit_event_strings_are_brand_clean(install, tmp_path, monkeypatch):
    from voicebridge import update_manager as UM
    import voicebridge.logging_setup as LS

    captured = []

    def _capture(event, **fields):
        captured.append((event, dict(fields)))

    monkeypatch.setattr(LS, "log_event", _capture)

    runner = _BrandRunner(install.env, brand=BRAND_BLOB)
    UM.full_apply(_cfg(install, tmp_path), confirm=True, runner=runner,
                  actor="cli:update")

    update_events = [(e, f) for e, f in captured
                     if str(e).startswith("qubit.update.")]
    assert update_events, "no qubit.update.* audit events were logged"
    for event, fields in update_events:
        _assert_brand_clean(event, "audit event name")
        _assert_brand_clean(json.dumps(fields, default=str),
                            "audit event fields")


# ---------------------------------------------------------------------------
# 3. update-module user-facing string constants are brand-clean
# ---------------------------------------------------------------------------
def _non_docstring_strings(path: Path):
    """Every string literal in *path* except module/class/function docstrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            out.append((node.lineno, node.value))
    return out


def test_owned_update_module_string_literals_are_brand_clean():
    offenders = []
    for rel in OWNED_MODULES:
        path = REPO_ROOT / rel
        assert path.is_file(), rel
        for lineno, value in _non_docstring_strings(path):
            if value in _ALLOWED_LITERALS:
                continue
            if LEGACY_RE.search(value):
                offenders.append(f"{rel}:{lineno}: {value!r}")
    assert not offenders, (
        "legacy brand in a user-facing update string (only the classified "
        f"safety scrub pattern may remain): {offenders}")
