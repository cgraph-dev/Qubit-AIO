#!/usr/bin/env python3
"""P8-02 — executable OWASP Agentic Top 10 + MCP security matrix.

The matrix is a catalog of rows, not a prose claim. Every row names one real
denial test in ``tests/phase8_security`` and the exact refusal it proves. The
script runs those tests with the current interpreter's pytest, classifies each
row from the observed result, and exits:

* ``0`` — every applicable row passed and no row is unclassified;
* ``1`` — at least one row failed or could not be classified;
* ``2`` — usage error.

A skipped / xfailed / inconclusive row is never reported as ``pass``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "qubit-office-security-matrix/1"
TASK_ID = "P8-02"

ROWS = [
    {
        "id": "ASI01",
        "category": "owasp_asi",
        "threat": "Agent Goal Hijack: injected content redirects the agent from "
                  "the owner goal or widens its authority.",
        "control": "Only an authenticated human surface may mint a "
                   "confirmation; model/page/tool text can neither mint nor "
                   "widen one; undeclared scopes are refused.",
        "test": "tests/phase8_security/test_asi_denials.py::"
                "test_asi01_injection_text_cannot_mint_or_widen_authority",
        "expected_denial": "CONFIRMATION_REQUIRED / "
                           "CONFIRMATION_SOURCE_FORBIDDEN / OUT_OF_SCOPE and "
                           "ISSUER_MODEL_FORBIDDEN at issuance.",
    },
    {
        "id": "ASI02",
        "category": "owasp_asi",
        "threat": "Tool Misuse and Exploitation: an agent invokes a tool "
                  "outside its grant or the executor runs unpolicy'd.",
        "control": "Deny-by-default ToolGateway allowlist with exact tool "
                   "names; refusal precedes the executor.",
        "test": "tests/phase8_security/test_asi_denials.py::"
                "test_asi02_unlisted_tool_is_denied_before_execution",
        "expected_denial": "BUSINESS_TOOL_NOT_ALLOWED with zero executor "
                           "calls.",
    },
    {
        "id": "ASI03",
        "category": "owasp_asi",
        "threat": "Identity and Privilege Abuse: an actor reads or mutates "
                  "another tenant's records.",
        "control": "Tenant-scoped lookup and invocation; a foreign tenant id "
                   "cannot address this business.",
        "test": "tests/phase8_security/test_asi_denials.py::"
                "test_asi03_cross_tenant_lookup_and_invocation_fail_closed",
        "expected_denial": "BUSINESS_NOT_FOUND with zero executor calls.",
    },
    {
        "id": "ASI04",
        "category": "owasp_asi",
        "threat": "Agentic Supply Chain: a poisoned or unpinned MCP tool is "
                  "treated as approved code.",
        "control": "Per-server allowlist; complete input/output/annotation "
                   "schema hash; a schema flip changes the catalog digest and "
                   "the pinned server version is carried on every record.",
        "test": "tests/phase8_security/test_asi_denials.py::"
                "test_asi04_unpinned_or_poisoned_mcp_tool_is_refused",
        "expected_denial": "BUSINESS_CATALOG_MCP_NOT_ALLOWLISTED for an "
                           "unlisted tool; a changed schema changes the hash "
                           "and digest, never silently keeping the old pin.",
    },
    {
        "id": "ASI05",
        "category": "owasp_asi",
        "threat": "Unexpected Code Execution: generated code escapes the "
                  "sandbox or reaches host IPC/files.",
        "control": "Workspace path jail, reserved/host-socket refusal, argv "
                   "allowlist and no shell interpolation.",
        "test": "tests/phase8_security/test_asi_denials.py::"
                "test_asi05_sandbox_refuses_escape_and_ipc",
        "expected_denial": "Typed sandbox refusals for traversal, host "
                           "sockets, command strings and shell interpreters.",
    },
    {
        "id": "ASI06",
        "category": "owasp_asi",
        "threat": "Memory and Context Poisoning: untrusted or cross-scope "
                  "memory is retrieved or written across tenants.",
        "control": "Per-scope aggregates, per-record ACL, instruction-like "
                   "untrusted content quarantined at admission, cross-tenant "
                   "write refused.",
        "test": "tests/phase8_security/test_asi_denials.py::"
                "test_asi06_poisoned_and_cross_scope_memory_is_withheld",
        "expected_denial": "A quarantined record is withheld from recall, a "
                           "different scope returns nothing, and a foreign "
                           "tenant write is BUSINESS_MEMORY_WRITE_DENIED.",
    },
    {
        "id": "ASI07",
        "category": "owasp_asi",
        "threat": "Insecure Inter-Agent Communication: a spoofed sender or a "
                  "replayed message acquires authority.",
        "control": "Authenticated-principal match and per-sender replay nonce "
                   "on the typed A2A fabric.",
        "test": "tests/phase8_security/test_asi_denials.py::"
                "test_asi07_spoofed_and_replayed_a2a_messages_are_refused",
        "expected_denial": "A2A_SPOOF_REFUSED and A2A_REPLAY_REFUSED.",
    },
    {
        "id": "ASI08",
        "category": "owasp_asi",
        "threat": "Cascading Failures: a runaway retry/cost loop exhausts "
                  "budget and starves other work.",
        "control": "Policy cost cap refused before execution and a fixed-"
                   "window per-capability rate limit before the adapter.",
        "test": "tests/phase8_security/test_asi_denials.py::"
                "test_asi08_budget_and_rate_caps_deny_before_execution",
        "expected_denial": "BUSINESS_BUDGET_EXCEEDED and "
                           "CAPABILITY_RUNTIME_RATE_LIMITED with zero "
                           "executor calls.",
    },
    {
        "id": "ASI09",
        "category": "owasp_asi",
        "threat": "Human-Agent Trust Exploitation: an approval for payload A "
                  "is replayed for payload B or a stale policy.",
        "control": "Execution-time re-authorization against the exact "
                   "normalized intent hash and current policy revision.",
        "test": "tests/phase8_security/test_asi_denials.py::"
                "test_asi09_approval_a_cannot_execute_payload_b",
        "expected_denial": "BUSINESS_APPROVAL_INTENT_MISMATCH for a changed "
                           "amount/recipient/target and "
                           "BUSINESS_APPROVAL_POLICY_STALE for a stale "
                           "revision.",
    },
    {
        "id": "ASI10",
        "category": "owasp_asi",
        "threat": "Rogue Agents: a compromised or runaway agent keeps acting "
                  "after the owner stops it.",
        "control": "Fail-closed runtime halt checked before dispatch and a "
                   "quarantine guard on the capability path.",
        "test": "tests/phase8_security/test_asi_denials.py::"
                "test_asi10_halt_and_quarantine_block_execution",
        "expected_denial": "CAPABILITY_RUNTIME_HALTED with zero adapter "
                           "calls while the halt is engaged.",
    },
    {
        "id": "MCP-OAUTH-PKCE",
        "category": "mcp",
        "threat": "MCP OAuth 2.1 without PKCE lets an intercepted code be "
                  "redeemed by another client.",
        "control": "authorization-code flow always sends a S256 PKCE "
                   "challenge; the pending verifier must hash to it.",
        "test": "tests/phase8_security/test_mcp_security.py::"
                "test_mcp_oauth_pkce_s256_challenge_is_exact",
        "expected_denial": "etsy_pkce_mismatch before any provider request.",
    },
    {
        "id": "MCP-OAUTH-STATE",
        "category": "mcp",
        "threat": "MCP OAuth callback CSRF or a stale pending authorization is "
                  "accepted.",
        "control": "Exact constant-time state comparison against the pending "
                   "record and a bounded pending TTL.",
        "test": "tests/phase8_security/test_mcp_security.py::"
                "test_mcp_oauth_state_and_pending_window_fail_closed",
        "expected_denial": "etsy_state_invalid for a missing, wrong or "
                           "expired pending state.",
    },
    {
        "id": "MCP-OAUTH-REDIRECT",
        "category": "mcp",
        "threat": "A client-supplied or missing redirect URI widens the OAuth "
                  "callback surface.",
        "control": "The redirect URI is fixed server-side configuration, is "
                   "required before a flow starts and is echoed exactly in "
                   "the authorization request.",
        "test": "tests/phase8_security/test_mcp_security.py::"
                "test_mcp_oauth_requires_configured_redirect_uri",
        "expected_denial": "etsy_not_configured when no exact redirect URI is "
                           "configured.",
    },
    {
        "id": "MCP-TOKEN-REPLAY",
        "category": "mcp",
        "threat": "A completed authorization code/state is replayed to mint a "
                  "second token.",
        "control": "The pending authorization is consumed on success; a "
                   "replayed callback has no pending state.",
        "test": "tests/phase8_security/test_mcp_security.py::"
                "test_mcp_oauth_callback_cannot_be_replayed",
        "expected_denial": "etsy_state_invalid on replay and exactly one "
                           "token-endpoint call.",
    },
    {
        "id": "MCP-OAUTH-ISSUER",
        "category": "mcp",
        "threat": "A different authorization-server issuer or a redirected "
                  "metadata document is accepted as the same issuer.",
        "control": "Exact RFC 8414/9728 issuer match; only the origin's own "
                   "derived well-known document for a path-scoped server may "
                   "stand in.",
        "test": "tests/phase8_security/test_mcp_security.py::"
                "test_mcp_oauth_issuer_must_match_exactly",
        "expected_denial": "A redirected document, another origin or the root "
                           "document is not accepted as an exact issuer match.",
    },
    {
        "id": "MCP-OAUTH-RESOURCE",
        "category": "mcp",
        "threat": "An OAuth token minted for another resource/audience is "
                  "accepted by this MCP server, or one server's token store is "
                  "reused by another.",
        "control": "RFC 8707 resource/audience binding through the pinned mcp "
                   "SDK; per-server token files (mcp-tokens/<server>.json) so a "
                   "token is never shared across servers.",
        "test": "tests/phase8_security/test_mcp_security.py::"
                "test_mcp_oauth_resource_audience_is_exact_and_per_server",
        "expected_denial": "A non-matching resource/audience is refused by "
                           "check_resource_allowed; each server resolves its "
                           "own token path.",
    },
    {
        "id": "MCP-TOOL-SCHEMA",
        "category": "mcp",
        "threat": "MCP tool poisoning / schema-flip after approval.",
        "control": "Hash the complete tool definition (input schema, output "
                   "schema and annotations), not just the description.",
        "test": "tests/phase8_security/test_mcp_security.py::"
                "test_mcp_complete_tool_schema_hash_covers_input_output_annotations",
        "expected_denial": "A changed output schema or annotation produces a "
                           "different complete hash, invalidating the old "
                           "pin.",
    },
    {
        "id": "MCP-SERVER-ALLOWLIST",
        "category": "mcp",
        "threat": "A disabled, unsupported or unlisted MCP server/tool is "
                  "invoked.",
        "control": "Brokered client gate: supported transport, enabled, "
                   "runnable, non-empty explicit allowlist, exact tool; "
                   "pinned server version carried on the record.",
        "test": "tests/phase8_security/test_mcp_security.py::"
                "test_mcp_server_allowlist_and_version_are_enforced",
        "expected_denial": "MCP_TOOL_NOT_ALLOWED, MCP_SERVER_DISABLED and "
                           "MCP_TRANSPORT_UNSUPPORTED before any transport.",
    },
    {
        "id": "MCP-SSRF",
        "category": "mcp",
        "threat": "MCP egress reaches loopback, private, link-local or cloud-"
                  "metadata addresses, or a redirect does.",
        "control": "Sandbox egress policy + capability runtime validation for "
                   "the MCP category before the adapter; every redirect hop "
                   "is re-checked.",
        "test": "tests/phase8_security/test_mcp_security.py::"
                "test_mcp_egress_blocks_loopback_private_and_metadata",
        "expected_denial": "Typed sandbox refusal for loopback/private/"
                           "metadata URLs and for a redirect into them, with "
                           "zero adapter calls.",
    },
    {
        "id": "MCP-OUTPUT",
        "category": "mcp",
        "threat": "Oversized or untrusted MCP output floods context or "
                  "carries unbounded content.",
        "control": "Bounded tool arguments, truncated/labelled output, and a "
                   "capability-runtime output cap before the result can reach "
                   "a prompt.",
        "test": "tests/phase8_security/test_mcp_security.py::"
                "test_mcp_oversized_args_and_output_are_bounded",
        "expected_denial": "MCP_PROTOCOL_ERROR for oversized arguments; output "
                           "truncated and marked redacted; "
                           "CAPABILITY_RUNTIME_OUTPUT_TOO_LARGE for an "
                           "oversized adapter result.",
    },
    {
        "id": "MCP-PER-SERVER-SCOPE",
        "category": "mcp",
        "threat": "A tool or credential from one MCP server is used against "
                  "another server.",
        "control": "Per-server allowlist and namespaced catalog identity "
                   "(mcp:<server>:<tool>); no cross-server tool reuse.",
        "test": "tests/phase8_security/test_mcp_security.py::"
                "test_mcp_tool_and_credential_scope_is_per_server",
        "expected_denial": "MCP_TOOL_NOT_ALLOWED when server B is asked for "
                           "server A's tool (and vice versa).",
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(rows: list[dict]) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    node_ids = [row["test"] for row in rows]
    env = dict(os.environ)
    pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(ROOT) if not pythonpath else f"{ROOT}{os.pathsep}{pythonpath}"
    )
    env.setdefault("TMPDIR", "/tmp")
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-v",
        "--no-header",
        "-p",
        "no:cacheprovider",
        *node_ids,
    ]
    completed = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=300,
    )
    observed: dict[str, str] = {}
    pattern = re.compile(r"^(tests/\S+::\S+)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)")
    for line in completed.stdout.splitlines():
        match = pattern.match(line.strip())
        if match:
            observed[match.group(1)] = match.group(2)
    return completed, observed


def _classify(row: dict, observed: dict[str, str]) -> tuple[str, str]:
    result = observed.get(row["test"])
    if result == "PASSED":
        return "pass", ""
    if result is None:
        return "fail", "test did not run or could not be collected"
    if result in ("SKIPPED", "XFAIL"):
        return "fail", f"test was {result}; a skipped control is not a pass"
    if result == "XPASS":
        return "fail", "test unexpectedly passed outside its strict contract"
    return "fail", f"test reported {result}"


def _tail(text: str, limit: int = 2000) -> str:
    clean = (text or "").strip()
    return clean[-limit:]


def build_report(completed: subprocess.CompletedProcess[str],
                 observed: dict[str, str]) -> dict:
    rows: list[dict] = []
    for row in ROWS:
        status, reason = _classify(row, observed)
        rows.append(
            {
                **row,
                "status": status,
                "reason": reason,
            }
        )
    applicable = [row for row in rows if row["status"] != "not_applicable"]
    passed = sum(1 for row in applicable if row["status"] == "pass")
    failed = sum(1 for row in applicable if row["status"] == "fail")
    not_applicable = sum(
        1 for row in rows if row["status"] == "not_applicable"
    )
    return {
        "schema": SCHEMA,
        "task_id": TASK_ID,
        "generated_at": _now(),
        "principle": (
            "A row passes only when its cited denial test executed and "
            "passed; skipped, xfailed, missing or not-applicable rows are "
            "never reported as pass."
        ),
        "command": {
            "cmd": " ".join(completed.args),
            "exit_code": completed.returncode,
            "stdout_tail": _tail(completed.stdout),
            "stderr_tail": _tail(completed.stderr),
        },
        "summary": {
            "rows": len(rows),
            "applicable": len(applicable),
            "passed": passed,
            "failed": failed,
            "not_applicable": not_applicable,
            "unclassified": 0,
        },
        "rows": rows,
    }


def _print_text(report: dict) -> None:
    print(f"P8-02 security matrix — {report['generated_at']}")
    for row in report["rows"]:
        print(f"  {row['status'].upper():4} {row['id']:24} {row['test']}")
        if row["reason"]:
            print(f"        {row['reason']}")
    summary = report["summary"]
    print(
        f"\n{summary['passed']} passed / {summary['failed']} failed / "
        f"{summary['not_applicable']} not applicable "
        f"({summary['rows']} rows)"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the P8-02 OWASP Agentic Top 10 + MCP security matrix."
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the machine-readable report"
    )
    parser.add_argument(
        "--list", action="store_true", help="list catalogued rows and exit"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list:
        for row in ROWS:
            print(f"{row['id']:24} {row['test']}")
        return 0
    try:
        completed, observed = _run(ROWS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"security_matrix: test run failed: {exc}", file=sys.stderr)
        return 1
    report = build_report(completed, observed)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_text(report)
    summary = report["summary"]
    if summary["failed"] or summary["unclassified"]:
        return 1
    if summary["applicable"] != summary["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
