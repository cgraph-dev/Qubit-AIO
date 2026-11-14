#!/usr/bin/env python3
"""Verify the P2-04 Obsidian memory projection without touching it.

The projection is derived from the Business OS event log. This reader checks
that what is on disk is internally consistent and safe to publish:

* every note has JSON-compatible YAML frontmatter and the required fields;
* the note ``id`` matches its filename and the ``scope`` matches its directory;
* every ``supersedes`` / ``superseded_by`` link resolves to a note that exists;
* each scope has a ``_MOC.md`` that lists every note exactly once, and every
  note backlinks to ``[[_MOC]]``;
* no note id is duplicated across scopes;
* no secret-shaped text or paid-art reference leaked into the projection.

Read-only by design: ``--check`` is the only supported mode. Exit 0 means the
projection is consistent; exit 1 lists the concrete defects; exit 2 is a usage
or missing-root error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REQUIRED_FIELDS = (
    "id",
    "scope",
    "scope_id",
    "tenant",
    "source",
    "provenance",
    "confidence",
    "visibility",
    "expires",
    "supersedes",
)

SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]{6,}"),
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token"
        r"|refresh[_-]?token|client[_-]?secret)\b\s*[:=]\s*\S+"
    ),
)

PAID_ART_PATTERNS = (
    re.compile(r"(?i)data:image/"),
    re.compile(r"(?i)room_builder_floors"),
    re.compile(r"(?i)modern_interiors"),
    re.compile(r"(?i)assets/limezu"),
    re.compile(r"(?i)limezu\.itch\.io/assets"),
)

_LINK_RE = re.compile(r"\[\[([^\]|#]+)")
_REDACTED = "***REDACTED***"
_REDACTED_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token"
    r"|refresh[_-]?token|client[_-]?secret)\b\s*[:=]\s*"
    + re.escape(_REDACTED)
)
_REDACTED_BEARER_RE = re.compile(
    r"(?i)bearer\s+" + re.escape(_REDACTED)
)


def _without_redactions(text: str) -> str:
    """Drop already-redacted assignments so placeholders are not re-flagged."""
    cleaned = _REDACTED_ASSIGNMENT_RE.sub("", text)
    cleaned = _REDACTED_BEARER_RE.sub("", cleaned)
    return cleaned.replace(_REDACTED, "")


def _split_frontmatter(text: str) -> tuple[dict, int] | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    block = text[4:end]
    data: dict = {}
    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            continue
        if ":" not in line:
            return None
        key, value = line.split(":", 1)
        key = key.strip()
        try:
            data[key] = json.loads(value.strip())
        except json.JSONDecodeError:
            return None
    return data, end + len("\n---\n")


def _verify_note(path: Path, scope_dir: Path, errors: list[str]) -> str | None:
    text = path.read_text(encoding="utf-8")
    parsed = _split_frontmatter(text)
    if parsed is None:
        errors.append(f"{path}: missing or malformed JSON frontmatter")
        return None
    data, body_at = parsed
    body = text[body_at:]
    for field in REQUIRED_FIELDS:
        if field not in data:
            errors.append(f"{path}: frontmatter is missing {field!r}")
    memory_id = data.get("id")
    if memory_id != path.stem:
        errors.append(
            f"{path}: id {memory_id!r} does not match filename {path.stem!r}"
        )
    if data.get("scope") != scope_dir.name:
        errors.append(
            f"{path}: scope {data.get('scope')!r} does not match directory"
            f" {scope_dir.name!r}"
        )
    if not isinstance(data.get("tenant"), str) or not data["tenant"]:
        errors.append(f"{path}: tenant must be a non-empty string")
    confidence = data.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0.0 <= float(confidence) <= 1.0
    ):
        errors.append(f"{path}: confidence must be a number in [0, 1]")
    if "quarantined" in data and not isinstance(data["quarantined"], bool):
        errors.append(f"{path}: quarantined must be a boolean")
    if "[[_MOC]]" not in body:
        errors.append(f"{path}: note does not backlink to [[_MOC]]")
    for pattern in SECRET_PATTERNS:
        if pattern.search(_without_redactions(text)):
            errors.append(f"{path}: secret-shaped text leaked into the projection")
            break
    for pattern in PAID_ART_PATTERNS:
        if pattern.search(text):
            errors.append(f"{path}: paid-art reference leaked into the projection")
            break
    return memory_id if isinstance(memory_id, str) else None


def verify(root: Path) -> tuple[list[str], dict]:
    errors: list[str] = []
    if not root.exists():
        return [f"projection root {root} does not exist"], {}
    if not root.is_dir():
        return [f"projection root {root} is not a directory"], {}

    notes_by_scope: dict[str, dict[str, Path]] = {}
    ids_seen: dict[str, str] = {}
    note_count = 0
    for scope_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        scope_notes: dict[str, Path] = {}
        for path in sorted(scope_dir.glob("*.md")):
            if path.name == "_MOC.md":
                continue
            memory_id = _verify_note(path, scope_dir, errors)
            if memory_id is None:
                continue
            if memory_id in ids_seen:
                errors.append(
                    f"{path}: duplicate note id {memory_id!r} (also in"
                    f" {ids_seen[memory_id]})"
                )
            else:
                ids_seen[memory_id] = str(path)
            scope_notes[memory_id] = path
            note_count += 1
        notes_by_scope[scope_dir.name] = scope_notes

    for scope, notes in notes_by_scope.items():
        moc = root / scope / "_MOC.md"
        if not moc.exists():
            errors.append(f"{moc}: scope MOC is missing")
            continue
        moc_text = moc.read_text(encoding="utf-8")
        listed = [
            match.group(1).strip()
            for match in _LINK_RE.finditer(moc_text)
            if match.group(1).strip() != "_MOC"
        ]
        if len(listed) != len(set(listed)):
            errors.append(f"{moc}: a note is listed more than once")
        for memory_id in notes:
            if memory_id not in listed:
                errors.append(f"{moc}: note {memory_id!r} is not listed")
        for linked in listed:
            if linked not in notes:
                errors.append(f"{moc}: dangling link [[{linked}]]")
        for memory_id, path in notes.items():
            data = _split_frontmatter(path.read_text(encoding="utf-8"))
            if data is None:
                continue
            values = data[0]
            for field in ("supersedes", "superseded_by"):
                target = values.get(field)
                if target and target not in notes:
                    errors.append(
                        f"{path}: {field} link [[{target}]] does not resolve"
                        f" in scope {scope!r}"
                    )

    return errors, {
        "root": str(root),
        "notes": note_count,
        "scopes": sorted(notes_by_scope),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="projection root directory")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify without writing (the only mode this tool supports)",
    )
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    errors, stats = verify(Path(args.root))
    report = {
        "ok": not errors,
        "errors": errors,
        **stats,
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif errors:
        print(f"FAIL: {len(errors)} projection defect(s) under {args.root}")
        for error in errors:
            print(f"  - {error}")
    else:
        scope_count = len(stats.get("scopes", []))
        print(
            f"OK: {stats.get('notes', 0)} note(s) across {scope_count}"
            f" scope(s) under {args.root}"
        )
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
