"""Regression: the no-auth-provider message must read the bundled provider's reason.

``qubit_cli.web_server._no_auth_provider_message`` imports the bundled dashboard
auth provider to surface ``LAST_SKIP_REASON``. A rebrand over-eagerly renamed
the imported submodule (``plugins.dashboard_auth.nous`` -> ``...qubit``), which
does not exist, so the import raised, the ``except`` swallowed it, and the
installed-but-unconfigured provider was reported as merely "no providers".

Behavioral: set the provider's reason and assert the operator message carries it.
"""
from __future__ import annotations


def test_message_surfaces_the_bundled_provider_skip_reason(monkeypatch):
    from plugins.dashboard_auth import nous

    monkeypatch.setattr(nous, "LAST_SKIP_REASON", "PROBE_SKIP_REASON_XYZ", raising=False)
    from qubit_cli import web_server

    msg = web_server._no_auth_provider_message("203.0.113.9")
    assert "PROBE_SKIP_REASON_XYZ" in msg
    assert "Bundled providers reported" in msg


def test_message_stays_clean_when_the_provider_has_no_reason(monkeypatch):
    from plugins.dashboard_auth import nous

    monkeypatch.setattr(nous, "LAST_SKIP_REASON", "", raising=False)
    from qubit_cli import web_server

    msg = web_server._no_auth_provider_message("203.0.113.9")
    assert "Bundled providers reported" not in msg
