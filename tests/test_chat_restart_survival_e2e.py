"""End-to-end proof: `/chat` survives a mid-turn client abandon and a backend bounce.

This is NOT a unit test with mocks. It spins the REAL dashboard listener
(``voicebridge.webui.WebUI`` -> ``ThreadingHTTPServer``) on an ephemeral
loopback port over a throwaway ``QUBIT_HOME`` and drives the REAL HTTP/SSE
surface (``POST /agent/api/qubit/chat/stream`` and
``GET /agent/api/qubit/chat/sessions/<id>``) over a real socket. Only the
model call is stubbed (``providers.gateway.run_role``), because a hermetic gate
cannot reach a real LLM; every persistence, marker, and routing decision under
test is the shipped code path.

Two product claims are proven against that surface:

A. **Abandoned stream persists the reply, exactly once.** The client opens the
   SSE stream, the turn worker is mid-flight, and the client closes the socket
   (the browser-refresh case). The worker thread — not the closed response
   generator — writes the assistant reply, and it is written exactly once.

B. **A process death mid-turn is surfaced as ``interrupted_turn``.** A turn
   writes a durable marker when it starts; a turn that never concludes (process
   death) leaves that marker behind. A FRESH backend instance, pointed at the
   same home, answers the session-show route with the original prompt under
   ``interrupted_turn`` so a client can re-issue it instead of the operator
   retyping ``continue``.

What this does NOT prove: a real browser click-through (no browser is driven
here). The SPA's ``withBackoff`` reconnect behaviour is covered separately by
the vitest suite and was verified against a live server with curl. See
``docs/E2E_CHAT_RESTART_PROOF.md``.

Run:
    .venv/bin/python -m pytest tests/test_chat_restart_survival_e2e.py -p no:cacheprovider
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
_RUNTIME = REPO / "qubit_runtime"
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

pytestmark = pytest.mark.integration

_TOKEN = "e2e-token"


# ---------------------------------------------------------------------------
# Harness: a real WebUI listener over a throwaway home
# ---------------------------------------------------------------------------
class _Orch:
    """The minimal orchestrator shape WebUI reads at construction/route time."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.task = type("T", (), {"active": False, "run_id": None})()
        self._route = "gateway"
        self._web_active = 0


