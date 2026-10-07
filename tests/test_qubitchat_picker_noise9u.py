"""``/qubit-chat`` picker noise filter (owner request 2026-10-06).

The picker reads the canonical ``state.db`` through ``QubitSessionStore`` — the
same store ``/chat``, the Sessions page and voice read. That store holds probe
and smoke-test artifacts (``final-e2e-probe``, ``verify-readonly-1``, …) which
sorted alongside real conversations in the ``/qubit-chat`` list.

The filter is deliberately conservative and LOCAL to this route: hiding a real
conversation is far worse than showing a probe row, and the other surfaces must
keep seeing every row.
"""

from __future__ import annotations

from voicebridge.qubit_compat import _is_chat_picker_noise


def _rec(sid="x", title="", msgs=0, source=None):
    rec = {"id": sid, "title": title, "message_count": msgs}
    if source is not None:
        rec["created_source"] = source
    return rec


class TestHidesProbeArtifacts:
    def test_hides_known_probe_names(self):
        assert _is_chat_picker_noise(
            _rec("final-e2e-probe", "final-e2e-probe", 2, "dashboard"))

    def test_hides_readonly_verifier(self):
        assert _is_chat_picker_noise(
            _rec("verify-readonly-1", "verify-readonly-1", 2, "dashboard"))

    def test_hides_tiny_oneshot(self):
        assert _is_chat_picker_noise(_rec("o", "HYGIENE_OK", 2, "oneshot"))

    def test_hides_untitled_dashboard_placeholder(self):
        # The auto-title placeholder: title is the session id.
        assert _is_chat_picker_noise(
            _rec("dashboard-efaef61a656d", "dashboard-efaef61a656d", 2, "dashboard"))

    def test_hides_a_multiturn_untitled_new_chat(self):
        """A New chat that ran a turn or two must not leak its ``dashboard-<hex>``.

        New chat mints an untitled ``dashboard-<hex>`` row and pins it to the
        local brain; each turn writes user+assistant messages, so the row reaches
        4 (and more) messages. The count-gated floor let those through — the
        picker then showed raw ids. ``title == id`` is the count-independent
        signal that the row was never named.
        """
        for msgs in (3, 4, 6, 10):
            assert _is_chat_picker_noise(
                _rec("dashboard-efaef61a656d", "dashboard-efaef61a656d", msgs,
                     "dashboard"))

    def test_hides_zero_message_dashboard_row(self):
        assert _is_chat_picker_noise(_rec("dashboard-abc", "", 0, "dashboard"))


class TestKeepsRealConversations:
    def test_keeps_a_long_tui_conversation(self):
        assert not _is_chat_picker_noise(
            _rec("20261006_100531_b6d69f", "Fix qubit-chat import", 785, "tui"))

    def test_keeps_a_substantial_dashboard_conversation(self):
        """A human who actually typed in the dashboard must not lose their chat.

        Message count, not source alone, is what protects them: a real
        conversation is never <= 2 messages.
        """
        assert not _is_chat_picker_noise(_rec("x", "my real chat", 30, "dashboard"))

    def test_keeps_a_one_message_tui_row(self):
        """Short but human-originated: source tui, no probe name => keep."""
        assert not _is_chat_picker_noise(_rec("y", "", 1, "tui"))

    def test_keeps_a_long_untitled_session(self):
        assert not _is_chat_picker_noise(_rec("z", "", 400, None))

    def test_keeps_a_two_message_named_conversation(self):
        """Two messages but a human-chosen title and no probe provenance."""
        assert not _is_chat_picker_noise(_rec("w", "quick question", 2, "tui"))

    def test_keeps_a_long_conversation_mentioning_a_probe_word(self):
        """The probe vocabulary must be delimited; prose must not trigger it."""
        assert not _is_chat_picker_noise(
            _rec("v", "my e2e deployment notes", 40, "tui"))

    def test_keeps_dashboard_chats_that_are_real(self):
        assert not _is_chat_picker_noise(_rec("u", "voice-chat", 592, "dashboard"))
        assert not _is_chat_picker_noise(
            _rec("t", "Continue previous conversation", 213, "dashboard"))


class TestProbeNamesAreCountIndependent:
    """A name only tooling mints is hidden regardless of message count.

    The first cut gated the name rule behind the <=2-message floor, so
    `e2e-final-probe` (4 messages) slipped through — the floor returned early
    and the name check never ran.
    """

    def test_hides_a_four_message_probe_name(self):
        assert _is_chat_picker_noise(
            _rec("e2e-final-probe", "e2e-final-probe", 4, "dashboard"))

    def test_hides_a_probe_name_with_many_messages(self):
        assert _is_chat_picker_noise(
            _rec("p", "smoke-suite-run", 40, "dashboard"))

    def test_readonly_is_recognised_delimited(self):
        assert _is_chat_picker_noise(_rec("q", "verify-readonly-1", 3, "dashboard"))

    def test_undelimited_probe_word_is_kept(self):
        assert _is_chat_picker_noise(_rec("f", "probe the market", 2, "tui")) is False


class TestDefensive:
    def test_non_dict_is_not_noise(self):
        assert not _is_chat_picker_noise(None)
        assert not _is_chat_picker_noise("nope")

    def test_missing_message_count_does_not_crash(self):
        # No count + no source defaults to "not noise" (fail toward showing).
        assert not _is_chat_picker_noise({"id": "a", "title": ""})

    def test_garbage_message_count_is_not_noise(self):
        assert not _is_chat_picker_noise(_rec("a", "", "not-a-number", "tui"))
