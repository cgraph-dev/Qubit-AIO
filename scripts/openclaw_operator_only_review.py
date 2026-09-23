#!/usr/bin/env python3
"""Deterministic OPERATOR_ONLY disposition review for the upstream pin.

The contractual-pin surface in
``docs/archive/2026-09-21/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json`` is fully
disposed (434/434), but 95 rows are ``OPERATOR_ONLY``: the registered Qubit
handler emits a typed refusal because a prerequisite is not present. This
script produces the owner-review document
``docs/archive/2026-09-22-pre-reset/QUBIT_OPENCLAW_OPERATOR_ONLY_REVIEW_2026-09-21.md`` so the owner can
separate genuine external prerequisites from architecture-gated refusals.

It does NOT verify behavior and does NOT change any disposition. It only reads
the manifest (or the disposition map the manifest is generated from) and the
handler source, and renders a review table plus a machine-readable JSON index.

Classification rule (documented in the output):

* ``external-prerequisite`` — the typed refusal names a resource *outside*
  Qubit's codebase (a credential, a remote service/registry, a paired device,
  a process supervisor, or a provider transport) that the operator can
  provision. Qubit's architecture does not forbid supporting it; a live adapter
  is the only gap.
* ``architecture-gated`` — supporting the method would require Qubit to add or
  reverse an intentional architecture decision: an upstream server-side
  registry/store/runtime/sandbox Qubit deliberately does not run (single
  authority, local-first), or an intentional single-agent / single-operator
  design. Provisioning an external credential alone would not enable it.

Usage::

    python scripts/openclaw_operator_only_review.py --write
    python scripts/openclaw_operator_only_review.py --check
"""
from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = (REPO_ROOT
            / "docs/generated/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json")
OUT = (REPO_ROOT
       / "docs/generated/QUBIT_OPENCLAW_OPERATOR_ONLY_REVIEW_2026-09-21.md")

EXTERNAL = "external-prerequisite"
ARCH = "architecture-gated"
CLASSIFICATIONS = (EXTERNAL, ARCH)

JSON_BEGIN = "<!-- QUBIT_OPERATOR_ONLY_REVIEW_JSON"
JSON_END = "QUBIT_OPERATOR_ONLY_REVIEW_JSON -->"

