"""Packet 4D tests: typed L2 planner, VRAM guard, deep-tier cycle,
bake-off manifest structure. Live model work is opt-in, never in pytest.
"""
from __future__ import annotations

import json
import sys
import subprocess
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.planner import (  # noqa: E402
    PLANNER_SCHEMA, PLANNER_TOOLS, TypedProposal, plan)
from voicebridge.vram_guard import (  # noqa: E402
    RestoreReport, VRAMGuardError, free_vram_mib, guard_load)


class _FakeResp:
    def __init__(self, obj):
        self._obj = json.dumps(obj).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def read(self, n=-1):
        return self._obj

    def close(self):
        pass


def _ollama_reply(content: str):
    return {"message": {"role": "assistant", "content": content},
            "done": True, "eval_count": 10}


# ------------------------------------------------------------- planner
def test_planner_schema_is_locked_and_minimal():
    tools = set(PLANNER_SCHEMA["properties"]["tool"]["enum"])
    assert tools == set(PLANNER_TOOLS) | {"none"}   # abstention is legal
    assert PLANNER_SCHEMA["required"] == ["tool", "confidence"]
    assert PLANNER_SCHEMA["additionalProperties"] is False
    # smallest schemas: no argument bloat
    for name, spec in PLANNER_TOOLS.items():
        assert name.startswith("local."), "shortlist is whitelisted-only"
        assert len(spec["args"]) <= 1


def test_planner_happy_path_open_app(monkeypatch):
    def fake(req, timeout=None, **kw):
        return _FakeResp(_ollama_reply(json.dumps(
            {"tool": "local.open_app", "arguments": {"app": "calculator"},
             "confidence": 0.9})))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    p = plan("open the calculator please")
    assert isinstance(p, TypedProposal)
    assert p.tool == "local.open_app"
    assert p.arguments == {"app": "calculator"}
    assert p.version == "1.0.0"
    # data-only proposal: no execution surface
    assert not hasattr(p, "execute") and not hasattr(p, "run")


def test_planner_unknown_tool_fails_closed(monkeypatch):
    for bad in ("local.delete_everything", "shell", "none"):
        def fake(req, timeout=None, **kw):
            return _FakeResp(_ollama_reply(json.dumps(
                {"tool": bad, "arguments": {}, "confidence": 0.99})))
        monkeypatch.setattr(urllib.request, "urlopen", fake)
        assert plan("do anything at all") is None


def test_planner_undeclared_argument_fails_closed(monkeypatch):
    def fake(req, timeout=None, **kw):
        return _FakeResp(_ollama_reply(json.dumps(
            {"tool": "local.volume_up",
             "arguments": {"percent": 80}, "confidence": 0.9})))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert plan("set volume higher") is None   # volume_up takes NO args


def test_planner_missing_required_arg_fails_closed(monkeypatch):
    def fake(req, timeout=None, **kw):
        return _FakeResp(_ollama_reply(json.dumps(
            {"tool": "local.play_youtube",
             "arguments": {}, "confidence": 0.9})))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert plan("play some jazz") is None


def test_planner_low_confidence_and_garbage_fail_closed(monkeypatch):
    def low(req, timeout=None, **kw):
        return _FakeResp(_ollama_reply(json.dumps(
            {"tool": "local.screenshot", "arguments": {},
             "confidence": 0.2})))
    monkeypatch.setattr(urllib.request, "urlopen", low)
    assert plan("screenshot") is None

    def junk(req, timeout=None, **kw):
        return _FakeResp(_ollama_reply("not json at all"))
    monkeypatch.setattr(urllib.request, "urlopen", junk)
    assert plan("anything") is None


def test_planner_transport_failure_is_typed_no_decision(monkeypatch):
    def down(req, timeout=None, **kw):
        raise OSError("refused")
    monkeypatch.setattr(urllib.request, "urlopen", down)
    assert plan("anything") is None


# ------------------------------------------------------------ vram guard
def test_vram_guard_refuses_under_budget(monkeypatch):
    monkeypatch.setattr("voicebridge.vram_guard.free_vram_mib",
                        lambda: 1000)
    with pytest.raises(VRAMGuardError, match="only 1000 MiB free"):
        guard_load(2600, what="candidate")


def test_vram_guard_fails_closed_when_unmeasurable(monkeypatch):
    monkeypatch.setattr("voicebridge.vram_guard.free_vram_mib",
                        lambda: None)
    with pytest.raises(VRAMGuardError, match="cannot measure"):
        guard_load(100, what="anything")


def test_vram_guard_passes_with_headroom(monkeypatch):
    monkeypatch.setattr("voicebridge.vram_guard.free_vram_mib",
                        lambda: 3300)
    assert guard_load(2600) == 3300


