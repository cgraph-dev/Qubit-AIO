# Hermes / OpenClaw Skill Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `hermes` and `openclaw` git-backed skill sources to the existing Qubit skills hub, ported from OpenJarvis's resolvers, without replacing the hub's auth, cache, trust, or installer.

**Architecture:** One new module `tools/skills_hub_git_sources.py` with a `GitSkillSource` base (lazy clone/pull, commit pin, stale-cache fallback, frontmatter preview, bundle fetch) and two subclasses (`HermesGitSource`, `OpenClawGitSource`). Register both in `tools/skills_hub_search.py::create_source_router`. Identifiers are `hermes:<name>` / `openclaw:<owner>/<name>` (bare names accepted when unique).

**Tech Stack:** Python, git subprocess, existing `SkillSource`/`SkillMeta`/`SkillBundle`, `_parse_frontmatter`, pytest with local git repos.

**Spec:** `docs/superpowers/specs/2026-10-26-runtime-boundary-and-openjarvis-design.md` §"Reviewed upstream adapters"

**Upstream:** `open-jarvis/OpenJarvis@0583901`, `src/openjarvis/skills/sources/{base,hermes,openclaw}.py`, Apache-2.0. Only the resolver semantics are reused; record provenance before merging.

## Global Constraints

- Do not modify/replace `tools/skills_hub_*` behavior beyond adding the two sources; the installer, trust, SSRF guards and bundle validation stay authoritative.
- No network in tests: every test uses a local `git init` repo and a temp cache root.
- Lazy sync: a source only clones on the first `search`/`inspect`/`fetch`; a failed refresh keeps the existing cache and sets `refresh_warning`; a failed first clone raises.
- Commit SHA is recorded in every `SkillMeta.extra` and `SkillBundle.metadata`.
- Never copy `.git`, `_meta.json` (OpenClaw sidecar is metadata), or symlinks into a bundle.
- Identify skills by the directory name and frontmatter `name`; reject unsafe rel paths.
- Commit as you go; one worktree.

## Review Focus

- **First run offline:** with a bad repo URL and no cache, the source must return no results (logged), never hang or write a partial cache.
- **Stale cache:** a pull failure must keep serving the cached skills and expose a warning; it must not silently look healthy.
- **Ambiguous bare name:** `hermes:same-name` with two categories must refuse with a clear error (or require category), never pick one arbitrarily.
- **Symlink escape:** a symlinked file inside a skill must not be read into the bundle.
- **Oversized bundle:** a skill with many files must respect the hub's existing bundle limits (do not invent a second policy; let the installer/hub enforce).

---

### Task 1: Provenance record

**Files:**
- Modify: `THIRD_PARTY_NOTICES.md`

- [ ] **Step 1: Append the entry**

```markdown
## OpenJarvis skill source resolvers (https://github.com/open-jarvis/OpenJarvis) — Apache-2.0
- Pinned: `05839014127772bd077ba9ba2a2393a007f63ac6`; reference only, not vendored.
- Adapted: `src/openjarvis/skills/sources/base.py`, `hermes.py`, `openclaw.py`
  (clone/pull cache, skill walk, frontmatter preview, commit pin, refresh fallback,
  `_meta.json` sidecar). Qubit keeps its own hub, trust, security and installer.
- Local files: `qubit_runtime/tools/skills_hub_git_sources.py`,
  `qubit_runtime/tests/tools/test_skills_hub_git_sources.py`.
```

- [ ] **Step 2: Commit**

```bash
git add THIRD_PARTY_NOTICES.md
git commit -m "docs: record the OpenJarvis skill-resolver provenance"
```

### Task 2: Git source base + Hermes

**Files:**
- Create: `qubit_runtime/tools/skills_hub_git_sources.py`
- Test: `qubit_runtime/tests/tools/test_skills_hub_git_sources.py`

**Interfaces:**
- Produces: `HermesGitSource(cache_root=None, repo_url=None)` with `SOURCE_ID="hermes"`,
  `sync()`, `search(query, limit)`, `inspect(identifier)`, `fetch(identifier)`,
  `current_revision(identifier)`, `refresh_warning`.

- [ ] **Step 1: Write the failing test (local repo)**

