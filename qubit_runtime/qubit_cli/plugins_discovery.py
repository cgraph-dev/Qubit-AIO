"""Plugin discovery: directory scanning, entry-point manifests, and the enable/disable gate.

Split out of :mod:`qubit_cli.plugins`. Names that tests patch on the origin
(``get_bundled_plugins_dir``, ``_env_enabled``) are looked up lazily through it.
"""

from __future__ import annotations

import contextlib
import contextvars
import importlib.metadata
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from qubit_constants import get_qubit_home
from qubit_cli.config import cfg_get
from qubit_cli.plugin_capabilities import VALID_CAPABILITY_IDS
from qubit_cli.plugin_capabilities import parse_declared_capabilities as _parse_declared_capabilities
from qubit_cli.plugins_manifest import (
    PluginManifest, _detect_kind_from_source, manifest_key, _resolve_module_source,
    parse_manifest_file, portable_plugin_manifest,
)
from qubit_cli.relay_plugin_cutover import LEGACY_RELAY_PLUGIN_KEYS, RELAY_PLUGINS_CONFIG_ENV

logger = logging.getLogger("qubit_cli.plugins")

#: Qubit-owned entry-point group. ``hermes_agent.plugins`` is kept as a
#: read-old fallback for upstream distributions that still advertise it.
QUBIT_ENTRY_POINTS_GROUP = "qubit_agent.plugins"
ENTRY_POINTS_GROUP = "hermes_agent.plugins"
#: Qubit's own groups, always honoured.
_OWN_ENTRY_POINT_GROUPS = (QUBIT_ENTRY_POINTS_GROUP, ENTRY_POINTS_GROUP)
QUBIT_ENTRY_POINT_CAPABILITIES_GROUP = "qubit_agent.plugin_capabilities"
ENTRY_POINT_CAPABILITIES_GROUP = "hermes_agent.plugin_capabilities"
ENTRY_POINT_CAPABILITIES_GROUPS = (
    QUBIT_ENTRY_POINT_CAPABILITIES_GROUP, ENTRY_POINT_CAPABILITIES_GROUP)


# ── Foreign-content extension point (neutral; this module knows no adapter) ───
#
# The vendored tree must never import a Qubit-side package: a clean rename would
# then silently rot (the external-only invariant, enforced by the test suite).
# So the dependency is INVERTED — this module owns a plain registry of callables,
# and the Qubit layer (voicebridge) registers into it at its own startup. The
# tree stays installable and runnable with no voicebridge present; a registered
# provider is optional capability, never a dependency.
#
# A provider is any object exposing the three plain callables below. It is
# duck-typed on purpose: the tree must not import the provider's types either.
_foreign_providers: List[Any] = []


def register_foreign_provider(provider: Any) -> None:
    """Register a foreign-content provider (idempotent by object identity).

    The provider supplies foreign entry-point groups to scan and participates
    in activating whatever mechanism it needs. Called by the Qubit layer at
    startup — never by this tree. A provider that raises is dropped, not
    propagated: foreign support must never break plugin discovery.
    """
    if provider is None or any(p is provider for p in _foreign_providers):
        return
    _foreign_providers.append(provider)


def clear_foreign_providers() -> None:
    """Drop every registered provider (test seam / teardown)."""
    _foreign_providers.clear()


def _provider_call(provider: Any, method: str) -> Any:
    """Call ``provider.<method>()`` defensively; None when absent or raising."""
    fn = getattr(provider, method, None)
    if not callable(fn):
        return None
    try:
        return fn()
    except Exception:  # noqa: BLE001 - a broken provider must not break discovery
        logger.warning("Foreign provider %s.%s() failed", type(provider).__name__, method, exc_info=True)
        return None


def install_foreign_adapters() -> int:
    """Let every registered provider activate its mechanism; return how many did.

    Idempotent. Called by the plugin-discovery funnel BEFORE any plugin import
    runs, so foreign content is loadable by the time it is imported. Returns 0
    when nothing is registered — a no-op, not a failure.
    """
    activated = 0
    for provider in tuple(_foreign_providers):
        if _provider_call(provider, "activate") is not None or callable(getattr(provider, "activate", None)):
            activated += 1
    return activated


