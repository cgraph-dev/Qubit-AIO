"""Behavioral E2E auditor for the live Qubit dashboard routes.

This is a **behavioral** auditor, not a source/route inventory. For every
``PORTED`` route enumerated in ``docs/generated/parity_inventory.json`` it makes
a real authenticated HTTP request against the running dashboard and records what
the backend actually did (HTTP status, the typed code/refusal, whether the body
is real data or a policy gate). A representative SAFE subset of mutations is
exercised through the real confirm-gated flow (stage 409 -> confirm -> verify
state -> cleanup) with temp artifacts that are always removed.

Classification (per route/mutation):

* ``E2E_PASS``  -- real backend behavior (2xx real payload, or a domain-valid
  typed response that is not a policy gate and not an internal error).
* ``E2E_GATED`` -- a typed policy refusal with a working counterpart (the
  ``qubit_counterpart`` / ``owner=qubit-policy`` envelope).
* ``E2E_FAIL``  -- 5xx, ``NO_HANDLER``/unhandled-exception, an auth failure, or
  a mutation whose round-trip did not restore state.
* ``E2E_SKIP``  -- needs operator/hardware/external input that cannot be safely
  supplied, or a path/query parameter that could not be resolved.

Non-negotiables enforced in code:

* the target must be loopback and must never be the reference ``:9119``;
* ``~/.hermes`` is never read or written (HTTP only, to the live Qubit server);
* the emergency stop is never latched; no user data is deleted (only the
  auditor's own ``e2e-audit-*`` temp artifacts, always cleaned up);
* the reference dashboard and reference home are never touched.

Run::

    python scripts/route_e2e_audit.py                 # full audit
    python scripts/route_e2e_audit.py --read-only     # reads only, no mutations
    python scripts/route_e2e_audit.py --check         # determinism check

The artifact is written to ``docs/generated/QUBIT_ROUTE_E2E_2026-09-23.json``.
``docs/archive/`` is historical-only and is never an input or output here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]
INVENTORY = REPO / "docs/generated/parity_inventory.json"
OUT = REPO / "docs/generated/QUBIT_ROUTE_E2E_2026-09-23.json"

DEFAULT_BASE = "http://127.0.0.1:8890"
API_PREFIX = "/agent"
FORBIDDEN_HOSTS = {"127.0.0.1:9119", "localhost:9119", "[::1]:9119"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}

TEMP_PREFIX = "e2e-audit-"

# Path-parameter defaults, applied in template order. Context-specific ``{name}``
# is handled by ``_name_for``.
_PATH_PARAMS: List[Tuple[str, str]] = [
    ("{job_id}", "1"),
    ("{session_id}", "1"),
    ("{model_id}", "x1"),
    ("{plugin_name}", "kanban"),
    ("{file_path:path}", "index.js"),
    ("{endpoint_id}", "x1"),
    ("{provider_id}", "nous"),
    ("{flow_id}", "x1"),
    ("{server_name:path}", "x1"),
    ("{pairing_id}", "x1"),
]

# Query defaults for routes whose inputs are not derivable from a list endpoint.
_QUERY_DEFAULTS: Dict[str, Dict[str, str]] = {
    "/api/git/status": {"path": str(REPO)},
    "/api/git/branches": {"path": str(REPO)},
    "/api/git/base-branches": {"path": str(REPO)},
    "/api/git/worktrees": {"path": str(REPO)},
    "/api/git/review/list": {"path": str(REPO)},
    "/api/git/review/diff": {"path": str(REPO), "file": "AGENTS.md"},
    "/api/git/review/commit-context": {"path": str(REPO)},
    "/api/git/review/rev-parse": {"path": str(REPO)},
    "/api/git/file-diff": {"path": str(REPO), "file": "AGENTS.md"},
    "/api/learning/node": {"id": "qubit-voice-mode"},
    "/api/skills/content": {"name": "qubit-voice-mode"},
    "/api/skills/hub/preview": {"identifier": "qubit-boards"},
    "/api/skills/hub/scan": {"identifier": "qubit-boards"},
    "/api/files/read": {"path": "AGENTS.md"},
    "/api/fs/read-text": {"path": str(REPO / "AGENTS.md")},
    "/api/fs/read-data-url": {"path": str(REPO / "AGENTS.md")},
    "/api/local-models/search": {"q": "qwen"},
    "/api/local-models/search/files": {"repo": "org/repo"},
}

# Policy codes that are legitimate typed refusals carrying a counterpart.
_GATE_CODES = {
    "CONFIRM_REQUIRED", "EMERGENCY_STOP", "GATEWAY_ESTOP",
    "GATEWAY_CONFIRM_REQUIRED", "GW_CONFIRM_REQUIRED", "GW_ESTOP",
}
_INTERNAL_CODES = {"NO_HANDLER", "INTERNAL_ERROR"}


# ---------------------------------------------------------------------------
# Target safety
# ---------------------------------------------------------------------------
class TargetRefused(RuntimeError):
    """The auditor refuses to run against a non-loopback / reference target."""


def validate_target(base: str) -> str:
    """Return a normalized loopback base or raise ``TargetRefused``."""
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme not in ("http", "https"):
        raise TargetRefused(f"unsupported scheme {parsed.scheme!r}")
    netloc = parsed.netloc
    if netloc in FORBIDDEN_HOSTS:
        raise TargetRefused(
            f"refusing to target the reference dashboard {netloc!r}")
    host = parsed.hostname or ""
    if host not in LOOPBACK_HOSTS:
        raise TargetRefused(
            f"refusing non-loopback target {host!r}; the auditor is "
            "loopback-only and never touches the reference home or :9119")
    if parsed.port == 9119:
        raise TargetRefused("refusing port 9119 (the reference dashboard)")
    return base.rstrip("/")


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable)
# ---------------------------------------------------------------------------
def _name_for(template: str) -> str:
    if "/api/actions/{name}" in template:
        return "gateway-restart"
    if "/api/memory/providers/{name}" in template:
        return "qubit-local"
    if "/api/tools/toolsets/{name}" in template:
        return "web"
    return "default"


def resolve_path(path_template: str) -> str:
    """Substitute every path parameter with a safe representative value."""
    path = path_template
    if "{name}" in path:
        path = path.replace("{name}", _name_for(path_template))
    for key, value in _PATH_PARAMS:
        path = path.replace(key, value)
    return path


def resolve_query(route_id: str) -> Dict[str, str]:
    """Static query defaults for a reference route id (``METHOD /path``)."""
    _, _, path = route_id.partition(" ")
    return dict(_QUERY_DEFAULTS.get(path, {}))


def _is_gate(body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    # A successful response may carry ``qubit_counterpart`` as an informational
    # pointer to the equivalent CLI/voice surface; that is not a refusal.
    if body.get("ok") is True:
        return False
    if body.get("qubit_counterpart"):
        return True
    if body.get("owner") == "qubit-policy" and str(
            body.get("state", "")).upper() in ("UNAVAILABLE", "FAILED"):
        return True
    return str(body.get("code", "")) in _GATE_CODES


def classify(http: int, body: Any, resolved: bool) -> str:
    """Classify a single response. Pure; the unit tests pin every branch."""
    if http < 0:
        return "E2E_FAIL"
    if http in (401, 403) and not _is_gate(body):
        return "E2E_FAIL"
    if http >= 500:
        return "E2E_FAIL"
    if _is_gate(body):
        return "E2E_GATED"
    code = str(body.get("code", "")) if isinstance(body, dict) else ""
    if code in _INTERNAL_CODES:
        return "E2E_FAIL"
    if 200 <= http < 300:
        if isinstance(body, dict) and body.get("ok") is False:
            return "E2E_FAIL"
        return "E2E_PASS"
    # 4xx that is not a policy gate.
    if http in (400, 404, 409, 422):
        if not resolved:
            return "E2E_SKIP"
        return "E2E_FAIL"
    return "E2E_FAIL"


def stable_digest(doc: Dict[str, Any]) -> str:
    """Deterministic digest over classifications only (volatile-free)."""
    material = {
        "reads": sorted(
            [row["id"], row["classification"]] for row in doc["read_routes"]),
        "mutations": sorted(
            [row["name"], row["classification"]] for row in doc["mutations"]),
    }
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _json_or_text(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _request(base: str, token: str, method: str, path: str,
             query: Optional[Dict[str, str]] = None,
             body: Optional[Dict[str, Any]] = None,
             timeout: float = 20.0) -> Tuple[int, Any]:
    url = base + API_PREFIX + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = None
    headers = {"X-Hermes-Session-Token": token}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _json_or_text(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _json_or_text(exc.read())
    except Exception as exc:  # noqa: BLE001 — recorded, never raised
        return -1, {"code": "TRANSPORT", "detail": str(exc)[:200]}


def scrape_token(base: str, timeout: float = 10.0) -> str:
    """Scrape the loopback session token from the served SPA shell."""
    html = ""
    try:
        with urllib.request.urlopen(base + "/chat", timeout=timeout) as resp:
            html = resp.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        pass
    match = re.search(r'window\.__HERMES_SESSION_TOKEN__="([^"]+)"', html)
    if not match:
        raise RuntimeError(
            f"no session token in {base}/chat (is the dashboard running?)")
    return match.group(1)


# ---------------------------------------------------------------------------
# Live state resolution (behavioral; drives state_dependency)
# ---------------------------------------------------------------------------
def _live_ids(base: str, token: str, timeout: float) -> Dict[str, Any]:
    ids: Dict[str, Any] = {}
    http, body = _request(base, token, "GET", "/api/cron/jobs", timeout=timeout)
    if http == 200 and isinstance(body, list) and body:
        ids["job_id"] = str(body[0].get("id"))
    http, body = _request(base, token, "GET", "/api/sessions", timeout=timeout)
    if http == 200 and isinstance(body, dict):
        sessions = body.get("sessions") or []
        if sessions:
            ids["session_id"] = str(sessions[0].get("id"))
            sid = ids["session_id"]
            h2, tl = _request(base, token, "GET",
                              f"/api/sessions/{sid}/timeline",
                              timeout=timeout)
            if h2 == 200 and isinstance(tl, dict):
                entries = tl.get("entries") or []
                if entries:
                    ids["row_id"] = str(entries[0].get("row_id"))
    return ids


def _apply_live_ids(path: str, ids: Dict[str, Any]) -> Tuple[str, bool]:
    """Replace live-resolved placeholders; report whether state was used."""
    used = False
    if "{job_id}" in path and "job_id" in ids:
        path = path.replace("{job_id}", str(ids["job_id"]))
        used = True
    if "{session_id}" in path and "session_id" in ids:
        path = path.replace("{session_id}", str(ids["session_id"]))
        used = True
    return path, used


# ---------------------------------------------------------------------------
# Read audit
# ---------------------------------------------------------------------------
def _ported_routes(inventory: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [r for r in inventory["rows"]
            if r.get("surface") == "route"
            and r.get("classification") == "PORTED"]


def audit_reads(base: str, token: str, inventory: Dict[str, Any],
                timeout: float) -> List[Dict[str, Any]]:
    ids = _live_ids(base, token, timeout)
    rows: List[Dict[str, Any]] = []
    for route in _ported_routes(inventory):
        if route.get("method") != "GET":
            continue
        route_id = str(route["id"])
        template = route_id.partition(" ")[2]
        path_live, state_used = _apply_live_ids(template, ids)
        path = resolve_path(path_live)
        query = resolve_query(route_id)
        if "{session_id}" in route_id and "messages/around" in route_id:
            query = dict(query)
            if "row_id" in ids:
                query["row_id"] = str(ids["row_id"])
                state_used = True
        resolved = ("{" not in path) and ("{" not in str(query))
        http, body = _request(base, token, "GET", path, query=query,
                              timeout=timeout)
        code = str(body.get("code", "")) if isinstance(body, dict) else ""
        klass = classify(http, body, resolved)
        row: Dict[str, Any] = {
            "id": route_id,
            "method": "GET",
            "live_path": API_PREFIX + path,
            "http": http,
            "classification": klass,
            "code": code,
            "state_dependency": state_used,
            "resolved_params": bool(query) or state_used,
        }
        if klass != "E2E_PASS":
            row["counterpart"] = (body.get("qubit_counterpart")
                                  if isinstance(body, dict) else None)
            row["detail"] = (str(body.get("detail", ""))[:200]
                             if isinstance(body, dict) else "")
            if body is None:
                row["detail"] = "non-JSON response"
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Mutation audit (safe subset, confirm-gated, temp artifacts, cleanup)
# ---------------------------------------------------------------------------
def _mcp_spec() -> Dict[str, Any]:
    name = TEMP_PREFIX + "mcp"
    base = {"name": name, "transport": "stdio", "command": "/bin/echo"}
    return {
        "name": "mcp_server_create_delete",
        "stage": ("POST", "/api/mcp/servers", base),
        "confirm": ("POST", "/api/mcp/servers", {**base, "confirm": True}),
        "verify": ("GET", "/api/mcp/servers", None),
        "verify_ok": lambda b: name in [s.get("name")
                                        for s in (b or {}).get("servers", [])],
        "cleanup": [("DELETE", f"/api/mcp/servers/{name}", None),
                    ("DELETE", f"/api/mcp/servers/{name}", {"confirm": True})],
        "restored_ok": lambda b: name not in [s.get("name")
                                              for s in (b or {}).get("servers", [])],
    }


def _cron_spec() -> Dict[str, Any]:
    name = TEMP_PREFIX + "cron"
    base = {"name": name, "prompt": "e2e noop", "when": "every 30m"}
    return {
        "name": "cron_create_delete",
        "stage": ("POST", "/api/cron/jobs", base),
        "confirm": ("POST", "/api/cron/jobs", {**base, "confirm": True}),
        "verify": ("GET", "/api/cron/jobs", None),
        "verify_ok": lambda b: name in [j.get("name") for j in (b or [])],
        "cleanup_by_name": ("GET", "/api/cron/jobs", None,
                            lambda b: next(
                                (str(j.get("id")) for j in (b or [])
                                 if j.get("name") == name), None),
                            "/api/cron/jobs/{id}"),
        "restored_ok": lambda b: name not in [j.get("name") for j in (b or [])],
    }


def _webhook_spec() -> Dict[str, Any]:
    name = TEMP_PREFIX + "wh"
    base = {"name": name, "url": "http://127.0.0.1:9/e2e",
            "events": ["job.completed"], "secret_ref": "env:E2E_AUDIT"}
    return {
        "name": "webhook_create_delete",
        "stage": ("POST", "/api/webhooks", base),
        "confirm": ("POST", "/api/webhooks", {**base, "confirm": True}),
        "verify": ("GET", "/api/webhooks", None),
        "verify_ok": lambda b: name in [
            s.get("name") for s in (b or {}).get("subscriptions", [])],
        "cleanup": [("DELETE", f"/api/webhooks/{name}", None),
                    ("DELETE", f"/api/webhooks/{name}", {"confirm": True})],
        "restored_ok": lambda b: name not in [
            s.get("name") for s in (b or {}).get("subscriptions", [])],
    }


def _hook_spec() -> Dict[str, Any]:
    name = TEMP_PREFIX + "hook"
    event = "e2e.audit"
    base = {"name": name, "event": event, "command": "/bin/true"}
    return {
        "name": "hook_create_delete",
        "stage": ("POST", "/api/ops/hooks", base),
        "confirm": ("POST", "/api/ops/hooks", {**base, "confirm": True}),
        "verify": ("GET", "/api/ops/hooks", None),
        "verify_ok": lambda b: event in [h.get("event")
                                         for h in (b or {}).get("hooks", [])],
        "cleanup": [("DELETE", "/api/ops/hooks", {"name": name}),
                    ("DELETE", "/api/ops/hooks",
                     {"name": name, "confirm": True})],
        "restored_ok": lambda b: event not in [h.get("event")
                                               for h in (b or {}).get("hooks", [])],
    }


def _profile_spec() -> Dict[str, Any]:
    name = TEMP_PREFIX + "prof"
    base = {"name": name, "no_skills": True}
    return {
        "name": "profile_create_delete",
        "stage": ("POST", "/api/profiles", base),
        "confirm": ("POST", "/api/profiles", {**base, "confirm": True}),
        "verify": ("GET", "/api/profiles", None),
        "verify_ok": lambda b: name in [p.get("name")
                                        for p in (b or {}).get("profiles", [])],
        "cleanup": [("DELETE", f"/api/profiles/{name}", None),
                    ("DELETE", f"/api/profiles/{name}", {"confirm": True})],
        "restored_ok": lambda b: name not in [p.get("name")
                                              for p in (b or {}).get("profiles", [])],
    }


def _custom_endpoint_spec() -> Dict[str, Any]:
    ident = TEMP_PREFIX + "ep"
    base = {"id": ident, "name": "e2e", "base_url": "http://127.0.0.1:9"}
    return {
        "name": "custom_endpoint_write_delete",
        "stage": ("POST", "/api/providers/custom-endpoints", base),
        "confirm": ("POST", "/api/providers/custom-endpoints",
                    {**base, "confirm": True}),
        "verify": ("GET", "/api/providers/custom-endpoints", None),
        "verify_ok": lambda b: ident in [e.get("id")
                                         for e in (b or {}).get("endpoints", [])],
        "cleanup": [("DELETE", f"/api/providers/custom-endpoints/{ident}", None),
                    ("DELETE", f"/api/providers/custom-endpoints/{ident}",
                     {"confirm": True})],
        "restored_ok": lambda b: ident not in [e.get("id")
                                               for e in (b or {}).get("endpoints", [])],
    }


def mutation_specs() -> List[Dict[str, Any]]:
    return [_mcp_spec(), _cron_spec(), _webhook_spec(), _hook_spec(),
            _profile_spec(), _custom_endpoint_spec()]


def _pre_clean(base: str, token: str, spec: Dict[str, Any],
               timeout: float) -> None:
    """Idempotently remove a prior run's temp artifact."""
    if "cleanup" in spec:
        for method, path, body in spec["cleanup"]:
            _request(base, token, method, path, body=body, timeout=timeout)
        return
    method, path, _body, find_id, id_path = spec["cleanup_by_name"]
    http, listing = _request(base, token, method, path, timeout=timeout)
    if http == 200:
        ident = find_id(listing)
        if ident:
            real = id_path.replace("{id}", ident)
            _request(base, token, "DELETE", real, timeout=timeout)
            _request(base, token, "DELETE", real, body={"confirm": True},
                     timeout=timeout)


