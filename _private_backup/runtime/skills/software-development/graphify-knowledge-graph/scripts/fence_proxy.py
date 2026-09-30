#!/usr/bin/env python3
"""OpenAI-Responses-API <-> chat/completions translation proxy (fence stripper).

WHY: graphify v1 (and any tool built on AI SDK v5) uses the OpenAI
Responses API shape by default -- request {input:[...]}, response
{output:[...]} -- but DeepSeek (and many local endpoints) only speak
/chat/completions ({messages:[...]} / {choices:[...]}). Without this
proxy every semantic extraction chunk fails with "Invalid JSON response"
and NO useful graphify output.

WHAT IT DOES (all three fixes were required, in order of discovery):
1. Request:  input -> messages; flatten content-part arrays to strings
   (DeepSeek 400s on array content: "MSG_SHAPE={'user': 'list'}").
2. Response: choices[].message.content -> Responses output[] shape with
   the Zod-REQUIRED fields: output[0].id, output[0].status,
   output[0].content[0].annotations: [], content[0].logprobs: [],
   top-level created_at, status, object:"response", and
   usage.input_tokens / output_tokens (numbers). Missing ANY of these
   fails schema validation ("expected array, received undefined").
3. Strip ```json fences from content (models wrap JSON; graphify's own
   parse handles fences but the SDK response schema does not).

CRITICAL subtlety: only translate the RESPONSE back to Responses shape
when the REQUEST arrived in Responses shape (responses_mode flag). If the
SDK used the chat path (graphify's openai() provider often does), adding
output/rewriting object:"response" CORRUPTS an otherwise-valid chat
response and causes the exact failure you're trying to fix.

USAGE:
    FENCE_PROXY_UPSTREAM=https://api.deepseek.com/v1 python3 fence_proxy.py
    # proxy reads DEEPSEEK_API_KEY from ~/.hermes/.env automatically when
    # upstream contains "deepseek"; ollama ignores auth.
    export OPENAI_API_KEY=x OPENAI_BASE_URL=http://127.0.0.1:18099/v1
    graphify extract "<repo>" --scope all --backend openai \
        --model deepseek-chat --out /tmp/out

Local-ollama alternative: FENCE_PROXY_UPSTREAM=http://127.0.0.1:11434/v1
(local 3B/phi-4-mini are too weak for the strict extraction schema —
they return prose or off-schema JSON; DeepSeek-chat is the reliable path).
"""
import json
import os
import re
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("FENCE_PROXY_UPSTREAM", "http://127.0.0.1:11434/v1")
API_KEY = os.environ.get("FENCE_PROXY_API_KEY", "")
if not API_KEY and "deepseek" in UPSTREAM:
    try:
        env_path = os.path.expanduser("~/.hermes/.env")
        for line in open(env_path, errors="replace"):
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY="):
                API_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    except Exception:
        pass
if not API_KEY:
    API_KEY = "local"
FENCE = re.compile(r'^\s*```(?:json)?\s*|\s*```\s*$', re.M)


class Handler(BaseHTTPRequestHandler):
    def _fwd(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        payload = json.loads(body)
        # AI SDK v5 'openai' provider uses the Responses API format
        # ({input:[...]}) by default. DeepSeek only speaks /chat/completions
        # ({messages:[...]}) — translate when needed. Remember which mode the
        # request used so the response is translated back only for Responses.
        responses_mode = False
        if "input" in payload and "messages" not in payload:
            payload["messages"] = payload["input"]
            del payload["input"]
            responses_mode = True
        # Responses API content is a list of {type, text} parts; DeepSeek
        # chat wants a plain string.
        for m in payload.get("messages", []):
            c = m.get("content")
            if isinstance(c, list):
                parts = []
                for p in c:
                    if isinstance(p, dict):
                        if p.get("type") == "text":
                            parts.append(p.get("text", ""))
                        elif p.get("type") in ("input_text", "output_text"):
                            parts.append(p.get("text", ""))
                    elif isinstance(p, str):
                        parts.append(p)
                m["content"] = "\n".join(parts)
        req = urllib.request.Request(
            UPSTREAM + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + API_KEY})
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read().decode())
        except Exception as exc:
            data = {"error": str(exc)}
        # strip fences from all assistant message contents
        if "choices" in data:
            for ch in data["choices"]:
                msg = ch.get("message", {})
                if "content" in msg and isinstance(msg["content"], str):
                    msg["content"] = FENCE.sub("", msg["content"]).strip()
        # If the request was Responses-API, translate the chat-completions
        # response back to the Responses shape the SDK expects. If it was
        # already chat-completions (SDK chat path), pass through untouched.
        if responses_mode and "choices" in data and "output" not in data:
            content = ""
            if data["choices"] and "message" in data["choices"][0]:
                content = data["choices"][0]["message"].get("content", "")
            data["output"] = [{
                "type": "message",
                "role": "assistant",
                "id": "msg_proxy_" + (data.get("id") or "x")[-16:],
                "status": "completed",
                "content": [{
                    "type": "output_text",
                    "text": content,
                    "annotations": [],
                    "logprobs": [],
                }],
            }]
            data["id"] = data.get("id", "chatcmpl-proxy")
            data["object"] = "response"
            data["created_at"] = data.get("created", 0)
            data["status"] = "completed"
            usage = data.get("usage") or {}
            data["usage"] = {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
        out = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_POST = _fwd
    do_GET = _fwd

    def log_message(self, *a):  # silence
        pass


if __name__ == "__main__":
    print(f"fence-proxy on 127.0.0.1:18099 -> {UPSTREAM}")
    ThreadingHTTPServer(("127.0.0.1", 18099), Handler).serve_forever()
