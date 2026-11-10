"""``qubit office theme`` — install, validate, list, and share a theme.

The repo ships **recipes**, never the purchased art. A recipe is the
shareable ``theme.recipe.json`` (tokens, agent recipes, template version,
credit, licence, source description). A full pack — the same manifest plus
locally built atlases — is what :mod:`scripts.office_theme_build` produces
into ``$QUBIT_HOME/office/themes/<id>`` on the owner's machine.

This module is the CLI seam onto :mod:`voicebridge.office.theme`:

* ``list``     installed/bundled themes, their kind, and the active id;
* ``validate`` a recipe directory/json/zip or a full pack directory/zip;
* ``install``  a recipe or a locally built pack, atomically, into the user
  theme root;
* ``export``   the recipe only — never an atlas, never art — as a zip that
  re-installs and re-validates.

Archive input is deliberately narrow: a plain 32-bit zip with only regular
files and directories. Traversal, symlinks, duplicate/NFC-colliding names,
encryption, ZIP64, unsupported compression, oversized or ratio-bomb entries,
and executable content are refused with typed reasons **before** anything is
written. The staged copy is validated, moved into place, and read back; a
failure restores whatever was there before.

No network and no service restart. ``install`` writes only under
``$QUBIT_HOME/office/themes`` (plus a sibling staging dir that is removed);
``export`` writes exactly the requested destination archive and nothing else.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import theme as T

#: Archive bounds. A full pack is a handful of files (manifest, agents,
#: calibration, two atlases, two indexes) totalling a few MiB; these caps are
#: generous for that and small enough that a bomb is refused early.
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_FILES = 128
MAX_COMPRESSION_RATIO = 250
MAX_RATIO_MIN_BYTES = 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024

RECIPE_NAME = "theme.recipe.json"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_ZIP64_LOCATOR = b"PK\x06\x07"
_EOCD_SEARCH = 66_000


class ThemeCLIError(Exception):
    """A typed CLI refusal. ``problems`` carries every contract reason."""

    def __init__(self, code: str, detail: str = "",
                 problems: Optional[Iterable[str]] = None):
        self.code = code
        self.detail = detail
        self.problems = [str(p) for p in (problems or [])]
        super().__init__(detail or code)

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"state": "REFUSED", "code": self.code}
        if self.detail:
            payload["detail"] = self.detail[:400]
        if self.problems:
            payload["problems"] = self.problems[:100]
        return payload


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _read_json(path: Path) -> Any:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ThemeCLIError("THEME_SOURCE_MISSING", str(path)) from exc
    if size > MAX_JSON_BYTES:
        raise ThemeCLIError(
            "THEME_JSON_OVERSIZE",
            f"{path.name}: {size} bytes exceeds {MAX_JSON_BYTES}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ThemeCLIError("THEME_BAD_JSON", f"{path.name}: {exc}") from exc


def _doc_id(document: Any) -> str:
    if isinstance(document, dict):
        value = document.get("id")
        if isinstance(value, str):
            return value
    return ""


def _write_json(path: Path, document: Any) -> None:
    text = json.dumps(document, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _locate(root: Path) -> Optional[Tuple[str, Path]]:
    """``(kind, document path)`` for a pack or recipe root, if any."""
    if not root.is_dir():
        return None
    if (root / "theme.json").is_file():
        return "pack", root / "theme.json"
    recipe = root / RECIPE_NAME
    if recipe.is_file():
        return "recipe", recipe
    candidates = sorted(
        p for p in root.iterdir()
        if p.is_file() and p.suffix.lower() == ".json"
        and "recipe" in p.name.lower())
    if len(candidates) == 1:
        return "recipe", candidates[0]
    return None


def _payload_root(root: Path) -> Optional[Tuple[Path, str, Path]]:
    """The directory that actually holds the theme, allowing one wrapper dir."""
    found = _locate(root)
    if found is not None:
        return root, found[0], found[1]
    try:
        children = [p for p in root.iterdir() if p.is_dir()
                    and not p.name.startswith(".")]
    except OSError:
        return None
    if len(children) == 1:
        found = _locate(children[0])
        if found is not None:
            return children[0], found[0], found[1]
    return None


def _validate_root(kind: str, root: Path, document: Path) -> List[str]:
    if kind == "pack":
        return T.validate_theme(root)
    return T.validate_recipe(_read_json(document))


# --------------------------------------------------------------------------
# Zip bounds and extraction
# --------------------------------------------------------------------------
def _has_zip64_locator(blob: bytes) -> bool:
    """True when the data carries a ZIP64 end-of-central-directory locator."""
    return _ZIP64_LOCATOR in blob


def _tail(path: Path, length: int) -> bytes:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - length))
            return handle.read(length)
    except OSError as exc:
        raise ThemeCLIError("THEME_ZIP_CORRUPT", str(exc)) from exc


def _entry_name_problems(name: str) -> List[str]:
    problems: List[str] = []
    if not isinstance(name, str) or not name:
        return ["entry: empty name"]
    if "\x00" in name or "\\" in name:
        problems.append(f"entry {name!r}: backslash or NUL")
        return problems
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        problems.append(f"entry {name!r}: absolute path")
        return problems
    trimmed = name.rstrip("/")
    segments = trimmed.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        problems.append(f"entry {name!r}: empty, '.' or '..' segment")
    return problems


def _windows_name_problems(name: str) -> List[str]:
    """Reserved Windows basenames and trailing dot/space, per path segment.

    This mirrors ``theme._path_problems`` for manifest paths. A zip entry that
    could not be extracted on Windows (``aux.txt``, ``CON/x.png``, ``x.``) is
    refused here rather than installing a pack that fails only on one host.
    """
    problems: List[str] = []
    for segment in name.rstrip("/").split("/"):
        if not segment:
            continue
        suffix = os.path.splitext(segment)[1].lower()
        stem = segment[:-len(suffix)] if suffix else segment
        if (stem.upper() in T.WINDOWS_RESERVED
                or segment.upper() in T.WINDOWS_RESERVED):
            problems.append(f"entry {name!r}: Windows reserved name {segment!r}")
        if segment != segment.rstrip(" ."):
            problems.append(
                f"entry {name!r}: trailing dot or space in {segment!r}")
    return problems


def _duplicate_problems(names: Sequence[str]) -> List[str]:
    problems: List[str] = []
    seen_nfc: Dict[str, str] = {}
    seen_folded: Dict[str, str] = {}
    for name in names:
        nfc = unicodedata.normalize("NFC", name)
        if nfc in seen_nfc:
            problems.append(
                f"entry duplicate after NFC normalisation: "
                f"{seen_nfc[nfc]!r} and {name!r}")
        else:
            seen_nfc[nfc] = name
        folded = nfc.casefold()
        if folded in seen_folded:
            problems.append(
                f"entry duplicate after case folding: "
                f"{seen_folded[folded]!r} and {name!r}")
        else:
            seen_folded[folded] = name
    return problems


def _inspect_zip(path: Path) -> List[zipfile.ZipInfo]:
    """Every bound and safety rule, checked before a single byte is written."""
    try:
        archive_bytes = path.stat().st_size
    except OSError as exc:
        raise ThemeCLIError("THEME_SOURCE_MISSING", str(path)) from exc
    if archive_bytes > MAX_ARCHIVE_BYTES:
        raise ThemeCLIError(
            "THEME_ZIP_OVERSIZE",
            f"archive is {archive_bytes} bytes; limit {MAX_ARCHIVE_BYTES}")
    if _has_zip64_locator(_tail(path, _EOCD_SEARCH)):
        raise ThemeCLIError(
            "THEME_ZIP_ZIP64", "ZIP64 archives are not supported")
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError) as exc:
        raise ThemeCLIError("THEME_ZIP_CORRUPT", str(exc)) from exc

    with archive:
        try:
            infos = archive.infolist()
        except (OSError, zipfile.BadZipFile) as exc:
            raise ThemeCLIError("THEME_ZIP_CORRUPT", str(exc)) from exc
        if len(infos) > MAX_FILES:
            raise ThemeCLIError(
                "THEME_ZIP_OVERSIZE",
                f"{len(infos)} entries exceeds {MAX_FILES}")
        names: List[str] = []
        total = 0
        for info in infos:
            name = info.filename
            if info.flag_bits & 0x1:
                raise ThemeCLIError(
                    "THEME_ZIP_ENCRYPTED", f"entry {name!r} is encrypted")
            name_problems = _entry_name_problems(name)
            if name_problems:
                raise ThemeCLIError(
                    "THEME_ZIP_UNSAFE_PATH", "; ".join(name_problems))
            windows_problems = _windows_name_problems(name)
            if windows_problems:
                raise ThemeCLIError(
                    "THEME_ZIP_WINDOWS_NAME", "; ".join(windows_problems))
            if info.compress_type not in (zipfile.ZIP_STORED,
                                          zipfile.ZIP_DEFLATED):
                raise ThemeCLIError(
                    "THEME_ZIP_COMPRESSION",
                    f"entry {name!r}: compression {info.compress_type} "
                    f"is not stored/deflate")
            if (info.file_size >= 0xFFFFFFFF or info.compress_size >= 0xFFFFFFFF
                    or info.header_offset >= 0xFFFFFFFF):
                raise ThemeCLIError(
                    "THEME_ZIP_ZIP64", f"entry {name!r} needs ZIP64")
            if name.endswith("/"):
                continue
            mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(mode)
            if mode:
                if stat.S_ISLNK(mode):
                    raise ThemeCLIError(
                        "THEME_ZIP_SYMLINK", f"entry {name!r} is a symlink")
                if stat.S_ISDIR(mode):
                    continue
                # Many writers store permission bits only (no S_IFREG); treat
                # that as a regular file. A typed entry that is neither a
                # regular file nor a directory is refused.
                if file_type and not stat.S_ISREG(mode):
                    raise ThemeCLIError(
                        "THEME_ZIP_UNSAFE_ENTRY",
                        f"entry {name!r} is not a regular file")
                if mode & 0o111:
                    raise ThemeCLIError(
                        "THEME_ZIP_EXECUTABLE",
                        f"entry {name!r} is executable")
            suffix = os.path.splitext(name)[1].lower()
            if suffix not in T.ALLOWED_SUFFIXES:
                raise ThemeCLIError(
                    "THEME_ZIP_SUFFIX",
                    f"entry {name!r}: suffix {suffix or '(none)'} not allowed")
            if info.file_size > MAX_FILE_BYTES:
                raise ThemeCLIError(
                    "THEME_ZIP_OVERSIZE",
                    f"entry {name!r}: {info.file_size} bytes exceeds "
                    f"{MAX_FILE_BYTES}")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise ThemeCLIError(
                    "THEME_ZIP_OVERSIZE",
                    f"declared {total} bytes exceeds {MAX_TOTAL_BYTES}")
            if (info.file_size >= MAX_RATIO_MIN_BYTES
                    and info.compress_size > 0
                    and info.file_size / info.compress_size
                    > MAX_COMPRESSION_RATIO):
                raise ThemeCLIError(
                    "THEME_ZIP_RATIO",
                    f"entry {name!r}: compression ratio "
                    f"{info.file_size / info.compress_size:.0f}:1")
            names.append(name)
        duplicates = _duplicate_problems(names)
        if duplicates:
            raise ThemeCLIError("THEME_ZIP_DUPLICATE", "; ".join(duplicates))
        return infos


def _extract_zip(path: Path, destination: Path) -> None:
    infos = _inspect_zip(path)
    destination.mkdir(parents=True, exist_ok=True)
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError) as exc:
        raise ThemeCLIError("THEME_ZIP_CORRUPT", str(exc)) from exc
    total = 0
    with archive:
        for info in infos:
            if info.filename.endswith("/"):
                (destination / info.filename.rstrip("/")).mkdir(
                    parents=True, exist_ok=True)
                continue
            target = destination / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            try:
                with archive.open(info) as source, open(target, "wb") as sink:
                    while True:
                        chunk = source.read(65_536)
                        if not chunk:
                            break
                        written += len(chunk)
                        if (written > info.file_size
                                or written > MAX_FILE_BYTES
                                or total + written > MAX_TOTAL_BYTES):
                            raise ThemeCLIError(
                                "THEME_ZIP_OVERSIZE",
                                f"entry {info.filename!r} exceeded its "
                                f"declared size")
                        sink.write(chunk)
            except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
                raise ThemeCLIError(
                    "THEME_ZIP_CORRUPT", f"{info.filename}: {exc}") from exc
            if written != info.file_size:
                raise ThemeCLIError(
                    "THEME_ZIP_CORRUPT",
                    f"entry {info.filename!r}: wrote {written} of "
                    f"{info.file_size} declared bytes")
            total += written


def _copy_tree(source: Path, destination: Path) -> None:
    """Copy a directory, refusing symlinks and special files."""
    for base, dirs, files in os.walk(source):
        for name in list(dirs) + list(files):
            candidate = Path(base) / name
            if candidate.is_symlink():
                raise ThemeCLIError(
                    "THEME_SOURCE_UNSAFE",
                    f"{candidate.relative_to(source)} is a symlink")
            if name in files and not candidate.is_file():
                raise ThemeCLIError(
                    "THEME_SOURCE_UNSAFE",
                    f"{candidate.relative_to(source)} is not a regular file")
    shutil.copytree(source, destination, symlinks=False)


def _stage(source: Path, stage: Path) -> Path:
    """Materialise ``source`` as a payload directory under ``stage``."""
    payload = stage / "payload"
    suffix = source.suffix.lower()
    if source.is_dir():
        _copy_tree(source, payload)
    elif suffix == ".zip":
        _extract_zip(source, payload)
    elif suffix == ".json":
        document = _read_json(source)
        payload.mkdir(parents=True, exist_ok=True)
        _write_json(payload / RECIPE_NAME, document)
    else:
        raise ThemeCLIError(
            "THEME_SOURCE_UNSUPPORTED",
            f"{source}: pass a theme directory, a recipe .json, or a .zip")
    return payload


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def list_themes(cfg: Any) -> Dict[str, Any]:
    """Installed/bundled themes, most specific root first."""
    active = T.active_theme_id(cfg)
    seen: set = set()
    entries: List[Dict[str, Any]] = []
    for index, root in enumerate(T.theme_roots()):
        try:
            if not root.is_dir():
                continue
            children = sorted(root.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for child in children:
            if not child.is_dir() or child.name.startswith("."):
                continue
            if not _ID_RE.fullmatch(child.name) or child.name in seen:
                continue
            located = _locate(child)
            if located is None:
                continue
            kind, document = located
            seen.add(child.name)
            entry: Dict[str, Any] = {
                "id": child.name,
                "kind": kind,
                "root": str(root),
                "source": "user" if index == 0 else "bundled",
                "active": child.name == active,
            }
            try:
                problems = _validate_root(kind, child, document)
                entry["valid"] = not problems
                if problems:
                    entry["problems"] = problems[:50]
                doc = _read_json(document)
                if isinstance(doc, dict):
                    entry["label"] = str(doc.get("label") or "")
                    entry["version"] = str(doc.get("version") or "")
                    doc_id = doc.get("id")
                    if doc_id != child.name:
                        entry["valid"] = False
                        entry.setdefault("problems", []).append(
                            f"id {doc_id!r} does not match directory "
                            f"{child.name!r}")
            except ThemeCLIError as exc:
                entry["valid"] = False
                entry["problems"] = exc.problems or [exc.detail or exc.code]
            entries.append(entry)
    return {"state": "OK", "active": active, "themes": entries}


def _command_validate(path: str) -> int:
    source = Path(path)
    if not source.exists():
        raise ThemeCLIError("THEME_SOURCE_MISSING", str(source))
    if source.is_dir():
        located = _payload_root(source)
        if located is None:
            raise ThemeCLIError(
                "THEME_NOT_FOUND",
                f"{source}: no theme.json or {RECIPE_NAME}")
        root, kind, document = located
        problems = _validate_root(kind, root, document)
        ident = _doc_id(_read_json(document))
    elif source.suffix.lower() == ".zip":
        with tempfile.TemporaryDirectory(prefix="office-theme-validate-") as tmp:
            payload = _stage(source, Path(tmp))
            located = _payload_root(payload)
            if located is None:
                raise ThemeCLIError(
                    "THEME_NOT_FOUND",
                    f"{source}: archive holds no theme.json or {RECIPE_NAME}")
            root, kind, document = located
            problems = _validate_root(kind, root, document)
            ident = _doc_id(_read_json(document))
    elif source.suffix.lower() == ".json":
        if source.name == "theme.json":
            problems = T.validate_theme(source.parent)
            kind = "pack"
            ident = _doc_id(_read_json(source))
        else:
            document = _read_json(source)
            problems = T.validate_recipe(document)
            kind = "recipe"
            ident = _doc_id(document)
    else:
        raise ThemeCLIError(
            "THEME_SOURCE_UNSUPPORTED",
            f"{source}: pass a theme directory, recipe .json, or .zip")

    if problems:
        print(f"INVALID {source}")
        for problem in problems:
            print(f"- {problem}")
        return 2
    print(f"OK {kind} {ident or ''} {source}".rstrip())
    return 0


def _builder_hint(theme_id: str, target: Path) -> str:
    return (f'python3 scripts/office_theme_build.py --source "<purchase dir>" '
            f'--out "{target}"')


def _command_install(cfg: Any, path: str, force: bool,
                     dry: bool = False) -> int:
    source = Path(path)
    if not source.exists():
        raise ThemeCLIError("THEME_SOURCE_MISSING", str(source))
    root = T.user_theme_root()
    # Stage beside the theme root (same filesystem, so the final move is an
    # atomic rename) but do not create ``themes`` until the staged copy has
    # validated: a refused archive leaves no theme tree behind.
    staging_parent = root.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=staging_parent,
                                     prefix=".theme-install-") as tmp:
        stage = Path(tmp)
        payload = _stage(source, stage)
        located = _payload_root(payload)
        if located is None:
            raise ThemeCLIError(
                "THEME_NOT_FOUND",
                f"{source}: no theme.json or {RECIPE_NAME}")
        staged_root, kind, document = located
        problems = _validate_root(kind, staged_root, document)
        if problems:
            raise ThemeCLIError("THEME_INSTALL_INVALID", problems=problems)
        staged_doc = _read_json(document)
        theme_id = staged_doc.get("id") if isinstance(staged_doc, dict) else None
        if not isinstance(theme_id, str) or not _ID_RE.fullmatch(theme_id):
            raise ThemeCLIError(
                "THEME_INSTALL_INVALID",
                problems=["id: must match ^[a-z0-9][a-z0-9-]{0,39}$"])

        target = root / theme_id
        existed = target.exists()
        if dry:
            print(json.dumps({
                "state": "preview",
                "would": f"install theme {theme_id}",
                "id": theme_id,
                "kind": kind,
                "path": str(target),
                "force": existed,
            }, indent=2, sort_keys=True))
            return 0
        if existed and not force:
            raise ThemeCLIError(
                "THEME_EXISTS",
                f"{target} already exists; re-run with --force to replace it")
        root.mkdir(parents=True, exist_ok=True)
        backup = stage / "backup"
        if existed:
            os.replace(target, backup)
        try:
            os.replace(staged_root, target)
        except OSError as exc:
            if existed and not target.exists():
                os.replace(backup, target)
            raise ThemeCLIError(
                "THEME_INSTALL_FAILED", f"{target}: {exc}") from exc

        located_after = _payload_root(target)
        post: List[str]
        if located_after is None:
            post = ["installed theme has no theme.json or recipe"]
        else:
            after_root, after_kind, after_doc = located_after
            post = _validate_root(after_kind, after_root, after_doc)
        if post:
            shutil.rmtree(target, ignore_errors=True)
            if existed:
                os.replace(backup, target)
            raise ThemeCLIError("THEME_INSTALL_INVALID", problems=post)

    payload_out: Dict[str, Any] = {
        "state": "OK",
        "id": theme_id,
        "kind": kind,
        "path": str(target),
    }
    if kind == "recipe":
        payload_out["next"] = _builder_hint(theme_id, target)
        payload_out["note"] = (
            "recipe installed; the atlases are built locally from the owner's "
            "purchase and are never shared")
    print(json.dumps(payload_out, indent=2, sort_keys=True))
    return 0


def _find_any(theme_id: str) -> Optional[Tuple[Path, str, Path]]:
    if not isinstance(theme_id, str) or not _ID_RE.fullmatch(theme_id):
        return None
    for root in T.theme_roots():
        candidate = root / theme_id
        located = _locate(candidate)
        if located is not None:
            return candidate, located[0], located[1]
    return None


_RECIPE_FIELDS: Sequence[str] = (
    "format", "schema", "id", "label", "version", "credit", "license",
    "template_version",
)


def _recipe_from_pack(root: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    missing = [key for key in _RECIPE_FIELDS if key not in manifest]
    if missing:
        raise ThemeCLIError(
            "THEME_EXPORT_INVALID",
            problems=[f"theme.json: missing required field {key!r}"
                      for key in missing])
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    agents = manifest.get("agents") if isinstance(manifest.get("agents"), dict) else {}
    parts = manifest.get("parts") if isinstance(manifest.get("parts"), dict) else {}
    if "agents.json" in files:
        try:
            agents_doc = json.loads(
                (root / "agents.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            agents_doc = None
        if isinstance(agents_doc, dict):
            if isinstance(agents_doc.get("recipes"), dict):
                agents = agents_doc["recipes"]
            if isinstance(agents_doc.get("parts"), dict):
                parts = agents_doc["parts"]
    return {
        "format": manifest["format"],
        "schema": manifest["schema"],
        "id": manifest["id"],
        "label": manifest["label"],
        "version": manifest["version"],
        "credit": manifest["credit"],
        "license": manifest["license"],
        "source": manifest.get("source", ""),
        "template_version": manifest["template_version"],
        "tokens": dict(manifest.get("tokens") or {}),
        "slices": dict(manifest.get("slices") or {}),
        "parts": dict(parts),
        "agents": {slug: dict(recipe) for slug, recipe in agents.items()},
    }


def _command_export(cfg: Any, theme_id: str, out: str,
                    dry: bool = False) -> int:
    found = _find_any(theme_id)
    if found is None:
        raise ThemeCLIError(
            "THEME_NOT_FOUND",
            f"theme {theme_id!r} is not installed or bundled")
    root, kind, document = found
    if kind == "pack":
        # Validate the LOCAL pack before deriving a recipe: a hand-edited pack
        # with a missing field is a typed refusal, never a KeyError traceback.
        problems = T.validate_theme(root)
        if problems:
            raise ThemeCLIError("THEME_EXPORT_INVALID", problems=problems)
        manifest = _read_json(document)
        if not isinstance(manifest, dict):
            raise ThemeCLIError("THEME_EXPORT_INVALID",
                                problems=["theme.json: must be an object"])
        recipe = _recipe_from_pack(root, manifest)
    else:
        recipe = _read_json(document)
        if not isinstance(recipe, dict):
            raise ThemeCLIError("THEME_INSTALL_INVALID",
                                problems=["recipe: must be an object"])
    problems = T.validate_recipe(recipe)
    if problems:
        raise ThemeCLIError("THEME_EXPORT_INVALID", problems=problems)

    destination = Path(out)
    if destination.is_dir():
        raise ThemeCLIError(
            "THEME_EXPORT_INVALID", f"{destination} is a directory")
    if dry:
        print(json.dumps({
            "state": "preview",
            "would": f"export theme {theme_id} recipe",
            "id": theme_id,
            "path": str(destination),
            "entries": [RECIPE_NAME],
        }, indent=2, sort_keys=True))
        return 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(recipe, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = destination.with_name(
        f".{destination.name}.tmp-{os.getpid()}")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            info = zipfile.ZipInfo(RECIPE_NAME, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data)
        os.replace(temporary, destination)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise ThemeCLIError(
            "THEME_EXPORT_FAILED", f"{destination}: {exc}") from exc

    print(json.dumps({
        "state": "OK",
        "id": theme_id,
        "kind": "recipe",
        "path": str(destination),
        "entries": [RECIPE_NAME],
    }, indent=2, sort_keys=True))
    return 0


# --------------------------------------------------------------------------
# Entry point (wired under the existing ``office`` family)
# --------------------------------------------------------------------------
def run(cfg: Any, args: Any) -> int:
    """``qubit office theme <list|validate|install|export>``."""
    command = getattr(args, "office_theme_command", None) or "list"
    try:
        if command == "list":
            print(json.dumps(list_themes(cfg), indent=2, sort_keys=True))
            return 0
        if command == "validate":
            return _command_validate(args.path)
        dry = bool(getattr(args, "dry_run", False))
        if command == "install":
            return _command_install(cfg, args.path,
                                    bool(getattr(args, "force", False)), dry)
        if command == "export":
            return _command_export(cfg, args.id, args.out, dry)
        print(json.dumps({
            "state": "REFUSED",
            "code": "THEME_USAGE",
            "detail": "unknown office theme command",
            "commands": ["list", "validate", "install", "export"],
        }, indent=2))
        return 2
    except ThemeCLIError as exc:
        print(json.dumps(exc.as_dict(), indent=2))
        return 2
    except OSError as exc:
        # A permission/unwritable-path failure is a refusal, not a traceback.
        print(json.dumps({
            "state": "REFUSED",
            "code": "THEME_IO_ERROR",
            "detail": str(exc)[:400],
        }, indent=2))
        return 2