def _read_authority_note(path: str) -> Optional[str]:
    """Explain a write/read split when the read route is fork-proxied.

    The served dashboard proxies non-gated GET/HEAD reads to the vendored
    runtime unless the path is in ``qubit_read_authority``. When a compat
    mutation writes a Qubit store but its verify read is proxied to the fork,
    the two halves read different stores -- a real split-brain.
    """
    try:
        from voicebridge.webui import qubit_read_authority
    except Exception:  # noqa: BLE001 — the auditor stays usable without it
        return None
    try:
        if not qubit_read_authority(path):
            return ("read route is fork-proxied (not in qubit_read_authority); "
                    "the compat write and the proxied read use different stores")
    except Exception:  # noqa: BLE001
        return None
    return None


def audit_mutations(base: str, token: str, timeout: float) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for spec in mutation_specs():
        _pre_clean(base, token, spec, timeout)
        row: Dict[str, Any] = {"name": spec["name"], "steps": {}}
        m, p, b = spec["stage"]
        stage_http, stage_body = _request(base, token, m, p, body=b,
                                          timeout=timeout)
        row["steps"]["stage"] = {
            "http": stage_http,
            "code": (stage_body or {}).get("code") if isinstance(stage_body, dict) else None,
        }
        m, p, b = spec["confirm"]
        confirm_http, confirm_body = _request(base, token, m, p, body=b,
                                              timeout=timeout)
        row["steps"]["confirm"] = {"http": confirm_http}
        m, p, _ = spec["verify"]
        verify_http, verify_body = _request(base, token, m, p, timeout=timeout)
        verify_ok = bool(verify_http == 200 and spec["verify_ok"](verify_body))
        row["steps"]["verify"] = {"http": verify_http, "ok": verify_ok}

        if "cleanup" in spec:
            for cm, cp, cb in spec["cleanup"]:
                _request(base, token, cm, cp, body=cb, timeout=timeout)
        else:
            cm, cp, _cb, find_id, id_path = spec["cleanup_by_name"]
            _h, listing = _request(base, token, cm, cp, timeout=timeout)
            ident = find_id(listing) if _h == 200 else None
            if ident:
                real = id_path.replace("{id}", ident)
                _request(base, token, "DELETE", real, timeout=timeout)
                _request(base, token, "DELETE", real, body={"confirm": True},
                         timeout=timeout)
        m, p, _ = spec["verify"]
        restored_http, restored_body = _request(base, token, m, p,
                                                timeout=timeout)
        restored_ok = bool(restored_http == 200
                           and spec["restored_ok"](restored_body))
        row["steps"]["restored"] = {"http": restored_http, "ok": restored_ok}

        stage_gate = bool(stage_http == 409
                          and isinstance(stage_body, dict)
                          and str(stage_body.get("code", "")) in _GATE_CODES)
        confirm_ok = 200 <= confirm_http < 300
        if not stage_gate:
            row["classification"] = "E2E_FAIL"
            row["detail"] = (f"stage expected 409 typed confirmation, got "
                             f"{stage_http}")
        elif not confirm_ok:
            row["classification"] = "E2E_FAIL"
            row["detail"] = f"confirm did not execute (http {confirm_http})"
        elif not verify_ok:
            row["classification"] = "E2E_FAIL"
            note = _read_authority_note(spec["verify"][1])
            row["detail"] = ("confirmed write not visible in the read route "
                             "(write/read store mismatch or stale service)")
            if note:
                row["root_cause"] = note
        elif not restored_ok:
            row["classification"] = "E2E_FAIL"
            row["detail"] = "cleanup did not restore the pre-run state"
        else:
            row["classification"] = "E2E_PASS"
            row["detail"] = "stage 409 -> confirm -> verified -> restored"
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Staleness (informational; the auditor never restarts anything)
# ---------------------------------------------------------------------------
def _service_staleness() -> Dict[str, Any]:
    info: Dict[str, Any] = {"service_started_at": None, "newest_product_mtime": None,
                            "stale": None, "note": ""}
    try:
        started = subprocess.check_output(
            ["systemctl", "--user", "show", "-p", "ActiveEnterTimestamp",
             "--value", "qubit-voicebridge"],
            stderr=subprocess.DEVNULL, timeout=5).decode().strip()
        info["service_started_at"] = started or None
    except Exception:  # noqa: BLE001
        return info
    newest = 0.0
    for root in (REPO / "voicebridge", REPO / "qubit_runtime" / "qubit_cli"):
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    if newest:
        info["newest_product_mtime"] = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(newest))
    if info["service_started_at"] and newest:
        match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})",
                          info["service_started_at"])
        if match:
            try:
                started_epoch = time.mktime(time.strptime(
                    match.group(1), "%Y-%m-%d %H:%M:%S"))
                info["stale"] = newest > started_epoch + 1
            except ValueError:
                info["stale"] = None
    if info["stale"]:
        info["note"] = ("the running dashboard predates on-disk product "
                        "changes; some live results may reflect the process "
                        "that is serving rather than the current checkout "
                        "(informational; it does not by itself explain any "
                        "specific failure)")
    return info


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def run_audit(base: str = DEFAULT_BASE, read_only: bool = False,
              timeout: float = 20.0) -> Dict[str, Any]:
    base = validate_target(base)
    token = scrape_token(base)
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    reads = audit_reads(base, token, inventory, timeout)
    mutations = [] if read_only else audit_mutations(base, token, timeout)
    doc: Dict[str, Any] = {
        "schema": "qubit-route-e2e/1",
        "generated_by": "scripts/route_e2e_audit.py",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "target": base,
        "target_policy": {
            "loopback_only": True,
            "forbidden": sorted(FORBIDDEN_HOSTS | {"~/.hermes"}),
        },
        "token_source": "/chat window.__HERMES_SESSION_TOKEN__",
        "source_inventory": "docs/generated/parity_inventory.json",
        "read_only": read_only,
        "counts": {},
        "read_routes": reads,
        "mutations": mutations,
        "staleness": _service_staleness(),
        "limitations": [
            "scope is the 214 PORTED route rows (101 GET + 113 non-GET); the "
            "CLI/control/tool surfaces of parity_inventory.json are not HTTP "
            "routes and are not exercised here",
            "live HTTP only: the running service is the subject; a stale "
            "process (see staleness) may differ from the current checkout",
            "read pass covers PORTED GET routes; the remaining PORTED non-GET "
            "routes are exercised only through the curated safe mutation subset",
            "E2E_SKIP marks inputs that cannot be safely supplied "
            "(operator/hardware/external), not failures",
            "no raw PTY/WebSocket/SSE route is driven by this JSON auditor",
            "the artifact's only volatile fields are generated_at and "
            "staleness; the classification map is anchored by stable_digest "
            "and enforced by --check",
        ],
    }
    counts: Dict[str, int] = {"read_total": len(reads), "mutation_total": len(mutations)}
    for row in reads:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    for row in mutations:
        key = "mutation_" + row["classification"]
        counts[key] = counts.get(key, 0) + 1
    doc["counts"] = counts
    findings: List[Dict[str, Any]] = []
    for row in mutations:
        if row["classification"] == "E2E_FAIL":
            findings.append({
                "kind": "mutation_round_trip",
                "name": row["name"],
                "detail": row.get("detail", ""),
                "root_cause": row.get("root_cause"),
            })
    for row in reads:
        if row["classification"] == "E2E_FAIL":
            findings.append({
                "kind": "read_route",
                "id": row["id"],
                "detail": row.get("detail", ""),
            })
        elif row["classification"] == "E2E_GATED":
            findings.append({
                "kind": "ported_but_gated",
                "id": row["id"],
                "code": row.get("code"),
                "detail": row.get("detail", ""),
            })
    doc["findings"] = findings
    doc["stable_digest"] = stable_digest(doc)
    return doc


