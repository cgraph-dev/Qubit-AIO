"""Git-backed skill sources (Hermes / OpenClaw), adapted from OpenJarvis.

Provenance: open-jarvis/OpenJarvis@0583901 ``src/openjarvis/skills/sources/``
(Apache-2.0; see THIRD_PARTY_NOTICES.md). Only the resolver semantics are reused —
clone/pull a cache, walk the source layout, preview frontmatter, pin the commit,
fall back to a stale cache on a failed refresh, and read OpenClaw's ``_meta.json``.
The Qubit hub keeps its auth, cache, trust, security and installer.

Identifiers: ``hermes:<name>`` / ``openclaw:<owner>/<name>`` (bare names accepted
when unique; an ambiguous bare name raises with the category-qualified forms).
``search`` never clones: a missing cache returns [] so an unrelated hub search
does not download either repository. ``prepare`` (explicit ``--source``) and
``inspect``/``fetch`` (explicit identifier) do the lazy clone.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from qubit_constants import get_qubit_home
from tools.skills_hub_models import SkillBundle, SkillMeta, SkillSource, _parse_frontmatter

logger = logging.getLogger("tools.skills_hub")

_SKIP_NAMES = frozenset({".git", "_meta.json"})


class GitSkillSource(SkillSource):
    """A skill source backed by a git repository; subclasses define the layout."""

    SOURCE_ID = ""
    REPO_URL = ""
    REPO_ENV = ""
    TRUST_LEVEL = "community"

    def __init__(self, cache_root: Path | None = None, repo_url: str | None = None) -> None:
        self._cache_root = (
            Path(cache_root) if cache_root is not None
            else get_qubit_home() / "cache" / "skill-cache" / self.SOURCE_ID
        )
        self._repo_url = (
            repo_url
            or (os.environ.get(self.REPO_ENV) if self.REPO_ENV else None)
            or self.REPO_URL
        )
        self.refresh_warning = ""

    def cache_dir(self) -> Path:
        return self._cache_root

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self._cache_root), *args],
            capture_output=True, text=True, check=check,
            stdin=subprocess.DEVNULL,
        )

    def sync(self) -> None:
        """Clone on first use; refresh best-effort (a pull failure keeps the cache)."""
        self.refresh_warning = ""
        if (self._cache_root / ".git").exists():
            refreshed = self._git("pull", "--ff-only", check=False)
            if refreshed.returncode != 0:
                if not self._list_skills():
                    raise RuntimeError(
                        f"Could not refresh {self.SOURCE_ID} and the cache is empty: "
                        f"{(refreshed.stderr or '').strip()[:300]}"
                    )
                self.refresh_warning = (
                    f"Could not refresh {self.SOURCE_ID}; using the existing skill cache, "
                    "which may be stale."
                )
                logger.warning(self.refresh_warning)
            return
        self._cache_root.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", self._repo_url, str(self._cache_root)],
            check=True, capture_output=True, text=True,
            stdin=subprocess.DEVNULL,
        )

    def prepare(self) -> bool:
        """Make the cache usable for an explicit search; never raises."""
        try:
            self.sync()
            return True
        except Exception as exc:
            logger.warning("skill source %s unavailable: %s", self.SOURCE_ID, exc)
            return False

    def _ensure(self) -> bool:
        if (self._cache_root / ".git").exists():
            return True
        return self.prepare()

    def current_revision(self, identifier: str = "") -> str:
        if not (self._cache_root / ".git").exists():
            return ""
        result = self._git("rev-parse", "HEAD", check=False)
        return result.stdout.strip() if result.returncode == 0 else ""

    # -- layout hooks -----------------------------------------------------
    def _iter_skills(self) -> Iterable[Tuple[str, Path]]:
        """Yield ``(category, skill_dir)`` for this source's layout."""
        raise NotImplementedError

    def _canonical(self, category: str, name: str) -> str:
        return f"{self.SOURCE_ID}:{name}"

    def _sidecar(self, skill_dir: Path) -> dict:
        return {}

    # -- SkillSource ------------------------------------------------------
    def _meta(self, category: str, skill_dir: Path) -> Optional[SkillMeta]:
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            return None
        try:
            frontmatter = _parse_frontmatter(skill_md.read_text(encoding="utf-8-sig"))
        except OSError:
            return None
        name = str(frontmatter.get("name") or skill_dir.name)
        return SkillMeta(
            name=name,
            description=str(frontmatter.get("description") or ""),
            source=self.SOURCE_ID,
            identifier=self._canonical(category, skill_dir.name),
            trust_level=self.TRUST_LEVEL,
            path=f"{category}/{skill_dir.name}",
            extra={"category": category, "commit": self.current_revision(),
                   "repo": self._repo_url},
        )

    def _list_skills(self) -> List[SkillMeta]:
        return [
            meta for category, path in self._iter_skills()
            if (meta := self._meta(category, path)) is not None
        ]

    def search(self, query: str, limit: int = 10) -> List[SkillMeta]:
        # No cache means no clone here: the hub fans out to every source, and an
        # unrelated search must not download a repository. ``prepare()`` handles
        # an explicit ``--source``; inspect/fetch clone for an explicit id.
        if not (self._cache_root / ".git").exists():
            return []
        q = query.strip().lower()
        out: List[SkillMeta] = []
        for meta in self._list_skills():
            haystack = f"{meta.name} {meta.description} {meta.extra.get('category', '')}".lower()
            if q and q not in haystack:
                continue
            out.append(meta)
            if len(out) >= limit:
                break
        return out

    def _find(self, identifier: str) -> Optional[Tuple[str, Path, SkillMeta]]:
        raw = identifier.split(":", 1)[1] if ":" in identifier else identifier
        raw = raw.strip().strip("/")
        if not raw:
            return None
        matches: Dict[Path, Tuple[str, Path, SkillMeta]] = {}
        qualified = "/" in raw
        for category, path in self._iter_skills():
            meta = self._meta(category, path)
            if meta is None:
                continue
            rel = f"{category}/{path.name}"
            if qualified:
                if raw != rel:
                    continue
            elif raw not in (path.name, meta.name):
                continue
            matches[path] = (category, path, meta)
        chosen = list(matches.values())
        if len(chosen) == 1:
            return chosen[0]
        if len(chosen) > 1:
            raise ValueError(
                f"{identifier!r} is ambiguous; use {self.SOURCE_ID}:<category>/<name> "
                f"({', '.join(c + '/' + p.name for c, p, _ in chosen[:5])})"
            )
        return None

    def inspect(self, identifier: str) -> Optional[SkillMeta]:
        if not self._ensure():
            return None
        found = self._find(identifier)
        return found[2] if found else None

    def fetch(self, identifier: str) -> Optional[SkillBundle]:
        if not self._ensure():
            return None
        found = self._find(identifier)
        if found is None:
            return None
        category, skill_dir, meta = found
        files: Dict[str, object] = {}
        for path in sorted(skill_dir.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(skill_dir).as_posix()
            if rel in _SKIP_NAMES or rel.split("/", 1)[0] in _SKIP_NAMES:
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                logger.warning("skipping unreadable skill file %s", path)
                continue
            try:
                files[rel] = raw.decode("utf-8")
            except UnicodeDecodeError:
                files[rel] = raw
        if "SKILL.md" not in files:
            return None
        return SkillBundle(
            name=meta.name,
            files=files,
            source=self.SOURCE_ID,
            identifier=meta.identifier,
            trust_level=self.TRUST_LEVEL,
            metadata={
                "commit": meta.extra.get("commit", ""),
                "category": category,
                "repo": self._repo_url,
                "sidecar": self._sidecar(skill_dir),
            },
        )


class HermesGitSource(GitSkillSource):
    """``NousResearch/hermes-agent`` skills: ``skills/<category>/<skill>/SKILL.md``."""

    SOURCE_ID = "hermes"
    REPO_URL = "https://github.com/NousResearch/hermes-agent.git"
    REPO_ENV = "QUBIT_HERMES_SKILLS_REPO"

    def _iter_skills(self) -> Iterable[Tuple[str, Path]]:
        root = self._cache_root / "skills"
        if not root.is_dir():
            return
        for category in sorted(p for p in root.iterdir() if p.is_dir()):
            for skill in sorted(p for p in category.iterdir() if p.is_dir()):
                yield category.name, skill


class OpenClawGitSource(GitSkillSource):
    """``openclaw/skills``: ``skills/<owner>/<skill>/SKILL.md`` + ``_meta.json``."""

    SOURCE_ID = "openclaw"
    REPO_URL = "https://github.com/openclaw/skills.git"
    REPO_ENV = "QUBIT_OPENCLAW_SKILLS_REPO"

    def _iter_skills(self) -> Iterable[Tuple[str, Path]]:
        root = self._cache_root / "skills"
        if not root.is_dir():
            return
        for owner in sorted(p for p in root.iterdir() if p.is_dir()):
            for skill in sorted(p for p in owner.iterdir() if p.is_dir()):
                yield owner.name, skill

    def _canonical(self, category: str, name: str) -> str:
        return f"{self.SOURCE_ID}:{category}/{name}"

    def _sidecar(self, skill_dir: Path) -> dict:
        path = skill_dir / "_meta.json"
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}


__all__ = ["GitSkillSource", "HermesGitSource", "OpenClawGitSource"]