class E2EBackend:
    """A real dashboard listener (real socket, real SSE framing, real auth)."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.state = base / "state"
        self.home = base / "runtime"
        self.state.mkdir(parents=True, exist_ok=True)
        self.home.mkdir(parents=True, exist_ok=True)
        # The marker store and the runtime home must agree: ``_chat_home``
        # prefers ``paths.home``, and WebUI publishes the runtime home into
        # QUBIT_HOME. Point both at the same directory.
        os.environ["QUBIT_HOME"] = str(self.home)
        os.environ["XDG_STATE_HOME"] = str(base / "xdg")
        cfg_path = base / "voice.yaml"
        cfg_path.write_text(
            "logging:\n"
            f"  jsonl_path: '{self.state}'\n"
            "webui:\n"
            f"  token: {_TOKEN}\n"
            "paths:\n"
            f"  home: '{self.home}'\n"
        )
        from voicebridge.config import Config

        self.cfg = Config.load(cfg_path)
        self.ui = None
        self.port = None

    def start(self) -> None:
        from voicebridge.webui import WebUI

        self.ui = WebUI(_Orch(self.cfg), host="127.0.0.1", port=0,
                        state_dir=self.state)
        self.ui.start()
        self.port = self.ui._server.server_address[1]

    def stop(self) -> None:
        if self.ui is not None:
            try:
                self.ui.stop()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass

    # -- route helpers (real HTTP over a real socket) ----------------------
    def sse_post(self, payload: dict) -> socket.socket:
        """Open the SSE stream and return the raw socket after the headers.

        HTTP/1.0 close-delimited SSE: the body has no Content-Length, so a
        client that wants to abandon mid-stream must read the socket itself.
        """
        body = json.dumps(payload).encode()
        head = (
            "POST /agent/api/qubit/chat/stream HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Content-Type: application/json\r\n"
            f"X-Api-Token: {_TOKEN}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        req = head + body
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=15)
        sock.sendall(req)
        hdr = b""
        while b"\r\n\r\n" not in hdr:
            chunk = sock.recv(4096)
            if not chunk:
                break
            hdr += chunk
        assert b"200" in hdr.split(b"\r\n", 1)[0], hdr
        return sock

    def session_show(self, session: str) -> dict:
        import httpx

        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"http://127.0.0.1:{self.port}/agent/api/qubit/chat/sessions/{session}",
                headers={"X-Api-Token": _TOKEN})
        assert resp.status_code == 200, (resp.status_code, resp.text)
        return resp.json()


# ---------------------------------------------------------------------------
# Model stub: role-aware so the intent classifier never blocks
# ---------------------------------------------------------------------------
class FakeModel:
    """Stub ``providers.gateway.run_role``.

    ``role != "chat"`` (the intent classifier and any probe role) returns a
    typed no-model result so branching falls back to the deterministic schema.
    ``role == "chat"`` signals ``entered`` and then parks on ``release`` (or
    never returns, to simulate a process death).
    """

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.reply = "Partial sentence one. Final sentence two."
        self.block_forever = False

    def __call__(self, prompt, cfg, *, on_sentence=None, should_stop=None,
                 role="chat", **kw):
        from voicebridge.providers.contracts import ProviderResult

        if role != "chat":
            return ProviderResult(ok=False, code="MODEL_UNAVAILABLE",
                                  message="e2e: no classify model")
        self.entered.set()
        if on_sentence:
            on_sentence("Partial sentence one.")
        if self.block_forever:
            self.release.wait(timeout=60)  # never released -> worker never ends
        else:
            self.release.wait(timeout=30)
        if on_sentence:
            on_sentence(self.reply)
        return ProviderResult(ok=True, text=self.reply, provider="local",
                              model="e2e", identity="local/e2e")


@pytest.fixture()
def backend(tmp_path, monkeypatch):
    from voicebridge.providers import gateway as GW

    model = FakeModel()
    monkeypatch.setattr(GW, "run_role", model)
    be = E2EBackend(tmp_path / "e2e")
    be.start()
    try:
        yield be, model
    finally:
        be.stop()


def _assistant_rows(be: E2EBackend, session: str) -> list:
    from voicebridge import chat_transport as CT

    msgs = CT.store(be.cfg).messages(session, limit=50).get("messages", [])
    return [m for m in msgs if m["role"] == "assistant"]


def _wait(predicate, deadline_s: float = 8.0, interval: float = 0.05):
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------------------
# (A) abandoned stream persists the reply exactly once
# ---------------------------------------------------------------------------
def test_abandoned_stream_persists_reply_exactly_once(backend):
    be, model = backend
    session = "e2e-abandon-1"

    sock = be.sse_post({"text": "tell me a long story about the sea",
                        "session": session})
    try:
        # The turn worker is running and parked in the model call.
        assert model.entered.wait(10), "chat worker never reached the model"
        sock.close()  # <-- the browser refresh: abandon mid-stream
    finally:
        sock.close()

    # The worker (not the closed response generator) persists the reply.
    model.release.set()
    rows = _wait(lambda: _assistant_rows(be, session))
    assert rows, "reply was NOT persisted after the client abandoned the stream"
    assert len(rows) == 1, f"reply persisted more than once: {len(rows)} rows"
    assert rows[0]["content"] == model.reply

    # A stored turn is exactly-once even after the generator unwinds: give the
    # drain-path duplicate guard time to (not) fire.
    time.sleep(0.5)
    assert len(_assistant_rows(be, session)) == 1, "duplicate assistant row"

    # The abandoned-but-completed turn concluded, so no crash marker survives.
    from tui_gateway import turn_marker

    cleared = _wait(lambda: turn_marker.read_turn_marker(be.home, session)
                    is None, deadline_s=5.0)
    assert cleared is True, "a concluded turn must not leave a crash marker"


# ---------------------------------------------------------------------------
# (B) a process death mid-turn is surfaced as interrupted_turn on the next boot
# ---------------------------------------------------------------------------
_DEMO_SCRIPT = REPO / "scripts" / "e2e_chat_restart_demo.py"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _spawn_boot(mode: str, base: Path, port: int):
    """Start one REAL backend process (``systemctl``-style boot)."""
    import subprocess

    env = dict(os.environ)
    env.update({
        "QUBIT_E2E_DEMO_BASE": str(base),
        "QUBIT_E2E_DEMO_PORT": str(port),
        "QUBIT_E2E_DEMO_TOKEN": _TOKEN,
        "QUBIT_HOME": str(base / "runtime"),
        "XDG_STATE_HOME": str(base / "xdg"),
        "PYTHONPATH": os.pathsep.join(
            [str(REPO), str(_RUNTIME), env.get("PYTHONPATH", "")]).strip(os.pathsep),
    })
    return subprocess.Popen(
        [sys.executable, str(_DEMO_SCRIPT), mode],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
        cwd=str(REPO))


def _await_reachable(port: int, session: str, deadline_s: float = 30.0):
    import httpx

    deadline = time.time() + deadline_s
    while time.time() < deadline:
        try:
            resp = httpx.get(
                f"http://127.0.0.1:{port}/agent/api/qubit/chat/sessions/{session}",
                headers={"X-Api-Token": _TOKEN}, timeout=3)
            if resp.status_code == 200:
                return resp.json()
        except Exception:  # noqa: BLE001 - server not up yet
            time.sleep(0.2)
    return None


def _kill(proc) -> None:
    import subprocess

    try:
        proc.terminate()
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def test_process_death_mid_turn_surfaces_interrupted_turn(tmp_path):
    """Boot 1 is KILLED mid-turn; a fresh process (boot 2) surfaces the marker.

    Both boots are genuine OS processes driven over the real HTTP surface. Boot
    1 runs a real chat turn whose model call parks forever; the test kills the
    process mid-turn (``SIGKILL`` — exactly an operator restart). The durable
    marker written by the real turn-start path survives, and boot 2 serves it on
    the session-show route as ``interrupted_turn`` with the original prompt.
    """
    import httpx

    from tui_gateway import turn_marker

    base = tmp_path / "e2e"
    session = "e2e-crash-1"
    prompt = "tell me a long story about the sea"

    # ---- boot 1: run a real turn, then die mid-turn ----
    port1 = _free_port()
    proc1 = _spawn_boot("boot1", base, port1)
    try:
        # Readiness probe: boot 1 has no session yet, so any 200 means up.
        ready = None
        deadline = time.time() + 30
        while time.time() < deadline and ready is None:
            try:
                ready = httpx.get(
                    f"http://127.0.0.1:{port1}/agent/api/qubit/chat/sessions",
                    headers={"X-Api-Token": _TOKEN}, timeout=3)
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        assert ready is not None and ready.status_code == 200, "boot 1 never came up"

        # Open a real SSE turn; the request reaches the server and the turn
        # starts, then the client read times out while the model call is parked.
        try:
            with httpx.Client(timeout=5) as c:
                c.post(f"http://127.0.0.1:{port1}/agent/api/qubit/chat/stream",
                       json={"text": prompt, "session": session},
                       headers={"X-Api-Token": _TOKEN})
        except httpx.HTTPError:
            pass  # mid-stream timeout: the turn is still running server-side

        # The durable marker was written by the REAL turn-start path.
        marker = _wait(lambda: turn_marker.read_turn_marker(base / "runtime", session))
        assert marker is not None, "turn start did not write an interrupted-turn marker"
        assert marker["prompt"] == prompt
    finally:
        # ---- process death mid-turn ----
        proc1.kill()
        proc1.wait(timeout=10)

    # The marker survives the process death.
    assert turn_marker.read_turn_marker(base / "runtime", session) is not None

    # ---- boot 2: a fresh process over the SAME home ----
    port2 = _free_port()
    proc2 = _spawn_boot("boot2", base, port2)
    try:
        payload = _await_reachable(port2, session)
        assert payload is not None, "boot 2 never became reachable"
        assert payload.get("interrupted_turn") is not None, \
            "a surviving marker was not surfaced by the session-show route"
        assert payload["interrupted_turn"]["prompt"] == prompt
    finally:
        _kill(proc2)
