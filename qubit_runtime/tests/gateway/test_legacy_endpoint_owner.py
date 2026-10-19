"""Regression for the launch-route endpoint-ownership check on legacy adoption.

Ported from upstream `gateway/session_local_migration.py` (pin 43faacfb8de7b29548547a7fbdedad0cc6e7391c),
whose `_endpoint_owner` is called from `adopt_legacy_session`. Without the call a saved session whose
named provider entry moved to another host resumes on the OLD host with the MOVED entry's credential:
the profile's key is dropped for that route and the adopted params carry the entry's provider name
next to the saved URL. The contract: rebind to whatever entry still serves the saved URL, or refuse
by name — never send the moved entry's key to the old host.
"""
import pytest

from gateway.session_local_migration import _endpoint_owner
from qubit_state_runtime import RuntimeStoreError

SAVED_URL = "https://api.old-host.example/v1"


def _config(**providers):
    return {
        "model": {"provider": "custom:work", "base_url": SAVED_URL, "api_key": "sk-profile"},
        "providers": providers,
    }


def test_rebinds_to_the_entry_still_serving_the_saved_url():
    config = _config(
        work={"name": "Work", "base_url": "https://api.new-host.example/v1", "api_key": "sk-work"},
        other={"name": "Other", "base_url": SAVED_URL, "api_key": "sk-other"},
    )
    assert _endpoint_owner({"provider": "custom:work", "base_url": SAVED_URL}, config) == "custom:other"


def test_refuses_by_name_when_nobody_serves_the_saved_url():
    config = _config(work={"name": "Work", "base_url": "https://api.new-host.example/v1", "api_key": "sk-work"})
    with pytest.raises(RuntimeStoreError) as err:
        _endpoint_owner({"provider": "custom:work", "base_url": SAVED_URL}, config)
    assert err.value.reason == "launch_credentials_unavailable"


def test_keeps_the_provider_when_the_entry_still_serves_the_saved_url():
    config = _config(work={"name": "Work", "base_url": SAVED_URL, "api_key": "sk-work"})
    assert _endpoint_owner({"provider": "custom:work", "base_url": SAVED_URL}, config) == "custom:work"


def test_provider_without_a_configured_entry_is_unchanged():
    assert _endpoint_owner({"provider": "openai", "base_url": ""}, {"model": {}}) == "openai"
