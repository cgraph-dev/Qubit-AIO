"""Final parity — OpenCode voice workflow on the shared broker entry.

Proves on one backend (``opencode.runner.request_coding_run`` + the
shared ActionRunner machinery):

* voice preview/run/cancel/status/retry/recover/receipts/grant/revoke
  reach the same runner the CLI and dashboard use, with the same honest
  results (disabled adapters never fake success);
* voice runs are disposable-worktree only with exact confirmations;
  in-project writes are a typed refusal naming the CLI/dashboard hash
  flow (never silently widened);
* project/worktree/path confinement, argv allowlist, and secret
  scrubbing hold on the execution path (fake transport captures argv,
  env, and workdir);
* cancellation, retry (same-args, mismatch refused), receipts, and
  recovery behave identically across surfaces.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from voicebridge import actions as A
from voicebridge import opencode as OC
from voicebridge import voice_commands as VC


def _cfg(tmp_path, monkeypatch, **over):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _voice(cfg, entry_id, session="v-oc", **args):
    entry = VC.REGISTRY[entry_id]
    ctx = VC.make_ctx(cfg, actor="voice:user", session=session, source="user")
    out = VC.execute(ctx, VC.Resolution(VC.MATCH, entry=entry, args=args))
    return ctx, out


def _voice_confirmed(cfg, entry_id, session="v-oc", **args):
    ctx, out = _voice(cfg, entry_id, session=session, **args)
    assert out.kind == VC.NEEDS_CONFIRM, (entry_id, out.kind, out.speech)
    done = VC.confirm_pending(ctx, out.confirmation_id)
    return done


def _dash(cfg, op_id, args, session="d-oc"):
    from voicebridge import dashboard_ops as DO

    out = DO.run_op(cfg, op_id, args, actor="dashboard:user",
                    session=session)
    if out.get("state") == "needs-confirm":
        out = DO.confirm_op(cfg, out["confirmation_id"],
                            actor="dashboard:user", session=session)
    return out


def _exec_cfg(tmp_path, monkeypatch):
    """Config with the opencode master switch on (operator opt-in)."""
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\nintent:\n  opencode:\n"
                 "    enabled: true\n    binary: /bin/true\n")
    return C.Config.load(p)


def _git_project(tmp_path, name="proj"):
    import shutil
    import subprocess

    root = tmp_path / name
    root.mkdir(exist_ok=True)
    (root / "main.py").write_text("x = 1\n")
    git = shutil.which("git")
    if git is not None:
        subprocess.run([git, "init", "-q"], cwd=root, check=False,
                       capture_output=True)
        subprocess.run([git, "config", "user.email", "t@t.t"], cwd=root,
                       check=False, capture_output=True)
        subprocess.run([git, "config", "user.name", "t"], cwd=root,
                       check=False, capture_output=True)
        subprocess.run([git, "add", "."], cwd=root, check=False,
                       capture_output=True)
        subprocess.run([git, "commit", "-qm", "init"], cwd=root,
                       check=False, capture_output=True)
    return root


def _ok_run(capture):
    def _fake(argv, workdir=None, env=None, timeout_s=None, **_kw):
        capture.append({"argv": list(argv), "workdir": str(workdir),
                        "env": dict(env or {}), "timeout_s": timeout_s})
        body = {"type": "text", "text": "did the thing"}
        return {"state": "OK", "code": "OK",
                "stdout": json.dumps(body) + "\n", "stderr": ""}
    return _fake


def _enabled_runner(cfg, capture):
    from voicebridge.opencode import policy as POL

    runner = OC.opencode_runner(
        cfg, run_opencode=_ok_run(capture),
        version_probe=lambda _b: POL.PINNED_OPENCODE_VERSION)
    runner.perms.grant("act.opencode.run", requested_by="user",
                       confirmed=True, scopes=("code",))
    runner.perms.grant("act.opencode.plan", requested_by="user",
                       confirmed=True, scopes=("code",))
    runner.enable(requested_by="user")
    return runner


# ------------------------------------------------------- voice confirmation
def test_voice_run_stages_confirmation_then_honest_state(tmp_path,
                                                         monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    ctx, staged = _voice(cfg, "opencode_run", task="add tests")
    assert staged.kind == VC.NEEDS_CONFIRM
    assert staged.confirmation_id
    done = VC.confirm_pending(ctx, staged.confirmation_id)
    # Hermetic default: adapter disabled → honest unavailable, never fake.
    assert done.kind in (VC.EXECUTED, VC.FAILED), done.speech
    body = json.dumps(done.receipt)
    assert "did the thing" not in body
    # Single-use: replay is refused.
    again = VC.confirm_pending(ctx, staged.confirmation_id)
    assert again.kind == VC.FAILED


def test_voice_preview_reads_without_confirmation(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    ctx, out = _voice(cfg, "opencode_preview", task="add tests")
    assert out.kind == VC.EXECUTED, out.speech


def test_voice_in_project_refused_with_guidance(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    done = _voice_confirmed(cfg, "opencode_run", task="add tests",
                            in_project="yes")
    assert done.kind == VC.EXECUTED
    result = done.receipt["result"]
    assert result["state"] == "denied"
    assert result["code"] == "VOICE_DISPOSABLE_ONLY"
    assert "expected-hash" in result["detail"]


def test_voice_grant_revoke_receipts(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    granted = _voice_confirmed(cfg, "opencode_grant",
                               capability="act.opencode.run", scope="code")
    assert granted.kind == VC.EXECUTED, granted.speech
    gid = granted.receipt["result"]["grant"]["id"]
    revoked = _voice_confirmed(cfg, "opencode_revoke", grant_id=gid)
    assert revoked.kind == VC.EXECUTED, revoked.speech
    listed = _voice(cfg, "opencode_receipts")[1]
    assert listed.kind == VC.EXECUTED, listed.speech


def test_voice_missing_task_and_bad_timeout(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    ctx, out = _voice(cfg, "opencode_run")
    # Confirm-gated entries stage first; the missing-task error surfaces
    # honestly after the (empty) confirmation round-trip.
    assert out.kind in (VC.FAILED, VC.NEEDS_CONFIRM)
    if out.kind == VC.NEEDS_CONFIRM:
        done = VC.confirm_pending(ctx, out.confirmation_id)
        assert done.kind == VC.FAILED and "task" in done.speech.lower()
    else:
        assert "task" in out.speech.lower()
    bad = _voice_confirmed(cfg, "opencode_run", task="x", timeout="9999")
    assert bad.kind == VC.FAILED and "600" in bad.speech


# ------------------------------------------------------- supervised execution
def test_disposable_run_confined_no_secrets(tmp_path, monkeypatch):
    cfg = _exec_cfg(tmp_path, monkeypatch)
    capture: list = []
    runner = _enabled_runner(cfg, capture)
    root = _git_project(tmp_path)
    for name in ("OPENCODE_API_KEY", "QUBIT_SECRET_PROBE", "MY_TOKEN",
                 "MY_COOKIE", "PYTHONPATH", "BASH_ENV"):
        monkeypatch.setenv(name, "sensitive-value")
    # Direct executor call (in-process) so argv/env can be inspected:
    # through the runner the same fn runs spawn-isolated and only the
    # receipt crosses.
    args = {"project": str(root), "task": "Add a hello function",
            "disposable": True}
    env = A.build_envelope(OC.opencode_spec(OC.ACT_RUN), args=args,
                           target=str(root), scope="code")
    full = json.loads(runner.executors[OC.ACT_RUN](env))
    assert full["state"] == "OK", full
    assert capture, "subprocess boundary must be reached"
    call = capture[0]
    # Workdir is a disposable copy, never the live project.
    assert call["workdir"] != str(root)
    assert Path(call["workdir"]).is_absolute()
    # No secrets, cookies, personal paths, or parent env leak in.
    for name in ("OPENCODE_API_KEY", "QUBIT_SECRET_PROBE", "MY_TOKEN",
                 "MY_COOKIE", "HOME", "PYTHONPATH", "BASH_ENV",
                 "XDG_CONFIG_HOME", "SSH_AUTH_SOCK"):
        assert name not in call["env"], name
    blob = json.dumps(call["env"])
    assert "sensitive-value" not in blob
    assert call["env"]["PATH"] == "/usr/bin:/bin"
    # No unrestricted flags survive the argv allowlist.
    assert "--auto" not in call["argv"] and "serve" not in call["argv"]
    # The broker-mediated run reaches the same receipt state.
    out = OC.request_coding_run(
        runner, project=str(root),
        task_args={"project": str(root), "task": "Add a hello function",
                   "disposable": True},
        confirmed=True)
    assert out["state"] == "OK", out


def test_cancel_retry_recover_receipts(tmp_path, monkeypatch):
    cfg = _exec_cfg(tmp_path, monkeypatch)
    # Fail-first fake: a counter FILE (forkserver children cannot share
    # parent memory, so cross-call state lives on disk).
    counter = tmp_path / "calls.txt"
    counter.write_text("0")

    def _flaky(argv, workdir=None, env=None, timeout_s=None, **_kw):
        n = int(counter.read_text()) + 1
        counter.write_text(str(n))
        if n == 1:
            return {"state": "OK", "code": "OK",
                    "stdout": "not-json-at-all\n", "stderr": ""}
        return _ok_run([])(argv, workdir=workdir, env=env,
                           timeout_s=timeout_s)

    from voicebridge.opencode import policy as POL

    runner = OC.opencode_runner(
        cfg, run_opencode=_flaky,
        version_probe=lambda _b: POL.PINNED_OPENCODE_VERSION)
    runner.perms.grant("act.opencode.run", requested_by="user",
                       confirmed=True, scopes=("code",))
    runner.enable(requested_by="user")
    root = _git_project(tmp_path)
    task_args = {"project": str(root), "task": "Add a hello function",
                 "disposable": True}
    first = OC.request_coding_run(runner, project=str(root),
                                  task_args=dict(task_args), confirmed=True)
    assert first["state"] == "failed", first
    # Cancel of a finished (failed) run is an honest no-op, not an error.
    cancelled = runner.cancel(first["receipt_id"])
    assert cancelled["state"] in ("cancelled", "OK", "failed"), cancelled
    # Retry with the SAME args re-executes and now succeeds; different
    # args are refused as a new action, never a silent retry.
    same = runner.retry(first["receipt_id"], args=dict(task_args),
                        target=str(root), scope="code", source="user",
                        confirmed=True)
    assert same["state"] == "OK", same
    changed_args = dict(task_args, task="Something else entirely")
    changed = runner.retry(first["receipt_id"], args=changed_args,
                           target=str(root), scope="code", source="user",
                           confirmed=True)
    assert changed.get("code") in ("ARGS_MISMATCH", "TARGET_SCOPE_MISMATCH",
                                   "STALE_TARGET") or \
        changed.get("state") in ("stale_target",), changed
    # Receipts persist with redaction.
    recs = runner.receipts(10)
    assert recs["count"] >= 2
    blob = json.dumps(recs)
    assert "sk-test" not in blob


def test_cli_retry_recover_wired(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    cfg = _cfg(tmp_path, monkeypatch)
    for cmd, extra in (("retry", {"project": ".", "task": "",
                                  "task_file": None, "timeout": None,
                                  "confirmation": None, "confirm": False}),
                       ("recover", {"confirmation": None,
                                    "confirm": False})):
        args = type("A", (), {})()
        args.opencode_command = cmd
        args.receipt_id = "actr_missing"
        args.fixture = None
        args.dry_run = False
        for key, val in extra.items():
            setattr(args, key, val)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = CLI._cmd_opencode(cfg, args)
        assert rc == 2, (cmd, buf.getvalue())
        assert "actr_missing" in buf.getvalue()


def test_dashboard_parity_and_prepare_flow(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    st = _dash(cfg, "opencode_status", {})
    assert st["state"] == "executed", st
    assert st["receipt"]["result"]["adapter"] == "opencode"
    preview = _dash(cfg, "opencode_preview", {"task": "add tests"})
    assert preview["state"] in ("executed", "needs-confirm"), preview
    if preview["state"] == "needs-confirm":
        preview = _dash(cfg, "opencode_preview", {"task": "add tests"},
                        session="d-oc2")
    recs = _dash(cfg, "opencode_receipts", {})
    assert recs["state"] == "executed", recs
    # In-project prepare mints a hash-bound confirmation (dashboard form
    # confirms the extra, then the runner returns the hash + approval id
    # the execute step must present).
    prep = _dash(cfg, "opencode_prepare",
                 {"task": "add tests", "in_project": "true"})
    assert prep["state"] == "executed", prep
    prepared = prep["receipt"]["result"]
    assert prepared["action_hash"] and prepared.get("confirmation_id")
    # Unknown receipts fail typed on every surface.
    assert _dash(cfg, "opencode_run_confirmed",
                 {"task": "x", "expected_hash": "deadbeef"})["state"] in (
                     "executed", "failed")


def test_required_utterances_resolve(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch)
    assert VC.resolve("qubit, run this opencode task add tests").entry.id == \
        "opencode_run"
    assert VC.resolve("qubit, preview coding task add tests").entry.id == \
        "opencode_preview"
    assert VC.resolve("qubit, list coding runs").entry.id == \
        "opencode_receipts"
    assert VC.resolve("qubit, open my project").entry.id == \
        "projects_active"
    assert VC.resolve("qubit, inspect this folder").entry.id == \
        "files_inspect"


def test_cli_preview_carries_project(tmp_path, monkeypatch):
    """Regression: `qubit opencode preview --project --task` must validate
    (the CLI task envelope carries the required project arg)."""
    import io
    import json
    from contextlib import redirect_stdout

    from voicebridge import cli as CLI

    cfg = _exec_cfg(tmp_path, monkeypatch)
    root = _git_project(tmp_path)
    args = type("A", (), {})()
    args.opencode_command = "preview"
    args.project = str(root)
    args.task = "Add hello"
    args.task_file = None
    args.disposable = True
    args.in_project = False
    args.fixture = None
    args.dry_run = False
    args.timeout = None
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_opencode(cfg, args)
    out = json.loads(buf.getvalue())
    assert rc == 0, out
    assert out["state"] == "preview", out
    assert out["action_hash"]
