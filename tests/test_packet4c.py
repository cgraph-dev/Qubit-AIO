"""Packet 4C tests: L0 deterministic routing, optional L1 semantic router,
feature-flag gating, and honest evaluation evidence.

L0/L1 unit paths never touch the network. Live embedding-gate tests skip
typed when Ollama is unavailable.
"""
from __future__ import annotations

import json
import statistics
import sys
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.intent.classifier import Classifier  # noqa: E402
from voicebridge.intent.state import (  # noqa: E402
    ConfirmationState, ConversationState)
from voicebridge.router.l0 import route as l0_route  # noqa: E402
from voicebridge.router.l1 import NO_MATCH, SemanticRouter  # noqa: E402
from voicebridge.router.pipeline import RouterPipeline, build_pipeline  # noqa: E402


def _no_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ------------------------------------------------------------------- L0
def test_l0_never_touches_network(monkeypatch):
    _no_network(monkeypatch)
    for t in ("mute", "what's the weather tomorrow", "hello there!",
              "write a python script to rename files", "asdf qwerty zzz"):
        l0_route(t)


def test_l0_decisions_match_expected_kinds():
    assert l0_route("mute").kind == "system"
    assert l0_route("unmute the microphone").kind == "local"
    d = l0_route("sure.", pending_confirmation=True)
    assert d.kind == "confirm"
    d = l0_route("nope.", pending_confirmation=True)
    assert d.kind == "cancel"
    assert l0_route("hello there!").kind == "conversation"
    assert l0_route(
        "write a python script that renames files").kind == "coding"
    assert l0_route("Ce mai faci, draga mea?").kind == "clarify"
    # ambiguous utterance is NOT decided at L0 — handed upward
    assert l0_route("can you handle the thing from before?") is None


def test_l0_latency_p95_under_10ms():
    texts = ["mute", "hello there!", "write a python script now",
             "unmute the mic", "what's the weather", "random gibberish xyz"]
    lat = []
    for i in range(200):
        t0 = __import__("time").perf_counter()
        l0_route(texts[i % len(texts)])
        lat.append((__import__("time").perf_counter() - t0) * 1000.0)
    s = sorted(lat)
    p95 = s[int(len(s) * 0.95)]
    assert p95 < 10.0, f"L0 p95 {p95:.3f}ms violates the 10ms gate"


def test_l0_busy_stop_and_empty():
    assert l0_route("stop the task now", busy=True).kind == "cancel"
    assert l0_route("").kind == "conversation"   # typed empty handling


# ------------------------------------------------------------------- L1
class _FakeEmbed:
    """Deterministic fake embedding space for pure-logic L1 tests."""

    def __init__(self):
        # orthogonal 4D route axes (realistic distinct semantic directions)
        self.axes = {"conversation": [1.0, 0.0, 0.0, 0.0],
                     "coding": [0.0, 1.0, 0.0, 0.0],
                     "local": [0.0, 0.0, 1.0, 0.0],
                     "clarify": [0.0, 0.0, 0.0, 1.0]}

    def __call__(self, texts, url=None, model=None, timeout_s=None):
        import math
        out = []
        for t in texts:
            v = [0.05, 0.05, 0.05, 0.05]
            for route, axis in self.axes.items():
                if any(w in t.lower() for w in self._words(route)):
                    v = [a + b for a, b in zip(v, axis)]
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])   # realistic unit embeddings
        return out

    @staticmethod
    def _words(route):
        return {
            "conversation": ["day", "joke", "space", "nice"],
            "coding": ["script", "test", "code", "refactor"],
            "local": ["volume", "music", "editor", "screenshot", "mic"],
            "clarify": ["thingy", "somehow", "usual", "earlier"],
        }[route]


def test_l1_fit_calibrate_decide_with_fake_embeddings():
    r = SemanticRouter(routes={
        "conversation": ["how was your day", "tell me a joke about space",
                         "say something nice"],
        "coding": ["write a script", "fix the failing test",
                   "refactor this code"],
        "local": ["set the volume", "pause the music",
                  "open the editor screenshot"],
        "clarify": ["the thingy somehow", "that usual thing earlier"],
    })
    r._embed_fn = _FakeEmbed()
    r.fit(url="x", model="fake")
    thr = r.calibrate([("conversation", "how was your day"),
                       ("coding", "write a script"),
                       ("local", "set the volume")],
                      url="x", model="fake")
    assert set(thr) == set(r.centroids)
    d = r.decide_vector(r.embed(["tell me a joke"], query=True)[0])
    assert d is not NO_MATCH and d.route == "conversation"
    assert d.margin >= 0.05
    assert d.runner_up in ("coding", "local", "clarify")


def test_l1_margin_gate_yields_explicit_no_match():
    r = SemanticRouter(routes={"conversation": ["how was your day"],
                               "coding": ["write a script"]})
    r._embed_fn = _FakeEmbed()
    r.fit()
    # ambiguous vector near two routes -> margin gate must abstain
    amb = [0.62, 0.60, 0.05, 0.05]
    r.thresholds = {"conversation": 0.3, "coding": 0.3}
    assert r.decide_vector(amb) is NO_MATCH


def test_l1_requires_fit_before_use():
    r = SemanticRouter(routes={"a": ["x"]})
    with pytest.raises(RuntimeError):
        r.decide("x")


# --------------------------------------------------------------- pipeline
def test_pipeline_disabled_by_default_returns_none_without_network(
        monkeypatch):
    _no_network(monkeypatch)

    class C:
        def get(self, *path, default=None):
            return default

    pipe = build_pipeline(C())
    assert pipe.enabled is False
    assert pipe.route("mute the mic") == (None, None)


