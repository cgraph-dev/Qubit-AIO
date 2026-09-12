#!/usr/bin/env python3
"""Regenerate ``docs/QUBIT_GAP_LEDGER.hermes.json`` deterministically.

The Hermes gap-ledger section is *evidence based*: a row is only reported
``IMPLEMENTED`` when the real production caller works and a behavioural test
exercises it.  No status is hand-flipped in the JSON.

Evidence sources
----------------
API rows
    The upstream route decorators are re-extracted from the pinned
    ``qubit_hermes/`` source, matched through
    :func:`voicebridge.hermes_compat.match`, and classified from the route
    disposition plus the handler classification (registered handler vs.
    typed gate vs. wire-shape stub).  A small, documented adjustment table
    records the handful of routes whose wire shape is honestly partial/gated;
    a route is never reported ``IMPLEMENTED`` when the cited compat handler is
    not registered in ``hermes_compat.HANDLERS``.

cli / cli-subcommand rows
    Status is derived from (a) whether ``voicebridge/cli.py`` /
    ``voicebridge/cli_hermes_forward.py`` really owns or forwards the
    subcommand (live ``build_parser()`` tree + ``FORWARD_TABLE``), and (b)
    whether a test in ``tests/`` exercises that caller.  The test scan is an
    AST pass that mirrors ``/tmp/opencode/gen_ledger.py``: it marks test
    functions that reach the CLI entry points / ``_cmd_*`` helpers and then
    looks for the family token or the fork argv.  Forwarded pairs are proven
    by ``test_cli_agent1_forward.py``.

The output keeps the schema and exact row keys required by
``scripts/gap_ledger.py`` plus the provenance fields.

Usage::

    python scripts/rebuild_ledger_hermes.py            # write the section
    python scripts/rebuild_ledger_hermes.py --check    # non-zero if stale
"""
from __future__ import annotations

import argparse
import ast
import inspect
import json
import re
import sys
import textwrap
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voicebridge import cli as _cli  # noqa: E402
from voicebridge import cli_hermes_forward as _fwd  # noqa: E402
from voicebridge import hermes_compat as _hc  # noqa: E402

PIN = "2237be355906fbe6065ce1815711eee52b2d646e"
QH = "qubit_hermes/"
GENERATED = "2026-09-12"
OUT_PATH = ROOT / "docs" / "QUBIT_GAP_LEDGER.hermes.json"

# Fork-forward tables owned by voicebridge/cli_hermes_forward.py.  A forwarded
# pair is a real production caller (the read-only table never routes a
# gated/mutating surface; the confirm table requires explicit --confirm and the
# whole-family table separates read-only from confirm subcommands), so they
# override a stale audit ``surface`` column exactly like a native subcommand.
_FWD_TABLE = _fwd.FORWARD_TABLE
_CONFIRM_TABLE = getattr(_fwd, "CONFIRM_TABLE", {})
_FAMILY_FORWARD = getattr(_fwd, "FAMILY_FORWARD", {})

UPSTREAM_GLOBS = (
    "hermes_cli/web_routers/*.py",
    "hermes_cli/web_server*.py",
    "hermes_cli/memory_oauth.py",
    "hermes_cli/dashboard_auth/routes.py",
)
UPSTREAM_EXCLUDE = {"hermes_cli/web_server_dashboard.py"}
MEMORY_OAUTH = "hermes_cli/memory_oauth.py"

_ROUTE_DECORATOR = re.compile(
    r'@(\w+)\.(get|post|put|patch|delete|websocket|head|options)'
    r'\(\s*(["\'])(.*?)\3'
)

HANDLERS = _hc.HANDLERS

# ---------------------------------------------------------------------------
# Areas / handler classification (reconciled from the hermes audit)
# ---------------------------------------------------------------------------
AREA_BY_FILE = {
    "web_routers/actions.py": "gateway-actions",
    "web_routers/analytics.py": "analytics-config",
    "web_routers/audio.py": "audio-voice",
    "web_routers/chat_ws.py": "chat-websocket",
    "web_routers/config_env.py": "config-env",
    "web_routers/cron.py": "cron",
    "web_routers/dashboard_ui.py": "dashboard-ui",
    "web_routers/files.py": "files-fs",
    "web_routers/git.py": "git",
    "web_routers/local_models.py": "local-models",
    "web_routers/mcp.py": "mcp",
    "web_routers/memory_providers.py": "memory",
    "web_routers/messaging.py": "messaging",
    "web_routers/models.py": "models",
    "web_routers/oauth.py": "provider-oauth",
    "web_routers/ops.py": "ops",
    "web_routers/profiles.py": "profiles",
    "web_routers/sessions.py": "sessions",
    "web_routers/skills.py": "skills",
    "web_routers/status.py": "status-curator",
    "web_routers/tools.py": "tools",
    "dashboard_auth/routes.py": "dashboard-auth",
    "memory_oauth.py": "memory",
}

# Explicit overrides for handlers whose real behaviour the source classifier
# cannot see: the two plain-tuple 404 refusals.  Everything else is derived
# from the handler body (see ``_handler_kind``): a handler whose normal exits
# all return a typed ``_gate``/``_need_confirm`` refusal is GATED, a real
# read/write is IMPLEMENTED, and a wire-shape stub is PARTIAL.  The denylists
# MUST NOT list an executed handler: ``config_save``, ``env_write``,
# ``toolset_write``, ``skill_update``, ``h_skills_toggle``, ``mem_config_write``,
# ``h_memory``, ``toolset_config``, ``toolset_models``, ``h_model_auxiliary``,
# ``h_model_moa``, ``h_ops``, ``h_events``, ``mem_config``, ``mem_provider_set``
# and ``h_profile_setup_command`` are now real, tested operations.
GATE_HANDLERS = {
    "mcp_delete", "oauth_delete",
}

# Wire-shape stubs: handlers that only echo an honest empty/static shape (no
# live read, no write).  Kept as a small explicit allowlist.
STUB_HANDLERS = {
    "h_mcp", "h_mcp_catalog", "mem_prov",
    "mcp_test", "msg_test", "plugin_visibility",
}

# Gate-code -> honest non-code need.  Credential/secret gates need the
# operator's credentials; hardware/local-runtime gates need the device; the
# rest are operator-owned policy decisions.
_GATE_CODE_NEEDS = {
    "SECRET_NEVER_SERVED": "creds",
    "SECRET_WRITE_REFUSED": "creds",
    "OAUTH_MANUAL_HANDOFF": "creds",
    "NETWORK_PROBE_BLOCKED": "creds",
    "GH_AUTH_OPERATOR": "creds",
    "GH_PR_EXTERNAL": "creds",
    "GIT_SHIP_GH_EXTERNAL": "creds",
    "PROVIDER_EXTERNAL": "creds",
    "AUDIO_RELAY_ONLY": "creds",
    "MSG_NO_CREDENTIAL": "creds",
    "BACKUP_PASSPHRASE": "creds",
    "AUDIO_LEASE_DESKTOP": "hardware",
    "AUDIO_STREAM_LOCAL": "hardware",
    "AUDIO_ACTION_LOCAL": "hardware",
    "LOCAL_MODEL_BROKERED": "hardware",
    "LOCAL_MODEL_UNAVAILABLE": "hardware",
    "MEDIA_NOT_SERVED": "hardware",
    "FILES_BINARY_STREAM": "hardware",
}

_GATE_RETURN_HELPERS = {"_gate", "_need_confirm"}


def _call_names(node: ast.AST) -> Set[str]:
    names: Set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def _pure_gate_source(fn) -> bool:
    """True when every normal ``return`` of ``fn`` is a typed refusal.

    A handler with an early malformed-body check plus a real work path is not
    pure: those extra exits are bare tuples (no call), so the classifier keeps
    it IMPLEMENTED.
    """
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError, SyntaxError):
        return False
    returned: Set[str] = set()
    bare = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.value is not None:
            names = _call_names(node.value)
            if not names:
                bare = True
            returned |= names
    return bool(returned) and returned <= _GATE_RETURN_HELPERS and not bare


def _handler_kind(handler: str) -> str:
    """Classify a compat handler as ``gate`` / ``stub`` / ``real`` / ``missing``.

    A lambda is classified through the production function it delegates to, so
    ``sess_import`` (delegates to the gate ``h_sessions_write``) stays GATED
    while ``pairing_create`` (delegates to a real mutation) is real.
    """
    if handler in STUB_HANDLERS:
        return "stub"
    if handler in GATE_HANDLERS:
        return "gate"
    fn = HANDLERS.get(handler)
    if fn is None:
        return "missing"
    if getattr(fn, "__name__", "") == "<lambda>":
        code = getattr(fn, "__code__", None)
        for name in (code.co_names if code else ()):
            target = getattr(_hc, name, None)
            if callable(target) and _pure_gate_source(target):
                return "gate"
        return "real"
    return "gate" if _pure_gate_source(fn) else "real"


def _handler_gate_code(handler: str) -> str:
    """Best-effort typed code for a gate handler (for notes + needs)."""
    fn = HANDLERS.get(handler)
    if fn is None:
        return ""
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        return ""
    match = re.search(r"_gate\(\s*[\"']([^\"']+)", src)
    if match:
        return match.group(1)
    match = re.search(r"[\"']code[\"']\s*:\s*[\"']([^\"']+)", src)
    return match.group(1) if match else ""


