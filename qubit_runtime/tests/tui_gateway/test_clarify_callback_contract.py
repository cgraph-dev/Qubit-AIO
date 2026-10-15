"""Pin the tui_gateway clarify callback contract against two shipped regressions.

Both defects reached the user as the SAME visible failure — the ``clarify`` tool
answering ``Failed to get user input: ...`` and the turn dying — from opposite ends:

1. **Arity.** ``agent_callbacks._agent_cbs`` handed ``tools.clarify_tool`` a 4-arg
   lambda (``q, c, multi_select=False, questions=None``) where the tool's contract
   is ``callback(questions)``. Calling it with the one normalized list raised
   ``_agent_cbs.<locals>.<lambda>() missing 1 required positional argument: 'c'``.

2. **Shape.** After the arity fix, ``server._clarify_block``'s batch branch returned
   the *server's raw reply* — which carries ``answers`` but no ``outcome``. The tool's
   ``_result`` reads ``reply["outcome"]``, so the SUCCESS path raised
   ``KeyError: 'outcome'`` and the tool answered ``Failed to get user input: 'outcome'``.

The callback's contract is ``callback(questions) -> {"answers": {qid: raw}, "outcome": str}``
(see ``tools/clarify_tool.py``: ``_result`` reads BOTH keys). These tests assert that
contract at the two seams — the callback object itself, and the real tool driven
through the real callback — so a revert to either defect goes RED here.

Sibling coverage that must keep passing: ``test_warning_callback_conservation.py``
reads the callback off ``_agent_cbs`` and invokes it, and ``test_tui_gateway_server.py::
test_clarify_callback_uses_configured_timeout`` drives the batch path with a stubbed
``server_requests.send``.
"""
import inspect
import json

import pytest

from tui_gateway import server
from tools.clarify_tool import clarify_tool

SID = "clarify-contract"


def _batch(qid="q0", question="Pick one", choices=("a", "b"), multi_select=False):
    """A normalized question list, as ``clarify_tool._normalize_questions`` would hand the callback."""
    return [{"qid": qid, "question": question, "choices": list(choices), "multi_select": multi_select}]


def _stub_send(monkeypatch, reply):
    """Stub the ``clarify`` server request and capture what the bridge asked for.

    ``_clarify_block`` does ``from tui_gateway import server_requests`` INSIDE the function,
    so the attribute must be patched on the MODULE, not on a name imported into server.
    """
    from tui_gateway import server_requests
    captured = {}

    def fake_send(method, sid, params, *, timeout, qids=None):
        captured.update(method=method, sid=sid, params=params, timeout=timeout, qids=qids)
        return reply

    monkeypatch.setattr(server_requests, "send", fake_send)
    return captured


def _callback(sid=SID):
    return server._agent_cbs(sid)["clarify_callback"]


# ---------------------------------------------------------------------------
# 1. ARITY — the callback takes exactly one positional argument (the questions).
# ---------------------------------------------------------------------------

def test_clarify_callback_accepts_exactly_one_positional_argument():
    """The tool calls ``callback(normalized_questions)``. A callback declaring a second
    required positional (the old ``lambda q, c, ...``) dies with ``missing 1 required
    positional argument`` before the user ever sees a card."""
    cb = _callback()
    params = list(inspect.signature(cb).parameters.values())
    positional = [p for p in params
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                  and p.default is p.empty]
    assert len(positional) == 1, (
        f"clarify_callback must take exactly one required positional arg (questions); "
        f"got signature {inspect.signature(cb)}")
    # No positional parameter may be required *beyond* the first.
    required_positional = [p for p in params
                           if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    assert len(required_positional) == 1, (
        f"clarify_callback declares {len(required_positional)} positional params; the tool only "
        f"passes one: {inspect.signature(cb)}")


def test_clarify_callback_callable_with_one_positional_argument(monkeypatch):
    """The arity defect is only real when *called* — assert the one-arg call does not raise
    TypeError (the old 4-arg lambda raised ``missing 1 required positional argument: 'c'``)."""
    _stub_send(monkeypatch, {"answers": {"q0": "a"}})
    try:
        _callback()(_batch())
    except TypeError as exc:  # pragma: no cover - only on the defect
        pytest.fail(f"clarify_callback rejected the one-arg call the tool makes: {exc!r}")


# ---------------------------------------------------------------------------
# 2. SHAPE — every branch returns {"answers", "outcome"} (both keys, always).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("reply", "expect_outcome"),
    [
        ({"answers": {"q0": "a"}}, "submitted"),          # happy batch reply
        ({"answers": {"q0": "a"}, "outcome": "submitted"}, "submitted"),  # surface already stated it
        ({"answers": {}}, "submitted"),                    # cancel-all with an explicit empty map
        ({}, "cancelled"),                                # no answers key at all -> cancel-all
        (None, "cancelled"),                              # timeout -> None
    ],
)
def test_batch_branch_always_returns_answers_and_outcome(monkeypatch, reply, expect_outcome):
    """The batch branch is the ONLY live path (the tool always passes a questions list).
    Returning the server's raw reply dropped ``outcome`` and the tool raised KeyError('outcome')
    on the SUCCESS path. Every branch must carry both keys."""
    _stub_send(monkeypatch, reply)
    result = _callback()(_batch())
    assert isinstance(result, dict), f"clarify callback must return a dict, got {type(result).__name__}"
    assert "answers" in result, f"reply {reply!r} produced {result!r} with no 'answers' key"
    assert "outcome" in result, f"reply {reply!r} produced {result!r} with no 'outcome' key"
    assert result["outcome"] == expect_outcome
    assert isinstance(result["answers"], dict)