# ------------------------------------------------------- deep-tier cycle
class _ScriptedHTTP:
    """Stateful fake: /api/generate(keep_alive=0) unloads; /api/chat restores;
    /api/ps reports state."""

    def __init__(self):
        self.resident = True
        self.calls = []

    def __call__(self, req, timeout=None, **kw):
        url = req.full_url
        payload = json.loads(bytes(req.data or b"{}"))
        self.calls.append((url, payload))
        if url.endswith("/api/ps"):
            models = ([{"name": "qubit-brain4:latest", "digest": "d94",
                        "context_length": 4096}]
                      if self.resident else [])
            return _FakeResp({"models": models})
        if url.endswith("/api/generate"):
            self.resident = False
            return _FakeResp({})
        if url.endswith("/api/chat"):
            self.resident = True
            return _FakeResp({"message": {"content": "warm"}})
        raise AssertionError(url)


def test_deep_tier_cycle_evicts_restores_and_reports(monkeypatch):
    from voicebridge.vram_guard import deep_tier_cycle
    http = _ScriptedHTTP()
    monkeypatch.setattr(urllib.request, "urlopen", http)

    ran = []
    result, report = deep_tier_cycle(
        "http://x", "qubit-brain4", 4096,
        lambda: ran.append("deep-work"), poll_timeout_s=2)

    assert ran == ["deep-work"]
    assert report.restored is True and report.restore_ms >= 0
    kinds = [u.rsplit("/", 1)[-1] for u, _ in http.calls]
    # expire -> ps poll(s) -> restore chat -> warm verify ps
    assert kinds[0] == "generate"
    assert any(k == "chat" for k in kinds)
    restore_payload = next(p for u, p in http.calls if u.endswith("/api/chat"))
    assert restore_payload["keep_alive"] == -1
    assert restore_payload["options"]["num_ctx"] == 4096


def test_deep_tier_restore_failure_is_typed(monkeypatch):
    from voicebridge.vram_guard import deep_tier_cycle
    http = _ScriptedHTTP()

    def broken_chat(req, timeout=None, **kw):
        raise OSError("restore probe failed")

    real_call = http.__call__

    def flaky(req, timeout=None, **kw):
        if req.full_url.endswith("/api/chat"):
            return broken_chat(req, timeout=timeout)
        return real_call(req, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    _, report = deep_tier_cycle("http://x", "qubit-brain4", 4096,
                                lambda: None, poll_timeout_s=1)
    assert report.restored is False
    assert report.detail == "restore failed"


# ------------------------------------------------- bake-off manifest shape
def test_bakeoff_manifest_and_pins_are_recorded():
    src = (REPO_ROOT / "scripts" / "bakeoff_4d.py").read_text()
    assert "a06e946bb6b655725eafa393f4a9745d460374c9" in src
    assert "sha256" in src and "delete_command" in src
    assert 'FROM qubit-brain4' not in src.replace(
        '"production_alias"', "").replace("production alias", ""), \
        "candidates must never be created FROM the production alias"


def test_create_candidate_uses_aligned_persona_not_inline_v2():
    """Regression: the v2 inline persona used to shadow _aligned_persona()
    (verifier-found). The generated Modelfile must carry the ALIGNED text."""
    import scripts.bakeoff_4d as B
    src = Path(B.__file__).read_text()
    # exactly ONE persona assignment inside create_candidate
    body = src[src.index("def create_candidate"):]
    assert body.count("persona = ") == 1, "duplicate persona assignment"
    assert "_aligned_persona()" in body
    # and the aligned refusal line must exist in the winner config
    winner = json.loads(
        (REPO_ROOT / "docs" / "ALIGN_4D_winner.json").read_text())
    assert "REFUSE IMMEDIATE ACTIONS FOR DESTRUCTION" in winner["persona"]


def test_created_modelfile_carries_aligned_system(tmp_path):
    import scripts.bakeoff_4d as B
    fake_gguf = tmp_path / "fake.gguf"
    fake_gguf.write_bytes(b"gguf-placeholder")
    captured = {}

    def fake_run(cmd, capture_output=True, text=True):
        class P:
            returncode = 0
            stderr = ""
        captured["modelfile"] = Path(cmd[cmd.index("-f") + 1]).read_text()
        return P()

    monkey = pytest.MonkeyPatch()

    def fake_urlopen(req, timeout=None, **kw):
        class R:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return None
            def read(self):
                return json.dumps(
                    {"details": {"quantization_level": "Q4_K_M"}}).encode()
        return R()

    monkey.setattr(subprocess, "run", fake_run)
    monkey.setattr(urllib.request, "urlopen", fake_urlopen)
    B.create_candidate("qubit-cand-test", fake_gguf)
    monkey.undo()
    mf = captured["modelfile"]
    import re as _re
    m = _re.search(r'SYSTEM """(.*?)"""', mf, _re.S)
    assert m, f"no SYSTEM block:\n{mf[:400]}"
    assert m.group(1).strip(), f"EMPTY persona:\n{mf[:400]}"
    sys_text = m.group(1)
    winner = json.loads(
        (REPO_ROOT / "docs" / "ALIGN_4D_winner.json").read_text())
    assert "REFUSE IMMEDIATE ACTIONS FOR DESTRUCTION" in sys_text
    assert sys_text.strip().startswith(winner["persona"].strip()[:40])