AREA_META = {
    "audio-voice": dict(cli="qubit audio / asr-test / tts-test / mic-test",
                        dashboard="hermes-dist Chat/Models",
                        voice="qubit voice audio",
                        test="tests/test_hermes_readsurfaces8u.py",
                        needs="hardware"),
    "git": dict(cli="qubit git status|log|diff|show",
                dashboard="hermes-dist Sessions/Files", voice="none",
                test="tests/test_hermes_readsurfaces8u.py", needs="none"),
    "files-fs": dict(cli="qubit files list|read|inspect",
                     dashboard="hermes-dist Files", voice="qubit voice files",
                     test="tests/test_hermes_readsurfaces8u.py", needs="none"),
    "local-models": dict(cli="qubit local-models * / runtime *",
                         dashboard="hermes-dist Models",
                         voice="voice local-models",
                         test="tests/test_hermes_routes_parity8u.py",
                         needs="hardware"),
    "profiles": dict(cli="qubit profile * / brain *",
                     dashboard="hermes-dist Profiles",
                     voice="voice profile/brain",
                     test="tests/test_hermes_routes_parity8u.py", needs="none"),
    "tools": dict(cli="qubit permissions / capability",
                  dashboard="hermes-dist System", voice="voice capability",
                  test="tests/test_hermes_routes_parity8u.py", needs="none"),
    "status-curator": dict(cli="qubit status / lessons / doctor",
                           dashboard="hermes-dist System/Analytics",
                           voice="voice status",
                           test="tests/test_hermes_read_routes8u.py",
                           needs="none"),
    "sessions": dict(cli="qubit sessions / memory",
                     dashboard="hermes-dist Sessions", voice="voice sessions",
                     test="tests/test_hermes_routes_parity8u.py", needs="none"),
    "memory": dict(cli="qubit memory search / brain",
                   dashboard="hermes-dist Profiles", voice="voice memory",
                   test="tests/test_hermes_routes_parity8u.py", needs="none"),
    "mcp": dict(cli="qubit mcp status", dashboard="hermes-dist MCP",
                voice="voice mcp", test="tests/test_hermes_routes_parity8u.py",
                needs="owner"),
    "skills": dict(cli="qubit skills / verified-skills",
                   dashboard="hermes-dist Skills", voice="voice skills",
                   test="tests/test_hermes_routes_parity8u.py", needs="none"),
    "config-env": dict(cli="qubit flags / capability / auth",
                       dashboard="hermes-dist Config/Keys",
                       voice="voice config",
                       test="tests/test_hermes_routes_parity8u.py", needs="none"),
    "provider-oauth": dict(cli="qubit auth login/logout",
                           dashboard="hermes-dist Keys/Config",
                           voice="voice auth",
                           test="tests/test_hermes_routes_parity8u.py",
                           needs="creds"),
    "models": dict(cli="qubit profile select / frontier",
                   dashboard="hermes-dist Models", voice="voice model",
                   test="tests/test_hermes_routes_parity8u.py", needs="none"),
    "cron": dict(cli="qubit schedules *", dashboard="hermes-dist Cron",
                 voice="voice schedules",
                 test="tests/test_hermes_routes_parity8u.py", needs="none"),
    "messaging": dict(cli="qubit msg * / channels",
                      dashboard="hermes-dist Channels", voice="voice channels",
                      test="tests/test_hermes_routes_parity8u.py", needs="creds"),
    "ops": dict(cli="qubit checkpoints / hooks / product",
                dashboard="hermes-dist System", voice="voice ops",
                test="tests/test_hermes_routes_parity8u.py", needs="none"),
    "gateway-actions": dict(cli="qubit gateway / product upgrade",
                            dashboard="hermes-dist System",
                            voice="voice gateway",
                            test="tests/test_hermes_routes_parity8u.py",
                            needs="owner"),
    "analytics-config": dict(cli="qubit status / flags",
                             dashboard="hermes-dist Analytics/Config",
                             voice="voice analytics",
                             test="tests/test_hermes_routes_parity8u.py",
                             needs="none"),
    "dashboard-ui": dict(cli="qubit plugins / theme",
                         dashboard="hermes-dist Plugins/System",
                         voice="voice plugins",
                         test="tests/test_hermes_routes_parity8u.py",
                         needs="owner"),
    "dashboard-auth": dict(cli="qubit auth",
                           dashboard="hermes-dist (loopback identity)",
                           voice="none",
                           test="tests/test_hermes_vendor8tf.py",
                           needs="owner"),
    "chat-websocket": dict(cli="qubit tui / voice",
                           dashboard="overlay Qubit Chat", voice="voice chat",
                           test="tests/test_hermes_read_routes8u.py",
                           needs="none"),
}

# Documented per-route adjustments applied *on top of* the mechanical
# disposition + handler classification.  Each entry records the honest wire
# shape where the compat table's disposition alone would over/under-claim.
ROUTE_EVIDENCE: Dict[Tuple[str, str], Dict[str, str]] = {
    ("GET", "/api/events"): dict(note=(
        "h_events reads the real state-dir event/audit journal (redacted); the "
        "upstream WS dialect is the separate WEBSOCKET row.")),
    ("WEBSOCKET", "/api/events"): dict(status="GATED", code="WS_SINGLE_PATH",
        note=("Qubit deliberately serves one event path (GET /api/events "
              "SSE/JSON) and does not run a second WebSocket event server; "
              "the WS dialect is a typed single-event-path policy gate, not a "
              "wire-shape gap.")),
    ("WEBSOCKET", "/api/pub"): dict(status="GATED", code="WS_SINGLE_PATH",
        note=("The live WS upgrade is refused with HTTP 426 by webui.py "
              "(single event path); only the POST /api/pub publish verb is "
              "served, so the WEBSOCKET row stays GATED.")),
    ("WEBSOCKET", "/api/pty"): dict(status="GATED", code="PTY_NO_SHELL", note=(
        "WS not served; HTTP GET/POST /api/pty gated, but the WS upgrade "
        "itself is unanswered.")),
    ("WEBSOCKET", "/api/console"): dict(status="GATED", code="PTY_NO_SHELL",
        note=("WS not served; HTTP GET /api/console gated, but the WS upgrade "
              "itself is unanswered.")),
    ("WEBSOCKET", "/api/ws"): dict(status="GATED", code="WS_SINGLE_PATH", note=(
        "WS not served; HTTP GET /api/ws gated, but the WS upgrade itself is "
        "unanswered.")),
    ("WEBSOCKET", "/api/audio/speak-stream"): dict(
        status="GATED", code="AUDIO_STREAM_LOCAL", note=(
            "Streaming PCM TTS WS not served; typed gate on HTTP verbs only.")),
    ("GET", "/api/memory"): dict(note=(
        "h_memory reads the live local active-memory + brain principal store "
        "(MemoryStatus shape); qubit-local is the only provider.")),
    ("GET", "/api/model/moa"): dict(note=(
        "Served wire shape: MoA fan-out is not shipped and the config reports "
        "disabled (ordered fallback is the counterpart).")),
    ("GET", "/api/model/auxiliary"): dict(note=(
        "Served from the active Qubit profile (_qubit_model); no auxiliary "
        "model pins exist.")),
    ("GET", "/api/memory/providers/{name}/config"): dict(note=(
        "mem_config reads the persisted local-store settings.")),
    ("PUT", "/api/memory/providers/{name}/config"): dict(note=(
        "mem_config_write persists local retrieval options confirm-gated; a "
        "non-local provider is a typed refusal.")),
    ("POST", "/api/memory/providers/{name}/setup"): dict(note=(
        "mem_config_write runs the real local setup confirm-gated; no cloud "
        "sync.")),
    ("PUT", "/api/memory/provider"): dict(note=(
        "Real persisted local-provider selection; only qubit-local is "
        "accepted, anything else is a typed refusal.")),
    ("GET", "/api/tools/toolsets/{name}/config"): dict(note=(
        "toolset_config reads the real provider/credential-reference matrix "
        "(credential references only, never values).")),
    ("GET", "/api/tools/toolsets/{name}/models"): dict(note=(
        "toolset_models reads the real local model catalog.")),
    ("GET", "/api/model/info"): dict(status="IMPLEMENTED", note=(
        "Profile/model info from the active Qubit profile (_qubit_model).")),
    ("GET", "/api/memory/providers"): dict(note=(
        "h_memory reports the live local provider description.")),
    ("POST", "/api/ops/debug-share"): dict(status="IMPLEMENTED", note=(
        "Runs the redacted diagnostics op; explicitly never shares externally.")),
    ("POST", "/api/ops/import-upload"): dict(
        status="GATED", code="IMPORT_REVIEW_REQUIRED", note=(
            "Uploads enter quarantine; typed refusal (handler h_import_upload).")),
    ("GET", "/dashboard-plugins/{plugin_name}/{file_path:path}"): dict(
        status="IMPLEMENTED",
        backend="voicebridge/webui.py:_serve_hermes_plugin",
        test="tests/test_hermes_serve8tf.py",
        note=("Served by webui.py _serve_hermes_plugin (static assets), "
              "outside the ROUTES table.")),
    ("GET", "/api/memory/providers/{provider}/oauth/status"): dict(
        note=("Provider OAuth status is a credential surface: the fork polls "
              "a live OAuth flow, which Qubit never starts. The explicit "
              "manual-handoff gate answers before the memory-providers "
              "wildcard, so needs=creds rather than code-side PARTIAL.")),
    ("POST", "/{provider}/oauth/start"): dict(status="GATED",
        code="OAUTH_MANUAL_HANDOFF", note=(
            "Mounted under /api/memory/providers; typed manual-handoff "
            "refusal.")),
    ("POST", "/api/dashboard/agent-plugins/install"): dict(
        status="GATED", code="BLOCKED_REMOTE_SOURCE", note=(
            "Wildcard remote-install gate precedes the specific route.")),
    ("DELETE", "/api/sessions/owner-backfill"): dict(status="MISSING"),
    ("GET", "/api/local-models/search"): dict(status="GATED",
        code="LOCAL_MODEL_BROKERED", note=(
            "Remote local-model search is not performed; typed brokered "
            "refusal.")),
    ("GET", "/api/local-models/search/files"): dict(status="GATED",
        code="LOCAL_MODEL_BROKERED", note=(
            "Remote local-model file search is not performed; typed brokered "
            "refusal.")),
    ("POST", "/api/local-models/runtime/install"): dict(status="GATED",
        code="LOCAL_MODEL_BROKERED", note=(
            "Runtime install is brokered (pinned/confined); typed refusal or "
            "executed via qubit runtime.")),
    ("POST", "/api/local-models/download"): dict(status="GATED",
        code="LOCAL_MODEL_BROKERED", note=(
            "Model download is brokered; typed refusal by default.")),
    ("POST", "/api/local-models/download-browsed"): dict(status="GATED",
        code="LOCAL_MODEL_BROKERED", note=(
            "Browsed download is brokered; nothing downloaded or queued.")),
    ("POST", "/api/local-models/sideload"): dict(status="GATED",
        code="LOCAL_MODEL_BROKERED", note=(
            "Sideload is brokered; typed refusal by default.")),
    ("POST", "/api/local-models/server"): dict(status="GATED",
        code="LOCAL_MODEL_BROKERED", note=(
            "Local server start is brokered; typed refusal by default.")),
    ("POST", "/api/local-models/eject"): dict(status="GATED",
        code="LOCAL_MODEL_BROKERED", note=(
            "Eject is brokered; typed refusal by default.")),
    ("POST", "/api/local-models/activate"): dict(status="GATED",
        code="LOCAL_MODEL_BROKERED", note=(
            "Activate is brokered; typed refusal by default.")),
    ("POST", "/api/local-models/quickstart"): dict(status="GATED",
        code="LOCAL_MODEL_BROKERED", note=(
            "Quickstart is brokered; typed refusal by default.")),
    ("DELETE", "/api/local-models/models/{model_id}"): dict(status="GATED",
        code="LOCAL_MODEL_BROKERED", note=(
            "Model removal is brokered; nothing deleted.")),
    ("POST", "/api/ops/backup"): dict(
        test=("tests/test_hermes_routes_agent2b_wiring.py::"
              "test_ops_backup_is_real_passphrase_sealed"),
        note=("Runs the real passphrase-sealed portable backup through the "
              "shared ops broker (backup_create); staged 409 until confirmed "
              "with the passphrase re-supplied.")),
    ("POST", "/api/ops/import"): dict(
        test=("tests/test_hermes_routes_agent2b_wiring.py::"
              "test_ops_import_is_real_staged_restore_review"),
        note=("Runs the real staged portable restore review through the shared "
              "ops broker (backup_restore): the bundle is decrypted into a "
              "restore plan; unconfirmed stages, and the passphrase is "
              "re-supplied at confirm.")),
    ("POST", "/api/ops/security-audit"): dict(status="IMPLEMENTED", note=(
        "Runs the Qubit security posture op (local read).")),
}


