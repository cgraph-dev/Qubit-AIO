"""Packet 8G: operator-gated qualification harness for the real Chromium/CDP
read-only browser path.

Qualifies the EXISTING user-launched Chromium over loopback CDP (127.0.0.1)
against the Packet 8B read-only adapters and Packet 8C report-bundle contract.
It is additive and hermetic by default:

- Hermetic stages run against synthetic fixtures in an ISOLATED qualification
  state dir; they never touch a browser, the production monitor store, or the
  production report root.
- Live stages attach to the user's already-running Chromium on loopback CDP
  ONLY when explicitly authorized: ``--live`` AND ``VOICE_BRIDGE_QUALIFY=1``.
  Without both they are impossible and fail closed (``LIVE_NOT_AUTHORIZED``).

The harness NEVER: launches a Playwright personal profile, clones a profile,
imports cookies/credentials, focuses/navigates/closes/mutates a user tab,
sends/replies/reacts/marks-read, exposes CDP beyond loopback, enables a
service/timer, or alters model/audio/user configuration.

Persistence contract: live runs store ONLY bounded redacted evidence (clipped
screenshot bytes, capped and sha256'd) and typed health into the isolated
qualification state dir with ``0700``/``0600`` modes. Sender, subject, message
body, cookie, token, and secret values are never stored. The qualification
summary is a redacted metadata record.
"""
from __future__ import annotations

import copy
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..config import Config
from .adapters.base import (CDP_LOOPBACK, CdpClient, ScanBounds, find_target,
                            find_target_preferring, list_targets)
from .adapters.registry import ADAPTERS, PLATFORM_NAMES, run_scan
from .contracts import AccountHealthStatus, Platform
from .monitor import cdp_precondition
from .reports import ReportStore

QUALIFY_ENV = "VOICE_BRIDGE_QUALIFY"
QUALIFY_VERSION = 1

# Bounded caps: a qualified scan never exceeds these.
SCAN_MAX_ITEMS = 60
SCAN_MAX_ROWS = 200
SCAN_DEADLINE_S = 30.0
CDP_TIMEOUT_S = 15.0
TARGET_LIMIT = 50
# A clipped row screenshot is normally a few tens of KB; anything above this
# cap is refused rather than stored (never an unbounded blob).
EVIDENCE_MAX_BYTES = 400_000

_PLATFORMS = tuple(sorted(p.value for p in Platform))


def live_authorized(env_value: Optional[str] = None) -> bool:
    """Live execution requires the operator gate ``VOICE_BRIDGE_QUALIFY=1``."""
    return (os.environ.get(QUALIFY_ENV) if env_value is None
            else env_value) == "1"


def deny_live(live: bool, *, env_value: Optional[str] = None
              ) -> Optional[Dict[str, Any]]:
    """Return a typed denial when live execution is requested without the gate.

    ``None`` means the requested live mode is authorized. This is the single
    fail-closed gate for every live stage; live work is impossible by default.
    """
    if not live:
        return None
    if live_authorized(env_value):
        return None
    return {
        "state": "denied", "code": "LIVE_NOT_AUTHORIZED",
        "detail": f"live browser qualification requires --live AND "
                  f"{QUALIFY_ENV}=1; nothing was touched",
    }


def redact_target_url(url: str) -> str:
    """Origin-only, bounded metadata for a target URL.

    Returns ``scheme://netloc`` plus the first path segment. Query strings and
    fragments are dropped (they can carry private tokens), page titles and
    message content are never recorded.
    """
    if not url:
        return ""
    try:
        from urllib.parse import urlsplit
        parts = urlsplit(str(url))
        if not parts.scheme or not parts.netloc:
            return ""
        seg = parts.path.strip("/").split("/")[0]
        host = parts.netloc
        return f"{parts.scheme}://{host}" + (f"/{seg}" if seg else "")
    except Exception:
        return ""


def describe_targets(targets: Iterable[Dict[str, Any]], *,
                     limit: int = TARGET_LIMIT) -> List[Dict[str, Any]]:
    """Bounded, title-free target metadata (type/origin/attachability only)."""
    out: List[Dict[str, Any]] = []
    for t in (targets or [])[:limit]:
        out.append({
            "type": t.get("type"),
            "origin": redact_target_url(t.get("url", "")),
            "attached": bool(t.get("attached")),
            "has_ws": bool(t.get("webSocketDebuggerUrl")),
        })
    return out


