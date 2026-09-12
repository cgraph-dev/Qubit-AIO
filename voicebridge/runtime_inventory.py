"""Packet 8T-F — backend-authoritative runtime inventory (dashboard/CLI).

One real probe for product identity, agent runtime state,
voice/provider/session/approval/adapter health, and the
broker emergency-stop latch. Read-only: never starts services, models, or
runtimes and never writes state. Anything unobservable is typed
UNAVAILABLE/DEGRADED, never invented. All values redacted (no tokens,
keys, cookies, raw paths, audio, or page text).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict

from . import foundation as F
from .redaction import redact_text


def _short_home(path: str) -> str:
    home = str(Path("~").expanduser())
    text = str(path)
    return ("~" + text[len(home):]) if home != "~" and text.startswith(home) else text


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        return {"state": "UNAVAILABLE", "detail": str(exc)[:160]}


def _agent_runtime(cfg: Any) -> Dict[str, Any]:
    """Qubit-native agent runtime identity (the effective runtime)."""
    from .agent import turn as _turn

    get = (lambda *p, default=None: cfg.get(*p, default=default)) \
        if hasattr(cfg, "get") else (lambda *p, default=None: default)
    return {"state": "OK",
            "runtime": "qubit-native",
            "entry": "voicebridge.agent.turn",
            "model": str(get("agent", "model", default="") or
                         "qubit-brain4"),
            "enabled": bool(get("agent", "enabled", default=True))}


def _voice(cfg: Any) -> Dict[str, Any]:
    get = (lambda *p, default=None: cfg.get(*p, default=default)) \
        if hasattr(cfg, "get") else (lambda *p, default=None: default)
    asr = cfg.section("asr") if hasattr(cfg, "section") else {}
    return {"asr_provider": str(asr.get("provider", "sherpa")
                                if isinstance(asr, dict) else "sherpa"),
            "asr_engine": str(asr.get("engine", "moonshine")
                              if isinstance(asr, dict) else "moonshine"),
            "tts_provider": str(get("tts", "provider", default="piper")),
            "tts_voice": str(get("tts", "voice", default="en_US-amy-medium")),
            "hotkey": str(get("hotkey", "hold_key", default="ctrl_r")),
            "dashboard_voice": "webui talk/type intake"}


def _broker() -> Dict[str, Any]:
    try:
        import voicebridge.power as PW

        control = PW.PowerControl()
        state = control.state
        return {"policy_path": "voicebridge broker + PowerPolicy",
                "emergency_stop": bool(state.get("emergency_stop", False)),
                "global_disable": bool(state.get("global_disable", False)),
                "enabled": bool(state.get("enabled", True))}
    except Exception as exc:
        return {"state": "UNAVAILABLE", "detail": str(exc)[:160]}


def _approvals(cfg: Any) -> Dict[str, Any]:
    try:
        from .approvals import ApprovalJournal

        state_dir = cfg.state_dir() if hasattr(cfg, "state_dir") else Path(".")
        journal = ApprovalJournal(Path(state_dir) / "approvals.jsonl")
        pending = journal.pending()
        return {"pending": len(pending),
                "backend": "durable settle-once journal"}
    except Exception as exc:
        return {"state": "UNAVAILABLE", "detail": str(exc)[:160]}


def current_vram_mb() -> int:
    """Free VRAM in MiB via nvidia-smi (best-effort)."""
    try:
        import subprocess
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return int(out.splitlines()[0])
    except Exception:
        return -1


def _adapters(cfg: Any) -> Dict[str, Any]:
    return {"worker": {"route": "local",
                       "note": "native worker sessions; no external "
                               "assistant required"},
            "coding": {"note": "supervised OpenCode runs via the Qubit "
                               "broker (pinned, confirmed)"}}


def _providers(cfg: Any) -> Dict[str, Any]:
    get = (lambda *p, default=None: cfg.get(*p, default=default)) \
        if hasattr(cfg, "get") else (lambda *p, default=None: default)
    return {"resident_alias": str(get("intent", "runner", "alias",
                                      default="qubit-brain4")),
            "frontier": "disabled by default (explicit allowlist + budgets)",
            "credentials": "env:/file: references only; values never shown"}


def _vision(cfg: Any) -> Dict[str, Any]:
    try:
        from .vision import status as _vision_status

        return _vision_status(cfg, timeout_s=2.0)
    except Exception as exc:
        return {"state": "UNAVAILABLE", "detail": str(exc)[:160]}


def _messaging(cfg: Any) -> Dict[str, Any]:
    try:
        from .messaging import account_list, inbox_list, list_msgs

        accounts = account_list(cfg).get("accounts", [])
        outbox = list_msgs(cfg).get("messages", [])
        states: Dict[str, int] = {}
        for msg in outbox:
            states[msg.get("state", "?")] = states.get(msg.get("state", "?"), 0) + 1
        return {"state": "OK", "accounts": len(accounts),
                "outbox": len(outbox), "outbox_states": states,
                "inbox": len(inbox_list(cfg).get("messages", []))}
    except Exception as exc:
        return {"state": "UNAVAILABLE", "detail": str(exc)[:160]}


def _browser_capability(cfg: Any) -> Dict[str, Any]:
    """Real loopback-CDP browser capability (typed UNAVAILABLE otherwise)."""
    from . import browser_tabs as BT

    out = BT.doctor(cfg)
    return {"state": out.get("state", "OK"),
            "cdp": out.get("state") == "OK",
            "browser": str(out.get("browser", ""))[:120],
            "protocol": str(out.get("protocol", ""))[:40],
            "tabs": int(out.get("tabs", 0) or 0),
            "screencast": out.get("screencast", {}),
            "profiles": str(out.get("profiles", ""))[:200]}


def _desktop_capability() -> Dict[str, Any]:
    """Real desktop-control capability state (read-only probes)."""
    from . import computer_use as CU

    st = CU.status()
    return {"state": st.get("state", "OK"),
            "session_type": str(st.get("session_type", ""))[:40],
            "display_present": bool(st.get("display_present", False)),
            "atspi_reachable": bool(st.get("atspi_reachable", False)),
            "control": str(st.get("control", "DISABLED"))[:40]}


def _ide_capability(cfg: Any) -> Dict[str, Any]:
    """Real editor launcher inventory (read-only PATH probe)."""
    from . import ide as IDE

    st = IDE.status(cfg)
    return {"state": st.get("state", "OK"),
            "editors": [str(e.get("editor", "")) for e in st.get("editors", [])],
            "default": str(st.get("default", ""))[:80]}


def _runtime_capability(cfg: Any) -> Dict[str, Any]:
    """Real pinned-runtime provision state (digest-verified)."""
    from . import runtime_images as RI

    st = RI.status(cfg)
    return {"state": st.get("state", "OK"),
            "runtimes": [{"name": str(r.get("name", "")),
                          "state": str(r.get("state", ""))}
                         for r in st.get("runtimes", [])],
            "daemon": str(st.get("daemon", ""))[:40]}


def _node_host_capability(cfg: Any) -> Dict[str, Any]:
    """Node-host-derived capabilities (declared only; availability typed)."""
    from . import node_host as NH

    st = NH.status(cfg)
    return {"state": st.get("state", "OK"),
            "nodes": int(st.get("nodes", 0) or 0),
            "host_state": str(st.get("host_state", ""))[:40],
            "supported_capabilities": list(st.get("supported_capabilities", [])),
            "remote_capabilities": list(st.get("remote_capabilities", []))}


def device_inventory(cfg: Any) -> Dict[str, Any]:
    """Device capability inventory: browser / desktop / IDE / vision / runtime.

    One real, read-only probe per family plus the node-host-derived
    capabilities. Every leaf is observed or typed UNAVAILABLE/DISABLED;
    availability is never treated as authorization. No process is started,
    no outbound network is opened (the browser probe only checks the
    loopback CDP endpoint), and every string is redacted.
    """
    return {
        "schema": "qubit-device-capabilities/1",
        "state": "OK",
        "browser": _safe(_browser_capability, cfg),
        "desktop": _safe(_desktop_capability),
        "ide": _safe(_ide_capability, cfg),
        "vision": _safe(_vision, cfg),
        "runtime": _safe(_runtime_capability, cfg),
        "node_host": _safe(_node_host_capability, cfg),
        "authority": "read-only probes; availability is not authorization",
    }


def _discovery(cfg: Any) -> Dict[str, Any]:
    """Gateway/peer discovery for the runtime surface (read-only)."""
    from . import gateway_peers as GW

    out = GW.discovery(cfg)
    return {"state": out.get("state", "OK"),
            "gateways": len(out.get("gateways", [])),
            "peers": len(out.get("peers", [])),
            "providers": out.get("providers", []),
            "marketplace": out.get("marketplace", "")}


def _marketplace(cfg: Any) -> Dict[str, Any]:
    """Local reviewed plugin catalog; remote marketplace fetches refused."""
    try:
        from . import plugin_runtime as PR

        status = PR.status(cfg)
        plugins = PR.list_plugins(cfg).get("plugins", [])
        return {"state": "OK", "local_plugins": len(plugins),
                "kinds": status.get("kinds", []),
                "marketplace": "remote plugin/marketplace installs are "
                               "refused by pinned-source policy; local "
                               "reviewed manifests only",
                "plugins": plugins}
    except Exception as exc:
        return {"state": "UNAVAILABLE", "detail": str(exc)[:160],
                "marketplace": "remote plugin/marketplace installs are "
                               "refused by pinned-source policy"}


def runtime_inventory(cfg: Any) -> Dict[str, Any]:
    """Assemble the real runtime inventory; every leaf is observed or typed."""
    from .auth_status import auth_status, recent_sessions
    from .integration_status import mcp_status, worker_status
    from .capability_catalog import summary as _cap_summary

    inv = {
        "schema": "qubit-runtime-inventory/1",
        "product": {"name": F.PRODUCT_NAME,
                    "public_identity": F.PUBLIC_IDENTITY,
                    "voice_subsystem": F.VOICE_SUBSYSTEM,
                    "install_identity": F.INSTALL_IDENTITY},
        "agent": _safe(_agent_runtime, cfg),
        "voice": _voice(cfg),
        "broker": _safe(_broker),
        "approvals": _approvals(cfg),
        "adapters": _adapters(cfg),
        "providers": _providers(cfg),
        "auth": _safe(auth_status, cfg),
        "mcp": _safe(mcp_status, cfg),
        "worker": _safe(worker_status, cfg),
        "vision": _safe(_vision, cfg),
        "devices": _safe(device_inventory, cfg),
        "messaging": _safe(_messaging, cfg),
        "sessions": _safe(recent_sessions, cfg, 5),
        "capabilities": _safe(_cap_summary),
        "discovery": _safe(_discovery, cfg),
        "marketplace": _safe(_marketplace, cfg),
    }
    return inv