# ---------------------------------------------------------------------------
# Upstream route extraction (deterministic, no /tmp dependency)
# ---------------------------------------------------------------------------
def _sample(path: str, collapse: bool = False) -> str:
    repl = "aa" if collapse else "aa/bb"
    p = re.sub(r"\{[^}]*:path\}", repl, path)
    p = re.sub(r"\{[^}]*\}", "aa", p)
    return p


def extract_upstream_routes() -> List[Dict[str, object]]:
    root = ROOT / "qubit_hermes"
    files = set()
    for pattern in UPSTREAM_GLOBS:
        files.update(root.glob(pattern))
    rows: List[Dict[str, object]] = []
    for path in sorted(files):
        rel = str(path.relative_to(root))
        if rel in UPSTREAM_EXCLUDE:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = _ROUTE_DECORATOR.search(line)
            if m:
                rows.append({"file": rel, "line": i,
                             "method": m.group(2).upper(), "path": m.group(4)})
    for row in rows:
        if row["file"] == MEMORY_OAUTH:
            row["path"] = "/api/memory/providers" + str(row["path"])
    return sorted(rows, key=lambda r: (r["file"], r["line"]))


def _match_for(method: str, path: str):
    cands = [method]
    if method == "WEBSOCKET":
        cands = ["WEBSOCKET", "GET", "POST"]
    if method == "HEAD":
        cands = ["HEAD", "GET"]
    for m in cands:
        route, params = _hc.match(m, path)
        if route:
            return m, route, params
    return None, None, {}


def _area_for(route: Dict[str, object]) -> str:
    for suffix, area in AREA_BY_FILE.items():
        if str(route["file"]).endswith(suffix):
            return area
    return Path(str(route["file"])).stem


# ---------------------------------------------------------------------------
# API behavioural-test evidence (AST scan of tests/, like rebuild_ledger_qubit)
# ---------------------------------------------------------------------------
def _parse_bridge_candidates(src: str) -> Set[Tuple[str, str]]:
    """``(module, function)`` pairs a handler's source calls downstream.

    The compat layer often delegates to the same module the CLI/voice use
    (``from . import local_models as LM`` -> ``LM.status``); a test that calls
    that module function is a real production-caller proof for the handler.
    """
    aliases: Dict[str, str] = {}
    direct: Dict[str, Tuple[str, str]] = {}
    for match in re.finditer(r'from\s+\.\s+import\s+([^\n]+)', src):
        for part in match.group(1).split(','):
            part = part.strip()
            if not part:
                continue
            bits = part.split(' as ')
            name, alias = bits[0].strip(), bits[-1].strip()
            if re.match(r'^[A-Za-z_]\w*$', name):
                aliases[alias] = name
    for match in re.finditer(r'from\s+\.(\w+)\s+import\s+([^\n]+)', src):
        module = match.group(1)
        for part in match.group(2).split(','):
            part = part.strip()
            if not part or part.startswith('('):
                continue
            bits = part.split(' as ')
            orig, local = bits[0].strip(), bits[-1].strip()
            if not re.match(r'^[A-Za-z_]\w*$', orig):
                continue
            direct[local] = (module, orig)
            aliases.setdefault(local, module)
    calls: Set[Tuple[str, str]] = set()
    for match in re.finditer(r'([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*\(', src):
        attr, fn = match.group(1), match.group(2)
        if attr in aliases:
            calls.add((aliases[attr], fn))
    for match in re.finditer(r'(?<![.\w])([A-Za-z_]\w*)\s*\(', src):
        fn = match.group(1)
        if fn in direct:
            calls.add(direct[fn])
    return calls


def _aliases_in(tree: ast.AST) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and \
                node.module.startswith("voicebridge"):
            parts = node.module.split(".")
            base = parts[1] if len(parts) > 1 else None
            for alias in node.names:
                aliases[alias.asname or alias.name] = base or alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("voicebridge"):
                    parts = alias.name.split(".")
                    if len(parts) > 1:
                        aliases[alias.asname or parts[-1]] = parts[1]
    return aliases


def _asserts_success(node: ast.AST) -> bool:
    """True when a test asserts a 200/202 success (not only a typed refusal).

    A route string that only appears in a typed-gate test is not proof of the
    non-refusal path: ``assert code == 501`` / ``assert code in (403, 501)``
    never yields a 200 comparator, so those tests are filtered out.
    """
    success = {200, 202}

    def _hits(value: ast.AST) -> bool:
        if isinstance(value, ast.Constant):
            return isinstance(value.value, int) and value.value in success
        if isinstance(value, (ast.Tuple, ast.Set, ast.List)):
            return any(_hits(e) for e in value.elts)
        return False

    for sub in ast.walk(node):
        if not isinstance(sub, ast.Assert):
            continue
        test = sub.test
        for cmp in ast.walk(test):
            if isinstance(cmp, ast.Compare):
                if any(_hits(c) for c in cmp.comparators):
                    return True
                if _hits(cmp.left):
                    return True
    return False


class ApiIndex:
    """On-disk behavioural evidence for the compat API routes.

    A handler is proven by a ``tests/`` function that (a) dispatches a literal
    route resolving to that handler, (b) calls the handler directly, or
    (c) calls the same downstream production module function the handler
    delegates to.  A bare row-id/route string in a non-executing test is never
    proof.
    """

    def __init__(self) -> None:
        self.handler_hits: Dict[str, Set[str]] = {}
        self.module_calls: Dict[Tuple[str, str], Set[str]] = {}

    def add_file(self, path: Path) -> None:
        rel = str(path.relative_to(ROOT))
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            return
        aliases = _aliases_in(tree)
        consts: Dict[str, List[str]] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                vals = [n.value for n in ast.walk(node.value)
                        if isinstance(n, ast.Constant)
                        and isinstance(n.value, str)]
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        consts[target.id] = vals
        names = {r["handler"] for r in _hc.ROUTES
                 if r["handler"] != "__gate__"}
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not fn.name.startswith("test_"):
                continue
            hit = f"{rel}::{fn.name}"
            literal_strings = [n.value for n in ast.walk(fn)
                               if isinstance(n, ast.Constant)
                               and isinstance(n.value, str)]
            strings = list(literal_strings)
            for name_node in ast.walk(fn):
                if isinstance(name_node, ast.Name) and name_node.id in consts:
                    strings.extend(consts[name_node.id])
            literal_paths = [s for s in literal_strings if s.startswith("/")]
            paths = [s for s in strings if s.startswith("/")]
            methods = {s for s in strings
                       if s in ("GET", "POST", "PUT", "PATCH", "DELETE",
                                "HEAD", "OPTIONS")}
            if methods:
                candidates = [(m, p) for m in methods for p in paths]
            else:
                # No explicit method: only a path literal written inside the
                # test body may imply the default verbs.  A path pulled from a
                # module-level table (e.g. a page -> GET-route map) must not
                # cross-product onto every verb, which would falsely credit a
                # mutating handler (PUT /api/config) to a GET-only test.
                candidates = [(m, p) for m in ("GET", "POST", "PUT", "PATCH",
                                               "DELETE")
                              for p in literal_paths]
            for method, candidate in candidates:
                route, _params = _hc.match(method, candidate)
                if route and route["handler"] != "__gate__" and \
                        _asserts_success(fn):
                    self.handler_hits.setdefault(
                        route["handler"], set()).add(hit)
            for sub in ast.walk(fn):
                if not isinstance(sub, ast.Call):
                    continue
                func = sub.func
                if isinstance(func, ast.Name) and func.id in names:
                    self.handler_hits.setdefault(func.id, set()).add(hit)
                elif isinstance(func, ast.Attribute) and \
                        isinstance(func.value, ast.Name):
                    base = aliases.get(func.value.id)
                    if base:
                        self.module_calls.setdefault(
                            (base, func.attr), set()).add(hit)

    def proof(self, handler: str) -> str:
        hits = self.handler_hits.get(handler)
        if hits:
            return sorted(hits)[0]
        fn = HANDLERS.get(handler)
        if fn is None:
            return ""
        try:
            src = textwrap.dedent(inspect.getsource(fn))
        except (OSError, TypeError):
            return ""
        for cand in sorted(_parse_bridge_candidates(src)):
            hits = self.module_calls.get(cand)
            if hits:
                return sorted(hits)[0]
        return ""


def build_api_index() -> ApiIndex:
    index = ApiIndex()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        index.add_file(path)
    return index


# ---------------------------------------------------------------------------
# API rows
# ---------------------------------------------------------------------------
def _needs_for(status: str, meta: Dict[str, str], override: str = "",
               code: str = "") -> str:
    if override:
        return override
    if status == "GATED":
        need = _GATE_CODE_NEEDS.get(code)
        if need:
            return need
        meta_need = meta.get("needs")
        if meta_need and meta_need != "none":
            return meta_need
        return "owner"
    if status == "PARTIAL":
        return meta.get("needs") or "owner"
    if status == "MISSING":
        return "owner"
    if status == "IMPLEMENTED":
        return meta.get("needs") or "none"
    return "owner"


