"""The bootstrap control listener is fatal (owner ruling 2026-10-21, F4).

The frozen pin's ``gateway/run_bootstrap.py`` raises when the control socket cannot bind.
The post-freeze non-fatal body at ``69948c0057`` is out of scope, and ``session-ticket``
attach has no scan fallback: a gateway without this listener serves no local session.
"""

import pytest

from gateway.run_bootstrap import _require_gateway_control_listener


def test_started_listener_is_returned_unchanged():
    sentinel = object()
    assert _require_gateway_control_listener(sentinel) is sentinel


def test_missing_listener_aborts_bootstrap():
    with pytest.raises(RuntimeError, match="control listener unavailable"):
        _require_gateway_control_listener(None)
