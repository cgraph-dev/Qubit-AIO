"""Generic adapter engine: make Qubit host any other agent's ecosystem content.

Design notes live in ``__init__.py``. This module holds the mechanism; adapters hold the data.

The engine is deliberately small and has four parts:

* :class:`ForeignAdapter` — the data describing one foreign agent's naming.
* :class:`ImportAliaser` — a ``sys.meta_path`` finder that resolves the foreign module names.
* :class:`EnvAliaser` / :class:`SymbolAliaser` — environment and attribute aliasing.
* :class:`SkillLoader` — enumerate the foreign agent's skill directories (``SKILL.md``).
* :class:`McpConfigLoader` — read the foreign agent's MCP server entries from its config.
* :class:`AdapterRegistry` — holds adapters and answers "does this directory belong to one?"

Everything is idempotent and reversible; ``reset()`` restores the process to stock Python.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import os
import pathlib
import sys
import threading
import types
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


class AgentCompatUnresolved(ImportError):
    """A foreign module name was requested that no adapter can resolve.

    Raised loudly on purpose. Returning a placeholder module here would make foreign
    content appear to work while doing nothing — the failure mode this whole layer exists
    to prevent.
    """


# ----------------------------------------------------------------------------------- adapter data

@dataclass(frozen=True)
class ForeignAdapter:
    """One foreign agent's naming conventions, as data.

    Adding support for another agent means constructing one of these — usually with no new
    code. Every mapping is an allowlist; nothing here performs a blanket substitution.

    Attributes
    ----------
    name:
        Short identifier, e.g. ``"hermes"``. Used in messages and by ``active_adapters()``.
    module_aliases:
        Exact foreign module name -> real module name, resolved in THIS process.
    prefix_aliases:
        Foreign module-name PREFIX -> real prefix, for decomposed module families where
        enumerating every sibling would rot (``("hermes_state_", "qubit_state_")``).
    symbol_aliases:
        Real module name -> ``{foreign_symbol: real_symbol}``. Applied as attributes on the
        already-imported module, for cases where the module kept its name but an attribute
        was renamed.
    env_aliases:
        Foreign env var -> real env var. Values are mirrored foreign -> real only, and a
        real variable that is already set is never overwritten.
    entry_point_groups:
        Entry-point groups this agent's plugin ecosystem publishes under. Qubit keeps
        accepting them so pip-installed plugins stay discoverable.
    detection_markers:
        Module prefixes whose presence in source marks a directory as this agent's content.
        Detection reads import statements only — never prose (matching prose is what
        corrupted 172 URLs during the vendor sync).
    content_roots:
        Conventional on-disk plugin directories for this agent, used as a discovery hint.
    skill_roots:
        Conventional on-disk directories where this agent keeps **skills** — each a directory
        containing a ``SKILL.md``. Distinct from ``content_roots`` (which holds plugins):
        a skill is data the host can enumerate and load, not code it imports. Paths may be
        ``~``-relative. A missing root is a graceful no-op, never an error — the foreign
        agent's skill tree is optional content, not a dependency.
    skill_frontmatter_aliases:
        Foreign frontmatter key -> real key, applied to a skill's YAML frontmatter so a foreign
        ``SKILL.md`` parses under the host's key names. An allowlist: an unlisted key is left
        untouched, and a real key that is already present is never overwritten.
    mcp_config_paths:
        Conventional on-disk config files where this agent registers **MCP servers** (a name
        plus a ``command``/``url`` launch spec). A foreign agent may keep MCP entries in a
        dedicated JSON/YAML file or inline under a key in its main config; list either shape
        here. Missing paths are a graceful no-op, never an error.
    mcp_config_keys:
        The top-level config key(s) under which MCP server entries live, when they are inlined
        in a config file (e.g. ``("mcp_servers",)``). Tried in order; the first mapping found
        wins. Empty means the file IS the ``{name: entry}`` mapping at top level.
    reserved:
        Names this agent must NOT alias because Qubit deliberately keeps them (its own
        entry-point group, legacy data paths, wire-format strings). Listed so a future
        contributor sees the intent instead of "fixing" an apparent omission.
    """

    name: str
    module_aliases: Dict[str, str] = field(default_factory=dict)
    prefix_aliases: Dict[str, str] = field(default_factory=dict)
    symbol_aliases: Dict[str, Dict[str, str]] = field(default_factory=dict)
    env_aliases: Dict[str, str] = field(default_factory=dict)
    entry_point_groups: Tuple[str, ...] = ()
    detection_markers: Tuple[str, ...] = ()
    content_roots: Tuple[str, ...] = ()
    skill_roots: Tuple[str, ...] = ()
    skill_frontmatter_aliases: Dict[str, str] = field(default_factory=dict)
    mcp_config_paths: Tuple[str, ...] = ()
    mcp_config_keys: Tuple[str, ...] = ()
    reserved: Tuple[str, ...] = ()

    # ------------------------------------------------------------------ alias resolution

    def resolve_module(self, name: str) -> Optional[str]:
        """Map a foreign module name to its real name, or None when this adapter has no opinion."""
        direct = self.module_aliases.get(name)
        if direct:
            return direct
        for foreign_prefix, real_prefix in self.prefix_aliases.items():
            if name == foreign_prefix.rstrip(".") or name.startswith(foreign_prefix):
                return real_prefix + name[len(foreign_prefix):]
        return None

    def is_reserved(self, name: str) -> bool:
        """True when *name* is deliberately kept as-is (must not be aliased)."""
        return any(name == r or name.startswith(r) for r in self.reserved)


# ------------------------------------------------------------------------- import aliasing engine

class _AliasLoader(importlib.abc.Loader):
    """Returns an already-imported module for an aliased name (no re-execution)."""

    def __init__(self, module: types.ModuleType) -> None:
        self._module = module

    def create_module(self, spec):  # noqa: D102
        return self._module

    def exec_module(self, module):  # noqa: D102
        return None


class ImportAliaser(importlib.abc.MetaPathFinder):
    """Resolve foreign module names to their real counterparts, preserving module IDENTITY.

    IDENTITY IS THE WHOLE POINT. The alias must hand back the *same* module object the rest of
    the process uses, so a foreign plugin that mutates (say) the plugin registry is mutating the
    live registry, not a private copy.

    WHY BINDINGS ARE PRE-SEEDED, NOT LEFT TO A RACE:
    ``find_spec`` alone is not enough. If a caller imports the alias first, this finder imports
    the real module and registers it — but a later ``import <real_name>`` still re-runs the
    default machinery and builds a SECOND object with the same ``__name__`` and fresh state.
    So :meth:`preload` imports each target once and writes BOTH names into ``sys.modules`` up
    front; whichever name is requested first, CPython finds an initialised entry and never
    executes the file twice.
    """

    def __init__(self, adapters: Sequence[ForeignAdapter]) -> None:
        self._adapters = tuple(adapters)

    # -- lookup

    def _map(self, name: str) -> Optional[str]:
        for adapter in self._adapters:
            if adapter.is_reserved(name):
                return None
            mapped = adapter.resolve_module(name)
            if mapped:
                return mapped
        return None

    # -- importlib hook

    def find_spec(self, fullname, path=None, target=None):  # noqa: D102
        mapped = self._map(fullname)
        if not mapped:
            return None
        real = self._bind(fullname, mapped)
        return importlib.util.spec_from_loader(
            fullname, _AliasLoader(real), is_package=hasattr(real, "__path__")
        )

    # -- binding

    def _bind(self, alias: str, target: str) -> types.ModuleType:
        """Import *target* once and bind BOTH names to the identical object."""

        real = sys.modules.get(target)
        if real is None:
            try:
                real = importlib.import_module(target)
            except ImportError as exc:
                raise AgentCompatUnresolved(
                    f"agent_compat: {alias!r} aliases to {target!r}, which failed to import: "
                    f"{exc}. Add the correct target to the adapter's module_aliases."
                ) from exc

        existing = sys.modules.get(alias)
        if existing is not None and existing is not real:
            raise AgentCompatUnresolved(
                f"agent_compat: {alias!r} is already loaded as a distinct module object "
                f"(duplicate state). Expected the same object as {target!r}."
            )

        # Both names -> one object. This is what keeps a foreign plugin's mutations visible.
        sys.modules[alias] = real
        sys.modules[target] = real
        return real

    def preload(self) -> List[str]:
        """Bind exact aliases AND discover prefix-family members up front.

        Exact aliases are bound directly. Prefix rules (``hermes_state_`` -> ``qubit_state_``)
        cannot be pre-bound from the table alone, because the siblings are only knowable by
        looking at what exists — so we enumerate the real modules that match a real prefix and
        bind each corresponding alias. Without this a prefix-aliased module is imported twice
        (once per name) and the foreign copy diverges from the host's, which is the same
        duplicate-module bug the exact-alias preload exists to prevent.
        """
        bound: List[str] = []
        for adapter in self._adapters:
            for alias, target in adapter.module_aliases.items():
                if not _importable(target):
                    continue
                self._bind(alias, target)
                bound.append(f"{alias}->{target}")
            for foreign_prefix, real_prefix in adapter.prefix_aliases.items():
                for real_name, alias_name in _prefix_family(real_prefix, foreign_prefix):
                    if not _importable(real_name):
                        continue
                    if adapter.is_reserved(alias_name):
                        continue
                    try:
                        self._bind(alias_name, real_name)
                    except AgentCompatUnresolved:
                        continue  # a genuine collision; leave it to fail loudly at use
                    bound.append(f"{alias_name}->{real_name}")
        return bound


def _importable(name: str) -> bool:
    """True when *name* can be imported (cheap probe; avoids binding a broken alias)."""
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _prefix_family(real_prefix: str, foreign_prefix: str) -> List[Tuple[str, str]]:
    """Enumerate ``(real_module, foreign_alias)`` pairs for a prefix rule.

    Prefix rules describe a FAMILY (``qubit_state_*`` has 20+ siblings), so the members are
    discovered from what actually exists rather than enumerated in the adapter table, which
    would rot as the host adds modules.

    Only top-level modules are discovered: a prefix rule like ``hermes_cli.`` maps a package's
    DEEP names, and those are resolved on demand by ``find_spec`` (the package itself is bound
    by an exact alias), so enumerating them here would be wasted work.
    """
    pairs: List[Tuple[str, str]] = []
    if real_prefix.endswith("."):
        # A package-prefix rule; the package is handled by the exact alias, and deep names
        # resolve through find_spec. Nothing to pre-seed.
        return pairs

    for entry in sorted(sys.path):
        if not entry:
            continue
        try:
            base = pathlib.Path(entry)
            if not base.is_dir():
                continue
            for path in base.glob(f"{real_prefix}*.py"):
                stem = path.stem
                if stem != real_prefix.rstrip("_"):
                    pairs.append((stem, foreign_prefix + stem[len(real_prefix):]))
        except OSError:  # pragma: no cover - unreadable sys.path entry
            continue
    return pairs


# ------------------------------------------------------------------- env / symbol aliasing

class EnvAliaser:
    """Mirror foreign env vars onto the real names Qubit reads, and back.

    Direction is non-destructive in BOTH directions: an explicitly-set variable always wins,
    and the other side is filled only when unset. This makes a foreign plugin that reads its
    OWN name (e.g. ``HERMES_HOME``) resolve correctly when only Qubit's name is set — and a
    foreign plugin that sets its own name cannot silently retarget a Qubit profile.

    Why both directions: ``env_aliases`` declares that two names denote the SAME variable
    (``HERMES_HOME`` ≡ ``QUBIT_HOME``). A one-way mirror only serves foreign code that reads
    the REAL name; foreign code that reads its own name — which is the common case for a
    plugin written before the rename, e.g. Hermes Herald's ``_resolve_hermes_home()`` — would
    otherwise fall back to a stale default path and miss the live config.
    """

    def __init__(self, adapters: Sequence[ForeignAdapter]) -> None:
        self._pairs: List[Tuple[str, str]] = []
        for adapter in adapters:
            for old, new in adapter.env_aliases.items():
                if (old, new) not in self._pairs:
                    self._pairs.append((old, new))

    def apply(self) -> List[str]:
        """Apply the mirrors (both directions). Returns the pairs actually written."""
        applied: List[str] = []
        for old, new in self._pairs:
            # foreign -> real (unchanged contract: a set real var is never overwritten)
            value = os.environ.get(old)
            if value and not os.environ.get(new):
                os.environ[new] = value
                applied.append(f"{old}->{new}")
            # real -> foreign: fill the foreign name when IT is unset, so foreign code that
            # reads its own variable resolves. An explicit foreign value still wins.
            value = os.environ.get(new)
            if value and not os.environ.get(old):
                os.environ[old] = value
                applied.append(f"{new}->{old}")
        return applied

    def pairs(self) -> List[Tuple[str, str]]:
        return list(self._pairs)


class SymbolAliaser:
    """Expose foreign attribute names on the real modules that renamed them.

    ``from tools.xai_http import hermes_xai_user_agent`` cannot be fixed by module aliasing:
    the module resolves, the attribute does not. We attach the foreign name as an alias of the
    real one on the SAME module object, so foreign code gets the live function, not a copy.

    Existing attributes are never overridden — if the foreign name genuinely exists, it wins.
    """

    def __init__(self, adapters: Sequence[ForeignAdapter]) -> None:
        self._aliases: Dict[str, Dict[str, str]] = {}
        for adapter in adapters:
            for module_name, mapping in adapter.symbol_aliases.items():
                self._aliases.setdefault(module_name, {}).update(mapping)

    def apply(self) -> List[str]:
        applied: List[str] = []
        for module_name, mapping in self._aliases.items():
            try:
                mod = importlib.import_module(module_name)
            except ImportError:
                continue  # optional module absent in this install
            for foreign, real in mapping.items():
                if hasattr(mod, foreign):
                    continue  # already present (or a genuine name) — never override
                target = getattr(mod, real, None)
                if target is None:
                    continue  # the rename we expected isn't there; leave it to fail loudly
                setattr(mod, foreign, target)
                applied.append(f"{module_name}.{foreign}->{real}")
        return applied


# -------------------------------------------------------------------- skills / MCP loading

@dataclass(frozen=True)
class SkillEntry:
    """One foreign skill discovered on disk.

    ``directory`` is the skill's own folder (the one holding ``SKILL.md``), ``name`` is the
    frontmatter ``name`` (falling back to the folder name), and ``frontmatter`` is the parsed
    mapping after ``skill_frontmatter_aliases`` were applied — so a caller sees the host's key
    names, not the foreign agent's.
    """

    directory: pathlib.Path
    name: str
    frontmatter: Dict[str, object]


@dataclass(frozen=True)
class McpServerEntry:
    """One foreign MCP server read from the foreign agent's config.

    ``name`` is the registry key and ``spec`` is the raw ``{command/url/...}`` mapping, kept
    verbatim so the host can hand it to its own MCP loader without this layer interpreting it.
    """

    name: str
    spec: Dict[str, object]
    source: pathlib.Path


def _read_frontmatter(text: str) -> Dict[str, object]:
    """Parse the YAML frontmatter block of a ``SKILL.md``.

    Minimal on purpose — no third-party YAML import in this layer. Handles the ``key: value``
    and ``key:`` forms the skills ecosystem actually uses; a value is kept as a string when it
    is not obviously a scalar. Returns ``{}`` when there is no frontmatter, so a malformed or
    frontmatter-less file degrades to "no metadata" rather than raising.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end]
    data: Dict[str, object] = {}
    for raw in block.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#") or line[0] in " \t":
            continue  # blank, comment, or a nested key (kept flat here; top-level only)
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        data[key] = value
    return data