#: code -> (classification, category, rationale). Every OPERATOR_ONLY code in
#: the manifest must appear here; the script fails closed on a missing entry.
CLASSIFICATION: dict[str, tuple[str, str, str]] = {
    # ----------------------------------------------------- external prerequisite
    "NODE_DEVICE_RELAY_OPERATOR_REQUIRED": (
        EXTERNAL, "paired node device",
        "Requires a paired node device/agent host running the native-harness "
        "relay; the device is external to the single local Qubit authority and "
        "can be provisioned by the operator."),
    "GITHUB_OPERATOR_REQUIRED": (
        EXTERNAL, "GitHub credential / OAuth",
        "Requires a GitHub credential reference (env:/file:) with scopes and "
        "network access, or a live github.com OAuth app for the device flow."),
    "TALK_REALTIME_OPERATOR_REQUIRED": (
        EXTERNAL, "provider realtime transport",
        "Requires an OpenAI Realtime (or equivalent) provider transport to be "
        "injected; Qubit resolves the profile read-only and starts no adapter."),
    "PUSH_WEB_OPERATOR_REQUIRED": (
        EXTERNAL, "web-push service",
        "Requires a configured web-push service (VAPID keypair + subscription "
        "store) and browser notification permission; an external protocol "
        "endpoint plus operator-issued keys."),
    "FORGE_API_OPERATOR_REQUIRED": (
        EXTERNAL, "git forge credential / API",
        "Requires a configured forge credential (for example a GitHub/GitLab "
        "token) and a network-reachable forge API."),
    "USERS_AUTH_CONNECT_OPERATOR_REQUIRED": (
        EXTERNAL, "external OAuth provider",
        "Requires a live external provider OAuth/connect service and network "
        "access; Qubit performs only a manual CLI handoff."),
    "CHANNEL_LISTENER_OPERATOR_REQUIRED": (
        EXTERNAL, "inbound channel transport",
        "Requires a configured inbound channel transport (webhook/poll) and "
        "its credential; Qubit stores references and runs no persistent "
        "listener."),
    "MESSAGING_TRANSPORT_OPERATOR_REQUIRED": (
        EXTERNAL, "messaging provider transport",
        "Requires a configured messaging account (provider credential + "
        "endpoint) and a reachable provider transport."),
    "PORTAL_SERVICE_OPERATOR_REQUIRED": (
        EXTERNAL, "portal hosting service",
        "Requires a browser/portal hosting service (the upstream portal HTTP "
        "proxy listening on a local port) that the operator hosts."),
    "RUNTIME_CLOUD_UNAVAILABLE": (
        EXTERNAL, "cloud-worker transport",
        "Requires an injected cloud-worker runtime transport and a reachable "
        "provider; Qubit sessions stay on the local runtime."),
    "ENVIRONMENT_CLOUD_OPERATOR_REQUIRED": (
        EXTERNAL, "cloud-worker transport",
        "Requires an injected cloud-worker provider transport and a reachable "
        "provider to create/destroy remote environments."),
    "EXEC_APPROVALS_NODE_OPERATOR_REQUIRED": (
        EXTERNAL, "paired node agent",
        "Requires a paired node agent that owns its own exec policy; Qubit's "
        "node host is enroll/revoke/show/dispatch only."),
    "GATEWAY_RESTART_OPERATOR_REQUIRED": (
        EXTERNAL, "operator service restart",
        "Requires the operator to restart the Qubit service unit; a gateway "
        "call never exits the process."),
    "SUSPEND_HANDOFF_OPERATOR_REQUIRED": (
        EXTERNAL, "service manager process ownership",
        "Requires a service manager that owns process exit (systemd/launchd "
        "external restart owner); Qubit never exits its own process."),
    "MCP_RESOURCE_TEMPLATES_OPERATOR_REQUIRED": (
        EXTERNAL, "external MCP server",
        "Requires an MCP server/runtime that advertises resource templates; "
        "Qubit's in-tree MCP tool layer exposes resources, not templates."),
    "NATIVE_HOOK_RELAY_OPERATOR_REQUIRED": (
        EXTERNAL, "live native-harness relay",
        "Requires a live native harness process exposing a registered hook "
        "relay (provider/relayId/generation)."),
    "PROJECT_REMOTE_SEARCH_OPERATOR_REQUIRED": (
        EXTERNAL, "git forge credential / network",
        "Requires a configured git forge credential and a network-reachable "
        "forge API for remote project discovery."),
    "SKILLS_SECURITY_VERDICTS_OPERATOR_REQUIRED": (
        EXTERNAL, "remote verdict registry",
        "Requires a reachable skill security-verdict registry/endpoint and "
        "network access; Qubit validates bundled/verified skills locally."),
    "UPDATE_HOLD_OPERATOR_ONLY": (
        EXTERNAL, "operator update hold",
        "Requires an operator-managed update hold on the runtime image; there "
        "is no hosted update campaign to hold locally."),
    "USERS_EMAIL_OPERATOR_REQUIRED": (
        EXTERNAL, "email / identity service",
        "Requires an email/identity verification service; Qubit stores no "
        "operator email."),
    # ----------------------------------------------------- architecture-gated
    "AGENTS_MULTI_AGENT_OPERATOR_REQUIRED": (
        ARCH, "intentional single-agent architecture",
        "Qubit is intentionally one unified agent (owner decision: one "
        "runtime, one source tree, no second agent registry). A multi-agent "
        "roster is not an external resource."),
    "ATTACH_MCP_LOOPBACK_OPERATOR_REQUIRED": (
        ARCH, "upstream server/transport (MCP loopback attach)",
        "Requires an MCP loopback attach server + a paired browser device "
        "identity; Qubit runs no attach server and uses session_share + device "
        "tokens instead."),
    "BOARD_ACTION_OPERATOR_REQUIRED": (
        ARCH, "board widget sandbox runtime",
        "Requires a board widget sandbox + capability authority runtime; "
        "Qubit's board authority is the slug-scoped kanban canvas with role "
        "grants."),
    "BOARD_EVENT_OPERATOR_REQUIRED": (
        ARCH, "board widget sandbox runtime",
        "Requires a board widget sandbox runtime + signed view tickets; Qubit "
        "models boards as kanban tabs/widgets by id/slug."),
    "BOARD_VIEW_TICKET_OPERATOR_REQUIRED": (
        ARCH, "signed board view-ticket runtime",
        "Requires a signed board view-ticket runtime; Qubit authorizes board "
        "access through kanban roles plus the shared confirmation journal."),
    "CRON_SCRATCH_OPERATOR_REQUIRED": (
        ARCH, "per-job scratch store",
        "Requires an upstream per-job cron scratch store (cron_job_scratch); "
        "Qubit's schedule store has no private per-job scratch column."),
    "MENTIONS_INBOX_OPERATOR_REQUIRED": (
        ARCH, "human-mention inbox service",
        "Requires a human-mention inbox service backed by configured message "
        "channels; Qubit runs no inbox registry."),
    "MCP_APP_SANDBOX_OPERATOR_REQUIRED": (
        ARCH, "MCP App sandbox runtime",
        "Requires an MCP App sandbox runtime + an active app view; Qubit's MCP "
        "authority is the in-process host in plugin_runtime."),
    "SKILLS_PROPOSALS_EVALUATE_OPERATOR_REQUIRED": (
        ARCH, "deliberate no-self-evaluation path",
        "Requires an isolated proposal-evaluation model runtime; Qubit "
        "deliberately has no automated evaluator and promotes only on an "
        "explicit human owner verdict."),
    "SKILLS_PROPOSALS_HISTORYSCAN_OPERATOR_REQUIRED": (
        ARCH, "session-history scanner",
        "Requires a session-history scan runtime (isolated model over the "
        "transcript) that emits pending proposals; Qubit runs no such scanner."),
    "SKILLS_UPLOAD_OPERATOR_REQUIRED": (
        ARCH, "chunked upload staging",
        "Requires a staged chunked upload store/transport for skill archives; "
        "Qubit installs only reviewed local manifests."),
    "TASK_SUGGESTIONS_OPERATOR_REQUIRED": (
        ARCH, "ephemeral task-suggestion registry",
        "Requires a model-proposed task-suggestion registry + run-control "
        "runtime; Qubit keeps no ephemeral suggestion registry."),
    "TASKS_DISMISS_OPERATOR_REQUIRED": (
        ARCH, "detached-task completion registry",
        "Requires a detached-task/subagent completion-delivery registry; "
        "Qubit's task ledger is canonical and read-only."),
    "USERS_AVATAR_OPERATOR_REQUIRED": (
        ARCH, "operator avatar/media store",
        "Requires an operator-managed avatar/media store and hosting; Qubit "
        "keeps no avatar field or media hosting."),
    "USERS_PROFILE_OPERATOR_REQUIRED": (
        ARCH, "intentional single-operator architecture",
        "Requires a multi-user profile/role store; Qubit is single-operator "
        "with fixed operator scopes, so there is no role to assign."),
    "WAKE_ROUTING_OPERATOR_REQUIRED": (
        ARCH, "wake-trigger/routing store",
        "Requires a configurable wake-trigger/routing store; Qubit routes wake "
        "detection through the one shared audio listener."),
    "WAKE_TRIGGERS_OPERATOR_REQUIRED": (
        ARCH, "writable wake-trigger store",
        "Requires a writable wake-trigger store; Qubit's wake phrases come "
        "from the operator-fetched sherpa-onnx keyword bundle."),
}