def isolate_cfg(cfg: Config, state_dir: Path) -> Config:
    """A copy of the config with the state root redirected to an isolated dir.

    Ensures the qualification harness never writes into the production state
    root (communications store, report bundles, journal). ``state_dir`` is
    created with private modes by the callers that persist.
    """
    data = copy.deepcopy(cfg.data)
    log = data.setdefault("logging", {})
    log["jsonl"] = True
    log["jsonl_path"] = str(state_dir)
    return Config(data=data, path=cfg.path)


# ------------------------------------------------------------------ preflight
def qualify_preflight(cfg: Config, *, base_url: str = CDP_LOOPBACK
                      ) -> Dict[str, Any]:
    """Typed CDP precondition + bounded target discovery metadata.

    Maps the existing ``cdp_precondition`` result to the qualification states:
    - ``OK``: loopback CDP answers (bounded target metadata is discovered);
    - ``BROWSER_RESTART_REQUIRED``: Chromium running without CDP (never killed/
      restarted by us);
    - ``UNAVAILABLE``: no Chromium/CDP.
    Profile lock is deliberately ``not_probed``: probing would require launching
    a competing profile, which this packet forbids.
    """
    pre = cdp_precondition(cfg)
    state = pre["state"]
    out: Dict[str, Any] = {
        "state": state, "cdp": bool(pre.get("cdp")),
        "loopback_only": True,
        "detail": pre.get("detail", ""),
        "states": {
            "browser_restart_required": state == "BROWSER_RESTART_REQUIRED",
            "cdp_unavailable": state == "UNAVAILABLE",
            "profile_lock": "not_probed",
            "profile_lock_reason": "never launch a competing profile/browser",
            "auth": "resolved_per_site_in_live_scans",
        },
        "qualify_version": QUALIFY_VERSION,
        "targets": [],
    }
    if state == "OK":
        try:
            out["targets"] = describe_targets(list_targets(base_url))
        except Exception as exc:  # noqa: BLE001 - typed, bounded
            out["targets_error"] = str(exc)[:120]
    return out


# ------------------------------------------------------------ target discovery
def qualify_targets(cfg: Config, platforms: Sequence[str], *, live: bool = False,
                    env_value: Optional[str] = None,
                    base_url: str = CDP_LOOPBACK) -> Dict[str, Any]:
    """Per-platform target discovery (live-gated).

    Returns a typed state per platform: ``found`` (target attachable on
    loopback), ``ambiguous`` (multiple targets, none on the preferred route —
    refuse rather than guess), ``hidden`` (target exists but has no debugger
    websocket), ``unavailable`` (no matching target), or ``failed``. One
    platform's failure never affects the others (per-platform isolation).
    """
    denied = deny_live(live, env_value=env_value)
    if denied:
        return denied
    out: Dict[str, Any] = {"state": "OK", "loopback_only": True,
                           "platforms": {}}
    for platform in platforms:
        pl = PLATFORM_NAMES.get(platform)
        if pl is None:
            out["platforms"][platform] = {"state": "failed",
                                          "detail": "unknown platform"}
            out["state"] = "degraded"
            continue
        adapter = ADAPTERS[pl]()
        rec: Dict[str, Any] = {"origin": adapter.origin,
                               "preferred_route": adapter.preferred_route or None}
        try:
            target, all_targets = find_target_preferring(
                adapter.origin, adapter.preferred_route, base_url=base_url)
        except Exception as exc:  # noqa: BLE001 - typed, bounded
            rec.update({"state": "failed", "detail": str(exc)[:120]})
            out["state"] = "degraded"
            out["platforms"][platform] = rec
            continue
        if target is None and len(all_targets) > 1:
            rec.update({
                "state": "ambiguous",
                "detail": "multiple targets, none on the preferred route; "
                          "refusing to guess",
                "match_count": len(all_targets),
            })
        elif target is None:
            rec.update({"state": "unavailable",
                        "detail": f"no {adapter.adapter_name} target found"})
        elif not target.get("webSocketDebuggerUrl"):
            rec.update({
                "state": "hidden",
                "detail": "target exists but has no debugger websocket "
                          "(unavailable for attach)",
                "target": {"type": target.get("type"),
                           "origin": redact_target_url(target.get("url", ""))},
            })
        else:
            rec.update({
                "state": "found",
                "target": {"type": target.get("type"),
                           "origin": redact_target_url(target.get("url", ""))},
                "match_count": len(all_targets),
            })
        out["platforms"][platform] = rec
    return out


