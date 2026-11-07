"""Focused regressions for the C7 tenancy harness itself.

The two-tenant gateways bind a port picked by ``free_port()``. Under the parallel E2E runner
another process can claim it between the probe and the bind; the api_server then PARKS the
conflict and the gateway stays alive but can never answer that port. ``MultiplexGateway.start``
must detect that from the log and rebind instead of waiting out the health budget.

Second, ``_helpers.await_cron`` must keep failing when a profile's cron never fires, and report
the store/ticker state so a starved ticker is distinguishable from a job that was never due.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import qubit_yaml as yaml

from . import _helpers as H
from . import test_two_tenant_gateway as G


class _AliveProc:
    """A process that has not exited (the parked-api_server shape)."""

    returncode = 0

    def __init__(self, pid: int) -> None:
        self.pid = pid

    def poll(self) -> None:
        return None


def test_start_rebinds_when_the_api_server_port_was_taken(tmp_path, monkeypatch) -> None:
    gw = G.MultiplexGateway(tmp_path, 11111, tmp_path / "config.yaml")
    gw.log_path.write_text("", encoding="utf-8")

    spawned: list[int] = []
    events: list[Any] = []

    def fake_spawn() -> None:
        pid = 4242 + len(spawned)
        spawned.append(pid)
        gw.proc = _AliveProc(pid)
        gw.pids.append(pid)
        if len(spawned) == 1:
            # Exactly what the gateway logs when another process holds our port: it parks the
            # api_server and keeps running, so it is neither dead nor healthy.
            gw.log_path.write_text(
                "ERROR gateway.platforms.api_server: [Api_Server] Could not bind "
                "127.0.0.1:11111: [Errno 98] address already in use\n",
                encoding="utf-8",
            )

    def fake_rebind(port: int) -> None:
        events.append(("rebind", port))
        gw.port = port

    monkeypatch.setattr(gw, "_spawn", fake_spawn)
    monkeypatch.setattr(gw, "_rebind", fake_rebind)
    monkeypatch.setattr(gw, "healthy", lambda: len(spawned) >= 2)
    monkeypatch.setattr(gw, "stop", lambda: events.append("stop"))
    monkeypatch.setattr(G.H, "free_port", lambda: 22222)

    gw.start()  # must not raise or wait: the first attempt is reaped and rebound

    assert spawned == [4242, 4243], spawned
    assert events == ["stop", ("rebind", 22222)], events
    assert gw.port == 22222


def test_await_cron_still_fails_and_reports_store_state(tmp_path, monkeypatch) -> None:
    tenant = SimpleNamespace(
        name="alpha",
        home=tmp_path,
        cron_prompt="cron-canary-alpha: run the probe",
        srv=SimpleNamespace(requests=[]),
        canaries=lambda: {},
        secrets=lambda: {},
    )
    monkeypatch.setattr(H, "CRON_WAIT_S", 0.05)

    with pytest.raises(AssertionError) as excinfo:
        H.await_cron({"alpha": tenant}, 1)

    message = str(excinfo.value)
    assert "timed out" in message
    assert "cron fire #1 in every profile" in message
    assert "alpha: provider cron requests=0" in message


def test_tenant_config_keeps_a_load_tolerant_provider_timeout(tmp_path) -> None:
    """The loopback fake answers instantly; the OpenAI client's 5s DEFAULT connect timeout is what
    aborted cron turns on an oversubscribed host, so the tenant config must set its own."""
    t = H.Tenant(name="alpha", home=tmp_path / "alpha", workdir=tmp_path / "work")
    t.srv = SimpleNamespace(base_url="http://127.0.0.1:1/v1", requests=[])

    H.write_tenant_home(t)

    cfg = yaml.safe_load((t.home / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["providers"]["custom"]["request_timeout_seconds"] >= 60
    assert cfg["providers"]["custom"]["stale_timeout_seconds"] >= 60