#: architecture-gated code -> (what Qubit would need to change,
#: real capability vs intentional architecture choice). Only codes classified
#: ARCH need an entry; the script fails closed if one is missing.
ARCH_NOTES: dict[str, tuple[str, str]] = {
    "AGENTS_MULTI_AGENT_OPERATOR_REQUIRED": (
        "Add a second agent roster with create/update/delete lifecycle "
        "(registry, per-agent config, routing) alongside the single runtime, "
        "and a routing authority that selects between agents.",
        "Real upstream capability, but supporting it contradicts the frozen "
        "single-unified-agent owner decision; it is an intentional architecture "
        "choice, not a provisioning gap."),
    "ATTACH_MCP_LOOPBACK_OPERATOR_REQUIRED": (
        "Run an MCP loopback attach server and a browser device-pairing "
        "handshake, then bind it to the canonical session/device authority.",
        "Real capability of the upstream server transport; Qubit deliberately "
        "runs one in-process MCP host plus session_share + device tokens, so "
        "this is an intentional architecture choice."),
    "BOARD_ACTION_OPERATOR_REQUIRED": (
        "Add a board widget sandbox and a capability-authority runtime that "
        "executes widget actions under signed grants.",
        "Real upstream capability; Qubit intentionally keeps boards as the "
        "slug-scoped kanban canvas with role grants and the shared "
        "confirmation journal, so this is architecture-gated."),
    "BOARD_EVENT_OPERATOR_REQUIRED": (
        "Add a board widget sandbox runtime plus signed view tickets for "
        "widget-sourced events.",
        "Real upstream capability; Qubit deliberately has no widget sandbox, "
        "so this is an intentional architecture choice."),
    "BOARD_VIEW_TICKET_OPERATOR_REQUIRED": (
        "Add a signed board view-ticket runtime and bind it to the board "
        "authorization path.",
        "Real upstream capability; Qubit authorizes boards through kanban "
        "roles and the confirmation journal, so this is an intentional "
        "architecture choice."),
    "CRON_SCRATCH_OPERATOR_REQUIRED": (
        "Add a private per-job scratch column/store to the schedule authority "
        "and expose read/write over it.",
        "Real capability, but it is a small internal state store Qubit "
        "intentionally does not keep; the local equivalent is a job field plus "
        "the memory workspace, so it is architecture-gated."),
    "MENTIONS_INBOX_OPERATOR_REQUIRED": (
        "Run a human-mention inbox service fed by configured message channels "
        "and persist mentions.",
        "Real upstream capability; Qubit intentionally runs no inbox service "
        "and uses the automation outbox/orders, so this is architecture-gated."),
    "MCP_APP_SANDBOX_OPERATOR_REQUIRED": (
        "Add an MCP App sandbox runtime with an active app view and bind it to "
        "the in-process MCP host.",
        "Real upstream capability; Qubit deliberately runs only the "
        "in-process MCP host in plugin_runtime, so this is an intentional "
        "architecture choice."),
    "SKILLS_PROPOSALS_EVALUATE_OPERATOR_REQUIRED": (
        "Add an isolated model runtime that scores skill proposals, then feed "
        "its verdict into the promotion gate.",
        "Real upstream capability, but Qubit deliberately verifies skills only "
        "by explicit human owner verdict (no self-evaluation path); it is an "
        "intentional architecture choice."),
    "SKILLS_PROPOSALS_HISTORYSCAN_OPERATOR_REQUIRED": (
        "Add a session-history scanner (isolated model over the transcript) "
        "that emits pending skill proposals.",
        "Real upstream capability; Qubit intentionally runs no history "
        "scanner and relies on the skill workshop author flow, so this is "
        "architecture-gated."),
    "SKILLS_UPLOAD_OPERATOR_REQUIRED": (
        "Add a staged chunked upload store/transport for skill archives plus "
        "the begin/chunk/commit protocol.",
        "Real upstream capability, but Qubit intentionally installs only "
        "reviewed local manifests (single-shot mutation + snapshot restore), "
        "so it is architecture-gated."),
    "TASK_SUGGESTIONS_OPERATOR_REQUIRED": (
        "Add an ephemeral task-suggestion registry and a run-control runtime "
        "that can accept a suggestion into a live run.",
        "Real upstream capability; Qubit deliberately keeps no ephemeral "
        "suggestion registry and uses durable schedules + the chat session, so "
        "this is architecture-gated."),
    "TASKS_DISMISS_OPERATOR_REQUIRED": (
        "Add a detached-task/subagent completion-delivery registry that can be "
        "dismissed.",
        "Real upstream capability; Qubit intentionally keeps the task ledger "
        "read-only, so this is an intentional architecture choice."),
    "USERS_AVATAR_OPERATOR_REQUIRED": (
        "Add an operator avatar/media store and media hosting, then bind it to "
        "the operator profile.",
        "Real upstream capability; Qubit deliberately keeps no avatar field "
        "and hosts no media, so this is an intentional architecture choice."),
    "USERS_PROFILE_OPERATOR_REQUIRED": (
        "Add a multi-user profile and role store and a role-assignment "
        "authority.",
        "Real upstream capability, but supporting it contradicts the "
        "single-operator design with fixed operator scopes; it is an "
        "intentional architecture choice."),
    "WAKE_ROUTING_OPERATOR_REQUIRED": (
        "Add a configurable wake-trigger/routing store read/write layer on the "
        "shared audio listener.",
        "Real upstream capability; Qubit deliberately routes wake detection "
        "through the one audio listener with an operator-fetched keyword "
        "bundle, so this is an intentional architecture choice."),
    "WAKE_TRIGGERS_OPERATOR_REQUIRED": (
        "Add a writable wake-trigger store bound to the wake phrase/model "
        "bundle.",
        "Real upstream capability; Qubit's wake phrases come from the "
        "operator-fetched sherpa-onnx keyword bundle, so persisting triggers "
        "is an intentional architecture choice."),
}


