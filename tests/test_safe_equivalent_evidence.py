"""SAFE_EQUIVALENT evidence validator (owner terminal-status decision).

Every ledger row whose status is ``SAFE_EQUIVALENT`` must be provable against
the owner criteria: a real Qubit backend, an explicit typed refusal/gate for
the unsafe raw upstream behavior, a valid non-empty gate ``needs``, and an
on-disk proving test. This module is the machine check behind
``docs/QUBIT_SAFE_EQUIVALENTS.md``.

Rows that do **not** meet a criterion are reported (not hidden) for the
orchestrator to reclassify. They are enumerated in
``REPORTED_CODE_GAPS`` / ``REPORTED_EMPTY_BACKEND`` below and in §5 of the
audit doc; any new offender fails this suite because the assertion is an exact
set match. The ledger generators are never edited here.

No skips, no xfail.
"""
from __future__ import annotations

import ast
import functools
import importlib.util
import inspect
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SECTION_FILES = (
    "docs/QUBIT_GAP_LEDGER.hermes.json",
    "docs/QUBIT_GAP_LEDGER.openclaw.json",
    "docs/QUBIT_GAP_LEDGER.qubit.json",
)
AUDIT_DOC = REPO_ROOT / "docs" / "QUBIT_SAFE_EQUIVALENTS.md"

# A typed refusal/gate code: an ALL_CAPS token with at least one underscore word.
CODE_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")

# SAFE_EQUIVALENT requires a real prerequisite gate; ``none`` is never valid.
VALID_NEEDS = frozenset({"owner", "creds", "hardware"})

# Rows whose serialized ledger evidence names no typed refusal/gate code and
# whose live compat route is not a declared gate.  All previously reported rows
# were resolved in the 2026-09-13 audit: every ``cli:*`` row now names the real
# production refusal/gate code and cites an on-disk proving test, and
# ``inv:skill:skills/index-cache`` was reclassified (it is a cache/asset
# directory with no loadable ``SKILL.md``, so it is **not** a safe equivalent
# and is no longer a ``SAFE_EQUIVALENT`` row).  The exact-set assertion below
# makes any new offender visible immediately.
REPORTED_CODE_GAPS = frozenset()

# Rows with no real Qubit backend path (empty ``qubit_backend`` and no live
# compat match).  Empty after the 2026-09-13 reclassification.
REPORTED_EMPTY_BACKEND = frozenset()


# --------------------------------------------------------------------------- io
def _sections() -> list[dict]:
    return [json.loads((REPO_ROOT / p).read_text(encoding="utf-8"))
            for p in SECTION_FILES]


def _rows() -> list[dict]:
    rows: list[dict] = []
    for data in _sections():
        for row in data["rows"]:
            if row.get("status") == "SAFE_EQUIVALENT":
                rows.append(row)
    return rows


def _row_ids() -> set[str]:
    return {row["id"] for row in _rows()}


# ------------------------------------------------------------- live evidence
@functools.lru_cache(maxsize=1)
def _live() -> tuple:
    """(hermes_compat, rebuild_ledger_hermes) loaded once for gate resolution."""
    from voicebridge import hermes_compat as hc  # noqa: PLC0415

    scripts = REPO_ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location(
        "_rebuild_ledger_hermes_evidence",
        scripts / "rebuild_ledger_hermes.py")
    rebuild = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(rebuild)
    return hc, rebuild


def _handler_codes(handler: str) -> set[str]:
    hc, _ = _live()
    fn = hc.HANDLERS.get(handler)
    if not callable(fn):
        return set()
    try:
        source = inspect.getsource(fn)
    except (OSError, TypeError):
        return set()
    found: set[str] = set()
    for match in re.finditer(r"_gate\(\s*['\"]([^'\"]+)", source):
        found.add(match.group(1))
    for match in re.finditer(r"['\"]code['\"]\s*:\s*['\"]([^'\"]+)", source):
        found.add(match.group(1))
    return {c for c in found if c.isupper()}


