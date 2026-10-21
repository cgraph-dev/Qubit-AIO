"""The ACP submit identity is the pin's staged-content fingerprint.

``_prepare_submit`` computes it through ``_submit_fingerprint`` after staging, and an
un-acked retry of the same prompt keeps the first submit's input id.
"""
import asyncio

import acp
import pytest

from acp_adapter import gateway_server


def test_submit_fingerprint_covers_text_and_staged_bytes(tmp_path):
    staged = tmp_path / "image.png"
    staged.write_bytes(b"first bytes")
    first = gateway_server._submit_fingerprint(
        "s1", "hello", [{"path": str(staged), "mime": "image/png"}])
    assert first[0] == "s1"
    assert first[1] == gateway_server._submit_fingerprint(
        "s1", "hello", [{"path": str(staged), "mime": "image/png"}])[1]
    staged.write_bytes(b"other bytes")
    assert gateway_server._submit_fingerprint(
        "s1", "hello", [{"path": str(staged), "mime": "image/png"}])[1] != first[1]
    assert gateway_server._submit_fingerprint("s1", "goodbye", [])[1] != first[1]


@pytest.mark.asyncio
async def test_prepare_submit_uses_the_pin_fingerprint_and_reuses_a_retry(tmp_path, monkeypatch):
    agent = gateway_server.GatewayACPAgent()
    agent._home = tmp_path
    seen = []

    def spy(session_id, text, attachments):
        seen.append((session_id, text, list(attachments)))
        return session_id, "retry-key"

    monkeypatch.setattr(gateway_server, "_submit_fingerprint", spy)
    retry_key, input_id, staged = await agent._prepare_submit("s1", [acp.text_block("hello")])
    assert seen == [("s1", "hello", [])]
    assert retry_key == ("s1", "retry-key")
    assert staged == ("hello", [])

    agent._unacked["s1"] = (retry_key, input_id)
    retry_key2, input_id2, _ = await agent._prepare_submit("s1", [acp.text_block("hello")])
    assert retry_key2 == ("s1", "retry-key")
    assert input_id2 == input_id
