"""Regression coverage for cancellation of overlapping chat turns.

These tests exercise the real chat transport cancellation registry without
requiring provider credentials or a running dashboard.
"""
import unittest

from voicebridge import chat_transport as chat


class CancelRegistryTests(unittest.TestCase):
    def test_overlapping_turns_get_distinct_tokens(self):
        sid = "cancel-registry-overlap-test"
        first = chat._cancel_event(sid)
        second = chat._cancel_event(sid)
        try:
            self.assertIsNot(first, second)
            first.set()
            self.assertFalse(second.is_set())
            self.assertTrue(chat.cancel(sid)["ok"])
            self.assertTrue(first.is_set())
            self.assertTrue(second.is_set())
        finally:
            chat._release_cancel(sid, first)
            chat._release_cancel(sid, second)
        self.assertEqual(chat.cancel(sid)["code"], "NO_ACTIVE_TURN")

    def test_finishing_one_turn_keeps_other_cancellable(self):
        sid = "cancel-registry-release-test"
        first = chat._cancel_event(sid)
        second = chat._cancel_event(sid)
        try:
            chat._release_cancel(sid, first)
            self.assertTrue(chat.cancel(sid)["ok"])
            self.assertTrue(second.is_set())
        finally:
            chat._release_cancel(sid, first)
            chat._release_cancel(sid, second)
        self.assertEqual(chat.cancel(sid)["code"], "NO_ACTIVE_TURN")


    def test_cancellation_does_not_cross_session_boundary(self):
        first_id = "cancel-registry-session-a"
        second_id = "cancel-registry-session-b"
        first = chat._cancel_event(first_id)
        second = chat._cancel_event(second_id)
        try:
            self.assertTrue(chat.cancel(first_id)["ok"])
            self.assertTrue(first.is_set())
            self.assertFalse(second.is_set())
        finally:
            chat._release_cancel(first_id, first)
            chat._release_cancel(second_id, second)


if __name__ == "__main__":
    unittest.main()
