"""W9 vendor-sync ui-tui gap close — port the 12 ``ui-tui/src`` files that the
vendor manifest flags as the only real product-code miss.

Pinned source (read-only): ``/tmp/opencode/pinned/hermes-agent-69948c0057``
(Hermes v0.21.5, pin ``69948c0057``). Ported into ``qubit_runtime/ui-tui/src/``
with the hermes→qubit rename map; product copy de-branded per docs/BRAND.md;
wire identifiers (RPC method names, error codes, ``tui_gateway/`` module path
cross-reference) preserved. Upstream test files (``__tests__/``,
``*.test.*``) are intentionally NOT ported (generator policy).

Provenance per file — pinned ``ui-tui/src/`` line 1 to end:
``app/agentRoster.ts`` (62 lines), ``app/connectionOperationStore.ts`` (121),
``app/createServerRequestHandler.ts`` (114), ``app/processRoster.ts`` (122),
``app/serverRequestStore.ts`` (38), ``app/userMessages.ts`` (333),
``components/agentControls.tsx`` (154), ``components/agentsPanel.tsx`` (173),
``components/connectionSetupOverlay.tsx`` (580), ``lib/agentRows.ts`` (176),
``lib/processGlyph.ts`` (19), ``lib/subagentGlyph.ts`` (42).
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_SRC = REPO_ROOT / "qubit_runtime" / "ui-tui" / "src"
PINNED_SRC = Path("/tmp/opencode/pinned/hermes-agent-69948c0057/ui-tui/src")

FILES = [
    "app/agentRoster.ts",
    "app/connectionOperationStore.ts",
    "app/createServerRequestHandler.ts",
    "app/processRoster.ts",
    "app/serverRequestStore.ts",
    "app/userMessages.ts",
    "components/agentControls.tsx",
    "components/agentsPanel.tsx",
    "components/connectionSetupOverlay.tsx",
    "lib/agentRows.ts",
    "lib/processGlyph.ts",
    "lib/subagentGlyph.ts",
]

# The rename map, in application order. Nothing else may differ.
RENAMES = [
    ("@hermes/ink", "@qubit/ink"),
    ("@hermes/shared/", "@qubit/shared/"),
    ("Hermes", "Qubit"),
    ("hermes doctor", "qubit doctor"),
    ("hermes update", "qubit update"),
]

LEGACY_TOKENS = ["Hermes", "hermes", "NousResearch", "NousPortal", "OpenClaw", "@NousResearch"]

# Wire/contract literals that must survive the port byte-for-byte.
WIRE_LITERALS = [
    "subagent.steer",
    "subagent.tail",
    "connection.respond",
    "tui_gateway/server_requests.py",
    "RPC_UNKNOWN_METHOD",
]


def _pinned(rel: str) -> str:
    if not PINNED_SRC.is_dir():
        pytest.skip("BLOCKED — PINNED SOURCE REQUIRED")
    return (PINNED_SRC / rel).read_text()


def _expected_port(rel: str) -> str:
    text = _pinned(rel)
    for old, new in RENAMES:
        text = text.replace(old, new)
    return text


@pytest.mark.parametrize("rel", FILES)
def test_ported_file_exists(rel: str) -> None:
    assert (LOCAL_SRC / rel).is_file(), f"missing port: {rel}"


@pytest.mark.parametrize("rel", FILES)
def test_port_matches_pinned_modulo_rename_map(rel: str) -> None:
    assert (LOCAL_SRC / rel).read_text() == _expected_port(rel)


@pytest.mark.parametrize("rel", FILES)
def test_no_legacy_brand_tokens(rel: str) -> None:
    text = (LOCAL_SRC / rel).read_text()
    hits = [tok for tok in LEGACY_TOKENS if tok in text]
    assert not hits, f"{rel} still carries legacy tokens: {hits}"


@pytest.mark.parametrize("rel", FILES)
def test_no_upstream_package_specifier(rel: str) -> None:
    text = (LOCAL_SRC / rel).read_text()
    assert "@hermes/" not in text, f"{rel} still imports @hermes/*"


def test_wire_identifiers_preserved() -> None:
    pool = "\n".join((LOCAL_SRC / rel).read_text() for rel in FILES)
    for lit in WIRE_LITERALS:
        assert lit in pool, f"wire literal lost in port: {lit}"


def test_no_upstream_test_files_ported() -> None:
    stems = {Path(rel).stem for rel in FILES}
    hits = [
        p
        for p in (LOCAL_SRC / "__tests__").glob("*.test.*")
        if p.stem.split(".test")[0] in stems
    ]
    assert not hits, f"upstream tests ported for the 12: {hits}"
    for rel in FILES:
        stem = Path(rel).stem
        d = LOCAL_SRC / Path(rel).parent
        assert not list(d.glob(f"{stem}.test.*")), f"upstream test ported for {rel}"
