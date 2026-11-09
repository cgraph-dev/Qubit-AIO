"""Restore the process-global gateway runner reference between relay tests.

`GatewayRunner.__init__` assigns `gateway.run._gateway_runner_ref = weakref.ref(self)`.
`gateway.relay.egress._live_relay_fronted()` prefers that live runner's relay adapter
over the `GATEWAY_RELAY_PLATFORMS` snapshot, by design: the connected adapter is the
authority on what it fronts, and the two disagree when the env changes after connect.

In a test process the design turns into cross-test state. Several relay tests build a
real `GatewayRunner`; while any of them is still alive, every later
`authorize_relay_target()` asks THAT runner's adapter instead of the per-test monkeypatch.
Measured: `test_relay_egress_declines.py` passes 34/34 alone and fails 2 when run after
`test_relay_native_refusal_ack.py`, with `relay_fronted_platforms()` returning set() —
the victim's own `GATEWAY_RELAY_PLATFORMS` never reached the guard.

This is a test-isolation fix, not a guard change: the production rule (live adapter wins)
is untouched. Each test starts from "no runner in this process", which is what a CLI or
cron process sees anyway.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_gateway_runner_ref():
    import gateway.run as gr

    saved = gr._gateway_runner_ref
    gr._gateway_runner_ref = lambda: None  # the "no runner" sentinel
    try:
        yield
    finally:
        gr._gateway_runner_ref = saved