def test_batch_branch_preserves_locked_answers(monkeypatch):
    """A submitted reply's answers reach the tool unchanged, keyed by qid."""
    _stub_send(monkeypatch, {"answers": {"q0": "yes", "q1": ["x"]}})
    result = _callback()(
        _batch() + [{"qid": "q1", "question": "More?", "choices": ["x", "y"], "multi_select": True}])
    assert result["answers"] == {"q0": "yes", "q1": ["x"]}
    assert result["outcome"] == "submitted"


def test_batch_branch_passes_canonical_clarify_timeout(monkeypatch):
    """Regression guard for the sibling timeout test, restated here so the shape and the
    timeout live together: the bridge sends a ``clarify`` request with the canonical timeout."""
    monkeypatch.setattr(server, "_clarify_timeout_seconds", lambda: 42)
    captured = _stub_send(monkeypatch, {"answers": {"q0": "a"}})
    _callback()(_batch())
    assert captured["method"] == "clarify"
    assert captured["sid"] == SID
    assert captured["timeout"] == 42
    assert captured["qids"] == ["q0"]
    assert captured["params"]["questions"] == [
        {"qid": "q0", "question": "Pick one", "choices": ["a", "b"], "multi_select": False}]


# ---------------------------------------------------------------------------
# 3. q0 MAPPING — the single-question branch keys its bare answer onto qid ``q0``.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("answer", ["Yes", "  spaced  ", "0"])
def test_single_question_branch_maps_answer_onto_q0(monkeypatch, answer):
    """The single-question (q/c) form asks exactly one question, which normalizes to qid ``q0``.
    It returned a bare string before, which ``clarify_tool._result`` cannot read — the tool keys
    answers by qid. The answer must land under ``q0`` with an ``outcome``."""
    _stub_send(monkeypatch, {"answer": answer})
    result = server._clarify_block(SID, "Pick one", ["a", "b"])
    assert isinstance(result, dict), f"single-question branch returned {type(result).__name__}, not a dict"
    assert result.get("answers") == {"q0": answer}
    assert result.get("outcome") == "submitted"


@pytest.mark.parametrize("reply", [{"answer": ""}, {}, None])
def test_single_question_branch_empty_answer_still_carries_outcome(monkeypatch, reply):
    """A skipped/cancelled single answer must still be a ``{answers, outcome}`` dict — a bare
    string (or a missing key) is unreadable by ``_result``, which reads ``reply["outcome"]``."""
    _stub_send(monkeypatch, reply)
    result = server._clarify_block(SID, "Pick one", ["a", "b"])
    assert isinstance(result, dict)
    assert "answers" in result and "outcome" in result
    assert result["answers"] == {}
    assert result["outcome"] == "cancelled"


# ---------------------------------------------------------------------------
# 4. END-TO-END — drive the REAL tool through the REAL callback.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("reply", "expect_outcome", "expect_status", "expect_response"),
    [
        ({"answers": {"q0": "Yes"}}, "submitted", "answered", "Yes"),
        ({"answers": {"q0": None}}, "submitted", "skipped", None),
        ({}, "cancelled", "unanswered", None),
        (None, "cancelled", "unanswered", None),
    ],
)
def test_real_clarify_tool_reads_the_real_callback(monkeypatch, reply, expect_outcome,
                                                    expect_status, expect_response):
    """The whole point: the real ``clarify_tool`` must return parseable JSON with an ``outcome``
    from the real ``_agent_cbs`` callback. Pre-fix this raised ``KeyError: 'outcome'`` and the
    tool returned ``Failed to get user input: 'outcome'`` — this asserts the success path."""
    _stub_send(monkeypatch, reply)
    raw = clarify_tool([{"question": "Does clarify work?", "choices": ["Yes", "No"]}],
                       callback=_callback())
    assert "Failed to get user input" not in raw, raw
    payload = json.loads(raw)
    assert payload["outcome"] == expect_outcome
    assert payload["responses"] == [
        {"question": "Does clarify work?", "choices_offered": ["Yes", "No"],
         "status": expect_status, "user_response": expect_response}]


def test_real_clarify_tool_normalizes_a_single_question_to_q0(monkeypatch):
    """``clarify_tool`` always hands the callback a normalized LIST (even for one question), so the
    batch branch runs and the answer arrives keyed by the tool's own qid ``q0``. Pins the wire
    contract the tool reads, end to end."""
    _stub_send(monkeypatch, {"answers": {"q0": "Blue"}})
    raw = clarify_tool([{"question": "Colour?", "choices": ["Red", "Blue"]}],
                       callback=_callback())
    payload = json.loads(raw)
    assert payload["outcome"] == "submitted"
    assert payload["responses"][0]["user_response"] == "Blue"