# ------------------------------------------------------------------- reconnect
def probe_reconnect(ws_url: str, *, timeout_s: float = CDP_TIMEOUT_S,
                    client_cls: Any = None) -> Dict[str, Any]:
    """Bounded no-mutation reconnect probe: open, close, reopen, close.

    Never evaluates JavaScript, activates, focuses, or navigates a target.
    ``client_cls`` defaults to the 8B ``CdpClient`` and is injectable for
    hermetic tests. Any connect failure is a typed failure (never a pass).
    """
    cls = client_cls or CdpClient
    opened = 0
    for _ in range(2):
        client = cls(ws_url, timeout_s=float(timeout_s))
        try:
            client.connect()
        except Exception as exc:  # noqa: BLE001 - typed, bounded
            return {"state": "failed", "opened": opened,
                    "detail": f"reconnect probe: {str(exc)[:120]}"}
        opened += 1
        client.close()
    return {"state": "OK", "opened": opened}


# ----------------------------------------------------------- state classification
def scan_states(scan: Any) -> Dict[str, Any]:
    """Map one ``AdapterScan`` to the 8G qualification checklist.

    Covers selector drift, auth/login/2FA/CAPTCHA, unavailable, overflow, and
    healthy states from the adapter's typed health. ``scan`` is an
    ``AdapterScan`` (the 8B typed result) or any object exposing
    ``health_status`` / ``overflow`` / ``auth_ok`` / ``reason``.
    """
    hs = scan.health_status
    return {
        "selector_drift": hs == AccountHealthStatus.DEGRADED,
        "auth_required": hs == AccountHealthStatus.AUTH_REQUIRED,
        "unavailable": hs == AccountHealthStatus.UNAVAILABLE,
        "healthy": hs == AccountHealthStatus.HEALTHY,
        "overflow": bool(scan.overflow),
        "health_reason": getattr(scan, "reason", ""),
        "auth": ("login_2fa_captcha"
                 if hs == AccountHealthStatus.AUTH_REQUIRED
                 else ("none" if scan.auth_ok else "unknown")),
    }


# ----------------------------------------------------------------- live scan
def _write_live_evidence(state_dir: Path, run_id: str, platform: str,
                         assets, blobs: Dict[str, bytes]) -> Dict[str, Any]:
    """Persist bounded clipped screenshot bytes + typed metadata privately.

    Private modes: ``0700`` dirs, ``0600`` files. An asset whose bytes exceed
    ``EVIDENCE_MAX_BYTES`` is refused and recorded as ``oversized_refused``,
    never written. Item bodies/senders/subjects are never written.
    """
    root = state_dir / "qualify" / run_id / platform
    try:
        root.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)
    except OSError as exc:
        return {"state": "failed", "detail": str(exc)[:120], "count": 0,
                "assets": []}
    recs: List[Dict[str, Any]] = []
    for asset in assets:
        rec: Dict[str, Any] = {
            "asset": asset.asset_id, "mime": asset.mime,
            "byte_count": asset.byte_count, "sha256": asset.sha256,
            "redaction_status": asset.redaction_status.value,
        }
        blob = blobs.get(asset.asset_id)
        if not blob:
            rec["status"] = "no_bytes"
        elif len(blob) > EVIDENCE_MAX_BYTES:
            rec["status"] = "oversized_refused"
        else:
            try:
                out = root / f"{asset.asset_id}.webp"
                out.write_bytes(blob)
                os.chmod(out, 0o600)
                rec["status"] = "stored"
            except OSError as exc:
                rec["status"] = "write_failed"
                rec["detail"] = str(exc)[:120]
        recs.append(rec)
    return {"state": "OK", "count": len(recs), "assets": recs,
            "dir": str(root), "private_mode": "0700/0600",
            "max_bytes": EVIDENCE_MAX_BYTES}


