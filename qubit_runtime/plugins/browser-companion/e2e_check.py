#!/usr/bin/env python3
"""Live end-to-end proof: the pair actually talks.

This is the check the wire contract alone cannot give: a real Browser Context
Protocol envelope is *produced by the extension's own code* (its committed
``lib/browser-context-protocol.mjs``, run under Node), POSTed over HTTP to the
route the companion wires, parsed by the companion's own parser, and then
consumed through the registered ``browser_get_context`` tool by the turn that
owns it. No mocks of either half: only the host's auth check and session store
are stood in for, because those belong to Qubit's API server, not to this pair.

Steps, each printed with what it proves:

1. Node renders ``serializeBrowserTurnEnvelope()`` → ``/tmp/.../envelope.json``.
2. The plugin's ``wire_extra`` factory is invoked as the API server invokes it,
   on a real ``aiohttp`` app; the resulting app is started on a loopback port
   for the duration of the check only.
3. ``POST /api/plugins/browser-companion/context`` with the extension's real
   envelope and ``X-Qubit-Session-Id`` → ``201 {object, stored: true, ...}``.
4. A *reference* turn for that session (the extension's own
   ``delivery: reference`` envelope, naming the same hash) is handed to the
   ``pre_llm_call`` hook, which adopts the upload and binds it to that owner.
5. ``browser_context_status`` then reports it, and ``browser_get_context``
   returns the page text **once**; a second call answers unavailable.

    .venv/bin/python qubit_runtime/plugins/browser-companion/e2e_check.py
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EXT = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-browser-ext" / "extension"
PLUGIN = Path(__file__).resolve().parent

sys.path.insert(0, str(REPO / "qubit_runtime"))
if str(PLUGIN.parent) not in sys.path:
    sys.path.insert(0, str(PLUGIN.parent))
sys.modules["browser_companion"] = importlib.import_module(PLUGIN.name)

SESSION_ID = "e2e-session-0001"
NODE_SCRIPT = """
import fs from 'fs';
const mod = await import(process.argv[3]);
// The extension never sends a full envelope with an empty context_hash: its real
// call sites (sidepanel.js serializePreparedContextMenuTurn / the upload path)
// compute browserContextPayloadHash(...) first and pass it in. This harness
// skipped that step, so the companion's parser — which requires the hash, and
// rejects an empty one — answered 422. ZERO in the source said anything about
// the hash being optional; the omission was the test's, so the test follows the
// extension's actual contract.
const activeTab = { id: 42, active: true, title: 'Example Domain', url: 'https://example.com/' };
const pageContext = {
  text: 'This domain is for use in illustrative examples.',
  meta: { description: 'Example', language: 'en', headings: ['Example Domain'] },
};
const contextHash = mod.browserContextPayloadHash({ activeTab, selectedTabs: [], pageContext });
const full = mod.serializeBrowserTurnEnvelope({
  humanInput: 'summarize this page',
  activeTab,
  pageContext,
  contextScope: { mode: 'pinned-tab' },
  contextHash,
  contextDelivery: 'full',
});
const parsed = JSON.parse(full);
const hash = parsed.source_receipt.context_hash;
if (!hash) {
  console.error('harness built a full envelope with an empty context_hash');
  process.exit(2);
}
const ref = mod.serializeBrowserTurnEnvelope({
  humanInput: 'and now the next one',
  contextHash: hash,
  contextDelivery: 'reference',
});
fs.writeFileSync(process.argv[2], JSON.stringify({ full, reference: ref, hash }));
console.log(JSON.stringify({ hash, chars: full.length }));
"""


def render_envelopes() -> tuple[str, str, str]:
    out = Path(tempfile.mkdtemp(prefix="qubit-bcp-")) / "envelopes.json"
    script = out.with_suffix(".mjs")
    script.write_text(NODE_SCRIPT, encoding="utf-8")
    result = subprocess.run(
        ["node", str(script), str(out), (EXT / "lib" / "browser-context-protocol.mjs").as_uri()],
        cwd=EXT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise SystemExit(f"node render failed:\n{result.stdout}\n{result.stderr}")
    payload = json.loads(out.read_text(encoding="utf-8"))
    print(f"1. extension rendered envelopes under Node: {result.stdout.strip()}")
    return payload["full"], payload["reference"], payload["hash"]


class _Adapter:
    """The API-server surface the factory is handed (auth + session store)."""

    def _check_auth(self, request):
        return None

    def _expected_api_key(self):
        return "e2e-key"

    async def _ensure_session_db_async(self):
        class _Db:
            async def get_session(self, session_id):
                return {"id": session_id}

        return _Db()

    def __getattr__(self, name):
        raise AttributeError(name)


async def run() -> int:
    import aiohttp
    from aiohttp import web

    pipe = sys.modules["browser_companion"]
    from browser_companion import context_routes, hooks

    # Production wires ONE store instance into both the routes and the hooks,
    # and it does that inside register(). This harness imports the plugin
    # without calling register(), so hooks._STORE stayed None and pre_llm_call
    # returned None for a turn whose upload the routes had accepted — the
    # upload landed in pipe.STORE while the hook read a different, unset store.
    # Wire it the way register() does rather than letting the test assert a
    # wiring production never omits.
    hooks.set_store(pipe.STORE)
    pipe.tools.set_store(pipe.STORE)

    # The tool table the assertions dispatch through is the real registry, and
    # register() is what populates it. Without this call the registry holds no
    # browser-context tool from THIS tree (it still holds the three from
    # whichever bundled copy was discovered first), so every tool assertion
    # fails with "Unknown tool". Register first, then dispatch.
    class _RegistryCtx:
        """The subset of PluginContext register() actually uses."""

        def __init__(self) -> None:
            self.state = None
            self.profile_name = "default"

        def get_config(self, _key, default=None, **_kw):
            return default

        def register_tool(self, **kwargs):
            from tools.registry import registry

            registry.register(
                name=kwargs["name"],
                toolset=kwargs.get("toolset", "browser-companion"),
                schema=kwargs.get("schema"),
                handler=kwargs.get("handler"),
                description=kwargs.get("description", ""),
                emoji=kwargs.get("emoji", ""),
            )

        def register_hook(self, *_a, **_k):
            return None

        def register_skill(self, *_a, **_k):
            return None

        def register_platform_handler(self, *_a, **_k):
            return None

    pipe.register(_RegistryCtx())

    full, reference, hash_value = render_envelopes()

    app = web.Application()
    context_routes.make_wire_extra(pipe.STORE, version="0.4.0")(app.router, _Adapter())
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    base = f"http://127.0.0.1:{port}"
    print(f"2. companion routes served on {base} (real aiohttp app, real router)")

    # An async client: the server and the client share this loop, so a blocking
    # request would deadlock against the very route it is calling.
    async with aiohttp.ClientSession() as client:

        async def post(path: str, body: str) -> tuple[int, dict]:
            async with client.post(
                base + path,
                data=body.encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Qubit-Session-Id": SESSION_ID},
            ) as response:
                return response.status, json.loads(await response.text() or "{}")

        status_code, uploaded = await post(context_routes.CONTEXT_UPLOAD_PATH, full)
        if status_code != 200 or uploaded.get("object") != "qubit.browser_companion.context_upload":
            # Network failures would surface here rather than mid-test.
            print(json.dumps(uploaded)[:400])
        assert status_code == 200, (status_code, uploaded)
        assert uploaded.get("stored") is True, uploaded
    print(f"3. POST {context_routes.CONTEXT_UPLOAD_PATH} -> {status_code} {json.dumps(uploaded)}")

    # The owning turn: a reference turn naming that same hash adopts the upload.
    adopted = asyncio.get_running_loop().run_in_executor(
        None,
        lambda: hooks.pre_llm_call(
            session_id=SESSION_ID,
            task_id="e2e-task",
            turn_id="e2e-turn-2",
            user_message=reference,
        ),
    )
    adopted_notice = await adopted
    assert adopted_notice and "context_id=" in adopted_notice.get("context", ""), adopted_notice
    print(f"4. pre_llm_call on the reference turn adopted it: {adopted_notice['context'][:110]}...")

    # The tools the model calls, authorized by pre_tool_call for that same owner.
    owner_kwargs = {"session_id": SESSION_ID, "task_id": "e2e-task", "turn_id": "e2e-turn-2"}
    hooks.pre_tool_call(tool_name="browser_context_status", args={}, **owner_kwargs)
    status = json.loads(_call("browser_context_status", {}, owner_kwargs))
    assert status.get("available") is True, status
    context_id = status["context_id"]
    print(f"5. browser_context_status -> available, context_id={context_id}, scope={status.get('scope')}")

    hooks.pre_tool_call(tool_name="browser_get_context", args={"context_id": context_id}, **owner_kwargs)
    # _call() returns the handler's JSON text; parse before asserting on keys.
    # The old line called .get() on that string and crashed before the payload
    # was ever inspected.
    consumed = json.loads(_call("browser_get_context", {"context_id": context_id}, owner_kwargs))
    assert consumed.get("success") is not False, consumed
    payload = consumed
    page_text = json.dumps(payload)
    assert "illustrative examples" in page_text, page_text[:400]
    print(f"6. browser_get_context -> {len(page_text)} chars, carries the extension's page text")

    hooks.pre_tool_call(tool_name="browser_get_context", args={"context_id": context_id}, **owner_kwargs)
    again = json.loads(_call("browser_get_context", {"context_id": context_id}, owner_kwargs))
    assert again.get("available") is False, again
    print(f"7. second browser_get_context -> {json.dumps(again)} (consume-once holds)")

    await runner.cleanup()
    print("\nE2E: PASS — extension envelope -> companion route -> hook -> tool")
    return 0


def _call(tool: str, args: dict, kwargs: dict) -> str:
    """Invoke the registered handler the way the registry dispatches it."""
    from tools.registry import registry

    raw = registry.dispatch(tool, args, **kwargs)
    return raw if isinstance(raw, str) else json.dumps(raw)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
