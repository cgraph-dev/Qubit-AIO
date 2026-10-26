import threading

from agent.runtime_native import NativeRuntime
from agent.runtime_port import TurnRequest


class _FakeAgent:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def run_conversation(self, message, **kwargs):
        self.calls.append((message, kwargs))
        callback = kwargs.get("stream_callback")
        if callback:
            callback("Hel")
            callback("lo")
        return self.result


def _collect(backend, request, cancel=None):
    return list(backend.run_turn(request, cancel))


def test_completed_turn_emits_deltas_then_one_done():
    agent = _FakeAgent({"completed": True, "final_response": "Hello", "turn_id": "t1"})
    backend = NativeRuntime(lambda request: agent)

    events = _collect(backend, TurnRequest(prompt="hi", session_id="s1"))

    assert [e.kind for e in events] == ["delta", "delta", "done"]
    assert "".join(e.text for e in events if e.kind == "delta") == "Hello"
    assert events[-1].text == "Hello"
    assert events[-1].data["turn_id"] == "t1"
    assert agent.calls[0][0] == "hi"


def test_failed_turn_emits_one_error():
    agent = _FakeAgent({"completed": False, "error": "provider rejected the request"})
    events = _collect(NativeRuntime(lambda request: agent), TurnRequest(prompt="hi", session_id="s1"))

    assert [e.kind for e in events] == ["delta", "delta", "error"]
    assert "provider rejected" in events[-1].text


def test_exception_emits_one_error():
    class _Boom:
        def run_conversation(self, message, **kwargs):
            raise RuntimeError("boom")

    events = _collect(NativeRuntime(lambda request: _Boom()), TurnRequest(prompt="hi", session_id="s1"))
    assert [e.kind for e in events] == ["error"]
    assert "boom" in events[-1].text


def test_result_payload_is_not_copied_wholesale():
    agent = _FakeAgent({"completed": True, "final_response": "ok", "api_key": "sk-secret"})
    events = _collect(NativeRuntime(lambda request: agent), TurnRequest(prompt="hi", session_id="s1"))
    assert "sk-secret" not in repr(events[-1].data)


def test_cancel_interrupts_the_agent_and_emits_one_error():
    started = threading.Event()
    release = threading.Event()

    class _BlockingAgent:
        def __init__(self):
            self.interrupted = False

        def interrupt(self, message=None, **kwargs):
            self.interrupted = True
            release.set()
            return True

        def run_conversation(self, message, **kwargs):
            started.set()
            release.wait(5)
            return {"completed": False, "interrupted": self.interrupted, "error": "cancelled"}

    agent = _BlockingAgent()
    cancel = threading.Event()
    events = []

    backend = NativeRuntime(lambda request: agent)
    consumer = threading.Thread(
        target=lambda: events.extend(backend.run_turn(
            TurnRequest(prompt="hi", session_id="s1"), cancel=cancel)),
        daemon=True,
    )
    consumer.start()
    assert started.wait(2)
    cancel.set()
    consumer.join(5)

    assert agent.interrupted is True
    terminals = [e for e in events if e.kind in ("done", "error")]
    assert len(terminals) == 1 and terminals[0].kind == "error"
    assert terminals[0].data["interrupted"] is True