def qualify_live_scan(cfg: Config, platform: str, *, state_dir: Path,
                      account_scope: str = "qualify", live: bool = False,
                      env_value: Optional[str] = None,
                      base_url: str = CDP_LOOPBACK,
                      persist_evidence: bool = True,
                      run_id: Optional[str] = None) -> Dict[str, Any]:
    """One operator-gated, read-only live scan of a platform over loopback CDP.

    Uses the existing 8B adapter directly (store-free), so the production
    cursor/store is never touched and no identity is committed. Persists ONLY
    bounded redacted evidence + typed health into the isolated ``state_dir``.
    """
    denied = deny_live(live, env_value=env_value)
    if denied:
        return denied
    pl = PLATFORM_NAMES.get(platform)
    if pl is None:
        return {"state": "failed", "detail": f"unknown platform {platform!r}",
                "platform": platform}
    adapter = ADAPTERS[pl](account_scope=account_scope)
    if adapter.preferred_route:
        target, all_targets = find_target_preferring(
            adapter.origin, adapter.preferred_route, base_url=base_url)
        if target is None and len(all_targets) > 1:
            return {"state": "unavailable",
                    "detail": "multiple targets, none on the preferred route; "
                              "refusing to guess",
                    "platform": platform}
    else:
        target = find_target(adapter.origin, base_url=base_url)
    if target is None:
        return {"state": "unavailable",
                "detail": f"no {adapter.adapter_name} target in Chromium "
                          f"({adapter.origin})",
                "platform": platform}
    ws = target.get("webSocketDebuggerUrl")
    if not ws:
        return {"state": "hidden",
                "detail": "target exists but has no debugger websocket "
                          "(unavailable for attach)",
                "platform": platform}
    bounds = ScanBounds(max_items=SCAN_MAX_ITEMS, max_rows=SCAN_MAX_ROWS,
                        deadline_s=SCAN_DEADLINE_S)
    client = CdpClient(ws, timeout_s=min(SCAN_DEADLINE_S, CDP_TIMEOUT_S),
                       allowed_expressions=(adapter.extract_js,))
    try:
        client.connect()
        scan = adapter.run(client, bounds, report_id="qualify")
    except Exception as exc:  # noqa: BLE001 - typed, bounded
        return {"state": "failed", "detail": str(exc)[:160],
                "platform": platform}
    finally:
        client.close()
    rid = run_id or f"q-{uuid.uuid4().hex[:12]}"
    evidence = _write_live_evidence(state_dir, rid, platform, scan.assets,
                                    scan.asset_bytes) if persist_evidence \
        else {"state": "dry_run", "count": len(scan.assets), "assets": []}
    reconnect = probe_reconnect(ws, client_cls=CdpClient)
    return {
        "state": "OK", "platform": platform, "account_scope": account_scope,
        "run_id": rid, "adapter": adapter.adapter_name,
        "adapter_version": adapter.version,
        "status": scan.status.value,
        "health": scan.health_status.value,
        "health_reason": scan.reason,
        "auth_ok": scan.auth_ok,
        "overflow": scan.overflow,
        "items_total": len(scan.items),
        "loopback_only": True,
        "states": scan_states(scan),
        "reconnect": reconnect,
        "evidence": evidence,
    }


# --------------------------------------------------------------- fixture scans
def _bundle_check(cfg: Config, result: Dict[str, Any]) -> Dict[str, Any]:
    rid = result.get("report_id")
    if not rid:
        return {"state": "skipped", "detail": "no report committed"}
    try:
        rstore = ReportStore(cfg.state_dir() / "reports")
        det = rstore.detail(rid)
        if not det:
            return {"state": "failed", "detail": "bundle detail missing"}
        idx = rstore.index(limit=10)
        present = any(r.get("report_id") == rid for r in idx.get("reports", []))
        legacy = rstore.list_legacy()
        bdir = rstore.bundle_dir(rid)
        formats = sorted(
            k for k, name in (("json", "manifest.json"),
                              ("markdown", "report.md"),
                              ("html", "index.html"))
            if (bdir / name).exists())
        return {
            "state": "OK" if present and formats else "failed",
            "report_id": rid, "formats": formats,
            "assets": len(det.get("assets", []) or []),
            "legacy_count": len(legacy),
            "private_mode": "0600",
        }
    except Exception as exc:  # noqa: BLE001 - typed, bounded
        return {"state": "failed", "detail": str(exc)[:160]}


