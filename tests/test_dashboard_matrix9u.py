"""9U — dashboard page-by-page control matrix (hermetic backend dispatch).

Sole-owner workstream evidence for the page-by-page plan
(``docs/archive/2026-09-22-pre-reset/QUBIT_PAGE_BY_PAGE_PARITY_EXECUTION_PLAN_2026-09-21.md``). Each case
drives the *real* canonical dispatcher
(``voicebridge.qubit_compat.dispatch``) against a hermetic
``voicebridge.config.Config`` (temp ``XDG_*`` + ``QUBIT_HOME``) and asserts a
real registered handler answered with the documented wire shape — never a 404,
``NO_HANDLER``, or ``STREAM_REQUIRES_SSE`` stub.

Disposition vocabulary used by this file (see the companion matrix doc
``docs/archive/2026-09-22-pre-reset/QUBIT_DASHBOARD_MATRIX_2026-09-21.md``):

* ``backend-verified`` — the hermetic backend chain ran: a real handler
  returned a documented 2xx payload, or a mutation staged/settled through the
  real confirmation authority with persisted read-back.
* ``OPEN`` — typed refusal (gate/boolean-confirm), needs a live provider /
  hardware / browser, or otherwise lacks the hermetic behavioral proof.

No real provider, model, network, or browser E2E is exercised here; these are
hermetic backend-contract proofs only and are NOT an ``IMPLEMENTED`` claim.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.unit

NUM_PARAM_RE = re.compile(r"\(\?P<[^>]+>\[0-9\]\+\)")
PARAM_RE = re.compile(r"\(\?P<[^>]+>[^)]+\)|\.\*|\[\^/\]\+")


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        target = tmp_path / var.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(target))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / "qh"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\nwebui:\n  token: t\n")
    return C.Config.load(p)


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    return _cfg(tmp_path, monkeypatch)


def _fill(pattern: str) -> str:
    """Replace route params/wildcards with a concrete token.

    Numeric params (``[0-9]+``) get a digit so the route still matches.
    """
    pattern = NUM_PARAM_RE.sub("1", pattern)
    return PARAM_RE.sub("probe", pattern)


def _dispatch(cfg, method, path, body=None, query=None):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(cfg, method, path, query or {}, body or {})


# ---------------------------------------------------------------------------
# Read matrix: real 2xx handler + documented wire shape.
# ---------------------------------------------------------------------------
READ_MATRIX = [
    ("achievements", "GET", "/api/plugins/hermes-achievements/achievements",
     ["achievements", "discovered_count", "total_count"]),
    ("achievements", "GET", "/api/plugins/hermes-achievements/recent-unlocks",
     []),
    ("achievements", "GET", "/api/plugins/hermes-achievements/scan-status",
     ["finished_at", "state", "run_count"]),
    ("achievements", "GET",
     "/api/plugins/hermes-achievements/sessions/probe/badges",
     ["badges", "session_id"]),
    ("agents", "GET", "/api/qubit/agents",
     ["agent", "agents", "authority", "worker"]),
    ("channels", "GET", "/api/messaging/platforms",
     ["platforms", "env_path"]),
    ("chat", "GET", "/api/audio/devices", ["inputs", "outputs"]),
    ("chat", "GET", "/api/audio/list", ["inputs", "outputs"]),
    ("chat", "GET", "/api/audio/status", ["mic", "output", "schema"]),
    ("chat", "GET", "/api/audio/voice-config",
     ["audio", "mode", "stt", "tts", "voice"]),
    ("chat", "GET", "/api/qubit/chat/sessions",
     ["sessions", "total", "limit", "offset", "source"]),
    ("chat", "GET", "/api/qubit/chat/sessions/probe/activity",
     ["counts", "events", "session_id", "summary"]),
    ("chat", "GET", "/api/qubit/chat/sessions/probe/ci",
     ["ci", "session_id"]),
    ("chat", "GET", "/api/qubit/chat/sessions/probe/comments",
     ["comments", "session_id"]),
    ("chat", "GET", "/api/qubit/chat/sessions/probe/drafts",
     ["drafts", "session_id"]),
    ("config", "GET", "/api/config",
     ["agent", "audio", "intent", "logging", "voice", "webui"]),
    ("config", "GET", "/api/config/defaults",
     ["agent", "audio", "intent", "logging", "voice", "webui"]),
    ("config", "GET", "/api/config/schema", ["category_order", "fields"]),
    ("cron", "GET", "/api/cron/blueprints", ["blueprints", "ok"]),
    ("cron", "GET", "/api/cron/delivery-targets",
     ["delivery_targets", "ok"]),
    ("cron", "GET", "/api/cron/jobs", []),
    ("cron", "GET", "/api/cron/jobs/probe/runs",
     ["count", "runs", "limit"]),
    ("devices", "GET", "/api/qubit/browser/favicon",
     ["code", "detail", "result", "state"]),
    ("devices", "GET", "/api/qubit/browser/favicon-cache",
     ["code", "detail", "result", "state"]),
    ("devices", "GET", "/api/qubit/browser/profiles",
     ["authority", "managed_root", "profiles", "schema"]),
    ("devices", "GET", "/api/qubit/desktop/ops",
     ["code", "detail", "result", "state"]),
    ("devices", "GET", "/api/qubit/desktop/presentation",
     ["code", "detail", "result", "state"]),
    ("docs", "GET", "/openapi.json", ["info", "openapi", "paths"]),
    ("files", "GET", "/api/files",
     ["can_change_path", "entries", "locked_root", "path", "root"]),
    ("files", "GET", "/api/fs/default-cwd", ["branch", "cwd"]),
    ("files", "GET", "/api/fs/git-root", ["root"]),
    ("files", "GET", "/api/fs/list",
     ["entries", "folder", "truncated"]),
    ("files", "GET", "/api/fs/stat", ["stat"]),
    ("files", "GET", "/api/fs/tree",
     ["depth", "entries", "folder", "truncated"]),
    ("files", "GET", "/api/git/base-branches",
     ["branches", "code", "git", "root", "schema", "state"]),
    ("files", "GET", "/api/git/branches",
     ["branches", "code", "git", "root", "schema", "state"]),
    ("files", "GET", "/api/git/diff",
     ["code", "git", "patch", "root", "schema", "state"]),
    ("files", "GET", "/api/git/file-diff",
     ["code", "diff", "git", "root", "schema", "state"]),
    ("files", "GET", "/api/git/log",
     ["code", "entries", "git", "root", "schema", "state"]),
    ("files", "GET", "/api/git/review/commit-context",
     ["code", "diff", "git", "recent", "root", "schema", "state"]),
    ("files", "GET", "/api/git/review/diff",
     ["code", "diff", "git", "root", "schema", "state"]),
    ("files", "GET", "/api/git/review/list",
     ["base", "code", "files", "git", "root", "schema", "state"]),
    ("files", "GET", "/api/git/review/rev-parse",
     ["code", "git", "ref", "root", "schema", "sha", "state"]),
    ("files", "GET", "/api/git/show",
     ["code", "content", "git", "path", "ref", "root", "schema", "state"]),
    ("files", "GET", "/api/git/status",
     ["code", "git", "root", "schema", "state", "status"]),
    ("files", "GET", "/api/git/worktrees",
     ["code", "git", "root", "schema", "state", "worktrees"]),
    ("gateway", "GET", "/api/gateway",
     ["gateway", "process", "state", "supervision"]),
    ("gateway", "GET", "/api/gateway/rpc", ["authority", "methods", "ok"]),
    ("gateway", "GET", "/api/qubit/gateway/discovery",
     ["authority", "gateways", "marketplace", "peers", "providers"]),
    ("kanban", "GET", "/api/plugins/kanban/assignees", ["assignees", "ok"]),
    ("kanban", "GET", "/api/plugins/kanban/board",
     ["assignees", "columns", "latest_event_id", "tenants"]),
    ("kanban", "GET", "/api/plugins/kanban/boards", ["boards", "current"]),
    ("kanban", "GET", "/api/plugins/kanban/config",
     ["default_tenant", "include_archived_by_default", "lane_by_profile",
      "render_markdown"]),
    ("kanban", "GET", "/api/plugins/kanban/diagnostics",
     ["count", "diagnostics"]),
    ("kanban", "GET", "/api/plugins/kanban/events", ["events", "state"]),
    ("kanban", "GET", "/api/plugins/kanban/home-channels",
     ["home_channels"]),
    ("kanban", "GET", "/api/plugins/kanban/model-options", ["providers"]),
    ("kanban", "GET", "/api/plugins/kanban/orchestration",
     ["active_profile", "auto_decompose", "orchestrator_profile"]),
    ("kanban", "GET", "/api/plugins/kanban/profiles", ["profiles"]),
    ("kanban", "GET", "/api/plugins/kanban/projects", ["projects"]),
    ("kanban", "GET", "/api/plugins/kanban/stats",
     ["by_assignee", "by_status", "oldest_ready_age_seconds"]),
    ("kanban", "GET", "/api/plugins/kanban/workers/active",
     ["checked_at", "count", "workers"]),
    ("keys", "GET", "/api/env", ["ANTHROPIC_API_KEY", "GITHUB_TOKEN"]),
    ("keys", "GET", "/api/qubit/credentials/pool",
     ["authority", "providers", "schema", "state"]),
    ("logs_reports", "GET", "/api/analytics",
     ["by_model", "daily", "skills", "totals"]),
    ("logs_reports", "GET", "/api/analytics/models",
     ["models", "period_days", "totals"]),
    ("logs_reports", "GET", "/api/analytics/usage",
     ["by_model", "daily", "skills", "totals"]),
    ("logs_reports", "GET", "/api/logs", ["file", "lines", "ok"]),
    ("mcp", "GET", "/api/mcp",
     ["mount_policy", "servers", "ok"]),
    ("mcp", "GET", "/api/mcp/catalog",
     ["diagnostics", "entries", "mount_policy", "total"]),
    ("mcp", "GET", "/api/mcp/servers",
     ["mount_policy", "servers", "state"]),
    ("memory", "GET", "/api/curator",
     ["archive_after_days", "enabled", "interval_hours", "paused",
      "summary"]),
    ("memory", "GET", "/api/learning/graph", ["edges", "nodes", "stats"]),
    ("memory", "GET", "/api/learning/node", ["detail", "found", "id"]),
    ("memory", "GET", "/api/memory",
     ["active", "builtin_files", "enabled", "providers", "status"]),
    ("memory", "GET", "/api/memory/provider",
     ["active", "builtin_files", "enabled", "providers"]),
    ("memory", "GET", "/api/memory/providers/probe",
     ["active", "enabled", "provider", "tiers"]),
    ("models", "GET", "/api/local-models/catalog",
     ["fallback", "models", "runtime_observed"]),
    ("models", "GET", "/api/local-models/hardware",
     ["accelerator", "free_vram_mib", "vision"]),
    ("models", "GET", "/api/local-models/jobs",
     ["code", "jobs", "schema", "state"]),
    ("models", "GET", "/api/local-models/jobs/probe",
     ["code", "count", "job", "jobs"]),
    ("models", "GET", "/api/local-models/status",
     ["available", "backend", "enabled", "endpoint", "fallback", "model",
      "provider", "state"]),
    ("models", "GET", "/api/model/auxiliary", ["main", "tasks"]),
    ("models", "GET", "/api/model/info",
     ["auto_context_length", "capabilities", "effective_context_length",
      "model", "provider"]),
    ("models", "GET", "/api/model/moa",
     ["active_preset", "aggregator", "enabled", "presets",
      "reference_models"]),
    ("models", "GET", "/api/model/options",
     ["model", "provider", "providers"]),
    ("models", "GET", "/api/model/recommended-default",
     ["free_tier", "model", "provider"]),
    ("models", "GET", "/api/providers/custom-endpoints",
     ["current", "endpoints", "ok"]),
    ("models", "GET", "/api/qubit/local-models/fallback",
     ["chain", "qubit_counterpart", "runtime_observed"]),
    ("operations", "GET", "/api/actions/probe", ["actions", "state"]),
    ("operations", "GET", "/api/ask/pending", ["pending", "schema", "state"]),
    ("operations", "GET", "/api/ask/status",
     ["estop", "kinds", "max_choices", "pending", "ttl_s"]),
    ("operations", "GET", "/api/documents/kinds",
     ["kinds", "schema", "state"]),
    ("operations", "GET", "/api/documents/status",
     ["kinds", "max_chars", "pdf", "trust"]),
    ("operations", "GET", "/api/ops", ["count", "ops", "ok"]),
    ("operations", "GET", "/api/ops/approvals",
     ["approvals", "count", "ok"]),
    ("operations", "GET", "/api/ops/checkpoints",
     ["sessions", "total_bytes"]),
    ("operations", "GET", "/api/ops/hooks",
     ["hooks", "valid_events"]),
    ("operations", "GET", "/api/qubit/hooks/doctor",
     ["bundled_known", "emissions", "events_known", "hooks"]),
    ("operations", "GET", "/api/qubit/logbook",
     ["entries", "path", "qubit_counterpart", "summary"]),
    ("operations", "GET", "/api/qubit/usage",
     ["authority", "by_day", "by_model", "calls", "cost_usd",
      "total_tokens"]),
    ("operations", "GET", "/api/recall/policy",
     ["estop", "policy", "schema", "state"]),
    ("operations", "GET", "/api/recall/status",
     ["enabled", "estop", "max_items", "policy_state", "state"]),
    ("operations", "GET", "/api/shares/status",
     ["active", "count", "expired", "estop", "revoked"]),
    ("operations", "GET", "/api/transfers/status",
     ["authority", "estop", "nodes", "state", "transport"]),
    ("pairing", "GET", "/api/pairing", ["approved", "pending", "ok"]),
    ("plugins", "GET", "/api/dashboard/plugins", []),
    ("plugins", "GET", "/api/dashboard/plugins/fleet", ["plugins"]),
    ("plugins", "GET", "/api/dashboard/plugins/hub",
     ["catalog", "catalog_state", "install_capability", "plugins",
      "providers"]),
    ("plugins", "GET", "/api/dashboard/plugins/rescan",
     ["count", "plugins", "rescanned"]),
    ("profiles", "GET", "/api/profiles", ["active", "profiles", "ok"]),
    ("profiles", "GET", "/api/profiles/active",
     ["active", "current", "ok"]),
    ("profiles", "GET", "/api/profiles/probe/setup-command",
     ["command", "principal", "registered"]),
    ("profiles", "GET", "/api/profiles/projects/tree",
     ["active_id", "errors", "projects", "scoped_session_ids"]),
    ("profiles", "GET", "/api/profiles/sessions",
     ["errors", "sessions", "store_authority", "total"]),
    ("profiles", "GET", "/api/profiles/sessions/sidebar",
     ["cron", "errors", "messaging", "recents", "store_authority"]),
    ("runtime", "GET", "/api/qubit/runtime/discovery",
     ["gateways", "marketplace", "peers", "providers", "state"]),
    ("runtime", "GET", "/api/qubit/runtime/doctor",
     ["findings", "records", "state"]),
    ("runtime", "GET", "/api/qubit/runtime/history",
     ["authority", "entries", "image", "state"]),
    ("runtime", "GET", "/api/qubit/runtime/images",
     ["images", "schema", "state"]),
    ("runtime", "GET", "/api/qubit/runtime/marketplace",
     ["kinds", "local_plugins", "marketplace", "plugins", "state"]),
    ("runtime", "GET", "/api/qubit/runtime/status",
     ["daemon", "recovery", "runtimes", "state", "update_history"]),
    ("runtime", "GET", "/api/qubit/runtime/update-report",
     ["authority", "history", "recovery", "state", "status"]),
    ("runtime", "GET", "/api/runtime",
     ["adapters", "agent", "approvals", "auth", "broker", "capabilities",
      "mcp", "providers", "sessions", "voice", "worker"]),
    ("sessions", "GET", "/api/sessions",
     ["sessions", "total", "store_authority", "store_state"]),
    ("sessions", "GET", "/api/sessions/empty/count",
     ["count", "sessions", "store_authority"]),
    ("sessions", "GET", "/api/sessions/search",
     ["sessions", "total", "store_authority"]),
    ("sessions", "GET", "/api/sessions/shares",
     ["count", "shares", "qubit_counterpart", "state"]),
    ("sessions", "GET", "/api/sessions/stats",
     ["active_store", "archived", "by_source", "by_state", "messages",
      "total"]),
    ("skills", "GET", "/api/skills", []),
    ("skills", "GET", "/api/skills/hub/official",
     ["qubit_counterpart", "skills", "source"]),
    ("skills", "GET", "/api/skills/hub/search",
     ["installed", "results", "source_counts"]),
    ("skills", "GET", "/api/skills/hub/sources",
     ["featured", "index_available", "installed", "sources"]),
    ("system", "GET", "/api/auth/me",
     ["actor", "authenticated", "mode", "user_id"]),
    ("system", "GET", "/api/auth/providers",
     ["identity", "mode", "providers"]),
    ("system", "GET", "/api/dashboard/font", ["font", "ok"]),
    ("system", "GET", "/api/dashboard/themes", ["active", "themes"]),
    ("system", "GET", "/api/egress/status", ["frontier", "proxy", "text"]),
    ("system", "GET", "/api/health",
     ["auth_required", "state", "version"]),
    ("system", "GET", "/api/power",
     ["control", "grant", "grant_health", "mcp", "profile", "state"]),
    ("system", "GET", "/api/qubit/egress/providers",
     ["frontier_enabled", "providers", "schema", "state"]),
    ("system", "GET", "/api/qubit/egress/status",
     ["policy", "registry", "schema", "server", "state"]),
    ("system", "GET", "/api/qubit/policy/attest",
     ["attestation_id", "counts", "findings_digest", "ruleset_digest",
      "state"]),
    ("system", "GET", "/api/qubit/policy/conformance",
     ["counts", "critical", "findings", "ruleset", "state"]),
    ("system", "GET", "/api/qubit/update/check",
     ["behind", "can_apply", "clean", "state", "update_available"]),
    ("system", "GET", "/api/status",
     ["active_sessions", "auth_flows", "auth_providers", "gateway_state",
      "qubit", "schema", "version"]),
    ("system", "GET", "/api/system/stats",
     ["arch", "cpu_count", "hostname", "os", "platform", "python_version",
      "qubit_version"]),
    ("system", "GET", "/login",
     ["identity", "loopback", "mode", "providers"]),
    ("unmapped", "GET", "/api/events", ["count", "events", "ok"]),
    ("unmapped", "GET", "/api/tools/computer-use/status",
     ["atspi_reachable", "control", "driver", "schema", "state"]),
    ("unmapped", "GET", "/api/tools/terminal/backends",
     ["active", "backends", "ok"]),
    ("unmapped", "GET", "/api/tools/toolsets", []),
    ("unmapped", "GET", "/api/tools/toolsets/probe/config",
     ["active_provider", "has_category", "name", "providers"]),
    ("unmapped", "GET", "/api/tools/toolsets/probe/models",
     ["current", "default", "models", "name", "provider"]),
    ("webhooks", "GET", "/api/webhooks",
     ["base_url", "enabled", "subscriptions", "ok"]),
]


@pytest.mark.parametrize(
    "area,method,path,keys", READ_MATRIX,
    ids=[f"{a}-{p}" for a, _m, p, _k in READ_MATRIX])
def test_read_control_is_backend_verified(cfg, area, method, path, keys):
    code, out = _dispatch(cfg, method, path)
    assert code == 200, (area, method, path, code, out)
    if not keys:
        assert isinstance(out, list), (area, method, path, out)
        return
    assert isinstance(out, dict), (area, method, path, out)
    assert out.get("ok") is not False, (area, method, path, out)
    missing = [k for k in keys if k not in out]
    assert not missing, (area, method, path, missing, sorted(out))


# ---------------------------------------------------------------------------
# Safe hermetic mutations: real 2xx, persisted state, no live provider.
# ---------------------------------------------------------------------------
SAFE_MUTATION_MATRIX = [
    ("chat", "POST", "/api/qubit/chat/sessions", {"name": "matrix9u"},
     ["messages", "session", "ok"]),
    ("chat", "POST", "/api/qubit/chat/pty", {},
     ["alive", "output", "session_id", "ok"]),
    ("achievements", "POST", "/api/plugins/hermes-achievements/rescan", {},
     ["achievements", "sessions", "total_count", "ok"]),
    ("channels", "POST", "/api/messaging/platforms/probe/test", {},
     ["platform", "reachable", "state", "ok"]),
    ("memory", "PUT", "/api/memory/provider", {},
     ["active", "config", "qubit_counterpart", "ok"]),
    ("system", "PUT", "/api/dashboard/theme", {"theme": "qubit"},
     ["persisted", "theme", "ok"]),
    ("system", "PUT", "/api/dashboard/font", {"font": "Inter"},
     ["font", "persisted", "ok"]),
    ("plugins", "POST", "/api/dashboard/plugins/probe/visibility",
     {"visible": False}, ["hidden", "name", "visible", "ok"]),
    ("gateway", "POST", "/api/gateway/drain", {"action": "cancel"},
     ["action", "draining", "ok"]),
    ("unmapped", "POST", "/api/pub", {"channel": "matrix9u", "text": "hi"},
     ["channel", "published", "seq", "ok"]),
    ("webhooks", "POST", "/api/webhooks/enable",
     {"enabled": True, "confirm": True}, ["enabled", "platform", "ok"]),
    ("operations", "POST", "/api/ops/debug-share", {},
     ["bundle", "redacted", "ok"]),
    ("pairing", "POST", "/api/pairing", {"confirm": True}, ["result", "ok"]),
]


@pytest.mark.parametrize(
    "area,method,path,body,keys", SAFE_MUTATION_MATRIX,
    ids=[f"{a}-{m}-{p}" for a, m, p, _b, _k in SAFE_MUTATION_MATRIX])
def test_safe_mutation_is_backend_verified(cfg, area, method, path, body,
                                           keys):
    code, out = _dispatch(cfg, method, path, body)
    assert code == 200, (area, method, path, code, out)
    assert isinstance(out, dict), (area, method, path, out)
    missing = [k for k in keys if k not in out]
    assert not missing, (area, method, path, missing, sorted(out))


# ---------------------------------------------------------------------------
# Journal-staged confirmation: real settleable confirmation_id.
# ---------------------------------------------------------------------------
JOURNAL_CONFIRM_MATRIX = [
    ("profiles", "POST", "/api/profiles", {"name": "matrix9u-principal"}),
    ("cron", "POST", "/api/cron/jobs",
     {"name": "matrix9u", "prompt": "x", "schedule": "every 60 minutes"}),
    ("cron", "DELETE", "/api/cron/jobs/probe", {}),
    ("mcp", "DELETE", "/api/mcp/servers/probe", {}),
    ("mcp", "POST", "/api/mcp/servers/probe/enabled", {}),
    ("mcp", "PUT", "/api/mcp/servers/probe/enabled", {}),
    ("webhooks", "DELETE", "/api/webhooks/probe", {}),
    ("devices", "POST", "/api/qubit/desktop/pip", {}),
    ("devices", "POST", "/api/qubit/desktop/present", {}),
    ("unmapped", "PUT", "/api/tools/toolsets/probe", {}),
]


@pytest.mark.parametrize(
    "area,method,path,body", JOURNAL_CONFIRM_MATRIX,
    ids=[f"{a}-{m}-{p}" for a, m, p, _b in JOURNAL_CONFIRM_MATRIX])
def test_journal_confirmation_stages_a_real_id(cfg, area, method, path, body):
    code, out = _dispatch(cfg, method, path, body)
    assert code == 409, (area, method, path, code, out)
    assert isinstance(out, dict), (area, method, path, out)
    assert out.get("code") == "CONFIRM_REQUIRED", (area, method, path, out)
    cid = out.get("confirmation_id")
    assert cid, (area, method, path, "empty confirmation id", out)
    assert out.get("qubit_counterpart"), (area, method, path, out)


# ---------------------------------------------------------------------------
# Boolean-confirm mutations: typed 409 with a documented re-issue contract.
# ---------------------------------------------------------------------------
BOOLEAN_CONFIRM_MATRIX = [
    ("profiles", "PUT", "/api/profiles/probe/soul", {}),
    ("profiles", "PUT", "/api/profiles/probe/description", {}),
    ("profiles", "PUT", "/api/profiles/probe/model", {}),
    ("profiles", "DELETE", "/api/profiles/probe", {}),
    ("profiles", "POST", "/api/profiles/probe/export", {}),
    ("operations", "POST", "/api/transfers/fetch", {}),
    ("operations", "POST", "/api/transfers/plan", {}),
    ("operations", "POST", "/api/recall/policy", {}),
    ("operations", "POST", "/api/recall/escalate", {}),
    ("operations", "POST", "/api/documents/extract", {}),
    ("operations", "POST", "/api/ask", {}),
    ("sessions", "POST", "/api/sessions/shares/purge", {}),
    ("sessions", "DELETE", "/api/sessions/shares/probe", {}),
    ("sessions", "POST", "/api/sessions/probe/share", {}),
    ("keys", "PUT", "/api/env", {}),
    ("keys", "DELETE", "/api/env", {}),
    ("system", "POST", "/api/qubit/update", {}),
    ("models", "POST", "/api/providers/custom-endpoints/probe/activate", {}),
    ("models", "DELETE", "/api/providers/custom-endpoints/probe", {}),
    ("unmapped", "PUT", "/api/tools/terminal/backend", {}),
    ("unmapped", "POST", "/api/tools/computer-use/permissions/grant", {}),
    ("memory", "POST", "/api/memory/reset", {}),
    ("achievements", "POST",
     "/api/plugins/hermes-achievements/reset-state", {}),
    ("plugins", "POST", "/api/dashboard/agent-plugins/probe/enable", {}),
    ("plugins", "POST", "/api/dashboard/agent-plugins/probe/disable", {}),
    ("plugins", "DELETE", "/api/dashboard/agent-plugins/probe", {}),
]

# Boolean-confirm rows whose handler validates its payload *before* the
# confirmation gate, so an empty body is a typed 400 MALFORMED rather than a
# staged 409. The contract lives in voicebridge/qubit_compat.profile_model
# (provider and model are required before the ``confirm`` flag is consulted,
# mirroring the reference web_routers/profiles.py:update_profile_model_endpoint).
BOOLEAN_CONFIRM_PREVALIDATED = {
    ("PUT", "/api/profiles/probe/model"): "provider and model are required",
}


@pytest.mark.parametrize(
    "area,method,path,body", BOOLEAN_CONFIRM_MATRIX,
    ids=[f"{a}-{m}-{p}" for a, m, p, _b in BOOLEAN_CONFIRM_MATRIX])
def test_boolean_confirm_mutation_is_typed(cfg, area, method, path, body):
    code, out = _dispatch(cfg, method, path, body)
    assert isinstance(out, dict), (area, method, path, out)
    # A handler that validates its payload before consulting the confirm flag
    # answers 400 MALFORMED for an empty body; the well-formed confirm path is
    # covered elsewhere (e.g. test_wave1_dispatch_non_refusal_success).
    prevalidated = BOOLEAN_CONFIRM_PREVALIDATED.get((method, path))
    if prevalidated is not None:
        assert code == 400, (area, method, path, code, out)
        assert out.get("code") == "MALFORMED", (area, method, path, out)
        assert prevalidated in str(out.get("detail", "")), \
            (area, method, path, out)
        return
    assert code == 409, (area, method, path, code, out)
    assert out.get("code") in (
        "CONFIRM_REQUIRED", "SHARE_CONFIRM_REQUIRED"), (area, method, path, out)
    assert out.get("state") in (
        "NEEDS_CONFIRMATION", "CONFIRM_REQUIRED", "CONFIRMATION_REQUIRED",
        "UNAVAILABLE"), (area, method, path, out)


def test_memory_reset_get_is_a_read_only_status(cfg):
    """GET /api/memory/reset is a read status, never a staged mutation (D-14)."""
    code, out = _dispatch(cfg, "GET", "/api/memory/reset")
    assert code == 200, (code, out)
    assert out.get("ok") is True, out
    assert out.get("confirm_required") is True, out
    assert set(out.get("targets") or []) == {"all", "memory", "user"}, out


# ---------------------------------------------------------------------------
# Settle + persisted read-back (strongest hermetic behavioral proof).
# ---------------------------------------------------------------------------
def test_cron_confirmation_settles_and_persists(cfg):
    body = {"name": "matrix9u-cron", "prompt": "summarize",
            "schedule": "every 60 minutes"}
    code, staged = _dispatch(cfg, "POST", "/api/cron/jobs", body)
    assert code == 409 and staged.get("confirmation_id"), staged
    code, done = _dispatch(
        cfg, "POST", "/api/cron/jobs",
        {**body, "confirmation_id": staged["confirmation_id"]})
    assert code == 200, (code, done)
    code, listed = _dispatch(cfg, "GET", "/api/cron/jobs")
    assert code == 200 and isinstance(listed, list), (code, listed)
    assert any(j.get("name") == "matrix9u-cron" for j in listed), listed


def test_mcp_confirmation_settles_and_persists(cfg):
    from voicebridge import mcp_servers as MS

    body = {"name": "matrix9u-mcp", "transport": "http",
            "url": "https://example.test/mcp"}
    code, staged = _dispatch(cfg, "POST", "/api/mcp/servers", body)
    assert code == 409 and staged.get("confirmation_id"), staged
    code, done = _dispatch(
        cfg, "POST", "/api/mcp/servers",
        {**body, "confirmation_id": staged["confirmation_id"]})
    assert code == 200, (code, done)
    names = {s["name"] for s in MS.list_servers(cfg)["servers"]}
    assert "matrix9u-mcp" in names, names


def test_webhook_confirmation_settles_and_persists(cfg):
    from voicebridge import webhooks as W

    code, created = _dispatch(
        cfg, "POST", "/api/webhooks",
        {"name": "matrix9u-hook", "url": "http://127.0.0.1:9/hook",
         "events": ["task.completed"], "secret_ref": "env:MATRIX9U_HOOK",
         "confirm": True})
    assert code == 200 and created.get("ok") is True, (code, created)
    code, staged = _dispatch(cfg, "DELETE", "/api/webhooks/matrix9u-hook")
    assert code == 409 and staged.get("confirmation_id"), staged
    code, done = _dispatch(
        cfg, "DELETE", "/api/webhooks/matrix9u-hook",
        {"confirmation_id": staged["confirmation_id"]})
    assert code == 200, (code, done)
    assert W.list_subs(cfg)["subscriptions"] == []


# ---------------------------------------------------------------------------
# Gated routes: every typed-refusal gate is a real, documented refusal.
# ---------------------------------------------------------------------------
def test_gated_routes_are_typed_refusals(cfg):
    from voicebridge import qubit_compat as HC

    gated = [r for r in HC.ROUTES if r["disposition"] == "gated"]
    assert gated, "no gated routes found"
    for r in gated:
        path = _fill(r["pattern"])
        route, _params = HC.match(r["method"], path)
        assert route is not None, (r["method"], r["pattern"], path)
        gate = r.get("gate") or {}
        code, out = HC.dispatch(cfg, r["method"], path)
        assert code == gate.get("http", 403), (r["method"], r["pattern"],
                                               path, code, out)
        assert isinstance(out, dict), (r["method"], r["pattern"], out)
        assert out.get("ok") is False, (r["method"], r["pattern"], out)
        assert out.get("code") == gate.get("code"), (
            r["method"], r["pattern"], out.get("code"), gate.get("code"))
        assert out.get("qubit_counterpart"), (r["method"], r["pattern"], out)


# ---------------------------------------------------------------------------
# Registry integrity: every registered route resolves to a callable handler.
# ---------------------------------------------------------------------------
def test_every_registered_route_resolves_to_a_real_handler():
    from voicebridge import qubit_compat as HC

    assert HC.ROUTES, "empty route table"
    missing = []
    for r in HC.ROUTES:
        route, _params = HC.match(r["method"], _fill(r["pattern"]))
        if route is None:
            missing.append(("UNMATCHED", r["method"], r["pattern"]))
            continue
        if r["disposition"] == "implemented":
            handler = HC.HANDLERS.get(r["handler"])
            if not callable(handler):
                missing.append(("NO_HANDLER", r["method"], r["pattern"],
                                r["handler"]))
    assert not missing, missing


def test_every_implemented_route_is_not_a_dispatcher_stub(cfg):
    """No implemented route falls through to the dispatcher's own 404/500.

    A handler may legitimately answer a typed 404 for a missing resource
    (e.g. ``SESSION_NOT_FOUND``); only the dispatcher's unmatched-route
    ``NOT_FOUND`` and a missing ``NO_HANDLER`` count as a stub.
    """
    from voicebridge import qubit_compat as HC

    stubs = []
    for r in HC.ROUTES:
        if r["disposition"] != "implemented":
            continue
        path = _fill(r["pattern"])
        code, out = HC.dispatch(cfg, r["method"], path)
        if code == 404 and isinstance(out, dict) and str(
                out.get("detail", "")).startswith("no compat route"):
            stubs.append(("UNMATCHED_404", r["method"], r["pattern"]))
        if isinstance(out, dict) and out.get("code") in (
                "NO_HANDLER", "STREAM_REQUIRES_SSE"):
            stubs.append((out.get("code"), r["method"], r["pattern"]))
    assert not stubs, stubs


# ---------------------------------------------------------------------------
# Audit journal: the dispatch path emits served/gated events.
# ---------------------------------------------------------------------------
def test_compat_dispatch_invokes_the_audit_callsite(cfg, monkeypatch):
    """The dispatch path calls the single audit writer for served/gated.

    OPEN finding (recorded in the matrix doc): the production ``_audit`` passes
    the detail dict positionally to ``logging_setup.log_event(event, **fields)``
    (``voicebridge/qubit_compat.py:50-51``), which raises a swallowed
    ``TypeError``, so compat events never reach ``events.jsonl``. This test pins
    that the call site fires; it does not claim the journal write works.
    """
    from voicebridge import logging_setup as LS

    seen = []

    def _rec(*args, **fields):
        seen.append((args[0] if args else None, args[1:], fields))

    monkeypatch.setattr(LS, "log_event", _rec)
    assert _dispatch(cfg, "GET", "/api/health")[0] == 200
    _dispatch(cfg, "GET", "/api/pty")
    names = [e for e, _a, _f in seen]
    assert "qubit_compat.served" in names, names
    assert "qubit_compat.gated" in names, names


# ---------------------------------------------------------------------------
# E-stop: the shared latch refuses the canonical turn path.
# ---------------------------------------------------------------------------
def test_estop_latch_refuses_turn_and_lifts(cfg):
    from voicebridge.power import power_control_store

    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        code, out = _dispatch(cfg, "POST", "/api/qubit/chat",
                              {"message": "hi"})
        assert code == 403, (code, out)
        assert out.get("code") == "EMERGENCY_STOP", out
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")
    code, out = _dispatch(cfg, "POST", "/api/qubit/chat", {})
    assert out.get("code") != "EMERGENCY_STOP", (code, out)


# ---------------------------------------------------------------------------
# Plan coverage: every plan area that owns a backend route is probed here.
# ---------------------------------------------------------------------------
PLAN_AREA_BACKEND_OWNERS = {
    "chat": {"chat"},
    "sessions": {"sessions"},
    "files": {"files"},
    "models": {"models"},
    "logs_reports": {"logs_reports"},
    "cron": {"cron"},
    "skills": {"skills"},
    "plugins": {"plugins"},
    "mcp": {"mcp"},
    "channels": {"channels"},
    "webhooks": {"webhooks"},
    "pairing": {"pairing"},
    "profiles": {"profiles"},
    "config": {"config"},
    "keys": {"keys"},
    "system": {"system"},
    "agents": {"agents"},
    "docs": {"docs"},
    "boards": {"kanban"},
    "kanban": {"kanban"},
    "gateway": {"gateway"},
    "memory": {"memory"},
    "runtime": {"runtime"},
    "operations": {"operations"},
    "achievements": {"achievements"},
    "devices": {"devices"},
}


def test_every_plan_area_with_backend_routes_is_probed():
    probed = set()
    for row in READ_MATRIX:
        probed.add(row[0])
    for row in SAFE_MUTATION_MATRIX:
        probed.add(row[0])
    for row in JOURNAL_CONFIRM_MATRIX:
        probed.add(row[0])
    for row in BOOLEAN_CONFIRM_MATRIX:
        probed.add(row[0])
    gaps = {plan: owners for plan, owners in PLAN_AREA_BACKEND_OWNERS.items()
            if not (owners & probed)}
    assert not gaps, gaps