```python
# tests/tools/test_skills_hub_git_sources.py
import subprocess
from pathlib import Path

from tools.skills_hub_git_sources import HermesGitSource


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path, files):
    repo = tmp_path / "origin"
    repo.mkdir()
    _git(repo, "init", "-q")
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "init")
    return repo


def _hermes_repo(tmp_path):
    return _repo(tmp_path, {
        "skills/research/DESCRIPTION.md": "category notes",
        "skills/research/demo/SKILL.md": "---\nname: demo\ndescription: Demo skill.\n---\n# demo\n",
        "skills/research/demo/references/notes.md": "notes\n",
    })


def test_hermes_search_inspect_fetch(tmp_path):
    origin = _hermes_repo(tmp_path)
    src = HermesGitSource(cache_root=tmp_path / "cache", repo_url=str(origin))
    src.sync()

    found = src.search("demo", limit=5)
    assert [m.name for m in found] == ["demo"]
    assert found[0].identifier == "hermes:demo"
    assert found[0].extra["commit"] == src.current_revision("hermes:demo")

    meta = src.inspect("hermes:demo")
    assert meta and meta.description == "Demo skill."

    bundle = src.fetch("hermes:demo")
    assert bundle is not None
    assert bundle.files["SKILL.md"].startswith("---")
    assert bundle.files["references/notes.md"] == "notes\n"
    assert bundle.metadata["commit"] == src.current_revision("hermes:demo")
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd qubit_runtime && TMPDIR=/tmp/opencode/hs-tmp <venv-python> -m pytest tests/tools/test_skills_hub_git_sources.py -q --basetemp=/tmp/opencode/hs-base`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the base + Hermes**

