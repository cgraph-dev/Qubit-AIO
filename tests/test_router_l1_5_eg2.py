"""L1.5 EmbeddingGemma-2 registry augmenter — hermetic safety contract.

No network, no live config, no service. Embeddings are injected; the
optional real-model test skips when the stable model directory is absent.
"""
from __future__ import annotations

import re
import zlib
from pathlib import Path

import pytest

from voicebridge import voice_commands as VC
from voicebridge.config import DEFAULTS
from voicebridge.router import l1_5
from voicebridge.router.pipeline import RouterPipeline


class FakeCfg:
    def __init__(self, *, enabled: bool = True, threshold: float = 0.87,
                 margin: float = 0.02,
                 model_dir: str = "/nonexistent/eg2") -> None:
        self.data = {"intent": {"router": {
            "l1_5_enabled": enabled,
            "l1_5_threshold": threshold,
            "l1_5_margin": margin,
            "l1_5_model_dir": model_dir,
        }}}

    def get(self, *path, default=None):
        node = self.data
        for part in path:
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


class BagEmbedder:
    """Deterministic lexical bag-of-words vectors (no model)."""

    dim = 768

    def __init__(self) -> None:
        self.calls = 0

    def _vector(self, text: str):
        vec = [0.0] * self.dim
        for word in re.findall(r"[a-z0-9']+", (text or "").lower()):
            bucket = zlib.crc32(word.encode()) % self.dim
            vec[bucket] += 1.0
        return vec

    def embed(self, texts):
        self.calls += 1
        return [self._vector(t) for t in texts]


class OneHotEmbedder:
    """Adversarial model: maps every query to a chosen entry prototype."""

    dim = 768

    def __init__(self, entries, query_targets):
        self.ids = [str(e.id) for e in entries]
        assert len(self.ids) <= self.dim
        self.index = {entry_id: i for i, entry_id in enumerate(self.ids)}
        texts, owners = l1_5._prototype_texts(entries)
        self.owner_by_text = dict(zip(texts, owners))
        self.query_targets = dict(query_targets)
        self.calls = 0

    def _vector(self, text: str):
        vec = [0.0] * self.dim
        entry_id = self.query_targets.get(text) or self.owner_by_text.get(text)
        if entry_id in self.index:
            vec[self.index[entry_id]] = 1.0
        return vec

    def embed(self, texts):
        self.calls += 1
        return [self._vector(t) for t in texts]


def _sub_entries(*ids):
    lookup = {str(e.id): e for e in VC._iter_entries()}
    return [lookup[i] for i in ids]


@pytest.fixture(autouse=True)
def _clean_test_hook():
    l1_5.reset_for_tests()
    yield
    l1_5.reset_for_tests()


# --------------------------------------------------------------- flag gates
def test_config_default_is_disabled_and_wired():
    router = DEFAULTS["intent"]["router"]
    assert router["l1_5_enabled"] is False
    assert 0.80 <= router["l1_5_threshold"] <= 0.95
    assert float(router["l1_5_margin"]) > 0
    assert "embeddinggemma" in router["l1_5_model_dir"]
    assert RouterPipeline(FakeCfg(enabled=False)).l1_5_enabled is False
    assert RouterPipeline(FakeCfg(enabled=True)).l1_5_enabled is True


def test_env_override_reaches_l1_5_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_BRIDGE__INTENT__ROUTER__L1_5_ENABLED", "true")
    from voicebridge.config import Config
    cfg = Config.load(tmp_path / "absent.yaml")
    assert bool(cfg.get("intent", "router", "l1_5_enabled", default=False))


def test_flag_off_never_embeds_or_matches():
    embedder = BagEmbedder()
    cfg = FakeCfg(enabled=False)
    decision = l1_5.augment_unknown(
        "show me the uncommitted diff", cfg=cfg,
        entries=_sub_entries("git_diff", "status"), embedder=embedder)
    assert decision is None
    assert embedder.calls == 0


def test_deterministic_registry_always_wins():
    embedder = BagEmbedder()
    cfg = FakeCfg(enabled=True)
    augmenter = l1_5.RegistryAugmenter(
        _sub_entries("git_diff", "status"), cfg, embedder)
    augmenter.fit()
    l1_5.install_test_augmenter(augmenter)
    resolution = VC.resolve("show diff")
    assert resolution.kind == VC.MATCH
    assert resolution.entry is not None and resolution.entry.id == "git_diff"
    assert embedder.calls == 1          # fit only, never a query embed


def test_l0_precedence_skips_l1_5():
    embedder = BagEmbedder()
    decision = l1_5.augment_unknown(
        "turn the volume up", cfg=FakeCfg(enabled=True),
        entries=_sub_entries("status", "git_diff"), embedder=embedder)
    assert decision is None
    assert embedder.calls == 0


def test_pending_confirmation_is_refused():
    embedder = BagEmbedder()
    decision = l1_5.augment_unknown(
        "show me the uncommitted diff", cfg=FakeCfg(enabled=True),
        pending_confirmation=True,
        entries=_sub_entries("git_diff", "status"), embedder=embedder)
    assert decision is None
    assert embedder.calls == 0


def test_low_confidence_is_no_match():
    embedder = BagEmbedder()
    decision = l1_5.augment_unknown(
        "zebra quasar nebula", cfg=FakeCfg(enabled=True),
        entries=_sub_entries("git_diff", "status"), embedder=embedder)
    assert decision is None


