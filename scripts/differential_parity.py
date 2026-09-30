#!/usr/bin/env python3
"""Differential parity runner for the Qubit / upstream 1:1 wave.

Implements the runner required by
``docs/archive/2026-09-21/QUBIT_LATEST_UPSTREAM_1TO1_EXECUTION_PLAN_2026-09-16.md`` section 8 and
the frozen ``docs/differential/INTERFACE.md`` contract (v1; canonical in-repo
copy of the former untracked ``/tmp/qubit-differential/INTERFACE.md``).

Fail-closed rules:

* stdlib only, Python 3.12, deterministic and rerunnable;
* hermetic by default: this module never opens a network socket. Network or
  subprocess work can only happen through an adapter explicitly provided to
  :func:`run_inventory`;
* state-changing fixtures are OPERATOR_ONLY by default and are NEVER executed
  (no socket, no subprocess) unless the explicit ``--allow-state-changing``
  opt-in is passed; a skipped row records ``state_changing_skipped``. The gate
  is fail-closed on ANY non-GET/HEAD HTTP method (POST/PUT/PATCH/DELETE), on
  mutating console/CLI verbs (``pause``/``power``/``reset``/``engage``/…) in
  the fixture command, sequence steps or declared references, on
  ``state_changing: true``, and on the built-in mutating path/action markers
  (``/api/power``, ``/api/ops/confirm``, ``/api/ops/run``, ``emergency-stop``,
  …). ``read_only: true`` never releases a mutating method: only GET/HEAD and a
  documented safe-read POST (JSON-RPC query, or a declared ``read_method``) may
  execute;
* SSRF guard: an absolute request URL is refused unless its host, scheme and
  port match the resolved base URL, and a literal ``base_url`` is accepted only
  when its exact resolved origin matches the operator env base URL or an
  explicit ``QUBIT_DIFFERENTIAL_ALLOWED_ORIGINS`` entry — the auth token never
  leaves the resolved base origin;
* an adapter payload is secret-scanned and REDACTED before it is stored or
  reported, so a server-echoed secret is never written verbatim while the
  ``secret_in_artifact`` gate still fires;
* a row is never ``PASS`` unless a result was executed and compared; an absent
  binary or fixture becomes ``OPERATOR_ONLY`` with an ``operator_prerequisite``;
* every ``SAFE_EQUIVALENT`` claim carries an explicit ``proof_level``:
  ``executed`` (a real executed comparison with both result hashes) or
  ``wiring`` (a resolved Qubit production mapping backed by a real,
  ``tests/<file>.py::<test_fn>`` reference that actually exercises the row's
  specific route or symbol). For a dashboard Control-UI row mapped to a
  ``frontend/qubit-web/src`` component, a second wiring PROOF TYPE,
  ``frontend_linkage``, applies: a real colocated vitest suite that IMPORTS the
  mapped component and references the mapped symbol disposes the row (both the
  component and the test must exist on disk; nothing is fabricated and an
  unsupported row stays OPEN). The level is validated by the ``proof_level`` /
  ``proof_level_requires_resolvable_test_id`` gates; a claim whose cited test
  resolves but does not reference the row's route/symbol is downgraded to OPEN
  with ``wiring_proof_status="no_row_linkage"`` (a class-level surface test is
  not proof for a specific route). ``safe_equivalent_requires_proof`` keeps the
  historical
  mapping+recon-keys standard, while an upstream latest-main delta row is
  disposed only by a REAL proof (executed result or a resolvable test): a bare
  ``qubit_path``/``disposition_proof``/name-level ``verification`` is not a
  test and stays OPEN. The resolver searches the Python test tree by AST and
  the owner ledger (``docs/generated/QUBIT_GAP_LEDGER.openclaw.json``) and never
  manufactures a test id: an unresolved citation is not proof;
* only the seven allowlisted normalizers may collapse nondeterminism; any other
  key raises, and a normalizer that changes an out-of-shape value is reported as
  ``unsafe_normalizer``;
* secrets never appear in argv, subprocess env values, logs, fixtures or
  reports. Fixture env values are references (``$NAME`` / ``env:NAME``);
* per-fixture reference binding: ``references.<project>.env_ref`` /
  ``bin_ref`` name an environment variable that resolves the reference binary
  at execution time. Only the resolved (non-secret) path is recorded; the
  variable's value is never copied into argv, the subprocess env, logs or the
  report. A declared but unresolved binding is fail-closed (no PASS);
* structural reference determinism: every executed declared reference that is
  not already ``nondeterministic: true`` is executed a SECOND time (only when
  it is a side-effect-free CLI/HTTP reference the state-changing guard permits),
  both payloads are normalized with the same normalizers and hashed, and a
  mismatch marks the reference ``nondeterministic`` and forces the row
  OPERATOR_ONLY (never PASS). A declared ``nondeterministic: true`` skips the
  recheck and is OPERATOR_ONLY. Extra executions are bounded per run by
  ``QUBIT_DIFFERENTIAL_REFERENCE_RECHECKS`` (default
  ``DEFAULT_REFERENCE_RECHECKS``); an unbudgeted, skipped or errored recheck is
  fail-closed. Each result carries ``reference_determinism`` per project
  (``{checked, stable, reason}``);
* references execute independently of the Qubit side, so a row whose Qubit
  transport/binary is unavailable still records its upstream evidence while
  remaining ``OPERATOR_ONLY`` (never PASS). The bounded ``run
  --references-only`` diagnostic executes declared references with the Qubit
  side disabled, purely for evidence, and can never emit PASS;
* the report header carries a non-secret ``reference_provenance`` block
  (``bin``/``version``/``commit``/``pinned`` per project) that ``verify``
  recomputes from the rows and checks, so a stale or leaked provenance block is
  a hard failure;
* the fail-closed ``byte_parity`` gate consumes the deterministic pinned upstream
  byte manifest. The default resolves to the in-repo location
  (``docs/differential/byte_manifest/hermes/HERMES_BYTE_MANIFEST.json``) when
  present, else falls back to the legacy
  ``/tmp/qubit-differential/byte_manifest/hermes/HERMES_BYTE_MANIFEST.json``;
  the explicit path is overridable via ``QUBIT_DIFFERENTIAL_BYTE_MANIFEST``. The
  gate fails when it is missing, malformed, not at the contractual pin, or when
  any actionable source entry is not ``IDENTICAL`` (``NOT_APPLICABLE`` generated
  artifacts are exempt).

Runner defaults resolve inside the repository (``tests/fixtures/differential/``
and ``docs/differential/``) so a run is reproducible without the untracked
analyst tree. The legacy ``/tmp/qubit-differential/`` support is retained: every
``--inventory``/``--source``/``--fixtures-dir``/``--out`` override and explicit
``/tmp`` path still works. Use ``self-check`` (or ``--self-check``) to validate
the in-repo defaults, the committed seed schema and runner determinism without
executing the inventory, opening a socket or spawning a subprocess.

Exit codes: 0 clean, 1 failure/gate, 2 usage, 3 unclassifiable prerequisite,
4 internal error.
"""

from __future__ import annotations

import argparse
import ast
import base64
import concurrent.futures
import dataclasses
import hashlib
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

__all__ = [
    "ALLOWED_NORMALIZERS",
    "CANONICAL_QUBIT_COMMAND",
    "DEFAULT_REFERENCE_RECHECKS",
    "DECLARED_DISPOSITIONS",
    "DECLARATIONS_FILENAME",
    "Diagnostic",
    "EVIDENCE_FIELDS",
    "NormalizerError",
    "PLUGIN_BUNDLE_LINKAGE",
    "SAFE_EQUIVALENT_PROOF_LEVELS",
    "SUCCESS_DISPOSITIONS",
    "TransportUnavailable",
    "VENDOR_TRANSPORT",
    "VendorParityAdapter",
    "apply_declarations",
    "apply_fail_closed_gates",
    "apply_open_closures",
    "asymmetric_normalizers",
    "canonical_json",
    "check_help_text",
    "classify_state_changing",
    "compare_expected_actual",
    "compare_response_headers",
    "collect_reference_provenance",
    "evaluate_byte_manifest",
    "is_openclaw_delta_row",
    "load_declarations",
    "load_inventory",
    "load_open_closures",
    "missing_evidence_fields",
    "missing_evidence_keys",
    "normalize",
    "normalize_and_audit",
    "resolve_reference_binding",
    "resolves_test_id",
    "result_hash",
    "run_inventory",
    "selftest",
    "state_changing_reasons",
]

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_VERSION = "1"
SCHEMA_VERSION = 1
GENERATED_BY = "scripts/differential_parity.py"
DEFAULT_FIXTURES_DIR = "tests/fixtures/differential"
# --- in-repo deterministic default locations ------------------------------- #
# The runner's defaults resolve INSIDE the repository so a run is reproducible
# without the untracked /tmp analyst tree. Every override still works:
# ``inventory --source``, ``run --inventory``, ``--fixtures-dir``, ``--out``,
# and ``QUBIT_DIFFERENTIAL_BYTE_MANIFEST``. The legacy /tmp locations are kept
# (below and as the byte-manifest fallback) and an explicit /tmp path is always
# accepted, so deleting that support is never required.
DEFAULT_INVENTORY_DIR = REPO_ROOT / "tests" / "fixtures" / "differential" / "inventory"
# Committed, deterministic seed pair. Tiny on purpose: a reproducibility anchor
# (all rows OPEN/undisposed, no fabricated success), not the full inventory.
DEFAULT_SEED_INVENTORY = (
    REPO_ROOT / "tests" / "fixtures" / "differential" / "SEED_INVENTORY.json"
)
DEFAULT_SEED_DECLARATIONS = (
    REPO_ROOT / "tests" / "fixtures" / "differential" / "SEED_DECLARATIONS.json"
)
DEFAULT_REPORTS_DIR = REPO_ROOT / "docs" / "differential" / "reports"
DEFAULT_INVENTORY_OUT = DEFAULT_REPORTS_DIR / "inventory.json"
DEFAULT_REPORT_OUT = DEFAULT_REPORTS_DIR / "differential_report.json"
# Legacy untracked analyst tree: still accepted, never the sole dependency.
LEGACY_INVENTORY_DIR = Path("/tmp/qubit-differential/inventory")
# Typed disposition declarations (B2/B3/B5/B7): a fixture-side overlay that
# classifies an otherwise-OPEN inventory row as OPERATOR_ONLY (or FAIL) with an
# explicit operator_prerequisite and a typed basis. Only a non-success terminal
# disposition may be declared: a declaration can NEVER manufacture PASS or
# SAFE_EQUIVALENT, so it can never fake closure.
DECLARATIONS_FILENAME = "DECLARATIONS.json"
DECLARED_DISPOSITIONS = frozenset({"OPERATOR_ONLY", "FAIL"})
# --- read-only analyst OPEN-closure overlays (wave 22 closure) -------------- #
# Three independent read-only analysts audited the still-OPEN differential rows
# per upstream project and emitted a closure manifest with an exact terminal
# disposition, a candidate proof (a row-linked test reference / vendor behavior
# observation), or an explicit operator prerequisite. The runner CONSUMES those
# manifests as a fail-closed overlay on rows that are still OPEN after every real
# Qubit/upstream execution pass has run:
#   * a success claim becomes SAFE_EQUIVALENT ONLY when a candidate test really
#     resolves AND references the row's own route/symbol/module (the same
#     standard as every other wiring proof) -- never on the analyst's assertion;
#   * everything else becomes a typed OPERATOR_ONLY hold (or FAIL for a recorded
#     defect) with an exact prerequisite; nothing is upgraded to a fake success.
# The frozen copies carry their source sha256 so the overlay is hermetic and
# reproducible without the /tmp analysis tree.
CLOSURE_DIRNAME = "closure"
CLOSURE_FILENAMES = (
    "QUBIT_OPEN_CLOSURE.json",
    "HERMES_OPEN_CLOSURE.json",
    "OPENCLAW_OPEN_CLOSURE.json",
)
CLOSURE_SUCCESS_DISPOSITIONS = frozenset(
    {"SAFE_EQUIVALENT", "IMPLEMENTED", "ADAPTED"}
)
CLOSURE_HOLD_DISPOSITIONS = frozenset(
    {"OPERATOR_ONLY", "OPEN", "IMPLEMENTABLE", "NOT_APPLICABLE", "MISSING"}
)
CLOSURE_DEFECT_DISPOSITIONS = frozenset({"DEFECT"})
CLOSURE_PROOF = "closure_test_reference"
# Vendor-parity transport (B1): executes a vendored upstream module under
# ``voicebridge.qubit_runtime.ensure_path()`` and compares its observed
# behavior (import + symbol signature + public-name hash) against the pinned
# reference root. A PROBE of byte identity is not behavior, so a vendor row can
# never claim PASS on a byte match alone; it is at most SAFE_EQUIVALENT, and only
# when the reference root resolves and the behavioral descriptors are equal.
VENDOR_TRANSPORT = "vendor"
DEFAULT_VENDOR_REF_ROOT_ENV = "QUBIT_DIFFERENTIAL_VENDOR_REF_ROOT"
# --- inventory provenance (fail-closed) ----------------------------------- #
# A directory scan must never let a scratch/backup generation shadow the
# canonical retriage-merged row for the same capability_id. Two rules apply:
#   1. any path SEGMENT starting with "_" is scratch (`_pre_retriage/`,
#      `__pycache__/`, `_*.json`) and is excluded outright;
#   2. the canonical merged artifacts (root files, named below) win over any
#      other file that happens to carry the same capability_id.
INVENTORY_SCRATCH_PREFIX = "_"
# Non-canonical generation outputs. An inventory scan must ingest only the
# canonical SOURCE artifacts so the same sources always yield the same
# inventory, regardless of which scratch/report/inventory generations happen to
# sit in the scan directory:
#   * directories named runner/, recheck*/, prooffix*/, *backup*/, bak/, old/;
#   * any file whose name contains REPORT or RECHECK;
#   * any *_/.* scratch entry or ``.off`` file (already excluded above/below).
# The canonical input set for /tmp/qubit-differential/inventory is therefore:
#   hermes_capabilities.json, hermes_delta_latest.json,
#   openclaw_capabilities.json, openclaw_delta_latest.json,
#   qubit/qubit_backend.json, qubit_transport/qubit_transport_surface.json,
#   dispositions/OPENCLAW_DELTA_RETRIAGE.json,
#   dispositions/openclaw_delta_dispositions.json,
#   dispositions/OPENCLAW_SCANNER_EXCLUSIONS.json (exclusions only),
# plus any explicitly passed single inventory file.
INVENTORY_NON_CANONICAL_DIR_PREFIXES = ("recheck", "prooffix")
INVENTORY_NON_CANONICAL_DIR_NAMES = frozenset(
    {"runner", "backup", "backups", "bak", "old", "qubit_closure_work"}
)
INVENTORY_NON_CANONICAL_DIR_SUFFIXES = ("_backup", "_backups", "_bak")
# ``*_OPEN_CLOSURE.json`` are read-only analyst closure overlays, not inventory
# sources; the runner consumes their frozen copies under
# ``tests/fixtures/differential/closure/``. Ingesting them from the scan tree
# would synthesize ``*.unclassified`` rows and break source-hermeticity.
INVENTORY_NON_CANONICAL_FILENAME_MARKERS = ("REPORT", "RECHECK", "OPEN_CLOSURE")
INVENTORY_NON_CANONICAL_SUFFIXES = (".off",)
CANONICAL_INVENTORY_FILENAMES = frozenset(
    {
        "hermes_capabilities.json",
        "hermes_delta_latest.json",
        "openclaw_capabilities.json",
        "openclaw_delta_latest.json",
    }
)
# HERMETIC allowlist: the exact canonical SOURCE inputs a directory scan may
# ingest, as repo/scan-relative POSIX paths. This is an ALLOWLIST, not a
# marker blocklist, so a stray JSON (a report, an evidence file, a runtime cache
# or an analyst overlay) can never shift the merged inventory counts. An
# explicitly passed single inventory file is still honored (``load_inventory``
# with a file path), which is how hermetic tests load one row.
CANONICAL_INVENTORY_INPUTS = (
    "hermes_capabilities.json",
    "hermes_delta_latest.json",
    "openclaw_capabilities.json",
    "openclaw_delta_latest.json",
    "qubit/qubit_backend.json",
    "qubit_transport/qubit_transport_surface.json",
    "dispositions/OPENCLAW_DELTA_RETRIAGE.json",
    "dispositions/openclaw_delta_dispositions.json",
    "dispositions/OPENCLAW_SCANNER_EXCLUSIONS.json",
)
# Retriage scanner exclusions: rows the merge deliberately never dispositions
# (test-support/test-harness modules), exported only as exclusions. They are not
# product capabilities and must never count as undisposed additions.
SCANNER_EXCLUSION_MARKER = "SCANNER_EXCLUSION"
SCANNER_EXCLUSION_REASON = "test_support_only"
# Bounded `--references-only` diagnostic. The mode executes declared references
# without the Qubit side purely for evidence, so the total number of upstream
# invocations per run is capped; the cap is deterministic (fixtures/rows are
# walked in sorted order) and a truncated run records it on the result.
DEFAULT_REFERENCES_ONLY_LIMIT = 512

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_UNCLASSIFIABLE = 3
EXIT_INTERNAL = 4

FAMILIES = frozenset(
    {
        "cli", "api", "rpc", "ws", "sse", "provider", "session", "memory",
        "file", "project", "skill", "plugin", "mcp", "cron", "gateway",
        "messaging", "browser", "desktop", "board", "task", "media", "acp",
        "webhook", "auth", "approval", "recovery", "dashboard", "voice",
    }
)
DISPOSITIONS = ("PASS", "FAIL", "SAFE_EQUIVALENT", "OPERATOR_ONLY", "OPEN")
VISUAL_FAMILIES = frozenset({"dashboard", "browser", "desktop"})
REFERENCE_PROJECTS = frozenset({"hermes", "openclaw"})
# Contractual pinned upstream revisions. A reference only counts as pinned when
# its resolved commit equals the pin; the floating installed binaries do not.
CONTRACTUAL_PINS = {
    # W9 moved the vendored Hermes pin to the stable v0.21.4 release (owner
    # decisions 2026-09-23 #14/#15). The differential byte manifest is
    # regenerated at this same pin by ``scripts/hermes_byte_manifest.py``.
    "hermes": "d337b736aa1e8ebecfab043842d13e4a2d2f48a3",
    "openclaw": "3a9d69db306cd7f081e06254cb89c4bcc14a7107",
}
# Visual evidence refs required for success rows in VISUAL_FAMILIES.
VISUAL_REF_FIELDS = ("screenshot_ref", "dom_snapshot_ref", "a11y_snapshot_ref")

GATE_NAMES = (
    "unexecuted_row",
    "missing_production_caller",
    "skipped_fixture",
    "mock_replaced_real_path",
    "refusal_counted_as_success",
    "unsafe_normalizer",
    "visual_only_control",
    "voice_backend_mismatch",
    "persistence_or_recovery_diff",
    "secret_in_artifact",
    "openclaw_undisposed_addition",
    "safe_equivalent_requires_proof",
    "proof_level",
    "proof_level_requires_resolvable_test_id",
    "unexecuted_open_row",
    "pass_without_reference",
    "pass_without_proof",
    "unpinned_reference_pass",
    "inventory_coverage",
    "declaration_integrity",
    "byte_parity",
)

# --------------------------------------------------------------------------- #
# State-changing guard (fail-closed, enabled by default)
#
# The runner must never auto-issue a mutating operation against a live Qubit
# instance. A fixture (or a single sequence step) is state-changing when it
# declares ``state_changing: true`` or when an EXECUTABLE field (request path /
# body / argv, sequence path / body / argv, the canonical dashboard op) names a
# built-in denylisted path or mutating action. Without an explicit
# ``--allow-state-changing`` opt-in the row is classified OPERATOR_ONLY and NO
# socket is opened (references and the Qubit side both stay unexecuted).
# --------------------------------------------------------------------------- #
STATE_CHANGING_PATH_MARKERS = (
    "/api/power",        # power control incl. emergency-stop / reset
    "/api/ops/confirm",  # confirmation endpoint for a mutating op
    "/api/ops/run",      # dashboard op dispatch: can run mutating catalogue ops
)
# Console/CLI verbs that mutate canonical state. Deliberately a fail-closed
# allowlist of READ-ONLY verbs is impossible (the command surface is open), so
# this is the classification list; anything in it is state-changing unless the
# fixture explicitly declares ``read_only: true`` (which suppresses only the
# HTTP-method reason, never an explicit path/action marker).
STATE_CHANGING_ACTION_TOKENS = (
    "emergency-stop",
    "emergency_stop",
    "emergencystop",
    "pause", "resume", "power", "engage", "reset", "confirm", "approve",
    "reject", "delete", "remove", "create", "update", "send", "install",
    "uninstall", "restart", "shutdown", "stop", "start", "run-op", "run_op",
    "runop", "restore", "rollback",
)
STATE_CHANGING_TOKEN_PATTERNS = {
    token: re.compile(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])")
    for token in STATE_CHANGING_ACTION_TOKENS
}
# Only GET/HEAD are read-only over HTTP. Any other method (POST/PUT/PATCH/
# DELETE, or an unexpected/unparsed method token) mutates canonical state and is
# blocked without the explicit opt-in. ``read_only: true`` never suppresses the
# mutating-method reason; it may only permit a POST that is a documented safe
# read: a JSON-RPC query (`kind: "rpc"`) or a request that declares one of
# SAFE_READ_METHODS in ``read_method``.
SAFE_HTTP_METHODS = frozenset({"GET", "HEAD"})
# Documented read verbs that make a POST a safe read when declared as the
# request's ``read_method`` (e.g. a list/batch/query read).
SAFE_READ_METHODS = frozenset(
    {"GET", "HEAD", "LIST", "BATCH", "QUERY", "READ", "SEARCH", "FETCH",
     "DESCRIBE", "STATUS", "WATCH", "SUBSCRIBE"}
)
STATE_CHANGING_FLAG = "--allow-state-changing"

# --------------------------------------------------------------------------- #
# SSRF / credential-exfiltration guard (fail-closed)
#
# A fixture may not redirect the runner and its resolved auth token off the
# resolved base host. Absolute request URLs must match the base host, a literal
# ``base_url`` is only accepted for loopback hosts or the operator's
# ``QUBIT_DIFFERENTIAL_<PROJECT>_URL`` host, and the token is never attached to
# any other destination.
# --------------------------------------------------------------------------- #
# Explicit operator allowlist of full origins (scheme://host:port), comma
# separated. A literal ``base_url`` is accepted only when its exact resolved
# origin matches the project env base URL or one of these origins.
ALLOWED_ORIGINS_ENV = "QUBIT_DIFFERENTIAL_ALLOWED_ORIGINS"

# --------------------------------------------------------------------------- #
# Reference nondeterminism (fail-closed)
#
# A reference is nondeterministic when it reports a capability's runtime-selected
# backend — a value chosen from the local environment/network on every
# invocation and therefore not covered by the fixture normalizer allowlist. Its
# payload is kept as evidence but never contributes an unstable hash; a row
# whose upstream proof is nondeterministic can never claim PASS, it is
# OPERATOR_ONLY.
# --------------------------------------------------------------------------- #
_REFERENCE_NONDET_LINE_RE = re.compile(
    r"(?im)^[ \t]*[\u2713\u2714\u2717\u2718\u26a0\u25cf\u25cb\u00b7\u2022\-\*][ \t]+"
    r"[^\n()]{0,80}\b(?:search|extract|embed|rerank|retriev\w*|provider|backend|engine)\b"
    r"[^\n()]{0,40}\(([a-z][a-z0-9_.\-]{1,40})\)[ \t]*$"
)
_REFERENCE_NONDET_REASON = (
    "live environment probe: a runtime-selected provider/capability value "
    "cannot be pinned by the fixture normalizers"
)

# --------------------------------------------------------------------------- #
# Structural reference determinism recheck (fail-closed)
#
# The regex above only catches a KNOWN live-probe line. The contract requires
# determinism (or OPERATOR_ONLY) for EVERY declared reference, so a reference
# that is not already declared nondeterministic is executed a SECOND time and
# both payloads are normalized with the SAME normalizers and hashed; a mismatch
# marks the reference nondeterministic and forces the row OPERATOR_ONLY (never
# PASS). Only side-effect-free CLI/HTTP references already permitted by the
# state-changing guard are rechecked, and the total number of extra executions
# per run is bounded (deterministic: rows/references are walked in sorted
# order). A recheck whose budget is exhausted is fail-closed: the reference is
# not proven stable and cannot back a PASS.
# --------------------------------------------------------------------------- #
DEFAULT_REFERENCE_RECHECKS = 64
REFERENCE_RECHECK_ENV = "QUBIT_DIFFERENTIAL_REFERENCE_RECHECKS"
_REFERENCE_RECHECK_MISMATCH_REASON = (
    "structural determinism recheck: two executions produced different "
    "normalized result hashes"
)

# --------------------------------------------------------------------------- #
# Byte-parity gate (fail-closed)
#
# The deterministic upstream byte manifest is the authoritative source-overlay
# comparison against the contractual pin. Every actionable (non-NOT_APPLICABLE)
# entry must be IDENTICAL; generated/runtime artifacts are exempt. A missing,
# malformed, unpinned or non-identical manifest fails the gate.
# --------------------------------------------------------------------------- #
# In-repo default first; the legacy /tmp manifest is the fallback when the
# in-repo copy is absent (and an explicit QUBIT_DIFFERENTIAL_BYTE_MANIFEST
# override always wins). No location is fabricated: the gate stays fail-closed.
HERMES_BYTE_MANIFEST_NAME = "HERMES_BYTE_MANIFEST.json"
DEFAULT_HERMES_BYTE_MANIFEST = str(
    REPO_ROOT
    / "docs"
    / "differential"
    / "byte_manifest"
    / "hermes"
    / HERMES_BYTE_MANIFEST_NAME
)
LEGACY_HERMES_BYTE_MANIFEST = (
    "/tmp/qubit-differential/byte_manifest/hermes/HERMES_BYTE_MANIFEST.json"
)
BYTE_MANIFEST_ARTIFACT = "HERMES_BYTE_MANIFEST"
BYTE_MANIFEST_EXEMPT_CLASSIFICATIONS = frozenset({"NOT_APPLICABLE"})
BYTE_MANIFEST_MAX_OFFENDERS = 50

# Top-level adapter envelope fields that are legitimate transport plumbing and
# must never be treated as "extra" differences on their own.
ENVELOPE_FIELDS = frozenset(
    {"exit_code", "stdout", "stderr", "http_status", "json", "events",
     "headers", "duration", "persisted_state", "receipts", "meta"}
)

# A disposition that asserts parity (and therefore must be fully evidenced).
SUCCESS_DISPOSITIONS = ("PASS", "SAFE_EQUIVALENT")
# Explicit proof level for a SAFE_EQUIVALENT claim. ``executed`` is a real
# executed comparison (executed result + result hashes); ``wiring`` is a
# resolved Qubit production mapping backed by a real, resolvable test
# reference. A SAFE_EQUIVALENT claim with neither level is not allowed.
SAFE_EQUIVALENT_PROOF_LEVELS = ("executed", "wiring")
# The second, disjoint wiring PROOF TYPE: a frontend/dashboard Control-UI row
# whose mapped component is genuinely exercised by a real vitest suite on disk.
# It is still ``proof_level="wiring"`` (resolution, not live execution), but the
# evidence is a frontend test file under
# ``frontend/qubit-web/src/**/*.test.ts(x)`` that IMPORTS the mapped component
# and references the mapped symbol. Never inferred from prose; the component and
# the test must both exist on disk (see :func:`_frontend_linkage`).
FRONTEND_LINKAGE = "frontend_linkage"
FRONTEND_ROOT = REPO_ROOT / "frontend" / "qubit-web" / "src"
FRONTEND_TEST_SUFFIXES = (".test.ts", ".test.tsx")
FRONTEND_SOURCE_SUFFIXES = (".ts", ".tsx")
# The fourth disjoint wiring PROOF TYPE: a dashboard/plugin row whose mapped
# Qubit surface is a COMPILED, SERVED plugin bundle (a
# ``frontend/qubit-web/public/plugins/<plugin>/dist/*.js`` file). A browser page
# may be a compiled plugin bundle with no ``*.tsx`` source, so the frontend
# linkage cannot see it. The bundle is genuinely exercised when a resolvable
# Python ``tests/`` test FUNCTION reads the served bundle and references the
# row's control/label, and the bundle file exists with a recorded hash. Still
# ``proof_level="wiring"`` (a resolved bundle-read, not a live dashboard
# execution); the bundle content and the reading test must both exist on disk and
# nothing is fabricated (see :func:`_plugin_bundle_linkage`).
PLUGIN_BUNDLE_LINKAGE = "plugin_bundle_linkage"
SERVED_PLUGIN_ROOT = REPO_ROOT / "frontend" / "qubit-web" / "public" / "plugins"
SERVED_PLUGIN_BUNDLE_SUFFIXES = (".js", ".mjs")
# The third disjoint wiring PROOF TYPE: a vendored upstream source row whose
# mapped module really imports through ``qubit_runtime.ensure_path()`` and
# whose behavioral descriptor (import success, public API names hash, symbol
# kind/signature) equals the pinned reference root. It is ``proof_level=wiring``
# (module-surface comparison, not the row's own end-to-end production path) and
# is NEVER inferred from a byte/catalog count: both modules must import and the
# descriptors must be equal. A missing import or an unresolved reference root is
# fail-closed (row stays OPEN / OPERATOR_ONLY).
VENDOR_PROOF = "vendor_behavior"
# The fifth/sixth disjoint proof types: a Qubit-native CLI row executed through
# the REAL ``qubit`` launcher, and (for an upstream CLI row) the same
# command compared to the pinned upstream CLI. ``cli_execution`` is
# ``proof_level="executed"`` (the production launcher really ran and produced a
# result hash; a Qubit-native addition has no upstream counterpart). ``cli_parity``
# additionally requires a real pinned reference run whose normalized help is
# byte-equal, so it carries ``reference_executed=true`` and both hashes. Never
# inferred from a catalog/help listing alone: the process must exit 0 and emit the
# command's help surface.
CLI_EXECUTION = "cli_execution"
CLI_PARITY = "cli_parity"
# The seventh disjoint proof type: a Qubit voice row whose canonical operation
# really resolves through the production voice registry (``voice_commands.resolve``)
# from one of the entry's own declared examples. Pure and side-effect-free; the
# resolved entry id must equal the row's declared op id (a clarify/match counts,
# an unknown/ambiguous resolution does not).
VOICE_RESOLUTION = "voice_resolution"
# Bounded worker pool for the CLI launcher probe (a subprocess per row). Fixed
# so the collected result set is deterministic regardless of completion order.
CLI_PROBE_WORKERS = 16
VENDOR_FORK_DIRNAME = "qubit_runtime"
# Separator for the batch descriptor map key ``module<US>symbol`` (U+001F can
# never appear in a Python module path or identifier).
VENDOR_KEY_SEP = "\x1f"
# Sources used to resolve a wiring-level proof deterministically: the Python
# test tree (AST) and the owner ledger for the upstream latest-main delta.
OWNER_LEDGER_PATH = REPO_ROOT / "docs/generated" / "QUBIT_GAP_LEDGER.openclaw.json"
TESTS_DIR = REPO_ROOT / "tests"
# Broad shared modules: a ledger entry naming one of these only counts for a
# row when it also names the row's symbol (otherwise every qubit_compat row
# would inherit the same proving test).
SHARED_QUBIT_MODULE_STEMS = frozenset({"qubit_compat", "cli", "webui"})
# Terminal dispositions: an upstream delta row is "disposed" once it is any of
# these (OPEN means the addition has not been dispositioned at all).
TERMINAL_DISPOSITIONS = ("PASS", "FAIL", "SAFE_EQUIVALENT", "OPERATOR_ONLY")

# INTERFACE v1.1 §"Plan §8/§15 coverage additions": every row/evidence result
# must carry these keys; they must be populated for PASS/SAFE_EQUIVALENT rows.
EVIDENCE_FIELDS = (
    "voice_utterance",
    "dashboard_route",
    "dashboard_control",
    "request_schema",
    "broker_policy_decision",
    "runtime_provider",
    "store_state_assertion",
    "output_event_assertion",
    "error_codes",
    "retry_recovery_proof",
    "audit_receipt_proof",
    "security_resource_proof",
    "package_proof",
    "response_headers",
    "screenshot_ref",
    "dom_snapshot_ref",
    "a11y_snapshot_ref",
    "raw_hash",
)
# The canonical Qubit CLI command (`voicebridge`) that CLI help must advertise.
CANONICAL_QUBIT_COMMAND = "voicebridge"

# Upstream latest-release/default-branch delta detection. A row is a delta row
# when it tracks the upstream default branch but not a pinned 40-hex commit (e.g. a release tag or
# the default branch), or when it explicitly marks a delta source.
_OPENCLAW_DELTA_CHANNELS = frozenset(
    {"latest", "latest-release", "release", "default", "default-branch",
     "main", "master", "head", "trunk"}
)
_PINNED_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")


# --------------------------------------------------------------------------- #
# Hashing
# --------------------------------------------------------------------------- #
def canonical_json(obj: Any) -> str:
    """Canonical JSON per the frozen contract (deterministic across machines)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def result_hash(payload: Any) -> str:
    """``"sha256:" + sha256(canonical_json(payload))`` exactly as specified."""
    return "sha256:" + sha256_hex(canonical_json(payload).encode("utf-8"))


def file_hash(path: str | Path) -> str:
    """Raw-bytes hash, matching the fixture MANIFEST ``sha256`` convention."""
    return "sha256:" + sha256_hex(Path(path).read_bytes())


# --------------------------------------------------------------------------- #
# Secret handling
# --------------------------------------------------------------------------- #
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|secret|password|passwd|access[_-]?token|auth[_-]?token)"
        r"\b\s*[:=]\s*\S{8,}"
    ),
)

_REF_RE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")
_ENV_REF_RE = re.compile(r"^env:([A-Za-z_][A-Za-z0-9_]*)$")


def contains_secret(value: Any) -> bool:
    if isinstance(value, str):
        return any(p.search(value) for p in SECRET_PATTERNS)
    if isinstance(value, Mapping):
        return any(contains_secret(k) or contains_secret(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_secret(v) for v in value)
    return False


def redact_text(text: str) -> str:
    out = text
    for pat in SECRET_PATTERNS:
        out = pat.sub("<redacted>", out)
    return out


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            (redact_text(k) if isinstance(k, str) else k): redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _short(value: Any, limit: int = 200) -> str:
    try:
        text = canonical_json(redact(value))
    except (TypeError, ValueError):
        text = repr(redact(value))
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return redact_text(text)


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _child_path(path: str, key: Any) -> str:
    if isinstance(key, str) and _IDENT_RE.match(key):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(str(key), ensure_ascii=True)}]"


def scan_secrets(obj: Any, path: str = "$") -> list[str]:
    """Return the JSON paths where secret-shaped values appear (never values)."""
    found: list[str] = []
    if isinstance(obj, Mapping):
        for key in sorted(obj, key=str):
            if contains_secret(str(key)):
                found.append(_child_path(path, key))
            found.extend(scan_secrets(obj[key], _child_path(path, key)))
    elif isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            found.extend(scan_secrets(item, f"{path}[{i}]"))
    elif isinstance(obj, str) and contains_secret(obj):
        found.append(path)
    return found


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class Diagnostic:
    path: str
    kind: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "kind": self.kind, "detail": redact_text(self.detail)}


def _diag(path: str, kind: str, detail: str) -> Diagnostic:
    return Diagnostic(path=path, kind=kind, detail=redact_text(detail))


# --------------------------------------------------------------------------- #
# Normalizers (exact allowlist)
# --------------------------------------------------------------------------- #
class NormalizerError(ValueError):
    """Raised for any normalizer key outside the frozen allowlist."""


_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([Tt ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?"
    r"(Z|z|[+-]\d{2}:?\d{2})?)?$"
)
_RFC2822_RE = re.compile(
    r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+\d{1,2}\s+"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}\s+"
    r"\d{2}:\d{2}(:\d{2})?\s+(GMT|UTC|[+-]\d{4})$"
)
_TIME_KEYS = frozenset(
    {"timestamp", "time", "ts", "date", "datetime", "created_at", "updated_at",
     "started_at", "finished_at", "expires_at", "expiry", "epoch", "mtime"}
)
# Time-ish key suffixes recognised both at the top level and inside stringified
# JSON (e.g. ``generated_at``, ``wall_time``, ``time_ns``).
_TIME_KEY_SUFFIXES = ("_at", "_time", "_ns", "_ts")
# ISO-8601 datetimes embedded inside a larger string (e.g. a JSON blob).
_EMBEDDED_ISO_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|z|[+-]\d{2}:?\d{2})?"
)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_TEMP_PREFIXES = (
    "/tmp/", "/var/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/",
)
_PID_KEYS = frozenset({"pid", "ppid", "process_id", "proc_id", "child_pid", "parent_pid"})
_TOKEN_KEYS = frozenset(
    {"session_token", "token", "access_token", "refresh_token", "auth_token",
     "bearer", "bearer_token", "session_secret"}
)
_TOKEN_VAL_RE = re.compile(r"^(sess|tok|sess_|tok_)[A-Za-z0-9._\-]{8,}$")
_HASH_VAL_RE = re.compile(r"^(sha256:)?[0-9a-f]{32,64}$")
_HASH_KEYS = ("hash", "sha", "digest", "checksum", "etag", "md5", "signature")
_BRAND_RE = re.compile(r"\b(hermes|openclaw|qubit)\b", re.IGNORECASE)


def _is_time_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    low = key.lower()
    return low in _TIME_KEYS or low.endswith(_TIME_KEY_SUFFIXES)


def _is_epoch_candidate(key: Any, value: Any) -> bool:
    if not _is_time_key(key):
        return False
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    magnitude = abs(value)
    if isinstance(key, str) and key.lower().endswith("_ns"):
        # nanosecond epochs are ~1e18
        return 1_000_000_000_000_000 <= magnitude <= 10_000_000_000_000_000_000
    return 1_000_000_000 <= magnitude <= 10_000_000_000_000


def _replace_embedded_iso(text: str) -> str:
    """Replace ISO-8601 datetimes embedded inside a larger string."""
    return _EMBEDDED_ISO_RE.sub("<timestamp>", text)


def _normalize_time_tree(node: Any, key: Any = None) -> Any:
    """Recursively collapse time values inside a parsed JSON structure."""
    if isinstance(node, Mapping):
        return {child_key: _normalize_time_tree(value, child_key)
                for child_key, value in node.items()}
    if isinstance(node, (list, tuple)):
        return [_normalize_time_tree(value, None) for value in node]
    if key is not None and _is_time_key(key) and node is not None:
        if isinstance(node, bool):
            return node
        if isinstance(node, (int, float)):
            return "<timestamp>" if _is_epoch_candidate(key, node) else node
        if isinstance(node, str):
            if _ISO_RE.match(node) or _RFC2822_RE.match(node):
                return "<timestamp>"
            return _replace_embedded_iso(node)
        return node
    if isinstance(node, str):
        return _replace_embedded_iso(node)
    return node


def _timestamp_string_transform(text: str, key: Any = None) -> tuple[bool, str]:
    """Normalize a string that may itself contain stringified JSON timestamps."""
    parsed = _parse_json_maybe(text)
    if isinstance(parsed, (dict, list)):
        normalized = _normalize_time_tree(parsed, key)
        if normalized != parsed:
            return True, json.dumps(
                normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
        return False, text
    replaced = _replace_embedded_iso(text)
    return replaced != text, replaced


def _shape_timestamp(key: Any, value: Any) -> bool:
    if isinstance(value, str):
        if _ISO_RE.match(value) or _RFC2822_RE.match(value):
            return True
        changed, _ = _timestamp_string_transform(value, key)
        return changed
    return _is_epoch_candidate(key, value)


def _norm_timestamp(value: Any, key: Any = None) -> Any:
    """Collapse timestamps, including ones embedded inside stringified JSON.

    A whole ISO/RFC value or a time-keyed epoch becomes ``<timestamp>``; a
    string that parses as JSON has its time-keyed and embedded ISO values
    collapsed, so identical logical results hash identically across runs.
    """
    if isinstance(value, str):
        if _ISO_RE.match(value) or _RFC2822_RE.match(value):
            return "<timestamp>"
        changed, replaced = _timestamp_string_transform(value, key)
        return replaced if changed else value
    if _is_epoch_candidate(key, value):
        return "<timestamp>"
    return value


def _shape_uuid(key: Any, value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value))


def _norm_uuid(value: Any, key: Any = None) -> Any:
    """Collapse canonical UUID strings only."""
    return "<uuid>" if _shape_uuid(key, value) else value


def _temp_roots() -> tuple[str, ...]:
    roots = list(_TEMP_PREFIXES)
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        roots.append(tmpdir.rstrip("/") + "/")
    return tuple(roots)


def _shape_temp_path(key: Any, value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return any(value.startswith(prefix) for prefix in _temp_roots())


def _norm_temp_path(value: Any, key: Any = None) -> Any:
    """Collapse absolute paths rooted under a known temp directory."""
    return "<temp_path>" if _shape_temp_path(key, value) else value


def _shape_pid(key: Any, value: Any) -> bool:
    if not isinstance(key, str):
        return False
    low = key.lower()
    if low not in _PID_KEYS and not low.endswith("_pid"):
        return False
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, str) and value.isdigit()


def _norm_pid(value: Any, key: Any = None) -> Any:
    """Collapse process ids, but only under explicitly pid-shaped keys."""
    return "<pid>" if _shape_pid(key, value) else value


def _shape_session_token(key: Any, value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if isinstance(key, str) and key.lower() in _TOKEN_KEYS:
        return len(value) >= 8
    return bool(_TOKEN_VAL_RE.match(value))


def _norm_session_token(value: Any, key: Any = None) -> Any:
    """Collapse opaque session tokens by documented key or token shape."""
    return "<session_token>" if _shape_session_token(key, value) else value


def _shape_generated_hash(key: Any, value: Any) -> bool:
    if not isinstance(value, str) or not _HASH_VAL_RE.match(value):
        return False
    if value.startswith("sha256:"):
        return True
    low = key.lower() if isinstance(key, str) else ""
    return any(token in low for token in _HASH_KEYS)


def _norm_generated_hash(value: Any, key: Any = None) -> Any:
    """Collapse hex digests only under hash-ish keys or ``sha256:`` values."""
    return "<generated_hash>" if _shape_generated_hash(key, value) else value


def _shape_product_branding(key: Any, value: Any) -> bool:
    return isinstance(value, str) and bool(_BRAND_RE.search(value))


def _norm_product_branding(value: Any, key: Any = None) -> Any:
    """Collapse upstream/product brand words to a single placeholder."""
    if not _shape_product_branding(key, value):
        return value
    return _BRAND_RE.sub("<product>", value)


ALLOWED_NORMALIZERS: dict[str, Callable[..., Any]] = {
    "timestamp": _norm_timestamp,
    "uuid": _norm_uuid,
    "temp_path": _norm_temp_path,
    "pid": _norm_pid,
    "session_token": _norm_session_token,
    "generated_hash": _norm_generated_hash,
    "product_branding": _norm_product_branding,
}

_NORMALIZER_SHAPES: dict[str, Callable[[Any, Any], bool]] = {
    "timestamp": _shape_timestamp,
    "uuid": _shape_uuid,
    "temp_path": _shape_temp_path,
    "pid": _shape_pid,
    "session_token": _shape_session_token,
    "generated_hash": _shape_generated_hash,
    "product_branding": _shape_product_branding,
}

def normalize_and_audit(
    obj: Any,
    normalizers: Sequence[str],
    registry: Mapping[str, Callable[..., Any]] | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    """Normalize ``obj`` and return ``(normalized, audit_events)``.

    Every normalizer key is validated against the allowlist first. Each audit
    event records where a transform actually changed a value so that
    :func:`detect_unsafe_normalizers` can flag out-of-shape collapses.
    """
    reg = ALLOWED_NORMALIZERS if registry is None else registry
    for name in normalizers:
        if name not in ALLOWED_NORMALIZERS:
            raise NormalizerError(f"unknown normalizer: {name!r}")
        if name not in reg:
            raise NormalizerError(f"normalizer not registered: {name!r}")
    audit: list[dict[str, Any]] = []

    def walk(value: Any, path: str, key: Any) -> Any:
        if isinstance(value, Mapping):
            return {k: walk(v, _child_path(path, k), k) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [walk(v, f"{path}[{i}]", key) for i, v in enumerate(value)]
        out = value
        for name in normalizers:
            fn = reg[name]
            try:
                new = fn(out, key)
            except TypeError:
                new = fn(out)
            if new != out:
                audit.append(
                    {"normalizer": name, "path": path, "key": key,
                     "before": out, "after": new}
                )
                out = new
        return out

    return walk(obj, "$", None), audit


def normalize(obj: Any, normalizers: Sequence[str]) -> Any:
    """Public normalizer entry point; returns only the normalized object."""
    normalized, _ = normalize_and_audit(obj, normalizers)
    return normalized


def detect_unsafe_normalizers(audit: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return audit events where a normalizer collapsed an out-of-shape value."""
    unsafe: list[dict[str, Any]] = []
    for event in audit:
        shape = _NORMALIZER_SHAPES.get(event["normalizer"])
        if shape is None:
            unsafe.append(dict(event))
            continue
        if not shape(event["key"], event["before"]):
            unsafe.append(dict(event))
    return unsafe


# --------------------------------------------------------------------------- #
# Comparators
# --------------------------------------------------------------------------- #
_NONDETERMINISTIC_HEADERS = frozenset(
    {"date", "server", "x-request-id", "x-trace-id", "x-amzn-trace-id",
     "etag", "last-modified", "cf-ray", "x-runtime", "x-correlation-id",
     "traceparent", "tracestate", "x-amzn-requestid", "x-request-start",
     "x-response-time", "x-served-by", "x-cache", "age",
     "nonce", "x-nonce", "x-request-nonce",
     # WebSocket handshake fields derived from the client's random key.
     "sec-websocket-accept", "sec-websocket-key", "sec-websocket-extensions"}
)
# Placeholder substituted for a volatile transport field before hashing.
_NONDETERMINISTIC_PLACEHOLDER = "<nondeterministic>"


def _canonical_transport_value(value: Any) -> Any:
    """Collapse a volatile transport field to a stable placeholder."""
    if isinstance(value, (Mapping, list, tuple)):
        return value
    return _NONDETERMINISTIC_PLACEHOLDER


def canonical_transport_headers(headers: Any) -> dict[str, Any]:
    """Copy headers, replacing volatile values with a stable placeholder.

    Applied by the transport adapters themselves so both the raw and the
    normalized result hashes are deterministic; comparison already ignores the
    same header names via :data:`_NONDETERMINISTIC_HEADERS`.
    """
    if not isinstance(headers, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key, value in headers.items():
        name = str(key)
        if name.lower() in _NONDETERMINISTIC_HEADERS:
            out[name] = _canonical_transport_value(value)
        else:
            out[name] = value
    return out


# Response keys whose numeric values are live host metrics (never a parity
# contract). They are collapsed at the transport boundary so the result hash is
# deterministic across runs.
_VOLATILE_METRIC_KEY_RE = re.compile(
    r"(?i)(?:^|_)(free|used|total|available|cached|buffers?|rss|vsz|mem|memory"
    r"|cpu|load|uptime|disk|swap|inode)(?:_|$)"
    r"|(?:_mb|_kb|_gb|_bytes|_percent|_pct)$"
)
_VOLATILE_METRIC_PLACEHOLDER = "<volatile-metric>"


def canonicalize_volatile_metrics(obj: Any) -> Any:
    """Collapse live host-metric values (free_mb, uptime, …) in a parsed body."""
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if (_VOLATILE_METRIC_KEY_RE.search(str(key))
                    and isinstance(value, (int, float)) and not isinstance(value, bool)):
                out[key] = _VOLATILE_METRIC_PLACEHOLDER
            else:
                out[key] = canonicalize_volatile_metrics(value)
        return out
    if isinstance(obj, (list, tuple)):
        return [canonicalize_volatile_metrics(item) for item in obj]
    return obj


def canonicalize_stream_data(data: Any) -> Any:
    """Reduce streamed event payloads to a stable structural projection.

    A live model stream is not reproducible token-for-token, so the parity
    contract for SSE/WS messages is the event type/order and JSON shape, not the
    volatile text. Non-JSON payloads collapse to a fixed marker.
    """
    parsed = _parse_json_maybe(data)
    if isinstance(parsed, Mapping):
        projection: dict[str, Any] = {"keys": sorted(str(k) for k in parsed)}
        if "event" in parsed:
            projection["event"] = parsed["event"]
        return canonical_json(projection)
    if isinstance(parsed, (list, tuple)):
        return canonical_json({"keys": ["<list>"]})
    return json.dumps("<stream-data>", ensure_ascii=True)


def _canonical_stream_event(event: Any) -> Any:
    if not isinstance(event, Mapping):
        return event
    canonical = dict(event)
    if event.get("data") is not None:
        canonical["data"] = canonicalize_stream_data(event["data"])
    return canonical


def _same_scalar(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return expected == actual
    if expected is None or actual is None:
        return expected is actual
    if isinstance(expected, str) and isinstance(actual, str):
        return expected == actual
    return type(expected) is type(actual) and expected == actual


def _parse_json_maybe(text: Any) -> Any:
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except (ValueError, TypeError):
        return None


def _compare_headers(expected: Any, actual: Any, path: str, diags: list[Diagnostic]) -> None:
    e = {(k.lower() if isinstance(k, str) else k): v for k, v in (expected or {}).items()}
    a = {(k.lower() if isinstance(k, str) else k): v for k, v in (actual or {}).items()}
    e = {k: v for k, v in e.items() if k not in _NONDETERMINISTIC_HEADERS}
    a = {k: v for k, v in a.items() if k not in _NONDETERMINISTIC_HEADERS}
    for key in sorted(set(e) | set(a), key=str):
        if key not in a:
            diags.append(_diag(_child_path(path, key), "missing_header", "header absent"))
        elif key not in e:
            diags.append(_diag(_child_path(path, key), "extra_header", "unexpected header"))
        elif not _same_scalar(e[key], a[key]):
            diags.append(
                _diag(_child_path(path, key), "header",
                      f"expected={_short(e[key])} actual={_short(a[key])}")
            )


def _compare_set(expected: Any, actual: Any, path: str, diags: list[Diagnostic]) -> None:
    e = {str(x) for x in (expected or [])}
    a = {str(x) for x in (actual or [])}
    for item in sorted(e - a):
        diags.append(_diag(_child_path(path, item), "missing_scope", f"missing {item!r}"))
    for item in sorted(a - e):
        diags.append(_diag(_child_path(path, item), "extra_scope", f"extra {item!r}"))


def _compare_node(
    expected: Any,
    actual: Any,
    path: str,
    diags: list[Diagnostic],
    template: bool,
    normalizers: Sequence[str] | None = None,
) -> None:
    """Compare an expected contract against actual, reporting exact paths.

    A mapping asserts only the keys present in ``expected`` unless it carries
    ``"closed": true``, in which case extra actual keys are differences. This
    keeps the adapter envelope (stdout/stderr/exit_code/...) out of the diff
    while still supporting strict objects. ``*_schema`` constructs and
    ``closed`` are honoured at any depth; ``stdout``/``stderr`` JSON is parsed
    and normalized before comparison.
    """
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            diags.append(_diag(path, "type", f"expected object, actual {type(actual).__name__}"))
            return
        closed = bool(expected.get("closed"))
        # ``help_contains`` is a family directive consumed by
        # :func:`_family_diagnostics` (it asserts the Qubit help text advertises
        # the canonical command); it is not a literal key of the result payload.
        declared_keys = [key for key in expected if key not in ("closed", "help_contains")]
        for key in sorted(declared_keys, key=str):
            if key in ("stdout_schema", "stderr_schema", "json_schema") and key not in actual:
                continue
            if key not in actual:
                diags.append(_diag(_child_path(path, key), "missing", "key absent"))
        if closed:
            for key in sorted(actual, key=str):
                if key in expected or key == "closed":
                    continue
                if key == "json" and "json_schema" in expected:
                    continue
                if key in ("stdout", "stderr") and f"{key}_schema" in expected:
                    continue
                diags.append(_diag(_child_path(path, key), "extra", "unexpected key"))
        for key in sorted(declared_keys, key=str):
            child = _child_path(path, key)
            if key in ("stdout_schema", "stderr_schema"):
                stream = key[: -len("_schema")]
                if stream not in actual:
                    continue
                parsed = _parse_json_maybe(actual[stream])
                if parsed is None:
                    if expected[key]:
                        diags.append(
                            _diag(child, "stdout_parse", f"{stream} is not JSON as required")
                        )
                else:
                    if normalizers:
                        parsed = normalize(parsed, list(normalizers))
                    _compare_node(expected[key], parsed, child, diags, True, normalizers)
                continue
            if key == "json_schema":
                if "json" not in actual:
                    continue
                target = actual["json"]
                if normalizers:
                    target = normalize(target, list(normalizers))
                _compare_node(expected[key], target, child, diags, True, normalizers)
                continue
            if key == "headers":
                _compare_headers(expected[key], actual.get(key, {}), child, diags)
                continue
            if key == "scopes":
                _compare_set(expected[key], actual.get(key, []), child, diags)
                continue
            if key not in actual:
                continue
            _compare_node(expected[key], actual[key], child, diags, template, normalizers)
        return

    if isinstance(expected, (list, tuple)):
        if not isinstance(actual, (list, tuple)):
            diags.append(_diag(path, "type", f"expected list, actual {type(actual).__name__}"))
            return
        if template:
            for i, item in enumerate(expected):
                if i < len(actual):
                    _compare_node(item, actual[i], f"{path}[{i}]", diags, True, normalizers)
                else:
                    diags.append(_diag(f"{path}[{i}]", "missing", "template item absent"))
            return
        if len(expected) != len(actual):
            diags.append(
                _diag(path, "length", f"expected {len(expected)} items, actual {len(actual)}")
            )
        for i in range(min(len(expected), len(actual))):
            _compare_node(expected[i], actual[i], f"{path}[{i}]", diags, False, normalizers)
        return

    if not _same_scalar(expected, actual):
        diags.append(
            _diag(path, "value", f"expected={_short(expected)} actual={_short(actual)}")
        )


def compare_expected_actual(
    expected: Any,
    actual: Any,
    path: str = "$",
    normalizers: Sequence[str] | None = None,
) -> list[Diagnostic]:
    """Structured SYMMETRIC diff of expected vs actual, reporting exact paths.

    When ``normalizers`` is given it is applied to BOTH sides before comparing
    (never one side only). Only keys present in ``expected`` are asserted unless
    the expected object marks itself ``"closed": true``; parsed ``stdout`` /
    ``stderr`` JSON is normalized too. ``headers`` ignore nondeterminism,
    ``scopes`` compare as sets, and nested/event/persisted-state recursion is
    unchanged.
    """
    if normalizers:
        expected = normalize(expected, list(normalizers))
        actual = normalize(actual, list(normalizers))
    diags: list[Diagnostic] = []
    _compare_node(expected, actual, path, diags, False, normalizers)
    return diags


_MISSING = object()


def _jsonpath(parts: Sequence[Any]) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        elif isinstance(part, str) and _IDENT_RE.match(part):
            path += f".{part}"
        else:
            path += f"[{json.dumps(str(part), ensure_ascii=True)}]"
    return path


def _raw_at_path(obj: Any, parts: Sequence[Any]) -> Any:
    cur = obj
    for part in parts:
        if isinstance(part, int):
            if not isinstance(cur, (list, tuple)) or part >= len(cur):
                return _MISSING
            cur = cur[part]
        else:
            if not isinstance(cur, Mapping) or part not in cur:
                return _MISSING
            cur = cur[part]
    return cur


def _normalizer_change_paths(obj: Any, normalizers: Sequence[str]) -> set[tuple]:
    """JSON-path tuples where a normalizer actually changed a value."""
    changed: set[tuple] = set()

    def walk(value: Any, path: tuple, key: Any) -> None:
        if isinstance(value, Mapping):
            for child_key, child_value in value.items():
                walk(child_value, path + (child_key,), child_key)
        elif isinstance(value, (list, tuple)):
            for index, child_value in enumerate(value):
                walk(child_value, path + (index,), key)
        else:
            out = value
            for name in normalizers:
                fn = ALLOWED_NORMALIZERS[name]
                try:
                    new = fn(out, key)
                except TypeError:
                    new = fn(out)
                if new != out:
                    changed.add(path)
                    out = new

    walk(obj, (), None)
    return changed


def asymmetric_normalizers(
    expected: Any, actual: Any, normalizers: Sequence[str] | None
) -> list[str]:
    """Paths where a normalizer collapsed exactly one side's differing value.

    Normalization must be symmetric; a one-sided collapse that hides a
    difference is an ``unsafe_normalizer`` trigger.
    """
    names = [name for name in (normalizers or []) if name in ALLOWED_NORMALIZERS]
    if not names:
        return []
    expected_paths = _normalizer_change_paths(expected, names)
    actual_paths = _normalizer_change_paths(actual, names)
    flagged: list[str] = []
    for path in sorted(expected_paths ^ actual_paths, key=str):
        expected_raw = _raw_at_path(expected, path)
        actual_raw = _raw_at_path(actual, path)
        if expected_raw is _MISSING or actual_raw is _MISSING:
            continue
        if expected_raw != actual_raw:
            flagged.append(_jsonpath(path))
    return sorted(flagged)


def compare_response_headers(
    expected: Any, actual: Any, path: str = "$"
) -> list[Diagnostic]:
    """Compare HTTP response headers, ignoring nondeterministic ones.

    Only headers outside :data:`_NONDETERMINISTIC_HEADERS` are compared, so a
    changing ``Date``/``Server``/``ETag``/trace id never reports a difference,
    while a real ``Content-Type``/``Location``/wire-contract change does.
    """
    diags: list[Diagnostic] = []
    _compare_headers(expected, actual, path, diags)
    return diags


def _extract_help_text(actual: Any) -> str:
    """Pull help text out of a string or a normalized adapter payload."""
    if isinstance(actual, str):
        return actual
    if isinstance(actual, Mapping):
        for key in ("help", "help_text", "usage", "stdout"):
            value = actual.get(key)
            if isinstance(value, str):
                return value
    return ""


def check_help_text(
    actual: Any,
    required_substrings: Sequence[str] | None = None,
    path: str = "$.help",
) -> list[Diagnostic]:
    """Assert CLI help text advertises required markers (canonical command).

    ``actual`` may be the raw help string / stdout or a normalized result
    payload. Missing markers are reported as ``help_missing`` diagnostics at
    the exact help path; this is fail-closed (absence is a difference).
    """
    if required_substrings is None:
        required_substrings = (CANONICAL_QUBIT_COMMAND,)
    text = _extract_help_text(actual)
    diags: list[Diagnostic] = []
    for needle in required_substrings:
        if not needle:
            continue
        if needle not in text:
            diags.append(
                _diag(path, "help_missing",
                      f"help text does not contain canonical command {needle!r}")
            )
    return diags


def is_openclaw_delta_row(row: Mapping[str, Any]) -> bool:
    """True when a row tracks the upstream latest-release/default-branch delta.

    Pinned upstream rows (40-hex ``upstream_commit``) are contractual, not
    delta, rows. Anything tracking a release tag, a branch name, a mutable ref
    or an explicit delta marker is a delta row and must be dispositioned.
    """
    if str(row.get("upstream_project") or "").lower() != "openclaw":
        return False
    if row.get("openclaw_delta") is True or row.get("delta") is True:
        return True
    if row.get("undisposed") is True:
        return True
    # Explicit latest-vs-pin delta markers emitted by the upstream delta scan.
    if str(row.get("delta_role") or "").strip():
        return True
    if str(row.get("delta_vs_pin_commit") or "").strip():
        return True
    if str(row.get("delta_note") or "").strip():
        return True
    for key in ("delta_channel", "delta_source", "source_channel", "upstream_ref",
                "upstream_branch", "branch", "channel", "release_channel"):
        value = str(row.get(key) or "").strip().lower()
        if value and (value in _OPENCLAW_DELTA_CHANNELS
                      or not _PINNED_COMMIT_RE.match(value)):
            return True
    commit = str(row.get("upstream_commit") or "").strip().lower()
    if not commit:
        return True
    return not _PINNED_COMMIT_RE.match(commit)


# --------------------------------------------------------------------------- #
# Wiring-level SAFE_EQUIVALENT proof resolution (deterministic, no invented ids)
#
# Proof LEVELS (module contract):
#   executed -> a real executed comparison (executed result + result hashes).
#   wiring   -> a resolved Qubit production mapping backed by a REAL, resolvable
#               ``tests/<file>.py::<test_fn>`` reference. The reference is only
#               ever a test that exists on disk (AST index) and, for the upstream
#               delta, is corroborated by the owner ledger. No test id is ever
#               manufactured: an unresolved citation is not proof.
#
# Two distinct gates consume this:
#   * ``safe_equivalent_requires_proof`` keeps the runner's historical standard:
#     a Qubit mapping plus recon keys (disposition_proof / verification) or a
#     declared test reference. It is the F2/retriage-compatibility gate.
#   * ``proof_level`` / ``proof_level_requires_resolvable_test_id`` enforce the
#     explicit level, and ``_has_qubit_disposition_and_proof`` additionally
#     requires a REAL test or executed proof for an upstream delta row (a bare
#     mapping is not disposal).
#
# ROUTE/SYMBOL LINKAGE: a cited test is proof only when the SPECIFIC test
# function references the row's route or symbol (a symbol call/attribute, a
# direct route string, or membership in a route table the function iterates).
# A class-level surface test that merely resolves is not proof for an
# individual route: such a row is left OPEN with
# ``wiring_proof_status="no_row_linkage"``.
# --------------------------------------------------------------------------- #
_TEST_INDEX_CACHE: set[str] | None = None
_REFERENCE_INDEX_CACHE: (
    tuple[
        dict[str, set[str]],
        dict[str, dict[str, set[str]]],
        dict[str, set[str]],
        dict[str, dict[str, set[str]]],
        dict[str, list[str]],
    ]
    | None
) = None
_OWNER_LEDGER_CACHE: list[dict[str, Any]] | None = None
# Whole-file token index for route/symbol-level row linkage (see
# :func:`_test_file_linkage_index`). Kept separate from the import/reference
# index so the linkage rule can be reasoned about (and tested) on its own.
_TEST_LINKAGE_CACHE: dict[str, dict[str, frozenset[str]]] | None = None
# Frontend vitest linkage index (see :func:`_frontend_test_index`).
_FRONTEND_TEST_INDEX_CACHE: dict[str, dict[str, Any]] | None = None

# Symbol names too ambiguous to tie a Qubit capability to a test on a bare name
# collision alone. ``gap_ledger.main`` in an unrelated test must never prove
# ``voicebridge.terminal:main``. A generic symbol is admissible only with a
# STRONG linkage: ``<qubit-module>.symbol`` (resolved through an import alias),
# ``from <qubit-module> import symbol``, or an owner-ledger ``proving_test``
# explicitly naming the test file for that module/symbol.
_GENERIC_QUBIT_SYMBOLS = frozenset(
    {
        "main", "run", "start", "stop", "restart", "reload", "init", "setup",
        "process", "handle", "execute", "call", "apply", "invoke", "act",
        "create", "delete", "update", "get", "set", "list", "check", "load",
        "save", "read", "write", "open", "close", "send", "recv", "next",
        "reset", "clear", "parse", "build", "dispatch", "info", "help",
        "version", "default", "value", "name", "data", "config", "state",
        "result", "output", "input", "status", "entries", "index", "item",
    }
)


def _qubit_module_dotted(qubit_path: str) -> str:
    """``voicebridge/terminal.py:382`` -> ``voicebridge.terminal`` (or "")."""
    path_part = str(qubit_path).split(":", 1)[0].strip()
    if not path_part.endswith(".py"):
        return ""
    return path_part[:-3].replace("/", ".").strip(".")


def _dotted_name(node: ast.AST) -> str:
    """Best-effort dotted source name of an expression (Name/Attribute only)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _imports_module(
    module: str, imported: set[str], bindings: Mapping[str, set[str]]
) -> bool:
    """Whether the module (or a namespace binding to it) is imported."""
    if not module:
        return False
    if module in imported:
        return True
    return any(module in candidates for candidates in bindings.values())


def _test_index() -> set[str]:
    """Every real ``tests/<file>.py::<test_fn>`` id, by AST scan (read-only).

    The index is the authoritative resolvability oracle: a test id absent from
    it was never authored on disk and can never back a SAFE_EQUIVALENT claim.
    """
    global _TEST_INDEX_CACHE
    if _TEST_INDEX_CACHE is not None:
        return _TEST_INDEX_CACHE
    index: set[str] = set()
    if TESTS_DIR.is_dir():
        for path in sorted(TESTS_DIR.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError):
                continue
            rel = str(path.relative_to(REPO_ROOT))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                    node.name.startswith("test")
                ):
                    index.add(f"{rel}::{node.name}")
    _TEST_INDEX_CACHE = index
    return index


def resolves_test_id(test_id: Any) -> bool:
    """True only when ``tests/<file>.py::<fn>`` names a real test function."""
    if not isinstance(test_id, str) or "::" not in test_id:
        return False
    path, _, fn = test_id.partition("::")
    path = path.strip().lstrip("./")
    return bool(fn) and path and f"{path}::{fn}" in _test_index()


def _reference_index() -> tuple[
    dict[str, set[str]],
    dict[str, dict[str, set[str]]],
    dict[str, set[str]],
    dict[str, dict[str, set[str]]],
    dict[str, list[str]],
]:
    """AST index of the Qubit linkage between tests and their imports.

    Returns ``(name_refs, attr_refs, file_modules, file_bindings, file_tests)``:

    * ``name_refs``: bare identifier -> test ids whose body uses it as a Name.
      Only real Name nodes are indexed; string constants and prose are never a
      symbol reference (the old whole-file substring tie is gone).
    * ``attr_refs``: attribute name -> {test id -> base dotted names}. So
      ``gap_ledger.main`` records ``main`` with base ``gap_ledger``, which lets
      the resolver check whether the base resolves to the Qubit module.
    * ``file_modules``: test file -> imported dotted module names.
    * ``file_bindings``: test file -> {bound name -> candidate dotted modules}.
      ``from voicebridge import terminal`` records ``terminal`` -> {voicebridge,
      voicebridge.terminal}; ``from voicebridge.terminal import main`` records
      ``main`` -> {voicebridge.terminal, voicebridge.terminal.main}.
    * ``file_tests``: test file -> sorted real test ids.

    Read-only and cached; deterministic (sorted file walk).
    """
    global _REFERENCE_INDEX_CACHE
    if _REFERENCE_INDEX_CACHE is not None:
        return _REFERENCE_INDEX_CACHE
    name_refs: dict[str, set[str]] = {}
    attr_refs: dict[str, dict[str, set[str]]] = {}
    file_modules: dict[str, set[str]] = {}
    file_bindings: dict[str, dict[str, set[str]]] = {}
    file_tests: dict[str, list[str]] = {}
    if TESTS_DIR.is_dir():
        for path in sorted(TESTS_DIR.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError):
                continue
            rel = str(path.relative_to(REPO_ROOT))
            imported: set[str] = set()
            bindings: dict[str, set[str]] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported.add(alias.name)
                        if alias.asname:
                            bindings.setdefault(alias.asname, set()).add(alias.name)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        # ``from P import x`` may bind a submodule (P.x) or a
                        # symbol (x defined in P); record both candidates so the
                        # resolver can decide from the row's module identity.
                        bindings.setdefault(alias.asname or alias.name, set()).update(
                            {node.module, f"{node.module}.{alias.name}"}
                        )
            file_modules[rel] = imported
            file_bindings[rel] = bindings
            tests: list[str] = []
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("test")
                ):
                    test_id = f"{rel}::{node.name}"
                    tests.append(test_id)
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Name):
                            name_refs.setdefault(sub.id, set()).add(test_id)
                        elif isinstance(sub, ast.Attribute):
                            base = _dotted_name(sub.value)
                            bucket = attr_refs.setdefault(sub.attr, {})
                            bucket.setdefault(test_id, set()).add(base)
            if tests:
                file_tests[rel] = sorted(tests)
    _REFERENCE_INDEX_CACHE = (
        name_refs, attr_refs, file_modules, file_bindings, file_tests)
    return _REFERENCE_INDEX_CACHE


def _owner_ledger_rows() -> list[dict[str, Any]]:
    """Owner ledger rows for the upstream delta (read-only, cached)."""
    global _OWNER_LEDGER_CACHE
    if _OWNER_LEDGER_CACHE is not None:
        return _OWNER_LEDGER_CACHE
    rows: list[dict[str, Any]] = []
    try:
        doc = json.loads(OWNER_LEDGER_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = None
    if isinstance(doc, Mapping):
        candidate = doc.get("rows")
        if isinstance(candidate, list):
            rows = [r for r in candidate if isinstance(r, Mapping)]
    _OWNER_LEDGER_CACHE = rows
    return rows


def _references_module_token(blob: str, module: str, stem: str) -> bool:
    """True when ``blob`` names the module/stem as a delimited token.

    The pre-hardening check was a raw substring (``stem in blob``), so an
    unrelated ``qubit_backend`` that merely contained the letters of the stem
    (``terminals`` for ``terminal``) could inherit a proving test. Match only a
    whole token (letters/digits/underscore boundary).
    """
    for token in {module, stem}:
        if not token:
            continue
        if re.search(
            r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])",
            blob,
        ):
            return True
    return False


def _ledger_proving_tests(qubit_path: str, qubit_symbol: str) -> list[str]:
    """Owner-ledger proving-test files for a row's Qubit module.

    A ledger entry only counts when its ``qubit_backend`` names the row's Qubit
    module as a delimited token (:func:`_references_module_token`); a shared hub
    (``qubit_compat.py`` / ``cli.py`` / ``webui.py``) must additionally name the
    row's symbol, so unrelated capabilities in the same file never inherit one
    another's proving test.
    """
    module = Path(str(qubit_path).split(":")[0]).name
    stem = module.rsplit(".", 1)[0]
    if not module:
        return []
    found: set[str] = set()
    for entry in _owner_ledger_rows():
        blob = str(entry.get("qubit_backend") or "")
        if not _references_module_token(blob, module, stem):
            continue
        if module in {s + ".py" for s in SHARED_QUBIT_MODULE_STEMS} and qubit_symbol not in blob:
            continue
        test = entry.get("proving_test")
        if isinstance(test, str) and test:
            found.add(test)
    return sorted(found)


def _iter_declared_test_ids(row: Mapping[str, Any]):
    """Yield every test id a row declares across its proof-bearing keys."""
    for key in ("test_ids", "trace_refs"):
        for test_id in row.get(key) or []:
            if isinstance(test_id, str):
                yield test_id
    for key in ("disposition_proof", "verification"):
        block = row.get(key)
        if isinstance(block, Mapping):
            for test_id in block.get("test_ids") or []:
                if isinstance(test_id, str):
                    yield test_id


def _resolvable_test_references(
    row: Mapping[str, Any], res: Mapping[str, Any] | None = None
) -> list[str]:
    """Declared test ids that resolve to a real test function (sorted, unique)."""
    found: set[str] = set()
    for source in (row, res if isinstance(res, Mapping) else {}):
        for test_id in _iter_declared_test_ids(source):
            if resolves_test_id(test_id):
                found.add(test_id)
    return sorted(found)


# --------------------------------------------------------------------------- #
# Route/symbol-level linkage (fail-closed)
#
# A test id that merely RESOLVES is not proof for a specific row: a class-level
# surface test (``test_gated_routes_resolve_to_typed_gates``) resolves for every
# route but exercises only its own iterated route table. An SE claim therefore
# requires the cited test FILE to reference the row's specific route path,
# symbol, or mapped Qubit module: a symbol call/attribute, a direct route
# string, membership in a route table the file iterates, or the module the
# kernel maps. Comments and prose are never tokens -- only AST names,
# attribute/import names and string constants. This is the conservative,
# independent criterion recorded in
# ``/tmp/qubit-differential/evidence/HONESTY_FINAL2.md`` (section 4).
# --------------------------------------------------------------------------- #
_ROUTE_TOKEN_RE = re.compile(r"/[A-Za-z0-9_\-./{}]+")


def _module_basename(qubit_path: str) -> str:
    """``voicebridge/terminal.py:382`` -> ``terminal`` (or "")."""
    path_part = str(qubit_path).split(":", 1)[0].strip()
    name = Path(path_part).name
    return name[:-3] if name.endswith(".py") else ""


def _test_file_linkage_index() -> dict[str, dict[str, frozenset[str]]]:
    """Whole-file token index for route/symbol-level row linkage (read-only).

    Returns ``{test_file: {"names", "strings"}}`` where ``names`` holds every
    AST identifier, attribute name and import binding in the file, and
    ``strings`` holds every string constant (which includes the literals of any
    route table the file declares). Deterministic (sorted file walk) and cached.
    Comments and prose are never tokens.
    """
    global _TEST_LINKAGE_CACHE
    if _TEST_LINKAGE_CACHE is not None:
        return _TEST_LINKAGE_CACHE
    index: dict[str, dict[str, frozenset[str]]] = {}
    if TESTS_DIR.is_dir():
        for path in sorted(TESTS_DIR.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError):
                continue
            rel = str(path.relative_to(REPO_ROOT))
            names: set[str] = set()
            strings: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    strings.add(node.value)
                elif isinstance(node, ast.Name):
                    names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    names.add(node.attr)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        names.add(alias.name)
                        names.add(alias.name.split(".", 1)[0])
                        if alias.asname:
                            names.add(alias.asname)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names.add(node.module)
                    for alias in node.names:
                        if alias.name != "*":
                            names.add(alias.name)
                        if alias.asname:
                            names.add(alias.asname)
            index[rel] = {
                "names": frozenset(names),
                "strings": frozenset(strings),
            }
    _TEST_LINKAGE_CACHE = index
    return index


def _row_route_tokens(row: Mapping[str, Any]) -> frozenset[str]:
    """Route paths (and their segments) a row declares.

    Every ``/path`` in ``command`` / ``route`` / ``dashboard_route`` plus each
    path segment longer than three characters (a route table entry matches
    through its segment). Route tokens are the only tokens allowed to match a
    *string constant* in a test file, because a route table is a set of route
    literals; symbol/module tokens must be AST identifiers (a prose substring
    such as the English word ``terminal`` in a docstring is not a route).
    """
    tokens: set[str] = set()
    routes: list[str] = []
    command = row.get("command")
    if isinstance(command, str):
        routes.extend(_ROUTE_TOKEN_RE.findall(command))
    elif isinstance(command, Mapping) and isinstance(command.get("path"), str):
        routes.append(str(command["path"]))
    for key in ("route", "dashboard_route"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            routes.append(value.strip())
    request = row.get("request")
    if isinstance(request, Mapping) and isinstance(request.get("path"), str):
        routes.append(str(request["path"]))
    for route in routes:
        route = route.split("?", 1)[0].split("#", 1)[0].rstrip(".*")
        if route.startswith("/"):
            tokens.add(route)
            for segment in route.split("/"):
                if len(segment) > 3:
                    tokens.add(segment)
    return frozenset(t for t in tokens if t and t != "__gate__")


def _row_symbol_tokens(row: Mapping[str, Any]) -> frozenset[str]:
    """Symbol plus its whitespace/dot-separated parts a row declares.

    ``qubit_symbol`` (trailing ``*`` stripped; ``__gate__`` ignored); a CLI
    command such as ``hermes approvals suggest`` contributes
    ``approvals``/``suggest``. The mapped module is deliberately NOT included:
    a module basename (``terminal``) must be proved by a real module import
    (:func:`_test_module_ast_linked`), never by a bare name or a prose word.
    """
    tokens: set[str] = set()
    symbol = str(row.get("qubit_symbol") or "").strip()
    if symbol and symbol != "__gate__":
        symbol = symbol.rstrip("*").strip()
        if symbol:
            tokens.add(symbol)
            for part in re.split(r"[\s.]+", symbol):
                if part and part != "__gate__":
                    tokens.add(part)
    return frozenset(t for t in tokens if t and t != "__gate__")


def _gate_error_codes(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Typed denial code(s) a gate emits (``error_codes``, comma/space split).

    A row carrying this field is an explicit security/policy gate: its
    disposition must be backed by a test that exercises the gate, not by a
    route string that merely appears somewhere in a test file.
    """
    raw = row.get("error_codes")
    codes: list[str] = []
    if isinstance(raw, str):
        parts = re.split(r"[,\s]+", raw.strip())
    elif isinstance(raw, (list, tuple)):
        parts = [str(part) for part in raw]
    else:
        parts = []
    for part in parts:
        part = part.strip()
        if part and part not in codes:
            codes.append(part)
    return tuple(codes)


def _is_explicit_gate(row: Mapping[str, Any]) -> bool:
    """True when the row is a typed security/policy gate, not a normal read."""
    return (
        str(row.get("qubit_symbol") or "") == "__gate__"
        or bool(_gate_error_codes(row))
    )


def _test_module_ast_linked(rel: str, module: str) -> bool:
    """True when the cited test file AST-references the Qubit module.

    A module is referenced by a real ``import`` / ``from ... import`` binding
    (:func:`_imports_module`) or by an attribute access whose base dotted name
    (or an import alias) resolves to the module. A prose substring, a docstring
    and a bare symbol that merely collides across modules are never a module
    reference. Unknown files fail closed.
    """
    if not module:
        return False
    _, attr_refs, file_modules, file_bindings, _ = _reference_index()
    imported = file_modules.get(rel, set())
    bindings = file_bindings.get(rel, {})
    if _imports_module(module, imported, bindings):
        return True
    for per_test in attr_refs.values():
        for test_id, bases in per_test.items():
            if not test_id.startswith(rel + "::"):
                continue
            for base in bases:
                resolved = bindings.get(base) or {base}
                if module in resolved:
                    return True
    return False


def _test_references_row(test_id: str, row: Mapping[str, Any]) -> bool:
    """True when the cited test FILE genuinely references this row.

    Three independent, fabrication-free linkages; a whole-file substring or a
    bare generic-symbol collision satisfies none of them:

    * *route*: a route token (a ``/path`` or a segment longer than three
      characters) really appears inside a **route-like** string constant (one
      that starts with ``/``), which is how a route table the file iterates is
      matched. An English word in a docstring is not a route;
    * *module*: when the row maps a Qubit module, the test must genuinely
      import/reference it (:func:`_test_module_ast_linked`) AND name the row's
      symbol. So ``gap_ledger.main`` in
      ``tests/test_ledger_ingestion_wave3.py`` cannot prove
      ``voicebridge/terminal.py:main``: the file never imports
      ``voicebridge.terminal`` and its bare ``terminal`` variable / docstring
      word is not the mapped module;
    * *symbol*: with no mapped module, the row's symbol must be a non-generic
      AST identifier or an exact string constant. A generic symbol (``main``,
      ``status``, ...) is never proof on its own.

    Unknown test files fail closed (``False``). An explicit gate row
    (``qubit_symbol == "__gate__"`` or a typed ``error_codes``) is held to a
    stricter standard: the cited test file must also reference the gate's
    denial code, so a route string in an error-suppression allowlist is not
    gate proof.
    """
    rel = str(test_id).split("::", 1)[0].strip().lstrip("./")
    entry = _test_file_linkage_index().get(rel)
    if not entry:
        return False
    names = entry["names"]
    strings = entry["strings"]
    route_strings = frozenset(value for value in strings if value.startswith("/"))
    route_match = any(
        token in value
        for token in _row_route_tokens(row)
        for value in route_strings
    )
    module = _qubit_module_dotted(str(row.get("qubit_path") or ""))
    symbol_tokens = _row_symbol_tokens(row)
    if module:
        module_linked = _test_module_ast_linked(rel, module)
        symbol_match = module_linked and any(
            token in names or token in strings for token in symbol_tokens
        )
    else:
        symbol_match = any(
            (token in names or token in strings)
            and token not in _GENERIC_QUBIT_SYMBOLS
            for token in symbol_tokens
        )
    if not (route_match or symbol_match):
        return False
    if _is_explicit_gate(row):
        codes = _gate_error_codes(row)
        if not codes:
            return False
        return any(
            code in names or any(code in value for value in strings)
            for code in codes
        )
    return True



def _linked_test_references(
    row: Mapping[str, Any], res: Mapping[str, Any] | None = None
) -> list[str]:
    """Resolvable declared test ids that genuinely reference this row."""
    return [
        test_id
        for test_id in _resolvable_test_references(row, res)
        if _test_references_row(test_id, row)
    ]


# --------------------------------------------------------------------------- #
# Frontend (dashboard Control-UI) wiring proof type
#
# A dashboard row mapped to a ``frontend/qubit-web/src`` component is normally
# invisible to the Python ``tests/`` AST resolver, so it stays OPEN even when a
# real vitest suite exercises it. :func:`_frontend_linkage` closes that gap with
# a conservative, deterministic, fabrication-free rule:
#
#   1. the mapped component file exists on disk;
#   2. the mapped symbol really appears in that component file;
#   3. a real ``frontend/qubit-web/src/**/*.test.ts(x)`` exists on disk;
#   4. that test IMPORTS the mapped component (a static/dynamic specifier that
#      resolves to the exact file — a comment, prose or a mock specifier is
#      never enough); and
#   5. the test references the mapped symbol as a word.
#
# Only then is the row disposed ``SAFE_EQUIVALENT`` with
# ``proof_level="wiring"`` and ``wiring_proof_status="frontend_linkage"``. A row
# whose mapped component or symbol cannot be substantiated stays OPEN.
# --------------------------------------------------------------------------- #
_FRONTEND_IMPORT_RE = re.compile(
    r"""(?x)
    (?:
      \bimport\s*(?:type\s*)?[^;"']*?\bfrom\s*["']([^"']+)["']   # import clause
    )
    |
    (?:
      \bimport\s*\(\s*["']([^"']+)["']                          # dynamic import()
    )
    |
    (?:
      \bimport\s*["']([^"']+)["']                               # side-effect import
    )
    """
)


def _frontend_import_specifiers(text: str) -> frozenset[str]:
    """Every module specifier in a real ``import`` statement (textual scan).

    Anchored on the ``import`` keyword and the two legal specifier shapes, so a
    prose substring such as ``... use X from "@/lib/clipboard" instead`` is NOT
    an import (the pre-hardening loose regex false-positived on it). A comment
    or a ``vi.mock(...)`` argument is therefore never evidence of linkage.
    """
    found: set[str] = set()
    for match in _FRONTEND_IMPORT_RE.finditer(text):
        specifier = next((group for group in match.groups() if group), None)
        if specifier:
            found.add(specifier)
    return frozenset(found)


def _frontend_test_index() -> dict[str, dict[str, Any]]:
    """``{rel_test_path: {"text": str, "imports": frozenset[str]}}`` (read-only).

    Deterministic (sorted file walk) and cached. Only real colocated vitest
    suites under :data:`FRONTEND_ROOT` are indexed; a missing frontend tree
    yields an empty index (fail closed).
    """
    global _FRONTEND_TEST_INDEX_CACHE
    if _FRONTEND_TEST_INDEX_CACHE is not None:
        return _FRONTEND_TEST_INDEX_CACHE
    index: dict[str, dict[str, Any]] = {}
    if FRONTEND_ROOT.is_dir():
        paths: list[Path] = []
        for suffix in FRONTEND_TEST_SUFFIXES:
            paths.extend(FRONTEND_ROOT.rglob(f"*{suffix}"))
        for path in sorted(set(paths)):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = str(path.relative_to(REPO_ROOT))
            index[rel] = {
                "text": text,
                "imports": _frontend_import_specifiers(text),
            }
    _FRONTEND_TEST_INDEX_CACHE = index
    return index


def _frontend_component_path(qubit_path: Any) -> str | None:
    """Repo-relative frontend source file for a row's ``qubit_path``, or None.

    Fail closed: only a real ``.ts``/``.tsx`` file at or under
    :data:`FRONTEND_ROOT` qualifies; a non-frontend or absent path returns None.
    """
    path_part = str(qubit_path or "").split(":", 1)[0].strip().lstrip("./")
    if not path_part:
        return None
    candidate = (REPO_ROOT / path_part)
    if candidate.suffix not in FRONTEND_SOURCE_SUFFIXES or not candidate.is_file():
        return None
    try:
        resolved = candidate.resolve()
        resolved.relative_to(FRONTEND_ROOT.resolve())
        return str(resolved.relative_to(REPO_ROOT.resolve()))
    except (ValueError, OSError):
        return None


def _resolve_frontend_specifier(test_rel: str, specifier: str) -> str | None:
    """Resolve a test import specifier to a frontend source file, or None.

    Supports the repo's two import styles: relative (``./X``) and root-index
    (``@/X``). A bare package specifier (``vitest``, ``react``, ...) is never a
    local component. Returns the repo-relative path only when the resolved file
    really exists under :data:`FRONTEND_ROOT`.
    """
    if specifier.startswith("@/"):
        base = FRONTEND_ROOT / specifier[2:]
    elif specifier.startswith("."):
        base = (REPO_ROOT / test_rel).parent / specifier
    else:
        return None
    candidates: list[Path] = [base]
    for suffix in FRONTEND_SOURCE_SUFFIXES:
        candidates.append(base.with_name(base.name + suffix))
    candidates.extend([base / "index.ts", base / "index.tsx"])
    frontend_root = FRONTEND_ROOT.resolve()
    repo_root = REPO_ROOT.resolve()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(frontend_root)
        except (ValueError, OSError):
            continue
        if not resolved.is_file():
            continue
        try:
            return str(resolved.relative_to(repo_root))
        except ValueError:
            continue
    return None


def _frontend_linkage(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """The real frontend test that genuinely links this row's mapped component.

    Returns ``{"test", "component", "symbol", "proof_type": FRONTEND_LINKAGE}``
    for the lexicographically first qualifying test, else ``None``. Never
    fabricates: an absent component, an absent symbol, an absent test or a
    comment/prose-only mention all fail closed to ``None`` (the row stays OPEN).
    """
    qubit_path = str(row.get("qubit_path") or "")
    symbol = str(row.get("qubit_symbol") or "").strip()
    if not qubit_path or not symbol:
        return None
    component_rel = _frontend_component_path(qubit_path)
    if component_rel is None:
        return None
    try:
        component_text = (REPO_ROOT / component_rel).read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return None
    word = re.compile(
        r"(?<![A-Za-z0-9_$])" + re.escape(symbol) + r"(?![A-Za-z0-9_$])")
    if not word.search(component_text):
        return None
    for test_rel, entry in _frontend_test_index().items():
        if not word.search(entry["text"]):
            continue
        if any(
            _resolve_frontend_specifier(test_rel, specifier) == component_rel
            for specifier in entry["imports"]
        ):
            return {
                "test": test_rel,
                "component": component_rel,
                "symbol": symbol,
                "proof_type": FRONTEND_LINKAGE,
            }
    return None


def _attach_frontend_linkage_proof(
    row: dict[str, Any], linkage: Mapping[str, Any]
) -> None:
    """Mark a row SAFE_EQUIVALENT on a REAL frontend vitest linkage.

    Only :func:`_frontend_linkage` (which requires the component, the symbol and
    an importing test to exist on disk) may call this; no field is synthesized.
    """
    test_rel = str(linkage["test"])
    row["disposition"] = "SAFE_EQUIVALENT"
    row["disposition_basis"] = "safe_equivalent_proven"
    row["proof_level"] = "wiring"
    row["proof_kind"] = FRONTEND_LINKAGE
    row["wiring_proof_status"] = FRONTEND_LINKAGE
    row["frontend_test_ref"] = test_rel
    row["frontend_component_ref"] = str(linkage["component"])
    row["frontend_symbol"] = str(linkage["symbol"])
    row["disposition_proof"] = {
        "kind": "frontend_test_reference",
        "executed": False,
        "level": "wiring/resolution-not-live-execution",
        "basis": FRONTEND_LINKAGE,
        "frontend_test_ref": test_rel,
        "component_ref": str(linkage["component"]),
        "symbol": str(linkage["symbol"]),
        "method": "frontend vitest import + symbol linkage",
    }
    row["verification"] = {
        "exists": True,
        "symbol_present": True,
        "test_resolves": True,
        "method": "static+text",
    }


# --------------------------------------------------------------------------- #
# Served plugin-bundle wiring proof type (dashboard/plugin compiled bundles)
#
# A dashboard/plugin row may map to a COMPILED plugin bundle served from
# ``frontend/qubit-web/public/plugins/<plugin>/dist/*.js`` (the built SPA ships
# these to ``dist/plugins/...``). Such a page has no ``*.tsx`` source, so the
# frontend vitest linkage cannot see it. :func:`_plugin_bundle_linkage` closes
# that gap conservatively and deterministically:
#
#   1. the row's ``qubit_path`` resolves to a REAL served ``.js``/``.mjs`` file
#      directly under a plugin's ``dist/`` directory (never an arbitrary asset);
#   2. the file exists, is read, and its sha256 is recorded;
#   3. the row's ``qubit_symbol`` (when present) really occurs in the bundle;
#   4. a REAL resolvable Python ``tests/<file>.py::<test_fn>`` FUNCTION reads
#      that bundle (a bundle-path literal or a module constant that resolves to
#      it, plus a file read) and references the row's symbol.
#
# Only then is the row disposed ``SAFE_EQUIVALENT`` with
# ``proof_level="wiring"`` and ``wiring_proof_status="plugin_bundle_linkage"``.
# An absent bundle, an unread bundle, a symbol absent from the bundle, or no
# reading test all fail closed (the row stays OPEN).
# --------------------------------------------------------------------------- #
_PLUGIN_BUNDLE_TEST_CACHE: dict[str, dict[str, Any]] | None = None
# The runner's own contract test must never certify a row it also asserts: that
# is circular. It is excluded from the independent bundle-reading index.
_DIFFERENTIAL_SELF_TEST_REL = "tests/test_differential_parity8u.py"


def _joined_path_string(node: ast.AST) -> str | None:
    """Best-effort path string from a ``Path``-style string expression.

    Handles ``REPO_ROOT / "a" / "b" / "index.js"`` and ``Path("a", "b")`` by
    joining every real string constant with ``/``; a non-path expression yields
    ``None``. Used only to resolve a test module's bundle-path constant.
    """
    parts: list[str] = []

    def visit(current: ast.AST) -> None:
        if isinstance(current, ast.Constant) and isinstance(current.value, str):
            if current.value:
                parts.append(current.value)
        elif isinstance(current, ast.BinOp) and isinstance(current.op, ast.Div):
            visit(current.left)
            visit(current.right)
        elif isinstance(current, ast.Call) and not isinstance(
            current.func, ast.Attribute
        ):
            for arg in current.args:
                visit(arg)

    visit(node)
    return "/".join(parts) if parts else None


def _plugin_bundle_path(qubit_path: Any) -> tuple[str, str, str] | None:
    """``(bundle_rel, plugin, filename)`` for a served plugin bundle, else None.

    Fail closed: only a real ``.js``/``.mjs`` file whose parent directory is
    ``dist`` under :data:`SERVED_PLUGIN_ROOT` qualifies. A non-bundle asset, a
    missing file or a path outside the served plugin root returns ``None``.
    """
    path_part = str(qubit_path or "").split(":", 1)[0].strip().lstrip("./")
    if not path_part:
        return None
    candidate = (REPO_ROOT / path_part)
    if candidate.suffix.lower() not in SERVED_PLUGIN_BUNDLE_SUFFIXES:
        return None
    if not candidate.is_file():
        return None
    try:
        resolved = candidate.resolve()
        rel = resolved.relative_to(SERVED_PLUGIN_ROOT.resolve())
    except (ValueError, OSError):
        return None
    parts = rel.parts
    if len(parts) < 3 or parts[-2] != "dist":
        return None
    return (
        str(resolved.relative_to(REPO_ROOT.resolve())),
        parts[0],
        parts[-1],
    )


def _plugin_bundle_test_index() -> dict[str, dict[str, Any]]:
    """AST index of Python test FUNCTIONS that read a served plugin bundle.

    Returns ``{test_id: {"consts", "names", "strings", "reads_file"}}`` where
    ``consts`` maps a module-level constant to its joined path string, ``names``
    and ``strings`` are the identifiers/string constants used inside the
    function, and ``reads_file`` is True when the function performs a real file
    read (``read_text``/``read_bytes``/``open``). Deterministic (sorted walk) and
    cached. Comments and prose are never tokens.
    """
    global _PLUGIN_BUNDLE_TEST_CACHE
    if _PLUGIN_BUNDLE_TEST_CACHE is not None:
        return _PLUGIN_BUNDLE_TEST_CACHE
    index: dict[str, dict[str, Any]] = {}
    if TESTS_DIR.is_dir():
        for path in sorted(TESTS_DIR.rglob("*.py")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(text)
            except (OSError, SyntaxError):
                continue
            rel = str(path.relative_to(REPO_ROOT))
            if rel == _DIFFERENTIAL_SELF_TEST_REL:
                continue
            consts: dict[str, str] = {}
            for node in tree.body:
                if not isinstance(node, ast.Assign):
                    continue
                value = _joined_path_string(node.value)
                if value is None:
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        consts[target.id] = value
            for node in ast.walk(tree):
                if not (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("test")
                ):
                    continue
                test_id = f"{rel}::{node.name}"
                names: set[str] = set()
                strings: set[str] = set()
                reads_file = False
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name):
                        names.add(sub.id)
                        if sub.id == "open":
                            reads_file = True
                    elif isinstance(sub, ast.Constant) and isinstance(
                        sub.value, str
                    ):
                        strings.add(sub.value)
                    elif isinstance(sub, ast.Attribute):
                        if sub.attr in ("read_text", "read_bytes", "is_file",
                                        "exists", "glob", "iterdir"):
                            reads_file = True
                index[test_id] = {
                    "file": rel,
                    "consts": consts,
                    "names": frozenset(names),
                    "strings": frozenset(strings),
                    "reads_file": reads_file,
                }
    _PLUGIN_BUNDLE_TEST_CACHE = index
    return index


def _plugin_bundle_tokens(bundle_rel: str, plugin: str, filename: str) -> tuple[str, ...]:
    """Path spellings a reading test may use for a served plugin bundle."""
    tokens = {
        bundle_rel,
        f"plugins/{plugin}/dist/{filename}",
        f"/plugins/{plugin}/dist/{filename}",
        f"{plugin}/dist/{filename}",
    }
    return tuple(sorted(tokens))


def _plugin_bundle_function_reads(
    entry: Mapping[str, Any], bundle_rel: str, plugin: str, filename: str
) -> bool:
    """True when a test function really reads this served bundle file.

    The bundle must be referenced either as a string constant or through a
    module-level path constant the function uses, AND the function must perform
    a real file read. A ``.js`` path that is only mentioned in prose is not a
    read.
    """
    if not entry.get("reads_file"):
        return False
    tokens = _plugin_bundle_tokens(bundle_rel, plugin, filename)
    consts = entry.get("consts") or {}
    for name in entry.get("names") or ():
        value = consts.get(name)
        if value and any(token in value for token in tokens):
            return True
    for value in entry.get("strings") or ():
        if any(token in value for token in tokens):
            return True
    return False


def _plugin_bundle_linkage(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """The served plugin bundle + real reading test that link this row, or None.

    Fail closed: a non-bundle ``qubit_path``, an absent file, a symbol absent
    from the bundle, or no reading test all return ``None``. The bundle's sha256
    is always recorded in the returned linkage so the proof carries a hash.
    """
    resolved = _plugin_bundle_path(row.get("qubit_path"))
    if resolved is None:
        return None
    bundle_rel, plugin, filename = resolved
    symbol = str(row.get("qubit_symbol") or "").strip()
    if not symbol:
        return None
    bundle_abs = REPO_ROOT / bundle_rel
    try:
        bundle_text = bundle_abs.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if symbol not in bundle_text:
        return None
    bundle_hash = file_hash(bundle_abs)
    test_index = _plugin_bundle_test_index()
    for test_id in sorted(test_index):
        entry = test_index[test_id]
        if not _plugin_bundle_function_reads(entry, bundle_rel, plugin, filename):
            continue
        if symbol not in (entry.get("strings") or set()) and symbol not in (
            entry.get("names") or set()
        ):
            continue
        return {
            "test": test_id,
            "bundle": bundle_rel,
            "plugin": plugin,
            "filename": filename,
            "symbol": symbol,
            "bundle_hash": bundle_hash,
            "proof_type": PLUGIN_BUNDLE_LINKAGE,
        }
    return None


def _attach_plugin_bundle_proof(
    row: dict[str, Any], linkage: Mapping[str, Any]
) -> None:
    """Mark a row SAFE_EQUIVALENT on a REAL served plugin-bundle read.

    Only :func:`_plugin_bundle_linkage` (which requires the served bundle, the
    symbol and a reading test to exist on disk) may call this; no field is
    synthesized. The bundle sha256 is recorded as the proof's evidence hash.
    """
    test_id = str(linkage["test"])
    bundle_rel = str(linkage["bundle"])
    row["disposition"] = "SAFE_EQUIVALENT"
    row["disposition_basis"] = "safe_equivalent_proven"
    row["proof_level"] = "wiring"
    row["proof_kind"] = PLUGIN_BUNDLE_LINKAGE
    row["wiring_proof_status"] = PLUGIN_BUNDLE_LINKAGE
    row["plugin_bundle_ref"] = bundle_rel
    row["plugin_bundle_hash"] = str(linkage["bundle_hash"])
    row["plugin_bundle_plugin"] = str(linkage["plugin"])
    row["test_ids"] = [test_id]
    row["trace_refs"] = [bundle_rel]
    row["qubit_result_hash"] = str(linkage["bundle_hash"])
    row["disposition_proof"] = {
        "kind": "python_test_reference",
        "executed": False,
        "level": "wiring/bundle-resolution-not-live-execution",
        "basis": PLUGIN_BUNDLE_LINKAGE,
        "test_ids": [test_id],
        "bundle_ref": bundle_rel,
        "bundle_hash": str(linkage["bundle_hash"]),
        "symbol": str(linkage["symbol"]),
        "method": "AST test resolution + served plugin bundle read + sha256",
    }
    row["verification"] = {
        "exists": True,
        "bundle_present": True,
        "hash_recorded": True,
        "symbol_present": True,
        "test_resolves": True,
        "method": "static+AST+bundle-hash",
    }


# --------------------------------------------------------------------------- #
# Vendor behavior wiring proof type (B1 closure)
#
# An upstream row whose `qubit_path` is a file under the vendored fork
# (`qubit_runtime/`) maps a module (and optionally a symbol). The proof is a real
# execution of both sides: the vendored module imports through
# `qubit_runtime.ensure_path()` and the pinned reference root imports the same
# module, and their behavioral descriptors (import success, public API names
# hash, symbol kind + `inspect.signature`, source hash as the descriptor's
# identity) must be equal. This is still `proof_level="wiring"` (a module-surface
# comparison, not the row's end-to-end production path); it is NEVER inferred
# from a byte/catalog count. A failed import or an unresolved reference root
# leaves the row OPEN (never PASS).
# --------------------------------------------------------------------------- #
def _vendored_module_spec(row: Mapping[str, Any]) -> tuple[str, str | None] | None:
    """``(module, symbol)`` for a vendored upstream source row, else ``None``.

    Fail closed: only a ``qubit_runtime/**/*.py`` mapping qualifies; a symbol that
    is really a file path (module-level row) becomes ``None``; a non-identifier
    symbol (a SQL table name or route) is still probed but will be rejected as
    "missing" by :func:`_vendor_behavior_proof` unless the module really exports
    it.
    """
    # A row may name the vendored fork directly (``qubit_path`` under
    # ``qubit_runtime/``) OR name the upstream file, which the fork mirrors at the
    # same relative path. Both resolve to the same vendored module, so an
    # upstream row whose file is shipped in the fork gets the same real
    # behavioral comparison against the pinned reference. A path not present in
    # the fork is fail-closed (``None``).
    module = None
    for key in ("qubit_path", "upstream_path"):
        path_part = str(row.get(key) or "").split(":", 1)[0].strip().lstrip("./")
        if not path_part.endswith(".py"):
            continue
        parts = Path(path_part).parts
        if not parts:
            continue
        if parts[0] == VENDOR_FORK_DIRNAME:
            rel_parts = parts[1:]
        elif (REPO_ROOT / VENDOR_FORK_DIRNAME / Path(*parts)).is_file():
            rel_parts = parts
        else:
            continue
        rel = Path(*rel_parts)
        if rel.name == "__init__.py":
            rel_parts = rel.parent.parts
        else:
            rel_parts = rel.with_suffix("").parts
        if rel_parts:
            module = ".".join(rel_parts)
            break
    if module is None:
        return None
    # Prefer the row's declared Qubit symbol; fall back to the upstream symbol
    # for an upstream-only row (e.g. a config schema key).
    symbol = str(row.get("qubit_symbol") or row.get("upstream_symbol") or "").strip()
    if not symbol or "/" in symbol or symbol.endswith(".py") or " " in symbol:
        symbol = None
    return module, symbol


def _vendor_behavior_valid(
    row: Mapping[str, Any], res: Mapping[str, Any]
) -> bool:
    """True only when a result carries a genuine vendor behavioral proof.

    Fail-closed: the proof marker, both descriptor hashes, a really executed
    reference and ``behavior_equal`` are all required. A byte identity alone (no
    ``vendor_reference``/``behavior_equal``) is never proof.
    """
    if res.get("proof_kind") != VENDOR_PROOF:
        return False
    vendor = res.get("vendor_reference")
    if not isinstance(vendor, Mapping):
        return False
    if vendor.get("reference_executed") is not True:
        return False
    if vendor.get("behavior_equal") is not True:
        return False
    if not vendor.get("reference_hash") or not vendor.get("qubit_hash"):
        return False
    return bool(res.get("qubit_result_hash")) and bool(res.get("upstream_result_hash"))


def _attach_vendor_behavior_proof(
    result: dict[str, Any], comparison: Mapping[str, Any]
) -> None:
    """Stamp a SAFE_EQUIVALENT wiring proof from a real vendor comparison.

    Only called after :func:`_vendored_module_spec` resolved a module AND the
    adapter confirmed both sides import with equal descriptors. The two
    behavioral descriptor hashes are recorded, so the row is fully evidenced and
    the ``unexecuted_row``/``pass_without_reference`` gates stay satisfied.
    """
    que = comparison.get("vendored") if isinstance(comparison.get("vendored"), Mapping) else {}
    result["disposition"] = "SAFE_EQUIVALENT"
    result["disposition_basis"] = "safe_equivalent_proven"
    result["proof_level"] = "wiring"
    result["proof_kind"] = VENDOR_PROOF
    result["wiring_proof_status"] = VENDOR_PROOF
    result["executed"] = True
    result["qubit_executed"] = True
    result["reference_executed"] = True
    if not result.get("production_caller"):
        result["production_caller"] = "qubit_runtime.ensure_path"
    result["qubit_result_hash"] = str(comparison.get("qubit_hash"))
    result["upstream_result_hash"] = str(comparison.get("reference_hash"))
    result["reference_result_hash"] = str(comparison.get("reference_hash"))
    result["raw_hash"] = str(comparison.get("qubit_hash"))
    result["qubit_payload"] = dict(que)
    result["vendor_reference"] = {
        "project": "hermes",
        "module": str(que.get("module") or ""),
        "symbol": que.get("symbol"),
        "reference_executed": True,
        "behavior_equal": True,
        "reference_hash": str(comparison.get("reference_hash")),
        "qubit_hash": str(comparison.get("qubit_hash")),
    }
    result["disposition_proof"] = {
        "kind": VENDOR_PROOF,
        "executed": True,
        "level": "wiring/module-surface-behavioral-comparison",
        "basis": VENDOR_PROOF,
        "module": str(que.get("module") or ""),
        "symbol": que.get("symbol"),
        "reference_hash": str(comparison.get("reference_hash")),
        "method": "qubit_runtime.ensure_path import + pinned reference compare",
    }
    result["verification"] = {
        "exists": True,
        "imported": True,
        "behavior_equal": True,
        "reference_executed": True,
        "method": "subprocess import + descriptor hash",
    }
    result["request_schema"] = {
        "kind": "vendor-behavior",
        "module": str(que.get("module") or ""),
        "symbol": que.get("symbol"),
    }
    result["runtime_provider"] = "hermes-vendored"
    result["package_proof"] = f"qubit_runtime.ensure_path:{que.get('module')}"
    _fill_vendor_evidence(result, str(que.get("module") or ""), que,
                          str(comparison.get("reference_hash")))


def _fill_vendor_evidence(
    result: dict[str, Any],
    module: str,
    vendored: Mapping[str, Any],
    reference_hash: str | None,
) -> None:
    """Populate the §8/§15 evidence keys for a vendor behavioral proof.

    Only ``None`` fields are filled, and the values are honest
    non-applicability statements or the descriptor's own hashes — never a
    fabricated live result. This keeps a vendor SAFE_EQUIVALENT row from failing
    the success-evidence requirement on fields that a module-surface probe
    genuinely does not exercise.
    """
    names_hash = vendored.get("names_hash")
    que_hash = result_hash(vendored) if vendored else None
    defaults: dict[str, Any] = {
        "voice_utterance": "not-applicable: module-surface behavioral probe",
        "dashboard_route": "not-applicable: module-surface behavioral probe",
        "dashboard_control": "not-applicable: module-surface behavioral probe",
        "request_schema": {"kind": "vendor-behavior", "module": module},
        "broker_policy_decision": "not-applicable: import-only probe",
        "runtime_provider": "hermes-vendored",
        "store_state_assertion": f"public-names:{names_hash}",
        "output_event_assertion": f"descriptor:{que_hash}",
        "error_codes": [],
        "retry_recovery_proof": "not-applicable: deterministic import probe",
        "audit_receipt_proof": reference_hash,
        "security_resource_proof": "import-only; no network and no state mutation",
        "package_proof": f"qubit_runtime.ensure_path:{module}",
        "response_headers": {},
        "screenshot_ref": "not-applicable: module-surface behavioral probe",
        "dom_snapshot_ref": "not-applicable: module-surface behavioral probe",
        "a11y_snapshot_ref": "not-applicable: module-surface behavioral probe",
        "raw_hash": que_hash,
    }
    for key, value in defaults.items():
        if result.get(key) is None:
            result[key] = value


def _vendor_behavior_pass(
    rows: Sequence[Mapping[str, Any]],
    adapters: Mapping[str, Adapter],
    manifest: Mapping[str, str],
    wanted_ids: set[str] | None,
    wanted_families: set[str] | None,
) -> dict[str, dict[str, Any]]:
    """Batch-resolve real vendor behavioral proofs for the selected rows.

    Returns ``{capability_id: comparison}`` only for rows that (a) map a
    ``qubit_runtime`` module, (b) are not already terminal, (c) have no fixture
    bound, and (d) import on BOTH sides with equal descriptors (and, when the row
    names a symbol, that symbol is really present). An unresolved reference root
    or an unavailable adapter yields ``{}`` — every such row stays OPEN
    (fail-closed), never a fabricated proof.
    """
    adapter = adapters.get(VENDOR_TRANSPORT)
    if adapter is None or not adapter.available():
        return {}
    reference_root = getattr(adapter, "resolve_reference_root", lambda *_: None)()
    if reference_root is None:
        return {}
    specs: dict[str, tuple[str, str | None]] = {}
    for row in rows:
        cid = str(row.get("capability_id"))
        if wanted_ids is not None and cid not in wanted_ids:
            continue
        if wanted_families is not None and str(row.get("family")) not in wanted_families:
            continue
        if str(row.get("disposition") or "OPEN") in TERMINAL_DISPOSITIONS:
            continue
        if row.get("fixture") or manifest.get(cid):
            continue
        if str(row.get("upstream_project") or "") != "hermes":
            continue
        spec = _vendored_module_spec(row)
        if spec is None:
            continue
        specs[cid] = spec
    if not specs:
        return {}
    try:
        compared = adapter.compare_specs(
            sorted(set(specs.values()), key=lambda pair: (pair[0], pair[1] or "")),
            reference_root)
    except (TransportUnavailable, OSError, subprocess.SubprocessError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for cid, (module, symbol) in specs.items():
        entry = compared.get(module + VENDOR_KEY_SEP + (symbol or ""))
        if not isinstance(entry, Mapping) or entry.get("behavior_equal") is not True:
            continue
        que = entry.get("vendored")
        ref = entry.get("reference")
        if not isinstance(que, Mapping) or not isinstance(ref, Mapping):
            continue
        if que.get("imported") is not True or ref.get("imported") is not True:
            continue
        if symbol and que.get("symbol_kind") in (None, "missing"):
            continue
        out[cid] = entry
    return out


# --------------------------------------------------------------------------- #
# Qubit-native CLI / voice execution proofs (wave 22 closure)
#
# These are REAL production-path executions, not catalog counts:
#
# * CLI: the repository's own launcher (``.venv/bin/voicebridge`` via the
#   existing ``QubitAdapter``) runs the row's exact command with ``--help``. A
#   row is only disposed when the process exits 0 AND the command's help surface
#   is emitted. An upstream CLI row additionally runs the pinned upstream
#   launcher and requires the normalized help to be byte-equal (``cli_parity``);
#   a Qubit-native row records its own execution (``cli_execution``) with no
#   fabricated upstream reference.
# * Voice: the production voice registry resolver (``voice_commands.resolve``,
#   pure) resolves one of the entry's own declared examples back to the row's
#   canonical op id.
#
# Every proof is fail-closed: a nonzero exit, a missing command, an unresolved
# reference or a mismatched resolution leaves the row OPEN.
# --------------------------------------------------------------------------- #
_CLI_BRAND_RE = re.compile(r"\b(?:qubit|hermes|openclaw|voicebridge)\b", re.IGNORECASE)


def _normalize_cli_help(text: str) -> str:
    """Deterministic CLI help comparison text (brand + whitespace only).

    Brand names legitimately differ between the Qubit launcher and the pinned
    upstream launcher (``qubit`` vs ``hermes``), and the program name length
    changes argparse line wrapping. Everything else is preserved, so a real
    divergence in flags/subcommands is never collapsed.
    """
    collapsed = _CLI_BRAND_RE.sub("brand", str(text or ""))
    collapsed = re.sub(r"[ \t]+", " ", collapsed)
    collapsed = re.sub(r"\s+", " ", collapsed).strip()
    return collapsed


def _cli_probe_spec(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Resolve a real CLI probe for a row, or ``None`` when not eligible.

    Fail-closed: a command that is not a ``qubit``/``hermes``/``openclaw``
    invocation, a row with no Qubit production mapping, or a flag row (whose
    token is not itself a command) yields ``None`` and keeps the row OPEN.
    """
    command = str(row.get("command") or "").strip()
    if not command:
        return None
    tokens = command.split()
    if not tokens or tokens[0] not in ("qubit", "hermes", "openclaw"):
        return None
    args = [t for t in tokens[1:] if t]
    if not args:
        return None
    # A trailing flag is not a command; appending ``--help`` to it would make
    # argparse fail. Only real subcommand paths are probed.
    if args[-1].startswith("-"):
        return None
    project = str(row.get("upstream_project") or "")
    if project == "qubit" and tokens[0] == "qubit":
        return {
            "kind": CLI_EXECUTION,
            "qubit_argv": args + ["--help"],
            "reference_project": None,
            "reference_argv": None,
        }
    if project in ("hermes", "openclaw") and tokens[0] in ("hermes", "openclaw"):
        # The Qubit product exposes upstream subcommands through the vendored
        # runtime (`qubit agent <args>`); the pinned upstream CLI is the
        # reference. Fail closed if the row carries no Qubit mapping.
        if not (row.get("qubit_symbol") or row.get("qubit_path")):
            return None
        return {
            "kind": CLI_PARITY,
            "qubit_argv": ["agent"] + args + ["--help"],
            "reference_project": project,
            "reference_argv": args + ["--help"],
        }
    return None


def _cli_behavior_pass(
    rows: Sequence[Mapping[str, Any]],
    adapters: Mapping[str, Adapter],
    manifest: Mapping[str, str],
    wanted_ids: set[str] | None,
    wanted_families: set[str] | None,
) -> dict[str, dict[str, Any]]:
    """Batch-execute the real Qubit launcher for every eligible CLI row.

    Returns ``{capability_id: proof}`` only for rows that really ran and (for a
    reference row) normalized-equal help. An absent launcher yields ``{}`` — every
    such row stays OPEN (fail-closed), never a fabricated proof.
    """
    qubit = adapters.get("qubit")
    if qubit is None or not qubit.available() or not getattr(qubit, "binary", None):
        return {}
    specs: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = str(row.get("capability_id"))
        if wanted_ids is not None and cid not in wanted_ids:
            continue
        if wanted_families is not None and str(row.get("family")) not in wanted_families:
            continue
        if str(row.get("disposition") or "OPEN") in TERMINAL_DISPOSITIONS:
            continue
        if row.get("fixture") or manifest.get(cid):
            continue
        if str(row.get("family")) != "cli":
            continue
        spec = _cli_probe_spec(row)
        if spec is not None:
            specs[cid] = spec
    if not specs:
        return {}

    def _probe(item: tuple[str, dict[str, Any]]) -> tuple[str, dict[str, Any] | None]:
        cid, spec = item
        try:
            qres = qubit.execute({"kind": "cli", "argv": list(spec["qubit_argv"])})
        except Exception:  # noqa: BLE001 - a probe failure is fail-closed
            return cid, None
        if qres.exit_code != 0:
            return cid, None
        qnorm = _normalize_cli_help(qres.stdout)
        if not qnorm:
            return cid, None
        proof: dict[str, Any] = {
            "kind": str(spec["kind"]),
            "qubit_argv": list(spec["qubit_argv"]),
            "qubit_exit": int(qres.exit_code),
            "qubit_norm": qnorm,
            "reference_argv": None,
            "reference_exit": None,
            "reference_norm": None,
        }
        if spec["kind"] == CLI_PARITY:
            ref = adapters.get(str(spec["reference_project"]))
            if ref is None or not ref.available() or not getattr(ref, "binary", None):
                return cid, None
            try:
                rres = ref.execute({"kind": "cli", "argv": list(spec["reference_argv"])})
            except Exception:  # noqa: BLE001
                return cid, None
            if rres.exit_code != 0:
                return cid, None
            rnorm = _normalize_cli_help(rres.stdout)
            if rnorm != qnorm:
                return cid, None
            proof["reference_argv"] = list(spec["reference_argv"])
            proof["reference_exit"] = int(rres.exit_code)
            proof["reference_norm"] = rnorm
        return cid, proof

    out: dict[str, dict[str, Any]] = {}
    ordered = sorted(specs.items(), key=lambda item: item[0])
    workers = max(1, min(CLI_PROBE_WORKERS, len(ordered)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for cid, proof in pool.map(_probe, ordered):
            if proof is not None:
                out[cid] = proof
    return out


def _attach_cli_behavior_proof(result: dict[str, Any], proof: Mapping[str, Any]) -> None:
    """Stamp a SAFE_EQUIVALENT proof from a real launcher execution.

    Only :func:`_cli_behavior_pass` (which requires a zero exit and a normalized
    help surface, plus a reference for ``cli_parity``) may call this. No field is
    synthesized from a catalog.
    """
    kind = str(proof.get("kind") or CLI_EXECUTION)
    qhash = result_hash({
        "kind": kind,
        "argv": list(proof.get("qubit_argv") or []),
        "exit_code": proof.get("qubit_exit"),
        "normalized": proof.get("qubit_norm"),
    })
    result["disposition"] = "SAFE_EQUIVALENT"
    result["disposition_basis"] = "safe_equivalent_proven"
    result["proof_level"] = "executed"
    result["proof_kind"] = kind
    result["wiring_proof_status"] = kind
    result["executed"] = True
    result["qubit_executed"] = True
    result["qubit_result_hash"] = qhash
    result["raw_hash"] = qhash
    result["qubit_payload"] = {
        "exit_code": proof.get("qubit_exit"),
        "normalized_help": proof.get("qubit_norm"),
    }
    result["request_schema"] = {"argv": list(proof.get("qubit_argv") or [])}
    result["runtime_provider"] = "qubit-cli"
    if kind == CLI_PARITY:
        rhash = result_hash({
            "kind": kind,
            "argv": list(proof.get("reference_argv") or []),
            "exit_code": proof.get("reference_exit"),
            "normalized": proof.get("reference_norm"),
        })
        result["reference_executed"] = True
        result["upstream_result_hash"] = rhash
        result["reference_result_hash"] = rhash
    result["disposition_proof"] = {
        "kind": kind,
        "executed": True,
        "level": "executed/real-launcher-help-surface",
        "basis": kind,
        "qubit_argv": list(proof.get("qubit_argv") or []),
        "reference_argv": list(proof.get("reference_argv") or []) or None,
        "method": "real qubit launcher subprocess (--help, exit 0)"
                  + (" + pinned upstream CLI normalized-equal" if kind == CLI_PARITY else ""),
    }
    result["verification"] = {
        "executed": True,
        "exit_code": proof.get("qubit_exit"),
        "reference_executed": bool(proof.get("reference_norm")),
        "method": "subprocess + normalized-help hash",
    }
    _fill_cli_evidence(result, list(proof.get("qubit_argv") or []))


def _fill_cli_evidence(result: dict[str, Any], argv: Sequence[str]) -> None:
    """Populate the §8/§15 evidence keys an executed CLI surface can honestly carry."""
    defaults: dict[str, Any] = {
        "voice_utterance": "not-applicable: CLI surface probe",
        "dashboard_route": "not-applicable: CLI surface probe",
        "dashboard_control": "not-applicable: CLI surface probe",
        "broker_policy_decision": "not-applicable: read-only help surface",
        "store_state_assertion": "no state written by --help",
        "output_event_assertion": "cli help output emitted (exit 0)",
        "error_codes": [],
        "retry_recovery_proof": "not-applicable: deterministic --help probe",
        "audit_receipt_proof": None,
        "security_resource_proof": "no network, no filesystem mutation",
        "package_proof": "qubit launcher entry point",
        "response_headers": {},
        "screenshot_ref": "not-applicable: CLI surface probe",
        "dom_snapshot_ref": "not-applicable: CLI surface probe",
        "a11y_snapshot_ref": "not-applicable: CLI surface probe",
    }
    for key, value in defaults.items():
        if result.get(key) is None:
            result[key] = value
    result.setdefault("request_schema", {"argv": list(argv)})


def _voice_behavior_pass(
    rows: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, str],
    wanted_ids: set[str] | None,
    wanted_families: set[str] | None,
) -> dict[str, dict[str, Any]]:
    """Resolve every eligible voice row through the production voice registry.

    Returns ``{capability_id: proof}`` only when the row's canonical op id really
    exists in the registry and one of the entry's own declared examples resolves
    back to that same id (match or clarify). The row must map a real Qubit
    production surface -- the voice registry or the dashboard-ops module it
    dispatches to. Fail-closed on a missing registry, a missing entry, a row
    with no such mapping, or an unknown/ambiguous/wrong-id resolution.
    """
    try:
        vc = _import_voice_commands()
    except Exception:  # noqa: BLE001 - registry unavailable -> rows stay OPEN
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = str(row.get("capability_id"))
        if wanted_ids is not None and cid not in wanted_ids:
            continue
        if wanted_families is not None and str(row.get("family")) not in wanted_families:
            continue
        if str(row.get("disposition") or "OPEN") in TERMINAL_DISPOSITIONS:
            continue
        if row.get("fixture") or manifest.get(cid):
            continue
        # The canonical op is implemented either in the voice registry itself or
        # in the dashboard-ops module that the registry dispatches to. Both are
        # real Qubit production surfaces; the resolver round-trip below (the
        # entry's OWN declared example must resolve back to the row's exact id)
        # is the proof, so a name-level coincidence is never enough.
        qubit_path = str(row.get("qubit_path") or "")
        if not ("voice_commands" in qubit_path or "dashboard_ops.py" in qubit_path):
            continue
        entry_id = str(row.get("qubit_symbol") or "").strip()
        if not entry_id:
            continue
        entry = _voice_registry_lookup(vc, entry_id)
        if entry is None:
            continue
        resolving_example = None
        for example in sorted(str(x) for x in (getattr(entry, "examples", ()) or ())):
            try:
                resolution = vc.resolve(example)
            except Exception:  # noqa: BLE001
                continue
            resolved_id = getattr(getattr(resolution, "entry", None), "id", None)
            if str(getattr(resolution, "kind", "")) in ("match", "clarify") and str(resolved_id) == entry_id:
                resolving_example = example
                break
        if resolving_example is None:
            continue
        out[cid] = {
            "entry_id": entry_id,
            "example": resolving_example,
            "title": str(getattr(entry, "title", "") or ""),
            "backend": str(getattr(entry, "backend", "") or ""),
            "scope": [str(s) for s in (getattr(entry, "scope", ()) or ())],
            "confirm": bool(getattr(entry, "confirm", False)),
            "aliases": [str(a) for a in (getattr(entry, "aliases", ()) or ())],
            "parity": [str(p) for p in (getattr(entry, "parity", ()) or ())],
        }
    return out


def _voice_registry_lookup(vc: Any, entry_id: str) -> Any:
    """Find a voice entry in any of the production registries (or ``None``)."""
    for attr in ("REGISTRY", "PLUGIN_FLEET_REGISTRY", "MODEL_PROFILE_REGISTRY"):
        registry = getattr(vc, attr, None)
        if isinstance(registry, Mapping) and entry_id in registry:
            return registry[entry_id]
    return None


def _attach_voice_resolution_proof(
    result: dict[str, Any], proof: Mapping[str, Any]
) -> None:
    """Stamp a SAFE_EQUIVALENT proof from a real voice-registry resolution."""
    payload = {
        "entry_id": proof.get("entry_id"),
        "example": proof.get("example"),
        "title": proof.get("title"),
        "backend": proof.get("backend"),
        "scope": proof.get("scope"),
        "confirm": proof.get("confirm"),
        "aliases": proof.get("aliases"),
        "parity": proof.get("parity"),
    }
    vhash = result_hash(payload)
    result["disposition"] = "SAFE_EQUIVALENT"
    result["disposition_basis"] = "safe_equivalent_proven"
    result["proof_level"] = "executed"
    result["proof_kind"] = VOICE_RESOLUTION
    result["wiring_proof_status"] = VOICE_RESOLUTION
    result["executed"] = True
    result["qubit_executed"] = True
    result["qubit_result_hash"] = vhash
    result["raw_hash"] = vhash
    result["qubit_payload"] = payload
    result["voice_utterance"] = str(proof.get("example") or "")
    result["runtime_provider"] = str(proof.get("backend") or "voice-registry")
    result["request_schema"] = {"utterance": proof.get("example")}
    result["output_event_assertion"] = f"resolved-entry:{proof.get('entry_id')}"
    result["store_state_assertion"] = "no state written by resolve()"
    result["disposition_proof"] = {
        "kind": VOICE_RESOLUTION,
        "executed": True,
        "level": "executed/voice-registry-resolution",
        "basis": VOICE_RESOLUTION,
        "entry_id": proof.get("entry_id"),
        "example": proof.get("example"),
        "method": "voicebridge.voice_commands.resolve(example) -> same canonical op id",
    }
    result["verification"] = {
        "executed": True,
        "resolved_entry_id": proof.get("entry_id"),
        "method": "production resolver round-trip (match/clarify)",
    }
    for key, value in {
        "dashboard_route": "not-applicable: voice surface probe",
        "dashboard_control": "not-applicable: voice surface probe",
        "broker_policy_decision": "not-applicable: resolve() is pure",
        "error_codes": [],
        "retry_recovery_proof": "not-applicable: pure resolver",
        "audit_receipt_proof": None,
        "security_resource_proof": "no network, no filesystem mutation",
        "package_proof": "voicebridge.voice_commands.REGISTRY",
        "response_headers": {},
        "screenshot_ref": "not-applicable: voice surface probe",
        "dom_snapshot_ref": "not-applicable: voice surface probe",
        "a11y_snapshot_ref": "not-applicable: voice surface probe",
    }.items():
        if result.get(key) is None:
            result[key] = value


def _ast_wiring_test_ids(row: Mapping[str, Any]) -> list[str]:
    """Real tests with a GENUINE linkage to this row's Qubit symbol/module.

    A candidate is admissible only on evidence of linkage; a bare name collision
    or a prose substring is never proof:

    * the test file is an owner-ledger ``proving_test`` for the row's module
      (and symbol, for shared hubs); or
    * a STRONG reference: the symbol appears as ``<qubit-module>.symbol`` with
      the base resolved through an import alias, or the symbol is bound by
      ``from <qubit-module> import symbol``; or
    * a weak reference (bare name / unrelated ``.symbol``) in a file that
      actually imports the Qubit module AND the symbol is not generic.

    Generic symbols (``main``, ``run``, ``status``, ...) never qualify on a weak
    reference, so ``gap_ledger.main`` in an unrelated test cannot prove
    ``voicebridge.terminal:main``. Nothing is synthesised. Every candidate is
    additionally required to reference the row's specific route/symbol
    (:func:`_test_references_row`), so a class-level surface test can never
    dispose an individual route.
    """
    qubit_path = str(row.get("qubit_path") or "")
    qubit_symbol = str(row.get("qubit_symbol") or "")
    if not qubit_path or not qubit_symbol:
        return []
    module = _qubit_module_dotted(qubit_path)
    if not module:
        return []
    name_refs, attr_refs, file_modules, file_bindings, _ = _reference_index()
    ledger_files = set(_ledger_proving_tests(qubit_path, qubit_symbol))
    generic = qubit_symbol in _GENERIC_QUBIT_SYMBOLS
    candidates: set[str] = set(name_refs.get(qubit_symbol, ()))
    candidates.update(attr_refs.get(qubit_symbol, {}))
    out: set[str] = set()
    for test_id in sorted(candidates):
        rel = test_id.split("::", 1)[0]
        if rel in ledger_files:
            out.add(test_id)
            continue
        bindings = file_bindings.get(rel, {})
        strong = module in bindings.get(qubit_symbol, set())
        if not strong:
            for base in attr_refs.get(qubit_symbol, {}).get(test_id, ()):
                resolved = bindings.get(base) or {base}
                if module in resolved:
                    strong = True
                    break
        if strong:
            out.add(test_id)
            continue
        if not generic and _imports_module(
            module, file_modules.get(rel, set()), bindings
        ):
            out.add(test_id)
    return sorted(t for t in out if _test_references_row(t, row))


def _resolved_proof_level(
    row: Mapping[str, Any], res: Mapping[str, Any]
) -> str | None:
    """The strongest REAL proof available: executed, wiring, or None.

    ``wiring`` is satisfied by either a Python ``tests/`` test reference or a
    genuine frontend vitest linkage (:func:`_frontend_linkage`).
    """
    if bool(res.get("executed")) and (
        res.get("qubit_result_hash") or row.get("qubit_result_hash")
    ):
        return "executed"
    if _resolvable_test_references(row, res):
        return "wiring"
    if _frontend_linkage(row):
        return "wiring"
    if _plugin_bundle_linkage(row):
        return "wiring"
    return None


def _executed_proof_level(
    row: Mapping[str, Any], res: Mapping[str, Any]
) -> str | None:
    """The level a RUN may claim from its own execution, never inferred wiring.

    A wiring level is only honored when the row/inventory explicitly declared
    it (the loader attaches it from a real, resolvable test reference). The run
    path must not upgrade an undeclared claim to wiring, otherwise a row with no
    declared proof level would survive on a citation it never made.
    """
    if bool(res.get("executed")) and (
        res.get("qubit_result_hash") or row.get("qubit_result_hash")
    ):
        return "executed"
    level = res.get("proof_level")
    return level if level in SAFE_EQUIVALENT_PROOF_LEVELS else None


def _safe_equivalent_accounted(
    row: Mapping[str, Any], res: Mapping[str, Any]
) -> bool:
    """True when a SAFE_EQUIVALENT success claim carries a valid typed proof.

    The frozen contract accounts a ``SAFE_EQUIVALENT`` row only when a real
    typed implementation/proof backs it: an ``executed`` proof level with a real
    Qubit execution and result hash, or a ``wiring`` proof level with a
    resolvable AND linked proof (a ``tests/`` test that exercises the row's
    route/symbol, a frontend vitest that imports the mapped component, a served
    plugin bundle read by a real test, or a recorded vendor ``behavior_equal``
    reference). A bare mapping or an unresolvable citation is NOT proof and
    stays flagged by ``unexecuted_row``/``pass_without_reference`` (the
    ``proof_level`` and ``safe_equivalent_requires_proof`` gates also fire).
    """
    level = res.get("proof_level")
    if level is None:
        level = row.get("proof_level")
    if level not in SAFE_EQUIVALENT_PROOF_LEVELS:
        return False
    if level == "executed":
        return bool(res.get("executed")) and bool(res.get("qubit_result_hash"))
    frontend = _frontend_linkage(row) or _frontend_linkage(res)
    bundle = _plugin_bundle_linkage(row) or _plugin_bundle_linkage(res)
    vendor = _vendor_behavior_valid(row, res)
    resolvable = (bool(_resolvable_test_references(row, res)) or bool(frontend)
                  or bool(bundle) or vendor)
    linked = (bool(_linked_test_references(row, res)) or bool(frontend)
              or bool(bundle) or vendor)
    return resolvable and linked


def _declares_safe_equivalent_interface(row: Mapping[str, Any]) -> bool:
    """True when a row carries a typed (interface-level) SAFE_EQUIVALENT claim."""
    return (
        str(row.get("disposition") or "") == "SAFE_EQUIVALENT"
        or str(row.get("interface_disposition") or "") == "SAFE_EQUIVALENT"
        or bool(row.get("declared_safe_equivalent_unproven"))
    )


def _resolve_wiring_proof_for_row(row: dict[str, Any]) -> None:
    """Attach a real wiring proof to a typed SAFE_EQUIVALENT row, or leave OPEN.

    Only a resolvable, non-mutating test reference (AST over ``tests/``,
    corroborated by the owner ledger where available) is attached. A row with no
    such reference is left OPEN and records why (``no_linkage``) -- no test id is
    invented and an unfounded citation is never attached. Deterministic:
    ledger-proven files win, then sorted ids.
    """
    qubit_path = str(row.get("qubit_path") or "")
    qubit_symbol = str(row.get("qubit_symbol") or "")
    if not qubit_path or not qubit_symbol:
        row["proof_level"] = None
        row["wiring_proof_status"] = "no_qubit_mapping"
        return
    declared = _resolvable_test_references(row)
    if declared and _linked_test_references(row):
        row["proof_level"] = "wiring"
        row["wiring_proof_status"] = "declared_test_reference"
        return
    candidates = _ast_wiring_test_ids(row)
    ledger_files = set(_ledger_proving_tests(qubit_path, qubit_symbol))
    preferred = [c for c in candidates if c.split("::", 1)[0] in ledger_files]
    chosen = sorted(preferred or candidates)
    if not chosen:
        # No Python ``tests/`` linkage: a mapped frontend component may still be
        # disposed by a REAL vitest suite that imports it (proof type
        # ``frontend_linkage``). ``_frontend_linkage`` never fabricates, so an
        # unsupported row stays OPEN below.
        linkage = _frontend_linkage(row)
        if linkage is not None:
            _attach_frontend_linkage_proof(row, linkage)
            return
        # A compiled plugin page has no ``*.tsx`` source: a real Python test that
        # reads the served bundle AND references the row's control disposes it
        # (proof type ``plugin_bundle_linkage``). Never fabricated; an absent
        # bundle or reading test stays OPEN below.
        bundle_linkage = _plugin_bundle_linkage(row)
        if bundle_linkage is not None:
            _attach_plugin_bundle_proof(row, bundle_linkage)
            return
        row["disposition"] = "OPEN"
        row["proof_level"] = None
        row["wiring_proof_status"] = "no_linkage"
        row["test_ids"] = []
        row["trace_refs"] = []
        return
    test_id = chosen[0]
    basis = (
        "owner_ledger"
        if test_id.split("::", 1)[0] in ledger_files
        else "ast_symbol_reference"
    )
    row["test_ids"] = [test_id]
    row["trace_refs"] = [test_id]
    row["disposition"] = "SAFE_EQUIVALENT"
    row["disposition_basis"] = "safe_equivalent_proven"
    row["proof_level"] = "wiring"
    row["wiring_proof_status"] = "resolved"
    row["disposition_proof"] = {
        "kind": "test_reference",
        "executed": False,
        "level": "wiring/resolution-not-live-execution",
        "basis": basis,
        "test_ids": [test_id],
        "method": "AST test resolution + owner ledger",
    }
    row["verification"] = {
        "exists": True,
        "symbol_present": True,
        "test_resolves": True,
        "method": "static+AST",
    }


def _resolve_inventory_proof(row: dict[str, Any]) -> None:
    """Give every merged inventory row an explicit, honest proof level.

    * A row already SAFE_EQUIVALENT keeps that claim only with a real
      (linked test or executed) proof. If it cites a test that merely RESOLVES
      but does not exercise the row's specific route/symbol, the claim is
      refused: the row is downgraded to OPEN with
      ``wiring_proof_status="no_row_linkage"``. A row that cites no test at all
      keeps its declared disposition and is failed closed by the ``proof_level``
      gate at report time (so the retriage row stays visible rather than being
      silently rewritten).
    * A row with a typed SAFE_EQUIVALENT interface (the retriage-disposed
      upstream delta rows) is re-resolved against the real test tree and owner
      ledger. No candidate => the row stays OPEN, never a fabricated proof.
    """
    if str(row.get("disposition") or "") == "SAFE_EQUIVALENT":
        declared = _resolvable_test_references(row, row)
        if declared and not _linked_test_references(row, row):
            row["disposition"] = "OPEN"
            row["proof_level"] = None
            row["wiring_proof_status"] = "no_row_linkage"
            row["test_ids"] = []
            row["trace_refs"] = []
            row["disposition_basis"] = "no_row_linkage"
            return
        level = _resolved_proof_level(row, row)
        row["proof_level"] = level
        if level is None:
            # A mapping-only SAFE_EQUIVALENT is left as declared (the loader
            # never silently rewrites a merged disposition); the ``proof_level``
            # gate fails it closed at report time. This is what keeps the
            # retriage row visible instead of hiding it.
            row.setdefault("wiring_proof_status", "no_resolvable_test_reference")
        return
    if _declares_safe_equivalent_interface(row):
        _resolve_wiring_proof_for_row(row)


def _declared_terminal_disposition(
    row: Mapping[str, Any], res: Mapping[str, Any]
) -> str | None:
    """Resolve a row's declared terminal disposition, never a synthetic OPEN.

    ``_run_row`` may leave ``res["disposition"] == "OPEN"`` for a row that
    declares a terminal disposition it could not execute. The declared value is
    authoritative here; a plain synthetic OPEN must not shadow it.
    """
    for source in (
        row.get("disposition"),
        res.get("declared_disposition"),
        res.get("disposition"),
    ):
        value = str(source or "")
        if value in TERMINAL_DISPOSITIONS:
            return value
    return None


def _safe_equivalent_proof(row: Mapping[str, Any], res: Mapping[str, Any]) -> bool:
    """The ``safe_equivalent_requires_proof`` standard (mapping + recon proof).

    This is the retriage/F2 compatibility gate. Required: a non-null Qubit
    production mapping (``qubit_path``/``qubit_symbol``/``production_caller``/
    ``disposition_proof``) AND one of the recon proof keys (``test_ids``/
    ``trace_refs``/``disposition_proof``, or a ``verification`` block). It is
    deliberately weaker than the explicit proof LEVEL: a bare mapping is not
    enough for the upstream delta gate (see
    :func:`_has_qubit_disposition_and_proof`) and a declared level must resolve
    (see the ``proof_level*`` gates).
    """
    mapping = (
        res.get("qubit_path")
        or row.get("qubit_path")
        or res.get("production_caller")
        or row.get("qubit_symbol")
        or row.get("disposition_proof")
    )
    if not mapping:
        return False
    if row.get("test_ids") or res.get("test_ids"):
        return True
    if row.get("trace_refs") or res.get("trace_refs"):
        return True
    if row.get("disposition_proof"):
        return True
    verification = row.get("verification")
    if isinstance(verification, Mapping) and (
        verification.get("exists") or verification.get("symbol_present")
    ):
        return True
    return False


def _has_qubit_disposition_and_proof(
    row: Mapping[str, Any], res: Mapping[str, Any]
) -> bool:
    """A delta row is disposed when it has a terminal disposition AND a basis.

    A declared terminal disposition is honored even when the row could not be
    executed (the declared value is not overwritten by a synthetic OPEN).
    ``OPEN`` (not terminal) and ``FAIL`` (a recorded defect) are never disposed
    here: both keep firing the gate.

    A terminal hold/proof is accepted with EITHER a real proof OR an explicit,
    non-empty ``operator_prerequisite``:

    * ``OPERATOR_ONLY`` with a non-empty ``operator_prerequisite`` is a legitimate
      fail-closed closure (the frozen contract's OPERATOR_ONLY disposition with a
      recorded prerequisite); without a prerequisite it still needs a proof
      (production caller / Qubit mapping / trace / executed).
    * ``SAFE_EQUIVALENT`` is disposed only by a REAL proof OR an explicit
      prerequisite: an executed comparison (executed result + result hash), a
      resolvable real ``tests/`` reference, or a genuine frontend vitest linkage
      (a test that imports the mapped component). A bare
      ``qubit_path``/``disposition_proof`` mapping or a name-level
      ``verification`` block is NOT a test and does not dispose the addition (the
      ``safe_equivalent_requires_proof`` gate is intentionally weaker; this gate
      is the upstream honesty check).
    * ``PASS`` must carry a real proof (the mapping/symbol/trace/executed basis);
      a PASS without proof is refused by the ``pass_without_proof`` gate anyway.
    """
    disposition = _declared_terminal_disposition(row, res)
    if disposition is None or disposition == "FAIL":
        return False
    prerequisite = str(
        res.get("operator_prerequisite") or row.get("operator_prerequisite") or ""
    ).strip()
    if disposition == "SAFE_EQUIVALENT":
        if prerequisite:
            return True
        if bool(res.get("executed")) and (
            res.get("qubit_result_hash") or row.get("qubit_result_hash")
        ):
            return True
        if _resolvable_test_references(row, res):
            return True
        return bool(_frontend_linkage(row) or _plugin_bundle_linkage(row))
    proof = (
        res.get("production_caller")
        or row.get("qubit_symbol")
        or row.get("qubit_path")
        or (row.get("trace_refs") or [])
        or res.get("executed")
    )
    if disposition == "OPERATOR_ONLY":
        return bool(prerequisite) or bool(proof)
    return bool(proof)


def missing_evidence_keys(result: Mapping[str, Any]) -> list[str]:
    """Evidence keys that are absent entirely (must exist on every result)."""
    return [field for field in EVIDENCE_FIELDS if field not in result]


def missing_evidence_fields(
    result: Mapping[str, Any], notes: Mapping[str, Any] | None = None
) -> list[str]:
    """Evidence keys missing/``null`` for a PASS/SAFE_EQUIVALENT result.

    Non-success dispositions (OPEN, OPERATOR_ONLY) are exempt here: their
    omissions are caught by the all-inventory coverage test, not this check.
    A field that carries an explicit non-applicability note (fixture
    ``evidence_notes``) is considered accounted for, so "not exposed by this
    surface" is a documented absence rather than a missing proof.
    """
    if str(result.get("disposition") or "OPEN") not in SUCCESS_DISPOSITIONS:
        return []
    notes = notes or {}
    family = str(result.get("family") or "")
    missing: list[str] = []
    for field in EVIDENCE_FIELDS:
        if field in result and result.get(field) is not None:
            continue
        # Visual evidence refs are only required in the visual families.
        if field in VISUAL_REF_FIELDS and family not in VISUAL_FAMILIES:
            continue
        # raw_hash is only required where the raw result is byte-comparable.
        if field == "raw_hash" and result.get("qubit_raw_result_hash") is None:
            continue
        note = notes.get(field)
        if isinstance(note, str) and note.strip():
            continue
        missing.append(field)
    return missing


def _family_diagnostics(
    family: str, expected: Mapping[str, Any], actual: Mapping[str, Any]
) -> list[Diagnostic]:
    """Family-specific additional checks on top of the structured diff."""
    diags: list[Diagnostic] = []
    if family in ("ws", "rpc", "gateway"):
        for key in ("handshake", "scopes", "events", "reconnect", "auth"):
            if key in expected or key in actual:
                _compare_node(expected.get(key), actual.get(key), f"$.{key}", diags, False)
    if family == "sse" and ("events" in expected or "events" in actual):
        _compare_node(expected.get("events"), actual.get("events"), "$.events", diags, False)
    if family in ("session", "memory", "board", "task", "recovery", "file", "project"):
        for key in ("persisted_state", "receipts"):
            if key in expected or key in actual:
                _compare_node(expected.get(key), actual.get(key), f"$.{key}", diags, False)
    if family == "voice":
        # Only keys the fixture's ``expected`` contracts are asserted: an
        # extra resolved-identity/confirmation field on the actual payload is
        # supporting evidence, not a difference (mirrors
        # :func:`compare_expected_actual`).
        for key in ("voice_operation", "resolved_identity", "confirmation"):
            if key in expected:
                _compare_node(expected.get(key), actual.get(key), f"$.{key}", diags, False)
    if family == "provider" and ("resolved_identity" in expected or "resolved_identity" in actual):
        _compare_node(
            expected.get("resolved_identity"), actual.get("resolved_identity"),
            "$.resolved_identity", diags, False,
        )
    # ``help_contains`` is a Qubit-side DIRECTIVE: it asserts the CLI help text
    # advertises the canonical command. It is never a data key of a result
    # payload, so the structured diff ignores it (see :func:`_compare_node`) and
    # it is consumed only here, against the Qubit actual. Declaring it stays
    # fail-closed: absent/incorrect help text reports ``help_missing``, and an
    # empty directive falls back to the canonical command rather than passing
    # vacuously.
    if "help_contains" in expected:
        required = expected.get("help_contains")
        if not isinstance(required, (list, tuple)):
            required = [required]
        required = [str(x) for x in required if str(x).strip()]
        if not required:
            required = [CANONICAL_QUBIT_COMMAND]
        diags += check_help_text(actual, required, "$.help")
    if family in ("api", "gateway", "webhook", "rpc", "ws", "sse"):
        if "response_headers" in expected or "response_headers" in actual:
            diags += compare_response_headers(
                expected.get("response_headers"), actual.get("response_headers"),
                "$.response_headers",
            )
    if family in VISUAL_FAMILIES:
        for key in ("dom", "accessibility", "backend_operation"):
            if key in expected or key in actual:
                _compare_node(expected.get(key), actual.get(key), f"$.{key}", diags, False)
        for key in ("screenshot", "screenshots", "screenshot_evidence",
                    "dom_snapshot", "accessibility_snapshot"):
            if key in expected:
                _compare_node(expected.get(key), actual.get(key), f"$.{key}", diags, False)
    return diags


# --------------------------------------------------------------------------- #
# Adapters
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class CallResult:
    """Normalized adapter result; absent fields are omitted from payloads."""

    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    http_status: int | None = None
    json: Any = None
    events: Any = None
    persisted_state: Any = None
    headers: Any = None
    dom: Any = None
    accessibility: Any = None
    voice_operation: Any = None
    # Voice resolution contract: the confirmation requirement/tier for the
    # resolved canonical operation (`required`, `staged`). ``staged`` is the
    # RESOLUTION tier (this op must stage before it can execute), never a claim
    # that resolution wrote the shared journal — a resolve-only adapter mutates
    # nothing. Transport adapters for other kinds leave it unset.
    confirmation: Any = None
    resolved_identity: Any = None
    receipts: Any = None
    backend_operation: Any = None
    duration_ms: int | None = None
    transport: str | None = None
    meta: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for field in dataclasses.fields(self):
            if field.name in ("meta", "duration_ms"):
                # ``duration_ms`` is volatile wall-clock evidence; it is
                # reported on the result but never hashed.
                continue
            value = getattr(self, field.name)
            if value is not None:
                payload[field.name] = value
        return payload


class Adapter:
    """Protocol: an adapter turns a request spec into a :class:`CallResult`."""

    name = "adapter"
    project = "qubit"
    is_mock = False

    def available(self) -> bool:
        raise NotImplementedError

    def execute(self, request: Mapping[str, Any]) -> CallResult:
        raise NotImplementedError


def _resolve_env_ref(value: Any) -> tuple[bool, str, str | None]:
    """Resolve a fixture env value. Returns (ok, resolved, missing_name)."""
    if not isinstance(value, str):
        return True, str(value), None
    ref = _REF_RE.match(value)
    if ref:
        name = ref.group(1)
        if name in os.environ:
            return True, os.environ[name], None
        return False, "", name
    ref = _ENV_REF_RE.match(value)
    if ref:
        name = ref.group(1)
        if name in os.environ:
            return True, os.environ[name], None
        return False, "", name
    return True, value, None


# --------------------------------------------------------------------------- #
# Transport adapters (v1.2 contract; stdlib only)
# --------------------------------------------------------------------------- #
class TransportUnavailable(RuntimeError):
    """A declared transport could not resolve a URL/token or could not connect.

    ``_run_row`` maps this to OPERATOR_ONLY with an operator_prerequisite: an
    unreachable transport is never PASS (and is not automatically FAIL).
    """


def _transport_timeout() -> float:
    raw = os.environ.get("QUBIT_DIFFERENTIAL_TRANSPORT_TIMEOUT")
    if raw:
        try:
            return max(0.5, float(raw))
        except ValueError:
            pass
    return 5.0


def _redact_url(url: str) -> str:
    """Strip userinfo/query/fragment so no token can reach a report."""
    try:
        parts = urllib.parse.urlsplit(str(url))
    except ValueError:
        return "<url>"
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _default_port(scheme: str) -> int:
    return 443 if scheme in ("https", "wss") else 80


def _join_url(base: str, path: Any) -> str:
    """Join a base URL and a request path, refusing an off-host absolute URL.

    An absolute ``http(s)://`` / ``ws(s)://`` path is only allowed when its host
    (and scheme/port) match the resolved base URL, so a fixture can never
    redirect the runner — and the auth token attached to it — to another host.
    """
    if not path:
        return base
    text = str(path)
    if text.startswith(("http://", "https://", "ws://", "wss://")):
        base_parts = urllib.parse.urlsplit(base)
        path_parts = urllib.parse.urlsplit(text)
        base_host = (base_parts.hostname or "").lower()
        path_host = (path_parts.hostname or "").lower()
        if not path_host or path_host != base_host:
            raise TransportUnavailable(
                "absolute request URL host mismatch: "
                f"{_redact_url(text)} is not the resolved base host "
                f"{base_host!r}; refusing to send the auth token off-host"
            )
        base_port = base_parts.port or _default_port(base_parts.scheme)
        path_port = path_parts.port or _default_port(path_parts.scheme)
        if path_parts.scheme != base_parts.scheme or path_port != base_port:
            raise TransportUnavailable(
                "absolute request URL host mismatch: "
                f"{_redact_url(text)} targets a different scheme/port than the "
                f"resolved base {_redact_url(base)}; refusing an off-base request"
            )
        return text
    return base.rstrip("/") + "/" + text.lstrip("/")


def _looks_like_env_ref(value: Any) -> bool:
    return isinstance(value, str) and bool(
        _REF_RE.match(value) or _ENV_REF_RE.match(value)
    )


def _url_origin(url: Any) -> tuple[str, str, int] | None:
    """Exact ``(scheme, host, port)`` origin of a URL, or ``None`` if invalid.

    The port is made explicit (defaulting by scheme) so ``http://h`` and
    ``http://h:80`` compare equal, while ``http://h:9000`` never matches
    ``http://h:8080``.
    """
    try:
        parts = urllib.parse.urlsplit(str(url))
    except (ValueError, TypeError):
        return None
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None:
        port = _default_port(scheme)
    return (scheme, host, int(port))


def _operator_allowed_origins(project: str) -> set[tuple[str, str, int]]:
    """Origins a literal ``base_url`` may resolve to (fail-closed).

    Exactly the resolved operator base origin
    (``QUBIT_DIFFERENTIAL_<PROJECT>_URL``) plus every explicit
    ``QUBIT_DIFFERENTIAL_ALLOWED_ORIGINS`` entry. Host-only matching is refused:
    the scheme and port must match too, so the operator token is never sent to a
    different port (or scheme) on the same host.
    """
    origins: set[tuple[str, str, int]] = set()
    env_value = os.environ.get(f"QUBIT_DIFFERENTIAL_{str(project).upper()}_URL")
    if env_value and env_value.strip():
        origin = _url_origin(env_value.strip())
        if origin is not None:
            origins.add(origin)
    for item in (os.environ.get(ALLOWED_ORIGINS_ENV) or "").split(","):
        item = item.strip()
        if not item:
            continue
        origin = _url_origin(item)
        if origin is not None:
            origins.add(origin)
    return origins


def _literal_base_url_allowed(project: str, url: str) -> tuple[bool, str]:
    """Validate a literal ``base_url`` by its exact resolved origin.

    Allowed only when the literal's ``(scheme, host, port)`` equals the resolved
    operator base origin or an explicit
    ``QUBIT_DIFFERENTIAL_ALLOWED_ORIGINS`` entry. A loopback host on a different
    port is refused: host-only allowlisting let a fixture hand the operator
    token to any other local service (SSRF residual R2).
    """
    candidate = _url_origin(url)
    if candidate is None:
        return False, f"literal base_url is not a valid http(s) origin: {_redact_url(url)}"
    allowed = _operator_allowed_origins(project)
    if candidate in allowed:
        return True, ""
    env_name = f"QUBIT_DIFFERENTIAL_{str(project).upper()}_URL"
    rendered = sorted(f"{s}://{h}:{p}" for (s, h, p) in allowed) or []
    return False, (
        f"literal base_url origin {candidate[0]}://{candidate[1]}:{candidate[2]} "
        f"is not the resolved base origin (allowed: {rendered}); set {env_name} "
        f"or add it to {ALLOWED_ORIGINS_ENV}"
    )


def _resolve_base_url(project: str, spec: Mapping[str, Any]) -> tuple[str | None, str]:
    """Resolve the base URL from a declared ref or the project env var.

    Env refs are operator-provided and trusted; a literal ``base_url`` is
    validated against the host allowlist (see :func:`_literal_base_url_allowed`)
    and refused otherwise, so the auth token can never reach an off-allowlist
    host.
    """
    declared = spec.get("base_url_ref") or spec.get("base_url")
    if isinstance(declared, str) and declared.strip():
        declared = declared.strip()
        if _looks_like_env_ref(declared):
            ok, resolved, missing = _resolve_env_ref(declared)
            if not ok or not resolved:
                return None, f"base URL reference {missing!r} is not set"
            return resolved.rstrip("/"), ""
        allowed, reason = _literal_base_url_allowed(project, declared)
        if not allowed:
            return None, reason
        return declared.rstrip("/"), ""
    env_name = f"QUBIT_DIFFERENTIAL_{str(project).upper()}_URL"
    value = os.environ.get(env_name)
    if value and value.strip():
        return value.strip().rstrip("/"), ""
    return None, f"no base URL for {project} (set {env_name})"


def _resolve_transport_headers(
    project: str, spec: Mapping[str, Any]
) -> dict[str, str]:
    """Build outgoing headers; token VALUES resolve from env refs only.

    The resolved token is placed on the outgoing request only and is never
    copied into a CallResult, log, fixture or report.
    """
    headers: dict[str, str] = {}
    raw_headers = spec.get("headers")
    if isinstance(raw_headers, Mapping):
        for key, value in raw_headers.items():
            if _looks_like_env_ref(value):
                ok, resolved, missing = _resolve_env_ref(value)
                if not ok:
                    raise TransportUnavailable(
                        f"header reference {missing!r} is not set")
                headers[str(key)] = resolved
            else:
                headers[str(key)] = str(value)
    auth = spec.get("auth")
    if isinstance(auth, Mapping):
        header = auth.get("header")
        value_ref = auth.get("value_ref") or auth.get("value")
        if header:
            if value_ref is None:
                raise TransportUnavailable(
                    f"auth header {header!r} declared without a value reference")
            ok, resolved, missing = _resolve_env_ref(value_ref)
            if not ok:
                raise TransportUnavailable(
                    f"auth reference {missing!r} is not set")
            headers[str(header)] = resolved
    return headers


def _http_request(
    project: str, spec: Mapping[str, Any], timeout: float, transport: str
) -> CallResult:
    """Single HTTP request (also used by the rpc transport)."""
    base, reason = _resolve_base_url(project, spec)
    if base is None:
        raise TransportUnavailable(reason)
    headers = _resolve_transport_headers(project, spec)
    method = str(spec.get("method") or "GET").upper()
    body = spec.get("body")
    data: bytes | None = None
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = json.dumps(body, sort_keys=True, ensure_ascii=True).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
    url = _join_url(base, spec.get("path"))
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            raw_headers = dict(response.headers.items()) if response.headers else {}
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw_headers = dict(exc.headers.items()) if exc.headers else {}
        raw = exc.read()
    except (urllib.error.URLError, ssl.SSLError, socket.timeout, TimeoutError, OSError) as exc:
        raise TransportUnavailable(
            f"{project} {transport} transport unreachable at "
            f"{_redact_url(url)}: {exc}") from exc
    duration = int((time.monotonic() - start) * 1000)
    text = raw.decode("utf-8", "replace")
    return CallResult(
        http_status=status,
        headers=canonical_transport_headers(raw_headers),
        json=_parse_json_maybe(text),
        stdout=text,
        duration_ms=duration,
        transport=transport,
        meta={
            "adapter": project,
            "transport": transport,
            "url": _redact_url(url),
            "method": method,
        },
    )


_SSE_MAX_EVENTS = 500
_SSE_MAX_BYTES = 2_000_000


def _sse_request(
    project: str, spec: Mapping[str, Any], timeout: float
) -> CallResult:
    """Server-sent events, preserving server order and bounding the read."""
    base, reason = _resolve_base_url(project, spec)
    if base is None:
        raise TransportUnavailable(reason)
    headers = _resolve_transport_headers(project, spec)
    headers.setdefault("Accept", "text/event-stream")
    method = str(spec.get("method") or "GET").upper()
    body = spec.get("body")
    data: bytes | None = None
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = json.dumps(body, sort_keys=True, ensure_ascii=True).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
    url = _join_url(base, spec.get("path"))
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    start = time.monotonic()
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raw_headers = dict(exc.headers.items()) if exc.headers else {}
        text = exc.read().decode("utf-8", "replace")
        return CallResult(
            http_status=exc.code,
            headers=canonical_transport_headers(raw_headers),
            json=_parse_json_maybe(text),
            stdout=text,
            events=[],
            duration_ms=int((time.monotonic() - start) * 1000),
            transport="sse",
            meta={"adapter": project, "transport": "sse", "url": _redact_url(url)},
        )
    except (urllib.error.URLError, ssl.SSLError, socket.timeout, TimeoutError, OSError) as exc:
        raise TransportUnavailable(
            f"{project} sse transport unreachable at "
            f"{_redact_url(url)}: {exc}") from exc

    status = getattr(response, "status", None)
    raw_headers = dict(response.headers.items()) if response.headers else {}
    events: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    truncated = False
    truncation_reason: str | None = None
    total_bytes = 0
    try:
        with response:
            for raw_line in response:
                total_bytes += len(raw_line)
                if total_bytes > _SSE_MAX_BYTES or len(events) >= _SSE_MAX_EVENTS:
                    truncated = True
                    truncation_reason = "event/byte bound reached"
                    break
                line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
                if line == "":
                    if current:
                        events.append(current)
                        current = {}
                    continue
                if line.startswith(":"):
                    continue
                field, _, value = line.partition(":")
                if value.startswith(" "):
                    value = value[1:]
                if field == "data":
                    current["data"] = (
                        current["data"] + "\n" + value if "data" in current else value
                    )
                elif field in ("event", "id", "retry"):
                    current[field] = value
    except (socket.timeout, TimeoutError, ssl.SSLError, OSError) as exc:
        truncated = True
        truncation_reason = f"stream ended: {exc}"
    if current:
        events.append(current)
    persisted: dict[str, Any] = {"truncated": truncated, "event_count": len(events)}
    if truncation_reason:
        persisted["truncation_reason"] = truncation_reason
    return CallResult(
        http_status=status,
        headers=canonical_transport_headers(raw_headers),
        events=events,
        persisted_state=persisted,
        duration_ms=int((time.monotonic() - start) * 1000),
        transport="sse",
        meta={"adapter": project, "transport": "sse", "url": _redact_url(url)},
    )


_WS_MAX_FRAMES = 50
_WS_MAX_BYTES = 1_000_000


def _recv_exact(sock: socket.socket, buf: bytes, n: int) -> tuple[bytes, bytes]:
    while len(buf) < n:
        chunk = sock.recv(max(4096, n - len(buf)))
        if not chunk:
            raise ConnectionError("connection closed by peer")
        buf += chunk
    return buf[:n], buf[n:]


def _read_ws_frame(sock: socket.socket, buf: bytes) -> tuple[bool, int, bytes, bytes]:
    """Read one RFC6455 frame. Server frames are unmasked."""
    head, buf = _recv_exact(sock, buf, 2)
    first, second = head[0], head[1]
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        ext, buf = _recv_exact(sock, buf, 2)
        length = int.from_bytes(ext, "big")
    elif length == 127:
        ext, buf = _recv_exact(sock, buf, 8)
        length = int.from_bytes(ext, "big")
    mask = b""
    if masked:
        mask, buf = _recv_exact(sock, buf, 4)
    payload, buf = _recv_exact(sock, buf, length)
    if masked:
        payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
    return fin, opcode, payload, buf


def _send_ws_text(sock: socket.socket, text: str) -> None:
    """Send a client text frame (clients MUST mask)."""
    data = text.encode("utf-8")
    mask = os.urandom(4)
    header = bytes([0x81])
    length = len(data)
    if length < 126:
        header += bytes([0x80 | length])
    elif length < 65536:
        header += bytes([0x80 | 126]) + length.to_bytes(2, "big")
    else:
        header += bytes([0x80 | 127]) + length.to_bytes(8, "big")
    masked = bytes(data[i] ^ mask[i % 4] for i in range(length))
    sock.sendall(header + mask + masked)


def _collect_ws_frames(
    sock: socket.socket, initial: bytes, spec: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], bool, str | None]:
    events: list[dict[str, Any]] = []
    buf = initial
    truncated = False
    reason: str | None = None
    total_bytes = 0
    while True:
        if len(events) >= _WS_MAX_FRAMES or total_bytes > _WS_MAX_BYTES:
            truncated = True
            reason = "frame/byte bound reached"
            break
        try:
            fin, opcode, payload, buf = _read_ws_frame(sock, buf)
        except (socket.timeout, TimeoutError, ssl.SSLError, OSError, ConnectionError) as exc:
            truncated = True
            reason = f"read ended: {exc}"
            break
        total_bytes += len(payload)
        if opcode == 0x1:  # text
            events.append({"event": "message", "data": payload.decode("utf-8", "replace")})
        elif opcode == 0x2:  # binary
            events.append({"event": "binary", "length": len(payload)})
        elif opcode == 0x8:  # close
            code = int.from_bytes(payload[:2], "big") if len(payload) >= 2 else None
            events.append({"event": "close", "code": code,
                           "reason": payload[2:].decode("utf-8", "replace")})
            break
        elif opcode == 0x9:  # ping -> pong
            events.append({"event": "ping"})
            try:
                mask = os.urandom(4)
                masked = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
                header = bytes([0x8A, 0x80 | len(payload)]) if len(payload) < 126 else None
                if header is not None:
                    sock.sendall(header + mask + masked)
            except OSError:
                pass
        elif opcode == 0xA:  # pong
            events.append({"event": "pong"})
        if fin is False:
            # Fragment continuation is out of scope; note it rather than guess.
            events.append({"event": "fragment-unsupported"})
    return events, truncated, reason


def _ws_request(
    project: str, spec: Mapping[str, Any], timeout: float
) -> CallResult:
    """Minimal RFC6455 client: handshake + bounded frame capture."""
    base, reason = _resolve_base_url(project, spec)
    if base is None:
        raise TransportUnavailable(reason)
    parts = urllib.parse.urlsplit(base)
    secure = parts.scheme in ("https", "wss")
    host = parts.hostname
    if not host:
        raise TransportUnavailable(f"ws base URL has no host: {_redact_url(base)}")
    port = parts.port or (443 if secure else 80)
    full = _join_url(base, spec.get("path"))
    full_parts = urllib.parse.urlsplit(full)
    resource = full_parts.path or "/"
    if full_parts.query:
        resource += "?" + full_parts.query
    headers = _resolve_transport_headers(project, spec)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    lines = [
        f"GET {resource} HTTP/1.1",
        f"Host: {host}:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    lines += [f"{k}: {v}" for k, v in headers.items()]
    request_bytes = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", "replace")
    start = time.monotonic()
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        if secure:
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=host)
        sock.sendall(request_bytes)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 65536:
                break
    except (socket.timeout, TimeoutError, ssl.SSLError, OSError) as exc:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        raise TransportUnavailable(
            f"{project} ws transport unreachable at {_redact_url(full)}: {exc}"
        ) from exc

    head, _, rest = buf.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace") if head else ""
    match = re.match(r"HTTP/\d(?:\.\d)?\s+(\d+)", status_line)
    status = int(match.group(1)) if match else None
    raw_headers: dict[str, Any] = {}
    for line in head.split(b"\r\n")[1:]:
        name, _, value = line.decode("latin-1", "replace").partition(":")
        if name.strip():
            raw_headers[name.strip()] = value.strip()

    events: list[dict[str, Any]] = []
    truncated = False
    truncation_reason: str | None = None
    if status == 101:
        for message in spec.get("messages") or []:
            text = message if isinstance(message, str) else json.dumps(
                message, sort_keys=True, ensure_ascii=True)
            try:
                _send_ws_text(sock, text)
            except OSError as exc:
                truncated = True
                truncation_reason = f"send failed: {exc}"
                break
        else:
            events, truncated, truncation_reason = _collect_ws_frames(sock, rest, spec)
    else:
        truncated = True
        truncation_reason = f"handshake returned {status}"
    try:
        sock.close()
    except OSError:
        pass

    persisted: dict[str, Any] = {
        "handshake_status": status,
        "reconnect_attempts": 0,
        "truncated": truncated,
        "event_count": len(events),
    }
    if truncation_reason:
        persisted["truncation_reason"] = truncation_reason
    return CallResult(
        http_status=status,
        headers=canonical_transport_headers(raw_headers),
        events=events,
        persisted_state=persisted,
        duration_ms=int((time.monotonic() - start) * 1000),
        transport="ws",
        meta={"adapter": project, "transport": "ws", "url": _redact_url(full)},
    )


_HTTP_METHODS = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"}
)


def _rpc_request(
    project: str, spec: Mapping[str, Any], timeout: float
) -> CallResult:
    """JSON-RPC-over-HTTP: build/forward the request envelope."""
    rpc_method = spec.get("rpc_method")
    params = spec.get("params")
    body = spec.get("body")
    declared_method = spec.get("method")
    if (rpc_method is None and isinstance(declared_method, str)
            and declared_method.upper() not in _HTTP_METHODS):
        rpc_method = declared_method
    if body is None and (rpc_method is not None or params is not None):
        body = {"type": "req", "id": 1, "method": rpc_method,
                "params": params if params is not None else {}}
    http_method = spec.get("http_method") or ("POST" if body is not None else "GET")
    http_spec = dict(spec)
    http_spec["method"] = http_method
    http_spec["body"] = body
    return _http_request(project, http_spec, timeout, "rpc")


def _execute_transport(
    project: str, kind: str, spec: Mapping[str, Any], timeout: float
) -> CallResult:
    """Dispatch a transport spec, supporting an optional multi-step sequence."""
    sequence = spec.get("sequence")
    if isinstance(sequence, list) and sequence:
        return _execute_transport_sequence(project, kind, spec, sequence, timeout)
    if kind == "http":
        return _http_request(project, spec, timeout, "http")
    if kind == "rpc":
        return _rpc_request(project, spec, timeout)
    if kind == "sse":
        return _sse_request(project, spec, timeout)
    if kind == "ws":
        return _ws_request(project, spec, timeout)
    raise NotImplementedError(f"{project}: kind {kind!r} is not a transport")


def _sequence_step_executes(step: Mapping[str, Any]) -> bool:
    """True when a sequence step describes a real network operation.

    Purely declarative steps (``handshake`` expectations, ``reconnect``
    attempt counts) are recorded, not executed as separate connections.
    """
    name = str(step.get("step") or "")
    if name in ("handshake", "reconnect", "expect"):
        return False
    if "attempts" in step and name == "reconnect":
        return False
    return any(key in step for key in ("path", "body", "argv", "params"))

def _execute_transport_sequence(
    project: str,
    kind: str,
    spec: Mapping[str, Any],
    sequence: Sequence[Any],
    timeout: float,
) -> CallResult:
    """Run a multi-step handshake/reconnect sequence, recording each step."""
    merged_events: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    persisted: dict[str, Any] = {"sequence": steps, "reconnect_attempts": 0}
    last: CallResult | None = None
    for index, raw_step in enumerate(sequence):
        step = dict(raw_step) if isinstance(raw_step, Mapping) else {}
        name = str(step.get("step") or "")
        if "attempts" in step:
            try:
                persisted["reconnect_attempts"] = int(step.get("attempts") or 0)
            except (TypeError, ValueError):
                pass
        if not _sequence_step_executes(step):
            steps.append({"step": index, "name": name, "executed": False,
                          "declarative": True})
            continue
        step_kind = str(step.get("kind")
                        or step.get("transport")
                        or (kind if kind != "sequence" else "http"))
        for inherited in ("base_url", "base_url_ref", "auth", "headers", "path"):
            if inherited not in step and inherited in spec:
                step[inherited] = spec[inherited]
        try:
            call = _execute_transport(project, step_kind, step, timeout)
        except TransportUnavailable as exc:
            persisted["reconnect_attempts"] = int(persisted["reconnect_attempts"]) + 1
            steps.append({"step": index, "name": name, "kind": step_kind,
                          "executed": False, "reason": str(exc)})
            if last is None:
                raise
            persisted["truncated"] = True
            persisted["truncation_reason"] = f"step {index} unavailable: {exc}"
            break
        last = call
        steps.append({"step": index, "name": name, "kind": step_kind,
                      "executed": True, "http_status": call.http_status})
        if isinstance(call.events, list):
            merged_events.extend(call.events)
        if isinstance(call.persisted_state, Mapping):
            for key, value in call.persisted_state.items():
                persisted.setdefault(key, value)
    if last is None:
        raise TransportUnavailable(
            f"{project} sequence had no executed step (all unavailable)")
    persisted.setdefault("truncated", False)
    return CallResult(
        exit_code=last.exit_code,
        stdout=last.stdout,
        stderr=last.stderr,
        http_status=last.http_status,
        json=last.json,
        events=merged_events,
        persisted_state=persisted,
        headers=last.headers,
        duration_ms=last.duration_ms,
        transport=last.transport,
        meta=last.meta,
    )


# --------------------------------------------------------------------------- #
# Voice transport (INTERFACE v1.3; resolve-only, side-effect free)
# --------------------------------------------------------------------------- #
# A voice fixture is driven through the REAL Qubit voice registry/resolver
# (``voicebridge.voice_commands.resolve``), the same pure function the CLI,
# dashboard, and audio turn path call. Resolution only: it never dispatches a
# backend, stages or cancels a confirmation, touches audio/TTS/ASR, calls a
# subprocess, or opens a socket. Confirm-tier operations are reported as their
# confirmation CONTRACT (required/staged tier, cancel, expiry, replay); the
# shared journal is never written. Reference projects have no side-effect-free
# utterance resolver, so a reference voice request stays unavailable
# (OPERATOR_ONLY, never PASS).
VOICE_RESOLVER_MODULE = "voicebridge.voice_commands"
VOICE_TRANSPORT = "voice"


class VoiceResolverUnavailable(TransportUnavailable):
    """The real Qubit voice registry/resolver could not be imported or resolved.

    Subclasses ``TransportUnavailable`` so the runner classifies the row
    OPERATOR_ONLY with an exact prerequisite (never PASS, never an uncaught
    internal error).
    """


def _import_voice_commands() -> Any:
    """Import ``voicebridge.voice_commands`` read-only (no side effects)."""
    import importlib

    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        return importlib.import_module(VOICE_RESOLVER_MODULE)
    except Exception as exc:  # pragma: no cover - environment dependent
        raise VoiceResolverUnavailable(
            f"Qubit voice resolver {VOICE_RESOLVER_MODULE!r} is not importable "
            f"from {root}: {redact_text(str(exc))}"
        ) from exc


def _fixture_transport_kind(fixture: Mapping[str, Any]) -> str:
    """The declared transport kind (``transport`` or ``request.kind``)."""
    transport = fixture.get("transport")
    if isinstance(transport, str) and transport.strip():
        return transport.strip().lower()
    request = fixture.get("request")
    if isinstance(request, Mapping):
        kind = request.get("kind")
        if isinstance(kind, str) and kind.strip():
            return kind.strip().lower()
    return ""


def _fixture_is_voice(fixture: Mapping[str, Any]) -> bool:
    """True when a fixture is driven by the in-process voice resolver."""
    return _fixture_transport_kind(fixture) == VOICE_TRANSPORT


def _voice_utterance(spec: Mapping[str, Any]) -> str:
    """Extract the utterance from a voice spec (never from argv/secrets)."""
    body = spec.get("body")
    text: Any = body.get("text") if isinstance(body, Mapping) else None
    if not isinstance(text, str) or not text.strip():
        text = spec.get("utterance") or spec.get("voice_utterance")
    if not isinstance(text, str) or not text.strip():
        argv = spec.get("argv")
        if isinstance(argv, (list, tuple)) and argv:
            text = " ".join(str(a) for a in argv)
    return text.strip() if isinstance(text, str) else ""


def _voice_confirmation_contract(confirm: bool, ttl: float) -> dict[str, Any]:
    """Stable confirmation/cancel/expiry/replay contract for a resolved op.

    ``staged`` is the resolution TIER (a confirm-gated op must stage before it
    can execute); ``applied``/``journal_write`` are always False because
    resolution alone never mutates the shared journal.
    """
    return {
        "required": bool(confirm),
        "staged": bool(confirm),
        "applied": False,
        "journal_write": False,
        "journal": "shared confirmation journal" if confirm else None,
        "ttl_s": ttl if confirm else None,
        "expires_in_s": ttl if confirm else None,
        "single_use": True if confirm else None,
        "cancellable": True if confirm else None,
        "replay": False if confirm else None,
    }


def _execute_voice(project: str, spec: Mapping[str, Any]) -> CallResult:
    """Resolve one utterance through the real Qubit voice registry (pure)."""
    if project != "qubit":
        raise VoiceResolverUnavailable(
            f"{project}: no side-effect-free voice resolver exists "
            "(reference voice resolution is unavailable; row is OPERATOR_ONLY)"
        )
    utterance = _voice_utterance(spec)
    if not utterance:
        raise VoiceResolverUnavailable(
            "voice fixture declares no utterance (request.body.text is empty)")
    vc = _import_voice_commands()
    try:
        resolution = vc.resolve(utterance)
    except Exception as exc:  # pragma: no cover - resolver is pure in practice
        raise VoiceResolverUnavailable(
            f"Qubit voice resolver failed: {redact_text(str(exc))}"
        ) from exc

    entry = getattr(resolution, "entry", None)
    entry_id = getattr(entry, "id", None) if entry is not None else None
    backend = getattr(entry, "backend", None) if entry is not None else None
    title = getattr(entry, "title", None) if entry is not None else None
    confirm = bool(getattr(entry, "confirm", False)) if entry is not None else False
    # Report the entry's declared aliases (deterministic registry data) rather
    # than claiming which alias matched: ``Resolution`` does not expose the
    # matched alias, and guessing from ``aliases[0]`` would misreport entries
    # whose longest alias differs from the first.
    aliases = list(getattr(entry, "aliases", ()) or ()) if entry is not None else []
    fixed = dict(getattr(entry, "fixed", ()) or ()) if entry is not None else {}
    args = dict(getattr(resolution, "args", {}) or {})
    kind = str(getattr(resolution, "kind", "") or "")
    candidates = [
        getattr(candidate, "id", None)
        for candidate in (getattr(resolution, "candidates", ()) or ())
    ]
    if kind != "match" or entry is None:
        # No canonical operation was resolved (unknown/clarify/ambiguous): the
        # capability is absent for this utterance. Fail closed as OPERATOR_ONLY
        # with an exact prerequisite rather than manufacture a payload that
        # could never be a real match.
        detail = f"resolution kind={kind or 'unknown'!r}"
        if candidates:
            detail += f", candidates={sorted(c for c in candidates if c)}"
        raise VoiceResolverUnavailable(
            f"utterance did not resolve to a canonical operation ({detail}); "
            "voice row is OPERATOR_ONLY, never PASS")
    ttl = float(getattr(vc, "CONFIRM_TTL_S", 0) or 0)
    confirmation = _voice_confirmation_contract(confirm, ttl)
    json_block = {
        "mode": "resolve-only",
        "side_effects": "none",
        "kind": kind,
        "entry_id": entry_id,
        "backend": backend,
        "title": title,
        "aliases": aliases,
        "extracted_args": args,
        "fixed_args": fixed,
        "candidates": candidates,
        "confirmation": confirmation,
        "cancel": {
            "required": bool(confirm),
            "decision": "deny" if confirm else None,
            "backend": "cancel_pending" if confirm else None,
            "executes": False,
        },
        "expiry": {
            "ttl_s": ttl if confirm else None,
            "on_expiry": "aborted" if confirm else None,
        },
        "replay": {
            "second_settle": "idempotent_noop" if confirm else None,
            "revalidate_args_from_journal": bool(confirm),
            "execute_once": bool(confirm),
        },
        "source": "voicebridge/voice_commands.py:resolve",
    }
    persisted = {
        "entry_id": entry_id,
        "resolved_entry_id": entry_id,
        "journal": "shared confirmation journal" if confirm else None,
        "journal_staged": False,
        "staged": False,
        "cancelled": False,
        "expired": False,
        "replayed": False,
    }
    return CallResult(
        exit_code=0,
        json=json_block,
        voice_operation={
            "entry_id": entry_id,
            "backend": backend,
            "args": args,
            "title": title,
            "aliases": aliases,
            "resolution_kind": kind,
        },
        confirmation=confirmation,
        persisted_state=persisted,
        transport=VOICE_TRANSPORT,
        meta={"adapter": "voice", "project": project, "mock": False,
              "side_effect_free": True},
    )


def _record_voice_result(result: dict[str, Any], payload: Mapping[str, Any]) -> None:
    """Surface resolved voice evidence as optional v1.3 row-result keys.

    ``resolved_operation``, ``confirmation_required`` and ``voice_detail`` are
    evidence only: they never drive the disposition (the payload diff does), and
    every value derives deterministically from the registry + fixture.
    """
    operation = payload.get("voice_operation")
    if operation is not None:
        result["resolved_operation"] = operation
    confirmation = payload.get("confirmation")
    json_block = payload.get("json")
    if not isinstance(confirmation, Mapping) and isinstance(json_block, Mapping):
        nested = json_block.get("confirmation")
        if isinstance(nested, Mapping):
            confirmation = nested
    if isinstance(confirmation, Mapping) and "required" in confirmation:
        result["confirmation_required"] = bool(confirmation.get("required"))
    detail: dict[str, Any] = {}
    if isinstance(operation, Mapping):
        for key in ("entry_id", "backend", "args", "aliases"):
            if operation.get(key) is not None:
                detail[key] = operation[key]
    if isinstance(json_block, Mapping):
        for key in ("kind", "confirmation", "cancel", "expiry", "replay"):
            if key in json_block:
                detail[key] = json_block[key]
    if isinstance(confirmation, Mapping):
        detail.setdefault("confirmation", confirmation)
    result["voice_detail"] = detail or None


class _SubprocessAdapter(Adapter):
    project = "qubit"

    def __init__(self, binary: str | None, env_var: str) -> None:
        self.env_var = env_var
        self.binary = os.environ.get(env_var) or binary
        self.timeout = float(os.environ.get("QUBIT_DIFFERENTIAL_TIMEOUT", "60"))

    def available(self) -> bool:
        """A transport URL makes an adapter usable even without a local binary."""
        if self.binary is not None and Path(self.binary).exists():
            return True
        url = os.environ.get(f"QUBIT_DIFFERENTIAL_{str(self.project).upper()}_URL")
        return bool(url and url.strip())

    def execute(self, request: Mapping[str, Any]) -> CallResult:
        kind = str(request.get("kind") or "cli")
        if kind == "cli":
            return self._execute_cli(request)
        if kind in ("http", "sse", "ws", "rpc"):
            return _execute_transport(
                str(self.project), kind, request, _transport_timeout())
        raise NotImplementedError(
            f"{self.name}: kind {kind!r} requires a dedicated transport adapter"
        )

    def _execute_cli(self, request: Mapping[str, Any]) -> CallResult:
        argv = [str(a) for a in request.get("argv", [])]
        if argv and argv[0] in ("voicebridge", "qubit", "hermes", "openclaw"):
            argv = argv[1:]
        env = dict(os.environ)
        for key, raw in (request.get("env") or {}).items():
            ok, resolved, missing = _resolve_env_ref(raw)
            if not ok:
                raise RuntimeError(f"environment reference {missing!r} is not set")
            env[str(key)] = resolved
        proc = subprocess.run(
            [self.binary, *argv],
            input=request.get("stdin"),
            capture_output=True,
            text=True,
            timeout=self.timeout,
            env=env,
            cwd=request.get("cwd") or str(REPO_ROOT),
            check=False,
        )
        return CallResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            meta={"adapter": self.name, "binary": self.binary, "argv": argv},
        )


class QubitAdapter(_SubprocessAdapter):
    name = "qubit"
    project = "qubit"

    def __init__(self, binary: str | None = None) -> None:
        if binary is None:
            # Prefer the repository's own venv entry point so the Qubit side is
            # deterministic and independent of the caller's PATH (a bare shell
            # must not silently run a globally-installed ``qubit``).
            venv_bin = REPO_ROOT / ".venv" / "bin" / "voicebridge"
            binary = (
                str(venv_bin)
                if venv_bin.exists()
                else (shutil.which("voicebridge") or shutil.which("qubit"))
            )
        super().__init__(binary, "QUBIT_BIN")

    def execute(self, request: Mapping[str, Any]) -> CallResult:
        """In-process voice resolution needs no binary/URL/subprocess.

        The Qubit voice path is the real registry resolver, not a transport, so
        ``voice`` is served here even when the CLI binary and the HTTP base URL
        are both absent (a reference adapter never reaches this override).
        """
        if str(request.get("kind") or "") == VOICE_TRANSPORT:
            return _execute_voice("qubit", request)
        return super().execute(request)


class ReferenceAdapter(_SubprocessAdapter):
    name = "reference"

    def __init__(self, project: str, binary: str | None = None) -> None:
        self.project = project
        self.name = project
        env_var = f"QUBIT_DIFFERENTIAL_{project.upper()}_BIN"
        if binary is None:
            binary = shutil.which(project)
        super().__init__(binary, env_var)


# --------------------------------------------------------------------------- #
# Vendor-parity adapter (B1)
#
# Executes a vendored upstream module for real through
# ``voicebridge.qubit_runtime.ensure_path()`` and compares its observed
# behavior against the pinned reference root. The behavioral descriptor is
# deterministic: the module's imported-ness, its source ``sha256``, the sorted
# public top-level names and their hash, and (when requested) the symbol's kind
# and ``inspect.signature``. Byte identity alone is NOT behavior, so a vendor row
# is never stamped PASS from a byte match: the row is at most SAFE_EQUIVALENT,
# and only when the pinned reference root actually resolves and the two
# behavioral descriptors are equal. An unresolved reference root is fail-closed
# (``TransportUnavailable`` -> OPERATOR_ONLY, never PASS).
# --------------------------------------------------------------------------- #
# The probe runs in a throwaway subprocess so importing the fork never mutates
# this runner's ``sys.path``/``sys.modules``. In "vendored" mode the child calls
# ``ensure_path()`` itself (the required execution path); in "reference" mode it
# imports from the operator-provided pinned reference root.
VENDOR_PROBE_SCRIPT = r"""
import hashlib, importlib, inspect, json, os, re, sys


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")


def stable_repr(value):
    # Deterministic repr for annotations/defaults: containers are canonicalised
    # (sets/dicts sorted), and object addresses are collapsed (ASLR). A raw
    # repr(frozenset(...)) iterates in per-process hash order, which would make
    # the signature hash nondeterministic.
    if value is inspect.Parameter.empty or value is inspect.Signature.empty:
        return "<empty>"
    if isinstance(value, (set, frozenset)):
        try:
            items = sorted(stable_repr(v) for v in value)
        except TypeError:
            items = sorted(stable_repr(v) for v in value)
        return "frozenset({" + ",".join(items) + "})"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(stable_repr(v) for v in value) + "]"
    if isinstance(value, dict):
        try:
            keys = sorted(value, key=lambda k: stable_repr(k))
        except TypeError:
            keys = list(value)
        return "{" + ",".join(
            stable_repr(k) + ":" + stable_repr(value[k]) for k in keys) + "}"
    try:
        text = repr(value)
    except Exception:
        return "type:" + type(value).__name__
    return _ADDR_RE.sub("0xADDR", text)


def signature_hash(target):
    try:
        sig = inspect.signature(target)
    except (TypeError, ValueError):
        return None
    params = []
    for name, param in sig.parameters.items():
        params.append([
            name,
            param.kind.name,
            stable_repr(param.annotation),
            stable_repr(param.default),
        ])
    payload = {"params": params, "return": stable_repr(sig.return_annotation)}
    return "sha256:" + hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def probe(module, symbol):
    try:
        mod = importlib.import_module(module)
    except Exception as exc:
        return {"module": module, "imported": False, "error": type(exc).__name__}
    path = getattr(mod, "__file__", None)
    source_hash = None
    basename = None
    if path and os.path.isfile(path):
        basename = os.path.basename(path)
        with open(path, "rb") as handle:
            source_hash = "sha256:" + hashlib.sha256(handle.read()).hexdigest()
    names = sorted(name for name in dir(mod) if not name.startswith("_"))
    info = {
        "module": module,
        "imported": True,
        "file": basename,
        "source_hash": source_hash,
        "public_names": names,
        "names_hash": "sha256:" + hashlib.sha256(
            canonical(names).encode("utf-8")).hexdigest(),
    }
    if symbol:
        info["symbol"] = symbol
        target = getattr(mod, symbol, None)
        if target is None:
            info["symbol_kind"] = "missing"
            info["signature_hash"] = None
        else:
            if inspect.isclass(target):
                info["symbol_kind"] = "class"
            elif callable(target):
                info["symbol_kind"] = "function"
            else:
                info["symbol_kind"] = "constant"
            info["signature_hash"] = signature_hash(target)
    return info


mode = sys.argv[1]
module = sys.argv[2]
symbol = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
if mode == "vendored":
    from voicebridge.qubit_runtime import ensure_path
    ensure_path()
else:
    sys.path.insert(0, sys.argv[4])
print(canonical(probe(module, symbol)))
"""

# Batch form of the probe (one process per side): imports every requested
# ``(module, symbol)`` spec and writes the behavioral descriptor map to a file
# (not stdout, because an imported module may legitimately replace sys.stdout).
# The output path and the specs path are supplied by the adapter; the specs are
# read as a JSON list of ``[module, symbol]`` pairs. Deterministic and
# stdlib-only; ``PYTHONDONTWRITEBYTECODE`` is set by the parent so probing the
# read-only pinned clone never writes bytecode into it.
VENDOR_BATCH_PROBE_SCRIPT = r"""
import hashlib, importlib, inspect, json, os, re, sys


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")


def stable_repr(value):
    # Deterministic repr (see VENDOR_PROBE_SCRIPT): containers canonicalised,
    # addresses collapsed.
    if value is inspect.Parameter.empty or value is inspect.Signature.empty:
        return "<empty>"
    if isinstance(value, (set, frozenset)):
        return "frozenset({" + ",".join(
            sorted(stable_repr(v) for v in value)) + "})"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(stable_repr(v) for v in value) + "]"
    if isinstance(value, dict):
        try:
            keys = sorted(value, key=lambda k: stable_repr(k))
        except TypeError:
            keys = list(value)
        return "{" + ",".join(
            stable_repr(k) + ":" + stable_repr(value[k]) for k in keys) + "}"
    try:
        text = repr(value)
    except Exception:
        return "type:" + type(value).__name__
    return _ADDR_RE.sub("0xADDR", text)


def signature_hash(target):
    try:
        sig = inspect.signature(target)
    except (TypeError, ValueError):
        return None
    params = []
    for name, param in sig.parameters.items():
        params.append([
            name,
            param.kind.name,
            stable_repr(param.annotation),
            stable_repr(param.default),
        ])
    payload = {"params": params, "return": stable_repr(sig.return_annotation)}
    return "sha256:" + hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


_modules = {}


def module_info(module):
    if module in _modules:
        return _modules[module]
    try:
        mod = importlib.import_module(module)
    except BaseException as exc:  # noqa: BLE001 - a probe survives SystemExit
        _modules[module] = {"imported": False, "error": type(exc).__name__}
        return _modules[module]
    path = getattr(mod, "__file__", None)
    source_hash = None
    basename = None
    if path and os.path.isfile(path):
        basename = os.path.basename(path)
        with open(path, "rb") as handle:
            source_hash = "sha256:" + hashlib.sha256(handle.read()).hexdigest()
    names = sorted(name for name in dir(mod) if not name.startswith("_"))
    _modules[module] = {
        "imported": True,
        "file": basename,
        "source_hash": source_hash,
        "public_names": names,
        "names_hash": "sha256:" + hashlib.sha256(
            canonical(names).encode("utf-8")).hexdigest(),
        "mod": mod,
    }
    return _modules[module]


_SCHEMA_KEY_CACHE = {}


def _walk_schema_keys(value, path, out, seen, depth):
    # Bounded, deterministic recursive key collection over module-level dicts.
    if depth > 5 or id(value) in seen:
        return
    seen.add(id(value))
    for key in sorted(value, key=lambda k: stable_repr(k)):
        if isinstance(key, str) and key not in out:
            out[key] = path + "." + key
        child = value[key]
        if isinstance(child, dict):
            _walk_schema_keys(child, path + "." + str(key), out, seen, depth + 1)


def schema_key_paths(module):
    # {key: dotted path} for every key reachable from a module-level dict.
    if module in _SCHEMA_KEY_CACHE:
        return _SCHEMA_KEY_CACHE[module]
    out = {}
    try:
        mod = importlib.import_module(module)
    except BaseException:  # noqa: BLE001
        _SCHEMA_KEY_CACHE[module] = out
        return out
    for name in sorted(dir(mod)):
        if name.startswith("__"):
            continue
        try:
            value = getattr(mod, name)
        except BaseException:  # noqa: BLE001
            continue
        if isinstance(value, dict):
            _walk_schema_keys(value, name, out, set(), 0)
    _SCHEMA_KEY_CACHE[module] = out
    return out


def descriptor(module, symbol):
    info = module_info(module)
    out = {"module": module, "symbol": symbol}
    if not info.get("imported"):
        out["imported"] = False
        out["error"] = info.get("error")
        return out
    out["imported"] = True
    out["file"] = info["file"]
    out["source_hash"] = info["source_hash"]
    out["public_names"] = info["public_names"]
    out["names_hash"] = info["names_hash"]
    if symbol:
        target = getattr(info["mod"], symbol, None)
        if target is None:
            # A config/CLI schema key is not a module attribute. It still has a
            # real behavioral identity: the module's declared key table. Record
            # the key's dotted path and a stable hash of its value so both sides
            # must agree, rather than treating "missing attribute" as absent.
            key_path = schema_key_paths(module).get(symbol)
            if key_path is None:
                out["symbol_kind"] = "missing"
                out["signature_hash"] = None
            else:
                out["symbol_kind"] = "schema_key"
                out["signature_hash"] = None
                out["schema_key_path"] = key_path
                try:
                    value = info["mod"]
                    for part in key_path.split("."):
                        value = value[part]
                    out["schema_value_hash"] = "sha256:" + hashlib.sha256(
                        stable_repr(value).encode("utf-8")).hexdigest()
                except BaseException:  # noqa: BLE001
                    out["schema_value_hash"] = None
        else:
            if inspect.isclass(target):
                out["symbol_kind"] = "class"
            elif callable(target):
                out["symbol_kind"] = "function"
            else:
                out["symbol_kind"] = "constant"
            out["signature_hash"] = signature_hash(target)
    return out


mode = sys.argv[1]
specs_path = sys.argv[2]
out_path = sys.argv[3]
if mode == "vendored":
    from voicebridge.qubit_runtime import ensure_path
    ensure_path()
else:
    sys.path.insert(0, sys.argv[4])
with open(specs_path, "r", encoding="utf-8") as handle:
    specs = json.load(handle)
out = {}
for pair in specs:
    module = str(pair[0])
    symbol = pair[1] if len(pair) > 1 and pair[1] else None
    out[module + "\x1f" + (symbol or "")] = descriptor(module, symbol)
fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
os.write(fd, canonical(out).encode("utf-8"))
os.close(fd)
"""


class VendorParityAdapter(Adapter):
    """Execute vendored upstream modules and compare behavior to the pinned root.

    ``available()`` is True only when the vendored fork root resolves on disk, so
    a missing fork is fail-closed. ``execute`` refuses (``TransportUnavailable``)
    unless the operator-provided pinned reference root resolves, so a vendor row
    is never a success claim without a real reference comparison.
    """

    name = VENDOR_TRANSPORT
    project = "hermes"

    def __init__(self, reference_root: str | None = None) -> None:
        self.reference_root = reference_root
        self.timeout = float(os.environ.get("QUBIT_DIFFERENTIAL_TIMEOUT", "60"))

    def fork_root(self) -> Path | None:
        """Resolve the vendored fork root without mutating ``sys.path``."""
        candidate = REPO_ROOT / "qubit_runtime"
        if candidate.is_dir():
            return candidate
        try:
            root = str(REPO_ROOT)
            if root not in sys.path:
                sys.path.insert(0, root)
            from voicebridge import qubit_runtime  # type: ignore
            resolved = Path(qubit_runtime.runtime_root())
        except Exception:  # noqa: BLE001 - availability probe, fail closed
            return None
        return resolved if resolved.is_dir() else None

    def available(self) -> bool:
        return self.fork_root() is not None

    def _reference_root(self, request: Mapping[str, Any]) -> Path | None:
        declared = request.get("ref_root") or self.reference_root
        if not declared:
            declared = os.environ.get(DEFAULT_VENDOR_REF_ROOT_ENV)
        if not declared:
            return None
        resolved = Path(str(declared)).expanduser()
        return resolved if resolved.is_dir() else None

    def _probe(
        self, mode: str, module: str, symbol: str | None, reference_root: Path | None
    ) -> dict[str, Any]:
        argv = [sys.executable, "-c", VENDOR_PROBE_SCRIPT, mode, module, symbol or ""]
        if mode == "reference":
            if reference_root is None:
                raise TransportUnavailable(
                    "vendor reference root is not configured; set "
                    f"{DEFAULT_VENDOR_REF_ROOT_ENV} to the pinned upstream checkout"
                )
            argv.append(str(reference_root))
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + existing if existing else "")
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True,
                timeout=self.timeout, cwd=str(REPO_ROOT), env=env, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise TransportUnavailable(
                f"vendor probe could not execute: {redact_text(str(exc))}"
            ) from exc
        if proc.returncode != 0:
            raise TransportUnavailable(
                "vendor probe failed (module import error): "
                + redact_text((proc.stderr or proc.stdout or "").strip())[:300]
            )
        parsed = _parse_json_maybe(proc.stdout or "")
        if not isinstance(parsed, Mapping):
            raise TransportUnavailable("vendor probe produced no behavioral descriptor")
        return dict(parsed)

    def probe_vendored(self, module: str, symbol: str | None = None) -> dict[str, Any]:
        """Execute a vendored module's behavioral probe (no reference required)."""
        root = self.fork_root()
        if root is None:
            raise TransportUnavailable("vendored upstream fork root is absent")
        return self._probe("vendored", module, symbol, None)

    def resolve_reference_root(
        self, request: Mapping[str, Any] | None = None
    ) -> Path | None:
        """Public accessor for the operator-configured pinned reference root."""
        return self._reference_root(request or {})

    def _probe_many(
        self,
        mode: str,
        specs: Sequence[tuple[str, str | None]],
        reference_root: Path | None,
    ) -> dict[str, dict[str, Any]]:
        """Batch behavioral probe (one subprocess for the whole spec set).

        Writes specs and the descriptor map through temp files so an imported
        module that replaces ``sys.stdout`` cannot corrupt the result. Returns
        ``{module<US>symbol: descriptor}``; a failed subprocess is
        ``TransportUnavailable`` (fail-closed, never a partial success claim).
        """
        ordered = sorted(
            {(str(m), (str(s) if s else None)) for m, s in specs},
            key=lambda pair: (pair[0], pair[1] or ""),
        )
        specs_fd, specs_path = tempfile.mkstemp(prefix="vendor-specs-", suffix=".json")
        out_fd, out_path = tempfile.mkstemp(prefix="vendor-probe-", suffix=".json")
        os.close(specs_fd)
        os.close(out_fd)
        try:
            Path(specs_path).write_text(
                canonical_json([[m, s] for m, s in ordered]), encoding="utf-8")
            argv = [
                sys.executable, "-c", VENDOR_BATCH_PROBE_SCRIPT,
                mode, specs_path, out_path,
            ]
            if mode == "reference":
                if reference_root is None:
                    raise TransportUnavailable(
                        "vendor reference root is not configured; set "
                        f"{DEFAULT_VENDOR_REF_ROOT_ENV} to the pinned upstream checkout"
                    )
                argv.append(str(reference_root))
            env = dict(os.environ)
            # Never write bytecode into a read-only pinned clone.
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + existing if existing else "")
            try:
                proc = subprocess.run(
                    argv, capture_output=True, text=True,
                    timeout=self.timeout, cwd=str(REPO_ROOT), env=env, check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise TransportUnavailable(
                    f"vendor batch probe could not execute: {redact_text(str(exc))}"
                ) from exc
            if proc.returncode != 0:
                raise TransportUnavailable(
                    "vendor batch probe failed: "
                    + redact_text((proc.stderr or proc.stdout or "").strip())[:300]
                )
            try:
                parsed = json.loads(Path(out_path).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise TransportUnavailable(
                    f"vendor batch probe produced no descriptor map: {exc}"
                ) from exc
            if not isinstance(parsed, Mapping):
                raise TransportUnavailable(
                    "vendor batch probe produced no behavioral descriptor map")
            return {str(k): dict(v) for k, v in parsed.items()
                    if isinstance(v, Mapping)}
        finally:
            for path in (specs_path, out_path):
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def compare_specs(
        self, specs: Sequence[tuple[str, str | None]], reference_root: Path
    ) -> dict[str, dict[str, Any]]:
        """Compare batched behavioral descriptors against the pinned reference.

        Returns ``{module<US>symbol: {"vendored","reference","behavior_equal",
        "qubit_hash","reference_hash"}}`` for every spec that resolved on BOTH
        sides. A spec that failed to import on either side is still returned (so
        the caller can distinguish "import failed" from "absent"), with
        ``behavior_equal`` False. Never a success claim by itself.
        """
        vendored = self._probe_many("vendored", specs, None)
        reference = self._probe_many("reference", specs, reference_root)
        out: dict[str, dict[str, Any]] = {}
        for key, que in vendored.items():
            ref = reference.get(key)
            if not isinstance(ref, Mapping):
                continue
            out[key] = {
                "vendored": que,
                "reference": dict(ref),
                "behavior_equal": canonical_json(que) == canonical_json(ref),
                "qubit_hash": result_hash(que),
                "reference_hash": result_hash(ref),
            }
        return out

    def execute(self, request: Mapping[str, Any]) -> CallResult:
        kind = str(request.get("kind") or VENDOR_TRANSPORT)
        if kind != VENDOR_TRANSPORT:
            raise NotImplementedError(
                f"{self.name}: kind {kind!r} is not a vendor probe"
            )
        module = str(request.get("module") or "").strip()
        if not module:
            raise TransportUnavailable("vendor spec requires a 'module'")
        symbol = request.get("symbol")
        symbol = str(symbol) if symbol else None
        if self.fork_root() is None:
            raise TransportUnavailable("vendored upstream fork root is absent")
        reference_root = self._reference_root(request)
        if reference_root is None:
            raise TransportUnavailable(
                "no pinned vendor reference root configured; set "
                f"{DEFAULT_VENDOR_REF_ROOT_ENV} to compare behavior (byte "
                "identity alone is not a behavioral proof)"
            )
        qubit = self._probe("vendored", module, symbol, None)
        reference = self._probe("reference", module, symbol, reference_root)
        equal = canonical_json(qubit) == canonical_json(reference)
        payload = {
            "module": module,
            "symbol": symbol,
            "vendored": qubit,
            "reference": reference,
            "behavior_equal": equal,
        }
        qubit_hash = result_hash(qubit)
        reference_hash = result_hash(reference)
        return CallResult(
            exit_code=0 if equal else 1,
            json=payload,
            stdout=canonical_json(payload),
            transport=VENDOR_TRANSPORT,
            meta={
                "adapter": self.name,
                "mock": False,
                "reference_executed": True,
                "reference_hash": reference_hash,
                "reference_project": "hermes",
                "qubit_hash": qubit_hash,
                "behavior_equal": equal,
                "reference_root": str(reference_root),
            },
        )


def _resolve_reference_env_value(ref_value: Any) -> tuple[str | None, str, str | None]:
    """Resolve a binding reference to ``(env_name, value, error)``.

    ``ref_value`` may be a bare environment variable name
    (``QUBIT_DIFFERENTIAL_HERMES_BIN``) or the ``$NAME`` / ``env:NAME`` form.
    The resolved VALUE is returned to the caller only; it is never logged, and
    the error string names the variable, never its value.
    """
    if not isinstance(ref_value, str) or not ref_value.strip():
        return None, "", "empty reference binding"
    ref = ref_value.strip()
    match = _ENV_REF_RE.match(ref) or _REF_RE.match(ref)
    name = match.group(1) if match else ref
    if not _IDENT_RE.match(name):
        return None, "", f"invalid reference env name {name!r}"
    value = os.environ.get(name)
    if value is None or not value.strip():
        return name, "", f"declared reference environment variable {name!r} is not set"
    return name, value.strip(), None


def resolve_reference_binding(
    project: str, spec: Mapping[str, Any], adapter: Adapter | None
) -> tuple[Adapter | None, dict[str, Any]]:
    """Bind a fixture's declared reference to a pinned runtime, fail-closed.

    Documented precedence for ``references.<project>``:

    1. ``bin_ref`` / ``env_ref`` — the value is the NAME of an environment
       variable holding the reference binary path (for example
       ``QUBIT_DIFFERENTIAL_HERMES_BIN``). It is read here, at execution time;
       the resolved path becomes the process entrypoint only and is never
       copied into argv, the subprocess environment, logs or the report. An
       unset/blank variable is an unresolved binding, so the reference is
       unavailable and can never yield PASS.
    2. ``bin`` — a literal binary path (used by hermetic tests).
    3. otherwise the adapter already resolved from the normal
       ``QUBIT_DIFFERENTIAL_<PROJECT>_BIN`` environment / ``$PATH`` rules.

    Returns ``(adapter, binding)``. ``binding`` records the non-secret reference
    SOURCE (env var NAME) and ``binding_resolved``; ``binding["binary"]`` is the
    resolved path used as provenance.
    """
    declared = spec.get("bin_ref")
    if declared is None:
        declared = spec.get("env_ref")
    if declared is not None:
        name, value, error = _resolve_reference_env_value(declared)
        binding: dict[str, Any] = {
            "binding": "env_ref",
            "env_ref": name if name is not None else str(declared),
            "binding_resolved": error is None,
        }
        if error is not None:
            binding["binding_error"] = error
            return None, binding
        bound = ReferenceAdapter(project, binary=value)
        # The declared binding is authoritative even when the default
        # ``QUBIT_DIFFERENTIAL_<PROJECT>_BIN`` is also set.
        bound.binary = value
        binding["binary"] = value
        return bound, binding
    literal = spec.get("bin")
    if isinstance(literal, str) and literal.strip():
        value = literal.strip()
        bound = ReferenceAdapter(project, binary=value)
        bound.binary = value
        return bound, {
            "binding": "literal_bin",
            "binding_resolved": True,
            "binary": value,
        }
    binding = {"binding": "adapter", "binding_resolved": adapter is not None}
    if adapter is None:
        binding["binding_error"] = (
            f"no {project} reference adapter resolved "
            f"(set QUBIT_DIFFERENTIAL_{project.upper()}_BIN or _URL)"
        )
    else:
        binding["binary"] = getattr(adapter, "binary", None)
    return adapter, binding


_VERSION_CACHE: dict[str, str | None] = {}


def _probe_version(adapter: Adapter) -> str | None:
    """Record the resolved binary's ``--version`` first line (best effort).

    The pinned clones are source trees without a built entrypoint, so the live
    binary is the runnable reference; its version is captured as provenance and
    is never part of the result hashes.
    """
    binary = getattr(adapter, "binary", None)
    if not binary:
        return None
    if binary in _VERSION_CACHE:
        return _VERSION_CACHE[binary]
    version: str | None = None
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            cwd=str(REPO_ROOT),
            check=False,
        )
        text = (proc.stdout or proc.stderr or "").strip()
        version = text.splitlines()[0] if text else ""
    except (OSError, subprocess.SubprocessError):
        version = None
    _VERSION_CACHE[binary] = version
    return version


def _resolve_reference_commit(
    project: str,
    adapter: Adapter,
    version: str | None,
    spec: Mapping[str, Any] | None = None,
) -> str | None:
    """Resolve the executed reference's commit (declared ref, env, --version).

    Precedence: an explicit ``references.<project>.commit_ref`` naming an
    environment variable (fail-closed: an unset/blank variable yields ``None``,
    i.e. unpinned), then the existing ``QUBIT_DIFFERENTIAL_<PROJECT>_COMMIT``
    override, then a 7-40 hex token in the probed ``--version`` output.
    """
    if isinstance(spec, Mapping) and spec.get("commit_ref") is not None:
        _name, value, error = _resolve_reference_env_value(spec.get("commit_ref"))
        if error is not None:
            return None
        return value.lower()
    override = os.environ.get(f"QUBIT_DIFFERENTIAL_{project.upper()}_COMMIT")
    if override and override.strip():
        return override.strip().lower()
    if isinstance(version, str):
        candidates = _version_commit_tokens(version)
        if candidates:
            # Prefer the token that corroborates the contractual pin; a version
            # line can carry several revisions (e.g. the CLI prints the upstream
            # AND local commit).
            for candidate in candidates:
                if _commit_matches_pin(project, candidate):
                    return candidate
            return candidates[0]
    return None


def _version_commit_tokens(version: Any) -> list[str]:
    """Every 7-40 hex revision token in a probed ``--version`` string."""
    if not isinstance(version, str):
        return []
    return [token.lower() for token in re.findall(r"\b[0-9a-f]{7,40}\b", version,
                                                  re.IGNORECASE)]


def _commit_matches_pin(project: str, commit: str | None) -> bool:
    """True when a resolved commit equals (or is a prefix of) the contractual pin."""
    pin = CONTRACTUAL_PINS.get(project)
    if not pin or not commit:
        return False
    commit = commit.lower()
    pin = pin.lower()
    return pin.startswith(commit) or commit.startswith(pin)


def _reference_is_pinned(
    project: str, commit: str | None, version: str | None = None
) -> bool:
    """Verify the resolved reference identity against the contractual pin.

    A probed ``--version`` is authoritative: when it carries revision tokens,
    at least one MUST corroborate the pin and any declared commit must agree.
    This prevents an env override from pinning a reference whose actual binary
    reports a different revision. When no version can be probed, the declared
    commit is the only available identity and must equal the pin.
    """
    pin = CONTRACTUAL_PINS.get(project)
    if not pin:
        return False
    tokens = _version_commit_tokens(version)
    if tokens:
        if not any(_commit_matches_pin(project, token) for token in tokens):
            return False
        if commit and not _commit_matches_pin(project, commit):
            return False
        return True
    return _commit_matches_pin(project, commit)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def default_adapters(offline: bool | None = None) -> dict[str, Adapter]:
    """Build default adapters; reference adapters run only when not offline."""
    if offline is None:
        offline = _env_truthy("QUBIT_DIFFERENTIAL_OFFLINE")
    adapters: dict[str, Adapter] = {
        "qubit": QubitAdapter(),
        # Vendor parity is always available to probe: a vendor fixture refuses
        # (TransportUnavailable -> OPERATOR_ONLY) when the pinned reference root
        # is not configured, so it can never fake a success claim.
        VENDOR_TRANSPORT: VendorParityAdapter(),
    }
    if not offline:
        for project in sorted(REFERENCE_PROJECTS):
            adapters[project] = ReferenceAdapter(project)
    return adapters


# --------------------------------------------------------------------------- #
# State-changing guard
# --------------------------------------------------------------------------- #
def _scan_state_changing_string(value: Any, where: str, reasons: set[str]) -> None:
    """Flag a single executable string that names a denylisted path/action."""
    if not isinstance(value, str):
        return
    low = value.lower()
    for marker in STATE_CHANGING_PATH_MARKERS:
        if marker in low:
            reasons.add(f"{where}:{marker}")
    for token, pattern in STATE_CHANGING_TOKEN_PATTERNS.items():
        if pattern.search(low):
            reasons.add(f"{where}:{token}")


def _scan_state_changing_node(node: Any, where: str, reasons: set[str]) -> None:
    """Recursively scan an executable body/argv tree for mutating names."""
    if isinstance(node, str):
        _scan_state_changing_string(node, where, reasons)
    elif isinstance(node, Mapping):
        for key in sorted(node, key=str):
            _scan_state_changing_node(node[key], f"{where}.{key}", reasons)
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            _scan_state_changing_node(value, f"{where}[{index}]", reasons)


def _classify_method(method: Any, where: str, reasons: set[str]) -> None:
    """Classify any non-GET/HEAD HTTP method as state-changing (fail closed)."""
    if not isinstance(method, str) or not method.strip():
        return
    normalized = method.strip().upper()
    if normalized not in SAFE_HTTP_METHODS:
        reasons.add(f"{where}:{normalized}")


def _scan_reference_spec(project: str, spec: Any, reasons: set[str]) -> None:
    """Scan a declared reference (CLI argv and/or nested HTTP request)."""
    if not isinstance(spec, Mapping):
        return
    where = f"references.{project}"
    _scan_state_changing_node(spec.get("argv"), f"{where}.argv", reasons)
    nested = spec.get("request") if isinstance(spec.get("request"), Mapping) else spec
    _scan_state_changing_string(nested.get("path"), f"{where}.path", reasons)
    _scan_state_changing_node(nested.get("body"), f"{where}.body", reasons)
    _scan_state_changing_node(nested.get("argv"), f"{where}.argv", reasons)
    method = nested.get("method")
    if not isinstance(method, str) or not method.strip():
        method = spec.get("method")
    _classify_method(method, f"{where}.method", reasons)


def _is_safe_read_post(node: Mapping[str, Any], parent: Mapping[str, Any]) -> bool:
    """True when a non-GET method is a documented safe read.

    Only ``POST`` can be a safe read, and only when it is explicitly a JSON-RPC
    query (``kind``/``transport`` == ``rpc``) or when the node declares a
    ``read_method`` in :data:`SAFE_READ_METHODS`. DELETE/PUT/PATCH and every
    other verb are never a safe read.
    """
    method = node.get("method") or parent.get("method")
    if not isinstance(method, str) or method.strip().upper() != "POST":
        return False
    kind = (
        node.get("kind")
        or parent.get("kind")
        or parent.get("transport")
        or node.get("transport")
    )
    # ``rpc`` queries and the in-process ``voice`` resolver are documented
    # side-effect-free reads: neither opens the declared HTTP request, so a
    # ``read_only: true`` voice fixture may execute its resolution without the
    # mutating-method opt-in. Every other non-GET verb stays blocked.
    if isinstance(kind, str) and kind.strip().lower() in ("rpc", "voice"):
        return True
    read_method = node.get("read_method") or parent.get("read_method")
    return (
        isinstance(read_method, str)
        and read_method.strip().upper() in SAFE_READ_METHODS
    )


def _read_only_method_permitted(obj: Mapping[str, Any]) -> bool:
    """True only when EVERY non-GET method is a documented safe read.

    ``read_only: true`` must never blanket-suppress the method guard. It only
    releases the method reason for an explicit JSON-RPC read or a declared
    ``read_method``; a single DELETE/PUT/PATCH (or any unparsed verb) keeps the
    row state-changing.
    """
    candidates: list[tuple[Any, Mapping[str, Any]]] = []
    request = obj.get("request")
    if isinstance(request, Mapping):
        candidates.append((request.get("method"), request))
    else:
        candidates.append((obj.get("method"), obj))
    sequence = obj.get("sequence")
    if isinstance(sequence, list):
        for step in sequence:
            if isinstance(step, Mapping):
                candidates.append((step.get("method"), step))
    saw_non_get = False
    for method, node in candidates:
        if not isinstance(method, str) or not method.strip():
            continue
        if method.strip().upper() in SAFE_HTTP_METHODS:
            continue
        saw_non_get = True
        if not _is_safe_read_post(node, obj):
            return False
    return saw_non_get


def state_changing_reasons(
    obj: Mapping[str, Any], read_only: bool | None = None
) -> list[str]:
    """Return the sorted reasons ``obj`` mutates persistent/canonical state.

    Only EXECUTABLE fields are inspected (request/command/sequence/reference
    specs and the canonical dashboard op) plus an explicit ``state_changing:
    true`` marker, so prose in ``notes``/``operator_prerequisite`` never
    classifies a row. A non-GET/HEAD HTTP method is state-changing fail-closed.
    ``read_only=True`` releases ONLY the method reason, and only for documented
    safe reads (JSON-RPC query or a declared ``read_method``); explicit
    path/action markers still apply.
    """
    if read_only is None:
        read_only = obj.get("read_only") is True
    reasons: set[str] = set()
    method_reasons: set[str] = set()
    if obj.get("state_changing") is True:
        reasons.add("declared state_changing=true")
    request = obj.get("request")
    if isinstance(request, Mapping):
        _scan_state_changing_string(request.get("path"), "request.path", reasons)
        _scan_state_changing_node(request.get("body"), "request.body", reasons)
        _scan_state_changing_node(request.get("argv"), "request.argv", reasons)
        _classify_method(request.get("method"), "request.method", method_reasons)
    command = obj.get("command")
    if isinstance(command, Mapping):
        _scan_state_changing_node(command.get("argv"), "command.argv", reasons)
    sequence = obj.get("sequence")
    if isinstance(sequence, list):
        for index, step in enumerate(sequence):
            if not isinstance(step, Mapping):
                continue
            _scan_state_changing_string(
                step.get("path"), f"sequence[{index}].path", reasons)
            _scan_state_changing_node(
                step.get("body"), f"sequence[{index}].body", reasons)
            _scan_state_changing_node(
                step.get("argv"), f"sequence[{index}].argv", reasons)
            _classify_method(
                step.get("method"), f"sequence[{index}].method", method_reasons)
    references = obj.get("references")
    if isinstance(references, Mapping):
        for project in sorted(references, key=str):
            _scan_reference_spec(str(project), references[project], reasons)
    evidence = obj.get("evidence")
    if isinstance(evidence, Mapping):
        _scan_state_changing_string(
            evidence.get("dashboard_control"), "evidence.dashboard_control", reasons)
        _scan_state_changing_string(
            evidence.get("dashboard_route"), "evidence.dashboard_route", reasons)
    if not (read_only and _read_only_method_permitted(obj)):
        reasons |= method_reasons
    return sorted(reasons)


def classify_state_changing(row: Mapping[str, Any], fixture: Mapping[str, Any]) -> list[str]:
    """Union the state-changing reasons of the inventory row and its fixture."""
    read_only = (
        row.get("read_only") is True or fixture.get("read_only") is True
    )
    reasons: set[str] = set(state_changing_reasons(row, read_only=read_only))
    reasons.update(state_changing_reasons(fixture, read_only=read_only))
    return sorted(reasons)


def _state_changing_prerequisite(reasons: Sequence[str]) -> str:
    detail = ", ".join(reasons[:6]) if reasons else "state-changing operation"
    return (
        f"state-changing fixture not executed: explicit operator authorization "
        f"required (pass {STATE_CHANGING_FLAG}); detected: {detail}"
    )


# --------------------------------------------------------------------------- #
# Byte-parity gate
# --------------------------------------------------------------------------- #
def _byte_manifest_path() -> Path:
    override = os.environ.get("QUBIT_DIFFERENTIAL_BYTE_MANIFEST")
    if override and override.strip():
        return Path(override.strip())
    in_repo = Path(DEFAULT_HERMES_BYTE_MANIFEST)
    if in_repo.is_file():
        return in_repo
    legacy = Path(LEGACY_HERMES_BYTE_MANIFEST)
    if legacy.is_file():
        return legacy
    return in_repo


def evaluate_byte_manifest(doc: Any) -> list[str]:
    """Return the actionable manifest paths that are not IDENTICAL.

    ``NOT_APPLICABLE`` entries are generated/runtime/gitignored artifacts and
    are exempt. A malformed document returns a single sentinel problem so the
    gate fails closed instead of vacuously passing.
    """
    if not isinstance(doc, Mapping):
        return ["<manifest-not-an-object>"]
    entries = doc.get("manifest")
    if not isinstance(entries, list):
        return ["<manifest-entries-missing>"]
    offenders: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            offenders.append("<manifest-entry-not-an-object>")
            continue
        classification = str(entry.get("classification") or "").upper()
        if classification in BYTE_MANIFEST_EXEMPT_CLASSIFICATIONS:
            continue
        if classification != "IDENTICAL":
            offenders.append(str(entry.get("path") or "<unknown-path>"))
    return offenders


_BYTE_GATE_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}


def _byte_parity_gate() -> dict[str, Any]:
    """Consume the pinned upstream byte manifest; fail on any non-identical source.

    Fail-closed: a missing/unreadable/malformed manifest, a non-contractual
    artifact or pin, or any actionable entry that is not IDENTICAL all fail the
    gate. Determinism is additionally checked against the sibling
    ``manifest.sha256`` when that file is present.
    """
    path = _byte_manifest_path()
    try:
        stat = path.stat()
        cache_key: tuple[Any, ...] = (str(path), stat.st_size, stat.st_mtime_ns)
    except OSError:
        cache_key = (str(path), None, None)
    cached = _BYTE_GATE_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)
    gate = _compute_byte_parity_gate(path)
    _BYTE_GATE_CACHE.clear()
    _BYTE_GATE_CACHE[cache_key] = gate
    return dict(gate)


def _compute_byte_parity_gate(path: Path) -> dict[str, Any]:
    detail = (
        "hermes byte-parity manifest: every actionable source entry must be "
        "IDENTICAL (NOT_APPLICABLE generated artifacts exempt)"
    )
    if not path.is_file():
        return _gate("byte_parity", ["<manifest-missing>"],
                     f"{detail}; manifest not found: {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _gate("byte_parity", ["<manifest-unreadable>"],
                     f"{detail}; unreadable: {redact_text(str(exc))}")
    problems: list[str] = []
    if not isinstance(doc, Mapping):
        problems.append("<manifest-not-an-object>")
    else:
        if doc.get("artifact") != BYTE_MANIFEST_ARTIFACT:
            problems.append(f"artifact={doc.get('artifact')!r}")
        contract = doc.get("contract") if isinstance(doc.get("contract"), Mapping) else {}
        pinned = contract.get("pinned_commit")
        if pinned != CONTRACTUAL_PINS["hermes"]:
            problems.append(f"pinned_commit={pinned!r}")
        offenders = evaluate_byte_manifest(doc)
        problems.extend(offenders[:BYTE_MANIFEST_MAX_OFFENDERS])
        if len(offenders) > BYTE_MANIFEST_MAX_OFFENDERS:
            problems.append(f"<+{len(offenders) - BYTE_MANIFEST_MAX_OFFENDERS} more>")
        hash_file = path.parent / "manifest.sha256"
        if hash_file.is_file():
            try:
                declared = ""
                for line in hash_file.read_text(encoding="utf-8").splitlines():
                    digest, _, name = line.partition("  ")
                    if name.strip() == path.name:
                        declared = digest.strip()
                        break
                actual = file_hash(path).split(":", 1)[1]
                if declared and declared != actual:
                    problems.append("<manifest-sha256-mismatch>")
            except OSError:
                pass
    return _gate("byte_parity", problems, detail)


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #
_ROW_DEFAULTS: dict[str, Any] = {
    "capability_id": None,
    "family": None,
    "upstream_project": "qubit",
    "upstream_commit": None,
    "upstream_path": None,
    "upstream_symbol": None,
    "upstream_hash": None,
    "qubit_path": None,
    "qubit_symbol": None,
    "disposition": "OPEN",
    "command": None,
    "fixture": None,
    "fixture_hash": None,
    "upstream_result_hash": None,
    "qubit_result_hash": None,
    "test_ids": [],
    "trace_refs": [],
    "operator_prerequisite": None,
    "rollback_ref": None,
    "normalizations": [],
}


def _coerce_rows(doc: Any) -> list[dict[str, Any]]:
    if isinstance(doc, list):
        return [r for r in doc if isinstance(r, dict)]
    if isinstance(doc, dict):
        for key in ("rows", "inventory", "capabilities", "entries", "items"):
            value = doc.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
        if doc.get("capability_id"):
            return [doc]
        values = [v for v in doc.values() if isinstance(v, dict) and v.get("capability_id")]
        if values:
            return values
    return []


def merge_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(_ROW_DEFAULTS)
    for key, value in raw.items():
        row[key] = value
    if not row.get("capability_id"):
        family = row.get("family") or "unknown"
        area = row.get("upstream_symbol") or row.get("upstream_path") or "unclassified"
        row["capability_id"] = f"{family}.{str(area).split(':')[0]}.unclassified"
        row["unclassifiable"] = True
    if row.get("disposition") not in DISPOSITIONS:
        row["declared_disposition_raw"] = row.get("disposition")
        row["disposition"] = "OPEN"
    if row.get("family") not in FAMILIES:
        row["unclassifiable"] = True
    for list_key in ("normalizations", "test_ids", "trace_refs"):
        if not isinstance(row.get(list_key), list):
            row[list_key] = []
    return row


def _is_non_canonical_inventory_path(relative: Path) -> bool:
    """True for a scratch/report/backup generation that must not be ingested."""
    for part in relative.parts:
        low = part.lower()
        if part.startswith(INVENTORY_SCRATCH_PREFIX):
            return True
        if low.startswith(INVENTORY_NON_CANONICAL_DIR_PREFIXES):
            return True
        if low in INVENTORY_NON_CANONICAL_DIR_NAMES:
            return True
        if low.endswith(INVENTORY_NON_CANONICAL_DIR_SUFFIXES):
            return True
        if low.endswith(INVENTORY_NON_CANONICAL_SUFFIXES):
            return True
    upper = relative.name.upper()
    return any(marker in upper for marker in INVENTORY_NON_CANONICAL_FILENAME_MARKERS)


def _inventory_scan_files(root: Path) -> list[Path]:
    """Ordered JSON files for a directory scan (hermetic allowlist).

    Only the explicit canonical SOURCE inputs (:data:`CANONICAL_INVENTORY_INPUTS`)
    are ingested, so a stray JSON -- a report, an evidence file, a runtime cache
    or an analyst overlay -- can never shift the merged inventory counts, no
    matter where it sits under the scan root. The four canonical merged
    artifacts win over the nested ``qubit*/`` / ``dispositions/`` inputs for a
    shared ``capability_id``. Deterministic: the allowlist order is ranked by
    (canonical, path), so two scans over the same tree are byte-identical.
    """
    picked: list[Path] = []
    for relative in CANONICAL_INVENTORY_INPUTS:
        candidate = root / relative
        if not candidate.is_file():
            continue
        if _is_non_canonical_inventory_path(Path(relative)):
            continue
        picked.append(candidate)

    def _rank(file_path: Path) -> tuple[int, str]:
        canonical = (
            file_path.parent == root
            and file_path.name in CANONICAL_INVENTORY_FILENAMES
        )
        return (0 if canonical else 1, str(file_path))

    return sorted(picked, key=_rank)


def _inventory_exclusion_ids(files: Sequence[Path]) -> set[str]:
    """Capability ids a generation explicitly excludes as test support only."""
    excluded: set[str] = set()
    for file_path in files:
        try:
            doc = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, Mapping):
            continue
        for entry in doc.get("exclusions") or []:
            if not isinstance(entry, Mapping):
                continue
            capability_id = entry.get("capability_id")
            if not capability_id:
                continue
            reason = str(entry.get("reason") or entry.get("retriage_reason") or "")
            if (
                str(entry.get("retriage_status") or "").upper()
                == SCANNER_EXCLUSION_MARKER
                or reason.strip().lower() == SCANNER_EXCLUSION_REASON
            ):
                excluded.add(str(capability_id))
    return excluded


def _is_scanner_excluded(row: Mapping[str, Any]) -> bool:
    """True when a row is a retriage scanner exclusion (test support only).

    The merge drops these non-capabilities; the runner must not demand a
    disposition the merge deliberately never assigns.
    """
    for key in ("retriage_status", "scanner_exclusion"):
        if str(row.get(key) or "").strip().upper() == SCANNER_EXCLUSION_MARKER:
            return True
    if row.get("test_support_only") is True:
        return True
    for key in ("retriage_reason", "reason"):
        if str(row.get(key) or "").strip().lower() == SCANNER_EXCLUSION_REASON:
            return True
    # B9 scanner hygiene: an upstream ``*.test-support.ts`` / ``*.test-harness.ts``
    # module is test scaffolding, never a product capability. The upstream delta
    # scan flags it (``upstream_is_test_support``) and the path/module name
    # carries the marker; either is sufficient to drop it from the merge and
    # exempt it from the undisposed-addition gate.
    if row.get("upstream_is_test_support") is True:
        return True
    for key in ("upstream_path", "upstream_symbol", "qubit_path", "capability_id"):
        value = str(row.get(key) or "").strip().lower()
        if not value:
            continue
        if value.endswith((".test-support.ts", ".test-harness.ts",
                           ".test-support.js", ".test-harness.js")):
            return True
        if any(marker in value for marker in (
                "test-support", "test-harness", "test_support", "test_harness")):
            return True
    return False


def load_inventory(path: str | Path) -> list[dict[str, Any]]:
    """Load and merge inventory JSON(s) from a file or a directory tree.

    Directory scans exclude scratch generations and honour the retriage's
    scanner exclusions, so a stale all-OPEN clone can never shadow the canonical
    merged disposition (see :data:`CANONICAL_INVENTORY_FILENAMES`).
    """
    root = Path(path)
    if not root.exists():
        return []
    if root.is_dir():
        files = _inventory_scan_files(root)
        excluded_ids = _inventory_exclusion_ids(files)
    else:
        files = [root]
        excluded_ids = set()
    merged: dict[str, dict[str, Any]] = {}
    for file_path in files:
        try:
            doc = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for raw in _coerce_rows(doc):
            row = merge_row(raw)
            cid = row["capability_id"]
            if cid in excluded_ids or _is_scanner_excluded(row):
                continue
            if cid not in merged:
                _resolve_inventory_proof(row)
                merged[cid] = row
    return [merged[cid] for cid in sorted(merged)]


def load_manifest(fixtures_dir: str | Path) -> dict[str, str]:
    """Read ``tests/fixtures/differential/MANIFEST.json`` -> {fixture_id: path}."""
    manifest_path = Path(fixtures_dir) / "MANIFEST.json"
    if not manifest_path.exists():
        return {}
    try:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for entry in doc.get("fixtures", []):
        if isinstance(entry, dict) and entry.get("fixture_id") and entry.get("path"):
            out[str(entry["fixture_id"])] = str(entry["path"])
    return out


def load_manifest_entries(fixtures_dir: str | Path) -> list[dict[str, Any]]:
    """Read ``MANIFEST.json`` -> ordered fixture entries (fixture_id/path/family)."""
    manifest_path = Path(fixtures_dir) / "MANIFEST.json"
    if not manifest_path.exists():
        return []
    try:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries: list[dict[str, Any]] = []
    for entry in doc.get("fixtures", []):
        if isinstance(entry, dict) and entry.get("fixture_id") and entry.get("path"):
            entries.append(dict(entry))
    return entries


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def load_declarations(fixtures_dir: str | Path) -> dict[str, dict[str, Any]]:
    """Read typed row declarations (``DECLARATIONS.json``) -> {capability_id: entry}.

    A declaration disposes an otherwise-OPEN inventory row as a non-success
    terminal disposition (``OPERATOR_ONLY``/``FAIL``) with an explicit
    ``operator_prerequisite`` and typed ``basis``. A declaration may NOT claim
    PASS/SAFE_EQUIVALENT and is ignored (fail-closed) if it tries: the overlay
    exists to classify operator-held work, never to fake closure.
    """
    path = Path(fixtures_dir) / DECLARATIONS_FILENAME
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, Mapping):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for entry in doc.get("declarations") or []:
        if not isinstance(entry, Mapping):
            continue
        capability_id = str(entry.get("capability_id") or "").strip()
        disposition = str(entry.get("disposition") or "").strip().upper()
        if not capability_id or disposition not in DECLARED_DISPOSITIONS:
            continue
        out[capability_id] = dict(entry)
    return out


def apply_declarations(
    rows: Sequence[Mapping[str, Any]],
    declarations: Mapping[str, Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Overlay typed declarations onto OPEN rows (or refuse an unproven PASS).

    A row currently OPEN is classified. A row that merely *declares* PASS with
    no executed comparison is not a verified terminal result: a FAIL declaration
    may correct that false success claim to a typed hold (the runner already
    refuses the PASS, this records the owner's basis). Every other terminal row
    (FAIL/SAFE_EQUIVALENT/OPERATOR_ONLY) is left untouched. The declared
    disposition is always non-success, so a declaration can only move a row
    toward a typed OPERATOR_ONLY/FAIL hold or refuse a PASS. A
    ``qubit_path``/``qubit_symbol`` from the declaration is attached only when
    the row has none, so an upstream delta row carries a real Qubit mapping for
    the ``openclaw_undisposed_addition`` gate.
    """
    if not declarations:
        return [dict(row) for row in rows]
    out: list[dict[str, Any]] = []
    for row in rows:
        declaration = declarations.get(str(row.get("capability_id")))
        current = str(row.get("disposition") or "OPEN")
        declared_disposition = str(declaration["disposition"]).upper() if declaration else ""
        overridable = current == "OPEN" or (
            current == "PASS" and declared_disposition == "FAIL")
        if declaration is None or not overridable:
            out.append(dict(row))
            continue
        tagged = dict(row)
        tagged["disposition"] = str(declaration["disposition"]).upper()
        tagged["declared_by"] = "fixture_declaration"
        prerequisite = declaration.get("operator_prerequisite")
        if prerequisite:
            tagged["operator_prerequisite"] = str(prerequisite)
        basis = declaration.get("basis")
        if basis:
            tagged["disposition_basis"] = str(basis)
        declaration_class = declaration.get("declaration_class") or declaration.get("class")
        if declaration_class:
            tagged["declaration_class"] = str(declaration_class)
        for key in ("qubit_path", "qubit_symbol"):
            if declaration.get(key) and not tagged.get(key):
                tagged[key] = declaration.get(key)
        out.append(tagged)
    return out


# --------------------------------------------------------------------------- #
# OPEN-closure overlays (consumed read-only analyst manifests, fail-closed)
# --------------------------------------------------------------------------- #
def _closure_manifest_paths(closure_dir: str | Path) -> list[Path]:
    root = Path(closure_dir)
    if not root.is_dir():
        return []
    return [root / name for name in CLOSURE_FILENAMES if (root / name).is_file()]


def _closure_tests(raw: Mapping[str, Any]) -> list[str]:
    """Candidate test ids an analyst manifest records, in declared order."""
    tests: list[str] = []

    def _add(value: Any) -> None:
        if isinstance(value, str):
            value = value.strip()
            if value and value not in tests:
                tests.append(value)

    proof = raw.get("proof")
    if isinstance(proof, Mapping):
        _add(proof.get("test_id"))
        _add(proof.get("test"))
    _add(raw.get("test"))
    for item in raw.get("test_ids") or []:
        _add(item)
    proof_ref = raw.get("proof_ref")
    if isinstance(proof_ref, str):
        for match in re.findall(r"\[test:\s*([^\]]+?)\]", proof_ref):
            _add(match)
    return tests


def load_open_closures(
    closure_dir: str | Path,
) -> dict[str, dict[str, Any]]:
    """Read the frozen OPEN-closure manifests -> ``{capability_id: entry}``.

    Deterministic and hermetic: only the three named manifest files are read, in
    a fixed order, and a malformed/absent file is skipped (no exception). Each
    entry records the manifest it came from so evidence can cite provenance.
    """
    out: dict[str, dict[str, Any]] = {}
    for path in _closure_manifest_paths(closure_dir):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = doc.get("rows") if isinstance(doc, Mapping) else doc
        if not isinstance(rows, list):
            continue
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            cid = raw.get("capability_id") or raw.get("id")
            if not cid:
                continue
            disposition = str(raw.get("disposition") or "").strip().upper()
            out[str(cid)] = {
                "capability_id": str(cid),
                "disposition": disposition,
                "qubit_path": raw.get("qubit_path"),
                "qubit_symbol": raw.get("qubit_symbol"),
                "tests": _closure_tests(raw),
                "operator_prerequisite": raw.get("operator_prerequisite"),
                "basis": (
                    raw.get("basis")
                    or raw.get("reason")
                    or raw.get("retriage_reason")
                    or raw.get("defect_note")
                    or raw.get("blocker")
                ),
                "blocker": raw.get("blocker"),
                "target": raw.get("target"),
                "proof_kind": (
                    (raw.get("proof") or {}).get("kind")
                    if isinstance(raw.get("proof"), Mapping)
                    else None
                )
                or raw.get("proof_type"),
                "upstream_path": raw.get("upstream_path"),
                "closure_source": path.name,
                "closure_raw_disposition": raw.get("disposition"),
            }
    return out


def _closure_prerequisite(entry: Mapping[str, Any]) -> str:
    """An exact prerequisite for a row the overlay cannot honestly prove."""
    prerequisite = str(entry.get("operator_prerequisite") or "").strip()
    if prerequisite:
        return prerequisite
    disposition = str(entry.get("disposition") or "")
    basis = str(entry.get("basis") or entry.get("blocker") or "no recorded basis")
    target = entry.get("target")
    if disposition == "IMPLEMENTABLE" and target:
        return (
            f"implement {target} and bind a row-linked resolvable test/fixture "
            f"(basis: {basis})"
        )
    if disposition == "MISSING":
        return (
            "no Qubit equivalent; owner must implement or record an explicit "
            f"parity omission (basis: {basis})"
        )
    if disposition == "NOT_APPLICABLE":
        return (
            "not a product capability (docs/test support only); no Qubit parity "
            f"requirement (basis: {basis})"
        )
    if disposition == "OPEN":
        return (
            f"no Qubit behavior proof on disk: {basis}; owner must implement or "
            "bind a row-linked test/fixture"
        )
    return (
        f"manifest claims {disposition} via {entry.get('proof_kind') or 'no proof'}, "
        f"but no row-linked resolvable test exists; owner must bind a real "
        f"test/fixture (basis: {basis})"
    )


def _closure_candidate_test(
    row: Mapping[str, Any], entry: Mapping[str, Any] | None
) -> str | None:
    """First candidate test that really resolves AND references this row.

    The manifest's own citations are tried first; an AST wiring search over the
    real ``tests/`` tree is the independent fallback. A file-only citation (no
    ``::test_fn``) never resolves, so it can never manufacture proof. A row with
    no Qubit production mapping (neither ``qubit_path`` nor ``qubit_symbol``)
    can never be disposed SAFE_EQUIVALENT here: a route-token match alone is not
    a production caller.
    """
    if not (row.get("qubit_path") or row.get("qubit_symbol")):
        return None
    candidates: list[str] = []
    if entry is not None:
        for test_id in entry.get("tests") or []:
            if isinstance(test_id, str) and test_id not in candidates:
                candidates.append(test_id)
    for test_id in _ast_wiring_test_ids(row):
        if test_id not in candidates:
            candidates.append(test_id)
    for test_id in candidates:
        if resolves_test_id(test_id) and _test_references_row(test_id, row):
            return test_id
    return None


def _stamp_closure_safe_equivalent(
    row: dict[str, Any],
    res: dict[str, Any],
    entry: Mapping[str, Any],
    test_id: str,
) -> None:
    proof = {
        "kind": "test_reference",
        "executed": False,
        "level": "wiring/resolution-not-live-execution",
        "basis": CLOSURE_PROOF,
        "test_ids": [test_id],
        "method": "AST test resolution + row route/symbol linkage",
        "closure_source": entry.get("closure_source"),
    }
    row.update({
        "disposition": "SAFE_EQUIVALENT",
        "disposition_basis": "safe_equivalent_proven",
        "proof_level": "wiring",
        "wiring_proof_status": CLOSURE_PROOF,
        "test_ids": [test_id],
        "trace_refs": [test_id],
        "disposition_proof": proof,
    })
    res.update({
        "disposition": "SAFE_EQUIVALENT",
        "disposition_basis": "safe_equivalent_proven",
        "proof_level": "wiring",
        "proof_kind": "test_reference",
        "wiring_proof_status": CLOSURE_PROOF,
        "executed": False,
        "test_ids": [test_id],
        "trace_refs": [test_id],
        "disposition_proof": proof,
        "verification": {
            "exists": True,
            "symbol_present": True,
            "test_resolves": True,
            "method": "static+AST",
        },
        "closure_source": entry.get("closure_source"),
    })
    # A real Qubit production caller: the mapped symbol, else the mapped module
    # path. The mapping is what the row already declared (or the manifest
    # supplied); no caller is invented.
    if not res.get("production_caller"):
        res["production_caller"] = row.get("qubit_symbol") or row.get("qubit_path")


def _stamp_closure_operator_only(
    row: dict[str, Any] | None,
    res: dict[str, Any],
    entry: Mapping[str, Any] | None,
    prerequisite: str,
    basis: str,
) -> None:
    patch = {
        "disposition": "OPERATOR_ONLY",
        "operator_prerequisite": prerequisite,
        "executed": False,
        "disposition_basis": basis,
        "proof_level": None,
        "proof_kind": None,
        "wiring_proof_status": "closure_operator_only",
    }
    if row is not None:
        row.update(patch)
    res.update(patch)
    # A corrected unproven SAFE_EQUIVALENT claim must not keep a success
    # ``declared_disposition`` that would re-trigger the success-proof gates.
    if str(res.get("declared_disposition") or "") in SUCCESS_DISPOSITIONS:
        res["declared_disposition"] = "OPERATOR_ONLY"
    if entry is not None:
        res["closure_source"] = entry.get("closure_source")
        if row is not None:
            row["closure_source"] = entry.get("closure_source")


def _stamp_closure_fail(
    row: dict[str, Any] | None,
    res: dict[str, Any],
    entry: Mapping[str, Any],
    basis: str,
) -> None:
    res["disposition"] = "FAIL"
    res["disposition_basis"] = basis
    res["executed"] = False
    res["closure_source"] = entry.get("closure_source")
    if row is not None:
        row["closure_source"] = entry.get("closure_source")
    _add_diags(
        res,
        [_diag("$.disposition", "closure_defect", basis)],
    )


def _unproven_hold(source: Mapping[str, Any] | None) -> tuple[str, str]:
    """Exact ``(prerequisite, basis)`` for a row the overlay cannot prove.

    An explicit security/policy gate names its typed denial code so the hold is
    actionable and never looks like an ordinary missing fixture; a row that
    already declared an operator prerequisite keeps it; otherwise the generic
    implement-or-bind instruction is used.
    """
    source = source or {}
    upstream = (
        source.get("upstream_path")
        or source.get("qubit_path")
        or source.get("capability_id")
    )
    codes = _gate_error_codes(source)
    existing = str(source.get("operator_prerequisite") or "").strip()
    if codes:
        detail = existing or (
            f"operator must exercise the typed gate {codes[0]} against a "
            f"configured resource"
        )
        return (
            f"security gate {'/'.join(codes)} is not exercised by a "
            f"row-linked resolvable test: {detail}",
            "explicit security gate without a gate-exercising linked test",
        )
    if existing:
        return (
            f"{existing} (no closure disposition and no row-linked "
            f"resolvable test on disk)",
            "row carries an operator prerequisite; no linked proof",
        )
    return (
        f"Qubit-native row has no closure disposition and no row-linked "
        f"resolvable test; owner must bind an executed fixture or test "
        f"reference for {upstream}",
        "no closure manifest entry and no row-linked resolvable test",
    )


def _close_open_result(
    row: dict[str, Any] | None,
    res: dict[str, Any],
    entry: Mapping[str, Any] | None,
) -> None:
    """Apply one closure entry (or a fallback hold) to an OPEN result."""
    if entry is not None:
        disposition = str(entry.get("disposition") or "").upper()
    else:
        disposition = ""
    if entry is not None and disposition in CLOSURE_DEFECT_DISPOSITIONS:
        basis = str(
            entry.get("basis") or entry.get("proof_ref") or "recorded differential defect"
        )
        _stamp_closure_fail(row, res, entry, basis)
        return
    # Only a manifest SUCCESS claim (or an unclassified OPEN row with no
    # manifest entry) may be resolved to SAFE_EQUIVALENT, and then ONLY with a
    # genuinely row-linked resolvable test. A manifest OPERATOR_ONLY/MISSING/
    # IMPLEMENTABLE/NOT_APPLICABLE disposition is honored as a typed hold: an
    # AST-inferred test is never allowed to upgrade the analyst's own non-success
    # classification to a success claim.
    if entry is None or disposition in CLOSURE_SUCCESS_DISPOSITIONS:
        test_id = _closure_candidate_test(row or res, entry)
        if test_id is not None:
            _stamp_closure_safe_equivalent(row or res, res, entry or {}, test_id)
            return
    if entry is not None:
        prerequisite = _closure_prerequisite(entry)
        basis = str(
            entry.get("basis")
            or entry.get("blocker")
            or f"closure manifest disposition {disposition or 'OPEN'}"
        )
    else:
        prerequisite, basis = _unproven_hold(row or res)
    _stamp_closure_operator_only(row, res, entry, prerequisite, basis)


def apply_open_closures(
    rows: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    closures: Mapping[str, Mapping[str, Any]] | None = None,
    closure_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Resolve every still-OPEN result from the frozen closure manifests.

    Real executions win: this runs AFTER ``run_inventory``, so a row already
    disposed by a fixture, vendor behavior comparison, CLI execution or voice
    resolution is untouched. Only ``disposition == OPEN`` results are considered.
    Returns enriched row copies (the originals are not mutated) so the caller can
    evaluate the fail-closed gates against the same effective dispositions the
    results carry.
    """
    if closures is None:
        closures = load_open_closures(closure_dir or Path(DEFAULT_FIXTURES_DIR) / CLOSURE_DIRNAME)
    by_id = _index_results(results)
    enriched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        cid = str(row.get("capability_id"))
        seen.add(cid)
        new_row = dict(row)
        res = by_id.get(cid)
        if res is not None and str(res.get("disposition") or "OPEN") == "OPEN":
            entry = closures.get(cid)
            # Attach a manifest Qubit mapping before linkage so an upstream row
            # with only an upstream mapping can still be proven/gated. Stamped on
            # BOTH the enriched row (gate input) and the report result so
            # `verify` recomputes the identical gates from the report alone.
            if entry is not None:
                for key in ("qubit_path", "qubit_symbol"):
                    if entry.get(key) and not new_row.get(key):
                        new_row[key] = entry.get(key)
                    if entry.get(key) and not res.get(key):
                        res[key] = entry.get(key)
            # Mutate the result object in place so the report carries the
            # terminal disposition, and the enriched row copy alongside it.
            _close_open_result(new_row, res, entry)
        elif (
            res is not None
            and str(res.get("disposition") or "") == "SAFE_EQUIVALENT"
            and not _safe_equivalent_accounted(new_row, res)
        ):
            # A pre-existing SAFE_EQUIVALENT claim with no valid proof is not a
            # success: give a manifest success one honest chance at a row-linked
            # test, then fail closed to a typed hold (never rewritten to PASS).
            entry = closures.get(cid)
            test_id = None
            if entry is None or str(entry.get("disposition") or "") in CLOSURE_SUCCESS_DISPOSITIONS:
                test_id = _closure_candidate_test(new_row, entry)
            if test_id is not None:
                _stamp_closure_safe_equivalent(new_row, res, entry or {}, test_id)
            elif _is_explicit_gate(new_row) and _gate_error_codes(new_row):
                prerequisite, basis = _unproven_hold(new_row)
                _stamp_closure_operator_only(
                    new_row, res, entry, prerequisite, basis)
            else:
                _stamp_closure_operator_only(
                    new_row,
                    res,
                    entry,
                    (
                        "SAFE_EQUIVALENT claim has no row-linked resolvable test or "
                        "executed proof; owner must bind a real test/fixture"
                    ),
                    "unproven SAFE_EQUIVALENT claim refused fail-closed",
                )
        enriched.append(new_row)
    # A fixture-synthesized result has no inventory row; still resolve it.
    for res in results:
        cid = str(res.get("capability_id"))
        if cid in seen:
            continue
        seen.add(cid)
        if str(res.get("disposition") or "OPEN") == "OPEN":
            _close_open_result(None, res, closures.get(cid))
    return enriched


def resolve_fixture_path(fixture_ref: str, fixtures_dir: str | Path) -> Path | None:
    ref = Path(fixture_ref)
    candidates = [ref]
    if not ref.is_absolute():
        candidates.append(Path(fixtures_dir) / ref)
        candidates.append(REPO_ROOT / ref)
        if ref.parts and ref.parts[0] != Path(fixtures_dir).name:
            candidates.append(Path(fixtures_dir) / ref.name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_fixture(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("fixture must be a JSON object")
    return doc


def fixture_normalizers(row: Mapping[str, Any], fixture: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    for source in (row.get("normalizations"), fixture.get("normalizers")):
        if isinstance(source, list):
            for name in source:
                if name not in names:
                    names.append(name)
    return names


def _command_spec(fixture: Mapping[str, Any]) -> str | None:
    """Human-readable command/request spec for a synthesized row."""
    command = fixture.get("command")
    if isinstance(command, Mapping) and command.get("argv"):
        return " ".join(str(a) for a in command.get("argv"))
    request = fixture.get("request")
    if isinstance(request, Mapping):
        method = str(request.get("method") or "").upper()
        path = request.get("path")
        if method or path:
            return f"{method} {path}".strip()
    return None


def synthesize_row_from_fixture(
    fixture_id: str, fixture: Mapping[str, Any], fixture_path: str | Path
) -> dict[str, Any]:
    """Build a first-class inventory row from a fixture that has no row of its own.

    The fixture is the source of truth. Its Qubit mapping supplies the real
    production caller, its command/request supplies the execution spec, and its
    normalizers are carried through. ``upstream_project`` is ``qubit`` because
    the fixture executes the Qubit surface; the verified hermes/openclaw
    mappings are retained as provenance only (there is no upstream command to
    run, so no reference adapter is invoked).
    """
    qubit = fixture.get("qubit") if isinstance(fixture.get("qubit"), Mapping) else {}
    upstream_path = None
    upstream_symbol = None
    for project in ("openclaw", "hermes"):
        mapping = fixture.get(project)
        if isinstance(mapping, Mapping) and mapping.get("path"):
            upstream_path = mapping.get("path")
            upstream_symbol = mapping.get("symbol")
            break
    # A fixture that declares an interface-level SAFE_EQUIVALENT claim must be
    # resolved from a REAL row-specific test/frontend/bundle linkage. The generic
    # manifest-execution test id would otherwise be misread as a row-specific
    # linkage (its token set overlaps almost every row), so it is not seeded when
    # the fixture makes such a claim.
    declares_interface = (
        str(fixture.get("interface_disposition") or "") == "SAFE_EQUIVALENT"
        or bool(fixture.get("declared_safe_equivalent_unproven"))
    )
    raw: dict[str, Any] = {
        "capability_id": fixture_id,
        "family": fixture.get("family"),
        "upstream_project": "qubit",
        "upstream_path": upstream_path,
        "upstream_symbol": upstream_symbol,
        "qubit_path": qubit.get("path"),
        "qubit_symbol": qubit.get("caller"),
        # Honour the fixture's declared disposition/prerequisite; only default
        # to OPEN when the fixture does not declare one.
        "disposition": fixture.get("disposition") or "OPEN",
        "operator_prerequisite": fixture.get("operator_prerequisite"),
        "command": _command_spec(fixture),
        "fixture": str(fixture_path),
        "normalizations": fixture.get("normalizers") or [],
        "test_ids": [] if declares_interface else [
            "tests/test_differential_parity8u.py::test_every_manifest_fixture_is_executed"
        ],
        "trace_refs": [str(fixture_path)],
    }
    if fixture.get("state_changing") is not None:
        raw["state_changing"] = fixture.get("state_changing")
    # A fixture may declare an interface-level SAFE_EQUIVALENT claim (a Qubit
    # mapping whose proof is a real linked test, e.g. a compiled plugin bundle
    # read by a Python test). It is resolved through the same fail-closed wiring
    # resolver as an inventory row; a claim with no real linkage stays OPEN.
    for interface_key in ("interface_disposition",
                          "declared_safe_equivalent_unproven"):
        if fixture.get(interface_key) is not None:
            raw[interface_key] = fixture.get(interface_key)
    evidence = fixture.get("evidence")
    if isinstance(evidence, Mapping):
        for field in EVIDENCE_FIELDS:
            value = evidence.get(field)
            if value is not None:
                raw[field] = value
    row = merge_row(raw)
    row["fixture_derived"] = True
    return row


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
def _normalize_payload(
    payload: Mapping[str, Any], names: Sequence[str]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized, audit = normalize_and_audit(payload, list(names))
    return normalized, audit


def _refusal_detected(result: CallResult) -> bool:
    if result.exit_code in (None, 0):
        return False
    text = " ".join(part for part in (result.stdout, result.stderr) if part)
    for token in ('"refused"', '"denied"', "refused", "not permitted", "permission denied"):
        if token in text:
            return True
    parsed = _parse_json_maybe(result.stdout or "")
    if isinstance(parsed, Mapping) and str(parsed.get("state", "")).lower() in (
        "refused", "denied", "rejected"
    ):
        return True
    return False


def _execute_request(adapter: Adapter, fixture: Mapping[str, Any]) -> CallResult:
    command = fixture.get("command")
    if isinstance(command, Mapping) and command.get("argv"):
        return adapter.execute(
            {
                "kind": "cli",
                "argv": list(command.get("argv") or []),
                "stdin": command.get("stdin"),
                "env": command.get("env") or {},
                "cwd": command.get("cwd"),
            }
        )
    request = fixture.get("request")
    if isinstance(request, Mapping):
        method = str(request.get("method", "GET")).upper()
        body = request.get("body")
        declared_transport = fixture.get("transport")
        kind = str(
            declared_transport
            or request.get("kind")
            or ("http" if method else "request")
        )
        params: Any = None
        if isinstance(body, Mapping):
            params = body.get("params")
        spec: dict[str, Any] = {
            "kind": kind,
            "method": method,
            "path": request.get("path"),
            "headers": request.get("headers") or {},
            "body": body,
            "event": request.get("event"),
            "messages": request.get("messages") or fixture.get("messages") or [],
            "params": params,
            "argv": request.get("argv") or [],
            "env": request.get("env") or {},
            "base_url": fixture.get("base_url"),
            "base_url_ref": fixture.get("base_url_ref"),
            "auth": fixture.get("auth"),
            "events_expected": fixture.get("events_expected"),
            "persisted_state_schema": fixture.get("persisted_state_schema"),
            # Vendor-parity probe (B1): the module/symbol to execute and the
            # optional operator-provided pinned reference root.
            "module": request.get("module") or fixture.get("module"),
            "symbol": request.get("symbol") or fixture.get("symbol"),
            "ref_root": fixture.get("ref_root"),
        }
        sequence = fixture.get("sequence")
        if isinstance(sequence, list) and sequence:
            spec["sequence"] = sequence
        return adapter.execute(spec)
    raise ValueError("fixture has neither command.argv nor request")


def _fixture_argv(fixture: Mapping[str, Any]) -> list[str]:
    command = fixture.get("command")
    if isinstance(command, Mapping) and command.get("argv"):
        return [str(a) for a in command.get("argv")]
    request = fixture.get("request")
    if isinstance(request, Mapping):
        return [str(a) for a in (request.get("argv") or [])]
    return []


def _new_result(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["declared_disposition"] = row.get("disposition", "OPEN")
    result["executed"] = False
    result["fixture_present"] = False
    result["qubit_executed"] = False
    result["reference_executed"] = False
    result["adapter_mock"] = False
    result["refused"] = False
    result["state_changing"] = None
    result["state_changing_skipped"] = False
    result["unsafe_normalizers"] = []
    result["visual_only"] = False
    result["voice_mismatch"] = False
    result["persistence_diff"] = False
    result["secret_in_artifact"] = False
    result["operator_prerequisite"] = result.get("operator_prerequisite")
    result["production_caller"] = row.get("qubit_symbol")
    result["diagnostics"] = []
    result["fixture_hash"] = None
    result["upstream_result_hash"] = None
    result["qubit_result_hash"] = None
    result["qubit_raw_result_hash"] = None
    result["reference_result_hash"] = None
    result["reference_provenance"] = None
    result["nondeterministic_references"] = []
    result["reference_determinism"] = {}
    # Explicit SAFE_EQUIVALENT proof level: only a declared level in
    # ``SAFE_EQUIVALENT_PROOF_LEVELS`` is carried; anything else is unknown.
    level = row.get("proof_level")
    result["proof_level"] = level if level in SAFE_EQUIVALENT_PROOF_LEVELS else None
    result["unclassifiable"] = bool(row.get("unclassifiable"))
    for field in EVIDENCE_FIELDS:
        result.setdefault(field, None)
    return result


def _add_diags(result: dict[str, Any], diags: Sequence[Diagnostic]) -> None:
    for diag in diags:
        result["diagnostics"].append(diag.to_dict())


def _reference_request_spec(spec: Mapping[str, Any], ref_kind: str) -> dict[str, Any]:
    """Build the adapter request for one declared reference (CLI or transport)."""
    request_spec: dict[str, Any] = {"kind": ref_kind, "env": spec.get("env") or {}}
    if ref_kind == "cli":
        request_spec["argv"] = list(spec.get("argv") or [])
        return request_spec
    nested = spec.get("request") if isinstance(spec.get("request"), Mapping) else spec
    request_spec.update(
        {
            "method": nested.get("method") or spec.get("method") or "GET",
            "path": nested.get("path") or spec.get("path"),
            "headers": nested.get("headers") or spec.get("headers") or {},
            "body": nested.get("body") if "body" in nested else spec.get("body"),
            "messages": nested.get("messages") or spec.get("messages") or [],
            "base_url": spec.get("base_url"),
            "base_url_ref": spec.get("base_url_ref"),
            "auth": spec.get("auth"),
            "sequence": spec.get("sequence"),
        }
    )
    return request_spec


def _iter_strings(obj: Any) -> list[str]:
    """Every string leaf of a payload (used for environment-probe detection)."""
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, Mapping):
        for value in obj.values():
            out.extend(_iter_strings(value))
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            out.extend(_iter_strings(value))
    return out


def reference_nondeterminism_reason(
    payload: Mapping[str, Any], spec: Mapping[str, Any]
) -> str | None:
    """Return why a reference result cannot be pinned, or ``None`` if stable.

    Two triggers, both fail-closed:

    * an explicit ``references.<project>.nondeterministic: true`` declaration;
    * a live environment-probe line (a capability's runtime-selected backend,
      e.g. ``web search (keenable)``) whose value changes per invocation and is
      not covered by the fixture normalizer allowlist.

    A nondeterministic reference is still executed and its payload is kept as
    evidence, but it never contributes an ``upstream_result_hash`` and cannot
    back a PASS.
    """
    if spec.get("nondeterministic") is True:
        return "declared nondeterministic=true"
    for text in _iter_strings(payload):
        if _REFERENCE_NONDET_LINE_RE.search(text):
            return _REFERENCE_NONDET_REASON
    return None


def _reference_recheck_budget() -> int:
    """Bounded number of extra reference executions per run (env override)."""
    raw = os.environ.get(REFERENCE_RECHECK_ENV)
    if raw is not None and raw.strip():
        try:
            return max(0, int(raw.strip()))
        except (TypeError, ValueError):
            return DEFAULT_REFERENCE_RECHECKS
    return DEFAULT_REFERENCE_RECHECKS


def _reference_is_state_changing(spec: Mapping[str, Any]) -> bool:
    """True when a declared reference is a mutating (non-rerecheckable) op.

    Covers a CLI argv / nested HTTP request (via :func:`_scan_reference_spec`)
    and every executable step of a declared reference ``sequence``, so a
    multi-step reference is never double-executed when any step could mutate
    state.
    """
    reasons: set[str] = set()
    _scan_reference_spec("reference", spec, reasons)
    sequence = spec.get("sequence")
    if isinstance(sequence, list):
        for index, step in enumerate(sequence):
            if not isinstance(step, Mapping):
                continue
            _scan_state_changing_string(
                step.get("path"), f"reference.sequence[{index}].path", reasons)
            _scan_state_changing_node(
                step.get("body"), f"reference.sequence[{index}].body", reasons)
            _scan_state_changing_node(
                step.get("argv"), f"reference.sequence[{index}].argv", reasons)
            _classify_method(
                step.get("method"), f"reference.sequence[{index}].method", reasons)
    return bool(reasons)


def _recheck_reference_determinism(
    adapter: Adapter,
    request_spec: Mapping[str, Any],
    spec: Mapping[str, Any],
    first_payload: Mapping[str, Any],
    normalized_names: Sequence[str],
    recheck_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Execute a side-effect-free reference a second time and compare hashes.

    Returns ``{"checked": bool, "stable": bool | None, "reason": str | None}``.
    Fail-closed: any condition that prevents a genuine SECOND observation
    (state-changing spec, absent/exhausted budget, transport error) leaves
    ``stable`` not ``True``, so the reference can never back a PASS. When the
    two normalized payloads hash identically the reference is stable and keeps
    its hash.
    """
    kind = str(request_spec.get("kind") or "")
    if kind not in ("cli", "http"):
        return {
            "checked": False,
            "stable": None,
            "reason": f"{kind or 'unknown'} reference kind is not rerecheckable "
                      "(only side-effect-free CLI/HTTP references are rechecked)",
        }
    if _reference_is_state_changing(spec):
        return {
            "checked": False,
            "stable": None,
            "reason": "state-changing reference: determinism recheck skipped",
        }
    if recheck_state is None:
        return {
            "checked": False,
            "stable": None,
            "reason": "determinism recheck not budgeted for this run",
        }
    if int(recheck_state.get("count", 0)) >= int(recheck_state.get("limit", 0)):
        recheck_state["exhausted"] = True
        return {
            "checked": False,
            "stable": None,
            "reason": "determinism recheck budget exhausted "
                      f"({recheck_state.get('limit')})",
        }
    recheck_state["count"] = int(recheck_state.get("count", 0)) + 1
    try:
        second_call = adapter.execute(dict(request_spec))
    except (TransportUnavailable, RuntimeError, OSError,
            subprocess.SubprocessError, NotImplementedError) as exc:
        return {
            "checked": True,
            "stable": False,
            "reason": f"determinism recheck execution failed: {redact_text(str(exc))}",
        }
    second_payload = second_call.to_payload()
    first_hash = result_hash(normalize(first_payload, list(normalized_names)))
    second_hash = result_hash(normalize(second_payload, list(normalized_names)))
    if first_hash != second_hash:
        return {
            "checked": True,
            "stable": False,
            "reason": _REFERENCE_RECHECK_MISMATCH_REASON,
        }
    return {"checked": True, "stable": True, "reason": None}


def _execute_references(
    references: Mapping[str, Any],
    adapters: Mapping[str, Adapter],
    expected: Mapping[str, Any],
    normalized_names: Sequence[str],
    result: dict[str, Any],
    *,
    limit_state: dict[str, Any] | None = None,
    recheck_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute every declared reference, recording evidence regardless of Qubit.

    A row's declared ``references`` (keyed by ``hermes``/``openclaw``) are run
    even when the Qubit side could not execute, so an OPERATOR_ONLY row still
    carries real upstream evidence. Execution is fail-closed: an unresolved
    binding, missing binary/URL, invalid spec or transport error records the
    reference as unavailable and never yields PASS.

    Reference-vs-expected differences are returned as ``diagnostics`` for the
    caller to store under ``reference_diagnostics``. A reference that declares
    its own contract (``reference_expected`` / ``expected`` inside the reference
    spec) is compared against that; otherwise the row contract is used. They
    deliberately do NOT drive the Qubit disposition: the row ``expected`` is the
    Qubit-surface contract (and references are often only the nearest read-only
    upstream surface), so folding them into the Qubit diff would let an upstream
    CLI turn a row GREEN (or RED) that the Qubit side never proved. Only the
    Qubit-vs-expected diff and the pinned/executed gates decide PASS.

    ``limit_state`` (a bounded ``--references-only`` run) caps total upstream
    invocations; it is ``None`` for a normal run. ``recheck_state`` caps the
    extra structural-determinism executions (see
    :func:`_recheck_reference_determinism`); a reference whose recheck budget is
    exhausted is fail-closed (it may not back a PASS).
    """
    reference_info: dict[str, Any] = {}
    reference_hashes: dict[str, str] = {}
    reference_unavailable: list[str] = []
    unpinned_references: list[str] = []
    nondeterministic_references: list[str] = []
    reference_determinism: dict[str, Any] = {}
    reference_executed = False
    reference_diags: list[Diagnostic] = []
    bounded = False

    for project in [p for p in sorted(references) if p in REFERENCE_PROJECTS]:
        spec = references.get(project)
        if (limit_state is not None
                and int(limit_state.get("count", 0)) >= int(limit_state.get("limit", 0))):
            reference_info[project] = {
                "project": project,
                "executed": False,
                "reason": "references-only bound reached",
            }
            reference_unavailable.append(project)
            limit_state["truncated"] = True
            bounded = True
            continue
        if not isinstance(spec, Mapping):
            adapter = adapters.get(project)
            reference_info[project] = {
                "project": project,
                "executed": False,
                "binary": getattr(adapter, "binary", None),
                "reason": "invalid reference spec (not an object)",
            }
            reference_unavailable.append(project)
            continue
        adapter, binding = resolve_reference_binding(project, spec, adapters.get(project))
        info: dict[str, Any] = {
            "project": project,
            "executed": False,
            "binary": binding.get("binary") or getattr(adapter, "binary", None),
        }
        info.update({k: v for k, v in binding.items() if k != "binary"})
        if not binding.get("binding_resolved", True):
            info["reason"] = str(
                binding.get("binding_error") or "reference binding unresolved"
            )
            reference_info[project] = info
            reference_unavailable.append(project)
            continue
        ref_kind = str(spec.get("kind") or spec.get("transport") or "")
        if not ref_kind:
            if spec.get("argv"):
                ref_kind = "cli"
            elif spec.get("request") or spec.get("path"):
                ref_kind = "http"
        if not ref_kind or ref_kind not in ("cli", "http", "sse", "ws", "rpc"):
            info["reason"] = "invalid reference spec (missing argv/transport)"
            reference_info[project] = info
            reference_unavailable.append(project)
            continue
        if adapter is None or not adapter.available():
            info["reason"] = (
                f"{project} binary/URL unavailable "
                f"(set QUBIT_DIFFERENTIAL_{project.upper()}_BIN or _URL)"
            )
            reference_info[project] = info
            reference_unavailable.append(project)
            continue
        request_spec = _reference_request_spec(spec, ref_kind)
        if limit_state is not None:
            limit_state["count"] = int(limit_state.get("count", 0)) + 1
        try:
            reference_call = adapter.execute(request_spec)
        except TransportUnavailable as exc:
            info["reason"] = f"reference transport unavailable: {exc}"
            reference_info[project] = info
            reference_unavailable.append(project)
            continue
        except (RuntimeError, OSError, subprocess.SubprocessError, NotImplementedError) as exc:
            info["reason"] = f"reference execution failed: {exc}"
            reference_info[project] = info
            reference_unavailable.append(project)
            continue
        reference_payload = reference_call.to_payload()
        nondet_reason = reference_nondeterminism_reason(reference_payload, spec)
        if nondet_reason is not None:
            # Declared nondeterministic or a known live-probe line: no second
            # execution is needed (and a declared one must be skipped).
            determinism: dict[str, Any] = {
                "checked": False,
                "stable": False,
                "reason": nondet_reason,
            }
        else:
            # Structural enforcement: a side-effect-free reference is executed a
            # SECOND time and both normalized payloads are hashed. A mismatch
            # (or an unbudgeted/skipped recheck) is fail-closed.
            determinism = _recheck_reference_determinism(
                adapter, request_spec, spec, reference_payload, normalized_names,
                recheck_state,
            )
        if determinism.get("stable") is not True:
            # Unpinnable reference: execute for evidence but never let its
            # payload contribute an unstable upstream hash or back a PASS.
            reason = determinism.get("reason") or _REFERENCE_NONDET_REASON
            reference_determinism[project] = determinism
            info.update(
                {
                    "executed": True,
                    "nondeterministic": True,
                    "nondeterministic_reason": reason,
                    "determinism": determinism,
                    "argv": list(spec.get("argv") or []),
                }
            )
            reference_info[project] = info
            result.setdefault("reference_payloads", {})[project] = reference_payload
            nondeterministic_references.append(project)
            reference_executed = True
            continue
        reference_determinism[project] = determinism
        reference_norm, reference_audit = _normalize_payload(
            reference_payload, normalized_names
        )
        # A reference is compared against the REFERENCE's OWN expected contract
        # when the fixture declares one (``references.<project>.reference_expected``
        # or ``...expected``). Otherwise its differences are still recorded under
        # ``reference_diagnostics`` using the row contract; either way they never
        # drive the Qubit disposition (see the docstring).
        reference_contract = spec.get("reference_expected")
        if not isinstance(reference_contract, Mapping):
            reference_contract = spec.get("expected")
        if not isinstance(reference_contract, Mapping):
            reference_contract = expected
        reference_unsafe = detect_unsafe_normalizers(reference_audit)
        reference_asym = asymmetric_normalizers(
            reference_contract, reference_payload, normalized_names
        )
        if reference_unsafe or reference_asym:
            result["unsafe_normalizers"] = sorted(
                set(result.get("unsafe_normalizers") or [])
                | {str(u.get("path")) for u in reference_unsafe}
                | set(reference_asym)
            )
        reference_hash = result_hash(reference_norm)
        reference_hashes[project] = reference_hash
        reference_executed = True
        version = _probe_version(adapter)
        commit = _resolve_reference_commit(project, adapter, version, spec)
        pinned = _reference_is_pinned(project, commit, version)
        if not pinned:
            unpinned_references.append(project)
        info.update(
            {
                "executed": True,
                "reference_version": version,
                "reference_commit": commit,
                "reference_pinned": pinned,
                "argv": list(spec.get("argv") or []),
                "result_hash": reference_hash,
                "raw_result_hash": result_hash(reference_payload),
                "determinism": determinism,
            }
        )
        reference_info[project] = info
        result.setdefault("reference_payloads", {})[project] = reference_payload
        reference_diags += compare_expected_actual(
            reference_contract, reference_payload, "$", normalizers=normalized_names
        )

    result["references"] = reference_info
    result["reference_provenance"] = {
        project: {
            "bin": entry.get("binary"),
            "version": entry.get("reference_version"),
            "commit": entry.get("reference_commit"),
            "pinned": entry.get("reference_pinned"),
        }
        for project, entry in reference_info.items()
        if isinstance(entry, Mapping)
    }
    result["unpinned_references"] = unpinned_references
    result["nondeterministic_references"] = nondeterministic_references
    result["reference_determinism"] = reference_determinism
    if reference_hashes:
        combined = (
            next(iter(reference_hashes.values()))
            if len(reference_hashes) == 1
            else result_hash(reference_hashes)
        )
        result["reference_result_hash"] = combined
        result["upstream_result_hash"] = combined
    result["reference_executed"] = reference_executed
    return {
        "executed": reference_executed,
        "unavailable": reference_unavailable,
        "unpinned": unpinned_references,
        "nondeterministic": nondeterministic_references,
        "diagnostics": reference_diags,
        "bounded": bounded,
    }


def run_inventory(
    rows: Sequence[Mapping[str, Any]],
    fixtures_dir: str | Path = DEFAULT_FIXTURES_DIR,
    adapters: Mapping[str, Adapter] | None = None,
    *,
    row_ids: Sequence[str] | None = None,
    families: Sequence[str] | None = None,
    references_only: bool = False,
    references_only_limit: int | None = None,
    allow_state_changing: bool = False,
) -> list[dict[str, Any]]:
    """Execute every inventory row AND every manifest fixture; never PASS unexecuted.

    Inventory-driven rows are run first. Then each ``MANIFEST.json`` fixture is
    run: if an inventory row already carries the fixture's ``capability_id`` the
    fixture binds to it; otherwise a first-class row is synthesized from the
    fixture's own fields. This makes an ignored fixture impossible — every
    manifest fixture yields exactly one result with a non-OPEN disposition
    (PASS/FAIL/SAFE_EQUIVALENT/OPERATOR_ONLY) or is explicitly unclassifiable.

    When ``references_only`` is set, the Qubit side is not executed at all and
    declared references are run purely for evidence. The mode can never produce
    PASS: every executed row is recorded as OPERATOR_ONLY with a
    ``references_only`` marker and a bounded number of upstream invocations.

    A state-changing fixture (declared or denylisted) is OPERATOR_ONLY and is
    not executed unless ``allow_state_changing`` is explicitly set; when it is
    skipped the result carries ``state_changing_skipped=True`` and no socket is
    opened.
    """
    if adapters is None:
        adapters = default_adapters()
    manifest = load_manifest(fixtures_dir)
    entries = load_manifest_entries(fixtures_dir)
    # Typed declarations (B2/B3/B5/B7): classify otherwise-OPEN rows as
    # OPERATOR_ONLY/FAIL with an explicit prerequisite before any execution. A
    # declaration can never claim success, so it cannot fake closure.
    declarations = load_declarations(fixtures_dir)
    if declarations:
        rows = apply_declarations(rows, declarations)
    wanted_ids = {str(x) for x in row_ids} if row_ids is not None else None
    wanted_families = {str(x) for x in families} if families is not None else None
    limit_state = None
    if references_only:
        limit = references_only_limit
        if limit is None or limit <= 0:
            limit = DEFAULT_REFERENCES_ONLY_LIMIT
        limit_state = {"count": 0, "limit": int(limit), "truncated": False}
    # Bounded structural-determinism rechecks: one extra execution per declared
    # side-effect-free reference, capped for the whole run.
    recheck_state: dict[str, Any] = {
        "count": 0,
        "limit": _reference_recheck_budget(),
        "exhausted": False,
    }

    # Vendor behavior proof (B1 closure): a single batched execution per side
    # disposes the vendored upstream source rows whose module imports and whose
    # behavioral descriptor equals the pinned reference. Skipped in the
    # references-only diagnostic (which can never claim success) and when the
    # operator has not resolved a pinned reference root (rows stay OPEN).
    vendor_proofs: dict[str, dict[str, Any]] = {}
    cli_proofs: dict[str, dict[str, Any]] = {}
    voice_proofs: dict[str, dict[str, Any]] = {}
    if not references_only:
        vendor_proofs = _vendor_behavior_pass(
            rows, adapters, manifest, wanted_ids, wanted_families)
        # Real Qubit-native execution proofs (wave 22): CLI launcher + voice
        # registry. Skipped in the references-only diagnostic (which can never
        # claim success). Fail-closed: an absent launcher/registry leaves rows
        # OPEN, never a fabricated proof.
        cli_proofs = _cli_behavior_pass(
            rows, adapters, manifest, wanted_ids, wanted_families)
        voice_proofs = _voice_behavior_pass(
            rows, manifest, wanted_ids, wanted_families)

    results: list[dict[str, Any]] = []
    covered: set[str] = set()
    for row in rows:
        results.append(
            _run_row(
                row, fixtures_dir, adapters, manifest,
                references_only=references_only, reference_limit_state=limit_state,
                reference_recheck_state=recheck_state,
                allow_state_changing=allow_state_changing,
                vendor_proofs=vendor_proofs,
                cli_proofs=cli_proofs,
                voice_proofs=voice_proofs,
            )
        )
        covered.add(str(row.get("capability_id")))

    for entry in entries:
        fixture_id = str(entry["fixture_id"])
        if fixture_id in covered:
            continue
        if wanted_ids is not None and fixture_id not in wanted_ids:
            continue
        if wanted_families is not None and str(entry.get("family")) not in wanted_families:
            continue
        results.append(
            _run_fixture_entry(
                entry, fixtures_dir, adapters, manifest,
                references_only=references_only, reference_limit_state=limit_state,
                reference_recheck_state=recheck_state,
                allow_state_changing=allow_state_changing,
            )
        )
        covered.add(fixture_id)
    return results


def _fixture_placeholder_result(
    entry: Mapping[str, Any], fixture_present: bool, kind: str, detail: str
) -> dict[str, Any]:
    """Never silently drop a manifest fixture: emit a fail-closed result."""
    row = merge_row(
        {
            "capability_id": str(entry.get("fixture_id")),
            "family": entry.get("family"),
            "disposition": "OPEN",
            "fixture": str(entry.get("path")),
        }
    )
    result = _new_result(row)
    result["fixture_present"] = fixture_present
    result["unclassifiable"] = True
    result["disposition"] = "OPEN"
    result["manifest_fixture"] = True
    _add_diags(result, [_diag("$.fixture", kind, detail)])
    return result


def _fixture_has_execution(fixture: Mapping[str, Any]) -> bool:
    """True when a fixture carries a real command/request to execute.

    A fixture that only supplies a Qubit mapping (no command/request) has no
    transport; its disposition must come from a resolved proof, never from a
    fabricated execution.
    """
    command = fixture.get("command")
    if isinstance(command, Mapping) and command.get("argv"):
        return True
    request = fixture.get("request")
    if isinstance(request, Mapping) and (
        request.get("path") or request.get("argv")
    ):
        return True
    if str(fixture.get("transport") or "") == VOICE_TRANSPORT:
        return True
    return False


def _wiring_linkage_result(row: Mapping[str, Any]) -> dict[str, Any]:
    """A result stamped from a resolved, non-executed wiring linkage.

    Used only for a fixture row whose proof is a resolved frontend/plugin-bundle
    linkage (there is no transport to execute). The proof fields and the recorded
    evidence hash are copied from the resolved row, so the result satisfies the
    same proof gates as a wiring-disposed inventory row. Fail closed: this is
    only reached after :func:`_resolve_inventory_proof` really attached a
    linkage.
    """
    result = _new_result(row)
    result["disposition"] = "SAFE_EQUIVALENT"
    result["disposition_basis"] = "safe_equivalent_proven"
    result["executed"] = False
    result["qubit_executed"] = False
    result["reference_executed"] = False
    result["proof_level"] = "wiring"
    result["production_caller"] = (
        row.get("qubit_symbol") or "plugin_bundle"
    )
    return result


def _run_fixture_entry(
    entry: Mapping[str, Any],
    fixtures_dir: str | Path,
    adapters: Mapping[str, Adapter],
    manifest: Mapping[str, str],
    *,
    references_only: bool = False,
    reference_limit_state: dict[str, Any] | None = None,
    reference_recheck_state: dict[str, Any] | None = None,
    allow_state_changing: bool = False,
) -> dict[str, Any]:
    """Execute one manifest fixture, synthesizing a row when none exists."""
    fixture_id = str(entry["fixture_id"])
    fixture_ref = str(entry["path"])
    fixture_path = resolve_fixture_path(fixture_ref, fixtures_dir)
    if fixture_path is None:
        return _fixture_placeholder_result(
            entry, False, "fixture_not_found",
            f"manifest fixture missing on disk: {fixture_ref}",
        )
    try:
        fixture = load_fixture(fixture_path)
    except (OSError, ValueError) as exc:
        return _fixture_placeholder_result(
            entry, True, "fixture_parse_error",
            f"manifest fixture could not be parsed: {exc}",
        )
    row = synthesize_row_from_fixture(fixture_id, fixture, fixture_path)
    # A fixture that declares an interface-level SAFE_EQUIVALENT claim is
    # resolved against the real test/frontend/bundle linkage exactly like an
    # inventory row. When that resolution yields a wiring linkage there is no
    # transport to execute: stamp the proof directly (never fabricate; an
    # unresolved claim stays OPEN and falls through to the normal run).
    _resolve_inventory_proof(row)
    if (str(row.get("disposition")) == "SAFE_EQUIVALENT"
            and row.get("proof_level") == "wiring"
            and row.get("test_ids")
            and not _fixture_has_execution(fixture)):
        result = _wiring_linkage_result(row)
        result["fixture_hash"] = file_hash(fixture_path)
        result["manifest_fixture"] = True
        # The fixture holds only the Qubit mapping; no fixture contract was
        # executed, so this is not a skipped fixture (fixture_present stays False).
        return result
    result = _run_row(
        row, fixtures_dir, adapters, manifest,
        references_only=references_only,
        reference_limit_state=reference_limit_state,
        reference_recheck_state=reference_recheck_state,
        allow_state_changing=allow_state_changing,
    )
    result["manifest_fixture"] = True
    return result


def _classify_unavailable(
    row: Mapping[str, Any], result: dict[str, Any], prerequisite: str
) -> dict[str, Any]:
    result["disposition"] = "OPERATOR_ONLY"
    result["operator_prerequisite"] = prerequisite
    result["executed"] = False
    return result


def _run_row(
    row: Mapping[str, Any],
    fixtures_dir: str | Path,
    adapters: Mapping[str, Adapter],
    manifest: Mapping[str, str],
    *,
    references_only: bool = False,
    reference_limit_state: dict[str, Any] | None = None,
    reference_recheck_state: dict[str, Any] | None = None,
    allow_state_changing: bool = False,
    vendor_proofs: Mapping[str, Mapping[str, Any]] | None = None,
    cli_proofs: Mapping[str, Mapping[str, Any]] | None = None,
    voice_proofs: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    result = _new_result(row)
    declared = result["declared_disposition"]

    if row.get("unclassifiable"):
        result["disposition"] = "OPEN"
        result["unclassifiable"] = True
        return result

    fixture_ref = row.get("fixture") or manifest.get(str(row.get("capability_id")))
    if not fixture_ref:
        # Vendor behavior proof (B1 closure) disposes an otherwise-OPEN vendored
        # upstream source row ONLY on a real executed comparison of both sides
        # (import + descriptor equality against the pinned reference root). No
        # proof -> the row keeps its declared disposition (OPEN), fail-closed.
        if declared not in TERMINAL_DISPOSITIONS:
            cid = str(row.get("capability_id"))
            proof = (vendor_proofs or {}).get(cid)
            if proof is not None:
                _attach_vendor_behavior_proof(result, proof)
                return result
            # Qubit-native CLI launcher execution (executed / cli_parity).
            cli_proof = (cli_proofs or {}).get(cid)
            if cli_proof is not None:
                _attach_cli_behavior_proof(result, cli_proof)
                return result
            # Production voice-registry resolution (executed).
            voice_proof = (voice_proofs or {}).get(cid)
            if voice_proof is not None:
                _attach_voice_resolution_proof(result, voice_proof)
                return result
        # A declared terminal disposition is preserved (never rewritten to OPEN);
        # only a SAFE_EQUIVALENT claim without real proof is refused. Execution
        # honesty stays with unexecuted_row / pass_without_reference, which fire
        # on an unexecuted terminal claim.
        resolved = declared if declared in TERMINAL_DISPOSITIONS else "OPEN"
        if resolved == "PASS":
            # A PASS claim requires a really executed comparison: executed=true,
            # both result hashes, an executed pinned reference and no unpinned
            # references. With no bound fixture it can never be proven, so it is
            # refused fail-closed (never preserved as PASS). The dedicated
            # `pass_without_proof` gate still names the declared claim.
            resolved = "FAIL"
            _add_diags(
                result,
                [_diag(
                    "$.disposition", "pass_without_proof",
                    "declared PASS has no bound fixture; PASS requires "
                    "executed=true, non-null qubit_result_hash and "
                    "upstream_result_hash, an executed pinned reference and no "
                    "unpinned references",
                )],
            )
        elif (resolved == "SAFE_EQUIVALENT"
              and not _safe_equivalent_proof(row, result)):
            resolved = "OPEN"
        result["disposition"] = resolved
        if resolved == "SAFE_EQUIVALENT":
            result["proof_level"] = _executed_proof_level(row, result)
        return result

    fixture_path = resolve_fixture_path(str(fixture_ref), fixtures_dir)
    if fixture_path is None:
        _classify_unavailable(
            row, result, f"fixture not found: {fixture_ref} (author the fixture first)"
        )
        return result

    result["fixture_present"] = True
    try:
        fixture = load_fixture(fixture_path)
        result["fixture_hash"] = file_hash(fixture_path)
    except (OSError, ValueError) as exc:
        result["disposition"] = "FAIL"
        _add_diags(result, [_diag("$.fixture", "fixture_parse_error", str(exc))])
        return result

    qubit_meta = fixture.get("qubit") if isinstance(fixture.get("qubit"), Mapping) else {}
    if qubit_meta.get("caller"):
        result["production_caller"] = qubit_meta.get("caller")

    # Plan §8/§15 evidence: row fields win, then fixture top-level, then the
    # fixture's nested ``evidence`` block. Keys already exist (defaults None).
    fixture_evidence = (
        fixture.get("evidence") if isinstance(fixture.get("evidence"), Mapping) else {}
    )
    for field in EVIDENCE_FIELDS:
        if result.get(field) is not None:
            continue
        value = row.get(field)
        if value is None:
            value = fixture.get(field)
        if value is None and isinstance(fixture_evidence, Mapping):
            value = fixture_evidence.get(field)
        if value is not None:
            result[field] = value

    evidence_notes = (
        fixture_evidence.get("evidence_notes")
        if isinstance(fixture_evidence.get("evidence_notes"), Mapping)
        else {}
    )

    normalizers = fixture_normalizers(row, fixture)
    argv = _fixture_argv(fixture)
    env_values = []
    command = fixture.get("command")
    if isinstance(command, Mapping) and isinstance(command.get("env"), Mapping):
        env_values = list(command["env"].values())

    secret_paths = sorted(
        set(
            scan_secrets(fixture)
            + [f"$.command.argv[{i}]" for i, a in enumerate(argv) if contains_secret(a)]
            + [f"$.command.env.{k}" for k, v in (command or {}).get("env", {}).items()
               if isinstance(v, str) and contains_secret(v)]
        )
    )
    if secret_paths:
        result["secret_in_artifact"] = True
        result["disposition"] = "FAIL"
        _add_diags(
            result,
            [_diag(p, "secret_in_artifact", "secret-shaped value present; not executed")
             for p in secret_paths],
        )
        return result

    # --- operator hold (B2/B3/B5/B7): a fixture that deliberately declares the
    # surface OPERATOR_ONLY (handler not served without locked product work, an
    # owner policy decision, or a secret/env held by the operator) is classified
    # terminal without executing anything: no socket, no subprocess, no
    # reference. It can NEVER be PASS and the declared prerequisite is preserved
    # verbatim. This is a typed hold, not a relabel: the disposition is explicit
    # in the fixture and the prerequisite names exactly what unblocks it. ---- #
    if fixture.get("operator_hold") is True:
        prerequisite = (
            fixture.get("operator_prerequisite")
            or row.get("operator_prerequisite")
            or "fixture declares an operator-only hold"
        )
        result["disposition"] = "OPERATOR_ONLY"
        result["operator_prerequisite"] = str(prerequisite)
        result["executed"] = False
        result["operator_hold"] = True
        _add_diags(result, [_diag(
            "$.fixture.operator_hold", "operator_hold",
            f"typed operator hold, not executed: {prerequisite}",
        )])
        return result

    # --- state-changing guard (fail-closed; runs BEFORE any reference/qubit
    # execution, so neither the Qubit side nor an upstream reference can open a
    # socket for a mutating fixture without the explicit opt-in). ----------- #
    state_changing = classify_state_changing(row, fixture)
    result["state_changing"] = bool(state_changing)
    if state_changing and not allow_state_changing:
        prerequisite = _state_changing_prerequisite(state_changing)
        result["state_changing_skipped"] = True
        result["disposition"] = "OPERATOR_ONLY"
        result["operator_prerequisite"] = prerequisite
        result["executed"] = False
        _add_diags(
            result,
            [_diag("$.fixture.state_changing", "state_changing_skipped", prerequisite)],
        )
        return result

    try:
        result["unsafe_normalizers"] = []
        normalized_names = normalizers
        for name in normalizers:
            if name not in ALLOWED_NORMALIZERS:
                raise NormalizerError(f"unknown normalizer: {name!r}")
    except NormalizerError as exc:
        result["disposition"] = "FAIL"
        result["unsafe_normalizers"] = [str(exc)]
        _add_diags(result, [_diag("$.normalizers", "unsafe_normalizer", str(exc))])
        return result

    expected = fixture.get("expected") if isinstance(fixture.get("expected"), Mapping) else {}
    references = (
        fixture.get("references") if isinstance(fixture.get("references"), Mapping) else {}
    )

    # --- reference execution (declared in fixture.references) -------------- #
    # References run independently of the Qubit side: an OPERATOR_ONLY row whose
    # Qubit transport/binary is unavailable (or a --references-only diagnostic)
    # still records real upstream evidence. Reference-vs-expected differences are
    # kept as reference_diagnostics and never manufacture a Qubit PASS.
    reference_outcome = _execute_references(
        references, adapters, expected, normalized_names, result,
        limit_state=reference_limit_state,
        recheck_state=reference_recheck_state,
    )
    result["reference_diagnostics"] = [
        d.to_dict() for d in reference_outcome["diagnostics"]
    ]
    reference_executed = bool(reference_outcome["executed"])
    reference_unavailable = list(reference_outcome["unavailable"])
    unpinned_references = list(reference_outcome["unpinned"])
    nondeterministic_references = list(reference_outcome["nondeterministic"])
    if reference_limit_state is not None and reference_limit_state.get("truncated"):
        result["reference_bounded"] = True
    if (reference_recheck_state is not None
            and reference_recheck_state.get("exhausted")):
        result["reference_recheck_bounded"] = True

    fixture_transport = _fixture_transport_kind(fixture)
    if fixture_transport == VENDOR_TRANSPORT:
        qubit_adapter = adapters.get(VENDOR_TRANSPORT)
        adapter_label = "vendor adapter unavailable (no vendored upstream fork)"
    else:
        qubit_adapter = adapters.get("qubit")
        adapter_label = "qubit adapter/binary unavailable (voicebridge CLI)"
    voice_in_process = _fixture_is_voice(fixture)
    qubit_reason: str | None = None
    if references_only:
        qubit_reason = "references-only diagnostic mode: Qubit side not executed"
    elif qubit_adapter is None:
        qubit_reason = adapter_label
    elif not qubit_adapter.available() and not voice_in_process:
        qubit_reason = adapter_label

    qubit_call: CallResult | None = None
    if qubit_reason is None:
        try:
            qubit_call = _execute_request(qubit_adapter, fixture)
        except NotImplementedError as exc:
            qubit_reason = f"qubit transport unavailable: {exc}"
        except TransportUnavailable as exc:
            qubit_reason = str(exc)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            result["disposition"] = "FAIL"
            _add_diags(result, [_diag("$.qubit", "adapter_error", str(exc)[:300])])
            return result

    if qubit_call is None:
        # The Qubit side could not execute. This is a terminal OPERATOR_ONLY
        # classification (never PASS) even when references did execute.
        result["disposition"] = "OPERATOR_ONLY"
        result["operator_prerequisite"] = qubit_reason or "qubit side not executed"
        if references_only:
            result["references_only"] = True
        return result

    diags: list[Diagnostic] = []
    result["qubit_executed"] = True
    result["executed"] = True
    result["duration_ms"] = qubit_call.duration_ms
    result["transport"] = qubit_call.transport
    result["http_status"] = qubit_call.http_status
    qubit_payload = qubit_call.to_payload()
    # A server/CLI-echoed secret must never be persisted verbatim: redact the
    # payload before storing/reporting, while still failing the secret gate.
    payload_secret_paths = scan_secrets(qubit_payload)
    if payload_secret_paths:
        result["secret_in_artifact"] = True
        _add_diags(
            result,
            [_diag(p, "secret_in_artifact",
                   "secret-shaped value in Qubit payload; redacted before storing")
             for p in payload_secret_paths],
        )
        qubit_payload = redact(qubit_payload)
    result["qubit_payload"] = qubit_payload
    if (str(row.get("family")) == "voice"
            or str(qubit_call.transport or "") == VOICE_TRANSPORT):
        _record_voice_result(result, qubit_payload)
    result["qubit_raw_result_hash"] = result_hash(qubit_payload)
    if result.get("raw_hash") is None:
        # raw_hash is the byte-comparable proof of the un-normalized result.
        result["raw_hash"] = result["qubit_raw_result_hash"]
    if qubit_call.meta.get("adapter"):
        result["adapter_mock"] = bool(qubit_call.meta.get("mock", False))
    # A vendor-parity probe executes BOTH sides itself (vendored module +
    # pinned reference). Its behavioral comparison is a real executed reference,
    # so record the reference evidence it carries before the disposition decision;
    # otherwise a vendor row could never earn even SAFE_EQUIVALENT.
    vendor_behavior_ok = False
    vendor_reference_hash: str | None = None
    if (str(qubit_call.transport or "") == VENDOR_TRANSPORT
            and qubit_call.meta.get("reference_executed")):
        reference_executed = True
        reference_hash = qubit_call.meta.get("reference_hash")
        vendor_reference_hash = str(reference_hash) if reference_hash else None
        if reference_hash:
            result["upstream_result_hash"] = str(reference_hash)
            result["reference_result_hash"] = str(reference_hash)
        result["vendor_reference"] = {
            "project": qubit_call.meta.get("reference_project"),
            "reference_executed": True,
            "reference_hash": reference_hash,
            "qubit_hash": qubit_call.meta.get("qubit_hash"),
            "behavior_equal": bool(qubit_call.meta.get("behavior_equal")),
        }
        vendor_behavior_ok = bool(qubit_call.meta.get("behavior_equal"))
        if not vendor_behavior_ok:
            _add_diags(result, [_diag(
                "$.behavior_equal", "value",
                "vendored module behavior differs from the pinned reference: "
                + canonical_json(qubit_call.json or {}),
            )])

    qubit_norm, qubit_audit = _normalize_payload(qubit_payload, normalized_names)
    expected_norm, expected_audit = _normalize_payload(expected, normalized_names)

    unsafe_events = (
        detect_unsafe_normalizers(qubit_audit)
        + detect_unsafe_normalizers(expected_audit)
    )
    asymmetric = asymmetric_normalizers(expected, qubit_payload, normalized_names)
    if unsafe_events or asymmetric:
        # Merge (never overwrite) the reference-side unsafe flags already found.
        result["unsafe_normalizers"] = sorted(
            set(result.get("unsafe_normalizers") or [])
            | set(str(u.get("path")) for u in unsafe_events)
            | set(asymmetric)
        )
    result["qubit_result_hash"] = result_hash(qubit_norm)
    result["refused"] = _refusal_detected(qubit_call)

    diags = compare_expected_actual(
        expected, qubit_payload, "$", normalizers=normalized_names
    )
    diags += _family_diagnostics(str(row.get("family")), expected_norm, qubit_norm)

    # --- disposition (fail closed) ---------------------------------------- #
    if declared == "FAIL":
        result["disposition"] = "FAIL"
    elif diags or result["unsafe_normalizers"] or result["secret_in_artifact"]:
        result["disposition"] = "SAFE_EQUIVALENT" if declared == "SAFE_EQUIVALENT" else "FAIL"
    elif nondeterministic_references:
        # A live environment probe cannot prove parity: drop the row to
        # OPERATOR_ONLY rather than emit an unstable (or hidden) PASS.
        result["disposition"] = "OPERATOR_ONLY"
        result["operator_prerequisite"] = (
            "nondeterministic upstream reference(s) cannot be pinned: "
            + ", ".join(nondeterministic_references)
            + "; re-run with a pinned reference or accept the limitation"
        )
    elif declared == "SAFE_EQUIVALENT":
        result["disposition"] = "SAFE_EQUIVALENT"
    elif vendor_behavior_ok:
        # A vendor behavioral comparison is a genuine executed proof, but the
        # module surface is not the row's own end-to-end production path, so it
        # is a wiring-level SAFE_EQUIVALENT — never PASS on a byte/descriptor
        # match alone.
        result["disposition"] = "SAFE_EQUIVALENT"
        result["disposition_basis"] = "safe_equivalent_proven"
        result["proof_level"] = "wiring"
        result["proof_kind"] = VENDOR_PROOF
        result["wiring_proof_status"] = VENDOR_PROOF
        vendor_block = result.get("vendor_reference")
        if isinstance(vendor_block, Mapping):
            result["disposition_proof"] = {
                "kind": VENDOR_PROOF,
                "executed": True,
                "level": "wiring/module-surface-behavioral-comparison",
                "basis": VENDOR_PROOF,
                "reference_hash": vendor_block.get("reference_hash"),
                "method": "vendor probe + pinned reference descriptor compare",
            }
    elif not reference_executed:
        result["disposition"] = "OPERATOR_ONLY"
        detail = (
            ", ".join(reference_unavailable)
            if reference_unavailable
            else "no references declared for this surface"
        )
        result["operator_prerequisite"] = (
            f"no upstream reference executed ({detail}); set "
            "QUBIT_DIFFERENTIAL_<PROJECT>_BIN or declare fixture.references"
        )
    elif unpinned_references:
        # A floating upstream can never prove parity: record a typed
        # safe-equivalent instead of a false PASS.
        result["disposition"] = "SAFE_EQUIVALENT"
        result["disposition_note"] = (
            "typed safe-equivalent: reference(s) unpinned ("
            + ", ".join(unpinned_references)
            + "); parity cannot be claimed against a floating upstream"
        )
    else:
        # Hard precondition for parity: a PASS must be executed and carry BOTH
        # the Qubit and upstream result hashes (the reference side is already
        # required to be executed and fully pinned above). If any proof is
        # missing, it is not PASS — fail closed instead of claiming parity.
        if (not result.get("executed")
                or not result.get("qubit_result_hash")
                or not result.get("upstream_result_hash")):
            result["disposition"] = "FAIL"
            _add_diags(
                result,
                [_diag("$.disposition", "pass_without_proof",
                       "PASS requires executed=true and non-null "
                       "qubit_result_hash and upstream_result_hash")],
            )
        else:
            result["disposition"] = "PASS"

    # v1.1 evidence requirement: a claimed success must carry every evidence
    # field (or an explicit non-applicability note) so a PASS can never be
    # counted without evidence. A vendor behavioral proof fills the fields a
    # module-surface probe genuinely exercises (see :func:`_fill_vendor_evidence`).
    if (result["disposition"] in SUCCESS_DISPOSITIONS
            and result.get("proof_kind") == VENDOR_PROOF):
        payload = result.get("qubit_payload")
        vendored = payload.get("vendored") if isinstance(payload, Mapping) else None
        vref = result.get("vendor_reference")
        _fill_vendor_evidence(
            result,
            str((vref or {}).get("module") or (vendored or {}).get("module") or ""),
            vendored if isinstance(vendored, Mapping) else {},
            (vref or {}).get("reference_hash"),
        )
    if result["disposition"] in SUCCESS_DISPOSITIONS:
        missing_evidence = missing_evidence_fields(result, evidence_notes)
        if missing_evidence:
            result["missing_evidence"] = missing_evidence
            result["disposition"] = "FAIL"
            _add_diags(
                result,
                [_diag("$.evidence", "missing_evidence",
                       "PASS/SAFE_EQUIVALENT row lacks evidence: "
                       + ", ".join(missing_evidence))],
            )

    if any(str(d.path).startswith(("$.persisted_state", "$.receipts")) for d in diags) or any(
        d.kind in ("persistence", "recovery") for d in diags
    ):
        result["persistence_diff"] = True
    if str(row.get("family")) == "voice":
        mismatched = [
            d for d in diags
            if str(d.path).startswith(("$.voice_operation", "$.resolved_identity", "$.confirmation"))
        ]
        if mismatched and result["disposition"] == "PASS":
            result["voice_mismatch"] = True
    if str(row.get("family")) in VISUAL_FAMILIES and not qubit_norm.get("backend_operation"):
        result["visual_only"] = True

    if (result["disposition"] == "SAFE_EQUIVALENT"
            and result.get("proof_kind") != VENDOR_PROOF):
        result["proof_level"] = _executed_proof_level(row, result)

    _add_diags(result, diags)
    return result


# --------------------------------------------------------------------------- #
# Fail-closed gates
# --------------------------------------------------------------------------- #
def _index_results(results: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(r.get("capability_id")): r for r in results}


def _gate(
    name: str, triggering: list[str], detail: str
) -> dict[str, Any]:
    return {"gate": name, "ok": not triggering, "rows": triggering, "detail": detail}


def apply_fail_closed_gates(
    rows: Sequence[Mapping[str, Any]], results: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Re-evaluate every required fail-closed gate from rows + results.

    Gates are evaluated over the UNION of inventory rows and results, so a
    result with no inventory row (a fixture synthesized by
    :func:`run_inventory`) is still gated instead of being silently ignored.
    """
    by_id = _index_results(results)
    gates: list[dict[str, Any]] = []

    entries: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    seen_ids: set[str] = set()
    for row in rows:
        cid = str(row.get("capability_id"))
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        entries.append((row, by_id.get(cid, row)))
    for res in results:
        cid = str(res.get("capability_id"))
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        entries.append((res, res))

    unexecuted: list[str] = []
    missing_caller: list[str] = []
    skipped: list[str] = []
    mocked: list[str] = []
    refusal: list[str] = []
    unsafe: list[str] = []
    visual: list[str] = []
    voice: list[str] = []
    persistence: list[str] = []
    secrets: list[str] = []
    undisposed: list[str] = []
    safe_eq_unproven: list[str] = []
    proof_level_bad: list[str] = []
    proof_level_resolvable_bad: list[str] = []
    open_rows: list[str] = []
    no_reference_pass: list[str] = []
    pass_without_proof: list[str] = []
    unpinned_pass: list[str] = []
    declaration_bad: list[str] = []

    # Explicit coverage: every INPUT row must have a result object at all.
    uncovered: list[str] = []
    covered_ids: set[str] = set()
    for row in rows:
        cid = str(row.get("capability_id"))
        if cid in covered_ids:
            continue
        covered_ids.add(cid)
        if cid not in by_id:
            uncovered.append(cid)

    for row, res in entries:
        cid = str(row.get("capability_id"))
        disposition = str(res.get("disposition") or "OPEN")
        executed = bool(res.get("executed"))

        if disposition == "OPEN":
            open_rows.append(cid)
        if disposition == "PASS" and res.get("unpinned_references"):
            unpinned_pass.append(cid)
        # A SAFE_EQUIVALENT row backed by a valid typed proof is accounted even
        # though it was not live-executed (an executed-level proof did run the
        # Qubit side; a wiring-level proof is a test/frontend/plugin/vendor
        # linkage) and even though it has no upstream reference (a vendor
        # behavior_equal reference or a linkage test is its proof of record).
        # A PASS is NEVER accounted by a typed proof: it still requires a live
        # executed comparison, which the pass_without_proof gate enforces.
        se_accounted = (
            disposition == "SAFE_EQUIVALENT"
            and _safe_equivalent_accounted(row, res)
        )
        if disposition == "PASS" and not res.get("reference_executed"):
            no_reference_pass.append(cid)
        elif (disposition == "SAFE_EQUIVALENT"
              and not res.get("reference_executed") and not se_accounted):
            no_reference_pass.append(cid)
        if disposition == "PASS" and not executed:
            unexecuted.append(cid)
        elif disposition == "SAFE_EQUIVALENT" and not executed and not se_accounted:
            unexecuted.append(cid)
        if disposition in ("PASS", "SAFE_EQUIVALENT") and not (
            res.get("production_caller") or row.get("qubit_symbol")
        ):
            missing_caller.append(cid)
        if res.get("fixture_present") and not executed and disposition != "OPERATOR_ONLY":
            skipped.append(cid)
        if res.get("adapter_mock") and disposition in ("PASS", "SAFE_EQUIVALENT"):
            mocked.append(cid)
        if res.get("refused") and disposition in ("PASS", "SAFE_EQUIVALENT"):
            refusal.append(cid)
        if res.get("unsafe_normalizers"):
            unsafe.append(cid)
        if res.get("visual_only") and disposition in ("PASS", "SAFE_EQUIVALENT"):
            visual.append(cid)
        if res.get("voice_mismatch"):
            voice.append(cid)
        if res.get("persistence_diff") and disposition in ("PASS", "SAFE_EQUIVALENT"):
            persistence.append(cid)
        if res.get("secret_in_artifact") or scan_secrets(res.get("qubit_payload")):
            secrets.append(cid)
        declared = str(
            res.get("declared_disposition") or row.get("declared_disposition")
            or row.get("disposition") or ""
        )
        # A PASS claim is refused no matter which side carries it: a declared
        # PASS that the runner refused, or a resolved PASS, must still be fully
        # proven (executed, both result hashes, an executed reference, no
        # unpinned reference) or it fails closed here.
        if (disposition == "PASS" or declared == "PASS") and not (
            executed
            and res.get("qubit_result_hash")
            and res.get("upstream_result_hash")
            and res.get("reference_executed")
            and not (res.get("unpinned_references") or [])
        ):
            pass_without_proof.append(cid)
        if declared == "SAFE_EQUIVALENT" and not _safe_equivalent_proof(row, res):
            safe_eq_unproven.append(cid)
        # Explicit proof LEVEL: a SAFE_EQUIVALENT claim must declare
        # executed/wiring AND back that level with the matching real proof. A
        # wiring-level proof must cite a test that RESOLVES and that actually
        # references the row's specific route/symbol (a class-level surface
        # test is not proof for an individual route).
        if declared == "SAFE_EQUIVALENT" or disposition == "SAFE_EQUIVALENT":
            level = res.get("proof_level")
            if level is None:
                level = row.get("proof_level")
            executable = bool(executed) and bool(res.get("qubit_result_hash"))
            frontend = _frontend_linkage(row) or _frontend_linkage(res)
            bundle = _plugin_bundle_linkage(row) or _plugin_bundle_linkage(res)
            vendor = _vendor_behavior_valid(row, res)
            resolvable = (bool(_resolvable_test_references(row, res))
                          or bool(frontend) or bool(bundle) or vendor)
            linked = (bool(_linked_test_references(row, res))
                      or bool(frontend) or bool(bundle) or vendor)
            if level not in SAFE_EQUIVALENT_PROOF_LEVELS:
                proof_level_bad.append(cid)
            elif level == "executed" and not executable:
                proof_level_bad.append(cid)
            elif level == "wiring" and not resolvable:
                proof_level_bad.append(cid)
                proof_level_resolvable_bad.append(cid)
            elif level == "wiring" and not linked:
                proof_level_bad.append(cid)
        if (is_openclaw_delta_row(row)
                and not _is_scanner_excluded(row)
                and not _has_qubit_disposition_and_proof(row, res)):
            undisposed.append(cid)
        # A typed fixture declaration (B2/B3/B5/B7/B10) is fail-closed: it must
        # carry a non-success terminal disposition AND an explicit
        # operator_prerequisite AND a typed basis. A declaration that cannot say
        # why it is held, or that tries to assert success, is refused here.
        if (res.get("declared_by") == "fixture_declaration"
                or row.get("declared_by") == "fixture_declaration"):
            disposition_value = str(res.get("disposition") or row.get("disposition"))
            prerequisite_value = res.get("operator_prerequisite") or row.get(
                "operator_prerequisite")
            basis_value = res.get("disposition_basis") or row.get("disposition_basis")
            if (disposition_value not in DECLARED_DISPOSITIONS
                    or not prerequisite_value or not basis_value):
                declaration_bad.append(cid)

    gates.append(_gate("unexecuted_row", unexecuted,
                       "row claims PASS without an executed result, or "
                       "SAFE_EQUIVALENT without an executed result and without "
                       "a valid typed proof (test/trace, frontend/plugin "
                       "linkage, or vendor behavior_equal reference)"))
    gates.append(_gate("missing_production_caller", missing_caller,
                       "row claims PASS/SAFE_EQUIVALENT without a Qubit production caller"))
    gates.append(_gate("skipped_fixture", skipped,
                       "fixture present but execution was skipped without OPERATOR_ONLY"))
    gates.append(_gate("mock_replaced_real_path", mocked,
                       "a mock adapter result was counted as PASS/SAFE_EQUIVALENT"))
    gates.append(_gate("refusal_counted_as_success", refusal,
                       "a typed refusal/denial was counted as PASS/SAFE_EQUIVALENT"))
    gates.append(_gate("unsafe_normalizer", unsafe,
                       "a normalizer collapsed an out-of-shape difference"))
    gates.append(_gate("visual_only_control", visual,
                       "a visual control reached no canonical backend operation"))
    gates.append(_gate("voice_backend_mismatch", voice,
                       "voice resolved a different canonical operation than the reference"))
    gates.append(_gate("persistence_or_recovery_diff", persistence,
                       "persisted state or recovery semantics diverged"))
    gates.append(_gate("secret_in_artifact", secrets,
                       "secret-shaped value present in an artifact"))
    gates.append(_gate("openclaw_undisposed_addition", undisposed,
                       "upstream latest-release/default-branch delta row lacks a "
                       "Qubit disposition and proof"))
    gates.append(_gate("safe_equivalent_requires_proof", safe_eq_unproven,
                       "row declares SAFE_EQUIVALENT without a Qubit mapping "
                       "and a real test/trace reference"))
    gates.append(_gate("proof_level", proof_level_bad,
                       "SAFE_EQUIVALENT row does not carry an explicit proof "
                       "level in {executed, wiring} backed by real proof "
                       "(executed result, a linked resolvable tests/ test, or a "
                       "genuine frontend vitest linkage)"))
    gates.append(_gate("proof_level_requires_resolvable_test_id",
                       proof_level_resolvable_bad,
                       "SAFE_EQUIVALENT wiring proof does not name a real, "
                       "resolvable tests/<file>.py::<test_fn> or a frontend "
                       "vitest that imports the mapped component"))
    gates.append(_gate("unexecuted_open_row", open_rows,
                       "row was left OPEN (not executed and not classified "
                       "OPERATOR_ONLY/PASS/FAIL) — fail closed"))
    gates.append(_gate("pass_without_reference", no_reference_pass,
                       "row claims PASS without any executed upstream "
                       "reference, or SAFE_EQUIVALENT without an executed "
                       "reference and without a valid typed proof"))
    gates.append(_gate("pass_without_proof", pass_without_proof,
                       "row claims PASS without an executed comparison, both "
                       "result hashes and an executed pinned upstream reference "
                       "(non-executed PASS fails closed)"))
    gates.append(_gate("unpinned_reference_pass", unpinned_pass,
                       "row claims PASS while an executed reference is not the "
                       "contractual pin (floating upstream)"))
    gates.append(_gate("inventory_coverage", uncovered,
                       "input row has no result object at all (silently unexecuted)"))
    gates.append(_gate("declaration_integrity", declaration_bad,
                       "typed declaration lacks a non-success terminal "
                       "disposition, an operator_prerequisite or a typed basis"))
    gates.append(_byte_parity_gate())
    return gates


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def build_totals(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {name.lower(): 0 for name in DISPOSITIONS}
    for row in rows:
        disposition = str(row.get("disposition") or "OPEN")
        key = disposition.lower()
        if key in counts:
            counts[key] += 1
    return {
        "total": len(rows),
        "pass": counts["pass"],
        "fail": counts["fail"],
        "safe_equivalent": counts["safe_equivalent"],
        "operator_only": counts["operator_only"],
        "open": counts["open"],
    }


def build_verdict(rows: Sequence[Mapping[str, Any]], gates: Sequence[Mapping[str, Any]]) -> str:
    if any(not g.get("ok") for g in gates):
        return "INCOMPLETE"
    if any(str(r.get("disposition")) == "FAIL" for r in rows):
        return "INCOMPLETE"
    # An OPEN row is an unexecuted, undisposed requirement: fail closed. An
    # OPERATOR_ONLY row that passed every fail-closed gate is a terminal,
    # allowed classification (a live hardware/credential prerequisite), so the
    # run is a limitation, not a failure.
    if any(str(r.get("disposition")) == "OPEN" for r in rows):
        return "INCOMPLETE"
    if any(str(r.get("disposition")) == "OPERATOR_ONLY" for r in rows):
        return "VERIFIED WITH EXPLICIT LIMITATION"
    return "VERIFIED"


def collect_reference_provenance(
    rows: Sequence[Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Resolved, non-secret reference provenance per project (deterministic).

    Derived only from the executed reference records already present on the row
    results, so ``verify`` recomputes the exact same block from the report
    alone. Contains no env value and no token: only the resolved binary path,
    probed ``--version`` string, resolved commit and pinned boolean. Rows are
    walked in ``capability_id`` order and the first non-null value per project
    wins, so two identical runs produce an identical block.
    """
    collected: dict[str, dict[str, Any]] = {}
    for result in sorted(rows, key=lambda r: str(r.get("capability_id"))):
        references = result.get("references")
        if not isinstance(references, Mapping):
            continue
        for project in sorted(references):
            entry = references[project]
            if not isinstance(entry, Mapping):
                continue
            slot = collected.setdefault(
                project,
                {"bin": None, "version": None, "commit": None, "pinned": None},
            )
            if slot["bin"] is None and entry.get("binary"):
                slot["bin"] = entry.get("binary")
            if slot["version"] is None and entry.get("reference_version") is not None:
                slot["version"] = entry.get("reference_version")
            if slot["commit"] is None and entry.get("reference_commit") is not None:
                slot["commit"] = entry.get("reference_commit")
            if slot["pinned"] is None and entry.get("reference_pinned") is not None:
                slot["pinned"] = bool(entry.get("reference_pinned"))
    return {project: collected[project] for project in sorted(collected)}


def build_report(
    rows: Sequence[Mapping[str, Any]],
    gates: Sequence[Mapping[str, Any]],
    reference_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if reference_provenance is None:
        reference_provenance = collect_reference_provenance(rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": GENERATED_BY,
        "runner_version": RUNNER_VERSION,
        "reference_provenance": dict(reference_provenance),
        "rows": list(rows),
        "totals": build_totals(rows),
        "gates": list(gates),
        "verdict": build_verdict(rows, gates),
    }


def report_exit_code(report: Mapping[str, Any]) -> int:
    if report.get("internal_error"):
        return EXIT_INTERNAL
    rows = report.get("rows") or []
    # A failed fail-closed gate (e.g. unexecuted_open_row) is a definitive FAIL
    # and outranks the weaker "could not classify" signal: an OPEN row must make
    # the run fail rather than merely report unclassifiable.
    if any(not g.get("ok") for g in report.get("gates") or []):
        return EXIT_FAIL
    if any(str(r.get("disposition")) == "FAIL" for r in rows):
        return EXIT_FAIL
    # An OPEN row is an unexecuted, undisposed requirement and stays non-zero.
    # An OPERATOR_ONLY row is a terminal, allowed classification (the gate suite
    # above already proved it is not a disguised PASS/SKIP): exit 0 with the
    # explicit-limitation verdict, matching build_verdict.
    if any(str(r.get("disposition")) == "OPEN" for r in rows):
        return EXIT_FAIL
    if any(r.get("unclassifiable") for r in rows):
        return EXIT_UNCLASSIFIABLE
    return EXIT_OK


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _write_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    if target.parent and str(target.parent):
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def cmd_inventory(args: argparse.Namespace) -> int:
    source = Path(args.source or DEFAULT_SEED_INVENTORY)
    rows = load_inventory(source)
    # The regenerated inventory must already carry the typed declarations, so a
    # later `run`/`verify` over it sees the same OPERATOR_ONLY/FAIL holds without
    # depending on the fixture dir being passed again.
    declarations = load_declarations(DEFAULT_FIXTURES_DIR)
    if declarations:
        rows = apply_declarations(rows, declarations)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_by": GENERATED_BY,
        "runner_version": RUNNER_VERSION,
        "inventory_source": str(source),
        "rows": rows,
        "totals": build_totals(rows),
    }
    _write_json(args.out, payload)
    print(f"inventory: {len(rows)} rows -> {args.out}")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    inventory_path = Path(args.inventory)
    if not inventory_path.exists():
        print(f"error: inventory not found: {inventory_path}", file=sys.stderr)
        return EXIT_USAGE
    rows = load_inventory(inventory_path)
    # Apply typed declarations before filtering so the gate evaluation below sees
    # the same classified rows the run executes.
    declarations = load_declarations(args.fixtures_dir)
    if declarations:
        rows = apply_declarations(rows, declarations)
    if args.family:
        rows = [r for r in rows if str(r.get("family")) == args.family]
    if args.row:
        rows = [r for r in rows if str(r.get("capability_id")) == args.row]
    adapters = default_adapters()
    results = run_inventory(
        rows, args.fixtures_dir, adapters,
        row_ids=[args.row] if args.row else None,
        families=[args.family] if args.family else None,
        references_only=bool(getattr(args, "references_only", False)),
        references_only_limit=getattr(args, "reference_limit", None),
        allow_state_changing=bool(getattr(args, "allow_state_changing", False)),
    )
    # Consume the read-only analyst OPEN-closure overlays LAST: real executions
    # already disposed their rows, and only the still-OPEN remainder is resolved
    # (SAFE_EQUIVALENT only with a genuinely row-linked test, else a typed
    # OPERATOR_ONLY hold). Applied to a copy of the rows so the gates evaluate the
    # same effective dispositions the report carries.
    closures = load_open_closures(
        Path(args.fixtures_dir) / CLOSURE_DIRNAME
    )
    if closures:
        rows = apply_open_closures(rows, results, closures)
    gates = apply_fail_closed_gates(rows, results)
    report = build_report(results, gates)
    _write_json(args.out, report)
    totals = report["totals"]
    mode = " [references-only diagnostic]" if getattr(args, "references_only", False) else ""
    print(
        f"run{mode}: {totals['total']} rows, pass={totals['pass']} fail={totals['fail']} "
        f"safe_equivalent={totals['safe_equivalent']} "
        f"operator_only={totals['operator_only']} open={totals['open']}; "
        f"verdict={report['verdict']}"
    )
    return report_exit_code(report)


def cmd_verify(args: argparse.Namespace) -> int:
    report_path = Path(args.report)
    if not report_path.exists():
        print(f"error: report not found: {report_path}", file=sys.stderr)
        return EXIT_USAGE
    doc = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        print("error: malformed report", file=sys.stderr)
        return EXIT_INTERNAL
    rows = doc["rows"]
    gates = apply_fail_closed_gates(rows, rows)
    rebuilt = build_report(rows, gates)
    recomputed = {gate["gate"]: gate["ok"] for gate in gates}
    reported = {gate.get("gate"): gate.get("ok") for gate in doc.get("gates") or []}
    if recomputed != reported:
        print("error: report gates do not match recomputed gates", file=sys.stderr)
        return EXIT_FAIL
    if doc.get("totals") != rebuilt["totals"]:
        print("error: report totals do not match recomputed totals", file=sys.stderr)
        return EXIT_FAIL
    if doc.get("verdict") != rebuilt["verdict"]:
        print("error: report verdict does not match recomputed verdict", file=sys.stderr)
        return EXIT_FAIL
    if doc.get("reference_provenance") != rebuilt["reference_provenance"]:
        print(
            "error: report reference_provenance does not match recomputed "
            "resolved reference provenance",
            file=sys.stderr,
        )
        return EXIT_FAIL
    for project in sorted(rebuilt["reference_provenance"]):
        prov = rebuilt["reference_provenance"][project]
        print(
            "verify: reference {project} bin={bin} version={version!r} "
            "commit={commit} pinned={pinned}".format(
                project=project,
                bin=prov.get("bin"),
                version=prov.get("version"),
                commit=prov.get("commit"),
                pinned=prov.get("pinned"),
            )
        )
    failed = [g["gate"] for g in gates if not g["ok"]]
    print(f"verify: verdict={rebuilt['verdict']} failed_gates={failed}")
    return report_exit_code(rebuilt)


def cmd_selftest(args: argparse.Namespace) -> int:
    return selftest()


def cmd_self_check(args: argparse.Namespace) -> int:
    return self_check()


# --------------------------------------------------------------------------- #
# In-repo self-check (deterministic; no inventory execution, no socket,
# no subprocess). Validates the default locations, the committed seed schema
# and runner determinism, so the reproducibility anchor can be checked cheaply.
# --------------------------------------------------------------------------- #
_SEED_CAPABILITY_ID_RE = re.compile(r"^[a-z][a-z0-9]*(\.[a-z0-9_]+)+$")
_SEED_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SEED_HASH_KEYS = (
    "upstream_hash", "fixture_hash", "upstream_result_hash",
    "qubit_result_hash", "raw_hash",
)


def _is_repo_path(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO_ROOT)
        return True
    except (OSError, ValueError):
        return False


def _seed_row_problems(raw: Any) -> list[str]:
    """Schema-shape problems for one seed inventory row (stdlib only)."""
    if not isinstance(raw, Mapping):
        return ["<row-not-an-object>"]
    problems: list[str] = []
    capability_id = raw.get("capability_id")
    if not isinstance(capability_id, str) or not _SEED_CAPABILITY_ID_RE.match(capability_id):
        problems.append(f"capability_id={capability_id!r}")
    if raw.get("family") not in FAMILIES:
        problems.append(f"family={raw.get('family')!r}")
    if raw.get("upstream_project") not in {"qubit", "hermes", "openclaw"}:
        problems.append(f"upstream_project={raw.get('upstream_project')!r}")
    if raw.get("disposition") not in DISPOSITIONS:
        problems.append(f"disposition={raw.get('disposition')!r}")
    for key in ("test_ids", "trace_refs"):
        if not isinstance(raw.get(key), list):
            problems.append(f"{key}-not-a-list")
    normalizations = raw.get("normalizations")
    if not isinstance(normalizations, list) or any(
        name not in ALLOWED_NORMALIZERS for name in normalizations
    ):
        problems.append(f"normalizations={normalizations!r}")
    for key in _SEED_HASH_KEYS:
        value = raw.get(key)
        if value is not None and not (
            isinstance(value, str) and _SEED_SHA256_RE.match(value)
        ):
            problems.append(f"{key}={value!r}")
    return problems


def _seed_inventory_problems(doc: Any) -> list[str]:
    if not isinstance(doc, Mapping):
        return ["<seed-inventory-not-an-object>"]
    problems: list[str] = []
    if doc.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version={doc.get('schema_version')!r}")
    rows = doc.get("rows")
    if not isinstance(rows, list) or not rows:
        problems.append("<rows-missing>")
        return problems
    seen: set[Any] = set()
    for index, raw in enumerate(rows):
        for problem in _seed_row_problems(raw):
            problems.append(f"rows[{index}].{problem}")
        if isinstance(raw, Mapping):
            capability_id = raw.get("capability_id")
            if capability_id in seen:
                problems.append(f"rows[{index}].duplicate-capability_id")
            seen.add(capability_id)
            if str(raw.get("disposition")) != "OPEN":
                problems.append(
                    f"rows[{index}].not-open={raw.get('disposition')!r}")
    return problems


def _seed_declaration_problems(doc: Any) -> list[str]:
    if not isinstance(doc, Mapping):
        return ["<seed-declarations-not-an-object>"]
    problems: list[str] = []
    if doc.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version={doc.get('schema_version')!r}")
    entries = doc.get("declarations")
    if not isinstance(entries, list) or not entries:
        problems.append("<declarations-missing>")
        return problems
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            problems.append(f"declarations[{index}]-not-an-object")
            continue
        capability_id = entry.get("capability_id")
        if not isinstance(capability_id, str) or not _SEED_CAPABILITY_ID_RE.match(capability_id):
            problems.append(f"declarations[{index}].capability_id={capability_id!r}")
        disposition = str(entry.get("disposition") or "").upper()
        if disposition not in DECLARED_DISPOSITIONS:
            problems.append(f"declarations[{index}].disposition={disposition!r}")
        if disposition == "OPERATOR_ONLY" and not entry.get("operator_prerequisite"):
            problems.append(f"declarations[{index}].operator_prerequisite-missing")
    return problems


def _read_json_mapping(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def self_check() -> int:
    """Validate in-repo defaults, seed schema and determinism; execute nothing."""
    chk = _Checker()
    fixtures_dir = REPO_ROOT / DEFAULT_FIXTURES_DIR

    # --- default locations are in-repo (and the /tmp support is preserved) -- #
    chk.check(
        "default_inventory_dir_is_in_repo",
        _is_repo_path(DEFAULT_INVENTORY_DIR)
        and DEFAULT_INVENTORY_DIR == (
            REPO_ROOT / "tests" / "fixtures" / "differential" / "inventory"),
        f"got {DEFAULT_INVENTORY_DIR}",
    )
    chk.check(
        "default_seed_inventory_is_in_repo",
        _is_repo_path(DEFAULT_SEED_INVENTORY)
        and DEFAULT_SEED_INVENTORY == (
            REPO_ROOT / "tests" / "fixtures" / "differential" / "SEED_INVENTORY.json"),
        f"got {DEFAULT_SEED_INVENTORY}",
    )
    chk.check(
        "default_seed_declarations_is_in_repo",
        _is_repo_path(DEFAULT_SEED_DECLARATIONS)
        and DEFAULT_SEED_DECLARATIONS == (
            REPO_ROOT / "tests" / "fixtures" / "differential" / "SEED_DECLARATIONS.json"),
        f"got {DEFAULT_SEED_DECLARATIONS}",
    )
    chk.check(
        "default_fixtures_dir_is_in_repo",
        _is_repo_path(fixtures_dir) and fixtures_dir.is_dir(),
        f"got {fixtures_dir}",
    )
    chk.check(
        "default_outputs_are_in_repo",
        _is_repo_path(DEFAULT_REPORTS_DIR)
        and _is_repo_path(DEFAULT_INVENTORY_OUT)
        and _is_repo_path(DEFAULT_REPORT_OUT)
        and DEFAULT_INVENTORY_OUT.parent == DEFAULT_REPORTS_DIR
        and DEFAULT_REPORT_OUT.parent == DEFAULT_REPORTS_DIR,
        f"reports={DEFAULT_REPORTS_DIR}",
    )
    chk.check(
        "default_byte_manifest_is_in_repo",
        _is_repo_path(Path(DEFAULT_HERMES_BYTE_MANIFEST))
        and Path(DEFAULT_HERMES_BYTE_MANIFEST).name == HERMES_BYTE_MANIFEST_NAME,
        f"got {DEFAULT_HERMES_BYTE_MANIFEST}",
    )
    chk.check(
        "legacy_tmp_support_preserved",
        str(LEGACY_INVENTORY_DIR)
        == "/tmp/qubit-differential/inventory"
        and str(LEGACY_HERMES_BYTE_MANIFEST)
        == "/tmp/qubit-differential/byte_manifest/hermes/HERMES_BYTE_MANIFEST.json",
    )
    chk.check(
        "input_schema_files_present",
        (fixtures_dir / "schema" / "row.schema.json").is_file()
        and (fixtures_dir / "schema" / "fixture.schema.json").is_file(),
    )

    # --- committed seed pair is present and schema-shaped ------------------- #
    seed_inventory_doc = _read_json_mapping(DEFAULT_SEED_INVENTORY)
    seed_declarations_doc = _read_json_mapping(DEFAULT_SEED_DECLARATIONS)
    chk.check(
        "seed_inventory_readable",
        isinstance(seed_inventory_doc, Mapping),
        f"path={DEFAULT_SEED_INVENTORY}",
    )
    chk.check(
        "seed_inventory_schema_valid",
        _seed_inventory_problems(seed_inventory_doc) == [],
        f"problems={_seed_inventory_problems(seed_inventory_doc)}",
    )
    chk.check(
        "seed_declarations_readable",
        isinstance(seed_declarations_doc, Mapping),
        f"path={DEFAULT_SEED_DECLARATIONS}",
    )
    chk.check(
        "seed_declarations_schema_valid",
        _seed_declaration_problems(seed_declarations_doc) == [],
        f"problems={_seed_declaration_problems(seed_declarations_doc)}",
    )

    # --- the runner ingests the seed deterministically, all rows OPEN ------- #
    rows_a = load_inventory(DEFAULT_SEED_INVENTORY)
    rows_b = load_inventory(DEFAULT_SEED_INVENTORY)
    chk.check(
        "seed_inventory_loads_open_rows",
        bool(rows_a)
        and all(str(row.get("disposition")) == "OPEN" for row in rows_a)
        and not any(
            str(row.get("disposition")) in ("PASS", "SAFE_EQUIVALENT")
            for row in rows_a
        ),
        f"rows={[(r.get('capability_id'), r.get('disposition')) for r in rows_a]}",
    )
    chk.check(
        "seed_inventory_load_deterministic",
        canonical_json(rows_a) == canonical_json(rows_b),
    )
    chk.check(
        "seed_inventory_carries_row_schema_keys",
        all(
            all(
                key in row
                for key in (
                    "capability_id", "family", "upstream_project", "disposition",
                    "test_ids", "trace_refs", "normalizations",
                )
            )
            for row in rows_a
        ),
    )
    chk.check(
        "seed_inventory_contains_no_secret",
        not scan_secrets(rows_a),
    )
    declarations = {
        str(entry["capability_id"]): dict(entry)
        for entry in (seed_declarations_doc or {}).get("declarations") or []
        if isinstance(entry, Mapping)
        and str(entry.get("disposition") or "").upper() in DECLARED_DISPOSITIONS
        and entry.get("capability_id")
    }
    applied = apply_declarations(rows_a, declarations)
    chk.check(
        "seed_declarations_cannot_manufacture_success",
        bool(declarations)
        and not any(
            str(row.get("disposition")) in ("PASS", "SAFE_EQUIVALENT")
            for row in applied
        ),
        f"applied={[(r.get('capability_id'), r.get('disposition')) for r in applied]}",
    )

    # --- hash / serialization determinism ----------------------------------- #
    chk.check(
        "canonical_json_is_key_order_independent",
        canonical_json({"b": 1, "a": [2, 3]}) == canonical_json({"a": [2, 3], "b": 1}),
    )
    sample = {"b": 1, "a": [2, 3], "u": "\u00e9"}
    chk.check(
        "result_hash_is_deterministic",
        result_hash(sample) == result_hash(json.loads(canonical_json(sample))),
    )
    chk.check(
        "byte_manifest_default_is_deterministic",
        _byte_manifest_path() == _byte_manifest_path(),
    )

    # --- explicit /tmp override is still accepted (read-free path check) ---- #
    previous = os.environ.get("QUBIT_DIFFERENTIAL_BYTE_MANIFEST")
    try:
        os.environ["QUBIT_DIFFERENTIAL_BYTE_MANIFEST"] = LEGACY_HERMES_BYTE_MANIFEST
        chk.check(
            "byte_manifest_explicit_tmp_override_accepted",
            _byte_manifest_path() == Path(LEGACY_HERMES_BYTE_MANIFEST),
            f"got {_byte_manifest_path()}",
        )
    finally:
        if previous is None:
            os.environ.pop("QUBIT_DIFFERENTIAL_BYTE_MANIFEST", None)
        else:
            os.environ["QUBIT_DIFFERENTIAL_BYTE_MANIFEST"] = previous

    print(f"\nself-check: {chk.checks - chk.failures}/{chk.checks} checks passed")
    return EXIT_OK if chk.failures == 0 else EXIT_FAIL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="differential_parity",
        description="Deterministic differential parity runner (fail-closed).",
    )
    parser.add_argument(
        "--version", action="version", version=f"{GENERATED_BY} {RUNNER_VERSION} (schema {SCHEMA_VERSION})"
    )
    sub = parser.add_subparsers(dest="command")

    p_inv = sub.add_parser("inventory", help="merge inventory JSONs into the Row schema")
    p_inv.add_argument(
        "--out",
        default=str(DEFAULT_INVENTORY_OUT),
        help="output JSON path (default %s)" % DEFAULT_INVENTORY_OUT,
    )
    p_inv.add_argument(
        "--source",
        default=None,
        help="inventory file or directory (default seed %s)" % DEFAULT_SEED_INVENTORY,
    )
    p_inv.set_defaults(func=cmd_inventory)

    p_run = sub.add_parser("run", help="execute fixtures and compare")
    p_run.add_argument(
        "--inventory",
        default=str(DEFAULT_SEED_INVENTORY),
        help="inventory file (default in-repo seed %s)" % DEFAULT_SEED_INVENTORY,
    )
    p_run.add_argument("--fixtures-dir", default=DEFAULT_FIXTURES_DIR)
    p_run.add_argument(
        "--out",
        default=str(DEFAULT_REPORT_OUT),
        help="report JSON path (default %s)" % DEFAULT_REPORT_OUT,
    )
    p_run.add_argument("--family", default=None)
    p_run.add_argument("--row", default=None)
    p_run.add_argument(
        "--references-only",
        dest="references_only",
        action="store_true",
        help="diagnostic: execute declared references without the Qubit side; "
             "never produces PASS (bounded evidence run)",
    )
    p_run.add_argument(
        "--reference-limit",
        dest="reference_limit",
        type=int,
        default=None,
        help="cap the number of upstream reference invocations in "
             "references-only mode (default %d)" % DEFAULT_REFERENCES_ONLY_LIMIT,
    )
    p_run.add_argument(
        "--allow-state-changing",
        dest="allow_state_changing",
        action="store_true",
        help="explicitly authorize execution of state-changing fixtures "
             "(any non-GET/HEAD HTTP method, mutating console/CLI verbs such "
             "as /api/power / emergency-stop / pause / power / reset / create "
             "/ update / delete, or state_changing:true). Default off: such "
             "rows are OPERATOR_ONLY and no socket is opened.",
    )
    p_run.set_defaults(func=cmd_run)

    p_verify = sub.add_parser("verify", help="re-check a report's gates")
    p_verify.add_argument("--report", required=True)
    p_verify.set_defaults(func=cmd_verify)

    p_self = sub.add_parser("selftest", help="run internal self-checks")
    p_self.set_defaults(func=cmd_selftest)

    p_self_check = sub.add_parser(
        "self-check",
        help="validate in-repo defaults, the committed seed schema and "
             "determinism without executing the inventory (no socket, "
             "no subprocess)",
    )
    p_self_check.set_defaults(func=cmd_self_check)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] in ("--version", "-V"):
        print(f"{GENERATED_BY} {RUNNER_VERSION} (schema {SCHEMA_VERSION})")
        return EXIT_OK
    if raw in (["--self-check"], ["self-check"]):
        return cmd_self_check(argparse.Namespace())
    parser = build_parser()
    try:
        args = parser.parse_args(raw)
    except SystemExit as exc:
        return int(exc.code or EXIT_USAGE)
    if not getattr(args, "func", None):
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    try:
        return int(args.func(args))
    except NormalizerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL
    except Exception as exc:  # noqa: BLE001 - last-resort fail-closed boundary
        print(f"internal error: {redact_text(str(exc))}", file=sys.stderr)
        return EXIT_INTERNAL


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
class _Checker:
    def __init__(self) -> None:
        self.checks = 0
        self.failures = 0

    def check(self, name: str, condition: Any, detail: str = "") -> bool:
        self.checks += 1
        ok = bool(condition)
        if not ok:
            self.failures += 1
        suffix = f": {detail}" if (not ok and detail) else ""
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{suffix}")
        return ok


def _baseline_gate_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    row = merge_row(
        {
            "capability_id": "cli.status.list",
            "family": "cli",
            "upstream_project": "qubit",
            "qubit_path": "voicebridge/cli.py:1",
            "qubit_symbol": "main",
            "disposition": "PASS",
            "fixture": "cli/status.json",
        }
    )
    result = _new_result(row)
    result["executed"] = True
    result["fixture_present"] = True
    result["disposition"] = "PASS"
    result["production_caller"] = "main"
    result["reference_executed"] = True
    result["qubit_result_hash"] = result_hash({"status": "ok"})
    result["upstream_result_hash"] = result_hash({"status": "ok"})
    result["unpinned_references"] = []
    return row, result


def selftest() -> int:
    """Exercise allowlist exactness, hash determinism, path precision, gates."""
    chk = _Checker()

    expected_names = {
        "timestamp", "uuid", "temp_path", "pid", "session_token",
        "generated_hash", "product_branding",
    }
    chk.check(
        "normalizer_allowlist_is_exact",
        set(ALLOWED_NORMALIZERS) == expected_names,
        f"got {sorted(ALLOWED_NORMALIZERS)}",
    )
    try:
        normalize({"x": 1}, ["bogus"])
        chk.check("unknown_normalizer_raises", False, "no error raised")
    except NormalizerError:
        chk.check("unknown_normalizer_raises", True)

    samples: dict[str, tuple[Any, Any, Any, Any]] = {
        "timestamp": ("2024-01-02T03:04:05Z", "name", "<timestamp>", "hello"),
        "uuid": ("12345678-1234-1234-1234-123456789abc", "id", "<uuid>", "hello"),
        "temp_path": ("/tmp/foo/bar.txt", "path", "<temp_path>", "relative/path"),
        "pid": (1234, "pid", "<pid>", "not-a-pid"),
        "session_token": ("abcdef123456", "token", "<session_token>", "short"),
        "generated_hash": ("a" * 64, "hash", "<generated_hash>", "xyz"),
        "product_branding": ("Hermes Agent", "name", "<product> Agent", "plain"),
    }
    for name, (value, key, want, other) in samples.items():
        fn = ALLOWED_NORMALIZERS[name]
        chk.check(f"normalizer_{name}_transforms", fn(value, key) == want, f"got {fn(value, key)!r}")
        chk.check(f"normalizer_{name}_narrow", fn(other, "unrelated") == other)

    normalized = normalize({"created_at": "2024-01-02T03:04:05Z", "name": "hermes"},
                           ["timestamp", "product_branding"])
    chk.check(
        "normalize_walks_nested",
        normalized == {"created_at": "<timestamp>", "name": "<product>"},
        f"got {normalized}",
    )

    payload = {"b": 1, "a": [1, 2], "u": "\u00e9"}
    direct = "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    chk.check("result_hash_matches_contract", result_hash(payload) == direct, result_hash(payload))
    chk.check(
        "result_hash_key_order_independent",
        result_hash({"b": 1, "a": 2}) == result_hash({"a": 2, "b": 1}),
    )

    diags = compare_expected_actual(
        {"a": {"b": [1, {"c": 2}]}}, {"a": {"b": [1, {"c": 3}]}}
    )
    chk.check(
        "diagnostic_path_precision",
        len(diags) == 1 and diags[0].path == "$.a.b[1].c" and diags[0].kind == "value",
        f"got {[(d.path, d.kind) for d in diags]}",
    )
    selector_diags = compare_expected_actual({"#submit": {"x": 1}}, {"#submit": {"x": 2}})
    chk.check(
        "diagnostic_selector_path",
        len(selector_diags) == 1 and selector_diags[0].path == '$["#submit"].x',
        f"got {[d.path for d in selector_diags]}",
    )
    schema_diags = compare_expected_actual(
        {"json_schema": {"id": 1}}, {"json": {"id": 2, "extra": 1}}
    )
    chk.check(
        "schema_template_ignores_extras",
        [d.path for d in schema_diags] == ["$.json_schema.id"],
        f"got {[d.path for d in schema_diags]}",
    )
    chk.check(
        "header_nondeterminism_ignored",
        compare_expected_actual(
            {"headers": {"Date": "x", "Content-Type": "application/json"}},
            {"headers": {"date": "y", "content-type": "application/json"}},
        ) == [],
    )
    chk.check(
        "scope_sets_compare",
        compare_expected_actual({"scopes": ["a", "b"]}, {"scopes": ["b", "a"]}) == [],
    )
    chk.check(
        "envelope_subset_ignores_transport_fields",
        compare_expected_actual(
            {"exit_code": 0},
            {"exit_code": 0, "stdout": "usage", "stderr": "", "duration": 0.1},
        ) == [],
    )
    chk.check(
        "closed_object_flags_extras",
        [d.path for d in compare_expected_actual(
            {"closed": True, "exit_code": 0},
            {"exit_code": 0, "stdout": "usage"},
        )] == ["$.stdout"],
    )
    symmetric = compare_expected_actual(
        {"created_at": "2024-01-02T03:04:05Z"},
        {"created_at": "2025-06-07T08:09:10Z"},
        normalizers=["timestamp"],
    )
    chk.check("normalizers_applied_symmetrically", symmetric == [], f"got {symmetric}")
    chk.check(
        "asymmetric_normalizer_flagged",
        asymmetric_normalizers(
            {"created_at": "2024-01-02T03:04:05Z"},
            {"created_at": "not-a-timestamp"},
            ["timestamp"],
        ) == ["$.created_at"],
    )

    chk.check(
        "response_headers_ignore_nondeterministic",
        compare_response_headers(
            {"Date": "Mon", "Server": "uvicorn", "Content-Type": "application/json"},
            {"date": "Tue", "server": "hypercorn", "content-type": "application/json"},
        ) == [],
    )
    header_diags = compare_response_headers(
        {"Content-Type": "application/json"}, {"content-type": "text/plain"}
    )
    chk.check(
        "response_headers_flag_deterministic",
        len(header_diags) == 1 and "content-type" in header_diags[0].path,
        f"got {[d.path for d in header_diags]}",
    )
    chk.check(
        "help_text_requires_canonical_command",
        check_help_text(f"usage: {CANONICAL_QUBIT_COMMAND} [OPTIONS]") == []
        and check_help_text("usage: unrelated-tool [OPTIONS]") != [],
    )
    chk.check(
        "openclaw_delta_detection",
        is_openclaw_delta_row({"upstream_project": "openclaw", "upstream_commit": "main"})
        and not is_openclaw_delta_row(
            {"upstream_project": "openclaw", "upstream_commit": "a" * 40}
        )
        and not is_openclaw_delta_row(
            {"upstream_project": "hermes", "upstream_commit": "main"}
        ),
    )
    evidence_probe = _new_result(_baseline_gate_inputs()[0])
    chk.check(
        "result_has_all_evidence_keys",
        missing_evidence_keys(evidence_probe) == [],
        f"missing {missing_evidence_keys(evidence_probe)}",
    )
    evidence_pass = dict(evidence_probe)
    evidence_pass["disposition"] = "PASS"
    for field in EVIDENCE_FIELDS:
        evidence_pass[field] = [] if field == "error_codes" else f"<{field}>"
    chk.check(
        "success_evidence_complete_ok",
        missing_evidence_fields(evidence_pass) == [],
    )
    evidence_missing = dict(evidence_pass)
    evidence_missing["response_headers"] = None
    chk.check(
        "success_missing_evidence_flagged",
        missing_evidence_fields(evidence_missing) == ["response_headers"],
        f"got {missing_evidence_fields(evidence_missing)}",
    )
    evidence_open = dict(evidence_missing)
    evidence_open["disposition"] = "OPEN"
    chk.check(
        "open_row_evidence_exempt",
        missing_evidence_fields(evidence_open) == [],
    )

    audit_obj = {"id": "alice", "ts": "2024-01-02T03:04:05Z"}
    _, audit = normalize_and_audit(audit_obj, ["timestamp"])
    chk.check("safe_normalizer_no_unsafe", detect_unsafe_normalizers(audit) == [])
    evil_registry = dict(ALLOWED_NORMALIZERS)
    evil_registry["timestamp"] = lambda value, key=None: "<collapsed>"
    _, evil_audit = normalize_and_audit({"id": "alice"}, ["timestamp"], registry=evil_registry)
    unsafe = detect_unsafe_normalizers(evil_audit)
    chk.check(
        "unsafe_normalizer_detected",
        len(unsafe) == 1 and unsafe[0]["path"] == "$.id",
        f"got {[u.get('path') for u in unsafe]}",
    )

    # Build the fake key by concatenation so this test harness itself does not
    # contain a secret-shaped literal (which would fail the release secret
    # scan for a file that only *demonstrates* redaction).
    _fake_aws_key = "AKIA" + "ABCDEFGHIJKLMNOP"
    secrets = scan_secrets({"api_key": _fake_aws_key})
    chk.check("secret_scan_finds", secrets == ["$.api_key"], f"got {secrets}")
    chk.check(
        "secret_redacted_in_detail",
        _fake_aws_key not in redact_text("key=" + _fake_aws_key),
    )

    row, result = _baseline_gate_inputs()
    clean_gates = apply_fail_closed_gates([row], [result])
    chk.check(
        "gate_names_exact",
        [g["gate"] for g in clean_gates] == list(GATE_NAMES),
        f"got {[g['gate'] for g in clean_gates]}",
    )
    chk.check(
        "clean_row_all_gates_ok",
        all(g["ok"] for g in clean_gates),
        f"failing: {[g['gate'] for g in clean_gates if not g['ok']]}",
    )

    # --- state-changing guard --------------------------------------------- #
    estop_fixture = {
        "fixture_id": "recovery.estop.selftest",
        "family": "recovery",
        "command": None,
        "request": {"method": "POST", "path": "/api/power", "headers": {},
                    "body": {"action": "emergency-stop", "confirmed": True}},
        "transport": "http",
        "normalizers": [],
        "expected": {"http_status": 200},
        "disposition": "OPERATOR_ONLY",
        "operator_prerequisite": "declared state-changing prerequisite",
        "qubit": {"path": "voicebridge/power.py:1", "caller": "power.emergency-stop"},
        "hermes": {"path": "agent/estop.py:1", "symbol": "check_paused"},
        "openclaw": {"path": "src/gateway/x.ts:1", "symbol": "suspendHandlers"},
        "notes": "selftest state-changing guard probe",
    }
    estop_reasons = state_changing_reasons(estop_fixture)
    chk.check(
        "state_changing_detects_estop",
        any("/api/power" in r for r in estop_reasons)
        and any("emergency-stop" in r for r in estop_reasons),
        f"got {estop_reasons}",
    )
    chk.check(
        "state_changing_benign_empty",
        state_changing_reasons(
            {"request": {"method": "GET", "path": "/agent/api/status", "body": None}}
        ) == [],
    )
    chk.check(
        "read_only_never_permits_mutation",
        bool(state_changing_reasons({
            "read_only": True,
            "request": {"kind": "http", "method": "DELETE",
                        "path": "/api/sessions/abc", "body": None},
        })) and bool(state_changing_reasons({
            "read_only": True,
            "request": {"kind": "http", "method": "PUT",
                        "path": "/api/sessions/abc", "body": None},
        })),
    )
    chk.check(
        "read_only_permits_rpc_read_post",
        state_changing_reasons({
            "read_only": True,
            "request": {"kind": "rpc", "method": "POST", "path": "/rpc",
                        "body": {"method": "ui.panels"}},
        }) == [],
    )
    derived = synthesize_row_from_fixture(
        "recovery.estop.selftest", estop_fixture, "/tmp/estop_fixture.json")
    chk.check(
        "synthesize_honors_disposition_and_prerequisite",
        derived["disposition"] == "OPERATOR_ONLY"
        and derived["operator_prerequisite"] == "declared state-changing prerequisite",
        f"got disposition={derived['disposition']!r} "
        f"prereq={derived['operator_prerequisite']!r}",
    )

    class _CountingAdapter:
        name = "qubit"
        project = "qubit"
        is_mock = False

        def __init__(self) -> None:
            self.calls = 0

        def available(self) -> bool:
            return True

        def execute(self, request: Any) -> "CallResult":
            self.calls += 1
            return CallResult(exit_code=0, stdout="ok")

    with tempfile.TemporaryDirectory() as tmpdir:
        estop_path = Path(tmpdir) / "latch_resume.json"
        estop_path.write_text(json.dumps(estop_fixture), encoding="utf-8")
        estop_row = synthesize_row_from_fixture(
            "recovery.estop.selftest", estop_fixture, estop_path)
        blocked_adapter = _CountingAdapter()
        blocked = run_inventory(
            [estop_row], tmpdir, adapters={"qubit": blocked_adapter})[0]
        chk.check(
            "state_changing_blocks_by_default",
            blocked_adapter.calls == 0
            and blocked["state_changing_skipped"] is True
            and blocked["disposition"] == "OPERATOR_ONLY"
            and STATE_CHANGING_FLAG in (blocked.get("operator_prerequisite") or ""),
            f"calls={blocked_adapter.calls} disposition={blocked['disposition']!r} "
            f"prereq={blocked.get('operator_prerequisite')!r}",
        )
        allowed_adapter = _CountingAdapter()
        run_inventory(
            [estop_row], tmpdir, adapters={"qubit": allowed_adapter},
            allow_state_changing=True)
        chk.check(
            "state_changing_flag_allows_execution",
            allowed_adapter.calls == 1,
            f"calls={allowed_adapter.calls}",
        )

    # --- wiring-proof resolver hardening ----------------------------------- #
    # A generic symbol collision or an English word in prose must never dispose
    # a Qubit capability. ``gap_ledger.main`` in an unrelated test does not
    # prove ``voicebridge.terminal:main``; the resolver must leave the row OPEN
    # with reason ``no_linkage`` rather than fabricate a SAFE_EQUIVALENT proof.
    chk.check(
        "resolver_module_dotted",
        _qubit_module_dotted("voicebridge/terminal.py:382") == "voicebridge.terminal"
        and _qubit_module_dotted("voicebridge/verified_skills/workshop.py:74")
        == "voicebridge.verified_skills.workshop"
        and _qubit_module_dotted("not_a_file:1") == "",
        f"got {_qubit_module_dotted('voicebridge/terminal.py:382')!r}",
    )
    chk.check(
        "resolver_rejects_generic_name_collision",
        _ast_wiring_test_ids(
            {"qubit_path": "voicebridge/terminal.py:382", "qubit_symbol": "main"}
        ) == [],
        "gap_ledger.main was wrongly accepted as proof for terminal.main",
    )
    chk.check(
        "resolver_keeps_real_linkage",
        bool(_ast_wiring_test_ids(
            {"qubit_path": "voicebridge/terminal.py:374",
             "qubit_symbol": "build_parser"}
        )),
        "a genuinely imported terminal.build_parser reference was rejected",
    )
    unlinked_row: dict[str, Any] = {
        "capability_id": "selftest.resolver.unlinked",
        "interface_disposition": "SAFE_EQUIVALENT",
        "qubit_path": "voicebridge/terminal.py:382",
        "qubit_symbol": "main",
        "test_ids": [],
        "trace_refs": [],
    }
    _resolve_wiring_proof_for_row(unlinked_row)
    chk.check(
        "resolver_unlinked_row_stays_open",
        unlinked_row.get("disposition") == "OPEN"
        and unlinked_row.get("proof_level") is None
        and unlinked_row.get("wiring_proof_status") == "no_linkage"
        and not unlinked_row.get("test_ids"),
        f"got disposition={unlinked_row.get('disposition')!r} "
        f"status={unlinked_row.get('wiring_proof_status')!r} "
        f"test_ids={unlinked_row.get('test_ids')!r}",
    )

    # --- frontend linkage proof type (dashboard Control-UI) ------------------ #
    # A real vitest suite that imports the mapped component and references the
    # mapped symbol disposes the row; an unmapped path or an absent symbol must
    # stay OPEN (never fabricated). Reads only in-repo files (hermetic).
    linked = _frontend_linkage({
        "qubit_path": "frontend/qubit-web/src/components/OAuthLoginModal.tsx:1",
        "qubit_symbol": "OAuthLoginModal",
    })
    chk.check(
        "frontend_linkage_resolves_real_test",
        isinstance(linked, Mapping)
        and str(linked.get("test", "")).endswith("OAuthLoginModal.test.tsx")
        and linked.get("proof_type") == FRONTEND_LINKAGE,
        f"got {linked!r}",
    )
    chk.check(
        "frontend_linkage_absent_symbol_stays_open",
        _frontend_linkage({
            "qubit_path": "frontend/qubit-web/src/lib/events-reconnect.ts:1",
            "qubit_symbol": "eventsReconnect",
        }) is None
        and _frontend_linkage({
            "qubit_path": "voicebridge/webui.py:1",
            "qubit_symbol": "main",
        }) is None,
        "an absent frontend symbol or a non-frontend path was linked",
    )
    frontend_row: dict[str, Any] = {
        "capability_id": "selftest.frontend.linked",
        "interface_disposition": "SAFE_EQUIVALENT",
        "qubit_path": "frontend/qubit-web/src/components/OAuthLoginModal.tsx:1",
        "qubit_symbol": "OAuthLoginModal",
        "test_ids": [],
        "trace_refs": [],
    }
    _resolve_wiring_proof_for_row(frontend_row)
    chk.check(
        "frontend_linkage_disposes_row",
        frontend_row.get("disposition") == "SAFE_EQUIVALENT"
        and frontend_row.get("proof_level") == "wiring"
        and frontend_row.get("wiring_proof_status") == FRONTEND_LINKAGE
        and str(frontend_row.get("frontend_test_ref", "")).endswith(
            "OAuthLoginModal.test.tsx"),
        f"got disposition={frontend_row.get('disposition')!r} "
        f"level={frontend_row.get('proof_level')!r} "
        f"status={frontend_row.get('wiring_proof_status')!r}",
    )

    # --- served plugin-bundle linkage proof type (dashboard/plugin) ---------- #
    # A compiled plugin page has no ``*.tsx``; a real Python test that READS the
    # served bundle and references the control disposes the row. An absent
    # bundle, an absent symbol or no reading test stays OPEN (never fabricated).
    bundle_linked = _plugin_bundle_linkage({
        "qubit_path": (
            "frontend/qubit-web/public/plugins/qubit-achievements"
            "/dist/index.js:1"),
        "qubit_symbol": "card.share_title",
    })
    chk.check(
        "plugin_bundle_linkage_resolves_real_test",
        isinstance(bundle_linked, Mapping)
        and str(bundle_linked.get("test", "")).endswith(
            "test_served_bundle_wires_the_real_workflow_controls")
        and bundle_linked.get("proof_type") == PLUGIN_BUNDLE_LINKAGE
        and str(bundle_linked.get("bundle_hash", "")).startswith("sha256:"),
        f"got {bundle_linked!r}",
    )
    chk.check(
        "plugin_bundle_linkage_never_fabricates",
        _plugin_bundle_linkage({
            "qubit_path": "frontend/qubit-web/src/App.tsx:1",
            "qubit_symbol": "App",
        }) is None
        and _plugin_bundle_linkage({
            "qubit_path": (
                "frontend/qubit-web/public/plugins/qubit-achievements"
                "/dist/index.js:1"),
            "qubit_symbol": "no.such.control.label",
        }) is None
        and _plugin_bundle_linkage({
            "qubit_path": (
                "frontend/qubit-web/public/plugins/nope/dist/index.js:1"),
            "qubit_symbol": "card.share_title",
        }) is None,
        "an absent bundle, non-bundle path or absent symbol was linked",
    )
    bundle_row: dict[str, Any] = {
        "capability_id": "selftest.plugin_bundle.linked",
        "interface_disposition": "SAFE_EQUIVALENT",
        "qubit_path": (
            "frontend/qubit-web/public/plugins/qubit-achievements"
            "/dist/index.js:1"),
        "qubit_symbol": "card.share_title",
        "test_ids": [],
        "trace_refs": [],
    }
    _resolve_wiring_proof_for_row(bundle_row)
    chk.check(
        "plugin_bundle_linkage_disposes_row",
        bundle_row.get("disposition") == "SAFE_EQUIVALENT"
        and bundle_row.get("proof_level") == "wiring"
        and bundle_row.get("proof_kind") == PLUGIN_BUNDLE_LINKAGE
        and bundle_row.get("wiring_proof_status") == PLUGIN_BUNDLE_LINKAGE
        and str(bundle_row.get("plugin_bundle_hash", "")).startswith("sha256:"),
        f"got disposition={bundle_row.get('disposition')!r} "
        f"proof_kind={bundle_row.get('proof_kind')!r} "
        f"hash={bundle_row.get('plugin_bundle_hash')!r}",
    )

    # One proof gate only (the duplicate ``safe_equivalent_proof_level`` was
    # removed); the row proof level is validated by ``proof_level`` and the
    # resolvability detail by ``proof_level_requires_resolvable_test_id``.
    chk.check(
        "gate_proof_level_single",
        "proof_level" in GATE_NAMES
        and "safe_equivalent_proof_level" not in GATE_NAMES
        and len(GATE_NAMES) == len(set(GATE_NAMES)),
        f"GATE_NAMES={GATE_NAMES}",
    )

    # Route/symbol-level linkage: a class-level surface test is not proof for a
    # route it does not carry; membership in the test's iterated route table is.
    gated_test = (
        "tests/test_qubit_readsurfaces8u.py"
        "::test_gated_routes_resolve_to_typed_gates"
    )
    chk.check(
        "linkage_rejects_unexercised_route",
        not _test_references_row(
            gated_test,
            {"command": "GET /api/credentials/pool/{provider}/{index}"},
        ),
        "a class-level gated-routes test was accepted as proof for an "
        "unrelated route",
    )
    chk.check(
        "linkage_accepts_iterated_route",
        _test_references_row(gated_test, {"command": "GET /api/git/gh-auth"}),
        "a route present in the test's iterated GATED table was rejected",
    )
    chk.check(
        "linkage_accepts_symbol_call",
        _test_references_row(
            "tests/test_computer_use8tf.py::test_status_shape_and_policy",
            {"qubit_path": "voicebridge/computer_use.py:83",
             "qubit_symbol": "status"},
        ),
        "a test that imports the mapped module and calls the row symbol was "
        "rejected as linkage",
    )
    chk.check(
        "linked_references_filter_unlinked",
        _linked_test_references(
            {
                "command": "GET /scan-status",
                "test_ids": [
                    "tests/test_agentD_parity.py"
                    "::test_achievements_full_real_catalog"
                ],
            }
        )
        == [],
        "an unexercised route citation was counted as a linked reference",
    )
    # A gate row's proof must EXERCISE the gate: a route string that merely
    # appears in an error-suppression allowlist (or a coincidental short route
    # segment) is not gate proof; a test that names the typed denial code is.
    _gate_row_probe = {
        "command": "GET /api/credentials/pool",
        "qubit_symbol": "__gate__",
        "error_codes": "SECRET_NEVER_SERVED",
    }
    chk.check(
        "linkage_gate_row_rejects_allowlist_only_route",
        not _test_references_row(
            "tests/test_dashboard_families8u.py"
            "::test_family_routes_render_qubit_shell",
            _gate_row_probe,
        ),
        "a route string in an error-suppression allowlist counted as gate proof",
    )
    chk.check(
        "linkage_gate_row_accepts_typed_denial_code",
        _test_references_row(
            "tests/test_qubit_routes_parity8u.py"
            "::test_every_gated_route_is_typed",
            _gate_row_probe,
        ),
        "a test that names the gate's typed denial code was rejected",
    )

    # --- byte-parity gate -------------------------------------------------- #
    chk.check(
        "byte_parity_manifest_is_pinned_hermes",
        _byte_manifest_path().name == "HERMES_BYTE_MANIFEST.json",
        f"got {_byte_manifest_path()}",
    )
    byte_offenders = evaluate_byte_manifest({
        "artifact": "HERMES_BYTE_MANIFEST",
        "contract": {"pinned_commit": CONTRACTUAL_PINS["hermes"]},
        "manifest": [
            {"path": "src/a.py", "classification": "IDENTICAL"},
            {"path": "__pycache__/x.pyc", "classification": "NOT_APPLICABLE"},
            {"path": "src/b.py", "classification": "MISSING"},
            {"path": "src/c.py", "classification": "EOL_NORMALIZED"},
        ],
    })
    chk.check(
        "byte_parity_flags_nonidentical",
        byte_offenders == ["src/b.py", "src/c.py"],
        f"got {byte_offenders}",
    )
    chk.check(
        "byte_parity_exempts_not_applicable",
        evaluate_byte_manifest({
            "artifact": "HERMES_BYTE_MANIFEST",
            "manifest": [
                {"path": "src/a.py", "classification": "IDENTICAL"},
                {"path": "__pycache__/x.pyc", "classification": "NOT_APPLICABLE"},
            ],
        }) == [],
    )
    byte_gate = next(g for g in clean_gates if g["gate"] == "byte_parity")
    chk.check(
        "byte_parity_gate_ok",
        byte_gate["ok"] is True,
        f"detail={byte_gate['detail']} rows={byte_gate['rows']}",
    )

    # Per-fixture reference binding: env_ref/bin_ref name the environment
    # variable that resolves the pinned runtime; an unresolved binding is
    # fail-closed (no adapter, no PASS) and never leaks the value.
    probe_name = "QUBIT_DIFFERENTIAL_SELFTEST_REFBIN_8U"
    probe_value = "/tmp/qubit-differential/refruntimes/bin/hermes-pinned"
    previous = os.environ.get(probe_name)
    try:
        os.environ[probe_name] = probe_value
        bound, binding = resolve_reference_binding(
            "hermes", {"argv": ["hermes", "--help"], "bin_ref": probe_name}, None)
        chk.check(
            "reference_binding_env_ref_resolves",
            bound is not None
            and getattr(bound, "binary", None) == probe_value
            and binding.get("binding") == "env_ref"
            and binding.get("binding_resolved") is True
            and binding.get("binary") == probe_value,
            f"got {binding}",
        )
        del os.environ[probe_name]
        missing, missing_binding = resolve_reference_binding(
            "hermes", {"argv": ["hermes", "--help"], "env_ref": probe_name}, None)
        chk.check(
            "reference_binding_unresolved_fail_closed",
            missing is None
            and missing_binding.get("binding_resolved") is False
            and bool(missing_binding.get("binding_error")),
            f"got {missing_binding}",
        )
    finally:
        if previous is None:
            os.environ.pop(probe_name, None)
        else:
            os.environ[probe_name] = previous

    provenance = collect_reference_provenance(
        [
            {"capability_id": "b", "references": {"hermes": {
                "executed": True, "binary": "/bin/hermes-pinned",
                "reference_version": "Hermes Agent v0", "reference_commit": "a" * 40,
                "reference_pinned": True}}},
            {"capability_id": "a", "references": {"hermes": {
                "executed": True, "binary": "/bin/hermes-pinned",
                "reference_version": "Hermes Agent v0", "reference_commit": "a" * 40,
                "reference_pinned": True}}},
        ]
    )
    chk.check(
        "reference_provenance_block_shape",
        provenance == {"hermes": {"bin": "/bin/hermes-pinned",
                                  "version": "Hermes Agent v0",
                                  "commit": "a" * 40, "pinned": True}},
        f"got {provenance}",
    )
    chk.check(
        "reference_provenance_no_secret",
        not contains_secret(provenance),
    )

    triggers: dict[str, dict[str, Any]] = {
        "unexecuted_row": {"executed": False, "fixture_present": False},
        "missing_production_caller": {"production_caller": None, "qubit_symbol": None},
        "skipped_fixture": {"executed": False, "fixture_present": True, "disposition": "OPEN"},
        "mock_replaced_real_path": {"adapter_mock": True, "disposition": "PASS"},
        "refusal_counted_as_success": {"refused": True, "disposition": "PASS"},
        "unsafe_normalizer": {"unsafe_normalizers": ["$.x"]},
        "visual_only_control": {"visual_only": True, "disposition": "PASS"},
        "voice_backend_mismatch": {"voice_mismatch": True, "disposition": "PASS"},
        "persistence_or_recovery_diff": {"persistence_diff": True, "disposition": "PASS"},
        "secret_in_artifact": {"secret_in_artifact": True},
        "openclaw_undisposed_addition": {
            "upstream_project": "openclaw",
            "upstream_commit": "main",
            "qubit_path": None,
            "qubit_symbol": None,
            "production_caller": None,
            "disposition": "OPEN",
            "executed": False,
        },
        "unexecuted_open_row": {"disposition": "OPEN", "executed": False},
        "pass_without_reference": {"reference_executed": False, "disposition": "PASS"},
        "pass_without_proof": {"qubit_result_hash": None},
        "unpinned_reference_pass": {
            "disposition": "PASS",
            "unpinned_references": ["hermes"],
        },
    }
    row_keys = {"upstream_project", "upstream_commit", "qubit_path", "qubit_symbol"}
    for gate, mutation in triggers.items():
        trow, tres = _baseline_gate_inputs()
        for key, value in mutation.items():
            if key in row_keys:
                trow[key] = value
            tres[key] = value
        gates = apply_fail_closed_gates([trow], [tres])
        match = next(g for g in gates if g["gate"] == gate)
        chk.check(
            f"gate_trigger_{gate}",
            (not match["ok"]) and trow["capability_id"] in match["rows"],
            f"ok={match['ok']} rows={match['rows']}",
        )

    # inventory_coverage needs an input row with no result at all.
    cov_row, _ = _baseline_gate_inputs()
    cov_gates = apply_fail_closed_gates([cov_row], [])
    cov_match = next(g for g in cov_gates if g["gate"] == "inventory_coverage")
    chk.check(
        "gate_trigger_inventory_coverage",
        (not cov_match["ok"]) and cov_row["capability_id"] in cov_match["rows"],
        f"ok={cov_match['ok']} rows={cov_match['rows']}",
    )

    # --- B-wave: vendor adapter, typed declarations, scanner hygiene -------- #
    vendor = VendorParityAdapter()
    chk.check(
        "vendor_adapter_available_and_named",
        vendor.name == VENDOR_TRANSPORT and vendor.available() is True
        and vendor.fork_root() is not None,
        f"name={vendor.name!r} available={vendor.available()}",
    )
    try:
        descriptor = vendor.probe_vendored("qubit_constants", "get_qubit_home")
        chk.check(
            "vendor_probe_executes_vendored_module",
            descriptor.get("imported") is True
            and str(descriptor.get("names_hash", "")).startswith("sha256:")
            and descriptor.get("symbol_kind") in ("function", "class", "constant"),
            f"descriptor={descriptor!r}",
        )
    except TransportUnavailable as exc:
        chk.check("vendor_probe_executes_vendored_module", False, str(exc))

    with tempfile.TemporaryDirectory() as decl_dir:
        decl_path = Path(decl_dir) / DECLARATIONS_FILENAME
        decl_path.write_text(json.dumps({
            "schema_version": 1,
            "declarations": [
                {"capability_id": "gateway.env.selftest.1",
                 "disposition": "OPERATOR_ONLY",
                 "operator_prerequisite": "operator-held secret"},
                {"capability_id": "forged.pass.selftest",
                 "disposition": "PASS", "operator_prerequisite": "nope"},
            ],
        }), encoding="utf-8")
        loaded = load_declarations(decl_dir)
        chk.check(
            "declarations_refuse_success",
            set(loaded) == {"gateway.env.selftest.1"},
            f"loaded={sorted(loaded)}",
        )
        applied = apply_declarations(
            [{"capability_id": "gateway.env.selftest.1", "family": "gateway",
              "disposition": "OPEN"}], loaded)
        chk.check(
            "declarations_apply_operator_only",
            applied[0]["disposition"] == "OPERATOR_ONLY"
            and applied[0]["declared_by"] == "fixture_declaration"
            and applied[0]["operator_prerequisite"] == "operator-held secret",
            f"row={applied[0]!r}",
        )

    chk.check(
        "scanner_excludes_test_support_modules",
        _is_scanner_excluded({"upstream_path": "ui/x.test-support.ts:1"})
        and _is_scanner_excluded({"upstream_path": "ui/x.test-harness.ts:1"})
        and _is_scanner_excluded({"upstream_is_test_support": True})
        and _is_scanner_excluded(
            {"capability_id": "dashboard.control_source.page_test_support"})
        and not _is_scanner_excluded({"upstream_path": "ui/x.ts:1"}),
    )
    shipped_declarations = load_declarations(DEFAULT_FIXTURES_DIR)
    classes: dict[str, int] = {}
    for entry in shipped_declarations.values():
        key = str(entry.get("declaration_class") or entry.get("class"))
        classes[key] = classes.get(key, 0) + 1
    chk.check(
        "shipped_declarations_cover_b2_b3",
        classes.get("E_operator") == 100 and classes.get("B_missing") == 30,
        f"classes={classes}",
    )

    print(f"\nselftest: {chk.checks - chk.failures}/{chk.checks} checks passed")
    return EXIT_OK if chk.failures == 0 else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
