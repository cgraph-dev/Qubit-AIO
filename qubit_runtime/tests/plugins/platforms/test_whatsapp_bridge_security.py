import stat
from pathlib import Path
from unittest.mock import MagicMock

from plugins.platforms.whatsapp.bridge_security import (
    BRIDGE_TOKEN_FILE,
    BRIDGE_TOKEN_HEADER,
    bridge_media_roots,
    bridge_request_headers,
    ensure_bridge_token,
    is_media_path_allowed,
    load_bridge_token,
)


def test_bridge_token_is_private_and_stable(tmp_path):
    token = ensure_bridge_token(tmp_path)
    assert len(token) == 64
    path = tmp_path / BRIDGE_TOKEN_FILE
    assert path.read_text(encoding="utf-8").strip() == token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert ensure_bridge_token(tmp_path) == token
    assert load_bridge_token(tmp_path) == token


def test_bridge_request_headers_carry_the_token(tmp_path):
    token = ensure_bridge_token(tmp_path)
    assert bridge_request_headers(tmp_path) == {BRIDGE_TOKEN_HEADER: token}


def test_bridge_request_headers_are_empty_without_a_token(tmp_path):
    assert bridge_request_headers(tmp_path) == {}


def test_media_path_is_confined_to_declared_roots(tmp_path):
    root = tmp_path / "media"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    allowed = root / "photo.png"
    secret = outside / "secret.txt"
    allowed.write_bytes(b"x")
    secret.write_bytes(b"x")

    ok, _ = is_media_path_allowed(str(allowed), [root])
    assert ok is True
    refused, reason = is_media_path_allowed(str(secret), [root])
    assert refused is False
    assert "outside" in reason


def test_media_path_rejects_a_symlink_escape(tmp_path):
    root = tmp_path / "media"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_bytes(b"x")
    (root / "escape.txt").symlink_to(secret)

    ok, _ = is_media_path_allowed(str(root / "escape.txt"), [root])
    assert ok is False


def test_media_roots_include_gateway_caches_and_tempdir():
    roots = bridge_media_roots()
    assert any(str(path).endswith("/cache/images") for path in roots)
    assert Path("/tmp").resolve() in roots


def test_adapter_env_carries_the_token_and_media_roots(tmp_path):
    from tests.gateway.test_whatsapp_formatting import _make_adapter

    adapter = _make_adapter()
    adapter._session_path = tmp_path
    adapter._send_read_receipts = False
    adapter._reply_prefix = None
    token = ensure_bridge_token(tmp_path)

    env = adapter._bridge_env()

    assert env["WHATSAPP_BRIDGE_TOKEN"] == token
    assert env["WHATSAPP_MEDIA_ROOTS"]


def test_adapter_requests_attach_the_bridge_token(tmp_path):
    from tests.gateway.test_whatsapp_formatting import _make_adapter

    adapter = _make_adapter()
    adapter._session_path = tmp_path
    token = ensure_bridge_token(tmp_path)
    adapter._http_session = MagicMock()
    adapter._http_session.post = MagicMock(return_value="response")

    result = adapter._bridge_req("post", "send", 5, json={"x": 1})

    assert result == "response"
    kwargs = adapter._http_session.post.call_args.kwargs
    assert kwargs["headers"][BRIDGE_TOKEN_HEADER] == token


import os
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_adapter_refuses_media_outside_the_roots(tmp_path):
    from tests.gateway.test_whatsapp_formatting import _make_adapter

    candidate = "/etc/hostname"
    if not os.path.isfile(candidate):
        pytest.skip("no /etc/hostname on this host")

    adapter = _make_adapter()
    adapter._session_path = tmp_path
    adapter._check_managed_bridge_exit = AsyncMock(return_value=False)
    adapter._http_session.post = MagicMock()

    result = await adapter._send_media_to_bridge("15551234567", candidate, "document")

    assert result.success is False
    assert "refused" in (result.error or "")
    adapter._http_session.post.assert_not_called()