def test_pipeline_l0_stage_when_enabled(monkeypatch):
    _no_network(monkeypatch)

    class C:
        def get(self, *path, default=None):
            if path[-1] == "enabled":
                return True
            if path[-1] == "l1_enabled":
                return False
            return None

    pipe = RouterPipeline(C())
    d0, d1 = pipe.route("mute")
    assert d0 is not None and d0.kind == "system"
    assert d1 is None
    assert pipe.last_l0_ms is not None and pipe.last_stage == "l0"


def test_classifier_integration_respects_attached_router(monkeypatch):
    _no_network(monkeypatch)

    class FakePipe:
        enabled = True

        def route(self, text, **kw):
            from voicebridge.router.l0 import RouteDecision
            return RouteDecision("system", 0.98, "test:fixed"), None

    cfg = {"llm_classifier": False}
    clf = Classifier(cfg, confirmations=ConfirmationState(),
                     conversation=ConversationState())
    clf.attach_router(FakePipe())
    intent = clf.classify("whatever text at all")
    assert intent.kind == "system"
    assert intent.reason.startswith("l0:")


def test_classifier_without_router_is_unchanged():
    from voicebridge.intent.classifier import Classifier as C
    clf = C({"llm_classifier": False}, confirmations=ConfirmationState(),
            conversation=ConversationState())
    assert clf._router is None          # default OFF, legacy path intact
    assert clf.classify("mute").kind == "system"


# ------------------------------------------- PRODUCTION WIRING (verifier)
class _FlagCfg:
    """Config stand-in carrying intent.router.* flags."""

    def __init__(self, router_enabled, l1_enabled=False):
        self.data = {"intent": {"router": {"enabled": router_enabled,
                                           "l1_enabled": l1_enabled}}}

    def get(self, *path, default=None):
        node = self.data
        for p in path:
            if not isinstance(node, dict) or p not in node:
                return default
            node = node[p]
        return node


def test_production_wiring_activates_l0_when_flag_enabled(monkeypatch):
    """Verifier-required: intent.router.enabled=true must activate L0 through
    the REAL production construction path (_attach_router_pipeline, the same
    function Orchestrator.__init__ calls) — no dead switch."""
    _no_network(monkeypatch)
    from voicebridge.intent.classifier import Classifier as C
    from voicebridge.orchestrator import _attach_router_pipeline

    clf = C({"llm_classifier": False}, confirmations=ConfirmationState(),
            conversation=ConversationState())
    assert clf._router is None                      # not yet wired
    _attach_router_pipeline(clf, _FlagCfg(router_enabled=True))
    assert clf._router is not None and clf._router.enabled is True
    intent = clf.classify("mute")                   # L0 short-circuit, no net
    assert intent.kind == "system"
    assert getattr(intent, "source", "") == "router" or \
        str(intent.reason).startswith("l0:")


def test_production_wiring_default_stays_inert(monkeypatch):
    _no_network(monkeypatch)
    from voicebridge.intent.classifier import Classifier as C
    from voicebridge.orchestrator import _attach_router_pipeline

    clf = C({"llm_classifier": False}, confirmations=ConfirmationState(),
            conversation=ConversationState())
    _attach_router_pipeline(clf, _FlagCfg(router_enabled=False))
    assert clf._router is None or clf._router.enabled is False


def test_orchestrator_init_calls_the_wiring():
    src = (REPO_ROOT / "voicebridge" / "orchestrator.py").read_text()
    assert "_attach_router_pipeline(self.classifier, self.cfg)" in src, \
        "Orchestrator.__init__ must wire the router pipeline"
    assert "def _attach_router_pipeline" in src


# ------------------------------------------------- live gates (typed skip)
def _ollama_up() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/version",
                                    timeout=3) as r:
            return bool(json.loads(r.read().decode()).get("version"))
    except Exception:
        return False


@pytest.mark.live
@pytest.mark.skipif(not _ollama_up(), reason="Ollama unavailable (DEGRADED)")
def test_l1_live_embedding_gates():
    r = SemanticRouter(routes={
        "conversation": ["how was your day so far?",
                         "tell me something interesting about space",
                         "say something nice about rainy days"],
        "coding": ["write a python script that renames files",
                   "fix the failing unit test in the repo",
                   "inspect the project and run tests"],
    })
    digest_before = None
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/ps")
        with urllib.request.urlopen(req, timeout=5) as resp:
            models = json.loads(resp.read().decode()).get("models", [])
        for m in models:
            if m.get("name", "").startswith("qubit-brain"):
                digest_before = m.get("digest")
    except Exception:
        pass

    r.fit(url="http://127.0.0.1:11434", model="nomic-embed-text")
    lat = []
    for i in range(8):
        t0 = __import__("time").perf_counter()
        r.embed([f"gate probe {i}"], query=True,
                url="http://127.0.0.1:11434", model="nomic-embed-text")
        lat.append((__import__("time").perf_counter() - t0) * 1000.0)
    warm_p95 = sorted(lat)[int(len(lat) * 0.95)]

    digest_after = None
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/ps")
        with urllib.request.urlopen(req, timeout=5) as resp:
            models = json.loads(resp.read().decode()).get("models", [])
        names = [m.get("name", "") for m in models]
        for m in models:
            if m.get("name", "").startswith("qubit-brain"):
                digest_after = m.get("digest")
    except Exception:
        pass

    assert warm_p95 < 75.0, f"embedding warm p95 {warm_p95:.1f}ms > 75ms gate"
    assert digest_before is None or digest_before == digest_after, \
        "embedding runner evicted/reloaded the generative runner"