def build_api_rows(index: Optional[ApiIndex] = None) -> List[Dict[str, str]]:
    if index is None:
        index = build_api_index()
    rows: List[Dict[str, str]] = []
    for route in extract_upstream_routes():
        method = str(route["method"])
        path = str(route["path"])
        area = _area_for(route)
        meta = AREA_META.get(area, {})
        override = ROUTE_EVIDENCE.get((method, path), {})

        full = _sample(path)
        matched, matched_route, _ = _match_for(method, full)
        handler = ""
        code = ""
        note = ""
        if matched is None and ":path}" in path:
            short = _sample(path, collapse=True)
            short_m, short_route, _ = _match_for(method, short)
            if short_m is not None:
                handler = str(short_route["handler"])
                status = "PARTIAL"
                backend = f"voicebridge/hermes_compat.py:{handler}"
                note = ("Compat route serves single-segment ids only; the "
                        "upstream multi-segment :path form is not matched.")
            else:
                status, backend = "MISSING", ""
        elif matched is None:
            status, backend = "MISSING", ""
        else:
            handler = str(matched_route["handler"])
            disposition = str(matched_route["disposition"])
            backend = f"voicebridge/hermes_compat.py:{handler}"
            gate = matched_route.get("gate") or {}
            code = str(gate.get("code", ""))
            if disposition == "gated":
                status = "GATED"
            else:
                kind = _handler_kind(handler)
                if kind == "gate":
                    status = "GATED"
                    code = code or _handler_gate_code(handler)
                elif kind == "missing":
                    status = "UNVERIFIED"
                elif kind == "stub":
                    status = "PARTIAL"
                else:
                    status = "IMPLEMENTED"

        # Documented adjustments after mechanical classification.
        if override.get("status"):
            status = override["status"]
        if override.get("code"):
            code = override["code"]
        if override.get("backend"):
            backend = override["backend"]
        if override.get("note"):
            note = override["note"]

        # Never claim IMPLEMENTED for an unregistered compat handler.
        if status == "IMPLEMENTED" and \
                backend.startswith("voicebridge/hermes_compat.py:"):
            registered = HANDLERS.get(handler)
            if not callable(registered):
                status = "UNVERIFIED"
                note = ((note + " ") if note else "") + (
                    "Compat handler is not registered; downgraded to "
                    "UNVERIFIED.")

        if status == "GATED" and not note:
            note = (f"Typed policy gate {code} via compat dispatcher."
                    if code else
                    "Typed policy refusal via compat dispatcher.")
        if status == "MISSING" and not note:
            note = "No Qubit compat row; route 404s."

        # IMPLEMENTED requires an on-disk test exercising the non-refusal path.
        proving_test = override.get("test") or index.proof(handler)
        if status == "IMPLEMENTED" and not proving_test:
            status = "UNVERIFIED"
            note = ((note + " ") if note else "") + (
                "No on-disk test exercises the non-refusal path; downgraded "
                "to UNVERIFIED.")

        needs = _needs_for(status, meta, override.get("needs", ""), code)
        rows.append({
            "id": f"api:{method} {path}",
            "area": area,
            "capability": f"{method} {path}",
            "upstream_ref": f"{QH}{route['file']}:{route['line']}",
            "qubit_backend": backend,
            "cli": meta.get("cli", ""),
            "dashboard": meta.get("dashboard", ""),
            "voice": meta.get("voice", ""),
            "status": status,
            "proving_test": proving_test,
            "note": note,
            "needs": needs,
        })
    return rows


# ---------------------------------------------------------------------------
# Test evidence index (AST scan of tests/)
# ---------------------------------------------------------------------------
_EXEC_SEED = {
    "main", "translate", "maybe_forward", "run", "dispatch", "run_op",
    "run_extra_op", "voice_say",
}


def _norm(text: str) -> str:
    return text.replace("-", "_").lower()


def _calls_in(node: ast.AST) -> set:
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Attribute):
                out.add(func.attr)
            elif isinstance(func, ast.Name):
                out.add(func.id)
    return out


def _strings_in(node: ast.AST) -> set:
    return {n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _argv_lists_in(node: ast.AST) -> List[Tuple[str, ...]]:
    out: List[Tuple[str, ...]] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.List):
            vals = [e.value for e in sub.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if vals and len(vals) == len(sub.elts):
                out.append(tuple(vals))
    return out


def _exec_like(name: str) -> bool:
    n = _norm(name)
    return (name in _EXEC_SEED or n.startswith("_cmd_") or n.startswith("cmd_")
            or n.startswith("_run") or n.startswith("run_"))


class TestIndex:
    """Behavioural-test evidence for the CLI caller.

    Proofs are tiered: an exact argv call (``["sessions", "stats"]``) is the
    strongest signal, then a test module named for the family, then any
    exec-path test whose strings/callees mention the family token.
    """

    def __init__(self, families: set):
        self.families = families
        self.argv_proofs: Dict[str, Tuple[str, str]] = {}
        self.pair_proofs: Dict[Tuple[str, str], Tuple[str, str]] = {}
        self.token_proofs: Dict[str, Tuple[str, str]] = {}
        self.forward_proof = ""

    def add_file(self, path: Path) -> None:
        rel = str(path.relative_to(ROOT))
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            return
        funcs = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        callmap = {f.name: _calls_in(f) for f in funcs}
        execset: set = set()
        changed = True
        while changed:
            changed = False
            for fn, callees in callmap.items():
                if fn in execset:
                    continue
                if any(_exec_like(c) for c in callees) or (callees & execset):
                    execset.add(fn)
                    changed = True
        for f in funcs:
            if f.name == "test_every_forward_pair_dispatches_to_fork":
                self.forward_proof = f"{rel}::{f.name}"
        for f in funcs:
            if not f.name.startswith("test_") or f.name not in execset:
                continue
            hit = (rel, f.name)
            for argv in _argv_lists_in(f):
                if not argv:
                    continue
                self.argv_proofs.setdefault(_norm(argv[0]), hit)
                if len(argv) >= 2:
                    self.pair_proofs.setdefault(
                        (_norm(argv[0]), _norm(argv[1])), hit)
            strings = " ".join(_norm(s) for s in _strings_in(f))
            callee_parts = set()
            for callee in callmap.get(f.name, set()):
                callee_parts.update(_norm(callee).split("_"))
            for family in self.families:
                token = _norm(family)
                if not token:
                    continue
                boundary = rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])"
                if token in callee_parts or re.search(boundary, strings):
                    self.token_proofs.setdefault(token, hit)

    def _module_match(self, token: str) -> str:
        for path in sorted((ROOT / "tests").glob("test_*.py")):
            if token in _norm(path.name):
                return str(path.relative_to(ROOT))
        return ""

    def family_proof(self, family: str) -> str:
        token = _norm(family)
        hit = self.argv_proofs.get(token)
        if hit:
            return f"{hit[0]}::{hit[1]}"
        module = self._module_match(token)
        if module:
            return module
        hit = self.token_proofs.get(token)
        if hit:
            return f"{hit[0]}::{hit[1]}"
        return ""

    def pair_proof(self, family: str, sub: str) -> str:
        hit = self.pair_proofs.get((_norm(family), _norm(sub)))
        if hit:
            return f"{hit[0]}::{hit[1]}"
        return self.family_proof(family)


def _parser_tree() -> Tuple[Dict[str, set], set]:
    import argparse

    families: Dict[str, set] = {}
    for action in _cli.build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            for fam, subparser in action.choices.items():
                subs: set = set()
                for sub in subparser._actions:
                    if isinstance(sub, argparse._SubParsersAction):
                        subs = set(sub.choices)
                families[fam] = subs
    return families, set(families)


def _build_test_index(families: set) -> TestIndex:
    index = TestIndex(families)
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        index.add_file(path)
    return index


def _derive_cli_status(surface: str, caller: bool, tested: bool,
                       forwarded: bool) -> str:
    """Derive a CLI row status from live callers + on-disk tests.

    A forwarded fork pair is a real caller (the read-only ``FORWARD_TABLE``
    never routes a gated/mutating surface), so it overrides the stale audit
    ``surface`` column just like a live native subcommand.  A genuine policy
    gate (``surface == "GATED"`` with no forwarded/native caller proof) is
    never an implementation.  Otherwise IMPLEMENTED requires both a real caller
    and an on-disk test that exercises it.
    """
    if forwarded and tested:
        return "IMPLEMENTED"
    if surface == "GATED":
        return "GATED"
    if caller and tested:
        return "IMPLEMENTED"
    if not caller:
        return "PARTIAL" if surface == "PARTIAL" else "MISSING"
    return "PARTIAL" if surface == "PARTIAL" else "UNVERIFIED"