```python
# tools/skills_hub_git_sources.py
"""Git-backed skill sources (Hermes / OpenClaw), adapted from OpenJarvis.

Provenance: open-jarvis/OpenJarvis@0583901 src/openjarvis/skills/sources/
(Apache-2.0, THIRD_PARTY_NOTICES.md). Only the resolver semantics are reused; the
Qubit hub keeps auth, cache, trust, security and the installer.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from qubit_constants import get_qubit_home
from tools.skills_hub_models import SkillBundle, SkillMeta, SkillSource, _parse_frontmatter

logger = logging.getLogger("tools.skills_hub")

_SKIP_NAMES = {".git", "_meta.json"}


class GitSkillSource(SkillSource):
    SOURCE_ID = ""
    REPO_URL = ""
    REPO_ENV = ""
    TRUST_LEVEL = "community"

    def __init__(self, cache_root: Path | None = None, repo_url: str | None = None) -> None:
        self._cache_root = Path(cache_root) if cache_root is not None else (
            get_qubit_home() / "cache" / "skill-cache" / self.SOURCE_ID)
        self._repo_url = repo_url or (os.environ.get(self.REPO_ENV) if self.REPO_ENV else None) or self.REPO_URL
        self.refresh_warning = ""

    def cache_dir(self) -> Path:
        return self._cache_root

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self._cache_root), *args],
                              capture_output=True, text=True, check=check)

    def sync(self) -> None:
        """Refresh the cache; an initial clone must succeed, a pull may degrade."""
        self.refresh_warning = ""
        if (self._cache_root / ".git").exists():
            refreshed = subprocess.run(
                ["git", "-C", str(self._cache_root), "pull", "--ff-only"],
                capture_output=True, text=True)
            if refreshed.returncode != 0:
                if not self._list_skills():
                    raise RuntimeError(
                        f"Could not refresh {self.SOURCE_ID} and the cache is empty: "
                        f"{(refreshed.stderr or '').strip()[:300]}")
                self.refresh_warning = (
                    f"Could not refresh {self.SOURCE_ID}; using the existing skill cache, "
                    "which may be stale.")
                logger.warning(self.refresh_warning)
            return
        self._cache_root.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", self._repo_url, str(self._cache_root)],
                       check=True, capture_output=True, text=True)

    def _ensure(self) -> bool:
        if not (self._cache_root / ".git").exists():
            try:
                self.sync()
            except Exception as exc:
                logger.warning("skill source %s unavailable: %s", self.SOURCE_ID, exc)
                return False
        return True

    def current_revision(self, identifier: str = "") -> str:
        if not (self._cache_root / ".git").exists():
            return ""
        result = self._git("rev-parse", "HEAD", check=False)
        return result.stdout.strip() if result.returncode == 0 else ""

    # -- layout hooks -----------------------------------------------------
    def _iter_skills(self) -> Iterable[Tuple[str, Path]]:
        """Yield (category, skill_dir) for this source's layout."""
        raise NotImplementedError

    def _canonical(self, category: str, name: str) -> str:
        return f"{self.SOURCE_ID}:{name}"

    # -- SkillSource ------------------------------------------------------
    def _meta(self, category: str, skill_dir: Path) -> Optional[SkillMeta]:
        md = skill_dir / "SKILL.md"
        if not md.is_file():
            return None
        try:
            fm = _parse_frontmatter(md.read_text(encoding="utf-8-sig"))
        except OSError:
            return None
        name = str(fm.get("name") or skill_dir.name)
        return SkillMeta(
            name=name, description=str(fm.get("description") or ""),
            source=self.SOURCE_ID, identifier=self._canonical(category, skill_dir.name),
            trust_level=self.TRUST_LEVEL, path=f"{category}/{skill_dir.name}",
            extra={"category": category, "commit": self.current_revision(),
                   "repo": self._repo_url},
        )

    def _list_skills(self) -> List[SkillMeta]:
        return [m for category, path in self._iter_skills() if (m := self._meta(category, path))]

    def search(self, query: str, limit: int = 10) -> List[SkillMeta]:
        if not self._ensure():
            return []
        q = query.strip().lower()
        out: List[SkillMeta] = []
        for meta in self._list_skills():
            hay = f"{meta.name} {meta.description} {meta.extra.get('category', '')}".lower()
            if q and q not in hay:
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
        exact: List[Tuple[str, Path, SkillMeta]] = []
        names: List[Tuple[str, Path, SkillMeta]] = []
        for category, path in self._iter_skills():
            meta = self._meta(category, path)
            if meta is None:
                continue
            if raw in (f"{category}/{path.name}", path.name, meta.name):
                if "/" in raw and raw.split("/")[-1] == path.name and raw.split("/")[0] == category:
                    exact.append((category, path, meta))
                elif raw == path.name or raw == meta.name:
                    names.append((category, path, meta))
        chosen = exact or names
        if len(chosen) == 1:
            return chosen[0]
        if len(chosen) > 1:
            raise ValueError(
                f"{identifier!r} is ambiguous; use {self.SOURCE_ID}:<category>/<name> "
                f"({', '.join(f'{c}/{p.name}' for c, p, _ in chosen[:5])})")
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
        files: Dict[str, str] = {}
        for path in sorted(skill_dir.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(skill_dir).as_posix()
            if rel in _SKIP_NAMES or rel.split("/")[0] in _SKIP_NAMES:
                continue
            try:
                files[rel] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                logger.warning("skipping unreadable skill file %s", path)
        if "SKILL.md" not in files:
            return None
        return SkillBundle(
            name=meta.name, files=files, source=self.SOURCE_ID, identifier=meta.identifier,
            trust_level=self.TRUST_LEVEL,
            metadata={"commit": meta.extra["commit"], "category": category,
                      "repo": self._repo_url, "sidecar": self._sidecar(skill_dir)},
        )

    def _sidecar(self, skill_dir: Path) -> dict:
        return {}


class HermesGitSource(GitSkillSource):
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
```

- [ ] **Step 4: Run and confirm pass**

