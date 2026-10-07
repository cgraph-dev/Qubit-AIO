"""Typed vision errors: an invalid image is a named code, not a raw 502.

Regression: a 1x1 PNG with a broken IDAT stream passed the magic-byte sniff,
reached the provider, and the provider's HTTP 400 ("Failed to load image or
audio file") surfaced as a raw ``502 VISION_UPSTREAM``. The vision tool now
validates decodability before the provider call and classifies provider image
errors, so the caller gets ``VISION_IMAGE_INVALID`` (a caller error) while a
genuine provider outage keeps ``VISION_UPSTREAM``.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tools.vision_tools import (
    VISION_IMAGE_INVALID,
    VISION_IMAGE_UNSUPPORTED,
    VISION_UPSTREAM,
    vision_analyze_tool,
)

# A structurally VALID 1x1 RGB PNG (Pillow decodes it).
VALID_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00"
    b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)

# The image from the live probe: correct PNG magic bytes, but a broken IDAT
# checksum that Pillow (and Ollama's loader) reject. Magic-byte sniffing alone
# passes it.
BROKEN_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ"
    "/pLvAAAAAElFTkSuQmCC")


def _data_url(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _mock_response(text: str = "A description"):
    from unittest.mock import MagicMock

    response = MagicMock()
    choice = MagicMock()
    choice.message.content = text
    response.choices = [choice]
    return response


@pytest.mark.asyncio
async def test_broken_png_is_a_typed_invalid_image():
    with patch("tools.vision_tools.async_call_llm",
               new_callable=AsyncMock) as mock_llm:
        result = json.loads(
            await vision_analyze_tool(_data_url(BROKEN_PNG), "describe"))
    assert result["success"] is False
    assert result["code"] == VISION_IMAGE_INVALID
    assert result["code"] != VISION_UPSTREAM
    assert "502" not in json.dumps(result)
    mock_llm.assert_not_awaited()  # rejected before any provider call


@pytest.mark.asyncio
async def test_non_image_is_a_typed_error(tmp_path: Path):
    not_an_image = tmp_path / "notes.txt"
    not_an_image.write_text("this is not an image")
    with patch("tools.vision_tools.async_call_llm",
               new_callable=AsyncMock) as mock_llm:
        result = json.loads(
            await vision_analyze_tool(str(not_an_image), "describe"))
    assert result["success"] is False
    assert result["code"] == VISION_IMAGE_INVALID
    mock_llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_unsupported_format_is_typed():
    # A recognized image whose format cannot be normalized to a supported one.
    with (
        patch("tools.vision_tools._normalize_to_supported_image",
              return_value=(None, None, "unsupported format x")),
        patch("tools.vision_tools.async_call_llm",
              new_callable=AsyncMock) as mock_llm,
    ):
        result = json.loads(
            await vision_analyze_tool(_data_url(VALID_PNG), "describe"))
    assert result["success"] is False
    assert result["code"] == VISION_IMAGE_UNSUPPORTED
    mock_llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_image_error_is_typed_not_upstream():
    # A valid image that the provider rejects with its image-load 400.
    provider_error = Exception(
        'Error code: 400 - {"error":{"code":400,"message":"Failed to load '
        'image or audio file","type":"invalid_request_error"}}')
    with patch("tools.vision_tools.async_call_llm",
               new_callable=AsyncMock, side_effect=provider_error):
        result = json.loads(
            await vision_analyze_tool(_data_url(VALID_PNG), "describe",
                                      "test/model"))
    assert result["success"] is False
    assert result["code"] == VISION_IMAGE_INVALID


@pytest.mark.asyncio
async def test_genuine_provider_failure_stays_upstream():
    provider_error = Exception("Error code: 500 - upstream exploded")
    with patch("tools.vision_tools.async_call_llm",
               new_callable=AsyncMock, side_effect=provider_error):
        result = json.loads(
            await vision_analyze_tool(_data_url(VALID_PNG), "describe",
                                      "test/model"))
    assert result["success"] is False
    assert result["code"] == VISION_UPSTREAM


@pytest.mark.asyncio
async def test_valid_image_still_succeeds():
    with patch("tools.vision_tools.async_call_llm",
               new_callable=AsyncMock,
               return_value=_mock_response("A red pixel")):
        result = json.loads(
            await vision_analyze_tool(_data_url(VALID_PNG), "describe",
                                      "test/model"))
    assert result["success"] is True
    assert result["analysis"] == "A red pixel"


# --- shared classifier (agent.auxiliary_client) ----------------------------
def test_classify_vision_exception_mapping():
    from agent.auxiliary_client import (
        VISION_AUTH, VISION_IMAGE_INVALID, VISION_IMAGE_TOO_LARGE,
        VISION_TIMEOUT, VISION_UNSUPPORTED_MODEL, VISION_UPSTREAM,
        classify_vision_exception)

    assert classify_vision_exception(
        Exception("Failed to load image or audio file")) == VISION_IMAGE_INVALID
    assert classify_vision_exception(
        Exception("image is too large for the API")) == VISION_IMAGE_TOO_LARGE
    assert classify_vision_exception(
        Exception("model does not support image input")) == \
        VISION_UNSUPPORTED_MODEL
    assert classify_vision_exception(
        Exception("request timed out")) == VISION_TIMEOUT
    assert classify_vision_exception(Exception("401 unauthorized")) == \
        VISION_AUTH
    assert classify_vision_exception(
        Exception("kaboom")) == VISION_UPSTREAM


def test_classification_never_raises():
    from agent.auxiliary_client import classify_vision_exception

    class _Hostile(Exception):
        def __str__(self):
            raise RuntimeError("no string for you")

    # A classifier that raised would turn a typed refusal into a traceback.
    assert classify_vision_exception(_Hostile()) == "VISION_UPSTREAM"
    assert classify_vision_exception(None) == "VISION_UPSTREAM"