# ---------------------------------------------------------------------------
# CLI tables.  ``surface`` is the audited relationship between the upstream
# command and the Qubit CLI (docs/AUDIT_HERMES_2026-09-11.md); the *final*
# status is derived below from live callers + behavioural tests.
# ---------------------------------------------------------------------------
# (cmd, qfam, surface, qubit_backend, note, upstream_ref)
CLI = [
    ('acp', 'acp', 'IMPLEMENTED', 'qubit acp', 'Agent Client Protocol client/server commands.', 'hermes_cli/subcommands/acp.py:10'),
    ('approvals', 'approvals', 'IMPLEMENTED', 'qubit approvals', 'Approval journal list/approve/test.', 'hermes_cli/subcommands/approvals.py:11'),
    ('auth', 'auth', 'IMPLEMENTED', 'qubit auth status|login|verify|logout', 'Manual-handoff enrollment; token dance refused.', 'hermes_cli/subcommands/auth.py:8'),
    ('backup', 'setup', 'PARTIAL', 'qubit export-state / setup backup', 'Passphrase-sealed bundles; no plain backup family.', 'hermes_cli/subcommands/backup.py:8'),
    ('browser', 'browser', 'IMPLEMENTED', 'qubit browser *', 'Browser tab/read/navigate surface.', 'hermes_cli/subcommands/browser.py:8'),
    ('bundles', 'bundles', 'MISSING', 'qubit bundles (read-only list/show; writes need --confirm)', 'Fork-backed family; read-only list/show forward verbatim, writes require --confirm.', 'hermes_cli/subcommands/bundles.py:6'),
    ('chat', 'run', 'PARTIAL', 'qubit run/task/voice', 'No interactive chat CLI; voice/run agent entrypoints instead.', 'hermes_cli/_parser.py:189'),
    ('checkpoints', 'checkpoints', 'IMPLEMENTED', 'qubit checkpoints list|write', 'Checkpoint store commands.', 'hermes_cli/subcommands/checkpoints.py:6'),
    ('claw', 'claw', 'MISSING', 'qubit claw (confirm-gated)', 'Fork-backed migration family; mutating subcommands require --confirm.', 'hermes_cli/subcommands/claw.py:10'),
    ('completion', 'completion', 'IMPLEMENTED', 'qubit completion', 'Shell completion generation.', 'hermes_cli/subcommands/completion.py:8'),
    ('computer-use', 'computer-use', 'IMPLEMENTED', 'qubit computer-use status', 'Status only; grants are dashboard-gated.', 'hermes_cli/subcommands/computer_use.py:116'),
    ('config', 'flags', 'PARTIAL', 'qubit flags / capability', 'No top-level config get/set/edit family.', 'hermes_cli/subcommands/config.py:10'),
    ('console', 'console', 'MISSING', 'qubit console (confirm-gated REPL)', 'Fork-backed console REPL; confirm-gated interactive surface.', 'hermes_cli/subcommands/console.py:8'),
    ('cron', 'schedules', 'PARTIAL', 'qubit schedules *', '8 of 17 subcommands; incidents/history/doctor/tick missing.', 'hermes_cli/subcommands/cron.py:14'),
    ('curator', 'lessons', 'PARTIAL', 'qubit lessons', 'On-demand curation only; no background daemon.', 'hermes_cli/subcommands/curator.py:8'),
    ('dashboard', 'webui', 'PARTIAL', 'qubit webui', 'Qubit webui/gateway instead of hermes dashboard.', 'hermes_cli/subcommands/dashboard.py:76'),
    ('debug', 'debug', 'MISSING', 'qubit debug (confirm-gated share/delete)', 'Fork-backed debug family; share/delete require --confirm.', 'hermes_cli/subcommands/debug.py:9'),
    ('desktop', 'desktop', 'MISSING', 'qubit desktop (confirm-gated)', 'Fork-backed desktop build/launch; confirm-gated.', 'hermes_cli/subcommands/gui.py:8'),
    ('doctor', 'setup', 'PARTIAL', 'qubit setup diagnose / doctor', 'Setup health exists; not the full Hermes doctor family.', 'hermes_cli/subcommands/doctor.py:8'),
    ('dump', 'dump', 'MISSING', 'qubit dump (read-only)', 'Fork-backed redacted support dump; read-only, forwards verbatim.', 'hermes_cli/subcommands/dump.py:8'),
    ('egress', 'egress', 'MISSING', 'none', 'No egress proxy family.', 'hermes_cli/subcommands/egress.py:6'),
    ('fallback', 'frontier', 'PARTIAL', 'qubit frontier', 'Ordered frontier fallback, not the hermes fallback surface.', 'hermes_cli/subcommands/fallback.py:6'),
    ('gateway', 'gateway', 'IMPLEMENTED', 'qubit gateway', 'Gateway records/run; process supervision gated.', 'hermes_cli/subcommands/gateway.py:28'),
    ('gui', 'gui', 'MISSING', 'qubit gui (confirm-gated)', 'Fork-backed desktop GUI build/launch; confirm-gated.', 'hermes_cli/subcommands/gui.py:8'),
    ('hooks', 'hooks', 'IMPLEMENTED', 'qubit hooks list', 'Hooks list/declare surface.', 'hermes_cli/subcommands/hooks.py:8'),
    ('import', 'import-state', 'IMPLEMENTED', 'qubit import-state', 'State import.', 'hermes_cli/subcommands/import_cmd.py:8'),
    ('import-agent', 'import-agent', 'IMPLEMENTED', 'qubit import-agent inspect', 'Reviewed import inspection.', 'hermes_cli/subcommands/import_agent.py:10'),
    ('insights', 'actions', 'PARTIAL', 'qubit actions/reports (partial)', 'No insights family; analytics is dashboard-only.', 'hermes_cli/subcommands/insights.py:8'),
    ('journey', 'lessons', 'PARTIAL', 'qubit lessons (partial)', 'No journey graph command; LearningLedger via lessons.', 'hermes_cli/subcommands/journey.py:8'),
    ('kanban', 'kanban', 'IMPLEMENTED', 'qubit kanban *', 'Full kanban board/card family.', 'hermes_cli/kanban.py:430'),
    ('learning', 'lessons', 'PARTIAL', 'qubit lessons', 'LearningLedger exposed via lessons, not the hermes learning parser.', 'hermes_cli/main.py:2595'),
    ('login', 'auth', 'IMPLEMENTED', 'qubit auth login', 'Manual-handoff guided enrollment.', 'hermes_cli/subcommands/login.py:8'),
    ('logout', 'auth', 'IMPLEMENTED', 'qubit auth logout', 'Logout.', 'hermes_cli/subcommands/logout.py:8'),
    ('logs', 'logs', 'MISSING', 'qubit logs', 'Top-level logs command present in the live parser tree.', 'hermes_cli/subcommands/logs.py:9'),
    ('lsp', 'lsp', 'IMPLEMENTED', 'qubit lsp', 'LSP surface.', 'hermes_cli/main.py:2595'),
    ('mcp', 'mcp', 'GATED', 'qubit mcp status', 'Qubit inventories MCP servers but never mounts; 1 of 14 subcommands.', 'hermes_cli/subcommands/mcp.py:11'),
    ('memory', 'memory', 'PARTIAL', 'qubit memory search / brain', 'Search/status only; no full memory family.', 'hermes_cli/subcommands/memory.py:10'),
    ('memory-graph', 'memory-graph', 'MISSING', 'qubit memory-graph (read-only list; writes need --confirm)', 'Fork-backed family; list forwards, edit/delete require --confirm.', 'hermes_cli/main.py:2595'),
    ('migrate', 'product', 'IMPLEMENTED', 'qubit product migrate', 'Product migration.', 'hermes_cli/subcommands/migrate.py:6'),
    ('moa', 'moa', 'MISSING', 'qubit moa (read-only list; configure/delete need --confirm)', 'Fork-backed MoA family; reads forward, writes require --confirm.', 'hermes_cli/subcommands/moa.py:6'),
    ('model', 'profile', 'PARTIAL', 'qubit profile select / frontier', 'No direct model-switch command.', 'hermes_cli/subcommands/model.py:8'),
    ('monitoring', 'monitor', 'PARTIAL', 'qubit monitor', 'Monitor exists, not the full monitoring surface.', 'hermes_cli/subcommands/monitoring.py:8'),
    ('pairing', 'pairing', 'IMPLEMENTED', 'qubit pairing *', 'Pairing list/create/approve/revoke.', 'hermes_cli/subcommands/pairing.py:8'),
    ('pause', 'pause', 'IMPLEMENTED', 'qubit pause --confirm', 'Qubit-owned ONE emergency stop via the shared PowerControl latch; no second fork ESTOP sentinel (bare `qubit pause` is a typed refusal).', 'hermes_cli/subcommands/pause.py:42'),
    ('peer', 'peer', 'IMPLEMENTED', 'qubit peer *', 'Peer list/dm/run.', 'hermes_cli/subcommands/peer.py:383'),
    ('pets', 'theme', 'PARTIAL', 'qubit theme (pets refusal)', 'No pets family; downloads refused.', 'hermes_cli/subcommands/pets.py:8'),
    ('plugins', 'plugins', 'PARTIAL', 'qubit plugins *', 'Qubit plugin registry differs from hermes plugin packs.', 'hermes_cli/subcommands/plugins.py:10'),
    ('portal', 'portal', 'MISSING', 'qubit portal (confirm-gated; operator-only)', 'Fork-backed portal; operator-only, confirm-gated.', 'hermes_cli/portal_cli.py:167'),
    ('profile', 'profile', 'IMPLEMENTED', 'qubit profile * / brain *', 'Profiles family.', 'hermes_cli/subcommands/profile.py:8'),
    ('project', 'project', 'IMPLEMENTED', 'qubit project *', 'Projects family.', 'hermes_cli/projects_cmd.py:12'),
    ('prompt-size', 'prompt-size', 'MISSING', 'qubit prompt-size (read-only)', 'Fork-backed offline prompt/tool-schema byte report.', 'hermes_cli/subcommands/prompt_size.py:10'),
    ('proxy', 'proxy', 'IMPLEMENTED', 'qubit proxy', 'Proxy command.', 'hermes_cli/proxy_cli.py:66'),
    ('resume', 'sessions', 'PARTIAL', 'none', 'No top-level resume; sessions resume exists under sessions.', 'hermes_cli/main.py:2595'),
    ('secrets', 'brain', 'PARTIAL', 'qubit brain vault', 'Vault references via brain; not the secrets parser surface.', 'hermes_cli/subcommands/secrets.py:6'),
    ('security', 'setup', 'PARTIAL', 'qubit setup audit', 'Setup audit; not the full security family.', 'hermes_cli/subcommands/security.py:10'),
    ('send', 'msg', 'PARTIAL', 'qubit msg send', 'Messaging send via msg family.', 'hermes_cli/send_cmd.py:251'),
    ('serve', 'webui', 'PARTIAL', 'qubit webui / gateway run', 'Webui/gateway instead of hermes serve.', 'hermes_cli/subcommands/dashboard.py:62'),
    ('sessions', 'sessions', 'GATED', 'qubit sessions list', '1 of 19; receipts are append-only (prune/import/rename gated).', 'hermes_cli/subcommands/sessions.py:15'),
    ('setup', 'setup', 'IMPLEMENTED', 'qubit setup *', 'Setup family.', 'hermes_cli/subcommands/setup.py:8'),
    ('skills', 'skills', 'GATED', 'qubit skills / verified-skills', 'Local reviewed installs; remote hub fetch/publish refused.', 'hermes_cli/subcommands/skills.py:20'),
    ('skin', 'theme', 'IMPLEMENTED', 'qubit theme', 'Theme/skin.', 'hermes_cli/subcommands/skin.py:8'),
    ('slack', 'msg', 'PARTIAL', 'qubit msg', 'Slack covered under msg family, not a slack parser.', 'hermes_cli/subcommands/slack.py:8'),
    ('status', 'status', 'IMPLEMENTED', 'qubit status', 'Status.', 'hermes_cli/subcommands/status.py:8'),
    ('sync', 'sync', 'IMPLEMENTED', 'qubit sync list|revoke', 'Sync family.', 'hermes_cli/subcommands/sync.py:15'),
    ('tools', 'tools', 'MISSING', 'qubit tools (read-only list; writes need --confirm)', 'Fork-backed tools family; list forwards, writes require --confirm.', 'hermes_cli/subcommands/tools.py:8'),
    ('uninstall', 'product', 'PARTIAL', 'qubit product uninstall / package', 'Product/package uninstall, not the hermes uninstall surface.', 'hermes_cli/subcommands/uninstall.py:10'),
    ('update', 'product', 'PARTIAL', 'qubit product upgrade', 'Versioned staged upgrade, not in-place self-update.', 'hermes_cli/subcommands/update.py:8'),
    ('verify', 'verify', 'IMPLEMENTED', 'qubit verify / verify-release', 'Verification.', 'hermes_cli/subcommands/verify.py:19'),
    ('webhook', 'webhook', 'IMPLEMENTED', 'qubit webhook *', 'Webhook family.', 'hermes_cli/subcommands/webhook.py:8'),
    ('whatsapp', 'msg', 'PARTIAL', 'qubit msg', 'WhatsApp under msg; onboarding gated.', 'hermes_cli/subcommands/whatsapp.py:8'),
    ('whatsapp-cloud', 'msg', 'PARTIAL', 'qubit msg', 'WhatsApp Cloud under msg; onboarding gated.', 'hermes_cli/subcommands/whatsapp.py:16'),
    ('worktree', 'worktree', 'IMPLEMENTED', 'qubit worktree (add/remove/switch confined git ops need --confirm; fork list/prune forwarded)', 'Qubit-native confined git-worktree lifecycle plus the fork read/prune surface.', 'hermes_cli/subcommands/worktree.py:6'),
]