Run: `cd qubit_runtime && TMPDIR=/tmp/opencode/hs-tmp <venv-python> -m pytest tests/tools/test_skills_hub_git_sources.py -q --basetemp=/tmp/opencode/hs-base`
Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add qubit_runtime/tools/skills_hub_git_sources.py qubit_runtime/tests/tools/test_skills_hub_git_sources.py
git commit -m "feat(skills): add the Hermes git skill source"
```

### Task 3: OpenClaw source + sidecar

**Files:**
- Modify: `qubit_runtime/tools/skills_hub_git_sources.py`
- Test: `qubit_runtime/tests/tools/test_skills_hub_git_sources.py`

- [ ] **Step 1: Add the failing test**

```python
def test_openclaw_owner_layout_and_sidecar(tmp_path):
    origin = _repo(tmp_path, {
        "skills/acme/demo/SKILL.md": "---\nname: demo\ndescription: OpenClaw demo.\n---\n",
        "skills/acme/demo/_meta.json": '{"downloads": 7}',
        "skills/acme/demo/scripts/run.sh": "echo hi\n",
    })
    from tools.skills_hub_git_sources import OpenClawGitSource
    src = OpenClawGitSource(cache_root=tmp_path / "cache", repo_url=str(origin))
    src.sync()

    meta = src.inspect("openclaw:acme/demo")
    assert meta and meta.extra["category"] == "acme"
    bundle = src.fetch("openclaw:acme/demo")
    assert bundle and "_meta.json" not in bundle.files
    assert bundle.metadata["sidecar"] == {"downloads": 7}
    assert bundle.files["scripts/run.sh"] == "echo hi\n"
```

- [ ] **Step 2: Implement OpenClaw**

```python
class OpenClawGitSource(GitSkillSource):
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
        return f"openclaw:{category}/{name}"

    def _sidecar(self, skill_dir: Path) -> dict:
        import json
        path = skill_dir / "_meta.json"
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}
```

- [ ] **Step 3: Run the file**

Run: `cd qubit_runtime && TMPDIR=/tmp/opencode/hs-tmp <venv-python> -m pytest tests/tools/test_skills_hub_git_sources.py -q --basetemp=/tmp/opencode/hs-base`
Expected: `2 passed`.

- [ ] **Step 4: Commit**

```bash
git add qubit_runtime/tools/skills_hub_git_sources.py qubit_runtime/tests/tools/test_skills_hub_git_sources.py
git commit -m "feat(skills): add the OpenClaw git skill source"
```

### Task 4: Refresh fallback + registration

**Files:**
- Modify: `qubit_runtime/tools/skills_hub_search.py`
- Test: `qubit_runtime/tests/tools/test_skills_hub_git_sources.py`

- [ ] **Step 1: Add tests**

```python
def test_pull_failure_keeps_cache_and_warns(tmp_path):
    origin = _hermes_repo(tmp_path)
    src = HermesGitSource(cache_root=tmp_path / "cache", repo_url=str(origin))
    src.sync()
    assert src.search("demo")
    _git(src.cache_dir(), "remote", "set-url", "origin", str(tmp_path / "gone"))
    src.sync()
    assert src.search("demo")
    assert "stale" in src.refresh_warning


def test_router_registers_both_sources():
    from tools.skills_hub_search import create_source_router
    ids = {s.source_id() for s in create_source_router()}
    assert {"hermes", "openclaw"} <= ids
```

- [ ] **Step 2: Register both in `create_source_router`**

```python
from tools.skills_hub_git_sources import HermesGitSource, OpenClawGitSource
...
        GitHubSource(auth=auth, extra_taps=TapsManager().list_taps()),
        HermesGitSource(),
        OpenClawGitSource(),
        ClawHubSource(),
```

- [ ] **Step 3: Run the file**

Run: `cd qubit_runtime && TMPDIR=/tmp/opencode/hs-tmp <venv-python> -m pytest tests/tools/test_skills_hub_git_sources.py -q --basetemp=/tmp/opencode/hs-base`
Expected: `4 passed`.

- [ ] **Step 4: Focused regression**

Run: `cd qubit_runtime && TMPDIR=/tmp/opencode/hs-tmp <venv-python> -m pytest tests/tools/test_skills_hub.py tests/tools/test_skills_hub_search.py -q --basetemp=/tmp/opencode/hs-base2`
Expected: all green (no behavior change for existing sources).

- [ ] **Step 5: Commit**

```bash
git add qubit_runtime/tools/skills_hub_search.py qubit_runtime/tests/tools/test_skills_hub_git_sources.py
git commit -m "feat(skills): register the Hermes and OpenClaw git sources"
```

---

## Exit criteria

- `tests/tools/test_skills_hub_git_sources.py` green (local repos only).
- Existing skills-hub tests unchanged.
- `hermes:<name>` and `openclaw:<owner>/<name>` search/inspect/fetch/install work.
- A failed refresh serves the stale cache with `refresh_warning`.
- `THIRD_PARTY_NOTICES.md` carries the OpenJarvis pin.