# --------------------------------------------------------------------------- #
# manifest + source extraction
# --------------------------------------------------------------------------- #
def load_operator_only(manifest_path: Path = MANIFEST) -> dict:
    """Return the manifest's OPERATOR_ONLY method -> record map."""
    data = json.loads(Path(manifest_path).read_text())
    methods = data["pinned_surface_disposition"]["methods"]
    return {name: dict(rec) for name, rec in methods.items()
            if rec.get("status") in ("OPERATOR_ONLY",
                                     "OPERATOR_ONLY_EXTERNAL",
                                     "OPERATOR_ONLY_ARCHITECTURE")}


def _const_str(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _resolve(node, consts: dict) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    return None


class _Source:
    """AST view of one handler module: constants, functions, registration."""

    def __init__(self, path: Path):
        self.path = path
        self.text = path.read_text()
        tree = ast.parse(self.text)
        self.consts: dict[str, str] = {}
        self.funcs: dict[str, ast.FunctionDef] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                value = _const_str(node.value)
                if value is not None:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            self.consts[target.id] = value
            elif isinstance(node, ast.AnnAssign) \
                    and isinstance(node.target, ast.Name):
                value = _const_str(node.value)
                if value is not None:
                    self.consts[node.target.id] = value
            elif isinstance(node, ast.FunctionDef):
                self.funcs[node.name] = node
        self.registry: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) \
                    and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "register" \
                    and len(node.args) >= 2:
                name, handler = node.args[0], node.args[1]
                if isinstance(name, ast.Constant) \
                        and isinstance(handler, ast.Name):
                    self.registry[name.value] = handler.id
        self.tree = tree

    def _emission(self, fn: ast.AST, code: str):
        """(lineno, counterpart) for the code emitted inside *fn*, or None."""
        direct = None
        for node in ast.walk(fn):
            if isinstance(node, ast.Dict):
                code_val = None
                code_lineno = node.lineno
                counterpart = None
                for key, value in zip(node.keys, node.values):
                    if not isinstance(key, ast.Constant):
                        continue
                    if key.value == "code":
                        code_val = _resolve(value, self.consts)
                        code_lineno = getattr(value, "lineno", node.lineno)
                    elif key.value == "qubit_counterpart":
                        counterpart = _resolve(value, self.consts)
                if code_val == code:
                    return (code_lineno, counterpart)
            if isinstance(node, ast.Call) \
                    and isinstance(node.func, ast.Name) \
                    and node.func.id == "_refusal" \
                    and node.args \
                    and _resolve(node.args[0], self.consts) == code:
                counterpart = (_resolve(node.args[3], self.consts)
                               if len(node.args) >= 4 else None)
                if direct is None:
                    direct = (node.lineno, counterpart)
        return direct

    def lookup(self, method: str, code: str):
        """Return (lineno, counterpart) for method's refusal emission."""
        handler = self.registry.get(method)
        if handler and handler in self.funcs:
            node = self.funcs[handler]
            found = self._emission(node, code)
            if found:
                return found
            for child in ast.walk(node):
                if isinstance(child, ast.Call) \
                        and isinstance(child.func, ast.Name) \
                        and child.func.id in self.funcs:
                    found = self._emission(self.funcs[child.func.id], code)
                    if found:
                        return found
        global_found = self._emission(self.tree, code)
        return global_found