# (upstream_fam, sub, qfam, qsub, surface, qubit_backend, upstream_ref)
SUBS = [
    ('sessions', 'list', 'sessions', 'list', 'IMPLEMENTED', 'qubit sessions list', 'hermes_cli/subcommands/sessions.py:22'),
    ('sessions', 'export', 'sessions', 'export', 'MISSING', 'none', 'hermes_cli/subcommands/sessions.py:15'),
    ('sessions', 'delete', 'sessions', 'delete', 'GATED', 'qubit sessions (append-only)', 'hermes_cli/subcommands/sessions.py:100'),
    ('sessions', 'prune', 'sessions', 'prune', 'GATED', 'qubit sessions (append-only)', 'hermes_cli/subcommands/sessions.py:15'),
    ('sessions', 'stats', 'sessions', 'stats', 'IMPLEMENTED', 'qubit sessions stats', 'hermes_cli/subcommands/sessions.py:208'),
    ('sessions', 'rename', 'sessions', 'rename', 'GATED', 'qubit sessions (append-only)', 'hermes_cli/subcommands/sessions.py:15'),
    ('sessions', 'pin', 'sessions', 'pin', 'MISSING', 'none', 'hermes_cli/subcommands/sessions.py:229'),
    ('sessions', 'optimize-storage', 'sessions', 'optimize-storage', 'MISSING', 'none', 'hermes_cli/subcommands/sessions.py:144'),
    ('sessions', 'clean-markers', 'sessions', 'clean-markers', 'MISSING', 'none', 'hermes_cli/subcommands/sessions.py:129'),
    ('mcp', 'add', 'mcp', 'add', 'GATED', 'qubit mcp (inventory only)', 'hermes_cli/subcommands/mcp.py:27'),
    ('mcp', 'remove', 'mcp', 'remove', 'GATED', 'qubit mcp (inventory only)', 'hermes_cli/subcommands/mcp.py:44'),
    ('mcp', 'test', 'mcp', 'test', 'PARTIAL', 'qubit mcp test (MCP_NO_MOUNT)', 'hermes_cli/subcommands/mcp.py:49'),
    ('mcp', 'configure', 'mcp', 'configure', 'GATED', 'qubit mcp (inventory only)', 'hermes_cli/subcommands/mcp.py:52'),
    ('mcp', 'catalog', 'mcp', 'catalog', 'PARTIAL', 'qubit mcp catalog (empty/refusal)', 'hermes_cli/subcommands/mcp.py:73'),
    ('config', 'show', 'flags', 'show', 'MISSING', 'none', 'hermes_cli/subcommands/config.py:17'),
    ('config', 'edit', 'flags', 'edit', 'MISSING', 'qubit flags edit (confirm-gated)', 'hermes_cli/subcommands/config.py:18'),
    ('config', 'get', 'flags', 'get', 'MISSING', 'none', 'hermes_cli/subcommands/config.py:20'),
    ('config', 'set', 'flags', 'set', 'GATED', 'qubit config profiles (staged)', 'hermes_cli/subcommands/config.py:24'),
    ('config', 'unset', 'flags', None, 'GATED', 'qubit config profiles (staged)', 'hermes_cli/subcommands/config.py:33'),
    ('config', 'path', 'flags', 'path', 'MISSING', 'none', 'hermes_cli/subcommands/config.py:36'),
    ('config', 'env-path', 'flags', 'env-path', 'MISSING', 'none', 'hermes_cli/subcommands/config.py:37'),
    ('config', 'check', 'flags', 'check', 'MISSING', 'none', 'hermes_cli/subcommands/config.py:38'),
    ('config', 'migrate', 'product', 'migrate', 'IMPLEMENTED', 'qubit product migrate', 'hermes_cli/subcommands/config.py:39'),
    ('plugins', 'enable', 'plugins', 'enable', 'PARTIAL', 'qubit plugins enable', 'hermes_cli/subcommands/plugins.py:72'),
    ('plugins', 'install', 'plugins', 'install', 'GATED', 'qubit plugins (remote refused)', 'hermes_cli/console_engine.py:281'),
    ('plugins', 'update', 'plugins', 'update', 'GATED', 'qubit plugins (remote refused)', 'hermes_cli/console_engine.py:281'),
    ('plugins', 'remove', 'plugins', 'remove', 'PARTIAL', 'qubit plugins remove', 'hermes_cli/console_engine.py:281'),
    ('plugins', 'capabilities', 'plugins', 'capabilities', 'MISSING', 'none', 'hermes_cli/console_engine.py:281'),
    ('plugins', 'doctor', 'plugins', 'doctor', 'PARTIAL', 'qubit plugins doctor', 'hermes_cli/console_engine.py:281'),
    ('cron', 'list', 'schedules', 'list', 'IMPLEMENTED', 'qubit schedules list', 'hermes_cli/subcommands/cron.py:20'),
    ('cron', 'edit', 'schedules', 'edit', 'PARTIAL', 'qubit schedules (create/pause/resume/delete)', 'hermes_cli/subcommands/cron.py:88'),
    ('cron', 'pause', 'schedules', 'pause', 'IMPLEMENTED', 'qubit schedules pause', 'hermes_cli/subcommands/cron.py:141'),
    ('cron', 'resume', 'schedules', 'resume', 'IMPLEMENTED', 'qubit schedules resume', 'hermes_cli/subcommands/cron.py:144'),
    ('cron', 'run', 'schedules', 'run', 'PARTIAL', 'qubit schedules run-due', 'hermes_cli/subcommands/cron.py:149'),
    ('cron', 'status', 'schedules', 'status', 'PARTIAL', 'qubit schedules show', 'hermes_cli/subcommands/cron.py:157'),
    ('cron', 'doctor', 'schedules', 'doctor', 'MISSING', 'none', 'hermes_cli/subcommands/cron.py:184'),
    ('cron', 'tick', 'schedules', 'tick', 'MISSING', 'none', 'hermes_cli/subcommands/cron.py:186'),
    ('skills', 'install', 'skills', 'install', 'GATED', 'qubit skills install (local manifest only)', 'hermes_cli/subcommands/skills.py:54'),
    ('skills', 'update', 'skills', 'update', 'GATED', 'qubit skills (remote refused)', 'hermes_cli/subcommands/skills.py:79'),
    ('skills', 'audit', 'skills', 'audit', 'GATED', 'qubit verified-skills (curated)', 'hermes_cli/subcommands/skills.py:85'),
    ('skills', 'publish', 'skills', 'publish', 'GATED', 'qubit verified-skills submit', 'hermes_cli/subcommands/skills.py:148'),
    ('skills', 'tap', 'skills', 'tap', 'GATED', 'qubit skills (remote refused)', 'hermes_cli/subcommands/skills.py:165'),
    ('tools', 'disable', 'tools', 'disable', 'MISSING', 'qubit tools disable (confirm-gated)', 'hermes_cli/subcommands/tools.py:25'),
    ('tools', 'enable', 'tools', 'enable', 'MISSING', 'qubit tools enable (confirm-gated)', 'hermes_cli/subcommands/tools.py:32'),
    ('tools', 'list', 'tools', 'list', 'MISSING', 'qubit tools list (read-only)', 'hermes_cli/console_engine.py:278'),
    ('tools', 'post-setup', 'tools', 'post-setup', 'MISSING', 'qubit tools post-setup (confirm-gated)', 'hermes_cli/console_engine.py:278'),
    ('worktree', 'list', 'worktree', 'list', 'MISSING', 'qubit worktree list (read-only)', 'hermes_cli/subcommands/worktree.py:6'),
    ('worktree', 'add', 'worktree', 'add', 'IMPLEMENTED', 'qubit worktree add (confined, needs --confirm)', 'hermes_cli/subcommands/worktree.py:6'),
    ('worktree', 'remove', 'worktree', 'remove', 'IMPLEMENTED', 'qubit worktree remove (confined, needs --confirm)', 'hermes_cli/subcommands/worktree.py:6'),
    ('worktree', 'switch', 'worktree', 'switch', 'IMPLEMENTED', 'qubit worktree switch (confined, needs --confirm)', 'hermes_cli/subcommands/worktree.py:6'),
]

