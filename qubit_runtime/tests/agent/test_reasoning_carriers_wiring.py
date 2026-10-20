"""Provider-native reasoning carriers are captured on both response paths."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from openai.types.chat import ChatCompletion

from agent.chat_completion_helpers import build_assistant_message, interruptible_streaming_api_call
from agent.transports.chat_completions import ChatCompletionsTransport
from run_agent import AIAgent


class _Agent:
    stream_delta_callback = _stream_callback = reasoning_callback = None
    verbose_logging = False

    def _extract_reasoning(self, msg):
        from agent.agent_runtime_helpers import extract_reasoning

        return extract_reasoning(self, msg)

    def _strip_think_blocks(self, text):
        return text

    def _needs_thinking_reasoning_pad(self):
        return False

    def _split_responses_tool_id(self, raw):
        return (None, None)

    def _derive_responses_function_call_id(self, call_id, item_id):
        return item_id

    def _deterministic_call_id(self, *a):
        return "call_x"


def test_build_assistant_message_captures_native_carriers():
    response = ChatCompletion.model_validate({
        "id": "carrier-proof", "object": "chat.completion", "created": 0, "model": "gemini-3.6-pro",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant", "content": "answer", "reasoning_text": "plan it",
            "reasoning_opaque": "OPAQUE", "extra_content": {"google": {"thought_signature": "SIG"}}}}]})
    stored = build_assistant_message(
        _Agent(), ChatCompletionsTransport().normalize_response(response), "stop")

    assert stored["extra_content"] == {"google": {"thought_signature": "SIG"}}
    assert stored["reasoning_opaque"] == "OPAQUE"
    assert stored["reasoning_text"] == "plan it"
    assert stored["reasoning"] == "plan it"
    assert stored["reasoning_details"] == [{
        "type": "hermes.native_assistant",
        "extra_content": {"google": {"thought_signature": "SIG"}},
        "reasoning_opaque": "OPAQUE",
        "reasoning_text": "plan it",
    }]


def test_streamed_carriers_land_on_the_final_message():
    deltas = [
        {"role": "assistant", "content": "hello", "reasoning_text": "plan "},
        {"content": " world", "reasoning_text": "it", "reasoning_opaque": "OPAQUE",
         "extra_content": {"google": {"thought_signature": "SIG"}}},
    ]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for index, delta in enumerate(deltas):
                chunk = {"id": "carrier-proof", "object": "chat.completion.chunk", "created": 1,
                         "model": "test-model", "choices": [{"index": 0, "delta": delta,
                         "finish_reason": "stop" if index == len(deltas) - 1 else None}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    agent = AIAgent(api_key="local-test", base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    provider="custom", model="test-model", platform="subagent", quiet_mode=True,
                    skip_memory=True, skip_context_files=True, enabled_toolsets=[],
                    save_trajectories=False)
    try:
        response = interruptible_streaming_api_call(
            agent, {"model": "test-model", "messages": [{"role": "user", "content": "hi"}]})
        message = response.choices[0].message
        assert message.content == "hello world"
        assert message.reasoning_text == "plan it"
        assert message.reasoning_opaque == "OPAQUE"
        assert message.extra_content == {"google": {"thought_signature": "SIG"}}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