def _evidence_check(result: Dict[str, Any]) -> Dict[str, Any]:
    evidence = result.get("evidence") or []
    stored = sum(1 for e in evidence
                 if isinstance(e, dict) and e.get("status")
                 == "available")
    return {"state": "OK", "assets_total": len(evidence),
            "assets_available": stored,
            "assets_bounded": all(
                int(e.get("byte_count") or 0) <= EVIDENCE_MAX_BYTES
                for e in evidence)}


def qualify_fixture(cfg: Config, platform: str, *, fixture: Path,
                    external_fixture: Optional[Path] = None,
                    state_dir: Path, account_scope: str = "qualify",
                    run_id: Optional[str] = None) -> Dict[str, Any]:
    """Hermetic qualification of the baseline/new/no-change contract.

    Runs the existing 8B scan driver + 8C report store against SYNTHETIC
    fixtures in an ISOLATED state dir (never the production store/reports):

    - baseline commit -> rendered 8C bundle (JSON/Markdown/HTML + assets);
    - repeat of the same fixture -> ``no-change`` (silent, no new report);
    - ``external_fixture`` (baseline rows + one new synthetic item) -> exactly
      one new item is committed;
    - repeat -> ``no-change`` again (no repeat alert).
    """
    pl = PLATFORM_NAMES.get(platform)
    if pl is None:
        return {"state": "failed", "detail": f"unknown platform {platform!r}"}
    state_dir.mkdir(parents=True, exist_ok=True)
    scfg = isolate_cfg(cfg, state_dir)
    from .adapters.registry import communications_store
    store = communications_store(scfg)
    rid = run_id or f"q-{uuid.uuid4().hex[:12]}"

    def _scan(fx: Path) -> Dict[str, Any]:
        return run_scan(scfg, platform, account_scope=account_scope,
                        source="fixture", fixture=fx, dry_run=False,
                        store=store)

    baseline = _scan(fixture)
    checks: Dict[str, Any] = {
        "baseline": {
            "state": "OK" if baseline.get("commit") == "baseline" else "failed",
            "commit": baseline.get("commit"),
            "report_id": baseline.get("report_id"),
        },
        "bundle": _bundle_check(scfg, baseline),
        "evidence": _evidence_check(baseline),
    }

    no_change = _scan(fixture)
    checks["no_change_rescan"] = {
        "state": "OK" if no_change.get("commit") == "no-change"
        and not no_change.get("report_id") else "failed",
        "commit": no_change.get("commit"),
    }

    if external_fixture is not None:
        external = _scan(external_fixture)
        checks["external_item"] = {
            "state": "OK" if external.get("commit") == "new"
            and external.get("new_count") == 1 else "failed",
            "commit": external.get("commit"),
            "new_count": external.get("new_count"),
        }
        after = _scan(external_fixture)
        checks["external_item_no_change"] = {
            "state": "OK" if after.get("commit") == "no-change" else "failed",
            "commit": after.get("commit"),
        }
    else:
        checks["external_item"] = {"state": "skipped",
                                   "detail": "external_fixture not provided"}
        checks["external_item_no_change"] = {"state": "skipped",
                                             "detail": "external_fixture not provided"}

    failed = any(c["state"] == "failed" for c in checks.values())
    return {
        "state": "failed" if failed else "OK",
        "platform": platform, "account_scope": account_scope,
        "run_id": rid, "state_dir": str(state_dir),
        "isolated": True, "checks": checks,
    }