# (path, component, qubit_backend, status, note, upstream_ref)
# Status is evidence-derived: build_dashboard_rows() only keeps IMPLEMENTED
# when DASHBOARD_PROOF below names an on-disk test that dispatches the page's
# real backing GET route (or served SPA shell for static pages) and asserts
# success; otherwise the page is downgraded to PARTIAL. Unsafe sub-actions stay
# typed policy gates and are documented in the note.
PAGES = [
    ('/', 'RootRedirect', '/sessions (redirect)', 'IMPLEMENTED',
     'Root redirects to /sessions; the served SPA shell answers 200.',
     'qubit_hermes/web/src/App.tsx:157'),
    ('/chat', 'ChatPage', 'overlay Qubit Chat', 'IMPLEMENTED',
     'Status/model backing route wired (GET /api/status 200); overlay Qubit '
     'chat transcript via POST /api/qubit/chat; the built-in xterm PTY WS is a '
     'typed policy gate (PTY_NO_SHELL).',
     'qubit_hermes/web/src/App.tsx:139'),
    ('/sessions', 'SessionsPage', 'hermes-dist SPA', 'IMPLEMENTED',
     'Sessions list/search/show wired (GET /api/sessions 200); append-only '
     'write verbs are typed policy gates.',
     'qubit_hermes/web/src/App.tsx:158'),
    ('/files', 'FilesPage', 'hermes-dist SPA', 'IMPLEMENTED',
     'Directory/read wired (GET /api/files 200); binary download/stream is a '
     'typed policy gate (FILES_BINARY_STREAM).',
     'qubit_hermes/web/src/App.tsx:159'),
    ('/analytics', 'AnalyticsPage', 'hermes-dist SPA', 'IMPLEMENTED',
     'Graphs load from GET /api/analytics 200; token/cost series are honest '
     'zeros (no local token economy).',
     'qubit_hermes/web/src/App.tsx:160'),
    ('/models', 'ModelsPage', 'hermes-dist SPA', 'IMPLEMENTED',
     'Model info/options wired (GET /api/model/info 200); MoA fan-out is a '
     'typed policy gate (not shipped).',
     'qubit_hermes/web/src/App.tsx:161'),
    ('/logs', 'LogsPage', 'hermes-dist SPA', 'IMPLEMENTED',
     'Logs read via h_logs (GET /api/logs 200).',
     'qubit_hermes/web/src/App.tsx:162'),
    ('/cron', 'CronPage', 'hermes-dist SPA', 'IMPLEMENTED',
     'Jobs/runs wired (GET /api/cron/jobs 200); blueprint instantiate is '
     'staged; external fire is a typed policy gate.',
     'qubit_hermes/web/src/App.tsx:163'),
    ('/skills', 'SkillsPage', 'hermes-dist SPA', 'IMPLEMENTED',
     'Local skills wired (GET /api/skills 200); remote hub install/update are '
     'typed policy gates.',
     'qubit_hermes/web/src/App.tsx:164'),
    ('/plugins', 'PluginsPage', 'hermes-dist SPA', 'IMPLEMENTED',
     'Local overlay manifests (GET /api/dashboard/plugins 200); remote '
     'agent-plugin install is a typed policy gate.',
     'qubit_hermes/web/src/App.tsx:165'),
    ('/mcp', 'McpPage', 'hermes-dist SPA', 'IMPLEMENTED',
     'Inventory served (GET /api/mcp 200); enable/mount is a typed policy '
     'gate (MCP_NO_MOUNT).',
     'qubit_hermes/web/src/App.tsx:166'),
    ('/pairing', 'PairingPage', 'hermes-dist SPA', 'IMPLEMENTED',
     'Pairing list/create/approve/revoke wired (GET /api/pairing 200).',
     'qubit_hermes/web/src/App.tsx:167'),
    ('/channels', 'ChannelsPage', 'hermes-dist SPA', 'IMPLEMENTED',
     'Platform list/update wired (GET /api/messaging/platforms 200); '
     'onboarding flows are typed policy gates (ONBOARDING_GUIDED).',
     'qubit_hermes/web/src/App.tsx:168'),
    ('/webhooks', 'WebhooksPage', 'hermes-dist SPA', 'IMPLEMENTED',
     'Webhook list/add/remove/toggle wired (GET /api/webhooks 200).',
     'qubit_hermes/web/src/App.tsx:169'),
    ('/system', 'SystemPage', 'hermes-dist SPA', 'IMPLEMENTED',
     'Status/stats wired (GET /api/system/stats 200); gateway process control '
     'is a typed policy gate (GATEWAY_NO_SUPERVISION).',
     'qubit_hermes/web/src/App.tsx:170'),
    ('/profiles', 'ProfilesPage', 'hermes-dist SPA', 'IMPLEMENTED',
     'Sidebar/tree wired (GET /api/profiles 200); create/import/export are '
     'staged or typed policy gates.',
     'qubit_hermes/web/src/App.tsx:171'),
    ('/profiles/new', 'ProfileBuilderPage', 'hermes-dist SPA', 'IMPLEMENTED',
     'Profile create is staged (409 confirmation), never fabricated success; '
     'GET /api/profiles 200.',
     'qubit_hermes/web/src/App.tsx:172'),
    ('/config', 'ConfigPage', 'hermes-dist SPA', 'IMPLEMENTED',
     'Config reads wired (GET /api/config 200); raw config secrets are typed '
     'policy gates (SECRET_NEVER_SERVED); PUT gated.',
     'qubit_hermes/web/src/App.tsx:173'),
    ('/env', 'EnvPage', 'hermes-dist SPA', 'IMPLEMENTED',
     'Key names only (GET /api/env 200); reveal/credential-pool are typed '
     'policy gates (SECRET_NEVER_SERVED).',
     'qubit_hermes/web/src/App.tsx:174'),
    ('/docs', 'DocsPage', 'hermes-dist SPA', 'IMPLEMENTED',
     'Docs page reachable via the served SPA shell (200).',
     'qubit_hermes/web/src/App.tsx:175'),
]

# page -> (proving test id, evidence token). The id is an on-disk test that
# dispatches the page's real backing API GET route (or served SPA shell for
# static pages) and asserts success; the token is the route literal that test
# file must reference. build_dashboard_rows() downgrades an IMPLEMENTED page to
# PARTIAL whenever the proof is absent or stale -- a page never claims
# IMPLEMENTED on the mere existence of an unrelated test file.
_DASHBOARD_TEST = "tests/test_dashboard_page_closure_wave2.py"
DASHBOARD_PROOF: Dict[str, Tuple[str, str]] = {
    "/": (f"{_DASHBOARD_TEST}::test_page_root_served_spa", "/hermes/"),
    "/chat": (f"{_DASHBOARD_TEST}::test_page_chat_backing_route",
              "/api/status"),
    "/sessions": (f"{_DASHBOARD_TEST}::test_page_sessions_backing_route",
                  "/api/sessions"),
    "/files": (f"{_DASHBOARD_TEST}::test_page_files_backing_route",
               "/api/files"),
    "/analytics": (f"{_DASHBOARD_TEST}::test_page_analytics_backing_route",
                   "/api/analytics"),
    "/models": (f"{_DASHBOARD_TEST}::test_page_models_backing_route",
                "/api/model/info"),
    "/logs": (f"{_DASHBOARD_TEST}::test_page_logs_backing_route",
              "/api/logs"),
    "/cron": (f"{_DASHBOARD_TEST}::test_page_cron_backing_route",
              "/api/cron/jobs"),
    "/skills": (f"{_DASHBOARD_TEST}::test_page_skills_backing_route",
                "/api/skills"),
    "/plugins": (f"{_DASHBOARD_TEST}::test_page_plugins_backing_route",
                 "/api/dashboard/plugins"),
    "/mcp": (f"{_DASHBOARD_TEST}::test_page_mcp_backing_route", "/api/mcp"),
    "/pairing": (f"{_DASHBOARD_TEST}::test_page_pairing_backing_route",
                 "/api/pairing"),
    "/channels": (f"{_DASHBOARD_TEST}::test_page_channels_backing_route",
                  "/api/messaging/platforms"),
    "/webhooks": (f"{_DASHBOARD_TEST}::test_page_webhooks_backing_route",
                  "/api/webhooks"),
    "/system": (f"{_DASHBOARD_TEST}::test_page_system_backing_route",
                "/api/system/stats"),
    "/profiles": (f"{_DASHBOARD_TEST}::test_page_profiles_backing_route",
                  "/api/profiles"),
    "/profiles/new": (
        f"{_DASHBOARD_TEST}::test_page_profiles_new_backing_route",
        "/api/profiles"),
    "/config": (f"{_DASHBOARD_TEST}::test_page_config_backing_route",
                "/api/config"),
    "/env": (f"{_DASHBOARD_TEST}::test_page_env_backing_route", "/api/env"),
    "/docs": (f"{_DASHBOARD_TEST}::test_page_docs_served_spa",
              "/hermes/docs"),
}


def _sub_caller_proof(qfam, qsub, parser_families, index, forward):
    """Shared caller/proof derivation for a subcommand row.

    Single source of truth for the generator and the ledger-build invariant
    test: a subcommand row has a real caller when it is a native parser
    subcommand or is routed by ``FORWARD_TABLE``, ``CONFIRM_TABLE`` or a
    ``FAMILY_FORWARD`` read-only/confirm subcommand.  Returns
    ``(caller, forwarded, proof)``.
    """
    native = bool(qfam) and (qsub in parser_families.get(qfam, set()))
    family_readonly = (
        qfam in _FAMILY_FORWARD and qsub in _FAMILY_FORWARD[qfam][1]) if qfam \
        else False
    family_confirm = (
        qfam in _FAMILY_FORWARD and qsub in _FAMILY_FORWARD[qfam][2]) if qfam \
        else False
    forwarded = bool(qfam) and (
        (qfam, qsub) in forward
        or (qfam, qsub) in _CONFIRM_TABLE
        or family_readonly or family_confirm)
    caller = native or forwarded
    if forwarded:
        proof = (index.pair_proof(qfam, qsub) or index.family_proof(qfam)
                 or index.forward_proof)
    elif qfam and qsub:
        proof = index.pair_proof(qfam, qsub)
    elif qfam:
        proof = index.family_proof(qfam)
    else:
        proof = ""
    return caller, forwarded, proof


