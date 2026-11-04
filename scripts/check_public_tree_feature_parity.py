#!/usr/bin/env python3
"""Static feature/page parity gate for the public source export.

The public export is intentionally narrower than the working tree (internal
docs, secrets, operator paths, caches), so a clean export can silently lose a
Qubit-only dashboard page: its client route, plugin manifest, bundle asset,
backend handler, TUI build, catalog, units, or docs.  This gate reads
``scripts/public_tree_feature_parity.json`` and asserts every declared surface
survives ``scripts/export_public_tree.py``'s exact selection rules.

It is static and hermetic: Git objects plus source files only.  No server is
started, no browser is used, and no network is touched.

Usage::

    python3 scripts/check_public_tree_feature_parity.py
    python3 scripts/check_public_tree_feature_parity.py --json

Exit status is 0 when every surface is present, 1 with a ``MISSING`` list
(otherwise).  The test wrapper is
``tests/test_public_tree_feature_parity.py``.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts import export_public_tree as EX  # noqa: E402

MANIFEST_PATH = REPO / "scripts" / "public_tree_feature_parity.json"


@dataclass
class ParityReport:
    schema: str
    manifest: str
    pages_checked: int = 0
    required_routes: list[str] = field(default_factory=list)
    included_routes: list[str] = field(default_factory=list)
    exported_paths_checked: int = 0
    deliberate_omissions: list[dict] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing

    def as_dict(self) -> dict:
        data = asdict(self)
        data["ok"] = self.ok
        return data


def _read(repo: Path, rel: str) -> str:
    return (repo / rel).read_text(encoding="utf-8")


def _app_routes(repo: Path, manifest: dict) -> set[str]:
    text = _read(repo, manifest["sources"]["app_routes"])
    block = re.search(r"const BUILTIN_ROUTES_CORE[^{]*\{(.*?)\n\};", text, re.S)
    if not block:
        return set()
    return set(re.findall(r'"([^"]+)":', block.group(1)))


def _spa_routes(repo: Path, manifest: dict) -> set[str]:
    text = _read(repo, manifest["sources"]["spa_routes"])
    block = re.search(r"SPA_ROUTE_PATHS\s*=\s*\((.*?)\n\)", text, re.S)
    if not block:
        return set()
    return set(ast.literal_eval("(" + block.group(1) + ")"))


def _reason_for_omission(
    path: str, tracked: set[str], included: set[str], personal: set[str]
) -> str:
    if path not in tracked:
        return "not tracked at HEAD"
    if path in included:
        return "included (manifest omission is stale)"
    allowed, reason = EX._classify(path)
    if allowed and path in personal:
        reason = "personal_path"
    return reason


def _new(
    manifest: dict,
    manifest_rel: str,
    tracked: list[str],
    included: list[str],
) -> tuple[ParityReport, set[str], set[str], set[str]]:
    report = ParityReport(
        schema=manifest.get("schema", ""),
        manifest=manifest_rel,
        deliberate_omissions=list(manifest.get("deliberate_omissions", [])),
    )
    tracked_set = set(tracked)
    included_set = set(included)
    omitted_paths = {
        row["path"]
        for row in report.deliberate_omissions
        if row.get("kind") == "export_file" and row.get("path")
    }
    return report, tracked_set, included_set, omitted_paths


def _require_exported(
    path: str,
    need: str,
    report: ParityReport,
    tracked: set[str],
    included: set[str],
    personal: set[str],
) -> bool:
    if path in tracked and path in included:
        report.exported_paths_checked += 1
        return True
    reason = _reason_for_omission(path, tracked, included, personal)
    report.missing.append(
        f"MISSING export {path} — {reason}; needed for {need} "
        f"(rule: scripts/export_public_tree.py)"
    )
    return False


def _require_needles(
    path: str,
    needles: list[str],
    need: str,
    report: ParityReport,
    repo: Path,
    tracked: set[str],
    included: set[str],
    personal: set[str],
) -> None:
    if not _require_exported(path, need, report, tracked, included, personal):
        return
    text = _read(repo, path)
    for needle in needles:
        if needle not in text:
            report.missing.append(
                f"MISSING backend symbol {needle!r} in {path}; needed for {need} "
                f"(source: {path})"
            )


def _check_dir(
    directory: str,
    need: str,
    report: ParityReport,
    tracked: set[str],
    included: set[str],
    personal: set[str],
    omitted: set[str],
) -> None:
    files = sorted(p for p in tracked if p == directory or p.startswith(directory + "/"))
    if not files:
        report.missing.append(
            f"MISSING source tree {directory} — no tracked files at HEAD; needed for {need}"
        )
        return
    for path in files:
        if path in omitted:
            continue
        _require_exported(path, need, report, tracked, included, personal)


def _check_page(
    page: dict,
    report: ParityReport,
    repo: Path,
    app_routes: set[str],
    spa_routes: set[str],
    tracked: set[str],
    included: set[str],
    personal: set[str],
    omitted: set[str],
    sources: dict,
) -> None:
    pid = page["id"]
    need = f"page {pid} ({page.get('label', pid)})"
    for route in page.get("routes", []):
        if route not in spa_routes:
            report.missing.append(
                f"MISSING route {route} — not in SPA_ROUTE_PATHS; needed for {need} "
                f"(source: {sources['spa_routes']})"
            )
    for route in page.get("app_routes", []):
        if route not in app_routes:
            report.missing.append(
                f"MISSING route {route} — not in BUILTIN_ROUTES_CORE; needed for {need} "
                f"(source: {sources['app_routes']})"
            )

    plugin = page.get("plugin")
    if plugin:
        root = sources["plugin_root"]
        manifest_rel = f"{root}/{plugin}/manifest.json"
        data = None
        if _require_exported(manifest_rel, need, report, tracked, included, personal):
            data = json.loads(_read(repo, manifest_rel))
        if data is not None:
            if data.get("name") != plugin:
                report.missing.append(
                    f"MISSING plugin name {plugin!r} in {manifest_rel} "
                    f"(found {data.get('name')!r})"
                )
            tab = data.get("tab") or {}
            if tab.get("path") != page.get("plugin_tab"):
                report.missing.append(
                    f"MISSING plugin tab {page.get('plugin_tab')!r} in {manifest_rel} "
                    f"(found {tab.get('path')!r}; source: {manifest_rel})"
                )
            if bool(tab.get("hidden", False)) != bool(page.get("plugin_hidden", False)):
                report.missing.append(
                    f"MISSING plugin hidden flag {page.get('plugin_hidden')!r} in "
                    f"{manifest_rel} (found {bool(tab.get('hidden', False))!r}; "
                    f"source: {manifest_rel})"
                )
            if bool(data.get("has_api", False)) != bool(page.get("plugin_has_api", False)):
                report.missing.append(
                    f"MISSING plugin has_api flag {page.get('plugin_has_api')!r} in "
                    f"{manifest_rel} (found {bool(data.get('has_api', False))!r}; "
                    f"source: {manifest_rel})"
                )
            for rel_key in ("entry", "css"):
                rel = data.get(rel_key)
                if not rel:
                    continue
                asset = f"{root}/{plugin}/{rel}"
                _require_exported(
                    asset, f"{need} {rel_key}", report, tracked, included, personal
                )

    fleet = page.get("fleet")
    if fleet:
        fleet_rel = sources["fleet_manifest"]
        if _require_exported(fleet_rel, need, report, tracked, included, personal):
            entries = json.loads(_read(repo, fleet_rel))
            row = next((e for e in entries if e.get("name") == fleet["name"]), None)
            if row is None:
                report.missing.append(
                    f"MISSING fleet entry {fleet['name']!r} in {fleet_rel}; "
                    f"needed for {need}"
                )
            else:
                route = page.get("routes", [None])[0]
                if (row.get("tab") or {}).get("path") != route:
                    report.missing.append(
                        f"MISSING fleet tab {route!r} in {fleet_rel} "
                        f"(found {(row.get('tab') or {}).get('path')!r}; "
                        f"source: {fleet_rel})"
                    )
                for rel_key in ("entry", "css"):
                    rel = row.get(rel_key)
                    if not rel:
                        continue
                    asset = f"frontend/qubit-web/public/plugins/{fleet['name']}/{rel}"
                    _require_exported(
                        asset, f"{need} {rel_key}", report,
                        tracked, included, personal,
                    )

    for rel in page.get("files", []):
        _require_exported(rel, need, report, tracked, included, personal)
    for directory in page.get("dirs", []):
        _check_dir(directory, need, report, tracked, included, personal, omitted)

    backend = page.get("backend", [])
    if not backend and not page.get("deliberate_backend_omission"):
        report.missing.append(
            f"MISSING backend declaration for {need} — declare backend handlers "
            f"or a deliberate_backend_omission in scripts/public_tree_feature_parity.json"
        )
    for row in backend:
        _require_needles(
            row["path"], row.get("needles", []), need, report, repo,
            tracked, included, personal,
        )


def check(repo: Path = REPO, manifest_path: Path | None = None) -> ParityReport:
    """Run the static gate against *repo* and return a structured report."""
    manifest_file = (manifest_path or MANIFEST_PATH).resolve()
    global_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    try:
        manifest_rel = manifest_file.relative_to(repo.resolve()).as_posix()
    except ValueError:
        manifest_rel = str(manifest_file)

    tracked, included, _excluded, _counts, personal = EX.selected_paths(repo)
    report, tracked_set, included_set, omitted = _new(
        global_manifest, manifest_rel, tracked, included
    )
    app_routes = _app_routes(repo, global_manifest)
    spa_routes = _spa_routes(repo, global_manifest)
    sources = global_manifest["sources"]

    for row in global_manifest.get("global_checks", []):
        _require_needles(
            row["path"], row.get("needles", []), row.get("reason", "global"),
            report, repo, tracked_set, included_set, personal,
        )

    pages = global_manifest.get("pages", [])
    report.pages_checked = len(pages)
    required = sorted({r for page in pages for r in page.get("routes", [])})
    report.required_routes = required
    report.included_routes = [r for r in required if r in spa_routes]

    for page in pages:
        _check_page(
            page, report, repo, app_routes, spa_routes,
            tracked_set, included_set, personal, omitted, sources,
        )

    for group, spec in global_manifest.get("groups", {}).items():
        need = f"group {group}"
        for directory in spec.get("dirs", []):
            _check_dir(
                directory, need, report, tracked_set, included_set,
                personal, omitted,
            )
            count = sum(
                1 for p in tracked_set
                if p == directory or p.startswith(directory + "/")
            )
            if count < int(spec.get("min_files", 0)):
                report.missing.append(
                    f"MISSING files under {directory} — found {count}, "
                    f"need at least {spec.get('min_files')}; needed for {need}"
                )
        for rel in spec.get("files", []):
            _require_exported(
                rel, need, report, tracked_set, included_set, personal
            )

    for row in report.deliberate_omissions:
        if row.get("kind") != "export_file" or not row.get("path"):
            continue
        path = row["path"]
        if path in included_set:
            report.missing.append(
                f"STALE omission {path} — it is now exported; remove the "
                f"deliberate omission from scripts/public_tree_feature_parity.json"
            )
        elif path not in tracked_set:
            report.missing.append(
                f"STALE omission {path} — no longer tracked at HEAD; remove "
                f"the deliberate omission"
            )

    report.missing.sort()
    return report


def _summary(report: ParityReport) -> str:
    lines = [
        "Qubit public-tree feature parity",
        f"  manifest          : {report.manifest}",
        f"  pages checked     : {report.pages_checked}",
        f"  routes required   : {len(report.required_routes)}",
        f"  routes included   : {len(report.included_routes)}",
        f"  export files seen : {report.exported_paths_checked}",
        f"  omissions         : {len(report.deliberate_omissions)}",
        f"  missing           : {len(report.missing)}",
    ]
    if report.deliberate_omissions:
        lines += ["", "Deliberate omissions:"]
        for row in report.deliberate_omissions:
            subject = row.get("path") or row.get("kind")
            lines.append(f"  - {subject}: {row.get('reason', '')}")
    if report.missing:
        lines += ["", "Missing list:"]
        lines += [f"  - {item}" for item in report.missing]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Static public-tree feature/page parity gate.",
    )
    parser.add_argument(
        "--repo", type=Path, default=REPO,
        help="checkout to inspect (default: this script's repository)",
    )
    parser.add_argument(
        "--manifest", type=Path, default=MANIFEST_PATH,
        help="parity manifest to use (default: scripts/public_tree_feature_parity.json)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    report = check(args.repo.resolve(), args.manifest.resolve())
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(_summary(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
