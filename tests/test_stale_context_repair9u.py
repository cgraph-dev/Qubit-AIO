"""9U — stale declared context windows get corrected to the probed value.

Live defect (2026-10-03, owner-caught): ``resolve_real_context_tokens``
probes **1000000** for BOTH ``deepseek-v4-flash`` and
``deepseek-v4.1-flash``, but the ``qubit-go`` profile declares 200000 and
the N29 rule enforces configured-over-advertised — so the whole dashboard
reports 200k while the runtime really runs 1M. ``backfill_profile_context``
only ever replaced the 4096 creation default, never a stale explicit value.

Contract pinned here (``providers/endpoints.py`` stale-correction path):

* stale declared < fresh probe -> corrected UPWARD to the probed value,
  stamped (probe time/value/source), audited, persisted through the
  sanctioned writer (``upsert_model_profile(confirm=True)``) — never a raw
  registry edit;
* NEVER downgrades (declared >= probe is untouched; equal is fresh);
* probe failure/None is a no-op (today's behavior);
* an explicit user-set window wins when it was set AFTER the probe
  (``explicit:`` stamp, or the window changed since the probe stamp) —
  unless ``force=True``;
* the 4096 creation-default fill keeps working AND now stamps the probe.

Hermetic: the probe is stubbed (no network); tmp state dirs only.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import config as C  # noqa: E402
from voicebridge.providers import endpoints as EP  # noqa: E402
from voicebridge.providers import resolver as R  # noqa: E402

_PROBED = 1_000_000


def _cfg(tmp_path, monkeypatch):
    from voicebridge import qubit_runtime

    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    data = {
        "logging": {"jsonl": True, "jsonl_path": str(state)},
        "intent": {"frontier": {"profile_overrides": {}}},
    }
    cfg = C.Config(data=data, path=tmp_path / "voice.yaml")
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qh"))
    (tmp_path / "qh").mkdir(parents=True, exist_ok=True)
    try:
        qubit_runtime.use_qubit_home(cfg)
    except Exception:  # noqa: BLE001 - hermetic home binding only
        pass
    return cfg


def _api_profile(pid="qubit-go", model="deepseek-v4-flash",
                 provider="opencode-go", ctx=200_000, **kw):
    args = dict(id=pid, kind=R.API, provider=provider, model=model,
                base_url="https://opencode.ai/zen/go/v1",
                key_ref="env:OPENCODE_GO_API_KEY",
                locality="external", egress_consent="allow",
                context_tokens=ctx, roles=["chat"], enabled=True)
    args.update(kw)
    return R.ModelProfile(**args)


# ------------------------------------------------------- in-memory correction
def test_correct_stale_upward_qubit_go_pin(monkeypatch):
    """qubit-go-shaped 200k profile + 1M probe -> corrected to 1M, stamped."""
    monkeypatch.setattr(EP, "resolve_real_context_tokens",
                        lambda *a: _PROBED)
    profile = _api_profile()
    assert EP.correct_stale_profile_context(profile) is True
    assert profile.context_tokens == _PROBED
    assert profile.context_probed_value == _PROBED
    assert profile.context_probed_at > 0
    assert str(profile.context_probe_source or "").startswith("probe:")


def test_correct_stale_never_downgrades(monkeypatch):
    """Declared 1M + probe 200k -> untouched (never a downgrade)."""
    monkeypatch.setattr(EP, "resolve_real_context_tokens",
                        lambda *a: 200_000)
    profile = _api_profile(ctx=_PROBED)
    assert EP.correct_stale_profile_context(profile) is False
    assert profile.context_tokens == _PROBED


def test_correct_stale_equal_is_fresh(monkeypatch):
    """Declared == probe -> no-op (nothing stale)."""
    monkeypatch.setattr(EP, "resolve_real_context_tokens",
                        lambda *a: _PROBED)
    profile = _api_profile(ctx=_PROBED)
    assert EP.correct_stale_profile_context(profile) is False
    assert profile.context_tokens == _PROBED


def test_correct_stale_probe_none_noop(monkeypatch):
    """Unresolvable probe -> no-op (today's fail-open behavior)."""
    monkeypatch.setattr(EP, "resolve_real_context_tokens",
                        lambda *a: None)
    profile = _api_profile()
    assert EP.correct_stale_profile_context(profile) is False
    assert profile.context_tokens == 200_000


def test_explicit_activation_stamp_wins(monkeypatch):
    """An explicit user-set window (explicit: stamp) is never auto-moved."""
    monkeypatch.setattr(EP, "resolve_real_context_tokens",
                        lambda *a: _PROBED)
    profile = _api_profile(ctx=200_000)
    EP.mark_profile_context_explicit(profile, actor="test")
    assert str(profile.context_probe_source or "").startswith("explicit:")
    assert EP.correct_stale_profile_context(profile) is False
    assert profile.context_tokens == 200_000


def test_window_changed_after_probe_wins(monkeypatch):
    """Stamped probe 1M, operator later set 500k -> the explicit edit wins."""
    monkeypatch.setattr(EP, "resolve_real_context_tokens",
                        lambda *a: _PROBED)
    profile = _api_profile(ctx=200_000)
    assert EP.correct_stale_profile_context(profile) is True
    assert profile.context_tokens == _PROBED
    # Operator narrows the window after the probe (their call, their data).
    profile.context_tokens = 500_000
    assert EP.correct_stale_profile_context(profile) is False
    assert profile.context_tokens == 500_000


def test_unchanged_since_probe_recorrects_on_new_probe(monkeypatch):
    """Stamped 800k untouched + fresh 1M probe -> corrected upward again."""
    monkeypatch.setattr(EP, "resolve_real_context_tokens",
                        lambda *a: 800_000)
    profile = _api_profile(ctx=200_000)
    assert EP.correct_stale_profile_context(profile) is True
    assert profile.context_tokens == 800_000
    monkeypatch.setattr(EP, "resolve_real_context_tokens",
                        lambda *a: _PROBED)
    assert EP.correct_stale_profile_context(profile) is True
    assert profile.context_tokens == _PROBED


def test_backfill_stamps_probe_on_default_fill(monkeypatch):
    """The 4096 creation-default fill keeps working AND stamps the probe."""
    monkeypatch.setattr(EP, "resolve_real_context_tokens",
                        lambda *a: _PROBED)
    profile = R.ModelProfile(
        id="n", kind=R.API, provider="p", model="m",
        base_url="https://x/v1", key_ref="env:K",
        locality="external", egress_consent="allow")
    assert EP.backfill_profile_context(profile) is True
    assert profile.context_tokens == _PROBED
    assert profile.context_probed_value == _PROBED
    assert profile.context_probed_at > 0


# ------------------------------------------------------- persisted repair
def test_repair_persists_through_sanctioned_writer(tmp_path, monkeypatch):
    """repair_stale_profile_context persists 200k -> 1M via upsert+confirm."""
    monkeypatch.setattr(EP, "resolve_real_context_tokens",
                        lambda *a: _PROBED)
    cfg = _cfg(tmp_path, monkeypatch)
    out = R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    assert out["state"] == "OK", out

    events = []
    monkeypatch.setattr("voicebridge.logging_setup.log_event",
                        lambda *a, **k: events.append((a, k)))

    repaired = EP.repair_stale_profile_context(
        cfg, "qubit-go", actor="test-9u")
    assert repaired["ok"] is True, repaired
    assert repaired["repaired"] is True, repaired
    assert repaired["before"] == 200_000, repaired
    assert repaired["after"] == _PROBED, repaired
    assert R.get_profile(cfg, "qubit-go").context_tokens == _PROBED
    assert any("context" in str(a[0]) for a in events), events


def test_repair_unknown_profile_is_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = EP.repair_stale_profile_context(cfg, "no-such", actor="test-9u")
    assert out["ok"] is False, out
    assert out["code"] == "UNKNOWN_PROFILE", out


def test_repair_explicit_wins_without_force(tmp_path, monkeypatch):
    """Explicit-stamped profile: repair refuses; force=True overrides."""
    monkeypatch.setattr(EP, "resolve_real_context_tokens",
                        lambda *a: _PROBED)
    cfg = _cfg(tmp_path, monkeypatch)
    profile = _api_profile()
    EP.mark_profile_context_explicit(profile, actor="test-9u")
    out = R.upsert_model_profile(cfg, profile, confirm=True)
    assert out["state"] == "OK", out

    refused = EP.repair_stale_profile_context(cfg, "qubit-go",
                                              actor="test-9u")
    assert refused["ok"] is True and refused["repaired"] is False, refused
    assert R.get_profile(cfg, "qubit-go").context_tokens == 200_000

    forced = EP.repair_stale_profile_context(cfg, "qubit-go",
                                             actor="test-9u", force=True)
    assert forced["ok"] is True and forced["repaired"] is True, forced
    assert R.get_profile(cfg, "qubit-go").context_tokens == _PROBED