def foreign_entry_point_groups() -> tuple:
    """Entry-point groups the registered providers want scanned, IN ADDITION to ours.

    Empty when nothing is registered, so default behaviour is unchanged.
    """
    groups: List[str] = []
    for provider in tuple(_foreign_providers):
        reported = _provider_call(provider, "entry_point_groups") or ()
        for group in reported:
            if isinstance(group, str) and group not in groups:
                groups.append(group)
    return tuple(groups)


def entry_point_groups() -> tuple:
    """Every entry-point group a plugin may be published under, deduped.

    Qubit's own groups plus any provider-contributed groups. Discovery and the
    entry-point loader must consult this SAME set — they previously disagreed
    (discovery scanned a tuple, the loader selected a single group), so a
    plugin discoverable under one group could be unreachable at load time.
    """
    groups = list(_OWN_ENTRY_POINT_GROUPS)
    for group in foreign_entry_point_groups():
        if group not in groups:
            groups.append(group)
    return tuple(groups)


# Per-harness manifest directories plugin repos ship for OTHER agent harnesses (e.g. obra/superpowers keeps one
# plugin.json per harness). Their plugin.json is not an Agent Plugins v1 manifest and can never validate, so
# parsing it on every discovery pass only spams warnings (#101962).
_FOREIGN_HARNESS_MANIFEST_DIRS = frozenset({
    ".claude-plugin", ".codex-plugin", ".cursor-plugin", ".devin-plugin", ".kimi-plugin",
})

# Set while a caller reads a profile's config WITHOUT wanting that profile's plugins in this process:
# the multiplex preflight loads a PARKED profile's gateway config for the duplicate-credential guard,
# and ``load_gateway_config`` discovers plugins in the scope it runs under. Importing them runs their
# ``register()`` (threads, DB handles) for a profile the operator took out of the host (#123386).
# A contextvar, not an env flag: it must not leak to other threads or outlive the read.
_discovery_suppressed: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "qubit_plugin_discovery_suppressed", default=False)


@contextlib.contextmanager
def suppress_plugin_discovery():
    """``discover_plugins()`` is a no-op inside; the scope's manager stays undiscovered, so the
    first real consumer after the block still loads its plugins."""
    token = _discovery_suppressed.set(True)
    try:
        yield
    finally:
        _discovery_suppressed.reset(token)


def plugin_discovery_suppressed() -> bool:
    return _discovery_suppressed.get()


def _select_entry_point_group(entry_points: Any, group: str) -> list:
    """Return one metadata entry-point group across supported Python APIs."""
    if hasattr(entry_points, "select"):
        return list(entry_points.select(group=group))
    if isinstance(entry_points, dict):
        return list(entry_points.get(group, []))
    return [ep for ep in entry_points if ep.group == group]