def _live_route_codes(row: dict) -> set[str]:
    """Gate code from the live compat route / generator evidence (API rows)."""
    row_id = str(row.get("id", ""))
    if not row_id.startswith("api:"):
        return set()
    rest = row_id[4:]
    if " " not in rest:
        return set()
    method, path = rest.split(" ", 1)
    hc, rebuild = _live()
    found: set[str] = set()
    override = rebuild.ROUTE_EVIDENCE.get((method, path), {})
    if override.get("code"):
        found.add(str(override["code"]))
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
        return {c for c in found if c.isupper()}
    try:
        route, _ = hc.match(method, path)
    except Exception:  # noqa: BLE001 - a broken matcher must not mask the row
        route = None
    if route:
        gate = route.get("gate") or {}
        if gate.get("code"):
            found.add(str(gate["code"]))
        handler = str(route.get("handler", ""))
        if handler and handler != "__gate__":
            found |= _handler_codes(handler)
    return {c for c in found if c.isupper()}


def _row_codes(row: dict) -> set[str]:
    """Typed gate/refusal codes named anywhere in the row's serialized evidence."""
    blob = " ".join(str(row.get(key, ""))
                    for key in ("note", "capability", "id", "qubit_backend"))
    return set(CODE_RE.findall(blob)) | _live_route_codes(row)