def test_model_unavailable_is_no_match():
    decision = l1_5.augment_unknown(
        "show me the uncommitted diff",
        cfg=FakeCfg(enabled=True, model_dir="/nonexistent/eg2"),
        entries=_sub_entries("git_diff", "status"))
    assert decision is None


def test_benign_unknown_can_match_a_safe_entry():
    embedder = BagEmbedder()
    decision = l1_5.augment_unknown(
        "show diff", cfg=FakeCfg(enabled=True, threshold=0.5, margin=0.0),
        entries=_sub_entries("git_diff", "status"), embedder=embedder)
    assert decision is not None and decision.entry_id == "git_diff"


# ----------------------------------------------------------- hard risk veto
HIGH_RISK_PROBES = [
    "stop the music", "pause the music", "stop talking", "shut up",
    "stop everything", "emergency stop", "resume work", "stop",
    "cancel that", "no don't do it", "yes do it", "confirm",
    "don't send it", "do not delete anything", "delete session 3",
    "archive session 3", "forget session 3", "send the message",
    "send draft", "draft a message to sam", "approve pairing",
    "reject the pairing", "pay the invoice", "buy it now",
    "transfer the money", "wipe the memory", "remember this",
    "shutdown the computer", "reboot the machine", "kill the process",
    "open the terminal", "take a screenshot", "mute",
    "unmute the microphone", "delete the file", "rm -rf the project",
    "cancel my autonomy", "revoke autonomy", "pause autonomy",
]
assert len(HIGH_RISK_PROBES) == 39

RISK_TARGETS = [
    "pause", "session_delete", "memory_forget", "msg_send",
    "pairing_approve", "pairing_revoke", "autonomy_pause",
    "autonomy_revoke", "ghost_estop", "actions_execute", "update_apply",
    "browser_open", "session_archive", "msg_draft", "memory_remember",
]


def _adversarial_entries():
    return _sub_entries(*(RISK_TARGETS + [
        "git_diff", "status", "memory_search", "files_list",
        "sessions", "browser_screenshot"]))


def test_adversarial_model_cannot_produce_a_high_risk_route():
    entries = _adversarial_entries()
    targets = {
        text: RISK_TARGETS[i % len(RISK_TARGETS)]
        for i, text in enumerate(HIGH_RISK_PROBES)
    }
    embedder = OneHotEmbedder(entries, targets)
    cfg = FakeCfg(enabled=True, threshold=0.87, margin=0.0)
    decisions = {}
    for text in HIGH_RISK_PROBES:
        decisions[text] = l1_5.augment_unknown(
            text, cfg=cfg, entries=entries, embedder=embedder)
    high_risk_routes = [
        text for text, decision in decisions.items()
        if decision is not None and l1_5.entry_is_high_risk(
            next(e for e in entries if str(e.id) == decision.entry_id))
    ]
    assert high_risk_routes == []
    assert all(decision is None for decision in decisions.values())
    # The veto is real, not vacuous: an unmarked probe still reaches the
    # adversarial embedder and is rejected because its candidate is risky.
    for text in ("remember this", "draft a message to sam",
                 "open the terminal", "take a screenshot"):
        assert l1_5.is_risky_utterance(text) is False
    assert embedder.calls > 0


def test_l1_5_adds_zero_high_risk_routes_to_resolve():
    entries = _adversarial_entries()
    targets = {
        text: RISK_TARGETS[i % len(RISK_TARGETS)]
        for i, text in enumerate(HIGH_RISK_PROBES)
    }
    embedder = OneHotEmbedder(entries, targets)
    augmenter = l1_5.RegistryAugmenter(
        entries, FakeCfg(enabled=True, threshold=0.87, margin=0.0), embedder)
    augmenter.fit()
    off = FakeCfg(enabled=False)
    baseline = {text: VC.resolve(text, cfg=off)
                for text in HIGH_RISK_PROBES}
    l1_5.install_test_augmenter(augmenter)
    for text in HIGH_RISK_PROBES:
        resolution = VC.resolve(text)
        before = baseline[text]
        assert resolution.kind == before.kind
        assert (resolution.entry.id if resolution.entry else None) == \
            (before.entry.id if before.entry else None)
        if before.kind == VC.UNKNOWN:
            assert resolution.kind == VC.UNKNOWN


# --------------------------------------------------------- real model (opt)
MODEL_DIR = Path(l1_5.DEFAULT_MODEL_DIR).expanduser()


@pytest.mark.skipif(not MODEL_DIR.is_dir(),
                    reason="EmbeddingGemma-2 ONNX model not installed")
def test_real_model_embeds_768d_and_matches_show_diff():
    embedder = l1_5.OnnxEg2Embedder(MODEL_DIR)
    vector = embedder.embed(["show diff"])[0]
    assert len(vector) == 768
    entries = _sub_entries("git_diff", "status", "session_delete")
    cfg = FakeCfg(enabled=True, model_dir=str(MODEL_DIR))
    augmenter = l1_5.RegistryAugmenter(entries, cfg, embedder)
    augmenter.fit()
    decision = augmenter.decide("show diff")
    assert decision is not None and decision.entry_id == "git_diff"
    assert augmenter.decide("delete session 3") is None
