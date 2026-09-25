"""Wave 21 — the update surface matrix: dashboard dispatch, voice, receipts.

This file drives the *public* seams only and never touches the network, the real
product checkout, ``~/.hermes``, the live service, or the gateway daemon:

* **Dashboard API** — the real ``qubit_compat.dispatch`` router for
  ``GET /api/qubit/update/check`` (read-only 200 report),
  ``POST /api/qubit/update`` (409 ``CONFIRM_REQUIRED`` with zero git calls, and
  the confirmed path reaching the canonical backend), and
  ``POST /api/qubit/update/rollback`` (canonical typed result).
* **Voice** — ``voice_commands.resolve`` / ``execute`` / ``confirm_pending`` for
  the ``update_check`` / ``update_apply`` / ``update_rollback`` / ``update_status``
  intents: the same canonical backend, confirmation gating, e-stop honoring, and
  no legacy brand in any spoken response.
* **Receipt schema** — ``update_manager.full_apply`` happy path against a
  disposable temp git world with an injected runner; the one receipt has every
  required field and no credential captured from runner output.

Every git command is injected and bound to a hermetic temp repo; the manager's
fail-closed guard is lifted only with an injected executor, so nothing real runs.
"""
from __future__ import annotations

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

from voicebridge import qubit_compat as QC  # noqa: E402
from voicebridge import update_manager as UM  # noqa: E402
from voicebridge import update_receipt as UR  # noqa: E402
from voicebridge import voice_commands as VC  # noqa: E402

# Legacy product-brand tokens that must never appear in a voice response.
LEGACY_RE = re.compile(
    r"hermes|openclaw|nousresearch|nousportal|nous\s+research", re.I)

# A credential shape the central redactor masks (``sk-`` + >=12 chars).
FAKE_SECRET = "sk-" + "QUBITfakeSECRET0123456789"


# ---------------------------------------------------------------------------
# disposable git world + helpers
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


class _RecordingRunner:
    """Injected ``runner``: records argv, then really executes it (temp repo)."""

    def __init__(self, env):
        self.env = env
        self.calls = []

    def __call__(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, env=self.env)
        return proc.returncode, proc.stdout, proc.stderr


