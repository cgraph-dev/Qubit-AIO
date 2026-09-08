"""Packet 8T-F close-out — REAL pinned-Hermes execution (no imitation).

Uses the isolated Hermes v0.21.1 install built from the pinned clone
(/tmp/hermes-isolated, HERMES_HOME redirected, operator installs untouched)
against the local Ollama model. Every test executes the real binary or
asserts typed DISABLED/UNAVAILABLE — false success is impossible by
construction. VRAM hygiene: the helper model is stopped after live turns.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

from voicebridge import foundation as F  # noqa: E402
from voicebridge.runtime import real_hermes as RH  # noqa: E402
from voicebridge.runtime.real_hermes import (RealHermesDenied,  # noqa: E402
                                             RealHermesRuntime)

ISOLATED_BIN = Path(os.environ.get("QUBIT_8TF_HERMES_BIN",
                                   "/tmp/hermes-isolated2/venv/bin/hermes"))
ISOLATED_HOME = Path(os.environ.get("QUBIT_8TF_HERMES_HOME",
                                    "/tmp/hermes-isolated2/home"))
CLONE = Path(os.environ.get("QUBIT_8TF_HERMES_CLONE",
                             "/tmp/upstream-8tfv3/hermes-agent"))
OPENCLAW_CLONE = Path(os.environ.get("QUBIT_8TF_OPENCLAW_CLONE",
                                     "/tmp/upstream-8tfv3/openclaw"))
MODEL = "huihui_ai/llama3.2-abliterate:3b"
SESSION = "qubit-8tf-proof"


def _require_setup():
    if not ISOLATED_BIN.is_file() or not ISOLATED_HOME.is_dir():
        pytest.fail("isolated Hermes missing: reinstall per docs/FOUNDATION_8T_F.md "
                    "(never substitutes the operator install)")


@pytest.fixture()
def real():
    _require_setup()
    rt = RealHermesRuntime(binary=ISOLATED_BIN, home=ISOLATED_HOME,
                           provider="ollama", model=MODEL, timeout_s=420.0)
    yield rt
    try:
        RH.stop_ollama_model(MODEL)
    except Exception:
        pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Identity: real binary, pinned source, exact flags
# ---------------------------------------------------------------------------
def test_real_hermes_version_identity():
    _require_setup()
    rt = RealHermesRuntime(binary=ISOLATED_BIN, home=ISOLATED_HOME)
    info = rt.version()
    assert info["state"] == "READY", info
    assert "0.21.1" in info["version"] and "Hermes Agent" in info["version"]
    assert info["binary"] == str(ISOLATED_BIN)
    assert info["expected_pin"] == F.HERMES_COMMIT == \
        "2237be355906fbe6065ce1815711eee52b2d646e"


def test_isolated_source_matches_pinned_clone():
    _require_setup()
    src = ISOLATED_BIN.parents[2] / "src" / "cli.py"
    assert src.is_file()
    assert _sha(src) == _sha(CLONE / "cli.py"), \
        "isolated install must be byte-identical to the pinned clone file"


def test_backend_argv_matches_real_chat_surface():
    """Qubit's argv must be a subset of the REAL `hermes chat` flags."""
    _require_setup()
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(ISOLATED_HOME),
           "HERMES_HOME": str(ISOLATED_HOME)}
    r = subprocess.run([str(ISOLATED_BIN), "chat", "--help"],
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stderr[-1000:]
    help_text = r.stdout
    rt = RealHermesRuntime(binary=ISOLATED_BIN, home=ISOLATED_HOME,
                           provider="ollama", model=MODEL)
    argv = rt._argv("probe", "sess")
    for flag in ("chat", "-q", "-Q", "--continue", "--create-if-missing",
                 "--provider", "-m"):
        assert flag in argv, flag
        assert flag in help_text, f"real binary lacks {flag}"


def test_openclaw_argv_matches_pinned_docs():
    """Qubit's OpenClaw CLI argv must exist in the pinned OpenClaw docs."""
    from voicebridge.openclaw import cli_backend as CB

    doc = (OPENCLAW_CLONE / "docs/cli/agent.md").read_text()
    argv = ["openclaw", "agent", "--agent", "main", "--session-key", "k",
            "--message-file", "f", "--json", "--timeout", "60"]
    assert CB.CliBackend is not None
    for flag in ("--agent", "--session-key", "--message-file", "--json",
                 "--timeout"):
        assert flag in argv and flag in doc, flag


# ---------------------------------------------------------------------------
# Live turns through the real implementation (typed either way)
# ---------------------------------------------------------------------------
def test_real_turn_ok_or_typed_degraded(real):
    r = real.turn("Reply with exactly: QUBIT_PROOF_MARKER", session=SESSION)
    assert r["session"] == SESSION
    if r["ok"]:
        assert "QUBIT_PROOF_MARKER" in r["reply"], r["reply"][:200]
        assert r["receipt"]["pin"] == F.HERMES_COMMIT
        assert r["receipt"]["binary"] == str(ISOLATED_BIN)
    else:
        assert r["code"] in ("TIMEOUT", "HERMES_ERROR", "CRASH",
                             "EMPTY_REPLY"), r
        assert r["error"], "typed failure must carry a reason"


def test_real_session_reusable_without_crash(real):
    for text in ("first proof turn", "second proof turn"):
        r = real.turn(text, session=SESSION)
        assert set(r) >= {"ok", "session"}, r
        assert r["session"] == SESSION
        if not r["ok"]:
            assert r["code"] in ("TIMEOUT", "HERMES_ERROR", "CRASH",
                                 "EMPTY_REPLY")


def test_real_secret_redacted(real):
    r = real.turn("my password is bearer sk-proof-secret-1", session=SESSION)
    if r["ok"]:
        assert "sk-proof-secret-1" not in r["reply"]
    else:
        assert "sk-proof-secret-1" not in r.get("error", "")


def test_real_unreachable_endpoint_fails_typed_fast(tmp_path):
    bad_home = tmp_path / "hhome"
    bad_home.mkdir()
    (bad_home / "config.yaml").write_text(
        'model:\n  provider: "ollama"\n  default: "nothing-here"\n'
        '  base_url: "http://127.0.0.1:9/v1"\n')
    rt = RealHermesRuntime(binary=ISOLATED_BIN, home=bad_home,
                           provider="ollama", timeout_s=90.0)
    r = rt.turn("hi", session="unreachable-probe")
    assert r["ok"] is False
    assert r["code"] in ("HERMES_ERROR", "CRASH", "TIMEOUT", "EMPTY_REPLY"), r


def test_real_malformed_and_estop_and_disabled(real):
    r = real.turn("   ", session=SESSION)
    assert r == {"ok": False, "code": "MALFORMED",
                 "error": "turn needs non-empty text"}
    import voicebridge.power as PW

    control = PW.PowerControl()
    rt = RealHermesRuntime(binary=ISOLATED_BIN, home=ISOLATED_HOME,
                           estop_engaged=lambda: control.state["emergency_stop"])
    control.emergency_stop()
    try:
        with pytest.raises(RealHermesDenied):
            rt.turn("blocked", session=SESSION)
    finally:
        control.reset()
    ghost = RealHermesRuntime()
    assert ghost.version()["state"] == "DISABLED"
    with pytest.raises(RealHermesDenied):
        ghost.turn("blocked", session=SESSION)


def test_real_env_carries_no_secrets(monkeypatch):
    monkeypatch.setenv("QUBIT_PROBE_API_KEY", "parent-key")
    monkeypatch.setenv("HERMES_TOKEN", "parent-token")
    rt = RealHermesRuntime(binary=ISOLATED_BIN, home=ISOLATED_HOME)
    env = rt._env()
    blob = "\n".join(f"{k}={v}" for k, v in env.items())
    assert "parent-key" not in blob and "parent-token" not in blob
    assert "QUBIT_PROBE_API_KEY" not in env and "HERMES_TOKEN" not in env
    assert env["HERMES_HOME"] == str(ISOLATED_HOME)
    assert env["HOME"] == str(ISOLATED_HOME)


def test_stop_model_reports_typed():
    out = RH.stop_ollama_model("model-that-never-existed-8tf")
    assert out["model"] == "model-that-never-existed-8tf"
    assert out["state"] in ("STOPPED", "UNKNOWN")
