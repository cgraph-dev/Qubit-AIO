"""Deterministic remote streaming contract tests (no network or credentials)."""
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from voicebridge import chat_transport as chat


class RemoteStreamContractTests(unittest.TestCase):
    def _stream(self, sid, *, timeout_s=2.0):
        return chat._stream_remote_turn(
            {}, sid, "hello", "",
            {"provider": "test", "model": "fake", "profile_id": "fake"},
            timeout_s=timeout_s, cancel=None)

    def test_real_incremental_deltas_and_final_outcome(self):
        sid = "remote-stream-success-contract"
        first_sent = threading.Event()
        continue_stream = threading.Event()

        def provider(req, cfg, on_delta, **kwargs):
            on_delta("hello ")
            first_sent.set()
            self.assertTrue(continue_stream.wait(2))
            on_delta("world")
            return SimpleNamespace(ok=True, details={"streaming": True})

        with patch.object(chat, "store", return_value=object()), \
             patch.object(chat, "_estop", return_value=False), \
             patch("voicebridge.providers.gateway.run_stream", side_effect=provider), \
             patch.object(chat, "_remote_outcome", return_value={
                 "ok": True, "reply": "hello world", "receipt": {}}):
            stream = self._stream(sid)
            try:
                self.assertEqual(next(stream), ("delta", "hello "))
                self.assertTrue(first_sent.is_set())
                continue_stream.set()
                self.assertEqual(next(stream), ("delta", "world"))
                kind, outcome = next(stream)
                self.assertEqual(kind, "outcome")
                self.assertEqual(outcome["reply"], "hello world")
                self.assertTrue(outcome["receipt"]["streaming"])
                with self.assertRaises(StopIteration):
                    next(stream)
            finally:
                continue_stream.set()
                stream.close()
        self.assertEqual(chat.cancel(sid)["code"], "NO_ACTIVE_TURN")

    def test_timeout_signals_provider_during_generation(self):
        sid = "remote-stream-timeout-contract"
        stopped = threading.Event()

        def provider(req, cfg, on_delta, **kwargs):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if kwargs["cancel"]():
                    stopped.set()
                    break
                time.sleep(0.005)
            return SimpleNamespace(ok=False, details={})

        with patch.object(chat, "store", return_value=object()), \
             patch.object(chat, "_estop", return_value=False), \
             patch("voicebridge.providers.gateway.run_stream", side_effect=provider):
            events = list(self._stream(sid, timeout_s=0.02))
            self.assertEqual(events[0][0], "outcome")
            self.assertEqual(events[0][1]["code"], "TURN_TIMEOUT")
            self.assertTrue(stopped.wait(2), "provider never saw cancellation")
        # Worker cleanup may race the final assertion; bounded wait only.
        deadline = time.monotonic() + 2
        while chat.cancel(sid).get("ok") and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(chat.cancel(sid)["code"], "NO_ACTIVE_TURN")


if __name__ == "__main__":
    unittest.main()
