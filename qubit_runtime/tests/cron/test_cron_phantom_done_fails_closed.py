"""Acted-work acceptance for a completed cron turn fails CLOSED (phantom-done guard).

The cron lane previously marked a run ``ok`` whenever ``run_conversation`` returned
without raising, so a promise-only turn ("I will build the weekly report now…",
zero tool calls, zero files) was recorded as a SUCCESS and delivered as the result
— the "worker promised a build but built nothing" symptom. ``_cron_phantom_done``
is the cron-side port of the chat lane's ``TASK_NO_BUILD`` guard: a completed turn
whose only evidence is a future-tense promise or progress chatter, with no tool
activity in the run history, is failure evidence; acted work and ordinary reports
are untouched.
"""

from cron.scheduler import _cron_phantom_done, _cron_turn_had_tool_activity

_USER = {"role": "user", "content": "Produce a weekly review."}
_TOOL_CALL = {"role": "assistant", "content": "", "tool_calls": [
    {"id": "1", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}]}
_TOOL_RESULT = {"role": "tool", "content": "ok", "tool_call_id": "1"}


def test_promise_only_with_no_tools_is_a_failure():
    result = {"messages": [_USER, {"role": "assistant",
              "content": "I will build the weekly report now and save it for you."}]}
    err = _cron_phantom_done(
        "I will build the weekly report now and save it for you.", result)
    assert err is not None
    assert "no tool activity" in err


def test_progress_chatter_with_no_tools_is_a_failure():
    result = {"messages": [_USER, {"role": "assistant", "content": "Working on hello.html inside the project folder."}]}
    assert _cron_phantom_done("Working on hello.html inside the project folder.", result) is not None


def test_tool_receipts_excuse_even_a_promise_phrased_reply():
    # Past-tense report + a real tool row: acted work, never a phantom.
    result = {"messages": [_USER, _TOOL_CALL, _TOOL_RESULT,
              {"role": "assistant", "content": "I will keep it tidy — created weekly_review.md."}]}
    assert _cron_phantom_done("I will keep it tidy — created weekly_review.md.", result) is None


def test_past_tense_acted_verb_in_text_is_a_report_not_a_phantom():
    result = {"messages": [_USER, {"role": "assistant", "content": "I will not pad it; the sync was created."}]}
    assert _cron_phantom_done("I will not pad it; the sync was created.", result) is None


def test_ordinary_answer_with_no_push_phrasing_is_untouched():
    # A briefing/summary that never promises anything must NOT be flagged.
    result = {"messages": [_USER, {"role": "assistant", "content": "Upstream has not moved since the last check."}]}
    assert _cron_phantom_done("Upstream has not moved since the last check.", result) is None


def test_empty_response_is_not_the_phantom_shape():
    assert _cron_phantom_done("", {"messages": []}) is None


def test_tool_activity_detection():
    assert _cron_turn_had_tool_activity([_USER, _TOOL_CALL]) is True
    assert _cron_turn_had_tool_activity([_USER, _TOOL_RESULT]) is True
    assert _cron_turn_had_tool_activity([_USER, {"role": "assistant", "content": "hi"}]) is False
    assert _cron_turn_had_tool_activity(None) is False
    assert _cron_turn_had_tool_activity("not-a-list") is False
