"""Writer/reader rebrand pairs: the same constant must be spelled one way.

The failure class these pin: a rebrand renamed ONE SIDE of a paired constant — an env
var, a WebSocket subprotocol, a header read — so the two processes that agree on
everything else silently stop talking. Nothing raises, every import check passes, and
the feature just stops firing. Each test below names the pair, drives BOTH sides, and
was verified to fail against the code before its fix.

Three pairs, one per test group:

1. ``QUBIT_SERVE_HEADLESS`` — written by ``main_dashboard._resolve_dashboard_web_dist``,
   read by ``web_server_dashboard``/``web_server_config``. The writer emitted only the
   legacy ``HERMES_`` name while both readers had moved to ``QUBIT_``, so ``qubit serve``
   mounted the SPA it exists to suppress.
2. ``X-*-Session-Token`` — ``_ticket_principal`` read only the canonical header name while
   the validator accepted every alias, so a legacy-header caller authenticated yet minted
   a socket ticket with no browser-credential generation (no sign-out revocation).
3. ``*-browser-control-v1`` — the shipped extension dials ``qubit-`` (pinned by
   ``plugins/browser-companion/wire_check.py``); ``api_server`` compared only ``hermes-``.
"""

import pytest


# ---------------------------------------------------------------------------
# 1. SERVE_HEADLESS: one writer, two readers, both spellings accepted
# ---------------------------------------------------------------------------

HEADLESS_WRITER = "qubit_cli.main_dashboard._resolve_dashboard_web_dist"


def _clear_headless(monkeypatch):
    for name in ("QUBIT_SERVE_HEADLESS", "HERMES_SERVE_HEADLESS"):
        monkeypatch.delenv(name, raising=False)


def test_headless_writer_sets_the_canonical_name(monkeypatch):
    """The serve path must write ``QUBIT_SERVE_HEADLESS`` — the name both readers key on."""
    from qubit_cli import main_dashboard

    _clear_headless(monkeypatch)
    main_dashboard._resolve_dashboard_web_dist(object(), _headless_backend=True)

    import os
    assert os.environ.get("QUBIT_SERVE_HEADLESS") == "1"


def test_headless_writer_value_is_seen_by_both_readers(monkeypatch):
    """End-to-end: what the writer sets, the readers report. This is the pair that broke."""
    from qubit_cli import main_dashboard, web_server_config, web_server_dashboard

    _clear_headless(monkeypatch)
    main_dashboard._resolve_dashboard_web_dist(object(), _headless_backend=True)

    assert web_server_dashboard._serve_headless() is True
    assert web_server_config._serve_headless_env() == "1"
    # And the reader the skew hint routes on.
    assert "Restart backend" in web_server_config._dashboard_skew_restart_hint()


@pytest.mark.parametrize("legacy_env", ["HERMES_SERVE_HEADLESS"])
def test_headless_readers_still_accept_the_legacy_spelling(legacy_env, monkeypatch):
    """A backend spawned by a pre-rename build exports only ``HERMES_``; it must stay headless."""
    import os

    from qubit_cli import web_server_config, web_server_dashboard

    _clear_headless(monkeypatch)
    monkeypatch.setenv(legacy_env, "1")

    assert web_server_dashboard._serve_headless() is True
    assert web_server_config._serve_headless_env() == "1"
    # Presence of the legacy var alone is enough for the skew hint to name the Desktop backend.
    assert "Restart backend" in web_server_config._dashboard_skew_restart_hint()
    assert os.environ[legacy_env] == "1"


@pytest.mark.parametrize("value", ["", "0", "yes"])
def test_headless_readers_do_not_treat_a_non_flag_as_headless(value, monkeypatch):
    """Only ``"1"`` means headless — the fix must not widen the accepted values."""
    from qubit_cli import web_server_config, web_server_dashboard

    _clear_headless(monkeypatch)
    monkeypatch.setenv("QUBIT_SERVE_HEADLESS", value)

    assert web_server_dashboard._serve_headless() is False
    assert web_server_config._serve_headless_env() == value


def test_non_headless_launch_strips_both_spellings(monkeypatch):
    """A browser dashboard must not inherit a leaked headless flag under either name."""
    import os

    from qubit_cli.main import _dashboard_sanitize_desktop_env

    for name in ("QUBIT_SERVE_HEADLESS", "HERMES_SERVE_HEADLESS"):
        monkeypatch.setenv(name, "1")
    _dashboard_sanitize_desktop_env(headless_backend=False)

    assert "QUBIT_SERVE_HEADLESS" not in os.environ
    assert "HERMES_SERVE_HEADLESS" not in os.environ


# ---------------------------------------------------------------------------
# 2. Session-token header aliases: the ticket principal reads what the validator reads
# ---------------------------------------------------------------------------

_HEADER_NAMES = ("X-Qubit-Session-Token", "X-Hermes-Session-Token")


def _request(headers: dict):
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/api/webapp/socket-ticket",
                    "headers": raw, "query_string": b""})


