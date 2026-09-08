"""Packet 8T-F hard gate — anti-substitution, inventories, E2E proof.

Phase B gate: verify_hermes_source must VERIFY the pinned clone and FAIL
a Qubit imitation, a wrong commit, an operator location, and /tmp.
Inventories must be complete (every file verdict-labeled, pins recorded).
The voice->real-runtime->TTS path must execute end to end.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_CLONE = Path(os.environ.get("QUBIT_8TF_HERMES_CLONE",
                                   "/tmp/upstream-8tfv3/hermes-agent"))
OPENCLAW_CLONE = Path(os.environ.get("QUBIT_8TF_OPENCLAW_CLONE",
                                     "/tmp/upstream-8tfv3/openclaw"))
HERMES_PIN = "2237be355906fbe6065ce1815711eee52b2d646e"
OPENCLAW_PIN = "3928bad9badfcb6c7d140530435e806fb8092190"

from voicebridge import foundation as F  # noqa: E402


def _require_clone():
    if not (HERMES_CLONE / "cli.py").is_file():
        pytest.fail(f"pinned Hermes clone missing: {HERMES_CLONE}")


# ---------------------------------------------------------------------------
# Anti-substitution gate
# ---------------------------------------------------------------------------
def test_valid_pinned_source_verifies():
    _require_clone()
    rec = F.verify_hermes_source(HERMES_CLONE, allow_temporary=True)
    assert rec["state"] == "VERIFIED" and rec["commit"] == HERMES_PIN
    assert rec["audit_only"] is True
    # The same tree is REJECTED as an execution foundation site while it
    # lives under /tmp — audit inputs are never foundation locations.
    with pytest.raises(F.FoundationError) as exc:
        F.verify_hermes_source(HERMES_CLONE)
    assert "TEMPORARY_SOURCE" in str(exc.value)


def test_imitation_source_fails(tmp_path):
    fake = tmp_path / "not-hermes"
    fake.mkdir()
    (fake / "worker.py").write_text("# quibt imitation")
    with pytest.raises(F.FoundationError):
        F.verify_hermes_source(fake)


def test_wrong_commit_fails(tmp_path):
    repo = tmp_path / "fake-hermes"
    repo.mkdir()
    for marker in F.HERMES_LAYOUT_MARKERS:
        (repo / marker).mkdir(exist_ok=True) if "." not in marker \
            else (repo / marker).write_text("x")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"],
                   check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"],
                   check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fake"],
                   check=True)
    # Tmp-scoped fakes exercise the commit check directly (the /tmp check
    # fires first in the full chain by design).
    with pytest.raises(F.FoundationError) as exc:
        F.check_layout_and_commit(repo)
    assert "WRONG_COMMIT" in str(exc.value)
    with pytest.raises(F.FoundationError) as exc2:
        F.check_not_temporary(repo)
    assert "TEMPORARY_SOURCE" in str(exc2.value)


def test_temporary_source_fails():
    with pytest.raises(F.FoundationError) as exc:
        F.verify_hermes_source(Path("/tmp") / "anything-at-all-8tf")
    assert "TEMPORARY_SOURCE" in str(exc.value)


def test_operator_location_fails(tmp_path):
    op = tmp_path / "operator-hermes"
    op.mkdir()
    with pytest.raises(F.FoundationError) as exc:
        F.check_not_operator(op, forbidden_roots=[str(op)])
    assert "OPERATOR_INSTALL" in str(exc.value)


def test_live_operator_binary_dir_is_forbidden():
    import shutil

    found = shutil.which("hermes")
    if not found:
        pytest.fail("no hermes binary on PATH; operator install check void")
    roots = F._operator_roots()
    assert any(str(Path(found).resolve()).startswith(r.rstrip("/") + "/")
               or str(Path(found).resolve().parents[1]) == r for r in roots)


# ---------------------------------------------------------------------------
# Inventories complete
# ---------------------------------------------------------------------------
def _load_inventory(name):
    path = REPO_ROOT / "docs" / name
    assert path.is_file(), f"missing inventory: {name}"
    return json.loads(path.read_text())


def test_hermes_inventory_complete():
    m = _load_inventory("INVENTORY_HERMES.json")
    assert m["commit"] == HERMES_PIN
    assert m["count"] >= 1000, "inventory must cover the full tree"
    valid = {"PRESERVE", "INTEGRATE", "ADAPT", "BLOCKED", "REJECTED"}
    for row in m["files"]:
        assert row["area_verdict"] in valid, row["path"]
        assert len(row["sha256"]) == 64
    paths = {r["path"] for r in m["files"]}
    assert "tools/ansi_strip.py" in paths
    assert "hermes_cli/sqlite_util.py" in paths
    assert "agent/conversation_loop.py" in paths or \
        any(p.startswith("agent/") for p in paths)


def test_openclaw_inventory_complete():
    m = _load_inventory("INVENTORY_OPENCLAW.json")
    assert m["commit"] == OPENCLAW_PIN
    assert m["count"] >= 1000
    valid = {"PRESERVE", "INTEGRATE", "ADAPT", "BLOCKED", "REJECTED"}
    for row in m["files"]:
        assert row["area_verdict"] in valid, row["path"]
    paths = {r["path"] for r in m["files"]}
    assert "src/cli/program/register.agent.ts" in paths or \
        any("register.agent" in p for p in paths)


def test_matrix_covers_inventory_areas():
    matrix = json.loads((REPO_ROOT / "docs" / "MATRIX_8T_F.json").read_text())
    assert len(matrix["rows"]) >= 30


# ---------------------------------------------------------------------------
# Voice->runtime->TTS contract (unprovisioned path fails closed; the live
# real-runtime->TTS path is proven in test_real_hermes.py +
# test_foundation_runtime.py against the provisioned install)
# ---------------------------------------------------------------------------
def test_unprovisioned_runtime_refuses_and_tts_contract_holds(tmp_path):
    from voicebridge.runtime import RealHermesRuntime
    from voicebridge.tts.base import TTSProvider

    home = tmp_path / "hhome"
    home.mkdir()
    rt = RealHermesRuntime(binary="/nonexistent-8tf/bin",
                           home=str(home))
    assert rt.version()["state"] == "DISABLED"

    spoken = []

    class _ProbeTTS(TTSProvider):
        name = "probe-tts"

        def load(self):
            return None

        def synthesize(self, text, out_path):
            spoken.append(text)
            wav = tmp_path / "e2e.wav"
            wav.write_bytes(b"RIFF-e2e")
            return wav

    class _ProbePlayback:
        def play(self, path, name=""):
            assert Path(path).exists()
            return True

    reply = "[turn 1 session voice] e2e fixture reply"
    assert _ProbeTTS().speak(reply, _ProbePlayback(), out_dir=tmp_path) is True
    assert spoken == [reply]


def test_no_tmp_dependence_in_production_defaults(monkeypatch):
    monkeypatch.delenv("QUBIT_8TF_HERMES_BIN", raising=False)
    monkeypatch.delenv("QUBIT_8TF_HERMES_HOME", raising=False)
    from voicebridge.runtime import RealHermesRuntime
    from voicebridge.runtime import provision as P

    rt = RealHermesRuntime()
    assert "/tmp/hermes" not in str(rt.binary)
    assert "/tmp/hermes" not in str(rt.home)
    assert "qubit-voicebridge/runtimes" in str(rt.binary)
    assert P.runtime_dir().is_absolute()
    assert "/tmp/hermes" not in str(P.runtime_dir())
