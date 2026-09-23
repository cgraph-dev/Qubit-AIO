"""Regression tests for the final integrity wave.

Covers: resolved-model threading, honest streaming capability flags, the
sanitized tracked Modelfile, the installer Modelfile fallback, the external
TTS egress/emergency-stop guard, and the canonical schedule executor path.
Hermetic: no network, no live model, no services.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# --------------------------------------------------------------- fix 1
def test_agent_turn_uses_the_passed_model(monkeypatch):
    from voicebridge import agent as AG

    seen: dict = {}

    def _fake_chat_local(text, conversation_prompt="", *, model="", **kw):
        seen["model"] = model
        return "hello from the model"

    monkeypatch.setattr("voicebridge.local_llm.chat_local", _fake_chat_local)

    class _Cfg:
        def get(self, *path, default=None):
            if path == ("agent", "enabled"):
                return True
            if path == ("agent", "model"):
                return "stale-agent-model"
            return default

    res = AG.turn(_Cfg(), "hi", session="s", model="resolved-model")
    assert res["ok"] is True
    assert seen["model"] == "resolved-model"
    assert res["receipt"]["model"] == "resolved-model"


def test_chat_transport_forwards_resolved_model(monkeypatch):
    from voicebridge import chat_transport as CT

    captured: dict = {}

    class _Store:
        def append(self, sid, role, text, meta=None):
            captured["meta"] = meta
            return {"state": "OK", "message": {"role": role},
                    "message_count": 1}

    monkeypatch.setattr(CT, "store", lambda cfg: _Store())

    def _fake_turn(cfg, prompt, **kw):
        captured["model"] = kw.get("model")
        return {"ok": True, "reply": "ok",
                "receipt": {"model": kw.get("model")}}

    monkeypatch.setattr("voicebridge.agent.turn", _fake_turn)

    out = CT._generate(None, "sid", "hi", "resolved-model", "",
                       timeout_s=1.0, cancel=None, routed={"kind": "local"})
    assert out["ok"] is True
    assert captured["model"] == "resolved-model"
    assert captured["meta"]["model"] == "resolved-model"


# --------------------------------------------------------------- fix 2
def test_streaming_capability_defaults_are_honest():
    from voicebridge.providers.catalog import (
        ModelEntry, catalog_from_allowlist)
    from voicebridge.providers.resolver import (
        ModelProfile, default_profiles)

    assert ModelEntry(provider="p", model="m").supports_streaming is False
    cat = catalog_from_allowlist(["openrouter/x", "zen/y"])
    assert all(e.supports_streaming is False for e in cat.models.values())
    # the local runner streams; an external profile must opt in explicitly
    assert ModelProfile(id="api", kind="api").supports_streaming is False
    assert ModelProfile(id="loc", kind="local").supports_streaming is False
    assert default_profiles()[0].supports_streaming is True


def test_frontier_require_streaming_fails_closed(monkeypatch):
    from voicebridge.providers import gateway as G
    from voicebridge.providers.catalog import catalog_from_allowlist
    from voicebridge.providers.contracts import ProviderRequest
    from voicebridge.providers.policy import FrontierPolicy, reset_governor

    reset_governor()
    monkeypatch.setenv("QUBIT_TEST_KEY", "k")

    class _Cfg:
        def get(self, *path, default=None):
            if path[:2] == ("intent", "frontier"):
                node = {
                    "enabled": True, "route": "frontier",
                    "allowlist": ["zen/m1"],
                    "providers": {"zen": {
                        "base_url": "https://zen.example/v1",
                        "key_ref": "env:QUBIT_TEST_KEY"}},
                    "max_tokens": 64, "max_context_chars": 4000,
                    "max_response_chars": 4000, "timeout_s": 5.0,
                    "max_retries": 1,
                }
                for p in path[2:]:
                    node = node[p] if isinstance(node, dict) else default
                return node
            return default

    cfg = _Cfg()
    cat = catalog_from_allowlist(FrontierPolicy.from_config(cfg).allowlist)
    called: list = []
    res = G.run(
        ProviderRequest(prompt="hi", route="frontier", provider="zen",
                        model="m1", require_streaming=True),
        cfg, catalog=cat, transport=lambda *a: (called.append(1), (200, {}))[1])
    assert not res.ok
    assert res.code == "UNSUPPORTED_CAPABILITY"
    assert called == [], "a non-streaming frontier model must fail before I/O"
    reset_governor()


# --------------------------------------------------------------- fix 3
_PII = re.compile(
    r"(\+40\s?750|40750200004|744863501217955940|8153897846|"
    r"contact@cgraph\.org|instagram\.com/tr\.cky|cgraph-dev)")


def test_tracked_modelfile_has_no_baked_pii():
    text = (REPO_ROOT / "Modelfile.qubit").read_text(encoding="utf-8")
    assert not _PII.search(text), "Modelfile.qubit still bakes private data"


def test_installer_falls_back_to_tracked_sanitized_modelfile():
    src = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "tests/fixtures/modelfiles/qubit4.sanitized" in src
    # the retired 3B alias must not be pulled by the default install
    assert "ollama pull huihui_ai/llama3.2-abliterate:3b" not in src


def test_bake_brain_falls_back_to_tracked_fixture():
    src = (REPO_ROOT / "scripts" / "bake_brain.py").read_text(encoding="utf-8")
    assert "qubit4.sanitized" in src


# --------------------------------------------------------------- fix 4
def test_tts_egress_guard_blocks_before_network(monkeypatch):
    from voicebridge.tts.base import TTSUnavailable
    from voicebridge.tts.openai_tts import OpenAITTS

    def _boom(*a, **k):
        raise AssertionError("network reached despite a denied egress guard")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    tts = OpenAITTS(egress_guard=lambda: False)
    with pytest.raises(TTSUnavailable):
        tts.synthesize("hello")


def test_edge_tts_egress_guard_blocks_before_network(monkeypatch):
    from voicebridge.tts.base import TTSUnavailable
    from voicebridge.tts.edge import EdgeTTS

    called: list = []

    def _boom(*a, **k):
        called.append(1)
        raise AssertionError("edge-tts network reached despite a denied guard")

    # Any attempt to reach the edge_tts module/network is a failure.
    monkeypatch.setattr("subprocess.run", _boom)
    tts = EdgeTTS(egress_guard=lambda: False)
    with pytest.raises(TTSUnavailable):
        tts.synthesize("hello")
    assert called == []


def test_local_prompt_has_no_baked_personal_handle():
    from voicebridge import local_llm
    assert not _PII.search(local_llm.SYSTEM_PROMPT)
    assert "cgraph" not in local_llm.SYSTEM_PROMPT.lower()


def _git_ls(*paths: str) -> str:
    import subprocess
    out = subprocess.run(["git", "ls-files", *paths], cwd=REPO_ROOT,
                         capture_output=True, text=True)
    return out.stdout.strip()


def test_personal_knowledge_is_not_tracked():
    assert _git_ls("config/personal-knowledge.md") == "", \
        "config/personal-knowledge.md must not be git-tracked (rule 29)"
    assert (REPO_ROOT / "config" / "personal-knowledge.md").is_file(), \
        "the operator's local working file must be preserved on disk"


def test_task_templates_is_not_tracked():
    assert _git_ls("config/task-templates.yaml") == "", \
        "config/task-templates.yaml must not be git-tracked (rule 29)"
    assert (REPO_ROOT / "config" / "task-templates.yaml").is_file(), \
        "the operator's local working file must be preserved on disk"
    src = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "task-templates.example.yaml" in src


def test_open_project_requires_a_configured_root():
    """Generic open-project patterns must not capture arbitrary task text."""
    from voicebridge.local import actions
    m = actions.match(
        "open the current project and inspect the stripe webhook validation")
    assert m is None or m.name != "open_project"


def test_production_modules_bake_no_operator_identity():
    """No shipped voicebridge module may hard-code the operator name/handle."""
    import subprocess
    bad = re.compile(r"Tricky|cgraph-dev|RankValut|CGraph")
    out = subprocess.run(
        ["git", "grep", "-n", "-I", "-e", r"Tricky", "-e", r"cgraph-dev",
         "-e", r"RankValut", "-e", r"CGraph", "--", "voicebridge/"],
        cwd=REPO_ROOT, capture_output=True, text=True)
    for line in out.stdout.splitlines():
        # The only allowed match is the tz-database string Europe/Bucharest,
        # which is not an operator identity.
        if "Europe/Bucharest" in line:
            continue
        assert not bad.search(line), f"baked operator identity: {line}"


def test_external_speech_guard_emergency_stop_and_consent(monkeypatch):
    from voicebridge.providers.speech import external_speech_guard

    class _P:
        def __init__(self, stopped):
            self._stopped = stopped

        def status(self):
            return {"emergency_stop": self._stopped, "global_disable": False}

    class _Cfg:
        def __init__(self, consent):
            self._consent = consent

        def get(self, *path, default=None):
            return self._consent if path[-1] == "egress_consent" else default

    monkeypatch.setattr("voicebridge.power.power_control",
                        lambda cfg=None, **k: _P(True))
    assert external_speech_guard(_Cfg("allow"))() is False

    monkeypatch.setattr("voicebridge.power.power_control",
                        lambda cfg=None, **k: _P(False))
    assert external_speech_guard(_Cfg("allow"))() is True
    assert external_speech_guard(_Cfg("deny"))() is False


# --------------------------------------------------------------- fix 5
def test_schedule_executor_uses_canonical_dashboard_ops_dispatch(monkeypatch):
    """S3 moved scheduled dispatch onto the one-operation broker.

    ``_default_executor`` (with a real ``cfg``) must route through
    ``schedules._contract_dispatch`` → ``dashboard_ops.run_op`` with op
    ``schedule_dispatch`` under the monitor actor, and must never bypass the
    broker by calling ``chat_transport.turn`` directly.
    """
    from voicebridge import chat_transport as CT
    from voicebridge import dashboard_ops as OPS
    from voicebridge import schedules as S

    seen: dict = {}

    def _fake_run_op(cfg, op_id, args, *, actor=None, session=None, **kw):
        seen["op"] = op_id
        seen["args"] = dict(args)
        seen["actor"] = actor
        seen["session"] = session
        return {"state": "executed", "code": "OK",
                "receipt": {"result": {"state": "OK", "code": "OK",
                                       "detail": "done"}}}

    def _boom(*a, **k):
        raise AssertionError(
            "scheduled dispatch must not bypass dashboard_ops.run_op")

    monkeypatch.setattr(OPS, "run_op", _fake_run_op)
    monkeypatch.setattr(CT, "turn", _boom)

    out = S._default_executor("do the thing", session="job:1",
                              estop_engaged=lambda: False, cfg=object())
    assert out["state"] == "OK"
    assert seen["op"] == "schedule_dispatch"
    assert seen["args"]["task_text"] == "do the thing"
    assert seen["args"]["session"] == "job:1"
    assert seen["args"]["directive"] == "false"
    assert seen["actor"] == S.MONITOR_ACTOR
    assert seen["session"] == "job:1"