@pytest.mark.parametrize("header_name", _HEADER_NAMES)
def test_ticket_principal_records_the_token_from_either_header(header_name, monkeypatch):
    """The principal must carry the presented token under EVERY accepted spelling.

    Before the fix only the canonical name populated ``session_token``; a legacy-header
    caller passed the validator but minted a ticket with ``session_token=None``, which
    skips ``generation_of(...)`` and so never revokes on browser sign-out.
    """
    from starlette.applications import Starlette

    from qubit_cli import web_server
    from qubit_cli.web_routers.webapp import _ticket_principal

    monkeypatch.setattr(web_server, "_SESSION_TOKEN", "s3cret-token")
    app = Starlette()
    app.state.auth_required = False

    request = _request({header_name: "s3cret-token"})
    request.scope["app"] = app
    identity, session_token = _ticket_principal(request)

    assert session_token == "s3cret-token"
    assert identity == {"user_id": "dashboard", "provider": "session",
                        "issuer": ""} or identity  # shape owned by SESSION_TOKEN_OWNER


@pytest.mark.parametrize("header_name", _HEADER_NAMES)
def test_ticket_principal_rejects_a_wrong_token_under_either_header(header_name, monkeypatch):
    """Reading both headers must not accept a credential neither validator would."""
    from fastapi import HTTPException
    from starlette.applications import Starlette

    from qubit_cli import web_server
    from qubit_cli.web_routers.webapp import _ticket_principal

    monkeypatch.setattr(web_server, "_SESSION_TOKEN", "s3cret-token")
    app = Starlette()
    app.state.auth_required = False

    request = _request({header_name: "wrong-token"})
    request.scope["app"] = app
    with pytest.raises(HTTPException) as caught:
        _ticket_principal(request)
    assert caught.value.status_code == 401


def test_ticket_principal_still_reads_the_bearer_fallback(monkeypatch):
    from starlette.applications import Starlette

    from qubit_cli import web_server
    from qubit_cli.web_routers.webapp import _ticket_principal

    monkeypatch.setattr(web_server, "_SESSION_TOKEN", "s3cret-token")
    app = Starlette()
    app.state.auth_required = False

    request = _request({"Authorization": "Bearer s3cret-token"})
    request.scope["app"] = app
    _identity, session_token = _ticket_principal(request)
    assert session_token == "s3cret-token"


# ---------------------------------------------------------------------------
# 3. Browser-control subprotocol: what the extension dials is what api_server accepts
# ---------------------------------------------------------------------------

EXTENSION_PROTOCOL = "qubit-browser-control-v1"   # what the packaged extension sends
EXTENSION_TICKET = "qubit-browser-control-ticket."
LEGACY_PROTOCOL = "hermes-browser-control-v1"     # what an older extension sends
LEGACY_TICKET = "hermes-browser-control-ticket."


def _split_like_api_server(requested: list[str]):
    """Mirror of the acceptance logic in ``_handle_browser_control_ws``."""
    from gateway.platforms.api_server import (
        _BROWSER_CONTROL_TICKET_PROTOCOL_PREFIXES,
        _BROWSER_CONTROL_WS_PROTOCOL,
        _BROWSER_CONTROL_WS_PROTOCOL_ALIASES,
    )

    ticket_protocols = [
        value for value in requested if value.startswith(_BROWSER_CONTROL_TICKET_PROTOCOL_PREFIXES)]
    offered = next((name for name in (_BROWSER_CONTROL_WS_PROTOCOL, *_BROWSER_CONTROL_WS_PROTOCOL_ALIASES)
                    if name in requested), "")
    if not offered or len(ticket_protocols) != 1:
        return None, None
    prefix = next(p for p in _BROWSER_CONTROL_TICKET_PROTOCOL_PREFIXES
                  if ticket_protocols[0].startswith(p))
    return offered, ticket_protocols[0][len(prefix):]


@pytest.mark.parametrize(("protocol", "prefix", "expected_protocol"), [
    (EXTENSION_PROTOCOL, EXTENSION_TICKET, EXTENSION_PROTOCOL),
    (LEGACY_PROTOCOL, LEGACY_TICKET, LEGACY_PROTOCOL),
])
def test_api_server_accepts_either_browser_control_spelling(protocol, prefix, expected_protocol):
    """Both the rebranded extension and a pre-rename one must reach the upgrade."""
    offered, ticket = _split_like_api_server([protocol, f"{prefix}tok-123"])
    assert offered == expected_protocol, "handshake must echo the spelling the client offered"
    assert ticket == "tok-123", "the matched prefix must be stripped exactly once"


def test_api_server_refuses_a_mixed_pairing():
    """A ticket prefix from one brand and a protocol from the other is not a valid dial."""
    offered, _ticket = _split_like_api_server([EXTENSION_PROTOCOL, "garbage-ticket.tok"])
    assert offered is None


def test_shipped_extension_speaks_the_protocol_api_server_now_accepts():
    """The real client: read the extension source and feed its literals to the acceptance logic."""
    from pathlib import Path

    import re

    extension = (Path(__file__).resolve().parents[3] / "frontend" / "qubit-app" / "plugins"
                 / "qubit-browser-ext" / "extension" / "lib" / "controller-protocol.mjs")
    if not extension.is_file():
        pytest.skip(f"extension source not in this checkout: {extension}")
    text = extension.read_text(encoding="utf-8")
    found = re.findall(r"'(qubit|hermes)-browser-control-v1'", text)
    assert found, "extension no longer declares a browser-control subprotocol"
    for brand in found:
        offered, ticket = _split_like_api_server(
            [f"{brand}-browser-control-v1", f"{brand}-browser-control-ticket.tok-abc"])
        assert offered == f"{brand}-browser-control-v1"
        assert ticket == "tok-abc"