# ------------------------------------------------------------- row validators
def _proving_errors(row: dict) -> list[str]:
    """Validators for the on-disk proving test (file and function)."""
    proving = str(row.get("proving_test", "") or "").strip()
    if not proving:
        return ["empty proving_test"]
    file_part, _, func_part = proving.partition("::")
    target = REPO_ROOT / file_part
    if not target.is_file():
        return [f"proving_test file missing: {file_part}"]
    if not func_part:
        return []
    try:
        tree = ast.parse(target.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:  # pragma: no cover - malformed test file
        return [f"proving_test file unparsable: {file_part}: {exc}"]
    names = {node.name for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if func_part not in names:
        return [f"proving_test function missing: {proving}"]
    return []


def _need_errors(row: dict) -> list[str]:
    needs = row.get("needs")
    if not needs:
        return ["empty needs"]
    if needs not in VALID_NEEDS:
        return [f"invalid needs: {needs!r} (want one of {sorted(VALID_NEEDS)})"]
    return []


def _backend_errors(row: dict) -> list[str]:
    if str(row.get("qubit_backend", "") or "").strip():
        return []
    if _live_route_codes(row):
        return []
    return ["empty qubit_backend and no live compat route"]


# ------------------------------------------------------------------- asserts
def test_every_safe_equivalent_row_has_proving_test_on_disk() -> None:
    offenders = [(row["id"], _proving_errors(row))
                 for row in _rows() if _proving_errors(row)]
    assert offenders == [], offenders


def test_every_safe_equivalent_row_has_valid_gate_need() -> None:
    offenders = [(row["id"], _need_errors(row))
                 for row in _rows() if _need_errors(row)]
    assert offenders == [], offenders


def test_every_safe_equivalent_row_names_a_typed_gate_or_refusal_code() -> None:
    discovered = frozenset(row["id"] for row in _rows() if not _row_codes(row))
    new = sorted(discovered - REPORTED_CODE_GAPS)
    fixed = sorted(REPORTED_CODE_GAPS - discovered)
    assert new == [], (
        "SAFE_EQUIVALENT rows with no typed gate/refusal code must be "
        f"reported/reclassified or fixed: {new}")
    assert fixed == [], (
        "rows in REPORTED_CODE_GAPS now name a code; remove them from the "
        f"reported set and audit doc: {fixed}")
    assert discovered == REPORTED_CODE_GAPS


def test_every_safe_equivalent_row_has_a_real_backend_path() -> None:
    discovered = frozenset(row["id"] for row in _rows()
                           if _backend_errors(row))
    assert discovered == REPORTED_EMPTY_BACKEND, sorted(discovered)


def test_audit_doc_lists_every_distinct_refusal_code() -> None:
    assert AUDIT_DOC.is_file(), f"missing audit doc: {AUDIT_DOC}"
    doc = AUDIT_DOC.read_text(encoding="utf-8")
    codes = set()
    for row in _rows():
        codes |= _row_codes(row)
    assert codes, "no typed gate/refusal codes resolved from the ledger"
    missing = sorted(code for code in codes if code not in doc)
    assert missing == [], f"audit doc does not list refusal codes: {missing}"


def test_audit_doc_reports_every_code_gap_row() -> None:
    doc = AUDIT_DOC.read_text(encoding="utf-8")
    missing = sorted(row_id for row_id in REPORTED_CODE_GAPS
                     if row_id not in doc)
    assert missing == [], f"audit doc omits reported rows: {missing}"


# -------------------------------------------------------------- negative checks
def _fabricated_row(**overrides) -> dict:
    row = {
        "id": "fabricated-row",
        "area": "test",
        "capability": "fabricated capability",
        "upstream_ref": "",
        "qubit_backend": "",
        "cli": "",
        "dashboard": "",
        "voice": "",
        "status": "SAFE_EQUIVALENT",
        "proving_test": "",
        "note": "",
        "needs": "",
    }
    row.update(overrides)
    return row


def _compliant_row() -> dict:
    return _fabricated_row(
        id="api:POST /api/example/gated",
        capability="POST /api/example/gated",
        qubit_backend="voicebridge/hermes_compat.py:__gate__",
        proving_test=("tests/test_hermes_routes_parity8u.py::"
                      "test_new_gates_dispatch_exact_typed_code"),
        note="Typed policy gate EXAMPLE_GATE via compat dispatcher.",
        needs="owner",
    )


def test_negative_fabricated_row_without_test_or_backend_is_rejected() -> None:
    row = _fabricated_row()
    assert _proving_errors(row) != []
    assert _backend_errors(row) != []
    assert _need_errors(row) != []
    assert _row_codes(row) == set()


def test_negative_fabricated_row_with_missing_test_file_is_rejected() -> None:
    row = _fabricated_row(
        qubit_backend="voicebridge/hermes_compat.py:__gate__",
        proving_test="tests/does_not_exist.py::test_nope",
        note="Typed policy gate EXAMPLE_GATE via compat dispatcher.",
        needs="owner",
    )
    assert _proving_errors(row) != []


def test_negative_fabricated_row_with_bad_need_is_rejected() -> None:
    assert _need_errors(_fabricated_row(needs="none")) != []
    assert _need_errors(_fabricated_row(needs="whatever")) != []


def test_negative_fabricated_row_without_typed_code_is_rejected() -> None:
    row = _fabricated_row(
        qubit_backend="voicebridge/hermes_compat.py:__gate__",
        proving_test=("tests/test_hermes_routes_parity8u.py::"
                      "test_new_gates_dispatch_exact_typed_code"),
        note="refused",
        needs="owner",
    )
    assert _row_codes(row) == set()


def test_positive_compliant_fabricated_row_passes_all_validators() -> None:
    row = _compliant_row()
    assert _proving_errors(row) == []
    assert _need_errors(row) == []
    assert _backend_errors(row) == []
    assert "EXAMPLE_GATE" in _row_codes(row)


_STATUS_WORDS = frozenset({
    "SAFE_EQUIVALENT", "OPERATOR_ONLY", "IMPLEMENTED", "GATED", "PARTIAL",
    "MISSING", "UNVERIFIED", "CLI_ONLY", "DASHBOARD_ONLY", "VOICE_ONLY",
})


@functools.lru_cache(maxsize=1)
def _production_corpus() -> str:
    """Concatenated production Python source (voicebridge/**)."""
    parts: list[str] = []
    for path in sorted((REPO_ROOT / "voicebridge").rglob("*.py")):
        try:
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(parts)


def test_named_refusal_codes_are_emitted_by_production_source() -> None:
    """Every code a SAFE_EQUIVALENT row names must exist in production code.

    Guards against a phantom gate label (a code named in the ledger note that no
    Qubit module ever emits).
    """
    corpus = _production_corpus()
    missing: dict[str, list[str]] = {}
    for row in _rows():
        codes = {c for c in CODE_RE.findall(str(row.get("note", "")))
                 if c not in _STATUS_WORDS
                 and not c.startswith(("QUBIT_", "HERMES_", "OPENCLAW_"))}
        absent = sorted(c for c in codes if c not in corpus)
        if absent:
            missing[row["id"]] = absent
    assert missing == {}, missing