def _git_env(tmp_path):
    empty = tmp_path / "gitconfig-empty"
    empty.write_text("")
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "Qubit Wave21",
        "GIT_AUTHOR_EMAIL": "wave21@example.invalid",
        "GIT_COMMITTER_NAME": "Qubit Wave21",
        "GIT_COMMITTER_EMAIL": "wave21@example.invalid",
        "GIT_CONFIG_GLOBAL": str(empty),
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def _git(args, cwd, env):
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, env=env)
    assert proc.returncode == 0, (
        f"git {' '.join(args)} failed ({proc.returncode}): "
        f"{proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


def _head(repo, env):
    return _git(["rev-parse", "HEAD"], repo, env)


@pytest.fixture(autouse=True)
def _estop_clear(monkeypatch):
    """The single e-stop authority is forced clear; its refusal is tested here."""
    monkeypatch.setattr(UM, "_estop", lambda _cfg: False)


@pytest.fixture
def install(tmp_path):
    """A temp install one commit behind its bare local remote."""
    env = _git_env(tmp_path)
    bare = tmp_path / "remote.git"
    source = tmp_path / "source"
    checkout = tmp_path / "install"

    _git(["init", "--bare", "-b", "main", str(bare)], tmp_path, env)
    _git(["init", "-b", "main", str(source)], tmp_path, env)
    (source / "VERSION").write_text("1\n")
    _git(["add", "-A"], source, env)
    _git(["commit", "-m", "first"], source, env)
    _git(["remote", "add", "origin", str(bare)], source, env)
    _git(["push", "-u", "origin", "main"], source, env)

    _git(["clone", str(bare), str(checkout)], tmp_path, env)
    first = _head(checkout, env)

    (source / "VERSION").write_text("2\n")
    _git(["add", "-A"], source, env)
    _git(["commit", "-m", "second"], source, env)
    _git(["push", "origin", "main"], source, env)
    second = _head(source, env)

    assert first != second
    assert _head(checkout, env) == first, "install must still be one behind"
    assert _git(["status", "--porcelain"], checkout, env) == ""
    return SimpleNamespace(checkout=checkout, source=source, bare=bare, env=env,
                           first=first, second=second)


def _cfg(checkout, *, backup_dir=None, lock_dir=None, skip_validation=False):
    update = {"repo_root": str(checkout), "remote": "origin", "branch": "main"}
    if backup_dir is not None:
        update["backup_dir"] = str(backup_dir)
    if lock_dir is not None:
        update["lock_dir"] = str(lock_dir)
    if skip_validation:
        update["skip_validation"] = True
    return _Cfg({"update": update})


def _wire_real_update(monkeypatch, install):
    """Lift the fail-closed guard and bind the default runner to the temp repo."""
    monkeypatch.setattr(UM, "_real_control_disabled", lambda: False)
    runner = _RecordingRunner(install.env)
    monkeypatch.setattr(UM, "_default_runner", runner)
    return runner


def _dispatch(cfg, method, path, body=None):
    return QC.dispatch(cfg, method, path, {}, body or {})


# ---------------------------------------------------------------------------
# 1. Dashboard API — the real compat dispatcher
# ---------------------------------------------------------------------------
def test_dispatch_get_update_check_is_real_read_only_report(install, monkeypatch):
    runner = _wire_real_update(monkeypatch, install)
    status, body = _dispatch(_cfg(install.checkout), "GET",
                             "/api/qubit/update/check")
    assert status == 200 and body["ok"] is True, (status, body)
    assert body["install_method"] == "git-checkout"
    assert body["current_revision"] == install.first
    assert body["remote_revision"] == install.second
    assert body["update_available"] is True and body["can_apply"] is True
    # A read: no pull, reset, or checkout is ever issued.
    for argv in runner.calls:
        args = argv[3:]
        assert args[:1] not in (["pull"], ["reset"])
        assert "checkout" not in args


def test_dispatch_post_update_without_confirm_409_and_zero_git(
        install, monkeypatch):
    runner = _wire_real_update(monkeypatch, install)
    status, body = _dispatch(_cfg(install.checkout), "POST", "/api/qubit/update")
    assert status == 409, (status, body)
    assert body["ok"] is False
    assert body["code"] == UM.CONFIRM_CODE
    assert body["state"] == "CONFIRM_REQUIRED"
    assert runner.calls == [], f"confirm gate must run no git: {runner.calls}"


def test_dispatch_post_update_confirm_reaches_canonical_backend(monkeypatch):
    seen = {}

    def _full(cfg, *, confirm=False, runner=None, git=None, source=None,
              channel=None, actor="cli:update", resume_token=None, tag=None):
        seen["confirm"] = confirm
        seen["actor"] = actor
        seen["source"] = source
        return {"ok": True, "state": "OK", "code": "",
                "post_revision": "b" * 40}

    monkeypatch.setattr(UM, "full_apply", _full)
    status, body = _dispatch(_Cfg({}), "POST", "/api/qubit/update",
                             {"confirm": True})
    assert status == 200 and body["ok"] is True, (status, body)
    assert seen.get("confirm") is True, "handler must forward confirm=True"
    assert seen.get("actor") == "dashboard:user"


def test_dispatch_post_update_rollback_reaches_canonical_rollback(monkeypatch):
    seen = {}

    def _rollback(cfg, *, backup_dir=None, runner=None, git=None,
                  actor="cli:update"):
        seen["backup_dir"] = backup_dir
        seen["actor"] = actor
        return {"ok": True, "state": "OK", "code": "",
                "restored_revision": "c" * 40, "reversible": True}

    monkeypatch.setattr(UM, "rollback", _rollback)
    status, body = _dispatch(_Cfg({}), "POST", "/api/qubit/update/rollback",
                             {"confirm": True, "backup_dir": "/tmp/qb"})
    assert status == 200 and body["ok"] is True, (status, body)
    assert body["restored_revision"] == "c" * 40
    assert seen == {"backup_dir": "/tmp/qb", "actor": "dashboard:user"}, seen


def test_dispatch_post_update_rollback_no_backup_maps_to_409(monkeypatch):
    def _rollback(cfg, *, backup_dir=None, runner=None, git=None,
                  actor="cli:update"):
        return {"ok": False, "state": "MISSING", "code": UM.NO_BACKUP_CODE,
                "detail": "no backup found"}

    monkeypatch.setattr(UM, "rollback", _rollback)
    status, body = _dispatch(_Cfg({}), "POST", "/api/qubit/update/rollback",
                             {"confirm": True})
    assert status == 409, (status, body)
    assert body["ok"] is False and body["code"] == UM.NO_BACKUP_CODE


# ---------------------------------------------------------------------------
# 2. Voice — intents resolve to the one canonical backend
# ---------------------------------------------------------------------------
def _voice_cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _ctx(cfg, session="wave21", source="user"):
    return VC.make_ctx(cfg, session=session, source=source)


def _assert_brand_clean(*texts):
    for text in texts:
        m = LEGACY_RE.search(str(text))
        assert m is None, f"legacy brand {m.group(0)!r} in voice text: {text!r}"


def test_voice_update_intents_resolve_to_canonical_backends():
    table = {
        "qubit, update check": ("update_check", "_b_update_check"),
        "qubit, apply update": ("update_apply", "_b_update_apply"),
        "qubit, roll back update": ("update_rollback", "_b_update_rollback"),
    }
    for phrase, (entry_id, backend_fn) in table.items():
        res = VC.resolve(phrase)
        assert res.kind == VC.MATCH, phrase
        assert res.entry is not None and res.entry.id == entry_id, phrase
        fn = VC._BACKENDS.get(res.entry.backend)
        assert fn is not None, f"{entry_id}: backend not wired"
        assert fn.__name__ == backend_fn, (entry_id, fn.__name__)
    assert VC.resolve("qubit, update check").entry.confirm is False
    assert VC.resolve("qubit, apply update").entry.confirm is True
    assert VC.resolve("qubit, roll back update").entry.confirm is True


def test_voice_update_check_runs_canonical_and_is_brand_clean(
        tmp_path, monkeypatch):
    cfg = _voice_cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    calls = []

    def _check(cfg_, *, runner=None, git=None, source=None):
        calls.append(source)
        return {"state": "OK", "install_method": "git-checkout",
                "current_version": "1.0.0", "update_available": False,
                "message": "You're on the latest version."}

    monkeypatch.setattr(UM, "check", _check)
    out = VC.execute(ctx, VC.resolve("qubit, update check"))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    assert len(calls) == 1, "the read-only intent must call UM.check once"
    _assert_brand_clean(out.speech, json.dumps(out.receipt))


def test_voice_mutations_stage_confirmation_then_run_canonical(
        tmp_path, monkeypatch):
    cfg = _voice_cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    apply_calls, rollback_calls = [], []

    def _full(cfg_, *, confirm=False, runner=None, git=None, source=None,
              channel=None, actor="cli:update", resume_token=None, tag=None):
        apply_calls.append({"confirm": confirm, "actor": actor})
        return {"ok": True, "state": "OK", "code": ""}

    def _rollback(cfg_, *, backup_dir=None, runner=None, git=None,
                  actor="cli:update"):
        rollback_calls.append({"backup_dir": backup_dir, "actor": actor})
        return {"ok": True, "state": "OK", "code": "",
                "restored_revision": "d" * 40}

    monkeypatch.setattr(UM, "full_apply", _full)
    monkeypatch.setattr(UM, "rollback", _rollback)

    staged = VC.execute(ctx, VC.resolve("qubit, apply update"))
    assert staged.kind == VC.NEEDS_CONFIRM, (staged.kind, staged.speech)
    assert staged.confirmation_id
    _assert_brand_clean(staged.speech)
    assert apply_calls == [], "no mutation before consent"

    done = VC.confirm_pending(ctx, staged.confirmation_id)
    assert done.kind == VC.EXECUTED, (done.kind, done.speech)
    assert apply_calls == [{"confirm": True, "actor": "voice:user"}]
    _assert_brand_clean(done.speech)

    staged_rb = VC.execute(ctx, VC.resolve("qubit, roll back update"))
    assert staged_rb.kind == VC.NEEDS_CONFIRM, staged_rb.kind
    assert rollback_calls == [], "no mutation before consent"
    done_rb = VC.confirm_pending(ctx, staged_rb.confirmation_id)
    assert done_rb.kind == VC.EXECUTED, (done_rb.kind, done_rb.speech)
    assert rollback_calls == [{"backup_dir": None, "actor": "voice:user"}]
    _assert_brand_clean(done_rb.speech)


def test_voice_mutations_honor_estop(tmp_path, monkeypatch):
    cfg = _voice_cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(VC, "_estop", lambda _cfg: True)
    ctx = _ctx(cfg)
    calls = []

    def _full(cfg_, *, confirm=False, runner=None, git=None, source=None,
              channel=None, actor="cli:update", resume_token=None, tag=None):
        calls.append("apply")
        return {"ok": True, "state": "OK"}

    def _rollback(cfg_, *, backup_dir=None, runner=None, git=None,
                  actor="cli:update"):
        calls.append("rollback")
        return {"ok": True, "state": "OK"}

    monkeypatch.setattr(UM, "full_apply", _full)
    monkeypatch.setattr(UM, "rollback", _rollback)

    for phrase in ("qubit, apply update", "qubit, roll back update"):
        out = VC.execute(ctx, VC.resolve(phrase))
        assert out.kind == VC.REFUSED, (phrase, out.kind, out.speech)
        assert "emergency stop" in out.speech.lower(), out.speech
    assert calls == [], "a latched e-stop must run no backend"


def test_voice_update_status_still_works_and_is_brand_clean(
        tmp_path, monkeypatch):
    cfg = _voice_cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    res = VC.resolve("qubit, update status")
    assert res.kind == VC.MATCH and res.entry.id == "update_status"
    out = VC.execute(ctx, res)
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    _assert_brand_clean(out.speech, json.dumps(out.receipt))


# ---------------------------------------------------------------------------
# 2b. The shared dashboard dispatcher (``dashboard_ops.run_op``) reaches the
#     same canonical update backends as the CLI and voice.  These explicit
#     op-id calls are also the linked evidence the gap ledger requires so the
#     three update catalog rows are terminal, not UNVERIFIED.
# ---------------------------------------------------------------------------
def test_update_ops_dispatch_through_the_shared_dashboard_dispatcher(
        tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _voice_cfg(tmp_path, monkeypatch)
    check_calls = []

    def _check(cfg_, *, source=None, runner=None, git=None):
        check_calls.append(source)
        return {"state": "OK", "install_method": "git-checkout",
                "current_version": "1.0.0", "update_available": False}

    monkeypatch.setattr(UM, "check", _check)
    monkeypatch.setattr(UM, "full_apply", lambda *a, **k: {
        "ok": True, "state": "OK", "code": ""})
    monkeypatch.setattr(UM, "rollback", lambda *a, **k: {
        "ok": True, "state": "OK", "code": "", "restored_revision": "e" * 40})

    # Read-only check executes through the shared dispatcher.
    out = OPS.run_op(cfg, "update_check", {})
    assert out["state"] == "executed", out
    assert len(check_calls) == 1, "dispatcher must call UM.check exactly once"

    # The mutating ops stage the single-use confirmation and never run the
    # backend before consent.
    staged_apply = OPS.run_op(cfg, "update_apply", {})
    assert staged_apply["state"] == "needs-confirm", staged_apply
    assert staged_apply.get("confirmation_id")
    staged_rollback = OPS.run_op(cfg, "update_rollback", {})
    assert staged_rollback["state"] == "needs-confirm", staged_rollback
    assert staged_rollback.get("confirmation_id")


# ---------------------------------------------------------------------------
# 3. Receipt schema — full_apply happy path on a disposable repo
# ---------------------------------------------------------------------------
def test_full_apply_happy_path_receipt_schema_and_no_secret(
        install, tmp_path, monkeypatch):
    backups = tmp_path / "qbackups"
    lock = tmp_path / "qlock"

    class _SecretRunner(_RecordingRunner):
        """Real git runner that leaks a fake credential into ``pull`` output."""

        def __call__(self, argv, timeout):
            rc, out, err = super().__call__(argv, timeout)
            if list(argv)[3:4] == ["pull"]:
                out = (out or "") + "\n" + FAKE_SECRET + "\n"
            return rc, out, err

    runner = _SecretRunner(install.env)
    cfg = _cfg(install.checkout, backup_dir=backups, lock_dir=lock,
               skip_validation=True)

    receipt = UM.full_apply(cfg, confirm=True, runner=runner,
                            actor="cli:update")

    assert receipt["ok"] is True and receipt["outcome"] == "success", receipt
    required = {
        "schema", "kind", "update_id", "started_at", "finished_at", "actor",
        "pid", "outcome", "channel", "previous_revision", "new_revision",
        "remote", "branch", "backup_dir", "dependency_result",
        "config_migration_result", "validation_result", "service_restart_result",
        "health_result", "rolled_back", "rollback_info", "install_method",
        "changed",
    }
    missing = required - set(receipt)
    assert not missing, f"receipt missing fields: {sorted(missing)}"
    assert receipt["kind"] == UR.RECEIPT_KIND
    assert receipt["schema"] == UR.RECEIPT_SCHEMA
    assert receipt["install_method"] == "git-checkout"
    assert receipt["previous_revision"] == install.first
    assert receipt["new_revision"] == install.second
    assert receipt["changed"] is True
    assert receipt["rolled_back"] is False
    assert receipt["backup_dir"]
    assert _head(install.checkout, install.env) == install.second

    # The credential captured from runner output is nowhere in the receipt, and
    # the persisted latest receipt is equally clean.
    blob = json.dumps(receipt)
    assert FAKE_SECRET not in blob, "runner output secret leaked into receipt"
    latest = UR.latest_receipt(cfg)
    assert isinstance(latest, dict), "the happy-path receipt must be persisted"
    assert latest["outcome"] == "success"
    assert FAKE_SECRET not in json.dumps(latest), "secret persisted to disk"

    # The lock is released and no destructive verb ran outside rollback.
    assert not (lock / ".qubit-update-in-progress").exists()
    for argv in runner.calls:
        args = list(argv)[3:]
        assert args[:1] != ["reset"], f"apply must not reset: {argv}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