class SkillLoader:
    """Enumerate a foreign agent's skills, applying foreign frontmatter key aliases.

    A skill is a directory containing ``SKILL.md``. Foreign roots are scanned recursively
    (skills live at ``<root>/<name>`` and ``<root>/<category>/<name>`` in the wild), so the
    loader does not assume a depth. A **missing root is a graceful no-op** — an empty report,
    never an exception and never a fabricated entry: the foreign agent simply may not be
    installed, and that is not an error.

    The frontmatter aliases are an allowlist: an unlisted foreign key is left as-is, and a
    real key already present is never overwritten (mirroring ``SymbolAliaser``).
    """

    def __init__(self, adapters: Sequence[ForeignAdapter]) -> None:
        self._roots: List[pathlib.Path] = []
        self._aliases: Dict[str, str] = {}
        for adapter in adapters:
            for root in adapter.skill_roots:
                expanded = pathlib.Path(os.path.expanduser(root))
                if expanded not in self._roots:
                    self._roots.append(expanded)
            for foreign, real in adapter.skill_frontmatter_aliases.items():
                # An explicit real key already claimed by another adapter wins; first wins.
                self._aliases.setdefault(foreign, real)

    def roots(self) -> List[pathlib.Path]:
        """The (expanded) skill roots this loader will scan."""
        return list(self._roots)

    def _alias_frontmatter(self, data: Dict[str, object]) -> Dict[str, object]:
        out = dict(data)
        for foreign, real in self._aliases.items():
            if foreign in out and real not in out:
                out[real] = out.pop(foreign)
        return out

    def enumerate(self) -> List[SkillEntry]:
        """Return every skill under the configured roots. Empty when none/missing."""
        found: List[SkillEntry] = []
        for root in self._roots:
            if not root.is_dir():
                continue  # missing root: graceful no-op, never an error
            for skill_md in sorted(root.rglob("SKILL.md")):
                try:
                    text = skill_md.read_text(errors="ignore")
                except OSError:  # pragma: no cover - unreadable file mid-scan
                    continue
                fm = self._alias_frontmatter(_read_frontmatter(text))
                name = str(fm.get("name") or skill_md.parent.name)
                found.append(SkillEntry(directory=skill_md.parent, name=name, frontmatter=fm))
        return found

    def report(self) -> Dict[str, object]:
        """A JSON-friendly summary: the roots scanned and the skills found."""
        skills = self.enumerate()
        return {
            "roots": [str(r) for r in self._roots],
            "skills": [
                {"name": s.name, "directory": str(s.directory), "frontmatter": s.frontmatter}
                for s in skills
            ],
            "count": len(skills),
        }


