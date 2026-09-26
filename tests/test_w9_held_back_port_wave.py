"""Behavioral coverage for the W9 held-back port (primitives + three files).

The primitives were ported from pinned Hermes v0.21.4
(``d337b736aa1e8ebecfab043842d13e4a2d2f48a3``) so the three held-back modules
could be ported with the ``hermes_* -> qubit_*`` rename map. This exercises the
real behavior the port exists for, not just importability.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = REPO_ROOT / "qubit_runtime"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))


def _owner(home: Path) -> dict:
    return {
        "profile_home": str(home.resolve()),
        "session_id": "sess-1",
        "lease_id": "lease-1",
        "live_session_id": "live-1",
    }


def test_bot_live_delivery_admit_claim_complete_round_trip(tmp_path):
    """A single private record advances queued -> claimed -> terminal, durably."""
    delivery = __import__("tools.bot_live_delivery", fromlist=["x"])
    home = tmp_path / "home"
    home.mkdir()
    owner = _owner(home)

    record = delivery.deliver_to_live_owner(home, owner, "hello", delivery_id="a" * 32)
    assert record["status"] == "queued"
    assert delivery.has_mailbox(home)
    # Retrying the same id + payload inspects the existing admission, never overwrites.
    assert delivery.deliver_to_live_owner(home, owner, "hello", delivery_id="a" * 32)["status"] == "queued"

    claimed = delivery.claim_pending_delivery(home, owner)
    assert claimed is not None and claimed["status"] == "claimed"

    receipt = delivery.complete_delivery(home, "a" * 32, status="settled", reply="done")
    assert receipt["status"] == "settled" and receipt["reply"] == "done"
    assert delivery.read_delivery_result(home, "a" * 32)["status"] == "settled"
    # A duplicate identical completion is safe; a different one is refused.
    assert delivery.complete_delivery(home, "a" * 32, status="settled", reply="done")["reply"] == "done"
    with pytest.raises(ValueError):
        delivery.complete_delivery(home, "a" * 32, status="failed")


def test_bot_live_delivery_rejects_different_payload_for_same_id(tmp_path):
    delivery = __import__("tools.bot_live_delivery", fromlist=["x"])
    home = tmp_path / "home"
    home.mkdir()
    owner = _owner(home)
    delivery.deliver_to_live_owner(home, owner, "one", delivery_id="b" * 32)
    with pytest.raises(ValueError):
        delivery.deliver_to_live_owner(home, owner, "two", delivery_id="b" * 32)


def test_cron_bot_chat_delivery_defer_write_path_no_longer_raises():
    """The already-ported cron consumer calls atomic_json_write(fsync_dir=, mode=)."""
    qubit_constants = __import__("qubit_constants")
    bcd = __import__("cron.bot_chat_delivery", fromlist=["x"])
    home = qubit_constants.get_qubit_home()
    record = bcd.defer("d" * 32, {"id": "job-1"}, "payload", "default", home)
    assert record["status"] == "queued"
    assert bcd.read_pending("d" * 32)["content"] == "payload"
    target = home / "cron" / "bot_chat_pending" / ("d" * 32 + ".json")
    assert target.is_file()
    assert (target.stat().st_mode & 0o777) == 0o600


def test_anon_auth_guest_gate_and_route_predicates(monkeypatch):
    anon = __import__("qubit_cli.anon_auth", fromlist=["x"])
    # The guest model id follows the rebrand; the wire header/env identifiers
    # (and the upstream welcome hostname) are preserved verbatim.
    assert anon.GUEST_MODEL == "qubit/welcome"
    assert anon.MODEL_SWITCH_HEADER == "x-nous-model-switch"
    assert anon.GUEST_ONBOARDING_ENV == "HERMES_GUEST_ONBOARDING"
    # The launch gate is exact "1"; anything else keeps the free tier dark.
    monkeypatch.setenv(anon.GUEST_ONBOARDING_ENV, "0")
    assert anon.guest_enabled() is False
    # Guest state detection is purely the auth_method marker.
    assert anon.is_guest_state({"auth_method": anon.ANON_AUTH_METHOD}) is True
    assert anon.is_guest_state({"auth_method": "oauth_device_code"}) is False
    # The welcome host predicate is keyed on the resolved endpoint.
    assert anon.route_is_welcome_host("https://welcome-api.nousresearch.com/v1") is True
    assert anon.route_is_welcome_host("https://portal.nousresearch.com/v1") is False
    # Failure copy is surface-agnostic and never says guest/anonymous/credential.
    copy = anon.anon_failure_copy(anon.ANON_RATE_LIMITED, retry_after=120)
    assert "guest" not in copy.lower() and "anonymous" not in copy.lower()
    # Structured welcome refusals parse only the gateway's known shape.
    parsed = anon.parse_welcome_refusal({"reason": "model_not_free", "retry_after": "3", "alternates": ["qubit/welcome"]})
    assert parsed == {"reason": "model_not_free", "retry_after": 3, "alternates": ["qubit/welcome"], "upgrade_url": ""}
    assert anon.parse_welcome_refusal({"reason": "unknown"}) is None


def test_browser_vault_tools_are_registered_in_browser_toolset():
    importlib = __import__("importlib")
    importlib.import_module("tools.browser_vault_tool")
    registry = __import__("tools.registry", fromlist=["registry"]).registry
    names = set(registry.get_tool_names_for_toolset("browser"))
    assert {"browser_vault_list", "browser_vault_unlock", "browser_vault_save_login",
            "browser_vault_enter_code", "browser_vault_fill"} <= names


def test_slash_commands_login_mixin_is_a_real_class():
    importlib = __import__("importlib")
    module = importlib.import_module("gateway.slash_commands_login")
    assert isinstance(module.GatewayLoginCommandsMixin, type)
    assert hasattr(module.GatewayLoginCommandsMixin, "_handle_login_command")
