"""Agent AA defects — reasoning-budget truncation repair + one model authority.

Two independently-verified defects:

* **Long-session empty response.** A reasoning API model (``deepseek-v4-flash``)
  spends the entire ``max_tokens`` budget on ``reasoning_content`` and stops at
  ``finish_reason="length"`` with empty ``content``; ``frontier.py`` reported
  the misleading ``MALFORMED_RESPONSE``/"empty content" and the turn was lost.
  The vendored agent retries a length stop with a larger output budget, so the
  frontier client now does the same (bounded) and surfaces a truthful
  ``OUTPUT_TRUNCATED`` only when the repair is exhausted.
* **Model-alias inconsistency.** ``/api/model/options`` top-level and the
  ``/qubit-chat`` stream reported the configured ``deepseek-v4-flash`` while
  ``/api/model/info``, ``/chat`` and the ``/qubit-chat`` header reported the
  session's ``deepseek-v4.1-flash``. The session's recorded model is now pinned
  onto the resolved profile so all surfaces report the one effective model.

Hermetic: injected transports and temp homes; no network.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _body(content="", *, finish="length", reasoning="", pt=10, ct=5):
    msg = {"role": "assistant", "content": content}
    if reasoning:
        msg["reasoning_content"] = reasoning
    return {"choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct}}


def _req(max_tokens=512):
    from voicebridge.providers.contracts import ProviderRequest

    return ProviderRequest(prompt="hi", max_tokens=max_tokens, timeout_s=5,
                           route="frontier", provider="opencode-go",
                           model="deepseek-v4-flash")


def test_length_truncated_empty_is_repaired_with_a_larger_budget():
    from voicebridge.providers import frontier as F

    budgets = []

    def transport(url, headers, payload, timeout):
        budgets.append(payload.get("max_tokens"))
        if len(budgets) == 1:
            return 200, _body("", finish="length", reasoning="thinking ...")
        return 200, _body("REAL_REPLY", finish="stop")

    client = F.FrontierClient("http://x", "k", "opencode-go",
                              "deepseek-v4-flash", max_retries=0,
                              transport=transport)
    res = client.complete(_req(512), "hi")
    assert res.ok, res
    assert res.text == "REAL_REPLY"
    # One repair: the second call carried a doubled output budget.
    assert budgets == [512, 1024], budgets


def test_length_truncated_after_repairs_is_a_truthful_typed_error():
    from voicebridge.providers import frontier as F

    budgets = []

    def transport(url, headers, payload, timeout):
        budgets.append(payload.get("max_tokens"))
        return 200, _body("", finish="length", reasoning="still thinking")

    client = F.FrontierClient("http://x", "k", "opencode-go",
                              "deepseek-v4-flash", max_retries=0,
                              max_length_retries=1, transport=transport)
    res = client.complete(_req(512), "hi")
    assert res.ok is False
    assert res.code == "OUTPUT_TRUNCATED", res
    assert "reasoning consumed the output budget" in res.message
    # Never converted into a fabricated success; no text.
    assert not res.text
    assert budgets == [512, 1024], budgets


def test_genuine_empty_completion_stays_malformed():
    from voicebridge.providers import frontier as F

    def transport(url, headers, payload, timeout):
        return 200, _body("", finish="stop")

    client = F.FrontierClient("http://x", "k", "opencode-go",
                              "deepseek-v4-flash", max_retries=0,
                              transport=transport)
    res = client.complete(_req(512), "hi")
    assert res.ok is False
    assert res.code == "MALFORMED_RESPONSE", res
    assert "empty" in res.message


def test_non_empty_length_stop_is_returned_not_repaired():
    from voicebridge.providers import frontier as F

    budgets = []

    def transport(url, headers, payload, timeout):
        budgets.append(payload.get("max_tokens"))
        return 200, _body("partial answer", finish="length")

    client = F.FrontierClient("http://x", "k", "opencode-go",
                              "deepseek-v4-flash", max_retries=0,
                              transport=transport)
    res = client.complete(_req(256), "hi")
    # A length stop WITH visible content is a real (partial) answer, not the
    # empty reasoning-truncation case; it is returned without a repair.
    assert res.ok and res.text == "partial answer"
    assert budgets == [256], budgets


# ---------------------------------------------------------------------------
# One model authority across model/options, model/info and the stream route
# ---------------------------------------------------------------------------
def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    voice = tmp_path / "voice.yaml"
    voice.write_text("logging:\n  jsonl: false\n"
                     "intent:\n  frontier:\n    enabled: true\n"
                     "    allowlist: ['opencode-go/deepseek-v4-flash']\n"
                     "terminal:\n  profile: qubit-go\n")
    cfg = C.Config.load(voice)
    home = cfg.state_dir() / "runtime"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(textwrap.dedent(
        "model:\n"
        "  provider: opencode-go\n"
        "  default: deepseek-v4-flash\n"
        "  base_url: https://opencode.ai/zen/go/v1\n"))
    monkeypatch.setenv("QUBIT_HOME", str(home))
    return cfg


def _go_profile(cfg):
    from voicebridge.providers import resolver as R

    R.upsert_model_profile(cfg, R.ModelProfile(
        id="qubit-go", kind=R.API, provider="opencode-go",
        model="deepseek-v4-flash", base_url="https://opencode.ai/zen/go/v1",
        key_ref="env:OPENCODE_GO_API_KEY", locality="external",
        egress_consent="allow", roles=["chat"], context_tokens=200000),
        confirm=True)


def test_session_model_is_pinned_across_options_info_and_route(tmp_path,
                                                               monkeypatch):
    from voicebridge import agent as AG
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as HC
    from voicebridge.providers import resolver as R

    R._RUNTIME_MODEL_CACHE.clear()
    cfg = _cfg(tmp_path, monkeypatch)
    _go_profile(cfg)
    R.set_active_profile(cfg, "qubit-go", confirm=True)

    sid = AG.canonical_session_id(cfg)
    assert CT.store(cfg).ensure(sid, model="deepseek-v4.1-flash")["state"] == "OK"

    routed = CT.resolve_route(cfg, None, session=sid)
    assert routed["ok"] is True, routed
    assert routed["model"] == "deepseek-v4.1-flash", routed
    assert routed["provider"] == "opencode-go"

    code, info = HC.h_model_info(cfg, {}, {}, {})
    assert code == 200 and info["model"] == "deepseek-v4.1-flash", info
    assert info["provider"] == "opencode-go"

    code, options = HC.h_model_options(cfg, {"include_unconfigured": "true"},
                                       {}, {})
    assert code == 200, options
    assert options["model"] == "deepseek-v4.1-flash", options.get("model")
    assert options["provider"] == "opencode-go"


def test_without_a_session_model_the_resolved_default_is_used(tmp_path,
                                                              monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as HC
    from voicebridge.providers import resolver as R

    R._RUNTIME_MODEL_CACHE.clear()
    cfg = _cfg(tmp_path, monkeypatch)
    _go_profile(cfg)
    R.set_active_profile(cfg, "qubit-go", confirm=True)

    routed = CT.resolve_route(cfg, None, session="no-such-session")
    assert routed["model"] == "deepseek-v4-flash", routed

    code, info = HC.h_model_info(cfg, {}, {}, {})
    assert code == 200 and info["model"] == "deepseek-v4-flash"


def test_explicit_model_request_wins_over_the_session_pin(tmp_path,
                                                          monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge.providers import resolver as R

    R._RUNTIME_MODEL_CACHE.clear()
    cfg = _cfg(tmp_path, monkeypatch)
    _go_profile(cfg)
    R.set_active_profile(cfg, "qubit-go", confirm=True)
    CT.store(cfg).ensure("pin-explicit", model="deepseek-v4.1-flash")

    routed = CT.resolve_route(cfg, "qubit-go", session="pin-explicit")
    assert routed["model"] == "deepseek-v4-flash", routed