class McpConfigLoader:
    """Read a foreign agent's MCP server entries without launching anything.

    An MCP server is a CONFIG ENTRY (a name plus a ``command``/``url`` launch spec). A foreign
    agent keeps these either in a dedicated config file or inline under a key in its main
    config; ``mcp_config_keys`` names that key. **No entry is connected, and a spec is never
    interpreted** — this returns the raw mapping for the host's own MCP registry to act on.

    A **missing config path is a graceful no-op** (empty list), never an exception. A path that
    exists but cannot be parsed contributes nothing for that path and no fabricated entry.
    JSON is parsed with the stdlib; a ``.yaml``/``.yml`` file is parsed with a minimal
    top-level reader so this layer carries no YAML dependency.
    """

    def __init__(self, adapters: Sequence[ForeignAdapter]) -> None:
        self._paths: List[pathlib.Path] = []
        self._keys: Tuple[str, ...] = ()
        keys: List[str] = []
        for adapter in adapters:
            for path in adapter.mcp_config_paths:
                expanded = pathlib.Path(os.path.expanduser(path))
                if expanded not in self._paths:
                    self._paths.append(expanded)
            for key in adapter.mcp_config_keys:
                if key not in keys:
                    keys.append(key)
        self._keys = tuple(keys)

    def paths(self) -> List[pathlib.Path]:
        """The (expanded) config paths this loader will read."""
        return list(self._paths)

    def _load_mapping(self, path: pathlib.Path) -> Optional[Dict[str, object]]:
        """Load a config file into a top-level mapping, or None if unreadable/unparseable."""
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            return None
        suffix = path.suffix.lower()
        if suffix == ".json":
            try:
                import json

                data = json.loads(text)
            except (ValueError, TypeError):
                return None
            return data if isinstance(data, dict) else None
        if suffix in (".yaml", ".yml"):
            return _read_yaml_top_level(text)
        # Unknown extension: try JSON first (the MCP convention), then YAML.
        try:
            import json

            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except (ValueError, TypeError):
            pass
        return _read_yaml_top_level(text)

    def _entries_from(self, mapping: Dict[str, object]) -> Dict[str, object]:
        """Pick the server mapping out of a loaded config, honouring ``mcp_config_keys``."""
        if not self._keys:
            return mapping
        for key in self._keys:
            candidate = mapping.get(key)
            if isinstance(candidate, dict):
                return candidate
        return {}

    def read(self) -> List[McpServerEntry]:
        """Return every MCP server entry found across the configured paths."""
        entries: List[McpServerEntry] = []
        seen: set = set()
        for path in self._paths:
            if not path.is_file():
                continue  # missing path: graceful no-op, never an error
            mapping = self._load_mapping(path)
            if mapping is None:
                continue  # unparseable: contributes nothing, invents nothing
            servers = self._entries_from(mapping)
            for name, spec in servers.items():
                if name in seen or not isinstance(spec, dict):
                    continue
                seen.add(name)
                entries.append(McpServerEntry(name=name, spec=spec, source=path))
        return entries

    def report(self) -> Dict[str, object]:
        """A JSON-friendly summary: the paths read and the servers found."""
        servers = self.read()
        return {
            "paths": [str(p) for p in self._paths],
            "servers": [
                {"name": s.name, "spec": s.spec, "source": str(s.source)} for s in servers
            ],
            "count": len(servers),
        }


