#!/usr/bin/env python3
"""Verify the P4-05 department/office Obsidian memory projection.

Read-only structural verification plus an optional rebuild-equality check:

* the office MOC links every declared department MOC and every shared special
  room MOC, and never repeats department prose;
* each department/special MOC links exactly the notes projected for it;
* note ids and artifact ids are stable and match their filenames;
* every wikilink resolves, and every MOC -> note edge has a note -> MOC
  backlink;
* the projection manifest digest matches the files on disk;
* no secret-shaped text or paid-art reference leaked into the projection;
* ACL posture: every manifest note is declared readable, and a private note is
  never linked from the office-wide MOC;
* with ``--db`` (or the default acceptance fixture) the projection is rebuilt
  from the authoritative event log and must be byte-identical.

``--json`` emits a machine report. Exit 0 = consistent, 1 = defects found,
2 = usage / missing root / unreadable store.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_BASE = Path("/tmp/qoffice-memory-projection-p4")
DEFAULT_ROOT = DEFAULT_BASE / "projection"
DEFAULT_DB = DEFAULT_BASE / "business.db"

REQUIRED_FIELDS = (
    "id",
    "scope",
    "scope_id",
    "tenant",
    "source",
    "visibility",
    "department",
    "special_room",
    "category",
)
VALID_VISIBILITY = frozenset(
    {"private", "internal", "department", "office", "public"}
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
    cleaned = _REDACTED_ASSIGNMENT_RE.sub("", text)
    cleaned = _REDACTED_BEARER_RE.sub("", cleaned)
    return cleaned.replace(_REDACTED, "")


def _split_frontmatter(text: str) -> tuple[dict, int] | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    data: dict = {}
    for raw in text[4:end].splitlines():
        line = raw.strip()
        if not line:
            continue
        if ":" not in line:
            return None
        key, value = line.split(":", 1)
        try:
            data[key.strip()] = json.loads(value.strip())
        except json.JSONDecodeError:
            return None
    return data, end + len("\n---\n")


def _links(text: str) -> list[str]:
    return [match.group(1).strip() for match in _LINK_RE.finditer(text)]


def _resolve(root: Path, target: str, from_file: Path | None = None) -> Path | None:
    target = target.strip()
    candidates: list[Path] = []
    if target in ("_MOC", ""):
        candidates.append(root / "_MOC.md")
    elif target.endswith(".md"):
        candidates.append(root / target)
    else:
        candidates.append(root / f"{target}.md")
    if from_file is not None:
        parent = from_file.parent
        if target.endswith(".md"):
            candidates.append(parent / target)
        else:
            candidates.append(parent / f"{target}.md")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _scan(text: str) -> list[str]:
    scan = _without_redactions(text)
    findings: list[str] = []
    if any(pattern.search(scan) for pattern in SECRET_PATTERNS):
        findings.append("secret-shaped text")
    if any(pattern.search(text) for pattern in PAID_ART_PATTERNS):
        findings.append("paid-art reference")
    return findings


def _digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.md")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _check_notes(
    root: Path, manifest: dict, errors: list[str]
) -> dict[str, dict]:
    rows = manifest.get("notes") or []
    if not isinstance(rows, list):
        errors.append("manifest notes is not a list")
        return {}
    notes: dict[str, dict] = {}
    declared_paths: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            errors.append("manifest note row is not an object")
            continue
        memory_id = row.get("memory_id")
        relative = row.get("path")
        if not isinstance(memory_id, str) or not memory_id:
            errors.append("manifest note row has no memory_id")
            continue
        if not isinstance(relative, str) or not relative:
            errors.append(f"{memory_id}: manifest note has no path")
            continue
        if memory_id in notes:
            errors.append(f"{memory_id}: note id is duplicated in the manifest")
            continue
        if row.get("readable") is not True:
            errors.append(f"{memory_id}: manifest note is not marked readable")
        declared_paths.add(relative)
        path = root / relative
        if not path.is_file():
            errors.append(f"{memory_id}: projected note {relative} is missing")
            continue
        text = path.read_text(encoding="utf-8")
        parsed = _split_frontmatter(text)
        if parsed is None:
            errors.append(f"{path}: missing or malformed JSON frontmatter")
            continue
        data, body_at = parsed
        body = text[body_at:]
        for field in REQUIRED_FIELDS:
            if field not in data:
                errors.append(f"{path}: frontmatter is missing {field!r}")
        if data.get("id") != memory_id:
            errors.append(
                f"{path}: id {data.get('id')!r} does not match manifest"
                f" {memory_id!r}"
            )
        if data.get("id") != path.stem:
            errors.append(
                f"{path}: id {data.get('id')!r} does not match filename"
                f" {path.stem!r}"
            )
        if data.get("tenant") != manifest.get("tenant_id"):
            errors.append(f"{path}: tenant does not match the manifest")
        visibility = data.get("visibility")
        if visibility not in VALID_VISIBILITY:
            errors.append(f"{path}: invalid visibility {visibility!r}")
        for finding in _scan(text):
            errors.append(f"{path}: {finding} leaked into the projection")
        notes[memory_id] = {
            "row": row,
            "path": path,
            "data": data,
            "body": body,
            "text": text,
        }
    actual: set[str] = set()
    notes_dir = root / "notes"
    if notes_dir.is_dir():
        for path in notes_dir.glob("*.md"):
            actual.add(str(path.relative_to(root)))
    missing = actual - declared_paths
    extra = declared_paths - actual
    for relative in sorted(missing):
        errors.append(f"{relative}: note file is not declared in the manifest")
    for relative in sorted(extra):
        errors.append(f"{relative}: declared note file is missing")
    return notes


def _check_mocs(
    root: Path, manifest: dict, notes: dict[str, dict], errors: list[str]
) -> None:
    office_moc = root / "_MOC.md"
    if not office_moc.is_file():
        errors.append("office MOC _MOC.md is missing")
        office_links: list[str] = []
    else:
        text = office_moc.read_text(encoding="utf-8")
        if _split_frontmatter(text) is None:
            errors.append("office MOC has malformed frontmatter")
        for finding in _scan(text):
            errors.append(f"office MOC: {finding} leaked into the projection")
        office_links = _links(text)
        for target in office_links:
            if _resolve(root, target, office_moc) is None:
                errors.append(f"office MOC: dangling link [[{target}]]")

    departments = manifest.get("departments") or []
    specials = manifest.get("special_rooms") or []
    department_ids: set[str] = set()
    for row in departments:
        dept_id = row.get("id")
        moc = row.get("moc")
        if not isinstance(dept_id, str) or not isinstance(moc, str):
            errors.append("department manifest row is malformed")
            continue
        department_ids.add(dept_id)
        path = root / moc
        if not path.is_file():
            errors.append(f"{dept_id}: department MOC {moc} is missing")
            continue
        expected_target = moc[:-3] if moc.endswith(".md") else moc
        if expected_target not in office_links and moc not in office_links:
            errors.append(f"office MOC does not link department {dept_id!r}")
        text = path.read_text(encoding="utf-8")
        if _split_frontmatter(text) is None:
            errors.append(f"{moc}: malformed frontmatter")
        for finding in _scan(text):
            errors.append(f"{moc}: {finding} leaked into the projection")
        links = _links(text)
        expected = {
            memory_id
            for memory_id, item in notes.items()
            if item["row"].get("department") == dept_id
        }
        listed: set[str] = set()
        for target in links:
            if target in ("_MOC",) or "/_MOC" in target:
                if _resolve(root, target, path) is None:
                    errors.append(f"{moc}: dangling MOC link [[{target}]]")
                continue
            if not target.startswith("notes/"):
                continue
            memory_id = target.split("/", 1)[1]
            listed.add(memory_id)
            item = notes.get(memory_id)
            if item is None:
                errors.append(f"{moc}: dangling note link [[{target}]]")
                continue
            if item["row"].get("department") != dept_id:
                errors.append(
                    f"{moc}: exposes note {memory_id!r} from"
                    f" department {item['row'].get('department')!r}"
                )
            moc_target = moc[:-3] if moc.endswith(".md") else moc
            if f"[[{moc_target}" not in item["text"]:
                errors.append(
                    f"{moc}: note {memory_id!r} has no backlink to the"
                    " department MOC"
                )
        if len(listed) != len(set(listed)):
            errors.append(f"{moc}: a note is listed more than once")
        missing = expected - listed
        for memory_id in sorted(missing):
            errors.append(f"{moc}: note {memory_id!r} is not listed")
        if _resolve(root, "_MOC", path) is None:
            errors.append(f"{path}: office MOC backlink does not resolve")

    special_ids: set[str] = set()
    for row in specials:
        special_id = row.get("id")
        moc = row.get("moc")
        if not isinstance(special_id, str) or not isinstance(moc, str):
            errors.append("special-room manifest row is malformed")
            continue
        special_ids.add(special_id)
        path = root / moc
        if not path.is_file():
            errors.append(f"{special_id}: special MOC {moc} is missing")
            continue
        expected_target = moc[:-3] if moc.endswith(".md") else moc
        if expected_target not in office_links and moc not in office_links:
            errors.append(f"office MOC does not link special room {special_id!r}")
        text = path.read_text(encoding="utf-8")
        if _split_frontmatter(text) is None:
            errors.append(f"{moc}: malformed frontmatter")
        for finding in _scan(text):
            errors.append(f"{moc}: {finding} leaked into the projection")
        expected = {
            memory_id
            for memory_id, item in notes.items()
            if item["row"].get("special_room") == special_id
        }
        listed = {
            target.split("/", 1)[1]
            for target in _links(text)
            if target.startswith("notes/")
        }
        missing = expected - listed
        for memory_id in sorted(missing):
            errors.append(f"{moc}: note {memory_id!r} is not listed")
        for memory_id in sorted(listed):
            item = notes.get(memory_id)
            if item is None:
                errors.append(f"{moc}: dangling note link [[notes/{memory_id}]]")
                continue
            if item["row"].get("special_room") != special_id:
                errors.append(
                    f"{moc}: exposes note {memory_id!r} from another room"
                )

    for target in office_links:
        if not target.startswith("notes/"):
            continue
        memory_id = target.split("/", 1)[1]
        item = notes.get(memory_id)
        if item is None:
            errors.append(f"office MOC: dangling note link [[{target}]]")
            continue
        row = item["row"]
        if row.get("department") or row.get("special_room"):
            errors.append(
                f"office MOC: repeats department/room note {memory_id!r}"
            )
        if item["data"].get("visibility") == "private":
            errors.append(
                f"office MOC: exposes private note {memory_id!r}"
            )
        if "[[_MOC" not in item["text"]:
            errors.append(
                f"note {memory_id!r} has no backlink to the office MOC"
            )

    # Every declared department/special id must be a real manifest row.
    for memory_id, item in notes.items():
        dept = item["row"].get("department")
        if dept is not None and dept not in department_ids:
            errors.append(
                f"{memory_id}: department {dept!r} has no MOC manifest row"
            )
        room = item["row"].get("special_room")
        if room is not None and room not in special_ids:
            errors.append(
                f"{memory_id}: special room {room!r} has no MOC manifest row"
            )


def _check_artifacts(
    root: Path, manifest: dict, notes: dict[str, dict], errors: list[str]
) -> None:
    rows = manifest.get("artifacts") or []
    declared: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            errors.append("artifact manifest row is not an object")
            continue
        artifact = row.get("id")
        relative = row.get("path")
        if not isinstance(artifact, str) or not isinstance(relative, str):
            errors.append("artifact manifest row is malformed")
            continue
        path = root / relative
        if not path.is_file():
            errors.append(f"artifact {artifact!r}: {relative} is missing")
            continue
        text = path.read_text(encoding="utf-8")
        if _split_frontmatter(text) is None:
            errors.append(f"{path}: malformed frontmatter")
        for finding in _scan(text):
            errors.append(f"{path}: {finding} leaked into the projection")
        declared[artifact] = {"path": path, "text": text}
    for memory_id, item in notes.items():
        for artifact in item["row"].get("artifacts") or []:
            if artifact not in declared:
                errors.append(
                    f"{memory_id}: artifact {artifact!r} has no index note"
                )
                continue
            if f"[[notes/{memory_id}" not in declared[artifact]["text"]:
                errors.append(
                    f"artifact {artifact!r}: no backlink to {memory_id!r}"
                )
        if item["row"].get("artifacts"):
            for target in _links(item["text"]):
                if target.startswith("artifacts/") and _resolve(
                    root, target, item["path"]
                ) is None:
                    errors.append(
                        f"{memory_id}: dangling artifact link [[{target}]]"
                    )


def _rebuild_equality(
    root: Path, manifest: dict, db_path: Path, errors: list[str]
) -> None:
    try:
        from voicebridge.business.memory import MemoryService, SameTenantACL
        from voicebridge.business.memory_projection import MemoryProjection
        from voicebridge.business.store import BusinessStore
    except Exception as exc:  # pragma: no cover - import environment failure
        errors.append(f"rebuild check could not import the runtime: {exc}")
        return
    tenant = manifest.get("tenant_id")
    viewer = manifest.get("viewer")
    departments = [
        row.get("id") for row in manifest.get("departments") or []
        if isinstance(row, dict) and row.get("id")
    ]
    specials = [
        row.get("id") for row in manifest.get("special_rooms") or []
        if isinstance(row, dict) and row.get("id")
    ]
    try:
        store = BusinessStore(db_path)
        with tempfile.TemporaryDirectory(prefix="qoffice-proj-verify-") as tmp:
            service = MemoryService(
                store, Path(tmp) / "unused", SameTenantACL()
            )
            rebuilt_root = Path(tmp) / "projection"
            projection = MemoryProjection(
                service,
                rebuilt_root,
                tenant_id=tenant,
                principal=viewer,
                departments=departments,
                special_rooms=specials,
            )
            projection.rebuild()
            left = _snapshot(root)
            right = _snapshot(rebuilt_root)
            if left != right:
                only_left = sorted(set(left) - set(right))
                only_right = sorted(set(right) - set(left))
                changed = sorted(
                    key for key in set(left) & set(right)
                    if left[key] != right[key]
                )
                detail = []
                if only_left:
                    detail.append(f"only in projection: {only_left[:5]}")
                if only_right:
                    detail.append(f"only in rebuild: {only_right[:5]}")
                if changed:
                    detail.append(f"different: {changed[:5]}")
                errors.append(
                    "rebuild from the event log is not identical: "
                    + "; ".join(detail)
                )
    except Exception as exc:
        errors.append(f"rebuild check failed: {exc}")


def verify(
    root: Path,
    *,
    db_path: Path | None = None,
    require_rebuild: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    stats: dict[str, Any] = {"root": str(root)}
    if not root.exists():
        return [f"projection root {root} does not exist"], stats
    if not root.is_dir():
        return [f"projection root {root} is not a directory"], stats
    manifest_path = root / "_projection.json"
    if not manifest_path.is_file():
        return [f"{manifest_path} is missing"], stats
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{manifest_path}: {exc}"], stats
    if not isinstance(manifest, dict):
        return [f"{manifest_path}: manifest is not an object"], stats
    if manifest.get("schema") != "qubit.office.memory_projection/1":
        errors.append("projection manifest has an unknown schema")
    if not isinstance(manifest.get("tenant_id"), str) or not manifest["tenant_id"]:
        errors.append("projection manifest has no tenant_id")
    if manifest.get("acl_mode") not in ("injected", "none"):
        errors.append("projection manifest has an unknown acl_mode")
    for field in ("departments", "special_rooms", "artifacts", "notes"):
        if not isinstance(manifest.get(field), list):
            errors.append(f"projection manifest {field} is not a list")

    notes = _check_notes(root, manifest, errors)
    _check_mocs(root, manifest, notes, errors)
    _check_artifacts(root, manifest, notes, errors)

    digest = _digest(root)
    if manifest.get("build_digest") != digest:
        errors.append(
            "projection digest does not match the files on disk"
            " (projection was edited or not produced by rebuild)"
        )
    stats.update(
        {
            "notes": len(notes),
            "departments": len(manifest.get("departments") or []),
            "special_rooms": len(manifest.get("special_rooms") or []),
            "artifacts": len(manifest.get("artifacts") or []),
            "build_digest": digest,
            "viewer": manifest.get("viewer"),
        }
    )

    if db_path is not None:
        if not db_path.is_file():
            errors.append(f"rebuild database {db_path} does not exist")
        else:
            _rebuild_equality(root, manifest, db_path, errors)
            stats["rebuild_checked"] = True
    elif require_rebuild:
        errors.append("rebuild equality was required but no database was given")
    else:
        stats["rebuild_checked"] = False
    return errors, stats


def _default_db(root: Path) -> Path | None:
    if root.resolve() != DEFAULT_ROOT.resolve():
        return None
    return DEFAULT_DB if DEFAULT_DB.is_file() else None


def _seed_fixture(base: Path) -> Path | None:
    """Build a hermetic acceptance fixture when the persisted one is absent.

    The bare acceptance command must work on a clean checkout. It only writes
    under a temporary directory; the caller removes it. Explicit ``--root``
    paths are never seeded.
    """
    try:
        from voicebridge.business import BusinessStore, identity
        from voicebridge.business.memory import MemoryService, SameTenantACL
        from voicebridge.business.memory_projection import MemoryProjection
    except Exception:
        return None
    store = BusinessStore(base / "business.db")
    tenant = store.create_tenant("Qubit Office Acceptance")
    store.create_business(tenant.tenant_id, "Qubit Office", slug="office")
    principal = identity.create_principal(store, tenant.tenant_id, kind="owner")
    service = MemoryService(store, base / "unused", SameTenantACL())
    service.remember(
        principal,
        scope="department",
        scope_id="engineering",
        content="Engineering runs the Friday release train.",
        source="owner",
        provenance={
            "writer": "owner",
            "department": "engineering",
            "room": "release-room",
            "artifact_ids": ["build-42-sha256"],
        },
        visibility="department",
        labels=["deliverable"],
    )
    service.remember(
        principal,
        scope="office",
        scope_id="office-1",
        content="Office hours start at 09:00.",
        source="owner",
        provenance={"writer": "owner"},
        visibility="office",
    )
    projection = MemoryProjection(
        service,
        base / "projection",
        tenant_id=tenant.tenant_id,
        principal=principal,
        departments=["engineering"],
    )
    projection.rebuild()
    return base / "projection"


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help="projection root directory (default: %(default)s)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Business OS SQLite store used for the rebuild-equality check",
    )
    parser.add_argument(
        "--require-rebuild",
        action="store_true",
        help="fail unless a rebuild-equality check ran",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="read-only verification (the only mode this tool supports)",
    )
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)

    explicit_root = any(
        item == "--root" or item.startswith("--root=") for item in raw_args
    )
    root = Path(args.root)
    db_path: Path | None = None
    fixture_note = ""
    temporary: tempfile.TemporaryDirectory | None = None
    if not root.exists() and not explicit_root and args.db is None:
        temporary = tempfile.TemporaryDirectory(
            prefix="qoffice-projection-fixture-"
        )
        seeded = _seed_fixture(Path(temporary.name))
        if seeded is not None:
            root = seeded
            db_path = Path(temporary.name) / "business.db"
            fixture_note = " (hermetic fixture)"
    if args.db is not None:
        db_path = Path(args.db)
    elif db_path is None:
        db_path = _default_db(root)
        if db_path is not None:
            print(
                f"note: checking rebuild equality against {db_path}",
                file=sys.stderr,
            )

    try:
        errors, stats = verify(
            root, db_path=db_path, require_rebuild=args.require_rebuild
        )
        root_present = root.exists()
        manifest_present = (root / "_projection.json").is_file()
    finally:
        if temporary is not None:
            temporary.cleanup()
    if fixture_note:
        stats["fixture"] = "ephemeral"
    report = {"ok": not errors, "errors": errors, "stats": stats}
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif errors:
        print(f"FAIL: {len(errors)} projection defect(s) under {root}{fixture_note}")
        for error in errors:
            print(f"  - {error}")
    else:
        print(
            f"OK: {stats.get('notes', 0)} note(s),"
            f" {stats.get('departments', 0)} department(s),"
            f" {stats.get('special_rooms', 0)} special room(s),"
            f" {stats.get('artifacts', 0)} artifact index note(s) under {root}"
            + (" (rebuild identical)" if stats.get("rebuild_checked") else "")
            + fixture_note
        )
    if not root_present or not manifest_present:
        return 2
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