def _normalized(doc: Dict[str, Any]) -> str:
    return json.dumps(doc, indent=1, sort_keys=False) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--read-only", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="compare the stored stable_digest (no write)")
    ap.add_argument("--timeout", type=float, default=20.0)
    args = ap.parse_args(argv)

    doc = run_audit(args.base, read_only=args.read_only, timeout=args.timeout)
    out = Path(args.out)
    if args.check:
        if not out.is_file():
            print(f"MISSING: {out}", file=sys.stderr)
            return 1
        stored = json.loads(out.read_text(encoding="utf-8"))
        if stored.get("stable_digest") != doc["stable_digest"]:
            print(f"STALE: {out} digest {stored.get('stable_digest')} != "
                  f"{doc['stable_digest']}", file=sys.stderr)
            return 1
        print(f"current: {out} digest {doc['stable_digest']}")
        return 0
    out.write_text(_normalized(doc), encoding="utf-8")
    counts = doc["counts"]
    print(f"wrote {out}")
    print(f"  reads={counts.get('read_total')} "
          f"PASS={counts.get('E2E_PASS', 0)} "
          f"GATED={counts.get('E2E_GATED', 0)} "
          f"FAIL={counts.get('E2E_FAIL', 0)} "
          f"SKIP={counts.get('E2E_SKIP', 0)}")
    print(f"  mutations={counts.get('mutation_total')} "
          f"PASS={counts.get('mutation_E2E_PASS', 0)} "
          f"FAIL={counts.get('mutation_E2E_FAIL', 0)}")
    print(f"  stable_digest={doc['stable_digest']}")
    if doc["staleness"].get("stale"):
        print(f"  WARNING: {doc['staleness']['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