def _read_yaml_top_level(text: str) -> Optional[Dict[str, object]]:
    """Parse a YAML document into nested dicts/lists by indentation, without PyYAML.

    Deliberately minimal (no third-party import in this layer): it understands the block
    subset the MCP config actually uses — nested ``key:`` mappings, ``key: value`` scalars,
    and ``- item`` sequences — and nothing more exotic. Returns ``{}`` for empty/garbage input
    rather than raising, so a bad file yields no entries and never a fabricated one.
    """
    root: Dict[str, object] = {}
    # Each stack entry is (indent, container) for the mapping currently being populated.
    stack: List[Tuple[int, Dict[str, object]]] = [(-1, root)]
    pending_list_key: Optional[Tuple[Dict[str, object], str]] = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()

        # A sequence item belongs to the most recent `key:` that opened a list.
        if line.startswith("- "):
            if pending_list_key is not None:
                container, key = pending_list_key
                seq = container.get(key)
                if not isinstance(seq, list):
                    seq = []
                    container[key] = seq
                seq.append(line[2:].strip())
            continue

        if ":" not in line:
            pending_list_key = None
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        # Pop any mapping whose indent is at or beyond this key's own indent.
        while stack and indent <= stack[-1][0]:
            stack.pop()
        container = stack[-1][1] if stack else root

        if value == "":
            # Opens a nested mapping OR, if followed by `- ` items, a sequence.
            child: Dict[str, object] = {}
            container[key] = child
            stack.append((indent, child))
            pending_list_key = (container, key)
        else:
            container[key] = value
            pending_list_key = None
    return root


