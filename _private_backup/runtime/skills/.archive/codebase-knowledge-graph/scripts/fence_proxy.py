#!/usr/bin/env python3
"""OpenAI-compatible proxy for graphify semantic extraction.

Two jobs:
1. Strip ```json ... ``` fences from LLM responses (graphify's own parser
   handles fences fine, but the AI SDK response handler can choke on them).
2. Translate the AI SDK v5 'openai' provider's **Responses API** wire format
   ({input:[...]}) to the **chat completions** format ({messages:[...]})
   that backends like DeepSeek actually speak, and translate the response
   back. WITHOUT this, semantic extraction fails with "Invalid JSON response"
   whose hidden cause is AI_TypeValidationError on the response shape.

CRITICAL: the response translation MUST be conditional on the request having
been Responses-API (responses_mode flag). Unconditionally rewriting
`object: "chat.completion"` -> `"response"` corrupts valid chat responses and
fails openaiChatResponseSchema validation.

Usage (cloud DeepSeek, reads DEEPSEEK_API_KEY from ~/.hermes/.env):
    FENCE_PROXY_UPSTREAM=https://api.deepseek.com/v1 python3 fence_proxy.py
    OPENAI_API_KEY=local OPENAI_BASE_URL=http://127.0.0.1:18099/v1 \
        graphify extract <repo> --scope all --backend openai \
        --model deepseek-chat --out <out>

Usage (local ollama OpenAI-compat endpoint, no key):
    python3 fence_proxy.py        # default upstream 127.0.0.1:11434/v1
    OPENAI_API_KEY=local OPENAI_BASE_URL=http://127.0.0.1:18099/v1 \
        graphify extract <repo> --backend openai --model <local-model>

Debug: FENCE_PROXY_DEBUG=1 logs request/response shapes to
/tmp/fence_proxy_requests.log and /tmp/fence_proxy_responses.log.
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

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.M)


class Handler(BaseHTTPRequestHandler):
    def _fwd(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        payload = json.loads(body)

        # --- request: Responses API -> chat completions -------------------
        # AI SDK v5 openai() sends {model, input:[...], temperature} by
        # default (Responses API). DeepSeek/ollama only accept
        # {model, messages:[...]}. Remember which mode so the response
        # translation only fires for Responses requests.
        responses_mode = False
        if "input" in payload and "messages" not in payload:
            payload["messages"] = payload["input"]
            del payload["input"]
            responses_mode = True
        # Responses content is a list of {type, text} parts; chat wants a
        # plain string.
        for m in payload.get("messages", []):
            c = m.get("content")
            if isinstance(c, list):
                parts = []
                for p in c:
                    if isinstance(p, dict):
                        if p.get("type") in ("text", "input_text", "output_text"):
                            parts.append(p.get("text", ""))
                    elif isinstance(p, str):
                        parts.append(p)
                m["content"] = "\n".join(parts)

        if os.environ.get("FENCE_PROXY_DEBUG"):
            with open("/tmp/fence_proxy_requests.log", "a") as fh:
                fh.write(json.dumps({
                    "model": payload.get("model"),
                    "body_keys": list(payload.keys()),
                    "msgs": [{"role": m.get("role"),
                              "content_type": type(m.get("content")).__name__,
                              "content_head": str(m.get("content"))[:120]}
                             for m in payload.get("messages", [])],
                    "len": len(body),
                }) + "\n")

        req = urllib.request.Request(
            UPSTREAM + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + API_KEY})
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                raw = resp.read().decode()
                if os.environ.get("FENCE_PROXY_DEBUG"):
                    with open("/tmp/fence_proxy_responses.log", "a") as fh:
                        fh.write(f"LEN={len(raw)} FIRST={raw[:200]!r}\n")
                data = json.loads(raw)
        except Exception as exc:
            data = {"error": str(exc)}
            if os.environ.get("FENCE_PROXY_DEBUG"):
                with open("/tmp/fence_proxy_responses.log", "a") as fh:
                    fh.write(f"UPSTREAM_ERR={exc!r} MSG_SHAPE="
                             + json.dumps({m.get("role"): type(m.get("content")).__name__
                                           for m in payload.get("messages", [])})
                             + "\n")

        # --- response: strip fences --------------------------------------
        if "choices" in data:
            for ch in data["choices"]:
                msg = ch.get("message", {})
                if "content" in msg and isinstance(msg["content"], str):
                    msg["content"] = FENCE.sub("", msg["content"]).strip()

        # --- response: chat completions -> Responses API -------------------
        # Only when the request was Responses-API. The SDK's
        # openaiResponsesResponseSchema requires, per output message:
        #   id (string, REQUIRED), status "completed",
        #   content [{type:"output_text", text}]
        # and top-level created_at: number, status "completed".
        if responses_mode and "choices" in data and "output" not in data:
            content = ""
            if data["choices"] and "message" in data["choices"][0]:
                content = data["choices"][0]["message"].get("content", "")
            data["output"] = [{
                "type": "message",
                "role": "assistant",
                "id": "msg_proxy_" + (data.get("id") or "x")[-16:],
                "status": "completed",
                "content": [{"type": "output_text", "text": content}],
            }]
            data["id"] = data.get("id", "chatcmpl-proxy")
            data["object"] = "response"
            data["created_at"] = data.get("created", 0)
            data["status"] = "completed"

        out = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_POST = _fwd
    do_GET = _fwd

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"fence-proxy on 127.0.0.1:18099 -> {UPSTREAM}")
    ThreadingHTTPServer(("127.0.0.1", 18099), Handler).serve_forever()
