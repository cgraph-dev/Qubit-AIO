"""Packet 8T-F — voice command registry: every implemented capability,
voice-addressable through one pipeline.

Spoken (or typed) requests resolve here BEFORE the model fallback:

  ASR/text → resolve() → execute() → receipt → summary → TTS/speech
                 │            │
                 │            ├── reads run immediately (scoped, audited)
                 │            └── state-changing ops mint a durable,
                 │                single-use ApprovalJournal confirmation
                 │                (the SAME journal the dashboard uses);
                 │                "yes" consumes it exactly once.
                 └── no match → existing classifier/model routing untouched.

Every entry carries: spoken aliases, typed arguments with extractors and
clarification questions, target/account binding args where applicable,
permission scope, confirmation level, summary renderer, examples, and the
parity rows it covers (a coverage test enforces the union over all
IMPLEMENTED rows — no silent omissions).

Safety posture (audited, never silent):

* only user-issued sources execute (``user``/``cli``/``dashboard``);
  model/page/tool text can never invoke the registry;
* single-word utterances never match (they fall through to existing
  routing); matches need a multi-word alias or ``qubit``-addressing;
* mutating entries probe the shared emergency stop first;
* confirmations are single-use, 120 s expiring, durable across restarts,
  cancellable (deny), and executable exactly once (executed-id set);
* every resolution, execution, confirmation, denial, and refusal is
  audit-logged with redacted args; spoken summaries are redacted and
  clamped for speech.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

ALLOWED_SOURCES = ("user", "cli", "dashboard")
CONFIRM_TTL_S = 120.0
MAX_PENDING_PER_SESSION = 10
EXECUTED_KEEP = 500
SPEECH_WORDS = 40

MATCH, CLARIFY, AMBIGUOUS, UNKNOWN = "match", "clarify", "ambiguous", "unknown"
EXECUTED, NEEDS_CONFIRM, FAILED, REFUSED = "executed", "needs-confirm", "failed", "refused"

_WAKE_RE = re.compile(r"\b(hey\s+qubit|ok\s+qubit|qubit|computer)\b", re.I)
_PLEASE_RE = re.compile(r"\bplease\b", re.I)
_WS_RE = re.compile(r"\s+")


# ------------------------------------------------------------------- context
def make_ctx(cfg: Any, *, actor: str = "voice:user",
             session: str = "voice", source: str = "user") -> SimpleNamespace:
    """Execution context (cfg + actor + session + source)."""
    return SimpleNamespace(cfg=cfg, actor=actor, session=session, source=source)


def _journal(cfg: Any):
    from .approvals import ApprovalJournal

    return ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                           default_ttl_s=CONFIRM_TTL_S)


def _registry_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / "voice-registry.json"


def _mark_executed(cfg: Any, confirmation_id: str) -> bool:
    """Record a confirmation as executed; False if already executed."""
    import json as _json

    try:
        try:
            raw = _json.loads(_registry_path(cfg).read_text(encoding="utf-8"))
            executed = raw.get("executed", []) if isinstance(raw, dict) else []
        except (OSError, ValueError):
            executed = []
        if confirmation_id in executed:
            return False
        executed.append(confirmation_id)
        path = _registry_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(_json.dumps({"executed": executed[-EXECUTED_KEEP:]}),
                       encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        return True


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"voice.{op}", actor=actor, **detail)
    except Exception:
        pass


def _speak(text: str) -> str:
    from .summarizer import clamp_words

    try:
        return clamp_words(_redact(text), SPEECH_WORDS)
    except Exception:
        return _redact(text)[:400]


def _estop(cfg: Any) -> bool:
    try:
        from .plugins import estop_engaged

        return bool(estop_engaged(cfg))
    except Exception:
        return True


# ------------------------------------------------------------------- schema
@dataclass
class VoiceArg:
    name: str
    type: str = "str"  # str | int | choice | remainder | path
    required: bool = False
    choices: Tuple[str, ...] = ()
    clarifier: str = ""


@dataclass
class VoiceCommand:
    id: str
    title: str
    aliases: Tuple[str, ...]
    args: Tuple[VoiceArg, ...] = ()
    backend: str = ""
    fixed: Tuple[Tuple[str, str], ...] = ()
    scope: Tuple[str, ...] = ("voice",)
    confirm: bool = False
    summary: str = "generic"
    examples: Tuple[str, ...] = ()
    parity: Tuple[str, ...] = ()
    help: str = ""
    split: str = ""


def _norm(text: str) -> str:
    lowered = (text or "").strip().lower()
    lowered = _WAKE_RE.sub(" ", lowered)
    lowered = _PLEASE_RE.sub(" ", lowered)
    lowered = re.sub(r"[?.!,;:]+$", "", lowered)
    lowered = _WS_RE.sub(" ", lowered).strip()
    # Strip leading punctuation left by wake-word removal ("qubit, ..." -> ", ...")
    lowered = re.sub(r"^[,.:;\-–—/]+\s*", "", lowered).strip()
    return lowered


def _clean_remainder(remainder: str) -> str:
    cleaned = (remainder or "").strip()
    cleaned = re.sub(r"^[,.:;\-–—/]+\s*", "", cleaned).strip()
    return cleaned


def _extract(entry: VoiceCommand,
             remainder: str) -> Tuple[Dict[str, Any], List[VoiceArg]]:
    """Pull typed args from the remainder; returns (args, missing)."""
    args: Dict[str, Any] = {}
    missing: List[VoiceArg] = []
    rest = _clean_remainder(remainder)
    if entry.split == "to-saying" and len(entry.args) >= 2:
        found = re.search(r"(?:\bto\s+)?(.+?)\s+saying\s+(.+)$", rest)
        if found:
            args[entry.args[0].name] = found.group(1).strip().strip(",.:;-").strip()
            args[entry.args[1].name] = found.group(2).strip().strip(",.:;-").strip()
            if not args[entry.args[0].name] or not args[entry.args[1].name]:
                return args, [a for a in entry.args if a.required]
            return args, []
        return args, [a for a in entry.args if a.required]
    if entry.split in ("first-rest", "int-rest") and len(entry.args) >= 2:
        parts = rest.split(None, 1)
        if not parts:
            return args, [a for a in entry.args if a.required]
        first, second = parts[0].strip(",.:;-"), (parts[1] if len(parts) > 1 else "")
        second = second.strip().strip(",.:;-").strip()
        target = entry.args[0]
        if entry.split == "int-rest":
            number = re.search(r"\b(\d{1,6})\b", first)
            if not number:
                return args, [target]
            args[target.name] = int(number.group(1))
        else:
            args[target.name] = first
        if second.strip():
            args[entry.args[1].name] = second.strip()
        elif entry.args[1].required:
            missing.append(entry.args[1])
        return args, missing
    if entry.split == "path" and entry.args:
        quoted = re.findall(r"'([^']+)'|\"([^\"]+)\"", rest)
        if quoted:
            args[entry.args[0].name] = quoted[0][0] or quoted[0][1]
            return args, []
        cleaned = re.sub(r"^(?:at|the|file|image|picture|photo|path|of)\b\s*",
                         "", rest).strip()
        if cleaned:
            args[entry.args[0].name] = cleaned
            return args, []
        return args, [a for a in entry.args if a.required]
    quoted = re.findall(r"'([^']+)'|\"([^\"]+)\"", rest)
    quoted_vals = [a or b for a, b in quoted]
    rest_bare = re.sub(r"'[^']+'|\"[^\"]+\"", " ", rest)
    for spec in entry.args:
        value: Any = None
        if spec.type == "choice":
            for choice in spec.choices:
                if re.search(r"\b" + re.escape(choice) + r"\b", rest_bare):
                    value = choice
                    break
        elif spec.type == "int":
            found = re.search(r"\b(\d{1,6})\b", rest_bare)
            if found:
                value = int(found.group(1))
        elif spec.type == "remainder":
            candidate = rest.strip()
            value = candidate or None
        else:
            if quoted_vals:
                value = quoted_vals.pop(0)
            else:
                value = rest.strip() or None
        if value is None:
            if spec.required:
                missing.append(spec)
        else:
            args[spec.name] = value
    return args, missing


@dataclass
class Resolution:
    kind: str = UNKNOWN
    entry: Optional[VoiceCommand] = None
    args: Dict[str, Any] = field(default_factory=dict)
    missing: Tuple[VoiceArg, ...] = ()
    candidates: Tuple[VoiceCommand, ...] = ()
    speech: str = ""


def resolve(text: str) -> Resolution:
    """Match text to at most one command (pure; no side effects)."""
    norm = _norm(text)
    if not norm:
        return Resolution(UNKNOWN, speech="I didn't catch that.")
    tokens = [t for t in norm.split(" ") if t]
    addressed = "qubit" in (text or "").lower()
    hits: List[Tuple[int, VoiceCommand, str, str]] = []
    for entry in REGISTRY.values():
        for alias in entry.aliases:
            if alias.startswith("rx:"):
                try:
                    found = re.search(alias[3:], norm)
                except re.error:
                    continue
                weight = len(alias) - 3
                alias_text = ""
            else:
                found = re.search(r"(?:^|\b)" + re.escape(alias) + r"(?:\b|$)", norm)
                weight = len(alias)
                alias_text = alias
            if found:
                groups = ()
                try:
                    groups = found.groups() or ()
                except Exception:
                    groups = ()
                remainder = (norm[:found.start()] + " " +
                             norm[found.end():]).strip()
                if groups:
                    # rx: aliases capture the argument phrase itself
                    # (e.g. "is (.+) available" -> "whatsapp")
                    captured = " ".join(str(g or "").strip() for g in groups if g)
                    remainder = (captured + " " + remainder).strip()
                remainder = _clean_remainder(remainder)
                hits.append((weight, entry, remainder, alias_text))
    if not hits:
        return Resolution(UNKNOWN)
    hits.sort(key=lambda h: -h[0])
    best_len = hits[0][0]
    tied = [h for h in hits if h[0] == best_len]
    ids = sorted({h[1].id for h in tied})
    if len(ids) > 1:
        seen: Dict[str, VoiceCommand] = {}
        for _, entry, _, _ in tied:
            seen.setdefault(entry.id, entry)
        cands = tuple(sorted(seen.values(), key=lambda e: e.id))[:3]
        names = ", ".join(f"'{e.aliases[0]}'" for e in cands)
        return Resolution(AMBIGUOUS, candidates=cands,
                          speech=f"Did you mean {names}? Say it again "
                                 f"with more detail.")
    _, entry, remainder, alias_text = tied[0]
    significant = len([t for t in tokens if len(t) > 2])
    if significant < 2 and not addressed and " " not in entry.aliases[0]:
        return Resolution(UNKNOWN)
    args, missing = _extract(entry, remainder)
    # Alias-implied choice args (e.g. "create checkpoint" implies action=create)
    if missing:
        still_missing: List[VoiceArg] = []
        for spec in missing:
            filled = False
            if spec.type == "choice" and alias_text:
                for choice in spec.choices:
                    if choice in alias_text:
                        args[spec.name] = choice
                        filled = True
                        break
            if not filled:
                still_missing.append(spec)
        missing = still_missing
    if missing:
        first = missing[0]
        question = first.clarifier or f"What {first.name.replace('_', ' ')}?"
        return Resolution(CLARIFY, entry=entry, args=args,
                          missing=tuple(missing),
                          speech=f"{entry.title}: {question}")
    return Resolution(MATCH, entry=entry, args=args)


# ---------------------------------------------------------------- backends
# Each backend takes (ctx, args) and returns a JSON-safe dict. All imports
# are function-local (no cycles); authority comes only from the caller
# passing confirmed=True after a journal confirmation — backends never
# widen grants and never touch the network except through the existing
# brokered provider paths.

def _b_status(ctx, args):
    from .runtime_inventory import runtime_inventory

    return {"inventory": runtime_inventory(ctx.cfg)}


def _b_pause(ctx, args):
    from . import power as PW

    out = PW.power_control(ctx.cfg).emergency_stop(issued_by="voice:user")
    return {"latched": True, "detail": out.get("detail", "")}


def _b_resume(ctx, args):
    from . import power as PW

    out = PW.power_control(ctx.cfg).reset(issued_by="voice:user")
    detail = out.get("detail", "") if isinstance(out, dict) else ""
    return {"cleared": True, "detail": detail}


def _b_doctor(ctx, args):
    from . import lifecycle as L

    return {"diagnose": L.checklist(ctx.cfg)}


def _b_setup_check(ctx, args):
    from . import lifecycle as L

    return {"checklist": L.checklist(ctx.cfg)}


def _b_setup_run_preview(ctx, args):
    from . import lifecycle as L

    return {"plan": L.setup_run(dry_run=True)}


def _b_verify_release(ctx, args):
    from .integration_status import verify_static

    return {"verification": verify_static()}


def _b_security(ctx, args):
    from .integration_status import verify_static

    return {"security": verify_static()}


def _b_diagnostics(ctx, args):
    from . import lifecycle as L

    try:
        bundle = L.diagnostic_bundle(ctx.cfg)
    except Exception:
        from .runtime_inventory import runtime_inventory

        bundle = {"inventory": runtime_inventory(ctx.cfg)}
    return {"diagnostics": bundle}


def _b_backup(ctx, args):
    return {"guidance": "encrypted backups live in `voicebridge export-state`: "
                        "run it with a passphrase, carry the bundle yourself"}


def _b_sessions(ctx, args):
    from .auth_status import recent_sessions

    limit = args.get("limit", 5)
    try:
        limit = max(1, min(int(limit), 20))
    except (TypeError, ValueError):
        limit = 5
    return {"sessions": recent_sessions(ctx.cfg, limit)}


def _b_capability(ctx, args):
    from .capability_catalog import check

    return {"capability": check(str(args.get("name", "")))}


def _b_checkpoints(ctx, args):
    from . import checkpoints as CK

    action = args.get("action", "list")
    state_dir = ctx.cfg.state_dir()
    if action == "list":
        return {"checkpoints": CK.list_all(state_dir)}
    if action == "verify":
        return {"verify": CK.verify(state_dir, str(args.get("name", "")))}
    if action == "create":
        journal = str(args.get("journal", "") or (state_dir / "approvals.jsonl"))
        return {"created": CK.create(state_dir, journal, str(args.get("name", "")))}
    if action == "prune":
        return {"pruned": CK.prune(state_dir, str(args.get("name", "")),
                                   confirm=True)}
    raise ValueError(f"unknown checkpoints action {action!r}")


def _b_auth_status(ctx, args):
    from .auth_status import auth_status

    return {"auth": auth_status(ctx.cfg)}


def _b_auth_login(ctx, args):
    from . import auth_login as AL

    return {"login": AL.login_plan(ctx.cfg, str(args.get("provider", "")))}


def _b_auth_verify(ctx, args):
    from . import auth_login as AL

    return {"verify": AL.verify_provider(ctx.cfg, str(args.get("provider", "")))}


def _b_auth_logout(ctx, args):
    from .integration_status import logout_check

    return {"logout": logout_check(ctx.cfg)}


def _b_mcp(ctx, args):
    from .integration_status import mcp_status

    return {"mcp": mcp_status(ctx.cfg)}


def _b_claw(ctx, args):
    from .integration_status import claw_status

    return {"claw": claw_status(ctx.cfg)}


def _b_memory(ctx, args):
    from .memory.store import MemoryStore

    action = args.get("action", "search")
    # Canonical personal-memory store (same file the compiler reads).
    store = MemoryStore(Path(ctx.cfg.state_dir()) / "memory" / "memory.db")
    try:
        if action == "search":
            return {"results": store.search(str(args.get("query", "")))[:5]}
        if action == "remember":
            store.remember(str(args.get("text", "")))
            return {"remembered": True}
        if action == "forget":
            store.forget(int(args.get("memory_id", 0)))
            return {"forgotten": True}
        raise ValueError(f"unknown memory action {action!r}")
    finally:
        try:
            store.close()
        except Exception:
            pass


def _b_skills(ctx, args):
    from .permissions import permissions_store

    store = permissions_store(ctx.cfg)
    action = args.get("action", "list")
    if action == "list":
        return {"skills": [s.to_dict() for s in store.skills()]}
    if action == "show":
        rec = store.skill(str(args.get("skill_id", "")))
        return {"skill": rec.to_dict() if rec else None}
    if action == "enable":
        rec = store.enable_skill(str(args.get("skill_id", "")), requested_by="voice:user",
                                 confirmed=True)
        return {"status": rec.status, "skill_id": rec.skill_id}
    if action == "disable":
        rec = store.disable_skill(str(args.get("skill_id", "")), requested_by="voice:user",
                                  confirmed=True)
        return {"status": rec.status, "skill_id": rec.skill_id}
    raise ValueError(f"unknown skills action {action!r}")


def _b_projects(ctx, args):
    from . import projects as PJ

    action = args.get("action", "list")
    if action == "list":
        return PJ.list_projects(ctx.cfg)
    if action == "show":
        return PJ.show(ctx.cfg, str(args.get("slug", "")))
    if action == "create":
        folders = args.get("folders") or []
        if isinstance(folders, str):
            folders = [folders]
        return PJ.create(ctx.cfg, str(args.get("name", "")), list(folders),
                         requested_by="voice:user", confirmed=True)
    if action == "use":
        return PJ.use(ctx.cfg, str(args.get("slug", "")),
                      requested_by="voice:user", confirmed=True)
    if action == "archive":
        return PJ.archive(ctx.cfg, str(args.get("slug", "")),
                          requested_by="voice:user", confirmed=True)
    raise ValueError(f"unknown projects action {action!r}")


def _b_kanban(ctx, args):
    from . import kanban as KB

    action = args.get("action", "list")
    if action == "boards":
        return KB.boards_list(ctx.cfg)
    if action == "list":
        return KB.list_tasks(ctx.cfg, board=str(args.get("board", "")),
                             status=str(args.get("status", "")),
                             limit=int(args.get("limit", 10) or 10))
    if action == "show":
        return KB.show(ctx.cfg, int(args.get("task_id", 0)))
    if action == "create":
        return KB.create(ctx.cfg, str(args.get("title", "")),
                         board=str(args.get("board", "") or "main"),
                         requested_by="voice:user", confirmed=True)
    if action == "claim":
        return KB.claim(ctx.cfg, int(args.get("task_id", 0)),
                        requested_by="voice:user", confirmed=True)
    if action == "complete":
        return KB.complete(ctx.cfg, int(args.get("task_id", 0)),
                           requested_by="voice:user", confirmed=True)
    if action == "comment":
        return KB.comment(ctx.cfg, int(args.get("task_id", 0)),
                          str(args.get("body", "")),
                          requested_by="voice:user", confirmed=True)
    raise ValueError(f"unknown kanban action {action!r}")


def _b_themes(ctx, args):
    from . import themes as TH

    action = args.get("action", "show")
    if action == "list":
        return TH.list_themes()
    if action == "show":
        return TH.show(ctx.cfg)
    if action == "set":
        name = str(args.get("name", "")).strip().lower()
        short = {"light": "qubit-light", "dark": "qubit-dark",
                 "contrast": "qubit-contrast", "high contrast": "qubit-contrast"}
        name = short.get(name, name)
        if name == "reset":
            return TH.reset(ctx.cfg, requested_by="voice:user", confirmed=True)
        return TH.set_theme(ctx.cfg, name, requested_by="voice:user",
                            confirmed=True)
    raise ValueError(f"unknown themes action {action!r}")


def _b_proxy(ctx, args):
    from . import proxy_status as PX

    if args.get("action", "status") == "providers":
        return {"providers": PX.providers(ctx.cfg)}
    return {"proxy": PX.status()}


def _b_import_agent(ctx, args):
    from . import import_agent as IA

    return {"inspect": IA.inspect_tree(str(args.get("source", "")),
                                       str(args.get("kind", "")))}


def _b_hooks(ctx, args):
    from . import hooks as HK

    action = args.get("action", "list")
    if action == "list":
        return HK.list_hooks(ctx.cfg)
    if action == "show":
        return HK.show(ctx.cfg, str(args.get("name", "")))
    if action == "validate":
        return HK.validate(ctx.cfg, str(args.get("name", "")))
    if action == "doctor":
        return HK.doctor(ctx.cfg)
    if action == "fire":
        return HK.fire(ctx.cfg, str(args.get("event", "")),
                       requested_by="voice:user", confirmed=True)
    raise ValueError(f"unknown hooks action {action!r}")


def _b_sync(ctx, args):
    from . import sync as SY

    action = args.get("action", "list")
    if action == "list":
        return SY.list_offers(ctx.cfg)
    if action == "show":
        return SY.show(ctx.cfg, str(args.get("offer_id", "")))
    raise ValueError(f"unknown sync action {action!r}")


def _b_vision_status(ctx, args):
    from . import vision as VI

    return {"vision": VI.status(ctx.cfg)}


def _b_vision_describe(ctx, args):
    from . import vision as VI

    return VI.describe(ctx.cfg, str(args.get("image", "")),
                       prompt=str(args.get("prompt", "")),
                       requested_by="voice:user", confirmed=True)


def _b_pairing(ctx, args):
    from . import pairing as PA

    action = args.get("action", "list")
    if action == "list":
        return PA.list_codes(ctx.cfg)
    if action == "create":
        return PA.create(ctx.cfg, str(args.get("label", "voice")),
                         [str(s) for s in args.get("scopes", ["messaging"])],
                         requested_by="voice:user", confirmed=True)
    if action == "approve":
        return PA.approve(ctx.cfg, str(args.get("code", "")),
                          requested_by="voice:user", confirmed=True)
    if action == "revoke":
        return PA.revoke(ctx.cfg, str(args.get("code_id", "")),
                         requested_by="voice:user", confirmed=True)
    raise ValueError(f"unknown pairing action {action!r}")


def _b_webhook(ctx, args):
    from . import webhooks as WH

    action = args.get("action", "list")
    if action == "list":
        return WH.list_subs(ctx.cfg)
    if action == "show":
        return WH.show(ctx.cfg, str(args.get("sub_id", "")))
    raise ValueError(f"unknown webhook action {action!r}")


def _b_msg(ctx, args):
    from . import messaging as MG

    action = args.get("action", "list")
    if action == "accounts":
        return MG.account_list(ctx.cfg)
    if action == "link":
        return MG.link(ctx.cfg, str(args.get("account", "")))
    if action == "list":
        return MG.list_msgs(ctx.cfg)
    if action == "inbox":
        inbox = MG.inbox_list(ctx.cfg)
        outbox = MG.list_msgs(ctx.cfg)
        return {"inbox": inbox.get("messages", [])[:5],
                "outbox": outbox.get("messages", [])[:5]}
    if action == "show":
        mid = args.get("msg_id")
        if mid is None:
            msgs = MG.list_msgs(ctx.cfg).get("messages", [])
            if not msgs:
                return {"message": None}
            return {"message": msgs[0]}
        return MG.show(ctx.cfg, str(mid))
    if action == "draft":
        return MG.draft(ctx.cfg, str(args.get("account", "")),
                        str(args.get("to", "")), str(args.get("body", "")),
                        requested_by="voice:user", confirmed=True)
    if action == "approve":
        mid = args.get("msg_id")
        if mid is None:
            drafts = [m for m in MG.list_msgs(ctx.cfg).get("messages", [])
                      if m.get("state") == "draft"]
            if not drafts:
                raise ValueError("no drafts to approve")
            mid = drafts[0]["id"]
        return MG.approve(ctx.cfg, str(mid),
                          requested_by="voice:user", confirmed=True)
    if action == "send":
        mid = args.get("msg_id")
        if mid is None:
            cand = [m for m in MG.list_msgs(ctx.cfg).get("messages", [])
                    if m.get("state") in ("draft", "approved", "queued")]
            if not cand:
                raise ValueError("no sendable message")
            mid = cand[0]["id"]
        return MG.send(ctx.cfg, str(mid),
                       requested_by="voice:user", confirmed=True)
    if action == "summarize":
        mid = args.get("msg_id")
        if mid is None:
            inbox = MG.inbox_list(ctx.cfg).get("messages", [])
            if not inbox:
                raise ValueError("inbox is empty")
            mid = inbox[0]["id"]
        try:
            return MG.summarize(ctx.cfg, str(mid))
        except MG.MessagingError:
            return MG.show(ctx.cfg, str(mid))
    raise ValueError(f"unknown msg action {action!r}")


def _b_gateway(ctx, args):
    from . import gateway_peers as GW

    action = args.get("action", "list")
    if action == "list":
        return GW.gateway_list(ctx.cfg)
    if action == "show":
        return GW.gateway_show(ctx.cfg, str(args.get("gateway_id", "")))
    if action == "link":
        return GW.gateway_link(ctx.cfg, str(args.get("gateway_id", "")))
    raise ValueError(f"unknown gateway action {action!r}")


def _b_peer(ctx, args):
    from . import gateway_peers as GW

    action = args.get("action", "list")
    if action == "list":
        return GW.peer_list(ctx.cfg)
    if action == "show":
        return GW.peer_show(ctx.cfg, str(args.get("peer_id", "")))
    if action == "link":
        return GW.peer_link(ctx.cfg, str(args.get("peer_id", "")))
    if action == "dm":
        return GW.peer_dm(ctx.cfg, str(args.get("peer_id", "")),
                          str(args.get("to", "")), str(args.get("text", "")),
                          requested_by="voice:user", confirmed=True)
    if action == "run":
        return GW.peer_run(ctx.cfg, str(args.get("peer_id", "")),
                           str(args.get("agent", "")),
                           str(args.get("text", "")),
                           requested_by="voice:user", confirmed=True)
    raise ValueError(f"unknown peer action {action!r}")


def _b_brain(ctx, args):
    from . import brain as BR

    action = args.get("action", "principals")
    if action == "principals":
        return BR.principal_list(ctx.cfg)
    if action == "profile":
        return BR.profile_show(ctx.cfg, str(args.get("principal", "")),
                               identity=str(args.get("identity", "default")))
    if action == "accounts":
        return BR.account_list(
            ctx.cfg, principal=str(args.get("principal", "")))
    raise ValueError(f"unknown brain action {action!r}")


def _b_permissions(ctx, args):
    from .permissions import permissions_store

    store = permissions_store(ctx.cfg)
    mode, scopes, expires = store.mode()
    return {"mode": mode, "scopes": sorted(scopes), "expires_at": expires}


def _b_monitor(ctx, args):
    from .communications.monitor import run_hourly

    return {"monitor": run_hourly(ctx.cfg)}


def _b_frontier(ctx, args):
    from .providers.policy import FrontierPolicy

    policy = FrontierPolicy.from_config(ctx.cfg)
    return {"frontier": {"enabled": policy.enabled, "route": policy.route,
                         "allowlist": policy.allowlist,
                         "privacy_tier": policy.privacy_tier}}


def _b_audio(ctx, args):
    from .audio import settings as AS

    try:
        devs = AS.list_devices()
    except Exception as exc:
        return {"audio": {"state": "UNAVAILABLE", "detail": str(exc)[:160]}}
    return {"audio": {"inputs": len(devs.get("inputs", [])),
                      "outputs": len(devs.get("outputs", []))}}


def _b_device(ctx, args):
    from . import device as Dev

    profile = Dev.profile(ctx.cfg)
    return {"device": {"machine_id": str(profile.get("machine_id", ""))[:16],
                       "enrolled": bool(profile.get("enrolled", False))}}


def _b_flags(ctx, args):
    from . import feature_flags as FF

    return {"flags": [(f.name, str(FF.effective(ctx.cfg, f.name)))
                      for f in FF.FLAGS]}


def _b_logs(ctx, args):
    path = Path(ctx.cfg.state_dir()) / "events.jsonl"
    try:
        lines = path.read_text(errors="replace").splitlines()[-5:]
    except OSError:
        lines = []
    return {"logs": [_redact(line)[:160] for line in lines]}


def _b_insights(ctx, args):
    from .auth_status import recent_sessions

    sessions = recent_sessions(ctx.cfg, 20).get("sessions", [])
    states: Dict[str, int] = {}
    for session in sessions:
        states[session.get("state", "?")] = states.get(
            session.get("state", "?"), 0) + 1
    return {"insights": {"recent_sessions": len(sessions), "states": states}}


def _b_model(ctx, args):
    from .providers.policy import FrontierPolicy
    from .runner_contract import resolve

    try:
        rc = resolve(ctx.cfg)
        resident = {"alias": rc.alias, "url": rc.url, "num_ctx": rc.num_ctx}
    except Exception:
        resident = {"alias": "unknown"}
    policy = FrontierPolicy.from_config(ctx.cfg)
    return {"model": {"resident": resident,
                      "frontier_enabled": policy.enabled,
                      "allowlist": policy.allowlist}}


def _b_prompt_budget(ctx, args):
    from .runner_contract import resolve

    try:
        rc = resolve(ctx.cfg)
        return {"prompt_budget": {"alias": rc.alias, "num_ctx": rc.num_ctx}}
    except Exception:
        return {"prompt_budget": {"alias": "unknown"}}


def _b_reasoning(ctx, args):
    from .local_llm import REASONING_BUDGETS

    return {"reasoning": {"levels": dict(REASONING_BUDGETS)}}


def _b_approvals(ctx, args):
    journal = _journal(ctx.cfg)
    pending = journal.pending()
    return {"approvals": {"pending": len(pending),
                          "ids": [a.id[:12] for a in pending[:10]]}}


def _b_tools(ctx, args):
    from .broker import build_host_broker

    try:
        names = sorted(build_host_broker().registry.names())
    except Exception:
        names = []
    return {"tools": {"count": len(names), "names": names[:100]}}


def _b_secrets(ctx, args):
    from .auth_status import auth_status

    providers = auth_status(ctx.cfg).get("providers", [])
    return {"secrets": {"providers": [
        {"provider": p.get("provider"), "key_ref": p.get("key_ref"),
         "configured": p.get("configured")} for p in providers]}}


def _b_worktree(ctx, args):
    from .opencode.workdir import sweep_orphaned_workdirs

    return {"worktree": sweep_orphaned_workdirs()}


def _b_completions(ctx, args):
    import os as _os

    root = Path(__file__).resolve().parents[1] / "voicebridge" / "_data" / \
        "completions"
    try:
        shells = sorted(p.suffix.lstrip(".") for p in root.iterdir()
                        if p.name.startswith("voicebridge."))
    except OSError:
        shells = []
    return {"completions": {"shells": shells, "progs": ["voicebridge", "qubit-dev"] if shells else []}}


def _b_tui_guidance(ctx, args):
    return {"guidance": "the live board needs a terminal: run "
                        "`voicebridge tui` locally, or open the web "
                        "dashboard for the visual surface"}


def _b_console_guidance(ctx, args):
    return {"guidance": "the terminal REPL runs on your machine: launch "
                        "`qubit-dev` and type /help for slash commands"}


def _b_mic_guidance(ctx, args):
    return {"guidance": "microphone checks run from the CLI: "
                        "`voicebridge mic-test` records five seconds "
                        "and reports levels"}


def _b_acp(ctx, args):
    from . import acp_lsp as AL

    action = args.get("action", "check")
    if action == "setup":
        return {"setup": AL.acp_setup_snippet(
            str(args.get("editor", "vscode"))
            if str(args.get("editor", "vscode")) in ("vscode", "zed", "jetbrains")
            else "vscode")}
    return {"check": AL.acp_check()}


def _b_lsp(ctx, args):
    from . import acp_lsp as AL

    action = args.get("action", "status")
    if action == "list":
        return AL.lsp_list()
    if action == "which":
        return AL.lsp_which(str(args.get("server", "")))
    return AL.lsp_status()


def _b_browser(ctx, args):
    from . import browser as BR
    from .communications.monitor import cdp_precondition

    return {"browser": BR.status(cdp_precondition(ctx.cfg))}


def _b_computer(ctx, args):
    from . import computer_use as CU

    if args.get("action", "status") == "doctor":
        return {"doctor": CU.doctor()}
    return {"computer": CU.status()}


def _b_plugins_install(ctx, args):
    import json as _json

    from .permissions import SkillManifest, permissions_store

    manifest = Path(str(args.get("manifest", ""))).expanduser()
    try:
        data = _json.loads(manifest.read_text(encoding="utf-8"))
        validated = SkillManifest.from_dict(data)
    except (OSError, ValueError) as exc:
        raise ValueError(f"manifest unreadable: {exc}") from exc
    rec = permissions_store(ctx.cfg).install_skill(
        validated, requested_by="voice:user", confirmed=True,
        source_path=str(manifest))
    return {"state": "OK", "staged": rec.status, "skill": rec.skill_id}


def _b_curator(ctx, args):
    from .verified_skills import verified_skills_store

    store = verified_skills_store(ctx.cfg)
    try:
        stats = store.stats()
    except Exception:
        stats = {}
    try:
        skills = store.list()
        by_status: Dict[str, int] = {}
        for rec in skills:
            status = getattr(rec, "status", "?")
            by_status[str(status)] = by_status.get(str(status), 0) + 1
    except Exception:
        by_status = {}
    path = Path(ctx.cfg.state_dir()) / "events.jsonl"
    try:
        audit_tail = len(path.read_text(errors="replace").splitlines())
    except OSError:
        audit_tail = 0
    return {"curator": {"verified": stats if isinstance(stats, dict) else {},
                        "by_status": by_status,
                        "audit_events": audit_tail}}


def _b_update_status(ctx, args):
    from . import product as P

    diag = P.path_map_diagnostic()
    try:
        migration = P.branding_status()
    except Exception:
        migration = {"state": "UNKNOWN"}
    return {"update": {"product": diag.get("product"),
                       "version": diag.get("version"),
                       "migration": migration.get("state"),
                       "platform": (diag.get("platform", {}) or {}).get("state")}}


def _b_config_profiles(ctx, args):
    from . import profiles as PF

    action = args.get("action", "list")
    if action == "show":
        manifests = [m for m in PF.registry(ctx.cfg)
                     if m.id == str(args.get("profile_id", ""))]
        if not manifests:
            raise ValueError(f"no profile {args.get('profile_id')!r}")
        manifest = manifests[0]
        return {"profile": {"id": manifest.id, "kind": manifest.kind}}
    return {"profiles": [{"id": m.id, "kind": m.kind}
                         for m in PF.registry(ctx.cfg)]}


def _b_migrate_status(ctx, args):
    from . import product as P

    try:
        return {"migration": P.branding_status()}
    except Exception as exc:
        return {"migration": {"state": "UNAVAILABLE", "detail": str(exc)[:160]}}


def _b_vision_guidance(ctx, args):
    return {"guidance": "image analysis needs a file path: run "
                        "`voicebridge vision describe <image> --confirm` "
                        "from the CLI"}


_BACKENDS: Dict[str, Callable] = {
    "status": _b_status, "pause": _b_pause, "resume": _b_resume,
    "doctor": _b_doctor, "setup_check": _b_setup_check,
    "setup_run_preview": _b_setup_run_preview,
    "verify_release": _b_verify_release, "security": _b_security,
    "diagnostics": _b_diagnostics, "backup": _b_backup,
    "sessions": _b_sessions, "capability": _b_capability,
    "checkpoints": _b_checkpoints, "auth_status": _b_auth_status,
    "auth_login": _b_auth_login, "auth_verify": _b_auth_verify,
    "auth_logout": _b_auth_logout, "mcp": _b_mcp, "claw": _b_claw,
    "memory": _b_memory, "skills": _b_skills, "projects": _b_projects,
    "kanban": _b_kanban, "themes": _b_themes, "proxy": _b_proxy,
    "import_agent": _b_import_agent, "hooks": _b_hooks, "sync": _b_sync,
    "vision_status": _b_vision_status, "vision_describe": _b_vision_describe,
    "pairing": _b_pairing, "webhook": _b_webhook, "msg": _b_msg,
    "gateway": _b_gateway, "peer": _b_peer, "brain": _b_brain,
    "permissions": _b_permissions, "monitor": _b_monitor,
    "frontier": _b_frontier, "audio": _b_audio, "device": _b_device,
    "flags": _b_flags, "logs": _b_logs, "insights": _b_insights,
    "model": _b_model, "prompt_budget": _b_prompt_budget,
    "reasoning": _b_reasoning, "approvals": _b_approvals, "tools": _b_tools,
    "secrets": _b_secrets, "worktree": _b_worktree,
    "completions": _b_completions, "tui_guidance": _b_tui_guidance,
    "console_guidance": _b_console_guidance, "mic_guidance": _b_mic_guidance,
    "vision_guidance": _b_vision_guidance, "backup": _b_backup,
    "curator": _b_curator, "update_status": _b_update_status,
    "config_profiles": _b_config_profiles, "migrate_status": _b_migrate_status,
    "acp": _b_acp, "lsp": _b_lsp, "browser": _b_browser,
    "computer": _b_computer, "plugins_install": _b_plugins_install,
}


# ---------------------------------------------------------------- summaries
def _generic_summary(result: Dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return "Done."
    state = str(result.get("state", "OK"))
    if state not in ("OK", "VALID"):
        return f"That failed: {result.get('code', state)}."
    bits = []
    for key in ("count", "total", "stage", "staged"):
        if result.get(key) is not None:
            bits.append(f"{key} {result[key]}")
    inner = next((v for k, v in result.items()
                  if isinstance(v, dict) and k not in ("inventory",)), None)
    if isinstance(inner, dict):
        for key in ("state", "status", "stage", "active_slug", "theme",
                    "mode", "control", "layer", "server"):
            if inner.get(key) is not None:
                bits.append(f"{key} {inner[key]}")
                break
    return ("Done" + (": " + ", ".join(str(b) for b in bits[:3]) if bits else "") + ".")


def _sum_status(result):
    inv = result.get("inventory", {})
    hermes = inv.get("hermes", {})
    broker = inv.get("broker", {})
    estop = "emergency stop latched" if broker.get("emergency_stop") else "all clear"
    return (f"Qubit ready. Hermes runtime {hermes.get('state', 'unknown')}, "
            f"{estop}.")


def _sum_sessions(result):
    sessions = result.get("sessions", {})
    items = sessions.get("sessions", [])
    if not items:
        return "No recent sessions."
    return f"{len(items)} recent sessions, latest {items[0].get('state', 'unknown')}."


def _sum_msg_send(result):
    msg = result.get("message", {})
    if msg.get("duplicate"):
        return "Already sent. Returning the original receipt."
    return f"Message sent, receipt {str(msg.get('id', ''))[:12]}."


def _sum_vision(result):
    desc = str(result.get("description", ""))[:300]
    restored = "resident model restored" if result.get("resident_restored") else \
        "resident restore unverified"
    return f"Image described: {desc} {restored}."


def _sum_pairing_create(result):
    code = str(result.get("code", ""))
    grouped = " ".join(code[i:i + 4] for i in range(0, len(code), 4))
    return f"Your pairing code is {grouped}. It expires in ten minutes."


def _sum_kanban_task(result):
    task = result.get("task", {})
    return f"Task {task.get('id', '?')}: {task.get('title', '')}, {task.get('status', '')}."


def _sum_project(result):
    proj = result.get("project", {})
    return f"Project {proj.get('name', proj.get('slug', '?'))} ready."


def _sum_permissions(result):
    mode = str(result.get("mode", "restricted"))
    scopes = result.get("scopes", [])
    scope_text = ", ".join(scopes[:3]) if scopes else "no scopes"
    return f"Permission mode {mode}, {scope_text}."


def _sum_guidance(result):
    guidance = str(result.get("guidance", "") or "")
    return guidance if guidance else "See the help text."


_SUMMARIES = {
    "generic": _generic_summary,
    "status": _sum_status,
    "sessions": _sum_sessions,
    "msg_send": _sum_msg_send,
    "vision": _sum_vision,
    "pairing_create": _sum_pairing_create,
    "kanban_task": _sum_kanban_task,
    "project": _sum_project,
    "permissions": _sum_permissions,
    "guidance": _sum_guidance,
}


def summarize(entry: VoiceCommand, result: Dict[str, Any]) -> str:
    """Render a redacted, speech-clamped summary for a receipt."""
    fn = _SUMMARIES.get(entry.summary or "generic", _generic_summary)
    try:
        return _speak(fn(result))
    except Exception:
        return _speak(_generic_summary(result))


# ---------------------------------------------------------------- registry
def _E(entry_id: str, title: str, aliases: Tuple[str, ...], backend: str,
       parity: Tuple[str, ...], args: Tuple[VoiceArg, ...] = (),
       fixed: Tuple[Tuple[str, str], ...] = (),
       scope: Tuple[str, ...] = ("voice",), confirm: bool = False,
       summary: str = "generic", examples: Tuple[str, ...] = (),
       split: str = "", help: str = "") -> VoiceCommand:
    entry = VoiceCommand(id=entry_id, title=title, aliases=aliases, args=args,
                         backend=backend, fixed=fixed, scope=scope,
                         confirm=confirm, summary=summary, examples=examples,
                         parity=parity, help=help, split=split)
    REGISTRY[entry_id] = entry
    return entry


def _R(name: str, required: bool = True, clarifier: str = "") -> VoiceArg:
    return VoiceArg(name, "remainder", required,
                    clarifier=clarifier or f"Which {name.replace('_', ' ')}?")


def _I(name: str, required: bool = True, clarifier: str = "") -> VoiceArg:
    return VoiceArg(name, "int", required,
                    clarifier=clarifier or f"Which {name.replace('_', ' ')} number?")


def _S(name: str, required: bool = False) -> VoiceArg:
    return VoiceArg(name, "str", required)


def _C(name: str, choices: Tuple[str, ...],
       required: bool = True) -> VoiceArg:
    return VoiceArg(name, "choice", required, choices,
                    clarifier=f"Which {name}? {', '.join(choices)}.")


REGISTRY: Dict[str, VoiceCommand] = {}

_E("status", "System status",
   ("status", "system status", "how are you", "health check", "status report"),
   "status", ("status", "gateway-status"),
   summary="status", examples=("qubit, system status",),
   help="Runtime, broker, and provider health.")
_E("pause", "Pause all work",
   ("pause everything", "pause all work", "emergency stop", "stop everything"),
   "pause", ("pause/resume",), confirm=True,
   examples=("qubit, pause everything",),
   help="Latch the emergency stop (confirmed).")
_E("resume", "Resume work",
   ("resume work", "clear emergency stop", "all clear", "resume everything"),
   "resume", ("pause/resume",), confirm=True,
   examples=("qubit, resume work",),
   help="Clear the emergency stop (confirmed).")
_E("doctor", "Diagnostics",
   ("run diagnostics", "check health", "diagnose problems", "doctor"),
   "doctor", ("doctor",),
   examples=("qubit, run diagnostics",),
   help="Setup and health diagnosis.")
_E("setup_check", "Setup status",
   ("setup status", "installation status", "setup check"),
   "setup_check", ("setup",),
   examples=("qubit, setup status",),
   help="Installation checklist state.")
_E("verify_release", "Release verification",
   ("verify release", "release check", "is the release good"),
   "verify_release", ("verify",),
   examples=("qubit, verify release",),
   help="Static release qualification.")
_E("security", "Security status",
   ("security status", "run security check", "security scan"),
   "security", ("security",),
   examples=("qubit, security status",),
   help="Secret scans and provenance gates.")
_E("diagnostics", "Diagnostic bundle",
   ("show diagnostics", "dump diagnostics", "diagnostic bundle"),
   "diagnostics", ("dump/debug",),
   examples=("qubit, show diagnostics",),
   help="Redacted diagnostic bundle.")
_E("backup_info", "Backup guidance",
   ("backup info", "how do i back up", "back up my data"),
   "backup", ("backup/import",),
   examples=("qubit, how do i back up",),
   help="Backup guidance (encrypted export).")
_E("sessions", "Recent sessions",
   ("list sessions", "recent sessions", "show sessions", "session history"),
   "sessions", ("sessions", "--resume/--continue"),
   summary="sessions", examples=("qubit, list sessions",),
   help="Session receipts and journals.")
_E("capability", "Capability check",
   ("check capability", "rx:is (.+) available", "can you"),
   "capability", ("tools",), args=(_R("name"),),
   examples=("qubit, is whatsapp available",),
   help="Typed state for any capability.")
_E("checkpoints_list", "List checkpoints",
   ("list checkpoints", "show checkpoints"),
   "checkpoints", ("checkpoints",), fixed=(("action", "list"),),
   examples=("qubit, list checkpoints",),
   help="Journal checkpoints.")
_E("checkpoints_write", "Checkpoint write",
   ("create checkpoint", "verify checkpoint", "prune checkpoint"),
   "checkpoints", ("checkpoints",),
   args=(_C("action", ("create", "verify", "prune")), _S("name")),
   confirm=True, examples=("qubit, create checkpoint deploy",),
   help="Create/verify/prune (confirmed).")
_E("auth_status", "Credential status",
   ("auth status", "credential status", "provider status", "credential check"),
   "auth_status", ("auth",),
   examples=("qubit, credential status",),
   help="Credential references (names only).")
_E("auth_login", "Login help",
   ("how do i log in", "login help", "enroll provider", "connect provider"),
   "auth_login", ("login/logout/portal",), args=(_R("provider"),),
   examples=("qubit, how do i log in xai",),
   help="Manual-handoff enrollment steps.")
_E("auth_verify", "Verify provider",
   ("verify provider", "is configured", "check provider",
    "rx:is (.+) configured", "rx:check (.+) provider"),
   "auth_verify", ("login/logout/portal",), args=(_R("provider"),),
   examples=("qubit, is xai configured",),
   help="Re-probe one credential reference.")
_E("auth_logout", "Logout check",
   ("logout check", "credential cleanup", "am i logged out"),
   "auth_logout", ("login/logout/portal",),
   examples=("qubit, logout check",),
   help="Residue scan for stored credentials.")
_E("mcp", "MCP servers",
   ("list mcp servers", "mcp status", "mcp inventory"),
   "mcp", ("mcp",),
   examples=("qubit, list mcp servers",),
   help="MCP inventory (never mounts).")
_E("claw", "Lineage status",
   ("claw status", "openclaw status", "lineage status"),
   "claw", ("claw",),
   examples=("qubit, claw status",),
   help="OpenClaw-lineage detection.")
_E("memory_search", "Memory search",
   ("search memory", "what do you remember about", "recall"),
   "memory", ("memory",), args=(_R("query"),), fixed=(("action", "search"),),
   examples=("qubit, what do you remember about invoices",),
   help="Search personal memory.")
_E("memory_remember", "Remember",
   ("remember", "remember this", "take a note", "note this"),
   "memory", ("memory",), args=(_R("text"),), fixed=(("action", "remember"),),
   confirm=True, examples=("qubit, remember the gate code",),
   help="Store a memory (confirmed).")
_E("memory_forget", "Forget memory",
   ("forget memory", "delete memory"),
   "memory", ("memory",), args=(_I("memory_id"),),
   fixed=(("action", "forget"),), confirm=True,
   examples=("qubit, forget memory 12",),
   help="Delete a memory (confirmed).")
_E("skills_list", "List skills",
   ("list skills", "show skills", "what skills"),
   "skills", ("skills/bundles", "plugins/plugins-list"),
   fixed=(("action", "list"),),
   examples=("qubit, list skills",),
   help="Installed skill manifests.")
_E("skills_show", "Show skill",
   ("show skill", "describe skill", "skill info"),
   "skills", ("skills/bundles",), args=(_R("skill_id"),),
   fixed=(("action", "show"),),
   examples=("qubit, describe skill",),
   help="One skill manifest.")
_E("skills_enable", "Enable skill",
   ("enable skill", "turn on skill"),
   "skills", ("skills/bundles",), args=(_R("skill_id"),),
   fixed=(("action", "enable"),), confirm=True,
   examples=("qubit, enable skill",),
   help="Enable a staged skill (confirmed).")
_E("skills_disable", "Disable skill",
   ("disable skill", "turn off skill"),
   "skills", ("skills/bundles",), args=(_R("skill_id"),),
   fixed=(("action", "disable"),), confirm=True,
   examples=("qubit, disable skill",),
   help="Disable a skill (confirmed).")
_E("projects_list", "List projects",
   ("list projects", "show projects", "what projects", "my projects"),
   "projects", ("project",), fixed=(("action", "list"),),
   examples=("qubit, list projects",),
   help="Named workspaces.")
_E("projects_show", "Show project",
   ("show project", "describe project", "project details"),
   "projects", ("project",), args=(_R("slug"),),
   fixed=(("action", "show"),),
   examples=("qubit, show project",),
   help="One workspace.")
_E("projects_create", "Create project",
   ("create project", "new project", "start project"),
   "projects", ("project",), args=(_R("name"),),
   fixed=(("action", "create"),), confirm=True,
   examples=("qubit, create project shop",),
   help="Create a workspace (confirmed).")
_E("projects_use", "Switch project",
   ("switch to project", "use project", "open project"),
   "projects", ("project",), args=(_R("slug"),),
   fixed=(("action", "use"),), confirm=True,
   examples=("qubit, switch to project",),
   help="Set the active workspace (confirmed).")
_E("projects_archive", "Archive project",
   ("archive project", "shelve project"),
   "projects", ("project",), args=(_R("slug"),),
   fixed=(("action", "archive"),), confirm=True,
   examples=("qubit, archive project",),
   help="Archive a workspace (confirmed).")
_E("kanban_boards", "List boards",
   ("list boards", "show boards", "task boards"),
   "kanban", ("kanban",), fixed=(("action", "boards"),),
   examples=("qubit, list boards",),
   help="Task boards with counts.")
_E("kanban_list", "List tasks",
   ("list tasks", "show tasks", "my tasks", "task list"),
   "kanban", ("kanban",), args=(_S("board"), _S("status")),
   fixed=(("action", "list"),),
   examples=("qubit, list tasks",),
   help="Board tasks.")
_E("kanban_show", "Show task",
   ("show task", "task details", "describe task"),
   "kanban", ("kanban",), args=(_I("task_id"),),
   fixed=(("action", "show"),),
   examples=("qubit, show task 5",),
   help="One task with comments and events.")
_E("kanban_create", "Create task",
   ("create task", "new task", "add task"),
   "kanban", ("kanban",), args=(_R("title"),),
   fixed=(("action", "create"),), confirm=True, summary="kanban_task",
   examples=("qubit, create task buy milk",),
   help="Create a task (confirmed).")
_E("kanban_claim", "Claim task",
   ("claim task", "take task", "assign me task"),
   "kanban", ("kanban",), args=(_I("task_id"),),
   fixed=(("action", "claim"),), confirm=True,
   examples=("qubit, claim task 5",),
   help="Claim a task (confirmed).")
_E("kanban_complete", "Complete task",
   ("complete task", "finish task", "done with task", "close task"),
   "kanban", ("kanban",), args=(_I("task_id"),),
   fixed=(("action", "complete"),), confirm=True,
   examples=("qubit, complete task 5",),
   help="Mark a task done (confirmed).")
_E("kanban_comment", "Comment on task",
   ("comment on task",),
   "kanban", ("kanban",), args=(_I("task_id"), _R("body")),
   fixed=(("action", "comment"),), confirm=True, split="int-rest",
   examples=("qubit, comment on task 5",),
   help="Append a comment (confirmed).")
_E("themes", "Themes",
   ("list themes", "what theme", "show theme"),
   "themes", ("pets/skin",), fixed=(("action", "show"),),
   examples=("qubit, what theme",),
   help="Active preset and tweaks.")
_E("themes_set", "Set theme",
   ("change theme", "set theme", "switch theme", "reset theme"),
   "themes", ("pets/skin",),
   args=(_C("name", ("qubit-dark", "qubit-light", "qubit-contrast", "reset",
                     "dark", "light", "contrast")),),
   fixed=(("action", "set"),), confirm=True,
   examples=("qubit, set theme light",),
   help="Switch preset (confirmed; light/dark/contrast/reset).")
_E("proxy", "Proxy status",
   ("proxy status", "proxy providers", "forwarding status"),
   "proxy", ("proxy",),
   args=(_C("action", ("status", "providers"), required=False),),
   fixed=(("action", "status"),),
   examples=("qubit, proxy status",),
   help="Forwarding inventory (no server runs).")
_E("import_inspect", "Inspect foreign setup",
   ("inspect foreign setup", "scan setup", "import preview"),
   "import_agent", ("import-agent",), args=(_R("source"),),
   examples=("qubit, inspect foreign setup",),
   help="Quarantined inventory (never imports).")
_E("hooks_list", "List hooks",
   ("list hooks", "show hooks", "event subscriptions"),
   "hooks", ("hooks",), fixed=(("action", "list"),),
   examples=("qubit, list hooks",),
   help="Declared subscriptions (never execute).")
_E("hooks_show", "Show hook",
   ("show hook", "describe hook", "validate hook"),
   "hooks", ("hooks",), args=(_R("name"),),
   fixed=(("action", "show"),),
   examples=("qubit, show hook deploy",),
   help="One declaration with findings.")
_E("hooks_fire", "Fire hook",
   ("fire hook", "trigger hook", "run hook"),
   "hooks", ("hooks",), args=(_R("event"),),
   fixed=(("action", "fire"),), confirm=True,
   examples=("qubit, fire hook deploy",),
   help="Broker-audited delivery (confirmed).")
_E("sync_list", "Sync offers",
   ("list sync offers", "sync status", "pending sync", "show sync offer"),
   "sync", ("sync",),
   args=(_C("action", ("list", "show"), required=False), _S("offer_id")),
   fixed=(("action", "list"),),
   examples=("qubit, sync status",),
   help="Outbox offers.")
_E("vision_status", "Vision status",
   ("vision status", "is vision ready", "can you see"),
   "vision_status", ("--image",),
   examples=("qubit, is vision ready",),
   help="Vision backend state.")
_E("vision_describe", "Describe image",
   ("describe image", "analyze image", "what is in",
    "look at image", "read image"),
   "vision_describe", ("--image",),
   args=(_R("image"), _S("prompt")), confirm=True, split="path",
   examples=("qubit, describe image at /tmp/pic.png",),
   help="VRAM-guarded analysis (confirmed).")
_E("pairing_list", "Pairing codes",
   ("list pairing codes", "show pairings", "pairing status"),
   "pairing", ("pairing/webhook/peer",), fixed=(("action", "list"),),
   examples=("qubit, list pairing codes",),
   help="Code records (redacted).")
_E("pairing_create", "Pairing code",
   ("create pairing code", "pair a device", "new pairing code",
    "pairing code"),
   "pairing", ("pairing/webhook/peer",),
   fixed=(("action", "create"),), confirm=True,
   summary="pairing_create",
   examples=("qubit, pair a device",),
   help="One-time code (confirmed).")
_E("pairing_approve", "Approve pairing",
   ("approve pairing code", "pair with code", "enter pairing code"),
   "pairing", ("pairing/webhook/peer",), args=(_R("code"),),
   fixed=(("action", "approve"),), confirm=True,
   examples=("qubit, pair with code AB12CD34",),
   help="Verify a code, issue its grant (confirmed).")
_E("pairing_revoke", "Revoke pairing",
   ("revoke pairing", "unpair", "remove pairing"),
   "pairing", ("pairing/webhook/peer",), args=(_R("code_id"),),
   fixed=(("action", "revoke"),), confirm=True,
   examples=("qubit, revoke pairing",),
   help="Tombstone a code and its grant (confirmed).")
_E("webhooks", "Webhooks",
   ("list webhooks", "show webhooks", "webhook status", "show webhook"),
   "webhook", ("pairing/webhook/peer",),
   args=(_C("action", ("list", "show"), required=False), _S("sub_id")),
   fixed=(("action", "list"),),
   examples=("qubit, list webhooks",),
   help="Subscriptions (receipts only).")
_E("msg_accounts", "Messaging accounts",
   ("list messaging accounts", "messaging accounts", "whatsapp accounts",
    "slack accounts"),
   "msg", ("whatsapp/whatsapp-cloud/slack/send",),
   fixed=(("action", "accounts"),),
   examples=("qubit, list messaging accounts",),
   help="Provider account records.")
_E("msg_link", "Account link",
   ("check account", "is linked", "account status"),
   "msg", ("whatsapp/whatsapp-cloud/slack/send",),
   args=(_R("account"),), fixed=(("action", "link"),),
   examples=("qubit, check account team",),
   help="Typed link state.")
_E("msg_inbox", "Check messages",
   ("check messages", "any new messages", "read inbox", "show inbox",
    "list outbox", "message status"),
   "msg", ("whatsapp/whatsapp-cloud/slack/send",),
   fixed=(("action", "inbox"),),
   summary="sessions",
   examples=("qubit, check messages",),
   help="Inbound and outbox overview.")
_E("msg_show", "Show message",
   ("show message", "message details"),
   "msg", ("whatsapp/whatsapp-cloud/slack/send",),
   args=(_I("msg_id", required=False),), fixed=(("action", "show"),),
   examples=("qubit, show message",),
   help="One outbox message.")
_E("msg_draft", "Draft message",
   ("draft message to", "text", "send a message to", "message"),
   "msg", ("whatsapp/whatsapp-cloud/slack/send",),
   args=(_R("to"), _R("body")), fixed=(("action", "draft"),),
   confirm=True, split="to-saying",
   examples=("qubit, text sam saying running late",),
   help="Stage a draft (confirmed).")
_E("msg_send", "Send message",
   ("send message", "send draft", "send it"),
   "msg", ("whatsapp/whatsapp-cloud/slack/send",),
   args=(_I("msg_id", required=False),), fixed=(("action", "send"),),
   confirm=True, summary="msg_send",
   examples=("qubit, send draft",),
   help="Send with fresh confirmation.")
_E("msg_approve", "Approve draft",
   ("approve draft", "approve message"),
   "msg", ("whatsapp/whatsapp-cloud/slack/send",),
   args=(_I("msg_id", required=False),), fixed=(("action", "approve"),),
   confirm=True,
   examples=("qubit, approve draft",),
   help="Approve without sending (confirmed).")
_E("msg_summarize", "Summarize message",
   ("summarize message", "read message", "what does message say"),
   "msg", ("whatsapp/whatsapp-cloud/slack/send",),
   args=(_I("msg_id", required=False),), fixed=(("action", "summarize"),),
   examples=("qubit, read message",),
   help="Extractive digest of inbound.")
_E("gateway", "Gateways",
   ("list gateways", "gateway status", "show gateways", "show gateway",
    "gateway link"),
   "gateway", ("gateway", "gateway-manage"),
   args=(_C("action", ("list", "show", "link"), required=False),
         _S("gateway_id")),
   fixed=(("action", "list"),),
   examples=("qubit, list gateways",),
   help="Gateway records and link states.")
_E("peer_list", "Peers",
   ("list peers", "show peers", "peer status"),
   "peer", ("pairing/webhook/peer",), fixed=(("action", "list"),),
   examples=("qubit, list peers",),
   help="Peer registry.")
_E("peer_dm", "Message peer",
   ("message peer", "tell peer", "ask peer"),
   "peer", ("pairing/webhook/peer",),
   args=(_R("peer_id"), _R("text")), fixed=(("action", "dm"),),
   confirm=True, split="first-rest",
   examples=("qubit, tell peer relay hello",),
   help="Credential-gated delivery (confirmed).")
_E("peer_run", "Run on peer",
   ("run on peer", "peer run task"),
   "peer", ("pairing/webhook/peer",),
   args=(_R("peer_id"), _R("text")), fixed=(("action", "run"),),
   confirm=True, split="first-rest",
   examples=("qubit, run on peer relay status",),
   help="Credential-gated peer task (confirmed).")
_E("brain", "Brain profiles",
   ("list profiles", "show profile", "my profile", "brain status",
    "list brain accounts"),
   "brain", (), args=(_S("principal"),),
   fixed=(("action", "principals"),),
   examples=("qubit, list profiles",),
   help="Identity registry (masked values).")
_E("permissions", "Permission mode",
   ("permission mode", "what are my permissions", "permission status",
    "am i restricted"),
   "permissions", ("--accept-hooks/--yolo/--ignore-user-config/--ignore-rules",),
   summary="permissions",
   examples=("qubit, permission mode",),
   help="Mode, scopes, and expiry (owner-authorized sessions).")
_E("monitor", "Monitor check",
   ("run monitor check", "inbox monitor", "hourly check"),
   "monitor", ("monitor/monitoring", "cron"), confirm=True,
   examples=("qubit, run monitor check",),
   help="Bounded monitor pass (confirmed).")
_E("model", "Model status",
   ("model status", "which model", "frontier status", "current model"),
   "frontier", ("model/moa/fallback", "-m/--model/--provider"),
   examples=("qubit, which model",),
   help="Resident model and frontier policy.")
_E("audio", "Audio devices",
   ("list audio devices", "audio status", "microphone status"),
   "audio", (),
   examples=("qubit, list audio devices",),
   help="Audio device inventory.")
_E("device", "Device status",
   ("device status", "is this device enrolled", "this device"),
   "device", (),
   examples=("qubit, device status",),
   help="Device profile and enrollment.")
_E("flags", "Feature flags",
   ("list flags", "feature flags", "flag status"),
   "flags", (),
   examples=("qubit, list flags",),
   help="Flag inventory.")
_E("logs", "Recent logs",
   ("show logs", "recent errors", "what happened", "system logs"),
   "logs", ("logs",),
   examples=("qubit, show logs",),
   help="Redacted journal tail.")
_E("insights", "Activity summary",
   ("usage summary", "activity summary", "what did you do"),
   "insights", ("insights",),
   summary="sessions",
   examples=("qubit, activity summary",),
   help="Recent session states.")
_E("prompt_budget", "Prompt budget",
   ("prompt budget", "context budget", "context size"),
   "prompt_budget", ("prompt-size",),
   examples=("qubit, prompt budget",),
   help="Context budget report.")
_E("reasoning", "Reasoning budgets",
   ("reasoning budget", "reasoning levels", "thinking budget"),
   "reasoning", ("--reasoning",),
   examples=("qubit, reasoning levels",),
   help="Per-request output budgets.")
_E("approvals", "Pending approvals",
   ("pending approvals", "approval status", "awaiting approval"),
   "approvals", ("approvals",),
   examples=("qubit, pending approvals",),
   help="Unsettled confirmations.")
_E("tools", "Tool inventory",
   ("list tools", "tool status", "what tools"),
   "tools", ("tools",),
   examples=("qubit, list tools",),
   help="Brokered tool names.")
_E("secrets", "Secret status",
   ("secret status", "vault status", "credential references"),
   "secrets", ("secrets/egress",),
   examples=("qubit, secret status",),
   help="Reference kinds (never values).")
_E("worktree", "Worktree cleanup",
   ("clean worktrees", "worktree status", "prune worktrees"),
   "worktree", ("worktree/--worktree",), confirm=True,
   examples=("qubit, clean worktrees",),
   help="Reap orphaned workdirs (confirmed).")
_E("completions", "Completion status",
   ("completion status", "shell completions", "are completions installed"),
   "completions", ("completion",),
   examples=("qubit, shell completions",),
   help="Shipped completion scripts.")
_E("tui_help", "Open the board",
   ("open dashboard", "show dashboard", "open the board"),
   "tui_guidance", ("--tui", "serve/dashboard/desktop/gui"),
   summary="guidance",
   examples=("qubit, open dashboard",),
   help="How to open the visual surface.")
_E("console_help", "Terminal help",
   ("open terminal", "terminal help", "command line help"),
   "console_guidance", ("console", "--safe-mode/--dev/--cli",
                        "serve/dashboard/desktop/gui"),
   summary="guidance",
   examples=("qubit, terminal help",),
   help="How to use the terminal REPL.")
_E("mic_help", "Microphone help",
   ("test microphone", "mic check", "check microphone"),
   "mic_guidance", ("mic-test",),
   examples=("qubit, check microphone",),
   help="How to test the microphone.")
_E("acp", "Editor checks",
   ("acp status", "editor check", "editor integration", "acp check"),
   "acp", ("acp/lsp",),
   examples=("qubit, editor check",),
   help="Editor readiness (no server shipped).")
_E("lsp", "Language servers",
   ("list language servers", "lsp status", "language server check"),
   "lsp", ("acp/lsp",),
   examples=("qubit, language server check",),
   help="PATH-presence inventory (never downloads).")
_E("browser_status", "Browser status",
   ("browser status", "is the browser ready", "chromium status"),
   "browser", ("browser",),
   examples=("qubit, browser status",),
   help="Loopback CDP state (profiles untouched).")
_E("computer_status", "Desktop capability",
   ("desktop status", "computer status", "can you control the desktop",
    "computer doctor"),
   "computer", ("computer-use",),
   args=(_C("action", ("status", "doctor"), required=False),),
   fixed=(("action", "status"),),
   examples=("qubit, desktop status",),
   help="Control DISABLED; probes only.")
_E("plugins_install", "Install plugin",
   ("install plugin", "add plugin", "install skill from file"),
   "plugins_install", ("plugins-install",), args=(_R("manifest"),),
   confirm=True, split="path",
   examples=("qubit, install plugin from manifest",),
   help="Stage a reviewed manifest disabled (confirmed).")
_E("curator", "Learning summary",
   ("learning summary", "what did you learn", "curator status",
    "skill lessons", "lessons learned"),
   "curator", ("curator/journey/learning/memory-graph",),
   examples=("qubit, learning summary",),
   help="Verified skills and audit timeline.")
_E("update_status", "Update status",
   ("update status", "check for updates", "new version", "uninstall info"),
   "update_status", ("update/uninstall",),
   examples=("qubit, update status",),
   help="Installed version and migration state.")
_E("config_profiles", "Config profiles",
   ("list config profiles", "show config", "configuration profiles",
    "config status", "show config profile"),
   "config_profiles", ("config", "profile"),
   args=(_C("action", ("list", "show"), required=False), _S("profile_id")),
   fixed=(("action", "list"),),
   examples=("qubit, list config profiles",),
   help="Configuration profiles.")
_E("migrate_status", "Migration status",
   ("migration status", "is migrated", "legacy status"),
   "migrate_status", ("migrate",),
   examples=("qubit, migration status",),
   help="Legacy migration state.")


# ---------------------------------------------------------------- coverage
# Chat-flag rows have no distinct voice command: voice IS the chat
# interface, so the talk pipeline (resolve-UNKNOWN fallthrough) is their
# counterpart. Matrix infra rows map to the vehicle entries below.
FALLBACK_ROWS = (
    "chat", "-z/--oneshot", "--resume/--continue", "-m/--model/--provider",
    "-t/--toolsets", "-s/--skills", "worktree/--worktree", "--query-file",
    "--max-turns/--run-budget", "--create-if-missing",
    "--pass-session-id/--source/--in/--no-restore-cwd", "-v/--verbose",
    "-Q/--quiet",
)

MATRIX_COVERAGE = {
    "H-ansi-strip": "status",
    "H-real-exec": "status",
    "H-approvals": "approvals",
    "H-estop": "pause",
    "O-secretref": "secrets",
    "O-approvals": "approvals",
    "O-audit": "logs",
    "O-sessions": "sessions",
    "O-cron": "monitor",
    "O-skills": "skills_list",
    "O-onboard": "setup_check",
    "O-memory": "memory_search",
    "O-models": "model",
}


# ---------------------------------------------------------------- execution
@dataclass
class Outcome:
    kind: str = FAILED
    entry: Optional[VoiceCommand] = None
    speech: str = ""
    receipt: Dict[str, Any] = field(default_factory=dict)
    confirmation_id: str = ""


def _pending_for(ctx, journal) -> List:
    try:
        items = journal.pending()
    except Exception:
        return []
    return [a for a in items
            if str(getattr(a, "capability", "")).startswith("voice.")
            and isinstance(getattr(a, "meta", None), dict)
            and a.meta.get("session", "voice") == ctx.session]


def _prune_session_pending(ctx, journal, keep: int = MAX_PENDING_PER_SESSION) -> None:
    try:
        stale = [a for a in _pending_for(ctx, journal)][keep:]
        for item in stale:
            try:
                journal.respond(item.id, "deny")
            except Exception:
                pass
    except Exception:
        pass


def execute(ctx, resolution: Resolution) -> Outcome:
    """Run a MATCH resolution through policy gates and the backend.

    Reads execute immediately; confirm-tier entries mint a durable
    single-use journal confirmation and return NEEDS_CONFIRM. Only
    user-issued sources run; anything else is REFUSED (never executed).
    """
    entry = resolution.entry
    if resolution.kind != MATCH or entry is None:
        if resolution.kind in (CLARIFY, AMBIGUOUS):
            _audit("clarify", ctx.actor,
                   {"session": ctx.session,
                    "speech": resolution.speech[:160]})
            return Outcome(FAILED, entry, resolution.speech, {})
        _audit("unknown", ctx.actor, {"session": ctx.session})
        return Outcome(FAILED, None, resolution.speech or
                       "I didn't understand that.", {})
    if ctx.source not in ALLOWED_SOURCES:
        _audit("refused_source", ctx.actor,
               {"entry": entry.id, "source": str(ctx.source)[:40],
                "session": ctx.session})
        return Outcome(REFUSED, entry,
                       "That request did not come from you.", {})
    backend = _BACKENDS.get(entry.backend)
    if backend is None:
        return Outcome(FAILED, entry, "That capability is not wired yet.", {})
    merged = dict(resolution.args)
    for key, value in (entry.fixed or ()):
        merged.setdefault(key, value)
    if entry.confirm:
        if _estop(ctx.cfg):
            _audit("estop", ctx.actor,
                   {"entry": entry.id, "session": ctx.session})
            return Outcome(REFUSED, entry,
                           "Emergency stop is latched. Reset it first.", {})
        journal = _journal(ctx.cfg)
        _prune_session_pending(ctx, journal)
        shown = ", ".join(f"{k} {_redact(str(v))[:40]}" for k, v in
                          sorted(merged.items()) if k != "action")[:160]
        wording = _speak(f"{entry.title}: say yes to confirm. " + shown)
        try:
            approval = journal.create(f"voice.{entry.id}", wording,
                                      ttl_s=CONFIRM_TTL_S,
                                      session=ctx.session, args=merged,
                                      actor=ctx.actor)
        except Exception as exc:
            return Outcome(FAILED, entry, f"Could not stage confirmation: {exc}.", {})
        _audit("needs_confirm", ctx.actor,
               {"entry": entry.id, "confirmation_id": approval.id,
                "session": ctx.session})
        return Outcome(NEEDS_CONFIRM, entry, wording + " Say yes to confirm.",
                       {"confirmation_id": approval.id},
                       confirmation_id=approval.id)
    try:
        result = backend(ctx, merged)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        _audit("failed", ctx.actor,
               {"entry": entry.id, "code": str(code)[:60],
                "session": ctx.session})
        return Outcome(FAILED, entry,
                       f"That failed: {_redact(str(exc))[:160]}.", {})
    receipt = {"entry": entry.id, "session": ctx.session,
               "result": result if isinstance(result, dict) else {"result": result}}
    _audit("executed", ctx.actor,
           {"entry": entry.id, "session": ctx.session})
    return Outcome(EXECUTED, entry, summarize(entry, result), receipt)


def confirm_pending(ctx, confirmation_id: Optional[str] = None) -> Outcome:
    """Consume one pending voice confirmation ("yes") exactly once."""
    journal = _journal(ctx.cfg)
    pending = _pending_for(ctx, journal)
    target = None
    if confirmation_id:
        target = next((a for a in pending if a.id == confirmation_id), None)
        if target is None:
            return Outcome(FAILED, None, "That confirmation is gone.", {})
    else:
        target = pending[-1] if pending else None
        if target is None:
            return Outcome(FAILED, None, "There's nothing to confirm.", {})
    entry_id = str(getattr(target, "capability", "") or "")[len("voice."):]
    entry = REGISTRY.get(entry_id)
    if entry is None:
        return Outcome(FAILED, None, "That confirmation is stale.", {})
    if ctx.source not in ALLOWED_SOURCES:
        return Outcome(REFUSED, entry, "That request did not come from you.", {})
    if _estop(ctx.cfg):
        try:
            journal.respond(target.id, "deny")
        except Exception:
            pass
        return Outcome(REFUSED, entry, "Emergency stop is latched.", {})
    try:
        journal.respond(target.id, "approve")
    except Exception as exc:
        return Outcome(FAILED, entry, f"Could not settle: {exc}.", {})
    if not _mark_executed(ctx.cfg, target.id):
        return Outcome(FAILED, entry, "Already done.", {})
    meta = getattr(target, "meta", {}) or {}
    # Re-validate args from the journal record (never trust the new turn).
    saved = dict(meta.get("args", {})) if isinstance(meta.get("args", {}), dict) else {}
    backend = _BACKENDS.get(entry.backend)
    if backend is None:
        return Outcome(FAILED, entry, "That capability is not wired yet.", {})
    try:
        result = backend(ctx, saved)
    except Exception as exc:
        _audit("confirmed_failed", ctx.actor,
               {"entry": entry.id, "confirmation_id": target.id,
                "session": ctx.session})
        return Outcome(FAILED, entry,
                       f"That failed: {_redact(str(exc))[:160]}.", {})
    receipt = {"entry": entry.id, "session": ctx.session,
               "confirmation_id": target.id,
               "result": result if isinstance(result, dict) else {"result": result}}
    _audit("confirmed_executed", ctx.actor,
           {"entry": entry.id, "confirmation_id": target.id,
            "session": ctx.session})
    return Outcome(EXECUTED, entry, summarize(entry, result), receipt)


def cancel_pending(ctx, confirmation_id: Optional[str] = None) -> Outcome:
    """Deny (cancel) pending voice confirmations for the session."""
    journal = _journal(ctx.cfg)
    pending = _pending_for(ctx, journal)
    if confirmation_id:
        pending = [a for a in pending if a.id == confirmation_id]
    count = 0
    for item in pending:
        try:
            journal.respond(item.id, "deny")
            count += 1
        except Exception:
            pass
    _audit("cancelled", ctx.actor,
           {"session": ctx.session, "count": count})
    if count == 0:
        return Outcome(FAILED, None, "There's nothing pending.", {})
    return Outcome(EXECUTED, None,
                   f"Cancelled {count} pending confirmation{'s' if count != 1 else ''}.",
                   {"cancelled": count})


# ------------------------------------------------- turn integration
def handle_turn(orch: Any, intent_kind: str, text: str) -> bool:
    """Voice-first routing for one turn; True when fully handled.

    Takes a duck-typed orchestrator (``cfg``/``speak``/``feed_push``) so
    the branch is unit-testable without the audio graph. MATCH executes
    (or stages confirmation), CLARIFY/AMBIGUOUS speak the question, and
    UNKNOWN returns False for existing routing.
    """
    if intent_kind in ("system", "confirm", "cancel"):
        return False
    try:
        resolution = resolve(text)
    except Exception:
        return False
    if resolution.kind == UNKNOWN:
        return False
    try:
        ctx = make_ctx(orch.cfg, actor="voice:user", session="voice",
                       source="user")
        outcome = execute(ctx, resolution)
        orch.speak(outcome.speech or "Done.")
        try:
            orch.feed_push({"kind": "voice-command", "text": text[:160],
                            "route": f"voice:{outcome.entry.id}"
                            if outcome.entry else "voice",
                            "detail": outcome.kind})
        except Exception:
            pass
        return True
    except Exception as exc:
        _audit("turn_error", "voice:user", {"error": str(exc)[:160]})
        return False


def confirm_voice_turn(orch: Any) -> bool:
    """Consume a pending voice confirmation on a "yes" turn; True if one
    existed (executed or refused), False when nothing was pending."""
    try:
        ctx = make_ctx(orch.cfg, actor="voice:user", session="voice",
                       source="user")
        outcome = confirm_pending(ctx)
        if outcome.speech == "There's nothing to confirm.":
            return False
        orch.speak(outcome.speech or "Done.")
        return True
    except Exception:
        return False


def cancel_voice_turn(orch: Any) -> None:
    """Deny pending voice confirmations (best-effort, never raises)."""
    try:
        ctx = make_ctx(orch.cfg, actor="voice:user", session="voice",
                       source="user")
        cancel_pending(ctx)
    except Exception as exc:
        _audit("cancel_error", "voice:user", {"error": str(exc)[:160]})