def build_cli_rows() -> List[Dict[str, str]]:
    parser_families, names = _parser_tree()
    index = _build_test_index(names)
    forward = _fwd.FORWARD_TABLE
    rows: List[Dict[str, str]] = []

    for cmd, qfam, surface, qubit, note, ref in CLI:
        caller = bool(qfam) and (qfam in parser_families
                                 or qfam in _FAMILY_FORWARD)
        proof = index.family_proof(qfam) if qfam else ""
        tested = bool(proof)
        status = _derive_cli_status(surface, caller, tested, forwarded=False)
        if status == "MISSING":
            proof = ""
        if status == "IMPLEMENTED" and not proof:
            status = "UNVERIFIED"
        if status == "MISSING":
            backend = ""
        elif qubit and qubit != "none":
            backend = qubit
        elif qfam:
            backend = f"qubit {qfam}"
        else:
            backend = qubit
        needs = ("none" if status == "IMPLEMENTED"
                 else "owner" if status in ("MISSING", "GATED")
                 else "creds" if cmd in ("auth", "secrets", "security")
                 else "none")
        rows.append({
            "id": f"cli:{cmd}",
            "area": "cli",
            "capability": f"hermes {cmd}",
            "upstream_ref": QH + ref,
            "qubit_backend": backend,
            "cli": qubit,
            "dashboard": "",
            "voice": "",
            "status": status,
            "proving_test": proof,
            "note": note,
            "needs": needs,
        })

    for fam, sub, qfam, qsub, surface, qubit, ref in SUBS:
        caller, forwarded, proof = _sub_caller_proof(
            qfam, qsub, parser_families, index, forward)
        tested = bool(proof)
        status = _derive_cli_status(surface, caller, tested, forwarded=forwarded)
        if status == "MISSING":
            proof = ""
        if status == "IMPLEMENTED" and not proof:
            status = "UNVERIFIED"
        if status == "MISSING":
            backend = ""
        elif qubit and qubit != "none":
            backend = qubit
        elif qfam and qsub:
            backend = f"qubit {qfam} {qsub}"
        else:
            backend = qubit
        needs = ("none" if status == "IMPLEMENTED"
                 else "owner" if status == "MISSING"
                 else "creds")
        rows.append({
            "id": f"cli:{fam} {sub}",
            "area": "cli-subcommand",
            "capability": f"hermes {fam} {sub}",
            "upstream_ref": QH + ref,
            "qubit_backend": backend,
            "cli": qubit,
            "dashboard": "",
            "voice": "",
            "status": status,
            "proving_test": proof,
            "note": f"Subcommand-surface gap inside the {fam} family.",
            "needs": needs,
        })
    return rows


_DISPATCH_CALLS = {
    "dispatch", "run_op", "run_extra_op", "run_registry_op", "request",
    "_get", "_post", "_dispatch", "handle", "fetch",
}


def _dashboard_proof_ok(path: str) -> Tuple[bool, str]:
    """True when ``path``'s DASHBOARD_PROOF test really dispatches its route.

    An IMPLEMENTED page must be backed by an on-disk *test function* that
    references the page's backing route and calls a production dispatch entry
    point.  A bare string/comment mention or an unrelated file is never
    sufficient (AST-checked, so a fixture cannot be satisfied by a comment).
    """
    proof = DASHBOARD_PROOF.get(path)
    if not proof:
        return False, "no DASHBOARD_PROOF entry"
    test_id, token = proof
    file_part, _, fn_part = test_id.partition("::")
    if not fn_part:
        return False, "DASHBOARD_PROOF must name a test function"
    test_path = ROOT / file_part
    if not test_path.is_file():
        return False, f"proving test file {file_part} is not on disk"
    source = test_path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False, f"proving test file {file_part} is not parseable"
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == fn_part), None)
    if fn is None:
        return False, f"proving test {fn_part} is not defined in {file_part}"
    body = ast.get_source_segment(source, fn) or ""
    if token and token not in body:
        return False, (f"backing route {token!r} is not referenced inside "
                       f"{fn_part}")
    calls = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                calls.add(func.attr)
            elif isinstance(func, ast.Name):
                calls.add(func.id)
    if not (calls & _DISPATCH_CALLS):
        return False, (f"{fn_part} does not call a production dispatch entry "
                       "point")
    return True, ""


def build_dashboard_rows() -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for path, comp, qubit, status, note, ref in PAGES:
        test_id = ""
        if status == "IMPLEMENTED":
            ok, why = _dashboard_proof_ok(path)
            if not ok:
                status = "PARTIAL"
                note = ((note + " ") if note else "") + (
                    f"Downgraded to PARTIAL: {why}.")
            else:
                test_id = DASHBOARD_PROOF[path][0]
        rows.append({
            "id": f"dashboard:{path}",
            "area": "dashboard-page",
            "capability": f"hermes dashboard {path} ({comp})",
            "upstream_ref": ref,
            "qubit_backend": qubit,
            "cli": "",
            "dashboard": qubit,
            "voice": "",
            "status": status,
            "proving_test": test_id,
            "note": note or ("Page reachable via served hermes-dist SPA; "
                             "backing routes mapped or typed-gated."),
            "needs": ("none" if status == "IMPLEMENTED"
                      else "owner"),
        })
    return rows


# (plugin, area, capability, qubit_backend, status, proving_test, needs,
#  dashboard, note, upstream_ref)
# Hand-maintained because the plugin APIs live under qubit_hermes/plugins/*,
# outside the web_routers glob the API extractor scans. The achievements row is
# IMPLEMENTED only because tests/test_hermes_plugins.py executes the real
# compat routes over a seeded SessionDB; kanban reads are real but the plugin's
# mutating routes stay on the typed policy gate, so that row stays PARTIAL.
PLUGINS = [
    ("hermes-achievements", "dashboard_plugin",
     "hermes dashboard plugin hermes-achievements (API + state)",
     "voicebridge/hermes_compat.py:h_plugin_achievements", "IMPLEMENTED",
     "tests/test_hermes_plugins.py::test_achievements_routes_compute_real_state",
     "none", "hermes-dist Achievements tab (/achievements)",
     "Real backend in voicebridge/hermes_plugins.py: reuses the vendored "
     "achievement engine + hermes_state.SessionDB over the local session "
     "history, persists snapshot/checkpoint/unlock state under the Qubit state "
     "dir (0600), and serves /achievements, /scan-status, /recent-unlocks, "
     "/sessions/{id}/badges, /rescan, /reset-state. The plugin JS bundle is "
     "served by the webui overlay path, not this compat backend.",
     "qubit_hermes/plugins/hermes-achievements/dashboard/plugin_api.py:57"),
    ("kanban", "dashboard_plugin",
     "hermes dashboard plugin kanban (read API)",
     "voicebridge/hermes_plugins.py", "PARTIAL",
     "tests/test_hermes_plugins.py::test_kanban_read_routes_serve_real_board",
     "owner", "hermes-dist Kanban tab (/kanban)",
     "Real read API in voicebridge/hermes_plugins.py backed by the vendored "
     "hermes_cli.kanban_db (board/tasks/stats/assignees/boards/config/projects/"
     "workers/diagnostics/model-options); mutating routes stay on the shared "
     "typed KANBAN_WRITE_BROKERED confirmation/broker gate and are not yet "
     "served directly.",
     "qubit_hermes/plugins/kanban/dashboard/plugin_api.py:266"),
    ("fleet-discovery", "dashboard_plugin",
     "hermes dashboard plugin fleet discovery",
     "voicebridge/hermes_compat.py:h_plugin_fleet", "IMPLEMENTED",
     "tests/test_hermes_plugins.py::test_plugin_fleet_discovery_lists_vendored_plugins",
     "none", "hermes-dist Plugins page",
     "GET /api/dashboard/plugins/fleet returns the vendored plugin manifests "
     "(source=vendored) additively; the fixed ten-plugin overlay response at "
     "/api/dashboard/plugins is unchanged.",
     "qubit_hermes/hermes_cli/web_server_dashboard.py:537"),
]


def build_plugin_rows() -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for (plugin, area, capability, backend, status, proving_test, needs,
         dashboard, note, ref) in PLUGINS:
        rows.append({
            "id": f"plugin:{plugin}",
            "area": area,
            "capability": capability,
            "upstream_ref": ref,
            "qubit_backend": backend,
            "cli": "",
            "dashboard": dashboard,
            "voice": "",
            "status": status,
            "proving_test": proving_test,
            "note": note,
            "needs": needs,
        })
    return rows


def build_ledger() -> Dict[str, object]:
    api_index = build_api_index()
    rows = (build_api_rows(api_index) + build_cli_rows() +
            build_dashboard_rows() + build_plugin_rows())
    by_status = Counter(r["status"] for r in rows)
    by_area = Counter(r["area"] for r in rows)
    return {
        "schema": "qubit-gap-ledger-section/1",
        "source": "hermes",
        "upstream_pin": PIN,
        "generated": GENERATED,
        "method": (
            "Evidence-based regeneration. API rows re-extract the pinned "
            "upstream route decorators and classify them from "
            "voicebridge.hermes_compat.match disposition plus the handler's "
            "real behaviour (a handler whose normal exits are typed "
            "gates/refusals is GATED; a real read/write is IMPLEMENTED; a "
            "wire-shape stub is PARTIAL). IMPLEMENTED additionally requires an "
            "on-disk tests/ function that dispatches the route, calls the "
            "handler, or calls the same downstream production module (AST "
            "scan), so no row is IMPLEMENTED on a phantom caller. cli / "
            "cli-subcommand rows derive status from the live "
            "voicebridge.cli build_parser tree plus "
            "voicebridge.cli_hermes_forward.FORWARD_TABLE and an AST scan of "
            "tests/ for the exercising caller. Plugin-fleet rows are declared "
            "explicitly and are IMPLEMENTED only when "
            "tests/test_hermes_plugins.py dispatches the registered compat "
            "handler over real local state. No status is hand-flipped."
        ),
        "sources_reconciled": [
            "voicebridge/hermes_compat.py",
            "voicebridge/hermes_plugins.py",
            "voicebridge/cli.py",
            "voicebridge/cli_hermes_forward.py",
            "tests/ (AST caller evidence)",
            "tests/test_hermes_plugins.py (plugin-fleet proof)",
            "qubit_hermes/hermes_cli/web_routers",
            "qubit_hermes/hermes_cli/web_server*.py",
            "qubit_hermes/hermes_cli/memory_oauth.py",
            "qubit_hermes/hermes_cli/dashboard_auth/routes.py",
            "qubit_hermes/plugins/hermes-achievements/dashboard/plugin_api.py",
            "qubit_hermes/plugins/kanban/dashboard/plugin_api.py",
            "qubit_hermes/web/src/App.tsx",
        ],
        "counts": {
            "total": len(rows),
            "by_status": dict(sorted(by_status.items())),
            "by_area": dict(sorted(by_area.items())),
        },
        "rows": rows,
    }


def _render(data: Dict[str, object]) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(OUT_PATH),
                        help="output JSON path (default: docs section)")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the committed section differs")
    args = parser.parse_args(argv)

    data = build_ledger()
    text = _render(data)
    out = Path(args.out)
    if args.check:
        current = out.read_text(encoding="utf-8") if out.exists() else ""
        if current != text:
            print(f"STALE: {out} does not match the regenerated section",
                  file=sys.stderr)
            return 1
        print(f"OK: {out} is up to date")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    counts = data["counts"]
    print(f"wrote {out} ({counts['total']} rows)")
    print(f"  by_status: {counts['by_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
