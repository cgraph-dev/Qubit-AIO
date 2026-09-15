"""Direct-provider bypass ratchet.

The provider/profile parity plan requires a regression test that fails when a
NEW direct provider path bypasses the canonical gateway/resolver authority
(``docs/QUBIT_PROVIDER_VOICE_PARITY_PLAN_2026-09-15.md`` Workstream P0, and
``docs/QUBIT_CURRENT_ARCHITECTURE_GAP_AUDIT_2026-09-15.md`` PV-03).

This test does not pretend the existing bypasses are gone. It records them as
tracked migration debt and *ratchets*: any module that is not in the recorded
set and that reaches a provider/ollama entry point directly will fail this
gate. As callers migrate to ``voicebridge.providers.gateway`` they can be
removed from the baseline.

Tracked PV-03 debt still to migrate: ``intent/classifier.py``,
``summarizer.py``, ``orchestrator.py`` (recorded in
``/tmp/qubit-evidence/DEFECT_MATRIX.md``; durable tracking lives in the plan).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Import the canonical gateway so this guard counts as a production test (and
# asserts the authority it protects is importable).
from voicebridge.providers import gateway as _gateway  # noqa: E402,F401

# Direct provider/ollama entry points that must go through the gateway unless
# the module is the sanctioned local adapter or the recorded migration debt.
_DIRECT_SYMBOLS = {
    "chat_local", "chat_local_stream", "classify_with_ollama", "summarize_ollama",
}

# Sanctioned local adapter/execution path + the gateway/resolver themselves.
_SANCTIONED = {
    "voicebridge/local_llm.py",           # the local adapter implementation
    "voicebridge/providers/gateway.py",   # the canonical gateway (local adapter)
    "voicebridge/providers/resolver.py",  # profile reachability probe
    "voicebridge/agent.py",               # sanctioned local agent turn path
    "voicebridge/chat_transport.py",      # sanctioned local streaming path
    "voicebridge/benchmarking/harness.py",  # offline measurement, not product
}

# Confirmed PV-03 migration debt. Remove an entry only after its callers go
# through voicebridge.providers.gateway. The F1 provider-migration wave moved
# intent/classifier.py, summarizer.py and orchestrator.py onto the canonical
# resolver + gateway (see tests/test_provider_migration.py), so the baseline is
# now empty — any NEW direct caller is a hard failure.
_TRACKED_DEBT: set = set()

ALLOWED = _SANCTIONED | _TRACKED_DEBT


def _direct_provider_modules() -> set:
    found = set()
    for path in sorted((REPO / "voicebridge").rglob("*.py")):
        if "__pycache__" in path.as_posix():
            continue
        rel = path.relative_to(REPO).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = (fn.attr if isinstance(fn, ast.Attribute)
                        else fn.id if isinstance(fn, ast.Name) else "")
                if name in _DIRECT_SYMBOLS:
                    found.add(rel)
    return found


def test_no_new_direct_provider_bypass():
    found = _direct_provider_modules()
    unexpected = sorted(found - ALLOWED)
    assert not unexpected, (
        "new direct provider/ollama callers bypass voicebridge.providers."
        f"gateway: {unexpected}. Route them through the canonical gateway/"
        "resolver, or record the module as tracked debt with a rationale.")


def test_allowed_debt_paths_still_exist():
    # Keep the ratchet honest: a baseline entry that no longer exists should be
    # pruned so the set reflects reality.
    missing = sorted(rel for rel in ALLOWED if not (REPO / rel).is_file())
    assert not missing, f"stale ratchet baseline entries: {missing}"


def test_chat_transport_uses_canonical_resolver():
    text = (REPO / "voicebridge/chat_transport.py").read_text(encoding="utf-8")
    assert re.search(r"from \.providers import resolver", text), (
        "chat_transport.resolve_model must resolve through the canonical "
        "providers.resolver authority")
