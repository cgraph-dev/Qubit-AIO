"""``run_bootstrap`` must check the welcome-trial leftovers before it records inference.

Upstream calls ``settle_interrupted_claim`` + ``grandfather_existing_guest`` in ``run_bootstrap``;
this fork dropped the try block, leaving ``welcome_trial.grandfather_existing_guest`` with zero
callers. These pin the call (and that a failure there cannot block boot).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fresh_bootstrap():
    from qubit_cli import free_tier_bootstrap as fb

    fb.reset_for_tests()
    yield
    fb.reset_for_tests()


def _record():
    from qubit_cli.free_tier_bootstrap import SetupRecord

    return SetupRecord(
        provider_configured=False,
        inference_provider="",
        free_tier_account=False,
        has_identity=False,
        other_providers=False,
    )


def _isolate(monkeypatch, events):
    from qubit_cli import anon_auth, free_tier_bootstrap as fb, welcome_trial

    monkeypatch.setattr(fb, "_inventory_other_providers", lambda: False)
    monkeypatch.setattr(fb, "_build_record", lambda **_kw: events.append("build") or _record())
    monkeypatch.setattr(fb, "_broadcast", lambda _record: None)
    monkeypatch.setattr(anon_auth, "on_local_desktop_backend", lambda: False)
    return fb, welcome_trial


def test_bootstrap_checks_existing_guests_before_building_the_record(monkeypatch):
    events = []
    fb, welcome_trial = _isolate(monkeypatch, events)
    monkeypatch.setattr(
        welcome_trial, "settle_interrupted_claim", lambda: events.append("settle") or False
    )
    monkeypatch.setattr(
        welcome_trial, "grandfather_existing_guest", lambda: events.append("grandfather") or False
    )

    fb.run_bootstrap(announce=False)

    assert events == ["settle", "grandfather", "build"]


def test_a_welcome_trial_failure_cannot_block_boot(monkeypatch, caplog):
    events = []
    fb, welcome_trial = _isolate(monkeypatch, events)

    def _boom():
        raise RuntimeError("state.db is locked")

    monkeypatch.setattr(welcome_trial, "settle_interrupted_claim", lambda: False)
    monkeypatch.setattr(welcome_trial, "grandfather_existing_guest", _boom)

    record = fb.run_bootstrap(announce=False)

    assert record.has_identity is False and record.inference_provider == ""
    assert events == ["build"], "boot still records inference"
    assert "could not check for an existing free-model guest" in caplog.text
