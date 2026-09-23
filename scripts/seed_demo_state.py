#!/usr/bin/env python3
"""Seed a representative dashboard state into an ISOLATED state dir.

Purpose
-------
Some dashboard pages render empty states because the host has no data. This
tool distinguishes a *genuine* empty state (no data exists, controls work) from
a *broken* page by populating one isolated runtime through the real canonical
operations (the Qubit CLI, the vendored runtime CLI, the on-disk stores, and
the dashboard HTTP API), then serving that isolated instance for evidence
capture.

Safety
------
Every path is derived from ``--state-dir``. The tool refuses to run against the
operator's live homes (``~/.hermes``, ``~/.config/qubit-voicebridge``) and never
sets env vars for the caller's shell. It is stdlib-only and safe to re-run.

Modes
-----
    prepare   create the isolated layout + config + seed inputs (no state)
    seed      run every canonical seed op (idempotent; re-runnable)
    verify    start the isolated dashboard, fetch each page's backing API,
              print a table and (with --out) write the JSON responses
    serve     run the isolated dashboard in the foreground (blocks)
    all       prepare + seed + verify

The dashboard served is the real Qubit ``WebUI`` (same class the product uses):
it serves the built SPA and dispatches reads to the vendored upstream fork for
fork-owned routes and to the Qubit compat backend for Qubit-owned routes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8899
TOKEN = "seed-evidence-token"

# Page -> backing API used to prove the page is populated. ``kinds`` of
# "fork" / "qubit" record which authority answers (see qubit_asgi).
PAGE_PROBES: List[Tuple[str, str, str, str]] = [
    # page,          method, api,                          authority
    ("/chat",          "GET", "/api/qubit/chat/sessions",        "qubit"),
    ("/sessions",      "GET", "/api/sessions",                   "fork"),
    ("/files",         "GET", "/api/files",                      "qubit"),
    ("/models",        "GET", "/api/model/options",              "fork"),
    ("/cron",          "GET", "/api/cron/jobs",                  "fork"),
    ("/skills",        "GET", "/api/skills",                     "fork"),
    ("/plugins",       "GET", "/api/dashboard/plugins/hub",      "qubit"),
    ("/webhooks",      "GET", "/api/webhooks",                   "fork"),
    ("/profiles",      "GET", "/api/profiles",                   "fork"),
    ("/memory",        "GET", "/api/memory",                     "fork"),
    ("/qubit-boards",  "GET", "/api/ops/boards",                 "qubit"),
]


# ---------------------------------------------------------------------------
# isolated layout
# ---------------------------------------------------------------------------
def paths(state_dir: Path) -> Dict[str, Path]:
    sd = state_dir.resolve()
    p = {
        "root": sd,
        "home": sd / "home",
        "qubit_home": sd / "qubit-home",
        "config": sd / "voice.yaml",
        "inputs": sd / "inputs",
        "project": sd / "project-demo",
        "xdg": sd / "xdg",
    }
    p["xdg_config"] = p["xdg"] / "config"
    p["xdg_state"] = p["xdg"] / "state"
    p["xdg_data"] = p["xdg"] / "data"
    p["xdg_cache"] = p["xdg"] / "cache"
    return p


def build_env(state_dir: Path) -> Dict[str, str]:
    p = paths(state_dir)
    env = dict(os.environ)
    env.update({
        "HOME": str(p["home"]),
        "XDG_CONFIG_HOME": str(p["xdg_config"]),
        "XDG_STATE_HOME": str(p["xdg_state"]),
        "XDG_DATA_HOME": str(p["xdg_data"]),
        "XDG_CACHE_HOME": str(p["xdg_cache"]),
        # Explicit, non-``$HOME/.hermes`` so the vendored runtime keeps it
        # (qubit_runtime.use_qubit_home treats the external default as a
        # request to redirect to the Qubit-owned home).
        "QUBIT_HOME": str(p["qubit_home"]),
        "PYTHONPATH": str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", ""),
    })
    return env


def _refuse_live(state_dir: Path) -> None:
    sd = state_dir.resolve()
    home = Path.home().resolve()
    forbidden = [home / ".hermes", home / ".config" / "qubit-voicebridge",
                 home / ".local" / "state" / "qubit-voicebridge"]
    for bad in forbidden:
        if sd == bad or bad in sd.parents:
            raise SystemExit(f"refusing to operate on live state: {sd}")
    if str(sd) in ("/", str(home)):
        raise SystemExit(f"refusing to operate on {sd}")


def _run(cmd: List[str], env: Dict[str, str], *, timeout: int = 180) -> Dict[str, Any]:
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                          timeout=timeout, cwd=str(REPO_ROOT))
    return {
        "cmd": cmd,
        "rc": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-2000:],
    }


def _cli(state_dir: Path, args: List[str]) -> Dict[str, Any]:
    """Qubit CLI op with the isolated config."""
    p = paths(state_dir)
    cmd = ["qubit", *args, "--config", str(p["config"])]
    return _run(cmd, build_env(state_dir))


def _fork(state_dir: Path, args: List[str]) -> Dict[str, Any]:
    """Vendored upstream runtime op with the isolated QUBIT_HOME."""
    return _run(["qubit", "agent", *args], build_env(state_dir))


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------
def prepare(state_dir: Path, *, fresh: bool) -> Dict[str, Any]:
    p = paths(state_dir)
    _refuse_live(state_dir)
    if fresh and p["root"].exists():
        shutil.rmtree(p["root"])
    for key in ("root", "home", "qubit_home", "inputs", "project",
                "xdg_config", "xdg_state", "xdg_data", "xdg_cache"):
        p[key].mkdir(parents=True, exist_ok=True)
    p["config"].write_text(
        "logging:\n"
        "  jsonl: true\n"
        f"  jsonl_path: '{p['root']}'\n"
        "webui:\n"
        f"  token: {TOKEN}\n"
        f"  host: 127.0.0.1\n"
        f"  port: {DEFAULT_PORT}\n",
        encoding="utf-8",
    )
    _write_inputs(p)
    return {"state_dir": str(p["root"]), "config": str(p["config"]),
            "qubit_home": str(p["qubit_home"])}


def _write_inputs(p: Dict[str, Path]) -> None:
    """Seed inputs (not state): the foreign session JSONL, the skill payloads,
    the plugin manifest, and the Qubit skill manifest."""
    # 1. A Claude-Code-format session for `qubit agent sessions import`.
    claude_dir = p["inputs"] / "claude" / "demo-project"
    claude_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {"type": "summary", "summary": "Demo seeded conversation"},
        {"type": "user", "sessionId": "demo-session-1", "cwd": str(p["project"]),
         "message": {"role": "user", "content": [
             {"type": "text", "text": "What does the demo board show?"}]}},
        {"type": "assistant", "sessionId": "demo-session-1",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "One open task: 'Seed task'."}]}},
    ]
    (claude_dir / "demo-session-1.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    # 2. Qubit plugin manifest for `qubit plugins register`.
    plugin = {
        "id": "demo.evidence",
        "kind": "tool",
        "entrypoint": "demo.evidence.handler",
        "digest": hashlib.sha256(b"demo.evidence:1.0.0").hexdigest(),
        "label": "Demo Evidence",
        "version": "1.0.0",
        "description": "Seeded plugin entry for EMPTY_STATE_EVIDENCE",
        "capabilities": ["run"],
    }
    (p["inputs"] / "plugin-manifest.json").write_text(
        json.dumps(plugin, indent=2), encoding="utf-8")

    # 4. Qubit skill manifest for `qubit skills install` (Plugins hub row).
    manifest: Dict[str, Any] = {
        "skill_id": "demo.evidence.skill",
        "version": "1.0.0",
        "name": "Demo Evidence Skill",
        "description": "Seeded evidence skill for the Plugins hub",
        "source": "https://example.test/skills",
        "revision": "seed1",
        "license": "MIT",
        "capabilities": ["web.observe.metadata"],
        "scopes": ["web"],
        "tier": "read_only",
        "sensitivity": "internal",
        "approval_policy": "auto",
        "timeout_s": 30.0,
        "resource_limits": {"max_bytes": 4096},
        "audit_events": ["skill.run"],
        "compatibility": "linux",
        "rollback": "remove restores prior manifest state",
    }
    manifest["content_hash"] = _manifest_hash(manifest)
    (p["inputs"] / "qubit-skill-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")


def _manifest_hash(manifest: Dict[str, Any]) -> str:
    payload = {k: v for k, v in manifest.items() if k != "content_hash"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------
def seed(state_dir: Path) -> Dict[str, Any]:
    _refuse_live(state_dir)
    p = paths(state_dir)
    if not p["config"].is_file():
        prepare(state_dir, fresh=False)
    report: Dict[str, Any] = {}

    # --- fork: profile (Models + Profiles pages read /api/profiles) --------
    report["profile"] = _fork(state_dir, [
        "profile", "create", "demo-profile",
        "--description", "Seeded provider profile for evidence",
        "--no-alias"])
    report["model"] = _fork(state_dir, ["config", "set", "model",
                                        "ollama/llama3.1", "--force"])

    # --- fork: session with messages (Sessions page) ----------------------
    report["session"] = _fork(state_dir, [
        "sessions", "import", "--from", "claude",
        str(p["inputs"] / "claude" / "demo-project" / "demo-session-1.jsonl")])

    # --- fork: bundled skills (Skills page reads the fork skill store) -----
    report["fork_skill"] = _fork(state_dir, ["skills", "opt-in", "--sync"])

    # --- fork: cron schedule (Cron page) ----------------------------------
    report["cron"] = _fork(state_dir, [
        "cron", "create", "every 2h", "Summarize the demo board",
        "--name", "Demo sweep"])

    # --- fork: webhook subscription (Webhooks page) -----------------------
    report["webhook_enable"] = _fork(state_dir, [
        "config", "set", "platforms.webhook.enabled", "true"])
    report["webhook"] = _fork(state_dir, [
        "webhook", "subscribe", "demo-hook",
        "--prompt", "payload={payload}",
        "--events", "demo.created",
        "--description", "Seeded evidence subscription",
        "--deliver", "log"])

    # --- qubit: project folder + artifact (Files page) --------------------
    report["project"] = _cli(state_dir, [
        "project", "create", "Demo Project", str(p["project"]),
        "--description", "Seeded evidence project", "--use", "--confirm"])
    artifact = p["project"] / "evidence-artifact.md"
    artifact.write_text(
        "# Seeded artifact\n\n"
        "Real file written into the active Qubit project folder; the Files\n"
        "page lists it through /api/files (compat, project-root confined).\n",
        encoding="utf-8")
    (p["project"] / "notes.txt").write_text(
        "second seeded file\n", encoding="utf-8")

    # --- qubit: kanban board + task (Boards page) -------------------------
    report["board"] = _cli(state_dir, [
        "kanban", "boards-create", "demo", "--name", "Demo Board", "--confirm"])
    report["task"] = _cli(state_dir, [
        "kanban", "create", "Seed task", "--board", "demo",
        "--body", "Created by scripts/seed_demo_state.py", "--confirm"])

    # --- qubit: memory entry (Memory page search) -------------------------
    remember = _cli(state_dir, [
        "memory", "remember",
        "Demo memory entry: the EMPTY_STATE_EVIDENCE seeder ran on an "
        "isolated state dir."])
    report["memory"] = remember
    try:
        cid = str(json.loads(remember.get("stdout") or "{}")
                  .get("confirmation_id") or "")
    except ValueError:
        cid = ""
    if cid:
        report["memory_confirm"] = _cli(state_dir, [
            "approvals", "respond", cid, "approve"])

    # --- qubit: verified skill (verified-skills store) --------------------
    report["verified_skill"] = _seed_verified_skill(state_dir)

    # --- qubit: plugin entry (Plugins catalog) ----------------------------
    report["plugin"] = _cli(state_dir, [
        "plugins", "register", str(p["inputs"] / "plugin-manifest.json"),
        "--confirm"])

    # --- qubit: skill (Plugins hub row) -----------------------------------
    report["qubit_skill"] = _cli(state_dir, [
        "skills", "install", str(p["inputs"] / "qubit-skill-manifest.json"),
        "--confirm"])
    report["qubit_skill_enable"] = _cli(state_dir, [
        "skills", "enable", "demo.evidence.skill", "--confirm"])

    # --- chat session with a couple of messages (Chat page, real API) -----
    report["chat"] = _seed_chat_via_api(state_dir)
    return report


def _seed_verified_skill(state_dir: Path) -> Dict[str, Any]:
    submit = _cli(state_dir, [
        "verified-skills", "submit",
        "--name", "Demo Evidence Recipe",
        "--interface", "run demo evidence recipe",
        "--implementation", "python -c 'print(\"evidence\")'",
        "--acceptance-test", "run(recipe) == 'evidence'",
        "--task", "demo-evidence",
        "--capability", "web.observe.metadata",
        "--confirm"])
    skill_id = ""
    try:
        payload = json.loads(submit.get("stdout", "") or "{}")
        skill_id = str(payload.get("skill_id") or payload.get("id") or "")
    except ValueError:
        skill_id = ""
    verify: Dict[str, Any] = {"skipped": "no skill id parsed"}
    if skill_id:
        verify = _cli(state_dir, [
            "verified-skills", "verify", skill_id,
            "--reason", "owner verified seeded evidence recipe", "--confirm"])
    return {"submit": submit, "verify": verify, "skill_id": skill_id}


def _seed_chat_via_api(state_dir: Path) -> Dict[str, Any]:
    """Create a chat session + messages through the real dashboard HTTP API."""
    ui, port = _start_server(state_dir, port=0)
    try:
        envelope = {
            "session": {"id": "demo-chat-session",
                        "name": "Seeded evidence chat",
                        "model": "ollama/llama3.1"},
            "messages": [
                {"role": "user",
                 "content": "What is on the demo board?"},
                {"role": "assistant",
                 "content": "One open task: 'Seed task'. Nothing is fabricated."},
            ],
        }
        status, body = _http(port, "POST",
                             "/agent/api/qubit/chat/sessions/import",
                             payload=envelope)
        return {"status": status, "body": body, "via": "HTTP",
                "api": "/agent/api/qubit/chat/sessions/import"}
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# serve / probe
# ---------------------------------------------------------------------------
def _make_orchestrator(cfg: Any) -> Any:
    class _Task:
        active = False

    class _Orch:
        _route = "local"
        _web_active = 0

        def __init__(self, cfg: Any) -> None:
            self.cfg = cfg
            self.task = _Task()
            self._t0 = time.time()

    return _Orch(cfg)


def _load_config(state_dir: Path) -> Any:
    sys.path.insert(0, str(REPO_ROOT))
    from voicebridge.config import Config

    return Config.load(paths(state_dir)["config"])


def _start_server(state_dir: Path, *, port: int) -> Tuple[Any, int]:
    env = build_env(state_dir)
    os.environ.update({k: env[k] for k in (
        "HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
        "XDG_CACHE_HOME", "QUBIT_HOME")})
    from voicebridge.webui import WebUI

    cfg = _load_config(state_dir)
    ui = WebUI(_make_orchestrator(cfg), host="127.0.0.1", port=port,
               state_dir=paths(state_dir)["root"])
    ui.start()
    return ui, ui._server.server_address[1]


def _http(port: int, method: str, path: str,
          payload: Optional[Dict[str, Any]] = None,
          timeout: float = 30.0) -> Tuple[int, Any]:
    url = f"http://127.0.0.1:{port}{path}"
    data = None
    headers = {"X-Hermes-Session-Token": TOKEN, "Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw.decode("utf-8"))
            except ValueError:
                return r.status, raw.decode("utf-8", "replace")[:4000]
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw.decode("utf-8"))
        except ValueError:
            return e.code, raw.decode("utf-8", "replace")[:4000]
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def _nonempty(api: str, payload: Any) -> bool:
    if not isinstance(payload, dict):
        return bool(payload)
    for key in ("sessions", "profiles", "subscriptions", "entries", "jobs",
                "boards", "plugins", "catalog", "providers", "lines", "runs"):
        if key in payload:
            return bool(payload[key])
    return True


def verify(state_dir: Path, *, out_dir: Optional[Path],
           reuse_port: int = 0) -> int:
    _refuse_live(state_dir)
    p = paths(state_dir)
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    ui = None
    port = reuse_port
    if not port:
        ui, port = _start_server(state_dir, port=0)
    rows = []
    try:
        for page, method, api, authority in PAGE_PROBES:
            status, payload = _http(port, method, "/agent" + api)
            n = _count(api, payload)
            rows.append({"page": page, "api": api, "authority": authority,
                         "status": status, "count": n,
                         "populated": bool(n),
                         "response": payload})
            if out_dir:
                (out_dir / (api.strip("/").replace("/", "_") + ".json")
                 ).write_text(json.dumps(
                     {"page": page, "api": api, "authority": authority,
                      "status": status, "count": n, "response": payload},
                     indent=2), encoding="utf-8")
        # the memory search proves the seeded entry is retrievable
        status, payload = _http(port, "POST", "/agent/api/ops/run",
                                {"id": "memory_search",
                                 "args": {"query": "EMPTY_STATE_EVIDENCE"}})
        mem_n = _count("/api/ops/run", payload)
        rows.append({"page": "/memory", "api": "/api/ops/run (memory_search)",
                     "authority": "qubit", "status": status,
                     "count": mem_n,
                     "populated": mem_n > 0,
                     "response": payload})
        if out_dir:
            (out_dir / "ops_memory_search.json").write_text(
                json.dumps(rows[-1], indent=2), encoding="utf-8")
    finally:
        if ui is not None:
            ui.stop()
    width = max(len(r["page"]) for r in rows)
    print(f"{'page'.ljust(width)}  {'api':42}  {'auth':5}  st  count  populated")
    for r in rows:
        print(f"{r['page'].ljust(width)}  {r['api']:42}  "
              f"{r['authority']:5}  {r['status']:>3}  {r['count']:>5}  "
              f"{'YES' if r['populated'] else 'no'}")
    if out_dir:
        (out_dir / "summary.json").write_text(json.dumps(
            [{k: v for k, v in r.items() if k != "response"} for r in rows],
            indent=2), encoding="utf-8")
    return 0


def _count(api: str, payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return 0
    if api.endswith("/api/skills"):
        return len(payload.get("skills", payload)) if isinstance(
            payload.get("skills"), list) else 0
    node = payload.get("result") if isinstance(payload.get("result"),
                                                dict) else payload
    for key in ("sessions", "profiles", "subscriptions", "entries", "jobs",
                "boards", "plugins", "catalog", "providers", "lines", "runs"):
        v = node.get(key, payload.get(key))
        if isinstance(v, list):
            return len(v)
    if "ops/run" in api:
        for key in ("results", "matches", "memories", "hits"):
            if isinstance(node.get(key), list):
                return len(node[key])
    return 0


def serve(state_dir: Path, *, port: int) -> int:
    _refuse_live(state_dir)
    ui, bound = _start_server(state_dir, port=port)
    print(f"isolated dashboard: http://127.0.0.1:{bound}/  "
          f"(token in X-Hermes-Session-Token; state={state_dir})")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        ui.stop()
    return 0


# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["prepare", "seed", "verify", "serve",
                                     "all"])
    ap.add_argument("--state-dir", required=True,
                    help="isolated state dir (never production)")
    ap.add_argument("--fresh", action="store_true",
                    help="prepare: delete and recreate the state dir")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help="serve: bind port")
    ap.add_argument("--reuse-port", type=int, default=0,
                    help="verify: probe an already-running isolated server")
    ap.add_argument("--out", type=Path, default=None,
                    help="verify: write JSON responses here")
    ap.add_argument("--report", type=Path, default=None,
                    help="seed: write the op report JSON here")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    state_dir = Path(args.state_dir)
    if args.mode == "prepare":
        out = prepare(state_dir, fresh=args.fresh)
    elif args.mode == "seed":
        out = seed(state_dir)
    elif args.mode == "verify":
        return verify(state_dir, out_dir=args.out, reuse_port=args.reuse_port)
    elif args.mode == "serve":
        return serve(state_dir, port=args.port)
    else:  # all
        prepare(state_dir, fresh=args.fresh)
        out = seed(state_dir)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(out, indent=2), encoding="utf-8")
        return verify(state_dir, out_dir=args.out)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2) if args.json else
          f"{args.mode}: ok  state_dir={state_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
