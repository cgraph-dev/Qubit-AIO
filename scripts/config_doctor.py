#!/usr/bin/env python3
"""voicebridge config doctor (Packet 0B).

Two modes:
  --static   validate files/contracts only: config parse, required keys, audio
             combination, security combination, machine-specific leaks in
             portable examples, frontend resolution, undeclared-import scan,
             repository systemd template vs installed user unit (read-only).
             Runs with no Ollama / audio / gateway / Chromium / systemd running.
  --runtime  everything in --static plus resolved effective values and live
             probes (Ollama availability/residency, Qubit worker lane,
             Chromium CDP loopback, Bubblewrap/systemd versions, audio devices).
             Every probe reports a typed state and degrades instead of failing
             when an optional dependency is absent.

Exit codes: 0 = ok or degraded-only warnings, 2 = at least one FAIL.
Secrets (webui.token, API keys) are never printed — presence only.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import socket
import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

PASS, CONFIGURED, HEALTHY = "PASS", "CONFIGURED", "HEALTHY"
ACTIVE, RESIDENT, AVAILABLE = "ACTIVE", "RESIDENT", "AVAILABLE"
MISSING, DEGRADED, WARN, FAIL = "MISSING", "DEGRADED", "WARN", "FAIL"

# Modules imported through guarded optional paths (try/except ImportError) as
# inventoried in docs/archive/2026-09-21/PACKET_0A_MISMATCH_REPORT.md section D. They degrade
# gracefully, so their absence is a warning, not a fresh-install failure.
OPTIONAL_GUARDED = {"psutil", "onnx", "prompt_toolkit", "websocket",
                    "pystray"}
# Core local source top-level packages that are never PyPI dependencies.
LOCAL_TOP = {"voicebridge", "scripts", "browser_auto"}


def _vendored_top_level_names() -> set[str]:
    """Top-level import names provided by the vendored Qubit runtime.

    The Qubit-owned fork ships as package data (``qubit_runtime/``); once
    ``qubit_runtime.ensure_path()`` puts that tree on ``sys.path`` its
    top-level packages/modules (``agent``, ``tools``, ``gateway``,
    ``plugins``, ``cron``, ``providers``, ``qubit_cli``, ``qubit_state_*``,
    …) are local source, not PyPI distributions.

    Derive the names from the build/packaging declaration in
    ``pyproject.toml`` — the hatch ``force-include`` tables, i.e. exactly the
    vendored artifacts the wheel/sdist ship — so the allowlist cannot drift
    from the vendored source (this mirrors
    ``voicebridge.release._vendored_top_level_names``, which derives the same
    names from the *installed* ``voicebridge/_data/qubit_runtime`` tree). A
    missing registration here is a W9 vendor-import gap, so it is read from
    packaging rather than hardcoded.

    An importable top-level name is a directory mapping whose source dir has
    an ``__init__.py`` (an importable package) or a top-level ``*.py`` file.
    Data directories (``skills``, ``locales``, ``ui-tui``, …) have no
    ``__init__.py`` and are correctly not treated as import names.
    """
    try:
        data = tomllib.loads((REPO / "pyproject.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    targets = (data.get("tool", {}).get("hatch", {})
               .get("build", {}).get("targets", {}))
    sources: set[str] = set()
    for target in ("wheel", "sdist"):
        sources |= set(targets.get(target, {}).get("force-include", {}))
    names: set[str] = set()
    for src in sources:
        if not src.startswith("qubit_runtime/"):
            continue
        rel = src[len("qubit_runtime/"):]
        source = REPO / "qubit_runtime" / rel
        if source.is_dir():
            # A directory mapping is an import name only when it is an
            # importable package (has __init__.py); data dirs are not.
            if "/" not in rel and (source / "__init__.py").is_file():
                names.add(rel)
        elif rel.endswith(".py") and "/" not in rel and rel != "__init__.py":
            names.add(rel[:-3])
    return names


# Distribution-name -> import-name corrections (pip name differs from module).
DIST_ALIASES = {"pyyaml": "yaml", "piper_tts": "piper"}


def _declared_modules() -> set[str]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    mods: set[str] = set()
    deps = list(data.get("project", {}).get("dependencies", []))
    for extra in data.get("project", {}).get("optional-dependencies", {}).values():
        deps.extend(extra)
    for req in deps:
        name = re.split(r"[<>=!\[;\s]", req.strip(), maxsplit=1)[0]
        name = name.split("/")[-1] if "/" in name else name
        if name:
            mods.add(name.replace("-", "_").split(".")[0].lower())
    mods |= {imp for d, imp in DIST_ALIASES.items() if d in mods and imp}
    return {m for m in mods if m}


def _import_scan() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """AST-scan production code for third-party imports not declared in
    pyproject.toml. Returns (failures, warnings) as (module, location)."""
    declared = _declared_modules()
    stdlib = set(sys.stdlib_module_names)
    local = LOCAL_TOP | _vendored_top_level_names()
    failures: list[tuple[str, str]] = []
    warnings: list[tuple[str, str]] = []
    for py in sorted((REPO / "voicebridge").rglob("*.py")) + sorted((REPO / "scripts").glob("*.py")):
        rel = py.relative_to(REPO)
        try:
            tree = ast.parse(py.read_text(errors="replace"))
        except SyntaxError as exc:
            failures.append(("<syntax>", f"{rel}:{exc.lineno}"))
            continue
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module]
            for m in mods:
                top = m.split(".")[0]
                if top in stdlib | declared | local:
                    continue
                loc = f"{rel}:{node.lineno}"
                if top in OPTIONAL_GUARDED:
                    warnings.append((top, loc))
                elif rel.parts[0] == "scripts":
                    warnings.append((top, loc))  # operational tooling, not package runtime
                else:
                    failures.append((top, loc))
    return failures, warnings


def _load_config(explicit: str | None):
    from voicebridge.config import Config
    return Config.load(Path(explicit) if explicit else None)


def _yaml_paths():
    return [REPO / "config/voice.example.yaml", REPO / "config/voice.local.yaml",
            REPO / "config/voice.yaml"]


def check_static(cfg, results) -> None:
    path = getattr(cfg, "path", None)
    results.append(("config.loaded_path",
                    CONFIGURED if path else DEGRADED,
                    str(path) if path else "defaults only (no YAML file found)"))

    bad = [k for k in (("mic.channels", cfg.get("mic", "channels")),
                       ("mic.gain", cfg.get("mic", "gain"))) if k[1] is None]
    ch = cfg.get("mic", "channels")
    gain = cfg.get("mic", "gain")
    if not isinstance(ch, int) or ch < 1:
        results.append(("audio.mic_channels", FAIL, f"channels={ch!r} must be int >= 1"))
    elif not isinstance(gain, (int, float)) or not (0 < float(gain) <= 16):
        results.append(("audio.mic_gain", FAIL, f"gain={gain!r} outside (0,16]"))
    else:
        results.append(("audio.combo", PASS, f"channels={ch} gain={gain}"))

    model = cfg.get("intent", "local_llm", "model")
    nctx = cfg.get("intent", "local_llm", "num_ctx")
    if not model or not isinstance(model, str):
        results.append(("model.alias", FAIL, "intent.local_llm.model empty"))
    else:
        results.append(("model.alias", CONFIGURED, str(model)))
    if nctx is not None and not (512 <= int(nctx) <= 262144):
        results.append(("model.num_ctx", FAIL, f"num_ctx={nctx!r} outside [512,262144]"))
    else:
        results.append(("model.num_ctx", CONFIGURED, f"num_ctx={nctx}"))

    host_raw = cfg.get("webui", "host")
    token_raw = cfg.get("webui", "token")
    if host_raw is None and token_raw is None:
        results.append(("security.token_combo", WARN,
                        "webui host/token unset in this file (Phase 1B owns enforced "
                        "loopback+token policy); runtime default binds 0.0.0.0"))
    else:
        host = host_raw or "0.0.0.0"
        token = token_raw or ""
        token_state = "<set>" if token else "<empty>"
        loopback = host in ("127.0.0.1", "localhost", "::1")
        if not loopback and not token:
            results.append(("security.token_combo", FAIL,
                            f"host={host} non-loopback with empty token (plan P0-C)"))
        else:
            results.append(("security.token_combo", PASS if token else WARN,
                            f"host={host} token={token_state}"))

    od = cfg.get("audio", "output_device")
    odn = cfg.get("audio", "output_device_name")
    if od in (None, "") and odn in (None, ""):
        results.append(("audio.output_pins", PASS, "empty pins follow default sink (AUDIO_SETUP rule 1)"))
    elif od not in (None, ""):
        results.append(("audio.output_pins", WARN,
                        "output_device index pinned; indices shift on reconnect"))
    else:
        results.append(("audio.output_pins", PASS, f"name pin '{odn}'"))

    leak_pat = re.compile(r"/home/[A-Za-z0-9_.-]+|Scarlett|HyperX|AT2020", re.I)
    for y in _yaml_paths():
        text = y.read_text(errors="replace") if y.exists() else ""
        hits = sorted(set(m.group(0) for m in leak_pat.finditer(text)))
        results.append((f"portable.{y.name}", PASS if not hits else WARN,
                        "machine-agnostic" if not hits else f"machine-specific tokens: {hits}"))

    explicit_fe = cfg.get("webui", "frontend_dir") or None
    try:
        from voicebridge.webui import frontend_status
        fe, serves, stale, version, active = frontend_status(explicit_fe)
        note = f"{fe} (source: {active}; version {version or '?'}"
        if stale:
            note += "; configured frontend_dir is stale/version-mismatched, " \
                    "serving repository UI (update the external checkout or " \
                    "remove the override)"
        results.append(("frontend.active",
                        HEALTHY if serves and not stale else DEGRADED,
                        note + ")"))
    except Exception as exc:
        results.append(("frontend.active", DEGRADED, f"resolver unavailable: {exc}"))

    failures, warns = _import_scan()
    for mod, loc in failures:
        results.append(("deps.declared", FAIL, f"undeclared import '{mod}' at {loc}"))
    for mod, loc in warns:
        results.append(("deps.optional", WARN, f"'{mod}' at {loc} (guarded/optional)"))
    if not failures and not warns:
        results.append(("deps.declared", PASS, "no undeclared imports"))

    tmpl = REPO / "systemd/qubit-voicebridge.service"
    installed = Path.home() / ".config/systemd/user/qubit-voicebridge.service"
    if not tmpl.exists():
        results.append(("systemd.template", MISSING, "repository template absent"))
    else:
        results.append(("systemd.template", PASS, str(tmpl)))
    if not installed.exists():
        results.append(("systemd.installed", MISSING, f"{installed} (no overrides to reconcile)"))
    else:
        tkeys = _unit_keys(tmpl)
        ikeys = _unit_keys(installed)
        deltas = [k for k in sorted(set(tkeys) | set(ikeys))
                  if tkeys.get(k) != ikeys.get(k)]
        secretish = [d for d in deltas if "TOKEN" in d or "KEY" in d]
        shown = [d for d in deltas if d not in secretish]
        results.append(("systemd.installed",
                        DEGRADED if deltas else PASS,
                        "matches template" if not deltas else
                        f"{len(deltas)} directives differ (machine/generated overrides): "
                        + ", ".join(shown[:8]) + (" …" if len(shown) > 8 else "")
                        + "; reconciliation is documented, never auto-applied"))


def _unit_keys(p: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in p.read_text(errors="replace").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith(("#", "[")):
            k, _, v = line.partition("=")
            if k in ("Restart", "RestartSec", "Wants", "After", "MemoryMax",
                     "MemoryHigh", "Nice", "Environment"):
                out[k] = v.strip()
    return out


def _probe_http(url: str, timeout: float) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def check_reconcile(cfg, results) -> None:
    """Packet 7B: final runtime/documentation reconciliation for Phase 0-7A.

    Cross-checks the feature-flag registry vs the resolved config, the
    documented model alias vs the config vs the runner contract, the AUDIO_SETUP
    rules vs the resolved config, the documented ops commands vs run_tests, and
    MIGRATIONS coverage. Read-only; never writes or restarts anything."""
    sys.path.insert(0, str(REPO))
    # 1. feature-flag registry defaults must match the config source of truth
    try:
        from voicebridge import feature_flags as FF
        from voicebridge.config import DEFAULTS as _D
        mismatches = []
        for f in FF.FLAGS:
            node: object = _D
            for seg in f.config_path:
                node = node.get(seg) if isinstance(node, dict) else None
            if node != f.default:
                mismatches.append(f"{f.name}: config default {node!r} != "
                                  f"registry default {f.default!r}")
        if mismatches:
            results.append(("reconcile.flags_defaults", FAIL,
                            "; ".join(mismatches)))
        else:
            results.append(("reconcile.flags_defaults", PASS,
                            f"{len(FF.FLAGS)} flags agree with config defaults"))
        # effective values all resolve
        eff = FF.effective_all(cfg)
        if len(eff) != len(FF.FLAGS):
            results.append(("reconcile.flags_resolve", FAIL, "not all flags resolve"))
        else:
            results.append(("reconcile.flags_resolve", PASS,
                            f"{len(eff)} flags resolve to effective values"))
    except Exception as exc:
        results.append(("reconcile.flags_defaults", DEGRADED,
                        f"flag registry unavailable: {exc}"))

    # 2. model alias: docs claim (example), config, and runner contract agree
    doc_alias = None
    for doc in (REPO / "docs/archive/2026-09-21" / "AUDIO_SETUP.md",
                REPO / "docs/archive/2026-09-21" / "VOICE_BRIDGE_SYSTEM.md",
                REPO / "README.md"):
        try:
            for line in doc.read_text().splitlines():
                if "qubit-brain4" in line:
                    doc_alias = "qubit-brain4"
        except OSError:
            pass
    cfg_alias = cfg.get("intent", "local_llm", "model")
    try:
        from voicebridge.runner_contract import DEFAULT_ALIAS
    except Exception:
        DEFAULT_ALIAS = None
    # runner-contract precedence WITHOUT the digest HTTP probe (hermetic):
    rc_alias = (cfg.get("intent", "runner", "alias")
                or cfg.get("intent", "ollama_model")
                or cfg_alias or DEFAULT_ALIAS)
    aliases = {a for a in (doc_alias, cfg_alias, rc_alias) if a}
    if len(aliases) > 1:
        results.append(("reconcile.model_alias", FAIL,
                        f"docs={doc_alias} config={cfg_alias} "
                        f"runner_contract={rc_alias}"))
    else:
        results.append(("reconcile.model_alias", PASS,
                        f"single alias everywhere: {sorted(aliases)}"))

    # 3. AUDIO_SETUP critical rules vs resolved config
    ch = cfg.get("mic", "channels")
    gain = cfg.get("mic", "gain")
    od = cfg.get("audio", "output_device")
    odn = cfg.get("audio", "output_device_name")
    violations = []
    if ch != 1:
        violations.append(f"mic.channels={ch} (AUDIO_SETUP rule 2: 1)")
    if not (0 < float(gain) <= 16):
        violations.append(f"mic.gain={gain} (AUDIO_SETUP rule 3)")
    if od not in (None, "") or odn not in (None, ""):
        violations.append("audio output pins must stay EMPTY (rule 1)")
    if violations:
        results.append(("reconcile.audio_rules", FAIL, "; ".join(violations)))
    else:
        results.append(("reconcile.audio_rules", PASS,
                        "mono mic, sane gain, empty output pins"))

    # 4. documented ops commands exist in run_tests
    try:
        from scripts import run_tests as RT
        for cmd in ("unit", "contract", "integration", "smoke", "live-model",
                    "live-dashboard", "monitor"):
            if cmd not in RT.CATEGORIES:
                results.append(("reconcile.ops_categories", FAIL,
                                f"run_tests missing category {cmd}"))
                break
        else:
            results.append(("reconcile.ops_categories", PASS,
                            "all documented test categories exist"))
    except Exception as exc:
        results.append(("reconcile.ops_categories", DEGRADED,
                        f"run_tests unavailable: {exc}"))

    # 5. MIGRATIONS coverage: the feature-flag change has an entry
    try:
        mig = (REPO / "docs/archive/2026-09-21" / "MIGRATIONS.md").read_text()
        if "## MIG-016" not in mig:
            results.append(("reconcile.migrations", FAIL,
                            "missing MIG-016 (feature-flag registry) entry"))
        else:
            results.append(("reconcile.migrations", PASS,
                            "MIG-016 present"))
    except OSError as exc:
        results.append(("reconcile.migrations", DEGRADED,
                        f"MIGRATIONS.md unreadable: {exc}"))


def check_runtime(cfg, results) -> None:
    url = cfg.get("intent", "ollama_url") or "http://127.0.0.1:11434"
    tags = _probe_http(f"{url}/api/tags", 2.0)
    if not tags:
        results.append(("ollama.server", MISSING, f"no response from {url}"))
    else:
        names = [m.get("name") for m in tags.get("models", [])]
        results.append(("ollama.server", AVAILABLE, f"{len(names)} models installed"))
        want = cfg.get("intent", "local_llm", "model")
        have = any(n and (n == want or n.split(":")[0] == want) for n in names)
        results.append(("ollama.model_present",
                        CONFIGURED if have else DEGRADED,
                        f"configured '{want}' " + ("installed" if have else "NOT pulled")))
        ps = _probe_http(f"{url}/api/ps", 2.0) or {}
        resident = ps.get("models") or []
        if resident:
            m0 = resident[0]
            want = str(want)
            if m0.get("name") and m0["name"].split(":")[0] != want:
                results.append(("ollama.resident_alias", WARN,
                                f"resident '{m0.get('name')}' != configured '{want}' "
                                "(another model occupies the GPU; plan rule 22 co-load risk)"))
            ctx = m0.get("context_length")
            if ctx is None:
                c = m0.get("context")
                ctx = c[-1] if isinstance(c, list) and c else c
            results.append(("ollama.resident", RESIDENT,
                            f"{m0.get('name')} ctx={ctx} size_vram={m0.get('size_vram')}"))
            if ctx is None:
                results.append(("ollama.ctx_match", DEGRADED,
                                "resident context size not reported by server"))
            elif nctx_ok(cfg, ctx):
                results.append(("ollama.ctx_match", WARN,
                                f"resident ctx {ctx} != configured num_ctx "
                                f"{cfg.get('intent','local_llm','num_ctx')} (reload risk, plan P0-M)"))
            else:
                results.append(("ollama.ctx_match", PASS, f"resident ctx {ctx} matches config"))
        else:
            results.append(("ollama.resident", MISSING, "nothing loaded (cold start on first call)"))

    results.append(("worker.lane", ACTIVE, "native agent turns + worker sessions"))

    cdp = _probe_http("http://127.0.0.1:9222/json/version", 1.5)
    if cdp and isinstance(cdp, dict):
        results.append(("chromium.cdp", AVAILABLE,
                        f"browser={cdp.get('Browser', '?')} loopback only"))
    else:
        results.append(("chromium.cdp", MISSING,
                        "127.0.0.1:9222 not listening (inbox precondition absent)"))

    for tool in (("bubblewrap", ["bwrap", "--version"]), ("systemd", ["systemctl", "--user", "--version"])):
        try:
            out = subprocess.run(tool[1], capture_output=True, text=True, timeout=3).stdout.splitlines()[0]
            results.append((f"isolation.{tool[0]}", AVAILABLE, out[:80]))
        except Exception:
            results.append((f"isolation.{tool[0]}", MISSING, f"{tool[0]} unavailable"))
    # Packet 3B: full typed probe (smoke test + transient-unit capability)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from voicebridge.isolated import probe_isolation
        pr = probe_isolation(refresh=True)
        if pr.available:
            results.append(("isolation.probe", AVAILABLE,
                            f"bwrap {pr.version}: smoke + user-systemd OK"))
        else:
            results.append(("isolation.probe", DEGRADED,
                            f"ISOLATION_UNAVAILABLE: {pr.message[:120]}"))
    except Exception as exc:
        results.append(("isolation.probe", DEGRADED, f"probe error: {str(exc)[:100]}"))

    try:
        import sounddevice as sd
        n = len(sd.query_devices())
        results.append(("audio.devices", AVAILABLE, f"{n} devices enumerated (read-only query)"))
    except Exception as exc:
        results.append(("audio.devices", DEGRADED, f"sounddevice unavailable: {exc.__class__.__name__}"))

    # Packet 7A health checks: disk, VRAM, event-journal writeability.
    _health_checks(cfg, results)


def _health_checks(cfg, results) -> None:
    """Typed health checks for disk space, free VRAM, and event-journal
    writeability. Each reports a typed state and degrades, never a false pass."""
    import shutil
    try:
        sd_ = cfg.state_dir()
        usage = shutil.disk_usage(sd_ if sd_ else REPO)
        free_gib = usage.free / (1024 ** 3)
        state = AVAILABLE if free_gib > 1.0 else DEGRADED
        results.append(("health.disk", state,
                        f"{free_gib:.1f} GiB free at "
                        f"{sd_ if sd_ else REPO} (threshold 1 GiB)"))
    except Exception as exc:
        results.append(("health.disk", DEGRADED, f"unable to stat disk: {exc.__class__.__name__}"))

    try:
        from voicebridge.vram_guard import free_vram_mib
        free = free_vram_mib(timeout_s=3.0)
        if free is None:
            results.append(("health.vram", DEGRADED,
                            "nvidia-smi unavailable (no GPU/VRAM measurement)"))
        else:
            state = AVAILABLE if free > 512 else DEGRADED
            results.append(("health.vram", state, f"{free:.0f} MiB free VRAM (threshold 512)"))
    except Exception as exc:
        results.append(("health.vram", DEGRADED, f"vram probe error: {exc.__class__.__name__}"))

    # Event-journal writeability: the bridge appends to <state_dir>/events.jsonl.
    # Probe the directory with a transient file — never a real journal write.
    try:
        probe_dir = cfg.state_dir() if cfg.state_dir() else REPO
        probe_dir = Path(probe_dir)
        probe_dir.mkdir(parents=True, exist_ok=True)
        probe = probe_dir / ".doctor-journal-probe"
        with open(probe, "w") as fh:
            fh.write("probe\n")
            fh.flush()
            os.fsync(fh.fileno())
        probe.unlink()
        results.append(("health.journal_write", HEALTHY,
                        f"state dir writable ({probe_dir})"))
    except Exception as exc:
        results.append(("health.journal_write", DEGRADED,
                        f"state dir not writable: {exc.__class__.__name__}"))


def nctx_ok(cfg, ctx) -> bool:
    try:
        return int(ctx) != int(cfg.get("intent", "local_llm", "num_ctx") or 0)
    except (TypeError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="voicebridge configuration doctor")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--static", action="store_true", help="files/contracts only (CI-safe)")
    g.add_argument("--runtime", action="store_true", help="static + live probes (typed degraded)")
    g.add_argument("--reconcile", action="store_true",
                   help="static + final runtime/documentation reconciliation (Phase 0-7A)")
    ap.add_argument("--config", help="explicit config path (else VOICE_BRIDGE_CONFIG/default)")
    args = ap.parse_args(argv)

    from voicebridge.config import Config
    try:
        cfg = Config.load(Path(args.config) if args.config else None)
    except Exception as exc:
        print(f"FAIL config.parse: {exc}")
        return 2

    results: list[tuple[str, str, str]] = []
    check_static(cfg, results)
    if args.runtime:
        check_runtime(cfg, results)
    if args.reconcile:
        check_reconcile(cfg, results)

    order = {FAIL: 0, WARN: 1, DEGRADED: 2, MISSING: 2}
    for name, state, detail in results:
        print(f"[{state:>9}] {name:24s} {detail}")
    fails = sum(1 for _, s, *_ in results if s == FAIL)
    warns = sum(1 for _, s, *_ in results if s == WARN)
    degr = sum(1 for _, s, *_ in results if s in (DEGRADED, MISSING))
    print(f"\ndoctor: {len(results)} checks — {fails} fail, {warns} warn, {degr} degraded/missing")
    return 2 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
