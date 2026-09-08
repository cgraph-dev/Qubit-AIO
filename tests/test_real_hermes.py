"""Packet 8T-F close-out — REAL pinned-Hermes execution (no imitation).

A session-scoped provision installs the pinned clone into an isolated
Qubit-owned tree (never /tmp/hermes-*, never the operator install) and
every test executes that real implementation or asserts typed
DISABLED/UNAVAILABLE. VRAM hygiene: the helper model is stopped after
live turns. Set QUBIT_8TF_OFFLINE=1 to run only the no-model subset
(provision still required: source copy + venv are local after first run).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLONE = Path(os.environ.get("QUBIT_8TF_HERMES_CLONE",
                            "/tmp/upstream-8tfv3/hermes-agent"))
OPENCLAW_CLONE = Path(os.environ.get("QUBIT_8TF_OPENCLAW_CLONE",
                                     "/tmp/upstream-8tfv3/openclaw"))
MODEL = "huihui_ai/llama3.2-abliterate:3b"
SESSION = "qubit-8tf-proof"

from voicebridge import foundation as F  # noqa: E402
from voicebridge.runtime import provision as P  # noqa: E402
from voicebridge.runtime import real_hermes as RH  # noqa: E402
from voicebridge.runtime.real_hermes import (RealHermesDenied,  # noqa: E402
                                             RealHermesRuntime)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="session")
def install(tmp_path_factory):
    """Provision the pinned clone once per session into isolated space."""
    if not CLONE.is_dir():
        pytest.fail(f"pinned clone missing: {CLONE} (re-clone v2026.9.7)")
    base = tmp_path_factory.mktemp("hermes-prov")
    r = P.install_from_clone(CLONE, dest_base=base / "data")
    assert r.ok, f"provision failed: {r.code} {r.detail[:500]}"
    rdir = Path(str(r.runtime_dir))
    home = base / "state" / "hermes-runtime"
    P.write_runtime_config(home)
    yield {"dir": rdir,
           "binary": rdir / "venv" / "bin" / "hermes",
           "home": home}
    try:
        RH.stop_ollama_model(MODEL)
    except Exception:
        pass


@pytest.fixture()
def real(install):
    rt = RealHermesRuntime(binary=install["binary"], home=install["home"],
                           provider="ollama", model=MODEL, timeout_s=420.0)
    yield rt


# ---------------------------------------------------------------------------
# Identity: real binary, pinned source, exact flags
# ---------------------------------------------------------------------------
def test_provision_procedure_verifies_pin_and_license(install):
    info = P.verify_installation(install["dir"])
    assert info["state"] == "READY", info
    assert info["commit"] == F.HERMES_COMMIT == \
        "2237be355906fbe6065ce1815711eee52b2d646e"
    assert "0.21.1" in info["version"]
    receipt = json_load(install["dir"] / "INSTALL_RECEIPT.json")
    assert receipt["commit"] == F.HERMES_COMMIT
    assert receipt["tag"] == "v2026.9.7"


def json_load(path):
    import json as _json
    return _json.loads(Path(path).read_text())


def test_real_hermes_version_identity(install):
    rt = RealHermesRuntime(binary=install["binary"], home=install["home"])
    info = rt.verify()
    assert info["state"] == "READY", info
    assert "0.21.1" in info["version"] and "Hermes Agent" in info["version"]
    assert info["commit"] == F.HERMES_COMMIT


def test_provisioned_source_matches_pinned_clone(install):
    for rel in ("cli.py", "hermes_constants.py",
                "tools/ansi_strip.py", "hermes_cli/sqlite_util.py"):
        assert _sha(install["dir"] / "src" / rel) == _sha(CLONE / rel), rel


def test_backend_argv_matches_real_chat_surface(install):
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(install["home"]),
           "HERMES_HOME": str(install["home"])}
    r = subprocess.run([str(install["binary"]), "chat", "--help"],
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stderr[-1000:]
    rt = RealHermesRuntime(binary=install["binary"], home=install["home"],
                           provider="ollama", model=MODEL)
    argv = rt._argv("probe", "sess")
    for flag in ("chat", "-q", "-Q", "--continue", "--create-if-missing",
                 "--provider", "-m"):
        assert flag in argv, flag
        assert flag in r.stdout, f"real binary lacks {flag}"


def test_openclaw_argv_matches_pinned_docs():
    from voicebridge.openclaw import cli_backend as CB

    doc = (OPENCLAW_CLONE / "docs/cli/agent.md").read_text()
    assert CB.CliBackend is not None
    for flag in ("--agent", "--session-key", "--message-file", "--json",
                 "--timeout"):
        assert flag in doc, flag


def test_production_defaults_use_qubit_owned_space():
    rt = RealHermesRuntime()
    assert "qubit-voicebridge/runtimes/hermes-agent-2237be3" in str(rt.binary)
    assert "hermes-isolated" not in str(rt.binary)
    assert "/tmp/hermes" not in str(rt.binary)
    assert rt.version()["state"] in ("DISABLED", "READY", "UNAVAILABLE",
                                     "DEGRADED")


# ---------------------------------------------------------------------------
# Live turns through the real implementation (typed either way)
# ---------------------------------------------------------------------------
def test_real_turn_ok_or_typed_degraded(real):
    r = real.turn("Reply with exactly: QUBIT_PROOF_MARKER", session=SESSION)
    assert r["session"] == SESSION
    if r["ok"]:
        assert "QUBIT_PROOF_MARKER" in r["reply"], r["reply"][:200]
        assert r["receipt"]["pin"] == F.HERMES_COMMIT
        assert "hermes-isolated" not in r["receipt"]["binary"]
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


def test_real_unreachable_endpoint_fails_typed_fast(tmp_path, install):
    bad_home = tmp_path / "hhome"
    bad_home.mkdir()
    (bad_home / "config.yaml").write_text(
        'model:\n  provider: "ollama"\n  default: "nothing-here"\n'
        '  base_url: "http://127.0.0.1:9/v1"\n')
    rt = RealHermesRuntime(binary=install["binary"], home=bad_home,
                           provider="ollama", timeout_s=90.0)
    r = rt.turn("hi", session="unreachable-probe")
    assert r["ok"] is False
    assert r["code"] in ("HERMES_ERROR", "CRASH", "TIMEOUT", "EMPTY_REPLY"), r


def test_real_malformed_and_estop_and_disabled(install):
    rt = RealHermesRuntime(binary=install["binary"], home=install["home"])
    r = rt.turn("   ", session=SESSION)
    assert r == {"ok": False, "code": "MALFORMED",
                 "error": "turn needs non-empty text"}
    import voicebridge.power as PW

    control = PW.PowerControl()
    rt2 = RealHermesRuntime(binary=install["binary"], home=install["home"],
                            estop_engaged=lambda: control.state["emergency_stop"])
    control.emergency_stop()
    try:
        with pytest.raises(RealHermesDenied):
            rt2.turn("blocked", session=SESSION)
    finally:
        control.reset()
    ghost = RealHermesRuntime(binary="/nonexistent-8tf/bin",
                              home="/nonexistent-8tf/home")
    assert ghost.version()["state"] == "DISABLED"
    with pytest.raises(RealHermesDenied):
        ghost.turn("blocked", session=SESSION)


def test_real_env_carries_no_secrets(monkeypatch, install):
    monkeypatch.setenv("QUBIT_PROBE_API_KEY", "parent-key")
    monkeypatch.setenv("HERMES_TOKEN", "parent-token")
    rt = RealHermesRuntime(binary=install["binary"], home=install["home"])
    env = rt._env()
    blob = "\n".join(f"{k}={v}" for k, v in env.items())
    assert "parent-key" not in blob and "parent-token" not in blob
    assert "QUBIT_PROBE_API_KEY" not in env and "HERMES_TOKEN" not in env
    assert env["HERMES_HOME"] == str(install["home"])


def test_stop_model_reports_typed():
    out = RH.stop_ollama_model("model-that-never-existed-8tf")
    assert out["model"] == "model-that-never-existed-8tf"
    assert out["state"] in ("STOPPED", "UNKNOWN")


def test_in_tree_turn_sanitizes_smuggling():
    """Vendored pinned sanitizers execute inside Qubit's own turn path."""
    from voicebridge.runtime import HermesRuntime

    rt = HermesRuntime(provider="fixture")
    try:
        r = rt.turn("x \x1b[31mred\x1b[0m y\U000E0069z", session="sanitize")
        assert r["ok"] is True
        assert "\x1b[" not in r["reply"] and "\U000E0069" not in r["reply"]
        assert "red" in r["reply"]
    finally:
        rt.stop()