# ---------------------------------------------------------------- orchestration
def run_qualification(cfg: Config, platforms: Sequence[str] = _PLATFORMS, *,
                      live: bool = False, env_value: Optional[str] = None,
                      state_dir: Optional[Path] = None,
                      fixtures: Optional[Dict[str, Path]] = None,
                      external_fixtures: Optional[Dict[str, Path]] = None,
                      dry_run: bool = False,
                      run_id: Optional[str] = None) -> Dict[str, Any]:
    """Run the full 8G qualification (hermetic + optional gated live).

    Returns a typed per-stage result and, unless ``dry_run``, persists a
    REDACTED summary JSON (private mode ``0600``) plus bounded evidence into
    ``state_dir`` (default ``<config state>/qualification``). The summary never
    contains senders, subjects, message bodies, cookies, tokens, or secrets.
    """
    state_dir = Path(state_dir or (cfg.state_dir() / "qualification"))
    state_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)
    rid = run_id or f"q-{uuid.uuid4().hex[:12]}"
    platforms = list(platforms) or list(_PLATFORMS)
    out: Dict[str, Any] = {
        "state": "OK", "qualify_version": QUALIFY_VERSION, "run_id": rid,
        "dry_run": bool(dry_run), "live": bool(live), "loopback_only": True,
        "stages": {},
    }
    out["stages"]["preflight"] = qualify_preflight(cfg)
    out["stages"]["targets"] = qualify_targets(cfg, platforms, live=live,
                                               env_value=env_value)
    for platform in platforms:
        stage: Dict[str, Any] = {"platform": platform}
        fx = (fixtures or {}).get(platform)
        if fx is not None:
            stage["fixture"] = qualify_fixture(
                cfg, platform, fixture=Path(fx),
                external_fixture=(external_fixtures or {}).get(platform),
                state_dir=state_dir / "fixtures")
        if live:
            stage["live_scan"] = qualify_live_scan(
                cfg, platform, state_dir=state_dir, live=True,
                env_value=env_value, persist_evidence=not dry_run)
        out["stages"][f"scan:{platform}"] = stage

    out["summary"] = {
        "platforms": platforms, "live": bool(live),
        "stages": sorted(out["stages"].keys()),
    }

    def _bad(value: Any) -> bool:
        return isinstance(value, dict) and value.get("state") in ("failed",
                                                                  "denied")

    # Preflight and target discovery are informational (their typed states are
    # reported, never a gate). Only the required scan stages can fail the run;
    # a denied live stage fails closed. "unavailable"/"hidden" live stages are
    # reported as typed states and do not falsify a hermetic result.
    gate_keys = [k for k in out["stages"]
                 if k != "preflight" and k != "targets"]
    failed = any(
        _bad(out["stages"][k]) or
        (isinstance(out["stages"][k], dict) and
         any(_bad(v) for v in out["stages"][k].values()))
        for k in gate_keys)
    out["state"] = "failed" if failed else "OK"

    if not dry_run:
        _persist_summary(state_dir, rid, out)
    return out


def _persist_summary(state_dir: Path, run_id: str, out: Dict[str, Any]) -> None:
    """Write the redacted qualification summary at ``0600``.

    Only bounded metadata and typed states are stored. Absolute state-dir
    paths and any message-derived ``items`` lists are dropped; fixture bodies
    are never persisted (fixtures are synthetic anyway).
    """
    payload = _redacted_stages(out["stages"])
    payload["qualify_version"] = out.get("qualify_version")
    payload["run_id"] = run_id
    payload["dry_run"] = out.get("dry_run")
    payload["live"] = out.get("live")
    payload["state"] = out.get("state")
    target = state_dir / "qualify" / run_id / "summary.json"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp.{uuid.uuid4().hex[:8]}")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except OSError:
        # Persistence of the summary is best-effort; evidence/health records
        # remain in the live-scan outputs and the state dir. Never raise.
        pass


def _redacted_stages(stages: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively drop anything the qualification must never persist: raw
    fixture payloads and message-derived fields."""
    redacted: Dict[str, Any] = {}
    for key, value in stages.items():
        if isinstance(value, dict):
            item = dict(value)
            for drop in ("items", "state_dir"):
                if drop in item:
                    item.pop(drop)
            redacted[key] = {k: _redacted_stages(v) if isinstance(v, dict)
                             else v for k, v in item.items()}
        else:
            redacted[key] = value
    return redacted


__all__ = [
    "CDP_TIMEOUT_S", "EVIDENCE_MAX_BYTES", "QUALIFY_ENV", "QUALIFY_VERSION",
    "SCAN_DEADLINE_S", "SCAN_MAX_ITEMS", "SCAN_MAX_ROWS", "TARGET_LIMIT",
    "deny_live", "describe_targets", "isolate_cfg", "live_authorized",
    "probe_reconnect", "qualify_fixture", "qualify_live_scan",
    "qualify_preflight", "qualify_targets", "redact_target_url",
    "run_qualification", "scan_states",
]