# ------------------------------------------------------------------------------- the registry

class AdapterRegistry:
    """Holds the known foreign-agent adapters and answers detection questions."""

    def __init__(self) -> None:
        self._adapters: List[ForeignAdapter] = []

    def register(self, adapter: ForeignAdapter) -> ForeignAdapter:
        """Register an adapter (idempotent by name; later registrations replace earlier)."""
        self._adapters = [a for a in self._adapters if a.name != adapter.name]
        self._adapters.append(adapter)
        return adapter

    def unregister(self, name: str) -> None:
        self._adapters = [a for a in self._adapters if a.name != name]

    def all(self) -> List[ForeignAdapter]:
        return list(self._adapters)

    def get(self, name: str) -> Optional[ForeignAdapter]:
        for a in self._adapters:
            if a.name == name:
                return a
        return None

    # -- detection

    def detect(self, directory: os.PathLike | str, *, scan_files: Optional[Iterable] = None):
        """Return the adapter whose markers appear in *directory*'s source, or None.

        Detection reads IMPORT STATEMENTS, never prose. A README that merely says the
        foreign agent's name must not trigger an adapter — matching prose is exactly how the
        vendor sync corrupted URLs.
        """
        base = pathlib.Path(directory)
        targets = list(scan_files) if scan_files is not None else list(base.rglob("*.py"))
        for path in targets:
            try:
                text = pathlib.Path(path).read_text(errors="ignore")
            except OSError:
                continue
            for adapter in self._adapters:
                for marker in adapter.detection_markers:
                    if f"import {marker}" in text or f"from {marker}" in text:
                        return adapter
        return None

    def entry_point_groups(self) -> Tuple[str, ...]:
        """Every foreign entry-point group Qubit should keep accepting."""
        groups: List[str] = []
        for adapter in self._adapters:
            for g in adapter.entry_point_groups:
                if g not in groups:
                    groups.append(g)
        return tuple(groups)