def _select_entry_point_groups(entry_points: Any, groups: tuple) -> list:
    """Union of the Qubit-owned and legacy upstream entry-point groups, deduped."""
    seen = set()
    out = []
    for group in groups:
        for ep in _select_entry_point_group(entry_points, group):
            key = (getattr(ep, "name", ""), getattr(ep, "value", ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(ep)
    return out


def discover_entrypoint_manifests() -> List["PluginManifest"]:
    """Return metadata-only manifests for installed entry-point plugins. Kind comes from an import-free source
    scan (memory/model providers route to their own discovery). Capabilities come from the companion
    ``hermes_agent.plugin_capabilities`` group (``<plugin-id>.<capability-id>`` entries pointing at the same
    object), so consent works without importing plugin code. Failures are isolated per entry point."""
    manifests: List[PluginManifest] = []
    try:
        eps = importlib.metadata.entry_points()
        group_eps = _select_entry_point_groups(eps, entry_point_groups())
        capability_eps = _select_entry_point_groups(
            eps, ENTRY_POINT_CAPABILITIES_GROUPS)
    except Exception as exc:
        logger.debug("Entry-point scan failed: %s", exc)
        return manifests
    for ep in group_eps:
        try:
            declared = {d.name for d in capability_eps if d.value == ep.value}
            capabilities = [c for c in VALID_CAPABILITY_IDS if f"{ep.name}.{c}" in declared]
            dist = getattr(ep, "dist", None)
            metadata = getattr(dist, "metadata", None)
            manifests.append(PluginManifest(
                name=ep.name, version=str(getattr(dist, "version", "") or ""),
                description=str(metadata.get("Summary", "") or "") if metadata is not None else "",
                source="entrypoint", path=ep.value, key=ep.name,
                kind=_classify_entrypoint_value_kind(ep.value),
                capabilities=_parse_declared_capabilities(capabilities, ep.name),
            ))
        except Exception as exc:
            logger.debug("Entry-point manifest for %r skipped: %s", getattr(ep, "name", "?"), exc)
    return manifests


def _classify_entrypoint_value_kind(value: str) -> str:
    """Classify an entry-point target by import-free source scan (unresolvable -> standalone)."""
    try:
        module_name = str(value).split(":", 1)[0].strip()
        return (_detect_kind_from_source(_resolve_module_source(module_name)) if module_name else None) or "standalone"
    except Exception:
        return "standalone"


def _get_disabled_plugins() -> set:
    """Read ``plugins.disabled`` — a deny-list that wins over ``plugins.enabled``."""
    try:
        from qubit_cli.config import load_config
        disabled = cfg_get(load_config(), "plugins", "disabled", default=[])
        return set(disabled) if isinstance(disabled, list) else set()
    except Exception:
        return set()


def _get_enabled_plugins() -> Optional[set]:
    """Read the ``plugins.enabled`` allow-list (plugins are opt-in). ``None`` = key missing/malformed ("nothing
    enabled yet"; the first ``migrate_config`` run grandfathers installed user plugins); ``set()`` = explicitly
    empty; else the allow-list."""
    try:
        from qubit_cli.config import load_config
        enabled = cfg_get(load_config(), "plugins", "enabled")
        return set(enabled) if isinstance(enabled, list) else None
    except Exception:
        return None


def scan_directory(
    path: Path, source: str, *, skip_names: Optional[Set[str]] = None, prefix: str = "", depth: int = 0
) -> List[PluginManifest]:
    """Read manifests under *path*: flat ``<root>/<name>/plugin.yaml`` (key ``name``) or category
    ``<root>/<cat>/<name>/plugin.yaml`` (key ``cat/name``; a manifest-less directory recurses one level, depth
    capped at two). *skip_names* ignores top-level names; portable ``plugin.json`` packages are accepted
    alongside YAML manifests."""
    manifests: List[PluginManifest] = []
    if not path.is_dir():
        return manifests
    try:
        children = sorted(path.iterdir())
    except OSError as exc:
        logger.warning("Skipping unreadable plugin directory %s: %s", path, exc)
        return manifests
    for child in children:
        # Cache/dunder dirs (__pycache__, __MACOSX__, …) are never
        # plugins. Walking them can raise PermissionError and take
        # down every subsequent tool call (#86996).
        if child.name.startswith("__") and child.name.endswith("__"):
            logger.debug("Skipping dunder plugin path %s", child)
            continue
        if child.name in _FOREIGN_HARNESS_MANIFEST_DIRS:
            logger.debug("Skipping %s (foreign-harness manifest convention)", child)
            continue
        # pathlib.Path.is_dir() swallows OSError, but injected Path-likes
        # and test doubles can still raise. Fail closed per child.
        try:
            if not child.is_dir() or (depth == 0 and skip_names and child.name in skip_names):
                continue
            manifest_file = next((f for f in (child / "plugin.yaml", child / "plugin.yml") if f.exists()), None)
            portable_file = child / "plugin.json"
            has_portable = portable_file.exists() or portable_file.is_symlink()
        except OSError as exc:
            # stat() raises (not "False") on an unsearchable directory — Windows ACLs (WinError 5) or a
            # mode-000 dir; one such plugin must not abort discovery for every other plugin (#111804).
            logger.warning("Skipping unreadable plugin directory %s: %s", child, exc)
            continue
        if manifest_file is not None:
            manifest = parse_manifest_file(manifest_file, child, source, prefix)
            if manifest is not None:
                manifests.append(manifest)
        elif has_portable:
            try:
                manifests.append(portable_plugin_manifest(child, source, prefix))
            except Exception as exc:
                logger.warning("Failed to parse %s: %s", portable_file, exc)
        elif depth >= 1:
            logger.debug("Skipping %s (no plugin.yaml, depth cap reached)", child)
        else:
            sub_prefix = f"{prefix}/{child.name}" if prefix else child.name
            manifests.extend(scan_directory(child, source, prefix=sub_prefix, depth=depth + 1))
    return manifests


def collect_directory_manifests() -> List[PluginManifest]:
    """Read directory manifests in full-discovery order (bundled top-level, bundled/platforms, user, opt-in
    project) without loading or mutating anything, so startup probes share the exact precedence/containment
    rules of the real discovery sweep."""
    from qubit_cli import plugins as _origin  # patched names resolve through the origin
    manifests: List[PluginManifest] = []

    def _scan(label: str, directory: Path, source: str, skip_names: Optional[Set[str]] = None) -> None:
        found = scan_directory(directory, source, skip_names=skip_names)
        logger.debug("  %s: %d manifest(s)", label, len(found))
        manifests.extend(found)

    # Excluded bundled top-level categories have their own discovery. ``platforms/`` is an ordinary category
    # dir: the recursion keys its adapters ``platforms/<dir>`` like every other category (``web/firecrawl``),
    # which is the key `qubit plugins enable/disable` and the dashboard write (#27548); the manifest name
    # (``photon-platform``) stays an accepted alias through ``gate_manifest``.
    repo_plugins = _origin.get_bundled_plugins_dir()
    logger.debug("Scanning bundled plugins: %s", repo_plugins)
    _scan("bundled (top-level)", repo_plugins, "bundled",
          {"memory", "context_engine", "model-providers", "cron_providers", "computer_use"})
    user_dir = get_qubit_home() / "plugins"
    logger.debug("Scanning user plugins: %s", user_dir)
    _scan("user", user_dir, "user")
    if _origin._env_enabled("HERMES_ENABLE_PROJECT_PLUGINS"):
        # Qubit-owned ``./.qubit/plugins/`` wins; ``./.hermes/plugins/`` is the
        # read-old fallback for repos laid out before the rebrand.
        project_dir = Path.cwd() / ".qubit" / "plugins"
        if not project_dir.is_dir():
            project_dir = Path.cwd() / ".hermes" / "plugins"
        logger.debug("Scanning project plugins: %s", project_dir)
        _scan("project", project_dir, "project")
    else:
        logger.debug("Project plugins disabled (set QUBIT_ENABLE_PROJECT_PLUGINS=1 to enable)")
    return manifests


def resolve_manifest_winners(manifests: List[PluginManifest]) -> Dict[str, PluginManifest]:
    """Later sources win on key collision (project > user > bundled): a same-named copy under
    ``~/.qubit/plugins/<name>`` is the documented way to override a bundled plugin, and is logged. A flat
    user/project manifest that claims a bundled key from a *differently named* directory is an impostor, not
    an override (``impostor_dir/plugin.yaml`` with ``name: kanban``): it is skipped with a warning so
    ``qubit plugins enable kanban`` never activates unrelated code under the bundled name."""
    winners: Dict[str, PluginManifest] = {}
    for manifest in manifests:
        key = manifest_key(manifest)
        shadowed = winners.get(key)
        if shadowed is not None and shadowed.source == "bundled" and manifest.source in {"user", "project"}:
            own_dir = Path(manifest.path).name if manifest.path else ""
            bundled_dir = Path(shadowed.path).name if shadowed.path else ""
            if own_dir and bundled_dir and own_dir != bundled_dir:
                logger.warning(
                    "Ignoring %s plugin at %s: its manifest name '%s' is a bundled plugin's key but the "
                    "directory is named '%s'; rename the directory to '%s' to override the bundled plugin",
                    manifest.source, manifest.path, key, own_dir, bundled_dir,
                )
                continue
            logger.info("Plugin '%s' at %s (%s) shadows the bundled copy at %s", key, manifest.path,
                        manifest.source, shadowed.path)
        winners[key] = manifest
    return winners


@dataclass(frozen=True)
class ManifestGate:
    """Routing verdict for one winning manifest (see :func:`gate_manifest`)."""

    action: str  # "load" (dependency-ordered pass) | "placeholder" | "load_now" | "defer"
    enabled: bool = False
    error: Optional[str] = None
    log: Optional[tuple] = None  # (level, message, *args)


def gate_manifest(
    manifest: PluginManifest, disabled: Set[str], enabled: Optional[Set[str]]
) -> ManifestGate:
    """Decide how one winning manifest is handled. Gate order matters: legacy relay refusal, explicit disable,
    category-owned kinds (exclusive / model-provider), bundled auto-loads (backend now, platform deferred),
    then ``plugins.enabled`` opt-in (path-derived key or legacy bare name)."""
    lookup_key = manifest_key(manifest)
    from qubit_cli.left_core_migration import legacy_plugin_keys
    # A bundled key that left core (``observability/langfuse``) keeps selecting its catalog plugin.
    names = {lookup_key, manifest.name, *legacy_plugin_keys(lookup_key)}

    def _placeholder(error: Optional[str], level: int, message: str, *args, enabled: bool = False) -> ManifestGate:
        return ManifestGate("placeholder", enabled=enabled, error=error, log=(level, message, lookup_key, *args))

    # Relay lifecycle is core-owned; an old plugin copy would compete for its registries.
    if names & LEGACY_RELAY_PLUGIN_KEYS:
        error = (
            "removed — Relay lifecycle is owned by Hermes core; configure a standard user or system Relay "
            f"plugins.toml, or use {RELAY_PLUGINS_CONFIG_ENV} for an explicit user-file override"
        )
        return _placeholder(error, logging.WARNING, "Refusing to load removed Qubit Relay plugin '%s'; %s", error)
    if names & disabled:
        return _placeholder("disabled via config", logging.DEBUG, "Skipping disabled plugin '%s'")
    # Exclusive plugins (memory providers) have their own activation path; record only.
    if manifest.kind == "exclusive":
        return _placeholder(
            "exclusive plugin — activate via <category>.provider config", logging.DEBUG,
            "Skipping '%s' (exclusive, handled by category discovery)",
        )
    # Model providers load via providers/__init__.py; a second import here would create two ProviderProfile
    # instances and break the bundled-vs-user "last writer wins" override.
    if manifest.kind == "model-provider":
        return _placeholder(
            None, logging.DEBUG, "Skipping '%s' (model-provider, handled by providers/ discovery)", enabled=True)
    if manifest.source == "bundled":
        # Bundled backends auto-load; selection among them is ``<category>.provider`` config.
        if manifest.kind == "backend":
            return ManifestGate("load_now")
        # Bundled platforms register LAZILY: eagerly importing ~20 heavy SDKs added seconds to every `qubit`
        # invocation. A deferred loader keeps every platform available on first use.
        if manifest.kind == "platform":
            return ManifestGate("defer")
    if enabled is None or not names & enabled:
        return _placeholder(
            f"not enabled in config (run `qubit plugins enable {lookup_key}` to activate)", logging.DEBUG,
            "Skipping '%s' (not in plugins.enabled)",
        )
    if manifest.source != "bundled":
        # The catalog kill list is enforced at install; a plugin recalled AFTER it was installed must not keep
        # loading. Offline check (in-tree list + cached live copy), honours an explicit install-time bypass.
        from qubit_cli.plugins_cmd_catalog import installed_plugin_removal
        removed = installed_plugin_removal(manifest.name, manifest.path)
        if removed is not None:
            error = f"removed from the Qubit plugin catalog: {removed.reason or 'no reason recorded'}"
            return _placeholder(error, logging.WARNING, "Refusing to load plugin '%s' — %s; run `qubit plugins remove`",
                                error)
    return ManifestGate("load")