_SOURCE_CACHE: dict[str, _Source] = {}


def source_for(rel_path: str) -> _Source:
    if rel_path not in _SOURCE_CACHE:
        _SOURCE_CACHE[rel_path] = _Source(REPO_ROOT / rel_path)
    return _SOURCE_CACHE[rel_path]


def build_rows(manifest_path: Path = MANIFEST) -> list[dict]:
    """Build the deterministic review rows for the manifest's OPERATOR_ONLY set."""
    ops = load_operator_only(manifest_path)
    rows: list[dict] = []
    for method in sorted(ops):
        rec = ops[method]
        code = str(rec["code"])
        evidence_file = str(rec["evidence"])
        if code not in CLASSIFICATION:
            raise SystemExit(
                f"OPERATOR_ONLY code {code!r} ({method}) has no classification")
        classification, category, rationale = CLASSIFICATION[code]
        source = source_for(evidence_file)
        found = source.lookup(method, code)
        if not found or found[0] is None:
            raise SystemExit(
                f"could not locate refusal emission for {method} ({code}) in "
                f"{evidence_file}")
        lineno, counterpart_from_source = found
        counterpart = (rec.get("qubit_counterpart")
                       or counterpart_from_source or "")
        row = {
            "method": method,
            "code": code,
            "prerequisite": str(rec.get("operator_prerequisite", "")),
            "classification": classification,
            "category": category,
            "rationale": rationale,
            "counterpart": str(counterpart),
            "evidence": f"{evidence_file}:{lineno}",
            "basis": str(rec.get("basis", "")),
            "would_need": None,
            "choice": None,
        }
        if classification == ARCH:
            if code not in ARCH_NOTES:
                raise SystemExit(
                    f"architecture-gated code {code!r} ({method}) has no "
                    "ARCH_NOTES entry")
            would_need, choice = ARCH_NOTES[code]
            row["would_need"] = would_need
            row["choice"] = choice
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def _cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def render(rows: list[dict], manifest_path: Path = MANIFEST) -> str:
    counts = Counter(row["classification"] for row in rows)
    total = len(rows)
    external = [r for r in rows if r["classification"] == EXTERNAL]
    gated = [r for r in rows if r["classification"] == ARCH]

    index = {
        "schema": "qubit-openclaw-operator-only-review/1",
        "manifest": str(manifest_path.relative_to(REPO_ROOT)),
        "counts": {
            "total": total,
            EXTERNAL: counts[EXTERNAL],
            ARCH: counts[ARCH],
        },
        "rows": rows,
    }

    out: list[str] = []
    out.append("# Qubit OpenClaw OPERATOR_ONLY Disposition Review — 2026-09-21")
    out.append("")
    out.append("**Status: NOT VERIFIED — review input for the owner ruling.**")
    out.append("This document classifies the 95 `OPERATOR_ONLY` rows of the "
               "OpenClaw contractual-pin surface; it does not verify behavior, "
               "does not change any disposition, and is not a parity claim.")
    out.append("")
    out.append("## Scope and method")
    out.append("")
    out.append(f"- **Source of the OPERATOR_ONLY set:** "
               f"`{manifest_path.relative_to(REPO_ROOT)}` "
               "(`pinned_surface_disposition.methods`, `status == "
               "OPERATOR_ONLY`).")
    out.append("- **Evidence:** each row's typed refusal is located in the "
               "handler source by AST (the registered handler function that "
               "emits the declared code, or the shared gate it delegates to), "
               "reported as `file:line`.")
    out.append("- **Regeneration:** `python "
               "scripts/openclaw_operator_only_review.py --write`; "
               "`--check` verifies this file is byte-identical. The "
               "machine-readable index is in the HTML comment at the end.")
    out.append("- **Classification is a review judgement**, not a manifest "
               "field. Ambiguous hybrid rows are called out in the notes.")
    out.append("")
    out.append("## Classification rule")
    out.append("")
    out.append(f"- **`{EXTERNAL}`** — the refusal names a resource *outside* "
               "Qubit's codebase (a credential, a remote service/registry, a "
               "paired device, a process supervisor, or a provider transport) "
               "that the operator can provision. Qubit's architecture does not "
               "forbid supporting it; a live adapter is the only gap.")
    out.append(f"- **`{ARCH}`** — supporting the method would require Qubit to "
               "add or reverse an intentional architecture decision: an "
               "upstream server-side registry/store/runtime/sandbox Qubit "
               "deliberately does not run (single authority, local-first), or "
               "an intentional single-agent / single-operator design. "
               "Provisioning an external credential alone would not enable it.")
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append(f"- Total `OPERATOR_ONLY` methods: **{total}**")
    out.append(f"- `{EXTERNAL}`: **{counts[EXTERNAL]}**")
    out.append(f"- `{ARCH}`: **{counts[ARCH]}**")
    out.append("")
    out.append("### Prerequisite families")
    out.append("")
    by_category: dict[str, list[str]] = {}
    for row in rows:
        by_category.setdefault(row["category"], []).append(row["method"])
    for category in sorted(by_category):
        members = ", ".join(f"`{m}`" for m in sorted(by_category[category]))
        out.append(f"- **{category}** ({len(by_category[category])}): {members}")
    out.append("")

    def table(rows_subset: list[dict], gated_table: bool) -> None:
        if gated_table:
            out.append("| method | typed code | exact prerequisite | local "
                       "counterpart / refusal | refusal evidence | what Qubit "
                       "would need to change | real capability or intentional "
                       "choice |")
            out.append("|---|---|---|---|---|---|---|")
            for row in rows_subset:
                out.append(
                    f"| `{row['method']}` | `{row['code']}` | "
                    f"{_cell(row['prerequisite'])} | "
                    f"{_cell(row['counterpart'])} | `{row['evidence']}` | "
                    f"{_cell(row['would_need'])} | {_cell(row['choice'])} |")
        else:
            out.append("| method | typed code | exact prerequisite | local "
                       "counterpart / refusal | refusal evidence |")
            out.append("|---|---|---|---|---|")
            for row in rows_subset:
                out.append(
                    f"| `{row['method']}` | `{row['code']}` | "
                    f"{_cell(row['prerequisite'])} | "
                    f"{_cell(row['counterpart'])} | `{row['evidence']}` |")

    out.append(f"## 1. `{EXTERNAL}` ({len(external)})")
    out.append("")
    out.append("Provisioning the named external resource is the remaining gap; "
               "no Qubit architecture change is implied.")
    out.append("")
    table(external, gated_table=False)
    out.append("")
    out.append(f"## 2. `{ARCH}` ({len(gated)})")
    out.append("")
    out.append("Each row states what Qubit would have to change and whether "
               "that is a real capability or an intentional architecture "
               "choice. These are the rows that need the owner's terminal "
               "ruling.")
    out.append("")
    table(gated, gated_table=True)
    out.append("")
    out.append("## Notes and uncertainty")
    out.append("")
    out.append("- Hybrid prerequisites (a service **and** an internal store) "
               "are classified by the primary provisionable primitive: "
               "`push.web.*` (web-push service + subscription store) and "
               "`portal.*` (hosted portal service) are `external-prerequisite`; "
               "`attach.*` (MCP loopback attach server + browser device "
               "identity) is `architecture-gated` because the missing primary "
               "is an upstream server Qubit does not run.")
    out.append("- `talk.*` is `external-prerequisite` because the missing "
               "primitive is an external provider transport, even though Qubit "
               "deliberately removed the realtime lane; whether to re-admit "
               "the lane is an owner decision.")
    out.append("- `skills.proposals.*` is `architecture-gated`: the missing "
               "primitive is Qubit's deliberate absence of a self-evaluation / "
               "history-scan runtime, not an external credential.")
    out.append("- `agents.*` and `users.setDisplayName` / `users.setRole` are "
               "`architecture-gated` by intentional single-agent / "
               "single-operator design.")
    out.append("- Classification rests on the recorded `operator_prerequisite` "
               "and the handler refusal detail; it is a judgement and should be "
               "confirmed by the owner. No disposition is changed here.")
    out.append("")
    out.append("## Machine-readable index")
    out.append("")
    out.append(JSON_BEGIN)
    out.append(json.dumps(index, indent=2, sort_keys=False))
    out.append(JSON_END)
    out.append("")
    return "\n".join(out)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(MANIFEST))
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--write", action="store_true",
                        help="write the review document")
    parser.add_argument("--check", action="store_true",
                        help="fail if the document is not regenerable")
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    rows = build_rows(manifest_path)
    text = render(rows, manifest_path)
    out_path = Path(args.out)

    if args.check:
        if not out_path.exists():
            raise SystemExit(f"missing review document: {out_path}")
        current = out_path.read_text()
        if current != text:
            raise SystemExit(
                f"{out_path} is stale; regenerate with "
                "scripts/openclaw_operator_only_review.py --write")
        counts = Counter(row["classification"] for row in rows)
        print(f"OK: {len(rows)} OPERATOR_ONLY rows "
              f"({counts[EXTERNAL]} {EXTERNAL}, {counts[ARCH]} {ARCH})")
        return 0

    if args.write:
        out_path.write_text(text)
        counts = Counter(row["classification"] for row in rows)
        print(f"wrote {out_path} ({len(rows)} rows: "
              f"{counts[EXTERNAL]} {EXTERNAL}, {counts[ARCH]} {ARCH})")
        return 0

    parser.error("pass --write or --check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