#: Process-wide adapter registry. Import adapters and register them here.
registry = AdapterRegistry()


# ------------------------------------------------------------------------------------ install

_lock = threading.Lock()
_finder: Optional[ImportAliaser] = None
_env_applied = False


def active_adapters() -> List[ForeignAdapter]:
    """Adapters currently registered (not necessarily installed)."""
    return registry.all()


def installed() -> bool:
    """True when the import aliaser is active."""
    return _finder is not None and _finder in sys.meta_path


def install(*, env: bool = False, adapters: Optional[Sequence[ForeignAdapter]] = None) -> ImportAliaser:
    """Install aliasing for the registered adapters (idempotent).

    ``env=True`` additionally mirrors environment aliases. Import aliasing is safe to enable
    globally; environment aliasing mutates process state, so it is opt-in at the call site.
    """
    global _finder, _env_applied
    with _lock:
        chosen = tuple(adapters) if adapters is not None else tuple(registry.all())
        if _finder is None or _finder not in sys.meta_path:
            _finder = ImportAliaser(chosen)
            sys.meta_path.insert(0, _finder)
        # Pre-seed so neither import order can produce a duplicate module.
        _finder.preload()
        SymbolAliaser(chosen).apply()
        # Apply the env mirror on EVERY call (idempotent, non-destructive — it only fills a
        # side that is unset). A one-shot latch is wrong here: an early install() can run
        # before QUBIT_HOME is visible (or before any pair has a source value), set the
        # latch, and then every later call is a silent no-op — which is exactly how a
        # foreign plugin's HERMES_HOME stayed unset while the process had QUBIT_HOME.
        if env:
            EnvAliaser(chosen).apply()
            _env_applied = True
    return _finder


def uninstall() -> None:
    """Remove the finder and drop aliased entries (test seam)."""
    global _finder, _env_applied
    with _lock:
        if _finder is not None and _finder in sys.meta_path:
            sys.meta_path.remove(_finder)
        if _finder is not None:
            aliased = {
                alias
                for adapter in _finder._adapters  # noqa: SLF001 (same package)
                for alias in adapter.module_aliases
            }
            for name in aliased:
                sys.modules.pop(name, None)
        _finder = None
        _env_applied = False


def reset() -> None:
    """Full teardown: uninstall and clear the registry (test seam)."""
    uninstall()
    registry._adapters = []  # noqa: SLF001 (same package)


def install_for(
    directory: os.PathLike | str,
    *,
    adapter: Optional[ForeignAdapter] = None,
    env: bool = False,
) -> Optional[ForeignAdapter]:
    """Install aliasing when *directory* holds a known foreign agent's content.

    Returns the adapter that matched (or None when nothing matched and nothing is installed).
    Passing *adapter* skips detection.
    """
    chosen = adapter or registry.detect(directory)
    if chosen is None:
        return None
    if chosen not in registry.all():
        registry.register(chosen)
    install(env=env)
    return chosen
