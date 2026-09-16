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
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

ALLOWED_SOURCES = ("user", "cli", "dashboard")
# Confirm-gated backends reachable while the emergency stop is latched: the
# whole point of the reset op is to clear the latch, so pre-probing the latch
# would make the e-stop unrecoverable by voice/dashboard. Every other
# mutation still fails closed while latched.
ESTOP_EXEMPT_BACKENDS = ("resume",)
GIT_LOG_DEFAULT = 10
CONFIRM_TTL_S = 120.0
MAX_PENDING_PER_SESSION = 10
EXECUTED_KEEP = 500
SPEECH_WORDS = 40

MATCH, CLARIFY, AMBIGUOUS, UNKNOWN = "match", "clarify", "ambiguous", "unknown"
EXECUTED, NEEDS_CONFIRM, FAILED, REFUSED = "executed", "needs-confirm", "failed", "refused"

_WAKE_RE = re.compile(r"\b(hey\s+qubit|ok\s+qubit|qubit)\b", re.I)
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


def _extract(
    entry: VoiceCommand, remainder: str, alias_text: str = ""
) -> Tuple[Dict[str, Any], List[VoiceArg]]:
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
    if entry.split == "task-when" and len(entry.args) >= 2:
        from .schedules import extract_when as _extract_when

        task_part, when_part = _extract_when(rest)
        task_spec, when_spec = entry.args[0], entry.args[1]
        if task_part:
            args[task_spec.name] = task_part
        elif "this task" in (alias_text or "") or "current task" in (alias_text or ""):
            # "schedule this task ..." names the current kanban task; the
            # backend resolves it (same sentinel as task_run from_current).
            args[task_spec.name] = "this task"
        elif task_spec.required:
            missing.append(task_spec)
        if when_part:
            args[when_spec.name] = when_part
        elif when_spec.required:
            missing.append(when_spec)
        return args, missing
    if entry.split == "path" and entry.args:
        quoted = re.findall(r"'([^']+)'|\"([^\"]+)\"", rest)
        if quoted:
            args[entry.args[0].name] = quoted[0][0] or quoted[0][1]
            return args, []
        cleaned = re.sub(
            r"^(?:at|the|file|image|picture|photo|path|of)\b\s*", "", rest
        ).strip()
        if cleaned:
            args[entry.args[0].name] = cleaned
            return args, []
        return args, [a for a in entry.args if a.required]
    quoted = re.findall(r"'([^']+)'|\"([^\"]+)\"", rest)
    quoted_vals = [a or b for a, b in quoted]
    rest_bare = re.sub(r"'[^']+'|\"[^\"]+\"", " ", rest)
    remainder_consumed = False
    typed_consumed = False
    for spec in entry.args:
        value: Any = None
        if spec.type == "choice":
            for choice in spec.choices:
                if re.search(r"\b" + re.escape(choice) + r"\b", rest_bare):
                    value = choice
                    typed_consumed = True
                    break
        elif spec.type == "int":
            found = re.search(r"\b(\d{1,6})\b", rest_bare)
            if found:
                value = int(found.group(1))
                typed_consumed = True
                # Consume the token so a following remainder/scalar does not
                # re-include it (e.g. "assign task 3 to alice" -> assignee
                # "alice", not "3 to alice").
                token = found.group(0)
                rest_bare = rest_bare[: found.start()] + " " + rest_bare[found.end() :]
                rest = re.sub(r"\b" + re.escape(token) + r"\b", " ", rest, count=1)
        elif spec.type == "remainder":
            candidate = rest.strip()
            if typed_consumed:
                candidate = re.sub(
                    r"^(?:to|for|with|onto|from|of)\s+", "", candidate, flags=re.I
                ).strip()
            value = candidate or None
            remainder_consumed = True
        else:
            if quoted_vals:
                value = quoted_vals.pop(0)
            elif remainder_consumed or typed_consumed:
                # A scalar after a typed (int/choice) or free-text arg is NOT
                # taken from the whole remainder: the typed arg already
                # consumed its token, and re-using the remainder would leak
                # that token into unrelated optional scalars (e.g.
                # "move task 3" must not set board='3'). Callers pass it
                # explicitly, or the speaker quotes it.
                value = None
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


def _iter_entries():
    """Every voice entry in resolution order.

    ``REGISTRY`` is the frozen CLI-parity surface audited by the wave-1..3
    example/coverage tables. ``PLUGIN_FLEET_REGISTRY`` adds the wave-4
    vendored dashboard-plugin fleet. Both flow through the same
    resolve/execute/confirm pipeline, the same journal, and the same
    redaction/audit path — there is still one listener and one authority.
    """
    yield from REGISTRY.values()
    yield from PLUGIN_FLEET_REGISTRY.values()
    yield from MODEL_PROFILE_REGISTRY.values()


def _lookup_entry(entry_id: str) -> Optional[VoiceCommand]:
    """Find an entry in either registry (execute paths never assume one)."""
    return (REGISTRY.get(entry_id) or PLUGIN_FLEET_REGISTRY.get(entry_id)
            or MODEL_PROFILE_REGISTRY.get(entry_id))


def resolve(text: str) -> Resolution:
    """Match text to at most one command (pure; no side effects)."""
    norm = _norm(text)
    if not norm:
        return Resolution(UNKNOWN, speech="I didn't catch that.")
    tokens = [t for t in norm.split(" ") if t]
    addressed = "qubit" in (text or "").lower()
    hits: List[Tuple[int, VoiceCommand, str, str]] = []
    for entry in _iter_entries():
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
    args, missing = _extract(entry, remainder, alias_text)
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

def _voice_board_principal(args: Dict[str, Any]) -> Tuple[str, str]:
    """Split a voice board/principal capture ("editor to sam" -> main/sam).

    Board slugs are single lowercase tokens; a multi-word board capture
    means the board was unspoken and the phrase is the principal. A
    leading "<role> to|for " is stripped from the principal (the role
    travels in its own choice arg). The confirmation echo still shows
    the resolved pair before anything mutates.
    """
    board = str(args.get("board", "") or "main").strip()
    principal = str(args.get("principal", "") or "").strip()
    if " " in board:
        board = "main"
    principal = re.sub(r"^(owner|editor|viewer)\s+(to|for|from)\s+", "",
                       principal, flags=re.I).strip()
    principal = re.sub(r"^(to|for|from|of)\s+", "", principal,
                       flags=re.I).strip()
    return board or "main", principal

def _b_status(ctx, args):
    from .runtime_inventory import runtime_inventory

    return {"inventory": runtime_inventory(ctx.cfg)}


def _b_pause(ctx, args):
    from . import power as PW

    # The voice confirmation (single-use journal, user source) is the human
    # authorization; the power backend only accepts an authenticated human
    # channel, so pass the canonical principal it normalizes to.
    out = PW.power_control(ctx.cfg).emergency_stop(issued_by="user")
    return {"latched": True, "detail": out.get("detail", "")}


def _b_resume(ctx, args):
    from . import power as PW

    out = PW.power_control(ctx.cfg).reset(issued_by="user")
    detail = out.get("detail", "") if isinstance(out, dict) else ""
    return {"cleared": True, "detail": detail}


def _autonomy_owner() -> str:
    # The canonical authenticated human principal the broker/autonomy store
    # binds profiles to (voicebridge.actions.HUMAN_PRINCIPAL).
    from .actions import HUMAN_PRINCIPAL
    return HUMAN_PRINCIPAL


def _b_autonomy_status(ctx, args):
    from . import autonomy_ops as AO

    return {"autonomy": AO.status(ctx.cfg)}


def _b_autonomy_list(ctx, args):
    from . import autonomy_ops as AO

    profiles = AO.list_profiles(ctx.cfg)
    return {"autonomy": {"count": len(profiles),
                         "profiles": [p.to_dict() for p in profiles]}}


def _b_autonomy_show(ctx, args):
    from . import autonomy_ops as AO

    prof = AO.show(ctx.cfg, str(args.get("profile_id", "") or ""))
    if prof is None:
        return {"autonomy": {"state": "UNAVAILABLE",
                             "code": "AUTONOMY_PROFILE_NOT_FOUND"}}
    return {"autonomy": {"state": "OK", "profile": prof.to_dict()}}


def _b_autonomy_pause(ctx, args):
    from . import autonomy_ops as AO

    prof = AO.pause(ctx.cfg, str(args.get("profile_id", "") or ""),
                    owner=_autonomy_owner())
    return {"autonomy": {"state": "OK", "profile": prof.to_dict()}}


def _b_autonomy_resume(ctx, args):
    from . import autonomy_ops as AO

    prof = AO.resume(ctx.cfg, str(args.get("profile_id", "") or ""),
                     owner=_autonomy_owner())
    return {"autonomy": {"state": "OK", "profile": prof.to_dict()}}


def _b_autonomy_revoke(ctx, args):
    from . import autonomy_ops as AO

    prof = AO.revoke(ctx.cfg, str(args.get("profile_id", "") or ""),
                     owner=_autonomy_owner())
    return {"autonomy": {"state": "OK", "profile": prof.to_dict()}}


def _b_autonomy_expire(ctx, args):
    from . import autonomy_ops as AO

    expired = AO.expire(ctx.cfg)
    return {"autonomy": {"state": "OK", "expired": list(expired),
                         "count": len(expired)}}


def _b_doctor(ctx, args):
    from . import lifecycle as L

    return {"diagnose": L.checklist(ctx.cfg)}


def _b_setup_check(ctx, args):
    from . import lifecycle as L

    return {"checklist": L.checklist(ctx.cfg)}


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


def _b_voice_unsupported(ctx, args):
    """Typed refusal for a spoken attempt at a CLI/dashboard-only op.

    This is real execution of the Qubit safety policy (audited, redacted),
    not help text: the request never binds the secret/nested/preview-hash
    data that makes the op unsafe to speak, and it names the exact safe
    counterpart. Every op excluded from voice has one of these guards.
    """
    guidance = str(args.get("guidance", "") or "")
    detail = str(args.get("detail", "") or "")
    return {"state": "refused",
            "code": str(args.get("code", "VOICE_UNSUPPORTED") or
                        "VOICE_UNSUPPORTED"),
            "op": str(args.get("op", "") or ""),
            "guidance": guidance,
            "detail": detail or
                      ("I can't do that safely by voice. " + guidance).strip()}


def _b_backup(ctx, args):
    if args.get("action") == "export_refused":
        return {"state": "refused", "code": "PASSPHRASE_NEVER_SPOKEN",
                "guidance": VOICE_IMPOSSIBLE["export-state.voice"],
                "detail": "I cannot take a passphrase by voice: spoken words"
                          " land in transcripts, the journal, and audio"
                          " history. Use the dashboard password form or run"
                          " `qubit export-state` in a terminal."}
    if args.get("action") == "import_refused":
        return {"state": "refused", "code": "PASSPHRASE_NEVER_SPOKEN",
                "guidance": VOICE_IMPOSSIBLE["import-state.voice"],
                "detail": "I cannot take a passphrase by voice: spoken words"
                          " land in transcripts, the journal, and audio"
                          " history. Use the dashboard password form or run"
                          " `qubit import-state <bundle>` in a terminal."}
    return {"guidance": "encrypted backups live in `qubit export-state`: "
                        "run it with a passphrase, carry the bundle yourself"}


def _b_sessions(ctx, args):
    from .auth_status import recent_sessions, session_store

    action = str(args.get("action", "list") or "list")
    sid = str(args.get("session_id", "") or "")
    if action in ("search", "show", "rename", "archive", "delete", "resume"):
        store = session_store(ctx.cfg)
        if action == "search":
            return {"sessions": store.search(str(args.get("query", "")), 20)}
        if action == "show":
            return {"session": store.show(sid)}
        if action == "rename":
            return {"session": store.rename(sid, str(args.get("name", "")))}
        if action == "archive":
            return {"session": store.archive(sid)}
        if action == "delete":
            return {"session": store.delete(sid, confirm=True)}
        return {"session": store.resume(sid)}
    # Match the CLI parser default (`sessions list --limit`, default 20);
    # `recent_sessions` applies the canonical [1, 100] bound itself.
    limit = args.get("limit", 20)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    return {"sessions": recent_sessions(ctx.cfg, limit)}


def _b_capability(ctx, args):
    from .capability_catalog import check, list_names, summary

    if str(args.get("action", "check")) == "list":
        available = list_names("IMPLEMENTED")
        return {"capabilities": {"state": "OK", "total": len(list_names()),
                                 "implemented": len(available),
                                 "available": available,
                                 "states": summary().get("states", {})}}
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


def _b_worker(ctx, args):
    from .integration_status import worker_status

    return {"worker": worker_status(ctx.cfg)}


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
    if action == "remove":
        rec = store.remove_skill(str(args.get("skill_id", "")),
                                 requested_by="voice:user", confirmed=True)
        return {"status": "removed", "skill_id": rec.skill_id}
    if action == "rollback":
        rec = store.rollback_skill(str(args.get("skill_id", "")),
                                   requested_by="voice:user", confirmed=True)
        return {"status": rec.status, "skill_id": rec.skill_id}
    raise ValueError(f"unknown skills action {action!r}")


def _b_projects(ctx, args):
    from . import projects as PJ

    action = args.get("action", "list")
    if action == "list":
        return PJ.list_projects(ctx.cfg)
    if action == "active":
        return PJ.active_project(ctx.cfg)
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
    if action == "rename":
        return PJ.rename(ctx.cfg, str(args.get("slug", "")),
                         str(args.get("name", "")),
                         requested_by="voice:user", confirmed=True)
    if action == "add_folder":
        return PJ.add_folder(ctx.cfg, str(args.get("slug", "")),
                             str(args.get("folder", "")),
                             requested_by="voice:user", confirmed=True)
    if action == "remove_folder":
        return PJ.remove_folder(ctx.cfg, str(args.get("slug", "")),
                                str(args.get("folder", "")),
                                requested_by="voice:user", confirmed=True)
    if action == "set_primary":
        return PJ.set_primary(ctx.cfg, str(args.get("slug", "")),
                              str(args.get("folder", "")),
                              requested_by="voice:user", confirmed=True)
    if action == "restore":
        return PJ.restore(ctx.cfg, str(args.get("slug", "")),
                          requested_by="voice:user", confirmed=True)
    if action == "rollback":
        return PJ.rollback(ctx.cfg, requested_by="voice:user", confirmed=True)
    if action == "bind_board":
        return PJ.bind_board(ctx.cfg, str(args.get("slug", "")),
                             str(args.get("board", "")),
                             requested_by="voice:user", confirmed=True)
    raise ValueError(f"unknown projects action {action!r}")


def _int_arg(args: Dict[str, Any], key: str, default: int = 0) -> int:
    """Coerce an int arg without leaking OverflowError/ValueError to callers.

    A malformed or absurd value (e.g. task_id=2**63) resolves to ``default``
    so the backend returns its own typed NOT_FOUND/BAD_ARGS instead of a raw
    exception class.
    """
    try:
        return int(args.get(key, default) or default)
    except (TypeError, ValueError, OverflowError):
        return default


def _b_kanban(ctx, args):
    from . import kanban as KB

    action = args.get("action", "list")
    if action == "boards":
        return KB.boards_list(ctx.cfg)
    if action == "stores":
        from . import kanban_bridge as KBG
        return KBG.stores(ctx.cfg)
    if action == "migrate":
        from . import kanban_bridge as KBG
        raw = str(args.get("dry_run", "") or "").strip().lower()
        return KBG.migrate(ctx.cfg, dry_run=raw in ("1", "true", "yes", "on"))
    if action == "current":
        return KB.current_task(ctx.cfg)
    if action == "list":
        return KB.list_tasks(
            ctx.cfg,
            board=str(args.get("board", "")),
            status=str(args.get("status", "")),
            limit=int(args.get("limit", KB.LIST_DEFAULT) or KB.LIST_DEFAULT),
        )
    if action == "show":
        return KB.show(ctx.cfg, _int_arg(args, "task_id"))
    if action == "create":
        return KB.create(
            ctx.cfg,
            str(args.get("title", "")),
            board=str(args.get("board", "") or "main"),
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "claim":
        return KB.claim(
            ctx.cfg,
            _int_arg(args, "task_id"),
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "complete":
        return KB.complete(
            ctx.cfg,
            _int_arg(args, "task_id"),
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "comment":
        return KB.comment(
            ctx.cfg,
            _int_arg(args, "task_id"),
            str(args.get("body", "")),
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "init":
        return KB.init(ctx.cfg)
    if action == "boards_create":
        return KB.boards_create(
            ctx.cfg,
            str(args.get("board", "main")),
            str(args.get("name", "")),
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "assign":
        return KB.assign(
            ctx.cfg,
            _int_arg(args, "task_id"),
            str(args.get("assignee", "")),
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "reassign":
        return KB.reassign(
            ctx.cfg,
            _int_arg(args, "task_id"),
            str(args.get("assignee", "")),
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "reclaim":
        return KB.reclaim(
            ctx.cfg,
            _int_arg(args, "task_id"),
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "edit":
        return KB.edit(
            ctx.cfg,
            _int_arg(args, "task_id"),
            title=str(args.get("title", "")),
            body=str(args.get("body", "")),
            priority=str(args.get("priority", "")),
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "block":
        return KB.block(
            ctx.cfg,
            _int_arg(args, "task_id"),
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "unblock":
        return KB.unblock(
            ctx.cfg,
            _int_arg(args, "task_id"),
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "archive":
        return KB.archive_task(
            ctx.cfg,
            _int_arg(args, "task_id"),
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "restore":
        return KB.restore_task(
            ctx.cfg,
            _int_arg(args, "task_id"),
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "stats":
        return KB.stats(ctx.cfg, board=str(args.get("board", "")))
    if action == "log":
        return KB.log(
            ctx.cfg,
            board=str(args.get("board", "")),
            limit=int(args.get("limit", KB.LIST_DEFAULT) or KB.LIST_DEFAULT),
        )
    if action == "export":
        return KB.export(ctx.cfg)
    if action == "open":
        return KB.board_open(ctx.cfg, board=str(args.get("board", "") or "main"))
    if action == "move":
        return KB.move_task(
            ctx.cfg,
            _int_arg(args, "task_id"),
            board=str(args.get("board", "") or ""),
            status=str(args.get("status", "") or ""),
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "widget_add":
        return KB.widget_add(
            ctx.cfg,
            str(args.get("board", "") or "main"),
            str(args.get("kind", "") or "note"),
            str(args.get("title", "")),
            content=str(args.get("content", "") or ""),
            tab=str(args.get("tab", "") or ""),
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "tab_list":
        return {"tabs": KB.tab_list(ctx.cfg, str(args.get("board", "") or "main"))}
    if action == "tab_create":
        return {
            "tab": KB.tab_create(
                ctx.cfg,
                str(args.get("board", "") or "main"),
                str(args.get("slug", "")),
                str(args.get("name", "")),
                requested_by="voice:user",
                confirmed=True,
            )
        }
    if action == "tab_rename":
        return {
            "tab": KB.tab_rename(
                ctx.cfg,
                str(args.get("board", "") or "main"),
                str(args.get("tab", "")),
                str(args.get("name", "")),
                requested_by="voice:user",
                confirmed=True,
            )
        }
    if action == "tab_delete":
        return {
            "tab": KB.tab_delete(
                ctx.cfg,
                str(args.get("board", "") or "main"),
                str(args.get("tab", "")),
                requested_by="voice:user",
                confirmed=True,
            )
        }
    if action == "tab_move":
        return {
            "tab": KB.tab_move(
                ctx.cfg,
                str(args.get("board", "") or "main"),
                str(args.get("tab", "")),
                int(args.get("position", 0) or 0),
                requested_by="voice:user",
                confirmed=True,
            )
        }
    if action == "widget_list":
        return KB.widget_list(ctx.cfg, board=str(args.get("board", "") or "main"))
    if action == "widget_remove":
        return KB.widget_remove(
            ctx.cfg,
            int(args.get("widget_id", 0)),
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "layout_show":
        return KB.layout_show(ctx.cfg, board=str(args.get("board", "") or "main"))
    if action == "layout_set":
        cols = [
            c.strip().lower()
            for c in str(args.get("columns", "") or "").replace(" ", ",").split(",")
            if c.strip()
        ]
        return KB.layout_set(
            ctx.cfg,
            str(args.get("board", "") or "main"),
            cols,
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "role_grant":
        board, principal = _voice_board_principal(args)
        return KB.role_grant(
            ctx.cfg,
            board,
            principal,
            str(args.get("role", "") or "viewer"),
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "role_revoke":
        board, principal = _voice_board_principal(args)
        return KB.role_revoke(
            ctx.cfg, board, principal, requested_by="voice:user", confirmed=True
        )
    if action == "widget_move":
        wid = int(args.get("widget_id", 0))
        nums = [
            int(n)
            for n in re.findall(r"\b(\d{1,4})\b", str(args.get("geometry", "") or ""))
        ]
        rest = nums[1:] if nums and nums[0] == wid else nums
        box = dict(zip(("x", "y", "w", "h"), rest[:4]))
        return KB.widget_move(
            ctx.cfg, wid, requested_by="voice:user", confirmed=True, **box
        )
    if action == "snapshots":
        # Newest-first canvas restore points, the same KB.snapshot_list the CLI
        # `qubit kanban snapshots` read calls (read-only, bounded).
        return {
            "snapshots": KB.snapshot_list(
                ctx.cfg,
                board=str(args.get("board", "") or "main"),
                limit=_int_arg(args, "limit", KB.LIST_DEFAULT),
            )
        }
    if action == "snapshot_restore":
        return KB.snapshot_restore(
            ctx.cfg,
            int(args.get("snapshot_id", 0)),
            requested_by="voice:user",
            confirmed=True,
        )
    if action == "delegated":
        sub = str(args.get("subcommand", "") or "watch")
        if sub not in KB.DELEGATED:
            sub = "watch"
        return KB.refuse_delegated(sub)
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
    if action == "tweak_set":
        return TH.set_tweak(ctx.cfg, str(args.get("tweak", "")),
                            str(args.get("color", "")),
                            requested_by="voice:user", confirmed=True)
    if action == "tweak_clear":
        return TH.clear_tweak(ctx.cfg, str(args.get("tweak", "")),
                              requested_by="voice:user", confirmed=True)
    if action == "reset":
        return TH.reset(ctx.cfg, requested_by="voice:user", confirmed=True)
    if action == "pets":
        return TH.refuse_pet_downloads()
    raise ValueError(f"unknown themes action {action!r}")


def _b_proxy(ctx, args):
    from . import proxy_status as PX

    action = args.get("action", "status")
    if action == "providers":
        return {"providers": PX.providers(ctx.cfg)}
    if action == "start":
        return {"refused": PX.refuse_start()}
    return {"proxy": PX.status()}


def _b_import_agent(ctx, args):
    from . import import_agent as IA

    if str(args.get("action", "inspect")) == "import":
        return {"refused": IA.refuse_import()}
    return {"inspect": IA.inspect_tree(str(args.get("source", "")),
                                       str(args.get("kind", "")))}


def _b_hooks(ctx, args):
    from . import hooks as HK

    action = args.get("action", "list")
    if action == "list":
        return HK.list_hooks(ctx.cfg)
    if action == "show":
        return HK.show(ctx.cfg, str(args.get("name", "")))
    if action == "declare":
        return HK.declare(ctx.cfg, str(args.get("name", "")),
                          str(args.get("event", "")),
                          str(args.get("shell", "")),
                          requested_by="voice:user", confirmed=True)
    if action == "validate":
        return HK.validate(ctx.cfg, str(args.get("name", "")))
    if action == "test":
        return HK.test_plan(ctx.cfg, str(args.get("name", "")))
    if action == "doctor":
        return HK.doctor(ctx.cfg)
    if action == "revoke":
        return HK.revoke(ctx.cfg, str(args.get("name", "")),
                         requested_by="voice:user", confirmed=True)
    if action == "remove":
        return HK.remove(ctx.cfg, str(args.get("name", "")),
                         requested_by="voice:user", confirmed=True)
    if action == "fire":
        return HK.fire(ctx.cfg, str(args.get("event", "")),
                       requested_by="voice:user", confirmed=True)
    if action == "emit":
        return HK.emit(ctx.cfg, str(args.get("event", "")),
                       requested_by="voice:user")
    raise ValueError(f"unknown hooks action {action!r}")


def _b_sync(ctx, args):
    from . import sync as SY

    action = args.get("action", "list")
    if action == "list":
        return SY.list_offers(ctx.cfg)
    if action == "show":
        return SY.show(ctx.cfg, str(args.get("offer_id", "")))
    if action == "revoke":
        return SY.revoke(ctx.cfg, str(args.get("offer_id", "")),
                         requested_by="voice:user", confirmed=True)
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
    if action == "setup_code":
        scopes = [s.strip() for s in
                  str(args.get("scopes", "messaging")
                      or "messaging").split(",") if s.strip()]
        return PA.encode_setup(
            ctx.cfg, str(args.get("label", "voice") or "voice"), scopes,
            requested_by="voice:user", confirmed=True)
    if action == "accept":
        return PA.accept(ctx.cfg, str(args.get("setup", "") or ""),
                         requested_by="voice:user", confirmed=True)
    raise ValueError(f"unknown pairing action {action!r}")


def _b_webhook(ctx, args):
    from . import webhooks as WH

    action = args.get("action", "list")
    if action == "list":
        return WH.list_subs(ctx.cfg)
    if action == "show":
        return WH.show(ctx.cfg, str(args.get("sub_id", "")))
    if action == "add":
        return WH.add(ctx.cfg, str(args.get("sub_id", "")),
                      str(args.get("url", "")),
                      [e.strip() for e in str(args.get("events", "")).split(",")
                       if e.strip()],
                      str(args.get("secret_ref", "")),
                      requested_by="voice:user", confirmed=True)
    if action == "remove":
        return WH.remove(ctx.cfg, str(args.get("sub_id", "")),
                         requested_by="voice:user", confirmed=True)
    if action == "test":
        return WH.test_plan(ctx.cfg, str(args.get("sub_id", "")))
    raise ValueError(f"unknown webhook action {action!r}")


def _b_msg(ctx, args):
    from . import messaging as MG

    action = args.get("action", "list")
    if action == "accounts":
        return MG.account_list(ctx.cfg)
    if action == "account_add":
        return MG.account_add(ctx.cfg, str(args.get("account_id", "")),
                              str(args.get("provider", "")),
                              str(args.get("label", "")),
                              key_ref=str(args.get("key_ref", "") or ""),
                              requested_by="voice:user", confirmed=True)
    if action == "account_show":
        return MG.account_show(ctx.cfg, str(args.get("account_id", "")))
    if action == "account_remove":
        return MG.account_remove(ctx.cfg, str(args.get("account_id", "")),
                                 requested_by="voice:user", confirmed=True)
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
    if action == "retry":
        mid = args.get("msg_id")
        if mid is None:
            cand = [m for m in MG.list_msgs(ctx.cfg).get("messages", [])
                    if m.get("state") in ("failed", "queued")]
            if not cand:
                raise ValueError("no retryable message")
            mid = cand[0]["id"]
        return MG.retry(ctx.cfg, str(mid),
                        requested_by="voice:user", confirmed=True)
    if action == "draft_reply":
        mid = args.get("msg_id") or ""
        if not mid:
            inbox = MG.inbox_list(ctx.cfg).get("messages", [])
            if not inbox:
                raise ValueError("inbox is empty")
            mid = inbox[0]["id"]
        return MG.draft_reply(ctx.cfg, str(mid),
                              str(args.get("account", "")),
                              str(args.get("body", "")),
                              requested_by="voice:user", confirmed=True)
    raise ValueError(f"unknown msg action {action!r}")


def _b_gateway(ctx, args):
    from . import gateway_peers as GW

    action = args.get("action", "list")
    if action == "list":
        return GW.gateway_list(ctx.cfg)
    if action == "show":
        return GW.gateway_show(ctx.cfg, str(args.get("gateway_id", "")))
    if action == "add":
        return GW.gateway_add(ctx.cfg, str(args.get("gateway_id", "")),
                              str(args.get("provider", "")),
                              str(args.get("account", "")),
                              str(args.get("channel", "")),
                              key_ref=str(args.get("key_ref", "") or ""),
                              requested_by="voice:user", confirmed=True)
    if action == "remove":
        return GW.gateway_remove(ctx.cfg, str(args.get("gateway_id", "")),
                                 requested_by="voice:user", confirmed=True)
    if action == "link":
        return GW.gateway_link(ctx.cfg, str(args.get("gateway_id", "")))
    if action == "supervision":
        return GW.refuse_supervision(str(args.get("target", "") or "run"))
    if action == "supervision_request":
        return GW.supervision_request(
            ctx.cfg, str(args.get("gateway_id", "") or ""),
            str(args.get("target_action", "run") or "run"),
            requested_by="voice:user", confirmed=True)
    if action == "supervision_revert":
        return GW.supervision_revert(
            ctx.cfg, str(args.get("gateway_id", "") or ""),
            requested_by="voice:user", confirmed=True)
    if action == "discovery":
        return {"discovery": GW.discovery(ctx.cfg)}
    raise ValueError(f"unknown gateway action {action!r}")


def _b_peer(ctx, args):
    from . import gateway_peers as GW

    action = args.get("action", "list")
    if action == "list":
        return GW.peer_list(ctx.cfg)
    if action == "show":
        return GW.peer_show(ctx.cfg, str(args.get("peer_id", "")))
    if action == "add":
        return GW.peer_add(ctx.cfg, str(args.get("peer_id", "")),
                           str(args.get("url", "")),
                           str(args.get("account", "")),
                           requested_by="voice:user", confirmed=True)
    if action == "remove":
        return GW.peer_remove(ctx.cfg, str(args.get("peer_id", "")),
                              requested_by="voice:user", confirmed=True)
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
    if action == "principal_add":
        return BR.principal_add(ctx.cfg, str(args.get("pid", "")),
                                str(args.get("kind", "person")),
                                str(args.get("label", "")),
                                requested_by="voice:user", confirmed=True)
    if action == "member_add":
        return BR.member_add(ctx.cfg, str(args.get("org", "")),
                             str(args.get("employee", "")),
                             requested_by="voice:user", confirmed=True)
    if action == "member_remove":
        return BR.member_remove(ctx.cfg, str(args.get("org", "")),
                                str(args.get("employee", "")),
                                requested_by="voice:user", confirmed=True)
    if action == "profile":
        return BR.profile_show(ctx.cfg, str(args.get("principal", "")),
                               identity=str(args.get("identity", "default")))
    if action == "profile_set":
        return BR.profile_set(ctx.cfg, str(args.get("principal", "")),
                              str(args.get("key", "")),
                              str(args.get("value", "")),
                              identity=str(args.get("identity", "default")),
                              requested_by="voice:user", confirmed=True)
    if action == "profile_forget":
        return BR.profile_forget(ctx.cfg, str(args.get("principal", "")),
                                 identity=str(args.get("identity", "default")),
                                 key=str(args.get("key", "")),
                                 requested_by="voice:user", confirmed=True)
    if action == "profile_sweep":
        return BR.profile_sweep(ctx.cfg, requested_by="voice:user",
                                confirmed=True)
    if action == "profile_export":
        return BR.profile_export(ctx.cfg, str(args.get("principal", "")),
                                 identity=str(args.get("identity", "default")))
    if action == "accounts":
        return BR.account_list(
            ctx.cfg, principal=str(args.get("principal", "")))
    if action == "account_add":
        return BR.account_add(ctx.cfg, str(args.get("alias", "")),
                              str(args.get("origin", "")),
                              str(args.get("principal", "")),
                              identity=str(args.get("identity", "default")),
                              requested_by="voice:user", confirmed=True)
    if action == "account_remove":
        return BR.account_remove(ctx.cfg, str(args.get("alias", "")),
                                 requested_by="voice:user", confirmed=True)
    if action == "vault_list":
        return BR.vault_list(ctx.cfg,
                             principal=str(args.get("principal", "")))
    if action == "vault_set":
        return BR.vault_set(ctx.cfg, str(args.get("name", "")),
                            str(args.get("ref", "")),
                            principal=str(args.get("principal", "")),
                            requested_by="voice:user", confirmed=True)
    if action == "vault_remove":
        return BR.vault_remove(ctx.cfg, str(args.get("name", "")),
                               requested_by="voice:user", confirmed=True)
    if action == "onboard_plan":
        return BR.onboard_plan()
    raise ValueError(f"unknown brain action {action!r}")


def _b_permissions(ctx, args):
    from .permissions import permissions_store

    store = permissions_store(ctx.cfg)
    mode, scopes, expires = store.mode()
    return {"mode": mode, "scopes": sorted(scopes), "expires_at": expires}


def _b_monitor(ctx, args):
    # Guidance backend (no communications import: Packet 8A boundary
    # reserves that package to cli.py/webui.py/scripts). The bounded pass
    # itself runs via `qubit monitor run` under its own confirmation;
    # voice stages the same confirmation then hands off.
    return {"monitor": {"state": "HANDOFF",
                        "guidance": "run `qubit monitor run` for the "
                                    "bounded hourly pass (its own --confirm); "
                                    "this voice confirmation authorized the handoff"}}


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

    root = Path(__file__).resolve().parents[1] / "voicebridge" / "_data" / \
        "completions"
    try:
        shells = sorted(p.suffix.lstrip(".") for p in root.iterdir()
                        if p.name.startswith("voicebridge."))
    except OSError:
        shells = []
    return {"completions": {"shells": shells, "progs": ["qubit", "voicebridge", "qubit-dev"] if shells else []}}


def _b_files(ctx, args):
    """Packet 8U scoped project-folder inspection (same files module the
    CLI uses). Read-only: every path must resolve inside a
    project-registered folder, and voice defaults to the active
    project's folder ("this project folder")."""
    from . import files as FI

    action = args.get("action", "inspect")
    path = str(args.get("path", "") or "")
    if action == "rollback":
        from . import file_patch as FP
        return {"file_patch": FP.rollback(
            ctx.cfg, str(args.get("target", "") or ""),
            requested_by="voice:user", confirmed=True)}
    if action == "patch":
        return {"file_patch": {
            "state": "refused", "code": "VOICE_NESTED_UNSUPPORTED",
            "detail": "structured diffs are refused by voice; use "
                      "`qubit files patch --patch-file` or the dashboard"}}
    if action == "list":
        if not path:
            path = _files_default_folder(ctx.cfg)
        return {"folder": FI.list_dir(ctx.cfg, path)}
    if action == "read":
        if not path:
            raise ValueError("which file? say the file name")
        return {"file": FI.read_file(ctx.cfg, path)}
    return {"inspected": FI.inspect(ctx.cfg, path)}


def _b_git(ctx, args):
    """Scoped read-only git inspection (same git_scope module the CLI
    and dashboard use). Status/log/diff/show execute directly (reads);
    anything else is a typed refusal — there are no write verbs."""
    from . import git_scope as GI

    action = str(args.get("action", "status"))
    root = str(args.get("root", "") or "")
    try:
        if action == "status":
            return {"git": GI.status(ctx.cfg, root, requested_by="voice:user")}
        if action == "log":
            return {
                "git": GI.log(
                    ctx.cfg,
                    root,
                    args.get("limit", GIT_LOG_DEFAULT),
                    requested_by="voice:user",
                )
            }
        if action == "diff":
            return {
                "git": GI.diff(
                    ctx.cfg,
                    root,
                    str(args.get("path", "") or ""),
                    requested_by="voice:user",
                )
            }
        if action == "show":
            return {
                "git": GI.show(
                    ctx.cfg,
                    root,
                    "HEAD",
                    str(args.get("path", "") or ""),
                    requested_by="voice:user",
                )
            }
        return {"refused": GI.refuse_write(action)}
    except GI.GitError as exc:
        return GI.public_error(exc)


def _files_default_folder(cfg: Any) -> str:
    from . import projects as PJ

    active = PJ.active_project(cfg)
    if active.get("state") != "OK" or not active.get("project"):
        raise ValueError("no active project; set one first")
    folders = active["project"].get("folders", []) or []
    if not folders:
        raise ValueError("the active project has no folders")
    return str(folders[0])


def _b_ide(ctx, args):
    """Packet 8U IDE launcher (same ide module the CLI uses).

    Status reads execute directly; opening runs only after the voice
    pipeline's confirmation and passes confirmed=True with the canonical
    human principal. Opening is the whole capability — no editor control
    is claimed."""
    from . import ide as IDE

    if str(args.get("action", "status")) == "open":
        return {"ide": IDE.open_editor(
            ctx.cfg, "", "", requested_by="voice:user", confirmed=True)}
    return {"ide": IDE.status(ctx.cfg)}


def _b_schedules(ctx, args):
    """Packet 8U scheduled jobs (same schedules module the CLI uses).

    Reads execute directly. Creation, pause/resume toggles, deletion,
    and run-due execute only after the voice pipeline's confirmation
    and pass confirmed=True with the canonical human principal.
    "This task" resolves to the current kanban task, exactly like the
    task_run from_current path."""
    from . import schedules as SC

    action = args.get("action", "list")
    if action == "list":
        return {"schedules": SC.list_jobs(ctx.cfg)}
    if action == "show":
        return {"schedule": SC.show(ctx.cfg, int(args.get("job_id", 0)))}
    if action == "history":
        return {"scheduled_history": SC.history(
            ctx.cfg, job_id=int(args.get("job_id", 0) or 0))}
    if action == "run_due":
        return {"scheduled_run": SC.run_due(ctx.cfg,
                                            requested_by="voice:user")}
    task_text = str(args.get("task_text", "") or "").strip()
    if action == "create" and task_text.lower() in (
            "this task", "the current task", "current task", "my task"):
        from . import kanban as _KB
        current = _KB.current_task(ctx.cfg)
        if current.get("state") != "OK" or not current.get("task"):
            raise ValueError("no open tasks: create a task first, "
                             "then schedule it")
        task = current["task"]
        task_text = (str(task.get("title", "")) + "\n" +
                     str(task.get("body", ""))).strip()
    if action == "create":
        name = str(args.get("name", "") or "").strip() or task_text[:60]
        return {"schedule": SC.create(
            ctx.cfg, name, task_text, str(args.get("when", "")),
            requested_by="voice:user", confirmed=True)}
    job_id = int(args.get("job_id", 0) or 0)
    if action == "pause":
        return {"schedule": SC.pause(
            ctx.cfg, job_id, requested_by="voice:user", confirmed=True)}
    if action == "resume":
        return {"schedule": SC.resume(
            ctx.cfg, job_id, requested_by="voice:user", confirmed=True)}
    if action == "delete":
        return {"schedule": SC.delete(
            ctx.cfg, job_id, requested_by="voice:user", confirmed=True)}
    raise ValueError(f"unknown schedules action {action!r}")


def _b_automation(ctx, args):
    """OpenClaw automation: standing orders, flows, heartbeat, QA, yields.

    Writes call the REAL production ``automation.*`` function immediately,
    through the canonical ``automation: <verb> key=value`` directive that
    ``automation.run_directive`` shlex-parses (so quoted values containing
    spaces/JSON survive), and return its structured result. The voice/journal
    confirmation already authorized the write, so the automation store is
    persisted before the receipt is returned -- no schedule round-trip. The
    one ``schedules`` authority still fires any ``automation:`` job the user
    authored through the CLI/dashboard, and reads answer from that same
    automation store read-only.
    """
    from . import automation as A

    action = str(args.get("action", "") or "")
    if action == "status":
        return {"automation": A.status(ctx.cfg)}
    if action == "orders":
        return {"automation": A.order_list(ctx.cfg)}
    if action == "heartbeats":
        return {"automation": A.heartbeat_list(ctx.cfg)}
    if action == "flows":
        return {"automation": A.flow_list(ctx.cfg)}
    if action == "scenarios":
        return {"automation": A.qa_scenario_list(ctx.cfg)}
    if action == "yield_pending":
        return {"automation": A.yield_pending(ctx.cfg)}
    verb = ""
    if action == "directive":
        verb = str(args.get("verb", "") or "").strip()
    elif action in ("yield_handoff", "yield_resume"):
        verb = {"yield_handoff": "yield.handoff",
                "yield_resume": "yield.resume"}[action]
    if verb:
        spec = str(args.get("spec", "") or "").strip()
        directive = f"{A.DIRECTIVE_PREFIX} {verb} {spec}".strip()
        # Immediate persisted write: run_directive reuses the production spec
        # parser (shlex) and the real automation function, and never raises
        # into the confirmation pipeline (typed FAILED envelopes instead).
        out = A.run_directive(ctx.cfg, directive, requested_by="voice:user")
        return {"automation": out, "directive": verb, "task_text": directive}
    raise ValueError(f"unknown automation action {action!r}")


def _b_tui_guidance(ctx, args):
    return {"guidance": "the live board needs a terminal: run "
                        "`qubit tui` locally, or open the web "
                        "dashboard for the visual surface"}


def _b_console_guidance(ctx, args):
    return {"guidance": "the terminal REPL runs on your machine: launch "
                        "`qubit-dev` and type /help for slash commands"}


def _b_mic_guidance(ctx, args):
    return {"guidance": "microphone checks run from the CLI: "
                        "`qubit mic-test` records five seconds "
                        "and reports levels"}


def _b_acp(ctx, args):
    from . import acp_lsp as AL

    action = args.get("action", "check")
    if action == "setup":
        return {"setup": AL.acp_setup_snippet(
            str(args.get("editor", "vscode"))
            if str(args.get("editor", "vscode")) in ("vscode", "zed", "jetbrains")
            else "vscode")}
    if action == "serve":
        return {"refused": AL.acp_refuse_serve()}
    if action == "accept_hooks":
        return {"refused": AL.acp_refuse_accept_hooks()}
    return {"check": AL.acp_check()}


def _b_lsp(ctx, args):
    from . import acp_lsp as AL

    action = args.get("action", "status")
    if action == "list":
        return AL.lsp_list()
    if action == "which":
        return AL.lsp_which(str(args.get("server", "")))
    if action == "restart":
        return AL.lsp_restart()
    if action == "install":
        return {"refused": AL.lsp_refuse_install(str(args.get("server", "")))}
    return AL.lsp_status()


def _b_browser(ctx, args):
    # Direct loopback probe (no communications import: Packet 8A boundary
    # reserves that package to cli.py/webui.py/scripts). Same endpoint the
    # 8B path checks; UNAVAILABLE covers both down and restart-required
    # (the CLI `browser status` distinguishes them).
    import urllib.request as _u

    if str(args.get("action", "status")) == "close":
        from . import browser as BW

        return {"refused": BW.refuse_close_profile(
            str(args.get("profile", "")))}
    try:
        with _u.urlopen("http://127.0.0.1:9222/json/version",
                        timeout=2) as resp:
            ok = resp.status == 200
    except Exception:
        ok = False
    if ok:
        return {"browser": {"state": "OK", "cdp": True,
                            "authority": "loopback CDP read-only (Packet 8B); "
                                         "personal profiles are never touched"}}
    return {"browser": {"state": "UNAVAILABLE", "cdp": False,
                         "detail": "no loopback CDP endpoint",
                         "authority": "loopback CDP read-only (Packet 8B); "
                                      "personal profiles are never touched"}}


def _b_tabs(ctx, args):
    """Packet 8U browser tab operations (same browser_tabs module the CLI
    uses). Reads execute directly; anything that changes the live browser
    runs only after the voice pipeline's confirmation (entries are
    confirm-gated) and passes confirmed=True with the canonical human
    principal. Page text is untrusted evidence and stays marked as such."""
    from . import browser_tabs as BT

    action = args.get("action", "tabs")
    target = str(args.get("target", "") or "")
    if action == "tabs":
        return {"tabs": BT.tabs(ctx.cfg)}
    if action == "doctor":
        return {"doctor": BT.doctor(ctx.cfg)}
    if action == "read":
        # Voice reads the Qubit-current tab (switch first with the
        # activate entry); the CLI keeps an explicit --target.
        return {"tab": BT.read(ctx.cfg, "")}
    if action == "screenshot":
        shot = BT.screenshot(ctx.cfg, "")
        if isinstance(shot, dict) and "image_base64" in shot:
            # Never place image bytes in a spoken/receipt path: the voice
            # surface returns metadata only; the dashboard/CLI fetches pixels.
            shot = {k: v for k, v in shot.items() if k != "image_base64"}
            shot["image_omitted"] = ("voice receipt carries metadata only; "
                                     "use the dashboard/CLI for the image")
        return {"screenshot": shot}
    if action == "find":
        return {"found": BT.find(ctx.cfg, str(args.get("query", "")),
                                 "")}
    if action == "open_browser":
        return {"browser": BT.open_browser(
            ctx.cfg, requested_by="voice:user", confirmed=True)}
    if action == "activate":
        return {"browser": BT.activate(
            ctx.cfg, target, requested_by="voice:user", confirmed=True)}
    if action == "open":
        return {"browser": BT.open(
            ctx.cfg, str(args.get("url", "")), requested_by="voice:user",
            confirmed=True)}
    if action == "navigate":
        return {"browser": BT.navigate(
            ctx.cfg, "", str(args.get("url", "")),
            requested_by="voice:user", confirmed=True)}
    if action == "fill":
        return {"browser": BT.fill(
            ctx.cfg, "", str(args.get("selector", "")),
            str(args.get("text", "")), requested_by="voice:user",
            confirmed=True)}
    if action == "click":
        return {"browser": BT.click(
            ctx.cfg, "", str(args.get("selector", "")),
            requested_by="voice:user", confirmed=True)}
    if action == "close":
        return {"browser": BT.close(
            ctx.cfg, "", requested_by="voice:user", confirmed=True)}
    raise ValueError(f"unknown browser-tabs action {action!r}")


def _b_lessons(ctx, args):
    from pathlib import Path as _Path

    from .learning import LearningLedger
    from .logging_setup import log_event as _log

    led = LearningLedger(_Path(ctx.cfg.state_dir()))
    action = args.get("action", "list")
    if action == "list":
        return {"lessons": led.lessons(), "reviews": led.reviews()}
    lid = str(args.get("lesson_id", "")).strip()
    if not lid:
        raise ValueError("lesson_id is required")
    if action == "promote":
        promoted = led.promote(lid)
        if promoted is None:
            raise ValueError(f"unknown lesson {lid!r}")
        _log("lessons.promote", actor="voice:user", lesson_id=lid[:16])
        return {"promoted": True, "lesson_id": lid, "lesson": promoted}
    if action == "remove":
        if not led.remove(lid):
            raise ValueError(f"unknown lesson {lid!r}")
        _log("lessons.remove", actor="voice:user", lesson_id=lid[:16])
        return {"removed": True, "lesson_id": lid}
    raise ValueError(f"unknown lessons action {action!r}")


def _b_power_status(ctx, args):
    from . import power as PW

    profile = PW.power_profile(ctx.cfg)
    return {"power": {"state": profile.get("state", "OK"),
                      "authorized": profile.get("authorized", 0),
                      "total": len(profile.get("capabilities", []))}}


def _b_websites(ctx, args):
    from . import websites as WS

    action = str(args.get("action", "list") or "list")
    if action in ("fetch", "search"):
        from . import web_tool as WT
        if action == "fetch":
            return {"web": WT.fetch(ctx.cfg, str(args.get("url", "") or ""))}
        return {"web": WT.search(ctx.cfg, str(args.get("query", "") or ""))}
    store = WS.website_integrations_store(ctx.cfg)
    try:
        items = list(store.integrations())
    except Exception:
        items = []

    def _public(rec: Any) -> Dict[str, Any]:
        if hasattr(rec, "to_dict"):
            try:
                data = dict(rec.to_dict())
            except Exception:
                data = {}
        elif isinstance(rec, dict):
            data = dict(rec)
        else:
            data = {"id": str(rec)}
        data.pop("secret", None)
        data.pop("token", None)
        return data

    action = args.get("action", "list")
    if action == "show":
        want = str(args.get("site_id", ""))
        for rec in items:
            pub = _public(rec)
            if str(pub.get("id", "")) == want or \
                    str(pub.get("origin", "")) == want:
                return {"site": pub}
        raise ValueError(f"unknown site {want!r}")
    return {"sites": [_public(r) for r in items]}


def _b_computer(ctx, args):
    from . import computer_use as CU

    action = args.get("action", "status")
    if action == "doctor":
        return {"doctor": CU.doctor()}
    if action == "permissions":
        return {"permissions": CU.permissions()}
    if action == "install":
        return {"refused": CU.refuse_install()}
    return {"computer": CU.status()}


def _b_plugins_install(ctx, args):
    import json as _json

    if str(args.get("action", "install")) == "remote":
        from . import plugins as PL

        spec = str(args.get("identifier", ""))
        try:
            verdict = PL.classify_install_source(spec)
        except Exception:
            verdict = {"kind": "remote", "identifier": spec}
        if verdict.get("kind") == "remote":
            return PL.refuse_remote(str(verdict.get("identifier", spec)))
        return {"state": "FAILED", "code": "NOT_REMOTE_PLUGIN",
                "detail": "that looks like a local path: use `qubit skills "
                          "install <manifest>` or say 'install plugin from "
                          "manifest'",
                "owner": "qubit-policy",
                "rollback": "nothing installed or enabled"}
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
    """Provider profile registry (same profiles fns the CLI calls)."""
    from . import profiles as PF

    action = args.get("action", "list")
    if action == "show":
        return PF.profiles_show(ctx.cfg, str(args.get("kind", "")))
    if action == "check":
        profile = str(args.get("profile", "") or "").strip() or None
        return PF.profile_check(ctx.cfg, str(args.get("kind", "")), profile)
    if action == "select":
        return PF.apply_profile(ctx.cfg, str(args.get("kind", "")),
                                str(args.get("profile", "")),
                                dry_run=False, require_confirmation=True)
    if action == "reset":
        return PF.reset_profile(ctx.cfg, str(args.get("kind", "")),
                                dry_run=False)
    if action == "upgrade":
        return PF.upgrade_profile(ctx.cfg, str(args.get("kind", "")),
                                  str(args.get("profile", "") or ""),
                                  dry_run=False)
    if action == "remove":
        return PF.remove_profile(ctx.cfg, str(args.get("kind", "")),
                                 str(args.get("profile", "")),
                                 dry_run=False)
    if action == "rollback":
        return PF.rollback_profile(ctx.cfg, str(args.get("kind", "")),
                                   dry_run=False)
    return PF.profiles_list(ctx.cfg)


# ------------------------------------------------ model/provider profiles
# Spoken intents that map to the canonical resolver
# (voicebridge.providers.resolver). Mutating selections are confirm-gated by
# the entry (the shared journal is the human authorization); backends pass a
# raw secret reference only through the CLI/dashboard flow and never accept,
# echo, or log a key value.
def _mp_role(args: Dict[str, Any], default: str = "chat") -> str:
    return str(args.get("role", "") or default)


def _b_model_profile_status(ctx, args):
    """Active model profile plus the active voice/STT/TTS registry records."""
    from . import profiles as PF
    from .providers import resolver as R

    role = _mp_role(args)
    try:
        eff = R.resolve(ctx.cfg, role=role, session=ctx.session)
        model: Dict[str, Any] = {"state": "OK", "role": role,
                                 "effective": eff.to_public_dict()}
    except R.ResolverError as exc:
        model = {"state": "FAILED", "role": role, **exc.to_dict()}
    active = PF.current_profiles(ctx.cfg)
    return {"model_profile": {
        "model": model,
        "voice": active.get("voice", {}),
        "stt": active.get("stt", {}),
        "tts": active.get("tts", {}),
    }}


def _b_model_profile_test(ctx, args):
    """Preflight a profile (same read-only test the CLI performs)."""
    from .providers import resolver as R

    pid = str(args.get("profile_id", "") or "").strip()
    try:
        if not pid:
            pid = R.resolve(ctx.cfg, role=_mp_role(args),
                            session=ctx.session).profile_id
        return {"model_profile": R.test_profile(
            ctx.cfg, pid, probe=bool(args.get("probe")))}
    except R.ResolverError as exc:
        return {"model_profile": exc.to_dict()}


def _b_speech_profile_test(ctx, args):
    """Read-only preflight of the active (or named) speech profile.

    Uses the speech registry (ASR/TTS/realtime), not the language-model
    registry, so 'test my speech profile' inspects the real speech profile.
    """
    from .providers import speech as SP

    pid = str(args.get("profile_id", "") or "").strip()
    kind = str(args.get("kind", "") or SP.SPEECH_OUT).strip() or SP.SPEECH_OUT
    try:
        if pid:
            rec = SP.show_speech_profile(ctx.cfg, pid)
        else:
            eff = SP.resolve_speech(ctx.cfg, kind, session=ctx.session)
            rec = eff.to_public_dict()
        return {"model_profile": {"state": "OK", "kind": kind,
                                  "profile": rec}}
    except SP.ResolverError as exc:
        return {"model_profile": {"state": "FAILED", "kind": kind,
                                  **exc.to_dict()}}
    except Exception as exc:  # noqa: BLE001 - typed, no fabrication
        return {"model_profile": {
            "state": "FAILED", "kind": kind,
            "code": getattr(exc, "code", type(exc).__name__),
            "detail": str(exc)[:200]}}


def _b_model_profile_local(ctx, args):
    from .providers import resolver as R

    out = R.set_session_profile(ctx.cfg, ctx.session, R.DEFAULT_LOCAL_ID,
                                role=_mp_role(args), confirm=True)
    return {"model_profile": {"state": out.get("state", "OK"),
                              "profile": R.DEFAULT_LOCAL_ID,
                              "session": ctx.session, "selected": out}}


def _b_model_profile_use(ctx, args):
    from .providers import resolver as R

    pid = str(args.get("profile_id", "") or "").strip()
    if not pid:
        return {"model_profile": {
            "state": "FAILED", "code": "MALFORMED",
            "detail": "which model profile? name one, or say 'use my local "
                      "brain'."}}
    return {"model_profile": R.set_session_profile(
        ctx.cfg, ctx.session, pid, role=_mp_role(args), confirm=True)}


def _b_model_profile_realtime(ctx, args):
    """Select the registered realtime voice profile (never invents one)."""
    from .providers import speech as SP

    pid = str(args.get("profile_id", "") or "").strip()
    if not pid:
        candidates = [p for p in SP.load_speech_profiles(ctx.cfg, SP.REALTIME)
                      if p.enabled]
        if not candidates:
            return {"model_profile": {
                "state": "UNAVAILABLE", "code": "NO_REALTIME_PROFILE",
                "detail": "no realtime voice profile is registered; create one "
                          "with the speech profile lifecycle or the dashboard"}}
        pid = candidates[0].id
    if SP.get_speech_profile(ctx.cfg, pid) is None:
        return {"model_profile": {
            "state": "FAILED", "code": "UNKNOWN_PROFILE",
            "detail": f"no realtime speech profile {pid!r}"}}
    out = SP.set_session_speech_profile(ctx.cfg, ctx.session, SP.REALTIME,
                                        pid, confirm=True)
    return {"model_profile": {"state": out.get("state", "OK"),
                              "kind": SP.REALTIME, "profile": pid,
                              "selected": out}}


def _b_model_profile_coding(ctx, args):
    """Route the coding role to the effective coding profile for the session."""
    from .providers import resolver as R

    pid = str(args.get("profile_id", "") or "").strip() or None
    try:
        eff = R.resolve(ctx.cfg, role="coding", profile_id=pid,
                        session=ctx.session)
    except R.ResolverError as exc:
        return {"model_profile": exc.to_dict()}
    out = R.set_session_profile(ctx.cfg, ctx.session, eff.profile_id,
                               role="coding", confirm=True)
    return {"model_profile": {
        "state": out.get("state", "OK"), "role": "coding",
        "profile": eff.profile_id, "effective": eff.to_public_dict(),
        "selected": out,
        "next": "coding-role work resolves this profile through the canonical "
                "resolver (chat_transport.turn(role='coding')); ordinary chat "
                "turns keep the chat role unless you select it explicitly"}}


def _b_model_profile_local_only(ctx, args):
    """Clear this session's overrides and reselect the local brain."""
    from .providers import resolver as R

    cleared = R.clear_session_profile(ctx.cfg, ctx.session, role="*")
    out = R.set_session_profile(ctx.cfg, ctx.session, R.DEFAULT_LOCAL_ID,
                                role=R.DEFAULT_ROLE, confirm=True)
    return {"model_profile": {"state": out.get("state", "OK"),
                              "local_only": True,
                              "profile": R.DEFAULT_LOCAL_ID,
                              "cleared": cleared, "selected": out}}


def _b_migrate_status(ctx, args):
    from . import product as P

    try:
        return {"migration": P.branding_status()}
    except Exception as exc:
        return {"migration": {"state": "UNAVAILABLE", "detail": str(exc)[:160]}}


def _runner_power(cfg):
    """Shared 8O power policy for voice backends (same object CLI passes)."""
    from . import power as PW

    return PW.power_policy(cfg)


def _b_task_run(ctx, args):
    """Run one typed task through the Qubit-native agent (same agent the
    CLI ``task`` path uses). Confirm-gated: agent execution only runs
    after the voice pipeline's explicit confirmation. DISABLED
    (agent.enabled=false) and EMERGENCY_STOP surface as typed unavailable
    states — never fake success."""
    text = str(args.get("task_text", "") or "").strip()
    if not text and str(args.get("from_current", "")).lower() in (
            "1", "true", "yes"):
        # "run this task": the newest actionable kanban task supplies the
        # text (same KB.current_task the CLI `task --current` resolves).
        # Titles/bodies are redact-on-read in kanban._task_dict.
        from . import kanban as _KB
        current = _KB.current_task(ctx.cfg)
        if current.get("state") != "OK" or not current.get("task"):
            return {"task": {"state": "UNAVAILABLE", "code": "NO_OPEN_TASKS",
                             "detail": str(current.get("detail", ""))[:200]}}
        task = current["task"]
        text = (str(task.get("title", "")) + "\n" +
                str(task.get("body", ""))).strip()
    if not text:
        raise ValueError("missing task text")
    # Canonical operation path: the task resolves the selected model/provider
    # profile through the same chat_transport resolver+gateway CLI/dashboard
    # use, so a chosen profile is honored and the receipt is truthful.
    from . import chat_transport as _CT

    res = _CT.turn(ctx.cfg, text, session=ctx.session or "qubit-voice",
                   role="coding")
    if not isinstance(res, dict) or not res.get("ok"):
        code = str((res or {}).get("code", "AGENT_ERROR"))
        detail = str((res or {}).get("detail")
                     or (res or {}).get("error", ""))[:200]
        return {"task": {"state": "UNAVAILABLE", "code": code,
                         "detail": detail}}
    return {"task": {"state": "OK", "runtime": "qubit",
                     "reply": str(res.get("reply", "")),
                     "receipt": res.get("receipt", {})}}


def _b_actions(ctx, args):
    """Packet 8F action broker (same runner + stores the CLI uses).

    Read paths execute directly. prepare/execute/retry/recover run only
    after the voice pipeline's confirmation and pass confirmed=True with
    the canonical human principal — the same contract the CLI's --confirm
    path uses. Structured-args flows (preview/prepare/retry/recover with
    JSON envelopes) stay CLI/dashboard-only (REASON_NESTED extras)."""
    from . import actions as A

    runner = A.action_runner(ctx.cfg, executors={},
                                 power=_runner_power(ctx.cfg))
    action = args.get("action", "list")
    if action == "list":
        return {"actions": {"actions": runner.actions()}}
    if action == "show":
        return {"action": runner.show(str(args.get("action_id", "")))}
    if action == "check":
        return runner.check(str(args.get("action_id", "")),
                            str(args.get("scope", "") or ""))
    if action == "receipts":
        return runner.receipts(int(args.get("limit", 30) or 30))
    if action == "receipt":
        return {"receipt": runner.receipt(str(args.get("receipt_id", "")))}
    if action == "execute":
        return runner.execute(
            str(args.get("action_id", "")),
            args={}, target=str(args.get("target", "") or ""),
            session=str(getattr(ctx, "session", "") or ""),
            account_scope="", scope=str(args.get("scope", "") or ""),
            source="user", confirmed=True)
    if action == "cancel":
        return runner.cancel(str(args.get("receipt_id", "")))
    raise ValueError(f"unknown actions action {action!r}")


def _ghost_browser_origin(cfg: Any) -> str:
    """Resolve the Qubit-current tab's https origin for role-bound actions.

    Role-bound Ghost capabilities require the envelope target to be an
    approved https origin (no bare/desktop fallback, no guessing). "This
    button" means the current tab's origin; with no tab open the caller
    gets a typed refusal with guidance instead of a fabricated target."""
    from . import browser_tabs as BT

    try:
        inv = BT.tabs(cfg)
    except Exception as exc:
        raise ValueError(
            "no browser tab available: "
            f"{getattr(exc, 'code', None) or exc}") from exc
    if not isinstance(inv, dict) or inv.get("state") != "OK":
        raise ValueError(
            f"no browser tab available: {inv.get('code', 'BROWSER_DOWN')}"
            if isinstance(inv, dict) else "no browser tab available")
    tabs = inv.get("tabs", []) or []
    cur = str(inv.get("current_target", "") or "")
    page = next((p for p in tabs if str(p.get("id", "")) == cur), None)
    if page is None and tabs:
        page = tabs[0]
    if page is None:
        raise ValueError("no open browser tabs: open a page first, then"
                         " say the command again")
    url = str(page.get("url", "") or "")
    match = re.match(r"(https?)://([^/:?#]+)(?::\d+)?(?:/|$|#|\?)", url)
    if not match or match.group(1) != "https":
        raise ValueError(f"current tab is not an https page ({url[:60]!r});"
                         " open the page first, then say the command again")
    return f"https://{match.group(2)}"


def _b_ghost(ctx, args):
    """Packet 8N supervised Ghost adapter (same runner the CLI uses).

    Disabled-by-default executes return the backend's honest
    ADAPTER_DISABLED; session grants are human-issued, bounded, expiring,
    and target-bound. Structured-args preview stays CLI/dashboard-only."""
    from . import ghost as G

    G.register_ghost_specs()
    runner = G.ghost_runner(ctx.cfg, executors={},
                            power=_runner_power(ctx.cfg))
    action = args.get("action", "list")
    if action == "list":
        return {"capabilities": runner.ghost_actions()}
    if action == "status":
        return runner.ghost_status()
    if action == "show":
        spec = G.ghost_spec(str(args.get("action_id", "")))
        if spec is None:
            raise ValueError(f"unknown action {args.get('action_id')!r}")
        return {"action": spec.to_dict()}
    if action == "check":
        return runner.check(str(args.get("action_id", "")),
                            str(args.get("scope", "") or ""))
    if action == "grant":
        ttl = args.get("ttl")
        try:
            ttl_s = float(ttl) if ttl not in (None, "") else None
        except (TypeError, ValueError):
            raise ValueError("grant ttl must be a number of seconds")
        return runner.grant(str(args.get("capability", "")),
                            scopes=(str(args.get("scope", "") or "read"),),
                            target=str(args.get("target", "") or "desktop"),
                            role=str(args.get("role", "") or ""),
                            ttl_s=ttl_s)
    if action == "revoke":
        return runner.revoke_grant(str(args.get("grant_id", "")))
    if action == "preview":
        # No-side-effect preview with the exact args the execution would
        # use. Unknown action shapes fall through to the schema validator,
        # whose typed error names the missing field (never a guessed call).
        aid = str(args.get("action_id", ""))
        _t = str(args.get("target", "") or "")
        _x = str(args.get("text", "") or "")
        _role = str(args.get("role", "") or "chromium")
        if aid == "ghost.input.click":
            pargs: Dict[str, Any] = {"role": _role, "target": _t,
                                     "action": "click"}
        elif aid == "ghost.input.type":
            pargs = {"role": _role, "target": _t, "text": _x}
        elif aid == "ghost.browser.tabs":
            pargs = {"role": _role}
        elif aid == "ghost.input.key":
            pargs = {"target": _t, "key": _x}
        elif aid == "ghost.input.cursor":
            pargs = {"target": _t}
        elif aid in ("ghost.desktop.screenshot",):
            pargs = {"window": _t} if _t else {}
        elif aid in ("ghost.window.anchor", "ghost.window.close"):
            pargs = {"window": _t}
        elif aid == "ghost.desktop.discover":
            pargs = {"query": _t} if _t else {}
        elif aid == "ghost.desktop.launch":
            pargs = {"app": _t}
        else:
            pargs = {"target": _t} if _t else {}
        _spec = G.ghost_spec(aid)
        _scope = str(args.get("scope", "") or "")
        if not _scope and _spec is not None:
            _scope = _spec.scopes[0]
        return runner.preview(
            aid, pargs,
            target=_t or "desktop",
            scope=_scope,
            role=_role if "role" in (_spec.arg_schema
                                     if _spec is not None else {}) else "")
    if action == "click":
        try:
            origin = _ghost_browser_origin(ctx.cfg)
        except ValueError as exc:
            return {"state": "denied", "code": "NO_CURRENT_TAB",
                    "action_id": "ghost.input.click",
                    "detail": str(exc)[:300]}
        return runner.execute(
            "ghost.input.click",
            args={"role": str(args.get("role", "") or "chromium"),
                  "target": str(args.get("target", "") or ""),
                  "action": "click"},
            target=origin,
            scope=str(args.get("scope", "") or "web"),
            source="user", confirmed=True,
            role=str(args.get("role", "") or ""))
    if action == "type":
        try:
            origin = _ghost_browser_origin(ctx.cfg)
        except ValueError as exc:
            return {"state": "denied", "code": "NO_CURRENT_TAB",
                    "action_id": "ghost.input.type",
                    "detail": str(exc)[:300]}
        return runner.execute(
            "ghost.input.type",
            args={"role": str(args.get("role", "") or "chromium"),
                  "target": str(args.get("target", "") or ""),
                  "text": str(args.get("text", "") or "")},
            target=origin,
            scope=str(args.get("scope", "") or "web"),
            source="user", confirmed=True,
            role=str(args.get("role", "") or ""))
    if action == "read_tab":
        try:
            origin = _ghost_browser_origin(ctx.cfg)
        except ValueError as exc:
            return {"state": "denied", "code": "NO_CURRENT_TAB",
                    "action_id": "ghost.browser.tabs",
                    "detail": str(exc)[:300]}
        return runner.execute(
            "ghost.browser.tabs",
            args={"role": str(args.get("role", "") or "chromium")},
            target=origin,
            scope=str(args.get("scope", "") or "web"),
            source="user", confirmed=True,
            role=str(args.get("role", "") or ""))
    if action == "retry":
        # Re-supply the exact original args (receipts never store private
        # content, so the speaker repeats them); any difference is an
        # honest ARGS_MISMATCH refusal, never a silent new action.
        again = {k: str(args.get(k, "") or "")
                 for k in ("role", "target", "text", "action")}
        again = {k: v for k, v in again.items() if v}
        if not again.get("role"):
            again["role"] = "chromium"
        return runner.retry(
            str(args.get("receipt_id", "")), args=again,
            target=str(args.get("target", "") or "desktop"),
            scope=str(args.get("scope", "") or ""),
            source="user", confirmed=True,
            role=str(args.get("role", "") or ""))
    if action == "recover":
        return runner.recover(str(args.get("receipt_id", "")),
                              source="user", confirmed=True)
    if action == "execute":
        return runner.execute(
            str(args.get("action_id", "")), args={},
            target=str(args.get("target", "") or "desktop"),
            scope=str(args.get("scope", "") or ""),
            source="user", confirmed=True, role="")
    if action == "cancel":
        return runner.cancel(str(args.get("receipt_id", "")))
    if action == "receipts":
        return runner.receipts(int(args.get("limit", 30) or 30))
    if action == "status":
        return runner.ghost_status()
    if action == "enable":
        return runner.adapter.enable(requested_by="user")
    if action == "disable":
        return runner.adapter.disable(requested_by="user")
    if action == "emergency-stop":
        return runner.adapter.emergency_stop()
    if action == "reset":
        return runner.adapter.reset(requested_by="user")
    raise ValueError(f"unknown ghost action {action!r}")


def _b_runtime(ctx, args):
    from . import runtime_images as RI

    action = args.get("action", "status")
    if action == "images":
        return RI.images()
    if action == "status":
        return RI.status(ctx.cfg)
    if action == "doctor":
        return RI.doctor(ctx.cfg)
    if action == "install":
        return RI.install(ctx.cfg, str(args.get("name", "")),
                          requested_by="voice:user", confirmed=True,
                          source=str(args.get("source", "") or ""))
    if action == "upgrade":
        return RI.upgrade(ctx.cfg, str(args.get("name", "")),
                          requested_by="voice:user", confirmed=True,
                          source=str(args.get("source", "") or ""))
    if action == "rollback":
        return RI.rollback(ctx.cfg, str(args.get("name", "")),
                           requested_by="voice:user", confirmed=True)
    if action == "remove":
        return RI.remove(ctx.cfg, str(args.get("name", "")),
                         requested_by="voice:user", confirmed=True)
    if action == "recover":
        return RI.recover(ctx.cfg, str(args.get("name", "")),
                          requested_by="voice:user", confirmed=True)
    if action == "repair":
        return RI.repair(ctx.cfg, requested_by="voice:user", confirmed=True)
    if action == "history":
        return {"history": RI.update_history(
            ctx.cfg, str(args.get("name", "") or ""))}
    if action == "update_report":
        return {"update": RI.update_report(ctx.cfg)}
    if action == "discovery":
        from .runtime_inventory import _discovery

        return {"discovery": _discovery(ctx.cfg)}
    if action == "marketplace":
        from .runtime_inventory import _marketplace

        return {"marketplace": _marketplace(ctx.cfg)}
    raise ValueError(f"unknown runtime action {action!r}")


def _opencode_voice_project(cfg: Any, explicit: str = "") -> str:
    """Resolve the voice coding project: explicit path, else the active
    project's first folder, else "." (same default the CLI uses).

    The resolved path is still validated by the runner's project/worktree
    confinement — resolution never widens scope, it only names the
    candidate the confinement checks."""
    text = str(explicit or "").strip()
    if text:
        return text
    try:
        from . import projects as PJ

        active = PJ.active_project(cfg)
        if active.get("state") == "OK" and active.get("project"):
            folders = active["project"].get("folders", []) or []
            if folders:
                return str(folders[0])
    except Exception:
        pass
    return "."


def _b_opencode(ctx, args):
    """OpenCode supervised coding workflow (same runner the CLI uses).

    Voice coding runs are disposable-workdir only with an exact spoken
    confirmation: in-project writes stay CLI/dashboard-only behind a prior
    preview hash (typed refusal with guidance here). Status/show/check/
    receipts/preview read immediately; run/retry/recover/cancel/grant/
    revoke execute after the voice pipeline's explicit confirmation.
    Confinement, argv allowlist, secret scrubbing, and receipts are the
    runner's own — identical on all three surfaces."""
    from . import opencode as OC

    OC.register_opencode_specs()
    runner = OC.opencode_runner(ctx.cfg, executors={},
                                power=_runner_power(ctx.cfg))
    action = args.get("action", "status")
    if action == "status":
        return runner.status()
    if action == "list":
        return {"capabilities": runner.opencode_actions()}
    if action == "show":
        spec = OC.opencode_spec(str(args.get("action_id", "")))
        if spec is None:
            raise ValueError(f"unknown action {args.get('action_id')!r}")
        return {"action": spec.to_dict()}
    if action == "check":
        return runner.check(str(args.get("action_id", "")),
                            str(args.get("scope", "") or ""))
    if action == "grant":
        ttl = args.get("ttl")
        try:
            ttl_s = float(ttl) if ttl not in (None, "") else None
        except (TypeError, ValueError):
            raise ValueError("grant ttl must be a number of seconds")
        return runner.grant(str(args.get("capability", "")
                                or "act.opencode.run"),
                            scopes=(str(args.get("scope", "") or "code"),),
                            ttl_s=ttl_s)
    if action == "revoke":
        return runner.revoke_grant(str(args.get("grant_id", "")))
    if action == "preview":
        task = str(args.get("task", "") or "").strip()
        if not task:
            raise ValueError("missing coding task text")
        project = _opencode_voice_project(ctx.cfg,
                                          str(args.get("project", "") or ""))
        return runner.preview(OC.ACT_RUN,
                              {"project": project, "task": task,
                               "disposable": True,
                               **_opencode_voice_timeout(args)},
                              target=project, scope="code")
    if action == "run":
        task = str(args.get("task", "") or "").strip()
        if not task:
            raise ValueError("missing coding task text")
        if str(args.get("in_project", "") or "").lower() in (
                "1", "true", "yes"):
            return {"state": "denied", "code": "VOICE_DISPOSABLE_ONLY",
                    "action_id": OC.ACT_RUN,
                    "detail": "voice coding runs use a disposable worktree"
                              " only; for in-project writes run `qubit"
                              " opencode preview` then `qubit opencode"
                              " execute --in-project --expected-hash"
                              " <hash> --confirm`, or use the dashboard"
                              " prepare/execute controls"}
        project = _opencode_voice_project(ctx.cfg,
                                          str(args.get("project", "") or ""))
        return OC.request_coding_run(
            runner, project=project,
            task_args={"project": project, "task": task,
                       "disposable": True,
                       **_opencode_voice_timeout(args)},
            target=project, scope="code", source="user", confirmed=True)
    if action == "cancel":
        return runner.cancel(str(args.get("receipt_id", "")))
    if action == "receipts":
        try:
            limit = int(args.get("limit", 30) or 30)
        except (TypeError, ValueError):
            raise ValueError("receipts limit must be a number")
        return runner.receipts(max(1, min(limit, 100)))
    if action == "retry":
        task = str(args.get("task", "") or "").strip()
        if not task:
            raise ValueError("missing coding task text: repeat the exact"
                             " original task to retry")
        project = _opencode_voice_project(ctx.cfg,
                                          str(args.get("project", "") or ""))
        return runner.retry(str(args.get("receipt_id", "")),
                            args={"project": project, "task": task,
                                  "disposable": True,
                                  **_opencode_voice_timeout(args)},
                            target=project, scope="code",
                            source="user", confirmed=True)
    if action == "recover":
        return runner.recover(str(args.get("receipt_id", "")),
                              source="user", confirmed=True)
    raise ValueError(f"unknown opencode action {action!r}")


def _opencode_voice_timeout(args: Dict[str, Any]) -> Dict[str, Any]:
    """Optional bounded voice timeout (contract MAX_TIMEOUT_S ceiling)."""
    raw = args.get("timeout", "")
    if raw in (None, ""):
        return {}
    try:
        timeout = int(raw)
    except (TypeError, ValueError):
        raise ValueError("timeout must be a number of seconds")
    if not 1 <= timeout <= 600:
        raise ValueError("timeout must be 1..600 seconds")
    return {"timeout_s": timeout}


def _b_verified(ctx, args):
    """Verified-skills curator (same store the CLI uses)."""
    from .verified_skills import verified_skills_store

    store = verified_skills_store(ctx.cfg)
    action = args.get("action", "list")
    if action == "list":
        status = str(args.get("status", "") or "")
        items = store.list(status=status) if status else store.list()
        return {"skills": [r.to_dict() for r in items]}
    if action == "show":
        rec = store.get(str(args.get("skill_id", "")))
        if rec is None:
            raise ValueError(f"unknown skill {args.get('skill_id')!r}")
        return {"skill": rec.to_dict()}
    if action == "recall":
        return store.recall(str(args.get("query", "")),
                            top_k=int(args.get("top_k", 5) or 5))
    if action == "verdict":
        rec = store.apply_verdict(
            str(args.get("skill_id", "")), decision=str(args.get("decision", "")),
            requested_by="voice:user")
        return {"id": rec.id, "status": rec.status}
    if action == "pin":
        rec = store.pin(str(args.get("skill_id", "")), requested_by="voice:user")
        return {"id": rec.id, "pinned": rec.pinned}
    if action == "unpin":
        rec = store.unpin(str(args.get("skill_id", "")), requested_by="voice:user")
        return {"id": rec.id, "pinned": rec.pinned}
    raise ValueError(f"unknown verified action {action!r}")


def _b_experiments(ctx, args):
    """Packet 8P experiment store (same store the CLI uses)."""
    from .experiments.store import experiments_store

    store = experiments_store(ctx.cfg)
    action = args.get("action", "status")
    if action == "status":
        out = store.journal_verify()
        out["experiments"] = len(store.experiments())
        return out
    if action == "candidates":
        return {"candidates": [c.to_dict() for c in store.candidates()]}
    if action == "list":
        return {"experiments": [e.to_dict() for e in store.experiments()]}
    if action == "show":
        exp = store.experiment(str(args.get("experiment_id", "")))
        return {"experiment": exp.to_dict(),
                "evaluate": store.evaluate(str(args.get("experiment_id", "")))}
    if action == "evaluate":
        return store.evaluate(str(args.get("experiment_id", "")))
    if action == "evaluate":
        return store.evaluate(str(args.get("experiment_id", "")))
    if action == "register_candidate":
        cand = store.register_candidate(role=str(args.get("role", "")),
                                        label=str(args.get("label", "")),
                                        model=str(args.get("model", "")),
                                        description=str(args.get("description", "")))
        return {"id": cand.id, "fingerprint": cand.fingerprint}
    if action == "start":
        exp = store.start_experiment(
            champion_config_id=str(args.get("champion", "")),
            challenger_config_id=str(args.get("challenger", "")))
        return {"id": exp.id, "status": exp.status}
    if action == "trial":
        tri = store.start_trial(str(args.get("experiment_id", "")),
                                variant=str(args.get("variant", "")))
        return {"id": tri.id, "variant": tri.variant}
    if action == "finish_trial":
        tri = store.finish_trial(str(args.get("trial_id", "")),
                                 outcome=str(args.get("outcome", "")),
                                 note=str(args.get("note", "") or ""))
        return {"id": tri.id, "outcome": tri.outcome}
    if action == "signal":
        import re as _re
        raw = str(args.get("value", "") or "")
        num = _re.search(r"[0-9]*\.?[0-9]+", raw)
        if not num:
            raise ValueError("signal value must be a number in [0,1]")
        value = float(num.group(0))
        sig = store.record_signal(str(args.get("trial_id", "")),
                                  kind=str(args.get("kind", "")),
                                  source="voice",
                                  value=value)
        return {"id": sig.id, "kind": sig.kind, "value": sig.value}
    if action == "abort":
        # Canonical human principal (voice audit carries the channel);
        # the store refuses anything but "user".
        return store.abort(str(args.get("experiment_id", "")),
                           requested_by="user")
    if action == "snapshot":
        return store.snapshot()
    if action == "promote":
        # Canonical human principal; without a spoken gate note the store
        # honestly returns hold (never auto-promote without a gate report).
        return store.record_promotion(str(args.get("experiment_id", "")),
                                      owner_approved=True,
                                      gate_report=str(args.get("gate_note", "") or ""),
                                      requested_by="user")
    raise ValueError(f"unknown experiments action {action!r}")


def _b_product(ctx, args):
    """Product surface (same voicebridge.product fns the CLI calls)."""
    from . import product as P

    action = args.get("action", "status")
    if action == "status":
        return {"product": {"paths": P.path_map_diagnostic(),
                            "capabilities": P.capabilities(),
                            "migration": P.branding_status(),
                            "boundary": P.package_boundary(),
                            "service": P.service_owner(),
                            "upgrade": P.upgrade_contract(),
                            "tray": P.tray_status()}}
    if action == "probe":
        return P.install(dry_run=True)
    if action == "first_run":
        return P.first_run(dry_run=True)
    if action == "install":
        return P.install(dry_run=False)
    if action == "migrate":
        return P.migrate(dry_run=False, backup_dir=None)
    if action == "rollback":
        return P.rollback(dry_run=False, backup_dir=None)
    if action == "repair":
        return P.repair(dry_run=False)
    raise ValueError(f"unknown product action {action!r}")


def _b_package(ctx, args):
    """Release certification (same release checks the CLI runs)."""
    from . import distribution as D
    from .release import certify

    action = args.get("action", "certify")
    if action == "certify":
        return certify()
    if action == "status":
        return {"package": {"manifest": D.manifest(),
                            "doctor": D.doctor(ctx.cfg),
                            "upgrade_plan": D.upgrade_plan(ctx.cfg),
                            "uninstall_plan": D.uninstall_plan(ctx.cfg),
                            "model_record": D.model_artifact_record(ctx.cfg)}}
    raise ValueError(f"unknown package action {action!r}")


def _b_reports(ctx, args):
    """Read-only voice projection of the dashboard's published report
    bundles (same on-disk store ``ReportStore`` owns — no communications
    import per the Packet 8A boundary, which reserves that package to
    cli/webui/scripts).

    Lists bundle manifests newest-first with scalar fields only (never
    paths, bodies, or secrets). A differential test pins this output
    against ``ReportStore.index()`` so schema drift fails loudly instead
    of silently diverging."""
    import json as _json
    import re as _re

    _id_re = _re.compile(r"^[a-z0-9_][a-z0-9_-]{0,63}$")
    root = Path(ctx.cfg.state_dir()) / "reports"
    entries = []
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError:
        children = []
    for child in children:
        name = child.name
        if not _id_re.match(name):
            continue
        try:
            if not child.is_dir():
                continue
            manifest = _json.loads(
                (child / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(manifest, dict):
            continue
        try:
            created = float(manifest.get("created_at") or 0)
        except (TypeError, ValueError):
            created = 0.0
        platforms = manifest.get("platforms") or []
        entries.append({
            "report_id": name,
            "summary": str(manifest.get("summary", "") or "")[:200],
            "created_at": created,
            "acknowledged": bool(manifest.get("acknowledged", False)),
            "platforms": [str(p)[:40] for p in platforms]
            if isinstance(platforms, list) else [],
        })
    entries.sort(key=lambda e: e["created_at"], reverse=True)
    limit = args.get("limit", 10)
    try:
        limit = max(1, min(int(limit or 10), 50))
    except (TypeError, ValueError):
        limit = 10
    return {"reports": {"state": "OK", "total": len(entries),
                        "reports": entries[:limit]}}


def _b_webui(ctx, args):
    """Local dashboard probe (same loopback check the CLI runs; the token
    is never placed in a URL, argument, or output)."""
    import urllib.request as _url

    try:
        host = ctx.cfg.get("webui", "host", default="127.0.0.1") or "127.0.0.1"
        port = int(ctx.cfg.get("webui", "port", default=8890) or 8890)
    except Exception:
        host, port = "127.0.0.1", 8890
    url = f"http://{host}:{port}/"
    try:
        with _url.urlopen(url, timeout=1.5) as resp:
            reachable = resp.status == 200
    except Exception:
        reachable = False
    return {"dashboard": {"state": "READY" if reachable else "UNAVAILABLE",
                          "url": url, "token_in_url": False,
                          "started_second_bridge": False}}


def _b_frontier_show(ctx, args):
    """One provider/model catalog entry (same policy + redaction the CLI
    ``frontier show`` uses)."""
    from .providers.credentials import redact_ref
    from .providers.policy import FrontierPolicy, disclosure_block

    ident = str(args.get("model_id", "") or "").strip()
    if "/" not in ident:
        raise ValueError("expected provider/model")
    prov, mod = ident.split("/", 1)
    policy = FrontierPolicy.from_config(ctx.cfg)
    pcfg = policy.provider_cfg(prov)
    return {"provider": prov, "model": mod,
            "allowed": policy.is_allowed(prov, mod),
            "enabled": policy.enabled,
            "base_url": pcfg.get("base_url", ""),
            "key_ref": redact_ref(str(pcfg.get("key_ref", ""))),
            "disclosure": disclosure_block(policy, prov, mod, 0.0)}


def _b_frontier_chat(ctx, args):
    """Bounded model completion via the 8Q provider gateway (same gateway
    + policy + redaction the CLI ``frontier chat`` uses). Local route by
    default (no network beyond Ollama); cloud routes run only after the
    voice pipeline's confirmation."""
    from .providers import gateway as G
    from .providers.catalog import catalog_from_allowlist
    from .providers.contracts import ProviderRequest
    from .providers.policy import FrontierPolicy

    prompt = str(args.get("prompt", "") or "").strip()
    if not prompt:
        raise ValueError("missing prompt")
    policy = FrontierPolicy.from_config(ctx.cfg)
    route = str(args.get("route", "") or "local").lower()
    req = ProviderRequest(prompt=prompt, max_tokens=policy.max_tokens,
                          timeout_s=policy.timeout_s, route=route,
                          provider="", model="")
    res = G.run(req, ctx.cfg,
                catalog=catalog_from_allowlist(policy.allowlist),
                allow_private=False)
    return {"completion": res.to_public_dict()}


def _b_commands(ctx, args):
    """List every voice command (aliases + help; no secrets — entries never
    carry secret-shaped args by test)."""
    return {"commands": [
        {"id": e.id, "title": e.title, "aliases": list(e.aliases)[:6],
         "confirm": bool(e.confirm), "help": e.help,
         "example": (e.examples[0] if e.examples else "")}
        for e in sorted(REGISTRY.values(), key=lambda e: e.id)]}


def _audio_asr_hw(cfg):
    """ASR hardware capability: the REAL configured-provider identity plus a
    bounded dependency probe (no model download/load).

    A transcription test needs a WAV the voice pipeline cannot carry, so the
    result is a typed gate that names the operator CLI; it is resolved from the
    real configured provider and never fabricates a transcript.
    """
    from importlib.util import find_spec

    from .audio.qualify import provider_identity

    identity = provider_identity(cfg).get("asr", {})
    provider = str(identity.get("actual_provider")
                   or identity.get("configured_provider") or "")
    module = {"whisper": "faster_whisper", "sherpa": "sherpa_onnx",
              "personaplex": "personaplex"}.get(provider, "")
    present = bool(module) and find_spec(module) is not None
    return {"state": "NEEDS_AUDIO", "code": "ASR_TEST_NEEDS_WAV",
            "action": "asr_test", "provider": identity,
            "dependency": module, "dependency_present": present,
            "operator_cli": "qubit asr-test <file.wav>",
            "detail": "transcription test needs a WAV file; the configured "
                      "provider identity is resolved (no fake transcript)"}


def _audio_voice_id_hw(cfg, *, enroll):
    """Real voice-ID backend state (sherpa-onnx ``SpeakerVerifier``).

    Loads the configured enrollment state and reports whether the speaker model
    is present and how many voices are enrolled. Enroll/verify themselves need
    live recorded audio, so the credential-independent result is a typed gate
    that never fabricates an enrollment score.
    """
    from .voice_id import SpeakerUnavailable, SpeakerVerifier

    verifier = SpeakerVerifier(
        model_dir=cfg.model_dir(), state_dir=cfg.state_dir(),
        name=str(cfg.get("speaker_verify", "name", default="user")),
        threshold=float(cfg.get("speaker_verify", "threshold", default=0.65)))
    model_present = bool(verifier.model_path.exists())
    loaded, error = False, ""
    try:
        verifier.load()
        loaded = True
    except SpeakerUnavailable as exc:
        error = str(exc)[:160]
    except Exception as exc:  # bounded: never a traceback on the voice path
        error = str(exc)[:160]
    if not loaded:
        state, code = "MODEL_MISSING", "VOICE_ID_MODEL_MISSING"
    elif verifier.num_speakers:
        state, code = "READY", "VOICE_ID_ENROLLED"
    else:
        state, code = "READY", "VOICE_ID_NO_ENROLLMENT"
    return {"state": state, "code": code,
            "action": "enroll" if enroll else "verify",
            "model": "sherpa-onnx", "model_present": model_present,
            "enrolled_speakers": int(verifier.num_speakers),
            "operator_cli": "qubit enroll" if enroll
                            else "qubit verify <file.wav>",
            "error": error}


def _b_audio_hw(ctx, args):
    """Audio-hardware operations through the real bounded audio backend.

    Each action drives the production probe the operator qualification path
    uses (``voicebridge.audio.qualify``) or the real provider/voice-ID classes
    — never fake success. Physical mic/speaker work is operator-gated
    (``VOICE_BRIDGE_QUALIFY=1``); with the gate (or the device/model) absent
    the result is a typed gate/degraded state.
    """
    from .audio import qualify as Q

    action = str(args.get("action", "") or "")
    cfg = ctx.cfg
    if action == "mic_test":
        return {"audio_hw": Q.capture_probe(cfg, live=True)}
    if action == "tts_test":
        return {"audio_hw": Q.playback_probe(cfg, live=True)}
    if action == "roundtrip":
        return {"audio_hw": Q.run_qualification(cfg, dry_run=True)}
    if action == "asr_test":
        return {"audio_hw": _audio_asr_hw(cfg)}
    if action == "enroll":
        return {"audio_hw": _audio_voice_id_hw(cfg, enroll=True)}
    if action == "verify":
        return {"audio_hw": _audio_voice_id_hw(cfg, enroll=False)}
    return {"audio_hw": {"state": "UNKNOWN", "code": "UNKNOWN_AUDIO_ACTION",
                         "action": action}}


def _b_loop(ctx, args):
    """Guidance for the voice loop itself (you are already inside it)."""
    return {"guidance": "you are already talking to the loop: start it with "
                        "`qubit run` (push-to-talk), `qubit once` for one "
                        "utterance, or `qubit task \"...\"` for typed tasks"}


def _b_wake(ctx, args):
    """Wake-word spotter state (same wake module the CLI uses).

    Read-only readiness: READY with keywords, or NO_MODEL with the exact
    fetch step. Arming the live loop is an operator step (mic hardware +
    model files), never claimed here."""
    from . import wake as WK

    return {"wake": WK.status(ctx.cfg)}


def _b_device_enroll(ctx, args):
    """Trusted-device enrollment (same device module the CLI uses; the
    voice pipeline's confirmation is the explicit authorization)."""
    from . import device as Dev

    alias = str(args.get("model_alias", "") or "").strip() or None
    return Dev.enroll(ctx.cfg, model_alias=alias,
                      require_new_authorization=False)


def _b_branding(ctx, args):
    """Branding/migration surface (same profiles fns the CLI uses)."""
    from . import profiles as PF

    action = args.get("action", "plan")
    if action == "plan":
        return PF.branding_plan(ctx.cfg, dry_run=True)
    if action == "verify":
        return PF.branding_verify(ctx.cfg)
    if action == "migrate":
        return PF.branding_plan(ctx.cfg, dry_run=False)
    raise ValueError(f"unknown branding action {action!r}")


def _b_node_host(ctx, args):
    """Paired node host (same module the CLI/dashboard use).

    Reads execute directly. ``enroll``/``revoke`` are confirm-tier (the
    registry gate already ran, so they pass confirmed=True and the
    canonical human principal). ``dispatch`` passes through to the module,
    which only executes local-safe reads and returns typed refusals for
    every remote/unsafe capability (never a shell). Typed failures are
    returned as redacted dicts; the backend never raises out."""
    from . import node_host as NH

    cfg = ctx.cfg
    action = str(args.get("action", "status") or "status")
    try:
        if action == "status":
            return {"node_host": NH.status(cfg)}
        if action == "list":
            return {"node_host": NH.list_nodes(cfg)}
        if action == "show":
            return {"node_host": NH.show(
                cfg, str(args.get("node_id", "") or ""))}
        if action == "capabilities":
            return {"node_host": NH.capabilities(
                cfg, str(args.get("node_id", "") or ""))}
        if action == "enroll":
            caps = args.get("capabilities")
            if caps is None:
                caps = args.get("capability")
            return {"node_host": NH.enroll(
                cfg, name=str(args.get("name", "") or ""),
                endpoint=str(args.get("endpoint", "") or ""),
                capabilities=caps, confirmed=True, source="voice",
                requested_by="voice:user")}
        if action == "revoke":
            return {"node_host": NH.revoke(
                cfg, str(args.get("node_id", "") or ""), confirmed=True,
                source="voice", requested_by="voice:user")}
        if action == "dispatch":
            return {"node_host": NH.dispatch(
                cfg, str(args.get("node_id", "") or ""),
                str(args.get("capability", "") or ""), args=None,
                confirmed=True, requested_by="voice:user")}
    except NH.NodeHostError as exc:
        return {"node_host": {
            "state": "FAILED", "code": exc.code,
            "detail": _redact(exc.detail)[:300], "owner": "qubit-policy",
            "rollback": "node registry untouched; nothing dispatched"}}
    return {"node_host": {
        "state": "FAILED", "code": NH.NODE_BAD_INPUT,
        "detail": f"unknown node host action {action!r}",
        "owner": "qubit-policy",
        "rollback": "node registry untouched; nothing dispatched"}}


def _b_task_ledger(ctx, args):
    """Unified background-task ledger (read-only read model).

    ``list``/``show``/``timeline`` accept an optional source filter. Bad
    caller input is returned as a typed redacted dict; the backend never
    raises out."""
    from . import task_ledger as TL

    cfg = ctx.cfg
    action = str(args.get("action", "status") or "status")
    source = args.get("source") or None
    try:
        if action == "status":
            return {"task_ledger": TL.status(cfg)}
        if action == "list":
            limit = args.get("limit")
            return {"task_ledger": TL.list_tasks(
                cfg, source=source, state=(args.get("state") or None),
                limit=(limit if limit not in (None, "") else 50))}
        if action == "show":
            return {"task_ledger": TL.show(
                cfg, str(args.get("task_id", "") or ""), source=source)}
        if action == "timeline":
            return {"task_ledger": TL.timeline(
                cfg, str(args.get("task_id", "") or ""), source=source)}
    except TL.TaskLedgerError as exc:
        return {"task_ledger": {
            "state": "FAILED", "code": exc.code,
            "detail": _redact(exc.detail)[:300], "owner": "qubit-policy",
            "rollback": "read-only; nothing to roll back"}}
    return {"task_ledger": {
        "state": "FAILED", "code": TL.TASK_BAD_INPUT,
        "detail": f"unknown task ledger action {action!r}",
        "owner": "qubit-policy",
        "rollback": "read-only; nothing to roll back"}}


def _b_media_gen(ctx, args):
    """Media generation registry/gate (same module the CLI/dashboard use).

    ``generate`` is confirm-tier (the registry gate already ran): the
    module still enforces e-stop, bounds and the credential/transport
    gate, raising typed errors that are returned here as redacted dicts."""
    from . import media_gen as MG

    cfg = ctx.cfg
    action = str(args.get("action", "status") or "status")
    try:
        if action == "status":
            return {"media_gen": MG.status(cfg)}
        if action == "catalog":
            return {"media_gen": MG.catalog(cfg)}
        if action == "generate":
            provider = str(args.get("provider", "") or "").strip() or None
            return {"media_gen": MG.generate(
                cfg, kind=str(args.get("kind", "") or ""),
                provider=provider, prompt=str(args.get("prompt", "") or ""),
                options=None, confirmed=True, source="voice")}
    except MG.MediaGenError as exc:
        return {"media_gen": {
            "state": "FAILED", "code": exc.code,
            "detail": _redact(exc.detail)[:300], "owner": "qubit-policy",
            "rollback": "nothing generated; no provider request attempted"}}
    return {"media_gen": {
        "state": "FAILED", "code": MG.BAD_INPUT_CODE,
        "detail": f"unknown media action {action!r}",
        "owner": "qubit-policy",
        "rollback": "nothing generated; no provider request attempted"}}


def _b_local_models(ctx, args):
    """Local-model inventory and brokered write gates (same module CLI uses).

    Reads derive from real Qubit state. Every write verb is an immediate
    typed gate/refusal: this surface never downloads, starts, ejects,
    activates or writes anything."""
    from . import local_models as LM

    cfg = ctx.cfg
    action = str(args.get("action", "status") or "status")
    confirmed = bool(args.get("confirmed", False))
    try:
        if action == "status":
            return {"local_models": LM.status(cfg)}
        if action == "hardware":
            return {"local_models": LM.hardware(cfg)}
        if action == "catalog":
            return {"local_models": LM.catalog(cfg)}
        if action == "jobs":
            return {"local_models": LM.jobs(cfg)}
        if action == "runtime_install":
            return {"local_models": LM.runtime_install(
                cfg, backend=(str(args.get("backend", "") or "") or None),
                requested_by="voice:user", confirmed=confirmed)}
        if action == "download":
            return {"local_models": LM.download(
                cfg, model=str(args.get("model", "") or ""),
                requested_by="voice:user", confirmed=confirmed)}
        if action == "sideload":
            return {"local_models": LM.sideload(
                cfg, path=str(args.get("path", "") or ""),
                requested_by="voice:user", confirmed=confirmed)}
        if action == "server":
            return {"local_models": LM.server(
                cfg, str(args.get("server_action", "start") or "start"),
                requested_by="voice:user", confirmed=confirmed)}
        if action == "eject":
            return {"local_models": LM.eject(
                cfg, model=str(args.get("model", "") or ""),
                requested_by="voice:user", confirmed=confirmed)}
        if action == "activate":
            return {"local_models": LM.activate(
                cfg, model=str(args.get("model", "") or ""),
                requested_by="voice:user", confirmed=confirmed)}
        if action == "quickstart":
            model = str(args.get("model", "") or "").strip() or None
            return {"local_models": LM.quickstart(
                cfg, model=model, requested_by="voice:user",
                confirmed=confirmed)}
        if action == "fallback":
            return {"local_models": LM.fallback_chain(cfg)}
        if action == "resolve_fallback":
            return {"local_models": LM.resolve_fallback(
                cfg, str(args.get("model", "") or ""))}
    except Exception as exc:  # typed gate only; never raise out
        return {"local_models": LM.public_error(
            getattr(exc, "code", "LOCAL_MODEL_BAD_INPUT"),
            str(exc))}
    return {"local_models": LM.public_error(
        "LOCAL_MODEL_BAD_INPUT", f"unknown local models action {action!r}")}


def channels_doctor(cfg: Any) -> Dict[str, Any]:
    """Compose the inbound-channel health report from the shared backend.

    ``channels_inbound`` exposes ``status``/``catalog``/``accounts`` but no
    dedicated ``doctor``; this derives the same typed view the sibling
    plugin/ACP doctors provide (store health, listener state, latch) using
    only the module's public functions.
    """
    from . import channels_inbound as CI

    st = CI.status(cfg)
    cat = CI.catalog(cfg)
    acct = CI.accounts(cfg)
    return {"state": "OK", "schema": CI.SCHEMA, "store_ok": True,
            "channels": st.get("channels", 0),
            "configured": st.get("configured", 0), "connected": 0,
            "listener": st.get("listener", "NONE"),
            "accounts": len(acct.get("accounts", [])),
            "available": cat.get("available", []),
            "estop": st.get("estop", False), "warnings": []}


def _b_plugin_runtime(ctx, args):
    """In-process plugin host registry (same module CLI/dashboard call).

    Reads derive from the real registry. register/invoke/unregister are
    confirm-tier and exercise the SAME in-process host contract the CLI
    calls; absent a host handler ``invoke`` resolves to the typed
    ``PLUGIN_RUNTIME_UNAVAILABLE`` gate, never a fabricated success.
    """
    import json as _json

    from . import plugin_runtime as PR

    action = str(args.get("action", "status") or "status")
    try:
        if action == "status":
            return {"plugins_runtime": PR.status(ctx.cfg)}
        if action == "list":
            return {"plugins_runtime": PR.list_plugins(ctx.cfg)}
        if action == "show":
            return {"plugins_runtime": PR.get_plugin(
                ctx.cfg, str(args.get("plugin_id", "") or ""))}
        if action == "doctor":
            return {"plugins_runtime": PR.doctor(ctx.cfg)}
        if action == "register":
            manifest_path = Path(
                str(args.get("manifest", "") or "")).expanduser()
            try:
                manifest = _json.loads(
                    manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise PR.PluginRuntimeError(
                    PR.INVALID_MANIFEST_CODE,
                    f"manifest unreadable: {exc}") from exc
            return {"registered": PR.register(
                ctx.cfg, manifest=manifest, confirmed=True,
                requested_by="voice:user")}
        if action == "invoke":
            text = str(args.get("text", "") or "")
            payload = {"text": text} if text else {}
            return {"invoked": PR.invoke(
                ctx.cfg, str(args.get("plugin_id", "") or ""),
                args=payload, confirmed=True, requested_by="voice:user")}
        if action == "unregister":
            return {"unregistered": PR.unregister(
                ctx.cfg, str(args.get("plugin_id", "") or ""),
                confirmed=True, requested_by="voice:user")}
        if action == "policy_rules":
            return {"policy_runtime": PR.policy_rules(ctx.cfg)}
        if action == "policy_doctor":
            from . import release as REL

            return {"policy": REL.workspace_conformance(
                _workspace_arg(ctx, str(args.get("workspace", "") or "")))}
        if action == "policy_attest":
            from . import release as REL

            return {"policy": REL.workspace_attestation(
                _workspace_arg(ctx, str(args.get("workspace", "") or "")))}
        if action == "marketplace":
            return {"marketplace": PR.marketplace_doctor(ctx.cfg)}
        if action == "marketplace_discover":
            return {"marketplace": PR.marketplace_discover(
                ctx.cfg, str(args.get("source", "") or ""),
                query=str(args.get("query", "") or ""))}
    except PR.PluginRuntimeError as exc:
        return {"state": "FAILED", "code": exc.code,
                "detail": exc.detail[:300], "owner": "qubit-policy",
                "rollback": "registry untouched; nothing executed"}
    return {"state": "FAILED", "code": PR.BAD_INPUT_CODE,
            "detail": f"unknown plugin runtime action {action!r}"}


class PluginFleetError(Exception):
    """Typed failure from one vendored plugin-fleet compat route."""

    def __init__(self, http_status: int, code: str, detail: str = "") -> None:
        super().__init__(detail or f"HTTP {http_status}")
        self.http_status = int(http_status)
        self.code = code or f"PLUGIN_FLEET_HTTP_{http_status}"
        self.detail = detail or self.code


def _b_plugin_fleet(ctx, args):
    """Vendored Hermes dashboard-plugin fleet, dispatched in-process.

    Every entry names an exact ``hermes_compat`` route (the same route table
    the dashboard SPA calls on the existing listener — no second server) via
    :data:`PLUGIN_FLEET_OPS`. Reads execute immediately; the plugin's own
    ``rescan``/``reset-state`` handlers keep their e-stop and confirm gates
    (``reset-state`` always supplies ``confirm=true`` only after the shared
    voice journal settled the spoken confirmation). A non-2xx route raises a
    typed :class:`PluginFleetError`, so a refused/estop/engine-unavailable
    route can never be reported as success.
    """
    from . import hermes_compat as HC

    op = str(args.get("op", "") or "")
    spec = PLUGIN_FLEET_OPS.get(op)
    if spec is None:
        raise PluginFleetError(400, "PLUGIN_FLEET_BAD_OP",
                               f"unknown plugin fleet op {op!r}")
    method, path_tmpl, body = spec
    try:
        path = path_tmpl.format(
            **{k: str(v) for k, v in args.items() if v is not None})
    except (KeyError, IndexError) as exc:
        raise PluginFleetError(400, "PLUGIN_FLEET_BAD_ARGS",
                               f"missing plugin fleet argument: {exc}") from exc
    query = {k: str(v) for k, v in (args.get("query") or {}).items()}
    payload_body = dict(body) if body else None
    code, payload = HC.dispatch(ctx.cfg, method, path, query=query,
                                body=payload_body, actor=ctx.actor)
    if int(code) >= 400:
        code_str = detail = ""
        if isinstance(payload, dict):
            code_str = str(payload.get("code") or "")
            detail = str(payload.get("detail") or "")
        raise PluginFleetError(int(code), code_str, detail or path)
    return {"state": "OK", "http_status": int(code), "op": op,
            "data": payload}


def _b_channels(ctx, args):
    """Inbound channel framework (same module CLI/dashboard ingress call).

    Reads derive from the real credential-reference-derived state. The raw
    signed ``receive`` payload cannot be spoken: that action resolves to a
    typed ``VOICE_BYTES_UNSUPPORTED`` refusal and never binds bytes.
    """
    from . import channels_inbound as CI

    action = str(args.get("action", "status") or "status")
    try:
        if action == "status":
            return {"channels": CI.status(ctx.cfg)}
        if action == "catalog":
            return {"channels": CI.catalog(ctx.cfg)}
        if action == "accounts":
            return {"channels": CI.accounts(ctx.cfg)}
        if action == "doctor":
            return {"channels": channels_doctor(ctx.cfg)}
        if action == "configure":
            events = args.get("events")
            if isinstance(events, str):
                events = [e.strip() for e in events.split(",") if e.strip()]
            return {"configured": CI.configure(
                ctx.cfg, str(args.get("channel", "") or ""),
                secret_ref=str(args.get("secret_ref", "") or ""),
                events=events, confirmed=True, requested_by="voice:user")}
        if action == "unconfigure":
            return {"unconfigured": CI.unconfigure(
                ctx.cfg, str(args.get("channel", "") or ""),
                confirmed=True, requested_by="voice:user")}
        if action == "receive":
            return _b_voice_unsupported(ctx, {
                "op": "channels_receive",
                "code": "VOICE_BYTES_UNSUPPORTED",
                "guidance": VOICE_IMPOSSIBLE["channels.receive"],
                "detail": ("I can't take a raw signed channel payload by "
                           "voice: the bytes must arrive over the dashboard "
                           "ingress endpoint or `qubit channels receive` in "
                           "a terminal.")})
    except CI.ChannelError as exc:
        return {"state": "FAILED", "code": exc.code,
                "detail": exc.detail[:300], "owner": "qubit-policy",
                "rollback": "channel registry untouched; nothing dispatched"}
    return {"state": "FAILED", "code": CI.BAD_INPUT_CODE,
            "detail": f"unknown channels action {action!r}"}


def _b_acp_client(ctx, args):
    """ACP external harness client (same module the CLI/dashboard call).

    PATH presence only; Qubit never executes a harness. ``handshake`` shapes
    the real ``initialize`` request or returns the typed
    ``ACP_HARNESS_UNAVAILABLE`` gate; ``send`` always resolves to the typed
    ``ACP_REFUSED`` gate.
    """
    from . import acp_client as AC

    action = str(args.get("action", "status") or "status")
    try:
        if action == "status":
            return {"acp_client": AC.status(ctx.cfg)}
        if action == "catalog":
            return {"acp_client": AC.catalog(ctx.cfg)}
        if action == "receipts":
            return {"acp_client": AC.receipts(
                ctx.cfg, harness=str(args.get("harness", "") or ""))}
        if action == "configure":
            return {"configured": AC.configure(
                ctx.cfg, str(args.get("harness", "") or ""),
                credential_ref=str(args.get("credential_ref", "") or ""),
                confirmed=True, requested_by="voice:user")}
        if action == "unconfigure":
            return {"unconfigured": AC.unconfigure(
                ctx.cfg, str(args.get("harness", "") or ""),
                confirmed=True, requested_by="voice:user")}
        if action == "handshake":
            return {"handshake": AC.handshake(
                ctx.cfg, str(args.get("harness", "") or ""), confirmed=True)}
        if action == "send":
            return {"send": AC.send(
                ctx.cfg, str(args.get("harness", "") or ""),
                prompt=str(args.get("prompt", "") or ""), confirmed=True)}
    except AC.AcpError as exc:
        return {"state": "FAILED", "code": exc.code,
                "detail": exc.detail[:300], "owner": "qubit-policy",
                "rollback": "no harness configured or executed"}
    return {"state": "FAILED", "code": AC.BAD_INPUT_CODE,
            "detail": f"unknown ACP client action {action!r}"}


def _ft_default_node(cfg: Any) -> str:
    """First local (loopback) enrolled node id, else the first node, else "".

    Voice never names a raw endpoint: file transfer only ever targets an
    enrolled node, and Qubit only reads a confined node on the local host.
    """
    from . import file_transfer as FT
    from . import node_host as NH

    try:
        recs = NH._load(cfg).get("nodes", {})
    except Exception:
        return ""
    for node_id, rec in recs.items():
        if isinstance(rec, dict) and FT._is_local(rec):
            return str(node_id)
    return str(next(iter(recs), "") or "")


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _b_file_transfer(ctx, args):
    """Node-host file transfer (same module the CLI/dashboard use).

    Reads (status/list/resume) execute directly. ``fetch`` and ``stream_plan``
    are confirm-tier (the registry gate already ran, so ``confirmed=True``):
    fetch returns a bounded chunk; stream_plan persists a digests-only plan.
    Raw file bytes are never spoken (the guard entry documents that); the
    summary reports size/digest only. Typed failures return redacted dicts.
    """
    from . import file_transfer as FT

    cfg = ctx.cfg
    action = str(args.get("action", "status") or "status")
    node_id = str(args.get("node_id", "") or "") or _ft_default_node(cfg)
    try:
        if action == "status":
            return {"file_transfer": FT.status(cfg)}
        if action == "list":
            return {"file_transfer": FT.list_directory(
                cfg, node_id, str(args.get("path", "") or ""),
                limit=_as_int(args.get("limit"), FT.MAX_LIST_ENTRIES))}
        if action == "fetch":
            return {"file_transfer": FT.fetch(
                cfg, node_id, str(args.get("path", "") or ""),
                offset=_as_int(args.get("offset"), 0),
                max_bytes=_as_int(args.get("max_bytes"),
                                  FT.DEFAULT_FETCH_BYTES),
                confirmed=True)}
        if action == "plan":
            return {"file_transfer": FT.stream_plan(
                cfg, node_id, str(args.get("path", "") or ""),
                chunk_size=_as_int(args.get("chunk_size"),
                                   FT.DEFAULT_CHUNK_BYTES))}
        if action == "resume":
            return {"file_transfer": FT.resume(
                cfg, node_id, str(args.get("transfer_id", "") or ""),
                offset=_as_int(args.get("offset"), 0))}
    except FT.FileTransferError as exc:
        return {"file_transfer": {
            "state": "FAILED", "code": exc.code,
            "detail": _redact(exc.detail)[:300], "owner": "qubit-policy",
            "rollback": "nothing was read or transferred"}}
    return {"file_transfer": {
        "state": "FAILED", "code": FT.FILE_TRANSFER_BAD_INPUT,
        "detail": f"unknown file transfer action {action!r}",
        "owner": "qubit-policy",
        "rollback": "nothing was read or transferred"}}


def _as_optional_float(value: Any):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_optional_int(value: Any):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_optional_bool(value: Any):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in (
        "on", "true", "yes", "enable", "enabled", "session", "scoped")


def _b_active_memory(ctx, args):
    """Active-recall memory layer (same module the CLI/dashboard use).

    Reads (status/policy_get/recall/resolve) execute directly. ``policy_set``
    and ``escalate`` are confirm-tier (the registry gate already ran, so
    ``confirmed=True``). Typed gates (disabled/estop/corrupt/empty) pass
    through as redacted dicts; the module never raises out for these paths.
    """
    from . import active_memory as AM

    cfg = ctx.cfg
    action = str(args.get("action", "status") or "status")
    if action == "status":
        return {"active_memory": AM.status(cfg)}
    if action == "policy_get":
        return {"active_memory": AM.policy_get(cfg)}
    if action == "policy_set":
        return {"active_memory": AM.policy_set(
            cfg, enabled=_as_optional_bool(args.get("enabled")),
            min_score=_as_optional_float(args.get("min_score")),
            max_items=_as_optional_int(args.get("max_items")),
            session_scoped=_as_optional_bool(args.get("session_scoped")),
            confirmed=True)}
    if action == "recall":
        return {"active_memory": AM.recall(
            cfg, str(args.get("query", "") or ""),
            session=str(args.get("session", "") or ""),
            limit=_as_optional_int(args.get("limit")))}
    if action == "resolve":
        return {"active_memory": AM.resolve(
            cfg, str(args.get("query", "") or ""),
            session=str(args.get("session", "") or ""))}
    if action == "escalate":
        return {"active_memory": AM.escalate(
            cfg, _as_int(args.get("memory_id"), 0),
            reason=str(args.get("reason", "") or ""), confirmed=True)}
    return {"active_memory": {
        "state": "FAILED", "code": AM.BAD_INPUT_CODE,
        "detail": f"unknown active memory action {action!r}",
        "results": [], "blocks": [], "refs": []}}


def _b_doc_extract(ctx, args):
    """Bounded document extraction (same module the CLI/dashboard use).

    ``status``/``kinds`` are reads; ``extract`` is confirm-tier (the registry
    gate already ran, so ``confirmed=True``). Typed ``DocExtractError`` codes
    are returned as redacted dicts; extraction never fabricates text.
    """
    from . import doc_extract as DE

    cfg = ctx.cfg
    action = str(args.get("action", "status") or "status")
    if action == "status":
        return {"documents": DE.status(cfg)}
    if action == "kinds":
        return {"documents": DE.kinds(cfg)}
    if action == "extract":
        try:
            return {"documents": DE.extract(
                cfg, str(args.get("path", "") or ""),
                max_chars=_as_int(args.get("max_chars"),
                                  DE.DEFAULT_MAX_CHARS),
                page=_as_optional_int(args.get("page")), confirmed=True)}
        except DE.DocExtractError as exc:
            return {"documents": {
                "state": "FAILED", "code": exc.code,
                "detail": _redact(exc.detail)[:300], "owner": "qubit-policy",
                "rollback": "nothing was extracted"}}
    return {"documents": {
        "state": "FAILED", "code": DE.BAD_INPUT_CODE,
        "detail": f"unknown documents action {action!r}",
        "owner": "qubit-policy", "rollback": "nothing was extracted"}}


def _b_question_prompt(ctx, args):
    """Structured question prompts (same module the CLI/dashboard use).

    ``status``/``pending`` are reads; ``ask`` is confirm-tier (the registry
    gate already ran, so ``confirmed=True``) and builds a single free-text
    question from the spoken text. Structured answer maps (nested JSON) cannot
    be spoken: that op is a typed voice guard, never a bound payload.
    """
    from . import question_prompt as QP

    cfg = ctx.cfg
    action = str(args.get("action", "status") or "status")
    try:
        if action == "status":
            return {"questions": QP.status(cfg)}
        if action == "pending":
            return {"questions": QP.pending(cfg)}
        if action == "render":
            return {"questions": {
                "state": "OK", "schema": "qubit-question-render/1",
                "text": QP.render(args.get("contract") or {})}}
        if action == "ask":
            text = str(args.get("question", "") or "").strip()
            return {"questions": QP.ask(
                cfg, [{"text": text, "kind": "text"}],
                session=str(args.get("session", "") or ""), confirmed=True)}
    except QP.QuestionError as exc:
        return {"questions": {
            "state": "FAILED", "code": exc.code,
            "detail": _redact(exc.detail)[:300], "owner": "qubit-policy",
            "rollback": "prompt store untouched"}}
    return {"questions": {
        "state": "FAILED", "code": QP.BAD_INPUT_CODE,
        "detail": f"unknown question action {action!r}",
        "owner": "qubit-policy", "rollback": "prompt store untouched"}}


def _b_session_share(ctx, args):
    """Qubit-safe session sharing (same module the CLI/dashboard use).

    Reads (status/list/resolve) execute directly; ``publish``/``revoke``/
    ``purge`` are confirm-tier (the registry gate already ran, so
    ``confirmed=True``). Tokens are never rendered in list/status; a spoken
    share-token resolve is impossible (guard entry documents that).
    """
    from . import session_share as SS

    cfg = ctx.cfg
    action = str(args.get("action", "status") or "status")
    if action == "status":
        return {"session_share": SS.status(cfg)}
    if action == "list":
        return {"session_share": SS.list_shares(cfg)}
    if action == "publish":
        return {"session_share": SS.publish(
            cfg, str(args.get("session_id", "") or ""),
            ttl_s=_as_optional_float(args.get("ttl_s")) or SS.DEFAULT_TTL_S,
            confirmed=True)}
    if action == "revoke":
        return {"session_share": SS.revoke(
            cfg, str(args.get("share_id", "") or ""), confirmed=True)}
    if action == "resolve":
        return {"session_share": SS.resolve(
            cfg, str(args.get("share_id", "") or ""),
            str(args.get("token", "") or ""))}
    if action == "purge":
        return {"session_share": SS.purge_expired(cfg)}
    return {"session_share": {
        "state": "FAILED", "code": SS.BAD_INPUT_CODE,
        "detail": f"unknown session share action {action!r}",
        "owner": "qubit-policy", "rollback": "share store untouched"}}


# ------------------------------------- integration wave (Agents 5-10)
def _b_browser_profiles(ctx, args):
    """Qubit-managed browser profile lifecycle (same ``browser`` module the
    CLI uses). Reads list only Qubit-dedicated profiles; create/open/close/
    remove run after the voice confirmation and never alias or touch a
    personal/user profile (an unmanaged name is the typed refusal)."""
    from . import browser as BW

    action = str(args.get("action", "list") or "list")
    profile = str(args.get("profile", "") or "")
    if action == "list":
        return {"browser_profiles": BW.profiles(ctx.cfg)}
    if action == "create":
        return {"browser_profile": BW.create_profile(
            ctx.cfg, profile, requested_by="voice:user", confirmed=True)}
    if action == "open":
        return {"browser_profile": BW.open_profile(
            ctx.cfg, profile, requested_by="voice:user", confirmed=True)}
    if action == "close":
        return {"browser_profile": BW.close_profile(
            ctx.cfg, profile, requested_by="voice:user", confirmed=True)}
    if action == "remove":
        return {"browser_profile": BW.remove_profile(
            ctx.cfg, profile, requested_by="voice:user", confirmed=True)}
    raise ValueError(f"unknown browser profiles action {action!r}")


def _b_computer_screen(ctx, args):
    """Control-UI pane arrangement (same ``computer_use`` module the CLI
    uses). Reads are free; arranging persists only the Qubit Control UI
    layout under the voice confirmation — never a window manager or a real
    display read."""
    from . import computer_use as CU

    action = str(args.get("action", "status") or "status")
    if action == "panes":
        return {"screen": CU.screen_panes()}
    if action == "arrange":
        raw = str(args.get("panes", "") or "")
        panes = [p for p in re.split(r"[\s,]+", raw) if p]
        layout = str(args.get("layout", "") or "").strip().lower()
        if panes and panes[-1].lower() in ("columns", "rows", "grid"):
            layout = panes.pop().lower()
        return {"screen": CU.screen(
            ctx.cfg, panes, layout=layout or "columns",
            requested_by="voice:user", confirmed=True)}
    return {"screen": CU.screen_status(ctx.cfg)}


def _b_egress(ctx, args):
    """Deny-by-default egress policy status / per-host decision (the same
    ``proxy_status`` backend the CLI ``egress status|check`` calls). Both
    are read-only local evaluations; no traffic is sent."""
    from . import proxy_status as PX

    action = str(args.get("action", "status") or "status")
    if action == "check":
        return {"egress": PX.check_egress(
            ctx.cfg, str(args.get("host", "") or ""))}
    if action == "substitute":
        import hashlib as _hashlib

        from .proxy_secrets import SecretRegistry

        value = PX.substitute_egress(
            ctx.cfg, str(args.get("request", "") or ""),
            str(args.get("host", "") or ""), registry=SecretRegistry())
        return {"egress": {
            "state": "OK", "host": str(args.get("host", "") or ""),
            "substituted": True,
            "request_sha256": _hashlib.sha256(value.encode()).hexdigest(),
            "detail": "secret materialized in memory only; the value is "
                      "never spoken, receipted, or logged",
            "authority": "deny-by-default egress policy; allowlisted host "
                         "and registered reference only"}}
    return {"egress": PX.egress_status(ctx.cfg)}


def _b_telemetry(ctx, args):
    """Opt-in telemetry gate status (the exact ``voicebridge.cli`` reader).
    Read-only: disabled unless explicitly enabled; no transport is created
    and no data leaves the machine in the default state."""
    from . import cli as CLI

    return {"telemetry": CLI._it_exp_telemetry(ctx.cfg, SimpleNamespace())}


def _workspace_arg(ctx, explicit):
    """Resolve a workspace to an absolute path: explicit, else the active
    project's first folder, else the current directory (same default the
    CLI uses). The policy backend requires an absolute existing dir."""
    import os as _os

    text = str(explicit or "").strip()
    if not text:
        try:
            from . import projects as PJ

            active = PJ.active_project(ctx.cfg)
            if active.get("state") == "OK" and active.get("project"):
                folders = (active["project"] or {}).get("folders", []) or []
                if folders:
                    text = str(folders[0])
        except Exception:
            text = ""
    if not text:
        text = _os.getcwd()
    return _os.path.abspath(_os.path.expanduser(text))


def _b_workshop(ctx, args):
    """Verified-skill workshop collection (same workshop the CLI uses).
    Reads are free; backup/restore/rollback run under the voice
    confirmation with pre-restore protection. Structured authoring
    proposals stay CLI/dashboard-only (typed refusal; nothing bound)."""
    from .verified_skills.workshop import verified_skill_workshop

    ws = verified_skill_workshop(ctx.cfg)
    action = str(args.get("action", "collection") or "collection")
    try:
        if action == "contracts":
            return {"workshop": ws.contracts()}
        if action == "collection":
            return {"workshop": ws.collection()}
        if action == "backups":
            return {"workshop": ws.backups()}
        if action == "curator":
            return {"workshop": ws.curator_status()}
        if action == "backup":
            return {"workshop": ws.backup(
                label=str(args.get("label", "") or ""))}
        if action == "restore":
            return {"workshop": ws.restore(
                str(args.get("backup_id", "") or ""),
                requested_by="user")}
        if action == "rollback":
            return {"workshop": ws.rollback(
                str(args.get("backup_id", "") or ""),
                requested_by="user")}
        if action == "author":
            return {"workshop": {
                "state": "refused", "code": "VOICE_NESTED_UNSUPPORTED",
                "detail": "structured skill proposals cannot be spoken; use "
                          "`qubit verified-skills submit` or the dashboard"}}
    except Exception as exc:
        return {"workshop": {
            "state": "FAILED",
            "code": getattr(exc, "code", type(exc).__name__),
            "detail": _redact(str(exc))[:200]}}
    raise ValueError(f"unknown workshop action {action!r}")


def _b_usage(ctx, args):
    """Token/cost usage ledger reader (the exact ``voicebridge.cli``
    evaluation reader the CLI ``usage`` family calls). Local, redacted;
    no secrets, no network."""
    from . import cli as CLI

    try:
        limit = max(1, min(int(args.get("limit", 1000) or 1000), 5000))
    except (TypeError, ValueError):
        limit = 1000
    return {"usage": CLI._it_exp_usage(ctx.cfg,
                                       SimpleNamespace(limit=limit))}


def _b_receipts_ledger(ctx, args):
    """Execution-identity receipt ledger reader + hash-chain verification
    (the exact ``voicebridge.cli`` receipts reader). An absent ledger is
    the typed ``RECEIPTS_NOT_FOUND`` state, never a fabricated chain."""
    from . import cli as CLI

    return {"receipts": CLI._it_exp_receipts(ctx.cfg, SimpleNamespace())}


def _b_logbook(ctx, args):
    """Append-only work-journal reader (the exact ``voicebridge.cli``
    logbook reader). Reads only; capture stays the typed no-screen-access
    refusal, and note creation stays CLI-only."""
    from . import cli as CLI

    action = str(args.get("action", "entries") or "entries")
    if action == "capture":
        from .evaluation import logbook as LB

        return {"logbook": LB.capture()}
    out = CLI._it_exp_logbook(ctx.cfg,
                              SimpleNamespace(limit=100, window_s=None))
    if action == "summary":
        return {"logbook": {"state": out.get("state", "OK"),
                            "summary": out.get("summary", {})}}
    return {"logbook": out}


_BACKENDS: Dict[str, Callable] = {
    "status": _b_status,
    "pause": _b_pause,
    "resume": _b_resume,
    "autonomy_status": _b_autonomy_status,
    "autonomy_list": _b_autonomy_list,
    "autonomy_show": _b_autonomy_show,
    "autonomy_pause": _b_autonomy_pause,
    "autonomy_resume": _b_autonomy_resume,
    "autonomy_revoke": _b_autonomy_revoke,
    "autonomy_expire": _b_autonomy_expire,
    "doctor": _b_doctor,
    "setup_check": _b_setup_check,
    "verify_release": _b_verify_release,
    "security": _b_security,
    "diagnostics": _b_diagnostics,
    "backup": _b_backup,
    "voice_unsupported": _b_voice_unsupported,
    "sessions": _b_sessions,
    "capability": _b_capability,
    "checkpoints": _b_checkpoints,
    "auth_status": _b_auth_status,
    "auth_login": _b_auth_login,
    "auth_verify": _b_auth_verify,
    "auth_logout": _b_auth_logout,
    "mcp": _b_mcp,
    "worker": _b_worker,
    "memory": _b_memory,
    "skills": _b_skills,
    "projects": _b_projects,
    "kanban": _b_kanban,
    "themes": _b_themes,
    "proxy": _b_proxy,
    "runtime": _b_runtime,
    "import_agent": _b_import_agent,
    "hooks": _b_hooks,
    "sync": _b_sync,
    "vision_status": _b_vision_status,
    "vision_describe": _b_vision_describe,
    "pairing": _b_pairing,
    "webhook": _b_webhook,
    "msg": _b_msg,
    "gateway": _b_gateway,
    "peer": _b_peer,
    "brain": _b_brain,
    "permissions": _b_permissions,
    "monitor": _b_monitor,
    "frontier": _b_frontier,
    "audio": _b_audio,
    "device": _b_device,
    "flags": _b_flags,
    "logs": _b_logs,
    "insights": _b_insights,
    "prompt_budget": _b_prompt_budget,
    "reasoning": _b_reasoning,
    "approvals": _b_approvals,
    "tools": _b_tools,
    "secrets": _b_secrets,
    "worktree": _b_worktree,
    "completions": _b_completions,
    "tui_guidance": _b_tui_guidance,
    "console_guidance": _b_console_guidance,
    "mic_guidance": _b_mic_guidance,
    "curator": _b_curator,
    "update_status": _b_update_status,
    "config_profiles": _b_config_profiles,
    "model_profile_status": _b_model_profile_status,
    "model_profile_test": _b_model_profile_test,
    "speech_profile_test": _b_speech_profile_test,
    "model_profile_local": _b_model_profile_local,
    "model_profile_use": _b_model_profile_use,
    "model_profile_realtime": _b_model_profile_realtime,
    "model_profile_coding": _b_model_profile_coding,
    "model_profile_local_only": _b_model_profile_local_only,
    "migrate_status": _b_migrate_status,
    "acp": _b_acp,
    "lsp": _b_lsp,
    "browser": _b_browser,
    "browser_tabs": _b_tabs,
    "computer": _b_computer,
    "plugins_install": _b_plugins_install,
    "plugins_remote": _b_plugins_install,
    "lessons": _b_lessons,
    "power_status": _b_power_status,
    "websites": _b_websites,
    "task_run": _b_task_run,
    "schedules": _b_schedules,
    "files": _b_files,
    "ide": _b_ide,
    "git": _b_git,
    "actions": _b_actions,
    "ghost": _b_ghost,
    "opencode": _b_opencode,
    "verified": _b_verified,
    "experiments": _b_experiments,
    "product": _b_product,
    "package": _b_package,
    "webui": _b_webui,
    "reports": _b_reports,
    "frontier_chat": _b_frontier_chat,
    "frontier_show": _b_frontier_show,
    "commands": _b_commands,
    "audio_hw": _b_audio_hw,
    "loop": _b_loop,
    "wake": _b_wake,
    "device_enroll": _b_device_enroll,
    "branding": _b_branding,
    "node_host": _b_node_host,
    "task_ledger": _b_task_ledger,
    "media_gen": _b_media_gen,
    "local_models": _b_local_models,
    "plugin_runtime": _b_plugin_runtime,
    "channels": _b_channels,
    "plugin_fleet": _b_plugin_fleet,
    "acp_client": _b_acp_client,
    "file_transfer": _b_file_transfer,
    "active_memory": _b_active_memory,
    "doc_extract": _b_doc_extract,
    "question_prompt": _b_question_prompt,
    "session_share": _b_session_share,
    "browser_profiles": _b_browser_profiles,
    "computer_screen": _b_computer_screen,
    "egress": _b_egress,
    "workshop": _b_workshop,
    "usage": _b_usage,
    "receipts_ledger": _b_receipts_ledger,
    "logbook": _b_logbook,
    "telemetry": _b_telemetry,
    "automation": _b_automation,
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
    agent = inv.get("agent", {})
    broker = inv.get("broker", {})
    estop = "emergency stop latched" if broker.get("emergency_stop") else "all clear"
    return (f"Qubit ready. Agent runtime {agent.get('runtime', 'qubit-native')}, "
            f"{estop}.")


def _sum_sessions(result):
    one = result.get("session")
    if isinstance(one, dict):
        state = str(one.get("state", ""))
        if state != "OK":
            return f"Session operation failed: {one.get('code', state)}."
        row = one.get("session", {}) if isinstance(one.get("session"), dict) \
            else {}
        sid = str(row.get("receipt_id", row.get("id", "")) or "")
        return f"Session updated: {sid[:12]}."
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


def _sum_pairing_create(result):
    code = str(result.get("code", ""))
    grouped = " ".join(code[i:i + 4] for i in range(0, len(code), 4))
    return f"Your pairing code is {grouped}. It expires in ten minutes."


def _sum_kanban_stores(result):
    if not isinstance(result, dict):
        return "Kanban store status unavailable."
    native = result.get("native", {})
    vendored = result.get("vendored", {})
    return (f"Qubit kanban store is authoritative with "
            f"{native.get('task_count', 0)} tasks; the legacy kanban store "
            f"has {vendored.get('task_count', 0)}.")


def _sum_kanban_migrate(result):
    if not isinstance(result, dict):
        return "Kanban migration did not return a result."
    if result.get("dry_run"):
        return f"Dry run: {result.get('imported', 0)} tasks would migrate."
    return (f"Migrated {result.get('imported', 0)} tasks into the Qubit "
            f"kanban store; skipped {result.get('skipped', 0)}.")


def _sum_kanban_task(result):
    if isinstance(result, dict) and result.get("state") == "EMPTY":
        return ("No open tasks. Create one first, for example "
                "say create task followed by a title.")
    task = result.get("task", {}) if isinstance(result, dict) else {}
    if not task:
        return "No task found."
    return f"Task {task.get('id', '?')}: {task.get('title', '')}, {task.get('status', '')}."


def _sum_project(result):
    if isinstance(result, dict) and result.get("state") == "EMPTY":
        return "No active project. Switch to one first."
    proj = result.get("project", {}) if isinstance(result, dict) else {}
    if not proj:
        return "No project found."
    return f"Project {proj.get('name', proj.get('slug', '?'))} ready."


def _sum_permissions(result):
    mode = str(result.get("mode", "restricted"))
    scopes = result.get("scopes", [])
    scope_text = ", ".join(scopes[:3]) if scopes else "no scopes"
    return f"Permission mode {mode}, {scope_text}."


def _sum_guidance(result):
    guidance = str(result.get("guidance", "") or "")
    return guidance if guidance else "See the help text."


def _sum_audio_hw(result):
    """Spoken summary of a real bounded audio-hardware probe (Qubit naming)."""
    body = result.get("audio_hw", {}) if isinstance(result, dict) else {}
    state = str(body.get("state", "?") or "?")
    code = str(body.get("code", "") or "")
    if code == "LIVE_NOT_AUTHORIZED":
        return ("qubit audio hardware is operator-gated: run the matching "
                "`qubit` audio command from a terminal with "
                "VOICE_BRIDGE_QUALIFY=1.")
    if code == "ASR_TEST_NEEDS_WAV":
        return ("qubit transcription test needs a WAV file: run "
                "`qubit asr-test <file.wav>` from a terminal.")
    if code == "VOICE_ID_MODEL_MISSING":
        return ("qubit voice-ID model is not installed: run `qubit enroll` "
                "from a terminal.")
    if code == "VOICE_ID_NO_ENROLLMENT":
        return ("qubit voice-ID model is ready but no voice is enrolled: say "
                "`qubit enroll my voice`.")
    if code == "VOICE_ID_ENROLLED":
        return (f"qubit voice-ID ready with "
                f"{int(body.get('enrolled_speakers', 0) or 0)} enrolled voice(s).")
    if state == "HEALTHY":
        return "qubit microphone check: healthy signal chain."
    if state == "SILENT":
        return "qubit microphone check: silent (check phantom/gain)."
    if state == "DEGRADED":
        return "qubit microphone check: degraded level (raise the input gain)."
    if state == "UNAVAILABLE":
        return "qubit audio device unavailable (check the input/output)."
    if "audible" in body:
        return ("qubit speech check: audible." if body.get("audible")
                else "qubit speech check: no audible output.")
    if "qualify_version" in body:
        return f"qubit voice self-test: {state}."
    return f"qubit audio hardware: {state}{(' ' + code) if code else ''}."


def _sum_capabilities(result):
    caps = result.get("capabilities", {}) if isinstance(result, dict) else {}
    total = caps.get("total", 0)
    implemented = caps.get("implemented", 0)
    if not total:
        return "No capabilities recorded."
    return f"{implemented} of {total} capabilities available."


def _sum_task(result):
    task = result.get("task", {}) if isinstance(result, dict) else {}
    if task.get("state") != "OK":
        code = str(task.get("code", "UNAVAILABLE"))
        return f"Task not run: {code}."
    return str(task.get("reply", "") or "Task ran.")[:1500]


def _sum_dashboard(result):
    dash = result.get("dashboard", {}) if isinstance(result, dict) else {}
    url = str(dash.get("url", "") or "")
    if dash.get("state") == "READY" and url:
        return f"Dashboard ready at {url}."
    return ("Dashboard is not running. Start it with qubit webui, "
            "then open the dashboard URL it prints.")


def _sum_reports(result):
    rep = result.get("reports", {}) if isinstance(result, dict) else {}
    total = int(rep.get("total", 0) or 0)
    if not total:
        return "No reports yet."
    items = rep.get("reports", []) or []
    newest = items[0].get("report_id", "") if items else ""
    if newest:
        return f"{total} reports, newest {newest}."
    return f"{total} reports."


def _sum_schedules(result):
    body = result if isinstance(result, dict) else {}
    if "scheduled_run" in body:
        run = body["scheduled_run"] or {}
        return f"Ran {int(run.get('ran', 0) or 0)} scheduled jobs."
    if "schedule" in body:
        job = (body["schedule"] or {}).get("job", {})
        if job:
            return f"Scheduled {job.get('name', '?')}."
        return "Schedule updated."
    scheds = (body.get("schedules", {}) or {})
    jobs = scheds.get("jobs", []) if isinstance(scheds, dict) else []
    active = sum(1 for j in jobs if j.get("enabled"))
    if not jobs:
        return "No scheduled jobs."
    return f"{len(jobs)} scheduled jobs, {active} active."


def _sum_files(result):
    body = result if isinstance(result, dict) else {}
    if body.get("state") == "EMPTY":
        return "No active project. Switch to one first."
    for key in ("folder", "file", "inspected"):
        inner = body.get(key, {})
        if not isinstance(inner, dict) or not inner:
            continue
        if inner.get("state") == "EMPTY":
            return "No active project. Switch to one first."
        if inner.get("state") != "OK":
            return f"That failed: {inner.get('code', 'FAILED')}."
        if "entries" in inner:
            extra = ", truncated" if inner.get("truncated") else ""
            return f"{len(inner['entries'])} entries{extra}."
        if "text" in inner:
            extra = ", clipped" if inner.get("clipped") else ""
            return f"File read{extra}."
        return "Done."
    return "Done."


def _sum_ide(result):
    body = (result.get("ide", {}) or {}) if isinstance(result, dict) else {}
    state = str(body.get("state", "OK"))
    if state == "UNAVAILABLE":
        return "No graphical session. Open the folder in the editor yourself."
    if state != "OK":
        return f"That failed: {body.get('code', state)}."
    if "pid" in body:
        return f"Opened {body.get('editor', 'editor')}."
    editors = body.get("editors", []) or []
    if not editors:
        return "No supported editor installed."
    return f"Editors: {', '.join(e.get('editor', '?') for e in editors[:4])}."


def _sum_wake(result):
    body = (result.get("wake", {}) or {}) if isinstance(result, dict) else {}
    state = str(body.get("state", ""))
    if state == "READY":
        words = body.get("keywords", []) or []
        shown = ", ".join(str(w)[:30] for w in words[:3])
        return f"Wake word ready for {shown}." if shown else "Wake word ready."
    return "Wake word model missing. Fetch the spotter bundle first."


def _sum_tabread(result):
    tab = result.get("tab", {}) if isinstance(result, dict) else {}
    if tab.get("state") != "OK":
        return f"Tab not read: {tab.get('code', 'UNAVAILABLE')}."
    title = str(tab.get("title", "") or "")[:80]
    text = str(tab.get("text", "") or "")[:400]
    if title:
        return f"Tab {title}: {text}"
    return text or "Empty page."


def _sum_tabs_action(result):
    for key in ("browser", "tabs", "found", "tab", "doctor"):
        body = result.get(key, {}) if isinstance(result, dict) else {}
        if isinstance(body, dict) and body:
            break
    else:
        return "Done."
    state = str(body.get("state", "OK"))
    if state == "UNAVAILABLE":
        return f"Browser unavailable: {body.get('code', 'BROWSER_DOWN')}."
    if state != "OK":
        return f"That failed: {body.get('code', state)}."
    verb = str(body.get("verb", "") or "")
    if verb:
        return f"Browser {verb}."
    if "count" in body:
        return f"Found {body['count']} matches."
    if "tabs" in body:
        return f"{len(body['tabs'])} tabs open."
    return "Done."


def _sum_board(result):
    if not isinstance(result, dict):
        return "No board found."
    if result.get("state") != "OK":
        return (f"That failed: {result.get('code', result.get('state', '?'))}."
                if result.get("state") else "No board found.")
    counts = result.get("counts", {}) or {}
    total = sum(int(v or 0) for v in counts.values())
    widgets = result.get("widgets", []) or []
    cols = result.get("columns", []) or []
    board = str(result.get("board", "main"))
    bits = [f"Board {board}: {total} tasks"]
    if cols:
        bits.append(f"columns {', '.join(str(c) for c in cols)}")
    bits.append(f"{len(widgets)} widgets")
    if result.get("restricted"):
        bits.append("restricted")
    return ", ".join(bits) + "."


def _sum_opencode(result):
    if not isinstance(result, dict):
        return "No coding result found."
    state = str(result.get("state", ""))
    if state in ("denied", "failed"):
        code = str(result.get("code", state or "failed"))
        detail = str(result.get("detail", ""))[:200]
        return f"Coding refused: {code}. {detail}".strip()
    if "capabilities" in result:
        caps = result.get("capabilities", []) or []
        return f"{len(caps)} OpenCode capabilities."
    if "grant" in result:
        return "OpenCode grant recorded."
    if state == "preview":
        return f"Preview ready: {str(result.get('preview', ''))[:200]}"
    rid = str(result.get("receipt_id", "") or "")
    if rid:
        return f"Coding run {state or 'recorded'}, receipt {rid}."
    return "Coding run recorded."


def _sum_ghost(result):
    if not isinstance(result, dict):
        return "No Ghost result found."
    state = str(result.get("state", ""))
    if state in ("denied", "failed", "DELEGATED", "BLOCKED"):
        code = str(result.get("code", state or "failed"))
        return f"Ghost refused: {code}."
    if "capabilities" in result:
        caps = result.get("capabilities", []) or []
        return f"{len(caps)} Ghost capabilities."
    if "grant" in result:
        grant = result.get("grant", {}) or {}
        return (f"Grant {grant.get('id', '?')} for "
                f"{grant.get('capability', 'capability')}.")
    if "preview" in result:
        aid = str(result.get("action_id", "action"))
        first = str(result.get("preview", "")).splitlines()
        line = first[0][:160] if first else aid
        needs = (" Needs confirmation." if str(result.get(
            "confirmation_required", "")) == "True" else "")
        return f"Preview {aid}: {line}.{needs}"
    if state == "OK":
        rid = str(result.get("receipt_id", "") or "")
        if rid:
            return f"Ghost action done, receipt {rid}."
        return "Ghost action done."
    return "Ghost result recorded."


def _sum_runtime(result):
    if not isinstance(result, dict):
        return "No runtime state found."
    if result.get("state") != "OK":
        return (f"That failed: {result.get('code', result.get('state', '?'))}."
                if result.get("state") else "No runtime state found.")
    if "runtimes" in result:
        parts = [f"{r.get('name', '?')} {r.get('state', '?')}"
                 for r in result.get("runtimes", [])]
        return "Runtimes: " + (", ".join(parts) + "." if parts else "none.")
    if "images" in result:
        names = [r.get("name", "?") for r in result.get("images", [])]
        return "Pinned images: " + (", ".join(names) + "." if names else "none.")
    if "findings" in result:
        return f"Runtime diagnosis: {len(result['findings'])} checks."
    name = str(result.get("image", "runtime"))
    return f"{name}: {result.get('state', 'done')}. {result.get('rollback', '')}".strip()


def _sum_screenshot(result):
    body = result.get("screenshot", {}) if isinstance(result, dict) else {}
    state = str(body.get("state", "?"))
    if state == "UNAVAILABLE":
        return f"Browser unavailable: {body.get('code', 'BROWSER_UNAVAILABLE')}."
    if state != "OK":
        return f"Screenshot refused: {body.get('code', state)}."
    return (f"Screenshot captured: {body.get('bytes', 0)} bytes "
            f"({body.get('format', 'image')}); the image stays on the "
            "dashboard/CLI, only this summary is spoken.")


def _sum_file_patch(result):
    body = result.get("file_patch", {}) if isinstance(result, dict) else {}
    state = str(body.get("state", "?"))
    if state == "OK":
        return (f"Patch {body.get('action', 'applied')}: "
                f"{body.get('target', 'file')}.")
    if state == "NO_CHANGE":
        return "Nothing to roll back."
    return f"File patch refused: {body.get('code', state)}."


def _sum_web(result):
    body = result.get("web", {}) if isinstance(result, dict) else {}
    state = str(body.get("state", "?"))
    if state == "UNAVAILABLE":
        return f"Web tool unavailable: {body.get('code', 'WEB_UNAVAILABLE')}."
    if state != "OK":
        return f"Web request refused: {body.get('code', state)}."
    if "results" in body:
        return f"{len(body.get('results', []))} web results."
    title = str(body.get("title", "") or "")[:120]
    return f"Fetched {title or body.get('url', 'page')}."


def _sum_plugin_fleet(result):
    """Voice summary for the vendored dashboard-plugin fleet reads."""
    if not isinstance(result, dict):
        return "Plugin fleet result recorded."
    if result.get("state") != "OK":
        return f"That failed: {result.get('code', result.get('state', '?'))}."
    data = result.get("data")
    op = str(result.get("op", "") or "")
    if op == "achievements_list" and isinstance(data, dict):
        return (f"Achievements: {data.get('unlocked_count', 0)} of "
                f"{data.get('total_count', 0)} unlocked.")
    if op == "achievements_scan_status" and isinstance(data, dict):
        return (f"Achievement scan {data.get('state', 'idle')}; "
                f"{data.get('run_count', 0)} runs.")
    if op == "achievements_recent":
        count = len(data) if isinstance(data, list) else 0
        return f"{count} recently unlocked achievements."
    if op == "achievements_rescan" and isinstance(data, dict):
        return (f"Achievement rescan complete: "
                f"{data.get('unlocked_count', 0)} unlocked.")
    if op == "achievements_reset":
        return "Achievement progress reset."
    if op == "achievements_session_badges" and isinstance(data, dict):
        return f"{len(data.get('badges', []))} session badges."
    if op == "plugin_fleet_list" and isinstance(data, dict):
        return f"{len(data.get('plugins', []))} vendored dashboard plugins."
    if op == "kanban_fleet_board" and isinstance(data, dict):
        tasks = sum(len(c.get("tasks", [])) for c in data.get("columns", []))
        return f"Vendored board: {tasks} tasks."
    if isinstance(data, dict):
        for key in ("count", "total"):
            if data.get(key) is not None:
                return f"{key} {data[key]}."
    return "Plugin fleet result recorded."


def _sum_model_profile(result):
    """Voice summary for resolver-backed model-profile intents."""
    if not isinstance(result, dict):
        return "Model profile updated."
    mp = result.get("model_profile")
    if not isinstance(mp, dict):
        return "Model profile updated."
    model = mp.get("model")
    if isinstance(model, dict):
        eff = model.get("effective", {}) if isinstance(
            model.get("effective"), dict) else {}
        if model.get("state") != "OK":
            return f"Model status {model.get('code', model.get('state', '?'))}."
        return (f"Active model {eff.get('profile_id', '?')} "
                f"({eff.get('provider', '?')}/{eff.get('model', '?')}).")
    if mp.get("local_only"):
        return "Local-only mode: session overrides cleared."
    if mp.get("gate") is not None and mp.get("effective") is not None:
        state = str(mp.get("state", "OK"))
        return (f"Profile {mp.get('profile', '?')} preflight {state}.")
    if str(mp.get("state", "OK")) in ("FAILED", "UNAVAILABLE", "DENIED"):
        return f"Model profile {mp.get('code', mp.get('state'))}."
    pid = mp.get("profile") or mp.get("profile_id") or "selected"
    return f"Model profile {pid} selected for this session."


_SUMMARIES = {
    "generic": _generic_summary,
    "status": _sum_status,
    "task": _sum_task,
    "sessions": _sum_sessions,
    "msg_send": _sum_msg_send,
    "pairing_create": _sum_pairing_create,
    "kanban_stores": _sum_kanban_stores,
    "kanban_migrate": _sum_kanban_migrate,
    "kanban_task": _sum_kanban_task,
    "board": _sum_board,
    "runtime": _sum_runtime,
    "ghost": _sum_ghost,
    "opencode": _sum_opencode,
    "project": _sum_project,
    "capabilities": _sum_capabilities,
    "dashboard": _sum_dashboard,
    "reports": _sum_reports,
    "tabread": _sum_tabread,
    "tabs_action": _sum_tabs_action,
    "screenshot": _sum_screenshot,
    "file_patch": _sum_file_patch,
    "web": _sum_web,
    "schedules": _sum_schedules,
    "files": _sum_files,
    "ide": _sum_ide,
    "wake": _sum_wake,
    "permissions": _sum_permissions,
    "guidance": _sum_guidance,
    "audio_hw": _sum_audio_hw,
    "plugin_fleet": _sum_plugin_fleet,
    "model_profile": _sum_model_profile,
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
_E("autonomy_status", "Autonomy status",
   ("autonomy status", "who is pre-authorized", "what can run unattended"),
   "autonomy_status", ("autonomy",),
   summary="status", examples=("qubit, autonomy status",),
   help="Effective owner-scoped autonomy policy (read-only).")
_E("autonomy_list", "List autonomy profiles",
   ("list autonomy profiles", "show autonomy profiles", "my autonomy profiles"),
   "autonomy_list", ("autonomy",),
   summary="list", examples=("qubit, list autonomy profiles",),
   help="Owner-scoped autonomy profiles (read-only).")
_E("autonomy_expire", "Expire elapsed autonomy profiles",
   ("expire autonomy profiles", "clean up autonomy profiles"),
   "autonomy_expire", ("autonomy",),
   summary="count", examples=("qubit, expire autonomy profiles",),
   help="Expire elapsed scoped-autonomy profiles (read-only).")
_E("autonomy_show", "Show an autonomy profile",
   ("show autonomy profile", "details of autonomy profile"),
   "autonomy_show", ("autonomy",), args=(_S("profile_id"),),
   summary="show", examples=("qubit, show autonomy profile nightly-uploads",),
   help="One owner-scoped autonomy profile by id (read-only).")
_E("autonomy_pause", "Pause an autonomy profile",
   ("pause autonomy", "suspend autonomy profile", "pause my autonomy"),
   "autonomy_pause", ("autonomy",), args=(_S("profile_id"),),
   confirm=True, examples=("qubit, pause autonomy nightly-uploads",),
   help="Pause one scoped-autonomy profile (confirmed).")
_E("autonomy_resume", "Resume an autonomy profile",
   ("resume autonomy", "unsuspend autonomy profile", "resume my autonomy"),
   "autonomy_resume", ("autonomy",), args=(_S("profile_id"),),
   confirm=True, examples=("qubit, resume autonomy nightly-uploads",),
   help="Resume one scoped-autonomy profile (confirmed).")
_E("autonomy_revoke", "Revoke an autonomy profile",
   ("revoke autonomy", "delete autonomy profile", "cancel my autonomy"),
   "autonomy_revoke", ("autonomy",), args=(_S("profile_id"),),
   confirm=True, examples=("qubit, revoke autonomy nightly-uploads",),
   help="Revoke one scoped-autonomy profile (confirmed).")
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
_E("export_state_voice", "Export state (voice refusal)",
   ("export my state", "export state", "export my data",
    "export everything", "back up everything", "encrypt my state"),
   "backup", ("backup/import",),
   fixed=(("action", "export_refused"),), summary="guidance",
   examples=("qubit, export my state",),
   help="Refused with guidance: secrets are never spoken.")
_E("import_state_voice", "Import state (voice refusal)",
   ("import my state", "import state", "restore my state",
    "restore from backup", "restore my data", "import everything"),
   "backup", ("backup/import",),
   fixed=(("action", "import_refused"),), summary="guidance",
   examples=("qubit, import my state",),
   help="Refused with guidance: secrets are never spoken.")
_E("sessions", "Recent sessions",
   ("list sessions", "recent sessions", "show sessions", "session history",
    "show my sessions", "list my sessions", "my sessions"),
   "sessions", ("sessions", "--resume/--continue"),
   summary="sessions", examples=("qubit, show my sessions",),
   help="Session receipts and journals.")
_E("sessions_search", "Search sessions",
   ("search sessions", "find session", "search my sessions"),
   "sessions", ("sessions",), args=(_R("query"),),
   fixed=(("action", "search"),), summary="sessions",
   examples=("qubit, search sessions invoice",),
   help="Search session names and receipts.")
_E("session_show", "Show a session",
   ("show session", "inspect session"),
   "sessions", ("sessions",), args=(_S("session_id", True),),
   fixed=(("action", "show"),), summary="sessions",
   examples=("qubit, show session 12",),
   help="Inspect one session receipt.")
_E("session_rename", "Rename a session",
   ("rename session", "name session"),
   "sessions", ("sessions",), args=(_S("session_id", True), _S("name", True)),
   fixed=(("action", "rename"),), summary="sessions",
   examples=("qubit, rename session 12 to invoices",),
   help="Set a session display name.")
_E("session_archive", "Archive a session",
   ("archive session",),
   "sessions", ("sessions",), args=(_S("session_id", True),),
   fixed=(("action", "archive"),), confirm=True, summary="sessions",
   examples=("qubit, archive session 12",),
   help="Archive a session (confirmed; resume clears it).")
_E("session_delete", "Delete a session",
   ("delete session", "forget session"),
   "sessions", ("sessions",), args=(_S("session_id", True),),
   fixed=(("action", "delete"),), confirm=True, summary="sessions",
   examples=("qubit, delete session 12",),
   help="Delete a session tombstone (confirmed; recoverable via resume).")
_E("session_resume", "Resume a session",
   ("resume session", "restore session"),
   "sessions", ("sessions",), args=(_S("session_id", True),),
   fixed=(("action", "resume"),), confirm=True, summary="sessions",
   examples=("qubit, resume session 12",),
   help="Restore a deleted or archived session (confirmed).")
_E("capability", "Capability check",
   ("check capability", "rx:is (.+) available", "can you"),
   "capability", ("tools",), args=(_R("name"),),
   examples=("qubit, is whatsapp available",),
   help="Typed state for any capability.")
_E("capabilities_list", "Show available capabilities",
   ("show available capabilities", "list capabilities",
    "available capabilities", "list my capabilities",
    "what capabilities are available", "show capabilities",
    "list all capabilities"),
   "capability", ("tools",),
   fixed=(("action", "list"),), summary="capabilities",
   examples=("qubit, show available capabilities",),
   help="Every capability with availability counts.")
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
_E("worker", "Worker status",
   ("worker status", "agent status", "worker lane", "is the worker ready",
    "claw status"),
   "worker", ("worker", "claw"),
   examples=("qubit, worker status",),
   help="Native worker-lane status.")
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
   ("list projects", "show projects", "what projects", "my projects",
    "list my projects", "show my projects"),
   "projects", ("project",), fixed=(("action", "list"),),
   examples=("qubit, list my projects",),
   help="Named workspaces.")
_E("projects_show", "Show project",
   ("show project", "describe project", "project details"),
   "projects", ("project",), args=(_R("slug"),),
   fixed=(("action", "show"),),
   examples=("qubit, show project",),
   help="One workspace.")
_E("projects_active", "Open the active project",
   ("open my active project", "open my project", "show my active project",
    "what is my active project", "active project", "my active project"),
   "projects", ("project",),
   fixed=(("action", "active"),), summary="project",
   examples=("qubit, open my active project",),
   help="The active workspace, or a typed empty state.")
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
_E("kanban_current", "Inspect the current task",
   ("inspect the current task", "show the current task",
    "what is the current task", "current task", "inspect current task"),
   "kanban", ("kanban",),
   fixed=(("action", "current"),), summary="kanban_task",
   examples=("qubit, inspect the current task",),
   help="Newest actionable task, or a typed empty state.")
_E("kanban_create", "Create task",
   ("create task", "new task", "add task"),
   "kanban", ("kanban",), args=(_R("title"), _S("board")),
   fixed=(("action", "create"),), confirm=True, summary="kanban_task",
   examples=("qubit, create task buy milk",),
   help="Create a task (confirmed; board defaults to main).")
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
   ("change theme", "set theme", "switch theme"),
   "themes", ("pets/skin",),
   args=(_C("name", ("qubit-dark", "qubit-light", "qubit-contrast", "reset",
                     "dark", "light", "contrast")),),
   fixed=(("action", "set"),), confirm=True,
   examples=("qubit, set theme light",),
   help="Switch preset (confirmed; light/dark/contrast/reset).")
_E("proxy", "Proxy status",
   ("proxy status", "forwarding status"),
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
_E("import_refusal", "Import foreign setup",
   ("import foreign setup", "import agent setup", "migrate foreign agent"),
   "import_agent", ("import-agent",), fixed=(("action", "import"),),
   examples=("qubit, import foreign setup",),
   help="Typed refusal: review first, then skills install.")
_E("hooks_list", "List hooks",
   ("list hooks", "show hooks", "event subscriptions"),
   "hooks", ("hooks",), fixed=(("action", "list"),),
   examples=("qubit, list hooks",),
   help="Declared subscriptions (never execute).")
_E("hooks_show", "Show hook",
   ("show hook", "describe hook"),
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
_E("sync_revoke_voice", "Revoke sync offer",
   ("revoke sync offer", "cancel sync offer", "withdraw sync offer"),
   "sync", ("sync",), args=(_R("offer_id"),),
   fixed=(("action", "revoke"),), confirm=True,
   examples=("qubit, revoke sync offer",),
   help="Tombstone a sync offer and delete its bundle (confirmed).")
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
   ("list webhooks", "webhook status"),
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
    "message status"),
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
   ("send message", "send draft", "send it", "send this message",
    "send this draft"),
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
   ("list gateways", "gateway status"),
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
   ("open dashboard", "show dashboard", "dashboard help"),
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
   ("browser status", "is the browser ready", "chromium status",
    "check the browser", "check browser"),
   "browser", ("browser",),
   examples=("qubit, check the browser",),
   help="Loopback CDP state (profiles untouched).")
_E("browser_doctor", "Browser health",
   ("browser doctor", "check browser health", "browser bridge health"),
   "browser_tabs", ("browser",),
   fixed=(("action", "doctor"),), summary="tabs_action",
   examples=("qubit, browser doctor",),
   help="Endpoint, tabs, ledger, launch config (read-only).")
_E("browser_tabs", "List open tabs",
   ("list open tabs", "show my tabs", "what tabs are open",
    "show open tabs", "list tabs"),
   "browser_tabs", ("browser",),
   fixed=(("action", "tabs"),), summary="tabs_action",
   examples=("qubit, list open tabs",),
   help="Page tabs with the Qubit-current marker (read-only).")
_E("browser_read", "Read the current tab",
   ("read the current tab", "read this tab", "read current tab",
    "what is on this page", "read the page"),
   "browser_tabs", ("browser",),
   fixed=(("action", "read"),),
   summary="tabread",
   examples=("qubit, read the current tab",),
   help="Title, URL, bounded text (untrusted evidence).")
_E("browser_screenshot", "Screenshot the current tab",
   ("take a screenshot", "screenshot this page", "capture the page",
    "take a screenshot of this page", "capture this tab"),
   "browser_tabs", ("browser",),
   fixed=(("action", "screenshot"),),
   summary="screenshot",
   examples=("qubit, take a screenshot",),
   help="Bounded screenshot metadata (pixels stay on the dashboard/CLI; "
        "captured image is untrusted evidence).")
_E("browser_find", "Search this page",
   ("search this page", "find on this page", "search the page",
    "find in page"),
   "browser_tabs", ("browser",),
   args=(_R("query"),), fixed=(("action", "find"),),
   summary="tabs_action",
   examples=("qubit, search this page invoices",),
   help="Match count plus bounded snippets (read-only).")
_E("browser_open_browser", "Open the browser",
   ("open the browser", "launch the browser", "show the browser",
    "focus the browser"),
   "browser_tabs", ("browser",),
   fixed=(("action", "open_browser"),), confirm=True,
   summary="tabs_action",
   examples=("qubit, open the browser",),
   help="Focus the current tab, or a typed handoff when down (confirmed).")
_E("browser_activate", "Activate a tab",
   ("activate tab", "switch to tab", "bring tab forward", "switch tab"),
   "browser_tabs", ("browser",),
   args=(_R("target"),), fixed=(("action", "activate"),), confirm=True,
   summary="tabs_action",
   examples=("qubit, switch to tab inbox",),
   help="Bring a tab to front by id or title (confirmed).")
_E("browser_open", "Open a URL",
   ("open url", "open page", "open website", "open link"),
   "browser_tabs", ("browser",),
   args=(_R("url"),), fixed=(("action", "open"),), confirm=True,
   summary="tabs_action",
   examples=("qubit, open page https://example.com",),
   help="Open http(s) URL in a new tab (confirmed).")
_E("browser_navigate", "Navigate a tab",
   ("go to", "navigate to", "navigate tab"),
   "browser_tabs", ("browser",),
   args=(_R("url"),), fixed=(("action", "navigate"),),
   confirm=True, summary="tabs_action",
   examples=("qubit, navigate to https://example.com",),
   help="Navigate the current tab (confirmed).")
_E("browser_fill", "Fill this form",
   ("fill this form", "fill the form", "fill form", "type into",
    "fill in the form"),
   "browser_tabs", ("browser",),
   args=(_R("selector"), _R("text")), fixed=(("action", "fill"),),
   confirm=True, split="first-rest", summary="tabs_action",
   examples=("qubit, fill form email bob@example.com",),
   help="Type into a form field, no page script (confirmed).")
_E("browser_click", "Click an element",
   ("click", "click button", "press the button", "click link"),
   "browser_tabs", ("browser",),
   args=(_R("selector"),), fixed=(("action", "click"),), confirm=True,
   summary="tabs_action",
   examples=("qubit, click sign in",),
   help="Click by CSS selector or label (confirmed).")
_E("browser_close_tab", "Close a tab",
   ("close tab", "close this tab", "close the tab", "close current tab"),
   "browser_tabs", ("browser",),
   fixed=(("action", "close"),), confirm=True,
   summary="tabs_action",
   examples=("qubit, close this tab",),
   help="Close the current tab; profiles untouched (confirmed).")
_E("computer_status", "Desktop capability",
   ("desktop status", "computer status", "can you control the desktop"),
   "computer", ("computer-use",),
   args=(_C("action", ("status", "doctor"), required=False),),
   fixed=(("action", "status"),),
   examples=("qubit, desktop status",),
   help="Control DISABLED; probes only.")
_E("plugins_install", "Install plugin",
   ("install plugin", "add plugin", "install skill from file",
    "install this reviewed skill", "install reviewed skill",
    "install reviewed manifest"),
   "plugins_install", ("plugins-install",), args=(_R("manifest"),),
   confirm=True, split="path",
   examples=("qubit, install plugin from manifest",),
   help="Stage a reviewed manifest disabled (confirmed).")
_E("plugins_remote_refusal", "Install remote plugin",
   ("install remote plugin", "plugin from github", "plugin from url",
    "install plugin from git"),
   "plugins_remote", ("plugins-install",), args=(_R("identifier"),),
   fixed=(("action", "remote"),),
   examples=("qubit, install remote plugin",),
   help="Typed refusal: remote identifiers are never fetched.")
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
   args=(_C("action", ("list", "show"), required=False), _S("kind")),
   fixed=(("action", "list"),),
   examples=("qubit, list config profiles",),
   help="Configuration profiles.")
_E("migrate_status", "Migration status",
   ("migration status", "is migrated", "legacy status"),
   "migrate_status", ("migrate",),
   examples=("qubit, migration status",),
   help="Legacy migration state.")


# ------------------------------------------- 8T-F tri-parity completions
# One entry per CLI-mutating sub-operation that previously had dashboard +
# CLI but no voice path. Each entry reuses the exact backend branch above
# (same module function the CLI calls), so CLI (`voice say`), dashboard
# (operations console), and microphone resolve the same backend, policy,
# confirmation, receipt, audit, redaction, and emergency-stop behavior.
_E("brain_principal_add", "Add Brain principal",
   ("add brain principal", "new brain person", "register principal"),
   "brain", ("memory",), args=(_R("pid"), _S("label")),
   fixed=(("action", "principal_add"),), confirm=True,
   examples=("qubit, add brain principal maria",),
   help="Register a Brain principal (confirmed).")
_E("brain_member_add", "Link Brain member",
   ("link brain member", "add employee to org", "join organization"),
   "brain", ("memory",), args=(_R("org"), _R("employee")),
   fixed=(("action", "member_add"),), confirm=True,
   examples=("qubit, link brain member",),
   help="Link an employee to an org (confirmed).")
_E("brain_member_remove", "Unlink Brain member",
   ("unlink brain member", "remove employee from org", "leave organization"),
   "brain", ("memory",), args=(_R("org"), _R("employee")),
   fixed=(("action", "member_remove"),), confirm=True,
   examples=("qubit, unlink brain member",),
   help="Unlink an employee (confirmed; data kept).")
_E("brain_profile_set", "Set Brain field",
   ("set brain field", "store profile field", "save brain detail"),
   "brain", ("memory",),
   args=(_R("principal"), _R("key"), _R("value")),
   fixed=(("action", "profile_set"),), confirm=True,
   examples=("qubit, set brain field",),
   help="Set a Brain profile field (confirmed).")
_E("brain_profile_forget", "Forget Brain field",
   ("forget brain field", "delete profile field", "erase brain detail"),
   "brain", ("memory",), args=(_R("principal"), _S("key")),
   fixed=(("action", "profile_forget"),), confirm=True,
   examples=("qubit, forget brain field",),
   help="Delete Brain profile data (confirmed).")
_E("brain_profile_sweep", "Sweep expired Brain fields",
   ("sweep brain fields", "delete expired brain data", "brain expiry sweep"),
   "brain", ("memory",), fixed=(("action", "profile_sweep"),), confirm=True,
   examples=("qubit, sweep brain fields",),
   help="Delete expired Brain fields (confirmed).")
_E("brain_profile_export", "Export Brain profile",
   ("export brain profile", "download brain profile"),
   "brain", ("memory",), args=(_R("principal"),),
   fixed=(("action", "profile_export"),),
   examples=("qubit, export brain profile",),
   help="Export scoped profile (references only).")
_E("brain_account_add", "Add Brain account",
   ("add brain account", "link brain account", "bind account alias"),
   "brain", ("memory",), args=(_R("alias"), _R("origin"), _R("principal")),
   fixed=(("action", "account_add"),), confirm=True,
   examples=("qubit, add brain account",),
   help="Bind an account alias (confirmed).")
_E("brain_account_remove", "Remove Brain account",
   ("remove brain account", "unlink brain account", "unbind account alias"),
   "brain", ("memory",), args=(_R("alias"),),
   fixed=(("action", "account_remove"),), confirm=True,
   examples=("qubit, remove brain account",),
   help="Remove an account binding (confirmed).")
_E("brain_vault_list", "List vault references",
   ("list vault references", "show vault", "brain vault status"),
   "brain", ("memory",), fixed=(("action", "vault_list"),),
   examples=("qubit, list vault references",),
   help="Secret references (kinds only).")
_E("brain_vault_set", "Record vault reference",
   ("record vault reference", "store secret reference", "add vault entry"),
   "brain", ("memory",), args=(_R("name"), _R("ref")),
   fixed=(("action", "vault_set"),), confirm=True,
   examples=("qubit, record vault reference",),
   help="Record a secret reference, never a value (confirmed).")
_E("brain_vault_remove", "Remove vault reference",
   ("remove vault reference", "delete vault entry"),
   "brain", ("memory",), args=(_R("name"),),
   fixed=(("action", "vault_remove"),), confirm=True,
   examples=("qubit, remove vault reference",),
   help="Delete a vault reference (confirmed).")
_E("brain_onboard_plan", "Brain setup plan",
   ("brain setup plan", "onboarding steps", "how do i set up my profile"),
   "brain", ("memory",), fixed=(("action", "onboard_plan"),),
   examples=("qubit, brain setup plan",),
   help="First-run onboarding steps.")
_E("hooks_declare", "Declare hook",
   ("declare hook", "add hook", "new event subscription"),
   "hooks", ("hooks",), args=(_R("name"), _R("event"), _R("shell")),
   fixed=(("action", "declare"),), confirm=True,
   examples=("qubit, declare hook deploy",),
   help="Declare a signed event subscription (confirmed; never executes).")
_E("hooks_validate", "Validate hook",
   ("validate hook", "check hook"),
   "hooks", ("hooks",), args=(_R("name"),),
   fixed=(("action", "validate"),),
   examples=("qubit, validate hook deploy",),
   help="Screen a hook declaration.")
_E("hooks_test", "Hook test plan",
   ("hook test plan", "dry run hook", "test hook"),
   "hooks", ("hooks",), args=(_R("name"),),
   fixed=(("action", "test"),),
   examples=("qubit, test hook deploy",),
   help="Broker delivery plan (no execution).")
_E("hooks_revoke", "Revoke hook",
   ("revoke hook", "disable hook"),
   "hooks", ("hooks",), args=(_R("name"),),
   fixed=(("action", "revoke"),), confirm=True,
   examples=("qubit, revoke hook deploy",),
   help="Revoke a hook (confirmed).")
_E("hooks_remove", "Remove hook",
   ("remove hook", "delete hook"),
   "hooks", ("hooks",), args=(_R("name"),),
   fixed=(("action", "remove"),), confirm=True,
   examples=("qubit, remove hook deploy",),
   help="Delete a hook declaration (confirmed).")
_E("hooks_doctor", "Hook health",
   ("hook health", "hooks doctor", "check hooks"),
   "hooks", ("hooks",), fixed=(("action", "doctor"),),
   examples=("qubit, hook health",),
   help="Hook registry health.")
_E("webhook_add", "Add webhook",
   ("add webhook", "subscribe webhook", "new webhook"),
   "webhook", ("pairing/webhook/peer",),
   args=(_R("sub_id"), _R("url"), _S("events")),
   fixed=(("action", "add"),), confirm=True,
   examples=("qubit, add webhook alerts",),
   help="Subscribe with HMAC verification (confirmed).")
_E("webhook_show", "Show webhook",
   ("show webhook", "describe webhook"),
   "webhook", ("pairing/webhook/peer",), args=(_R("sub_id"),),
   fixed=(("action", "show"),),
   examples=("qubit, show webhook alerts",),
   help="One subscription (receipts only).")
_E("webhook_remove", "Remove webhook",
   ("remove webhook", "delete webhook", "unsubscribe webhook"),
   "webhook", ("pairing/webhook/peer",), args=(_R("sub_id"),),
   fixed=(("action", "remove"),), confirm=True,
   examples=("qubit, remove webhook alerts",),
   help="Delete a subscription (confirmed).")
_E("webhook_test", "Webhook test plan",
   ("webhook test plan", "test webhook"),
   "webhook", ("pairing/webhook/peer",), args=(_R("sub_id"),),
   fixed=(("action", "test"),),
   examples=("qubit, test webhook alerts",),
   help="Delivery test plan (receipts only).")
_E("gateway_show", "Show gateway",
   ("show gateway", "describe gateway"),
   "gateway", ("gateway", "gateway-manage",), args=(_R("gateway_id"),),
   fixed=(("action", "show"),),
   examples=("qubit, show gateway main",),
   help="One gateway record.")
_E("gateway_add", "Add gateway",
   ("add gateway", "register gateway"),
   "gateway", ("gateway", "gateway-manage",),
   args=(_R("gateway_id"), _R("provider"), _R("account"), _R("channel"),
         _S("key_ref")),
   fixed=(("action", "add"),), confirm=True,
   examples=("qubit, add gateway main",),
   help="Record an account-bound gateway (confirmed; key_ref is an "
        "env:VAR/file:PATH reference, never a secret value).")
_E("gateway_remove", "Remove gateway",
   ("remove gateway", "delete gateway"),
   "gateway", ("gateway", "gateway-manage",), args=(_R("gateway_id"),),
   fixed=(("action", "remove"),), confirm=True,
   examples=("qubit, remove gateway main",),
   help="Delete a gateway record (confirmed).")
_E("gateway_link", "Link gateway",
   ("link gateway", "check gateway link"),
   "gateway", ("gateway", "gateway-manage",), args=(_R("gateway_id"),),
   fixed=(("action", "link"),),   examples=("qubit, link gateway main",),
   help="Typed gateway link state.")
_E("gateway_supervision_refusal", "Supervise gateway process",
   ("supervise gateway", "start gateway server", "restart gateway service",
    "run gateway process"),
   "gateway", ("gateway", "gateway-manage",),
   args=(_C("target", ("run", "start", "stop", "restart", "install")),),
   fixed=(("action", "supervision"),),
   examples=("qubit, restart gateway service",),
   help="Typed refusal: operators supervise gateway processes.")
_E("peer_show", "Show peer",
   ("show peer", "describe peer"),
   "peer", ("pairing/webhook/peer",), args=(_R("peer_id"),),
   fixed=(("action", "show"),),
   examples=("qubit, show peer office",),
   help="One peer record.")
_E("peer_add", "Add peer",
   ("add peer", "register peer gateway"),
   "peer", ("pairing/webhook/peer",),
   args=(_R("peer_id"), _R("url"), _R("account")),
   fixed=(("action", "add"),), confirm=True,
   examples=("qubit, add peer office",),
   help="Register a peer gateway (confirmed).")
_E("peer_remove", "Remove peer",
   ("remove peer", "delete peer"),
   "peer", ("pairing/webhook/peer",), args=(_R("peer_id"),),
   fixed=(("action", "remove"),), confirm=True,
   examples=("qubit, remove peer office",),
   help="Delete a peer record (confirmed).")
_E("peer_link", "Link peer",
   ("link peer", "check peer link"),
   "peer", ("pairing/webhook/peer",), args=(_R("peer_id"),),
   fixed=(("action", "link"),),
   examples=("qubit, link peer office",),
   help="Typed peer link state.")
_E("msg_account_add", "Add messaging account",
   ("add messaging account", "connect whatsapp", "connect slack",
    "register message account"),
   "msg", ("whatsapp/whatsapp-cloud/slack/send",),
   args=(_R("account_id"), _R("provider"), _R("label"), _S("key_ref")),
   fixed=(("action", "account_add"),), confirm=True,
   examples=("qubit, add messaging account team",),
   help="Record a provider account (confirmed; keys stay vaulted — "
        "key_ref is an env:VAR/file:PATH reference, never a value).")
_E("msg_account_show", "Show messaging account",
   ("show messaging account", "describe message account"),
   "msg", ("whatsapp/whatsapp-cloud/slack/send",),
   args=(_R("account_id"),), fixed=(("action", "account_show"),),
   examples=("qubit, show messaging account team",),
   help="One provider account record.")
_E("msg_account_remove", "Remove messaging account",
   ("remove messaging account", "disconnect whatsapp", "disconnect slack"),
   "msg", ("whatsapp/whatsapp-cloud/slack/send",),
   args=(_R("account_id"),), fixed=(("action", "account_remove"),),
   confirm=True,
   examples=("qubit, remove messaging account team",),
   help="Remove an account record (confirmed).")
_E("msg_list", "List outbox",
   ("list outbox", "show outbox", "message queue"),
   "msg", ("whatsapp/whatsapp-cloud/slack/send",),
   fixed=(("action", "list"),),
   examples=("qubit, list outbox",),
   help="Outbox messages.")
_E("msg_retry", "Retry message",
   ("retry message", "resend message"),
   "msg", ("whatsapp/whatsapp-cloud/slack/send",),
   args=(_I("msg_id", required=False),), fixed=(("action", "retry"),),
   confirm=True,
   examples=("qubit, retry message",),
   help="Retry a queued message (confirmed).")
_E("msg_draft_reply", "Draft reply",
   ("draft reply", "reply to message"),
   "msg", ("whatsapp/whatsapp-cloud/slack/send",),
   args=(_R("account"), _R("body")), fixed=(("action", "draft_reply"),),
   confirm=True, split="to-saying",
   examples=("qubit, draft reply",),
   help="Draft a linked reply (confirmed).")
_E("kanban_init", "Init task board",
   ("init task board", "create main board", "set up kanban"),
   "kanban", ("kanban",), fixed=(("action", "init"),), confirm=True,
   examples=("qubit, init task board",),
   help="Create the default board (confirmed).")
_E("kanban_boards_create", "Create board",
   ("create board", "new task board"),
   "kanban", ("kanban",), args=(_R("board"), _S("name")),
   fixed=(("action", "boards_create"),), confirm=True,
   examples=("qubit, create board home",),
   help="Create a task board (confirmed).")
_E("kanban_assign", "Assign task",
   ("assign task", "give task to"),
   "kanban", ("kanban",), args=(_I("task_id"), _R("assignee")),
   fixed=(("action", "assign"),), confirm=True,
   examples=("qubit, assign task 3",),
   help="Assign a task (confirmed).")
_E("kanban_reassign", "Reassign task",
   ("reassign task", "move task to"),
   "kanban", ("kanban",), args=(_I("task_id"), _R("assignee")),
   fixed=(("action", "reassign"),), confirm=True,
   examples=("qubit, reassign task 3",),
   help="Reassign a task (confirmed).")
_E("kanban_reclaim", "Reclaim task",
   ("reclaim task", "take back task"),
   "kanban", ("kanban",), args=(_I("task_id"),),
   fixed=(("action", "reclaim"),), confirm=True,
   examples=("qubit, reclaim task 3",),
   help="Reclaim a task (confirmed).")
_E("kanban_edit", "Edit task",
   ("edit task", "rename task", "update task"),
   "kanban", ("kanban",), args=(_I("task_id"), _R("title")),
   fixed=(("action", "edit"),), confirm=True,
   examples=("qubit, edit task 3",),
   help="Edit a task title (confirmed).")
_E("kanban_role_grant_voice", "Grant board role",
   ("grant board role", "give board access", "share board with"),
   "kanban", ("kanban",),
   args=(_S("board"), _R("principal"),
         _C("role", ("owner", "editor", "viewer"))),
   fixed=(("action", "role_grant"),), confirm=True,
   examples=("qubit, grant board role editor to sam",),
   help="Grant a board role to a delegate (confirmed).")
_E("kanban_role_revoke_voice", "Revoke board role",
   ("revoke board role", "remove board access", "unshare board with"),
   "kanban", ("kanban",), args=(_S("board"), _R("principal")),
   fixed=(("action", "role_revoke"),), confirm=True,
   examples=("qubit, revoke board role from sam",),
   help="Revoke a delegate's board role (confirmed).")
_E("kanban_widget_move_voice", "Move canvas widget",
   ("move widget", "reposition widget", "resize widget"),
   "kanban", ("kanban",),
   args=(_I("widget_id"), _R("geometry", required=False)),
   fixed=(("action", "widget_move"),), confirm=True, split="int-rest",
   examples=("qubit, move widget 4 to 100 200",),
   help="Reposition/resize a canvas widget (confirmed).")
_E("kanban_snapshot_restore_voice", "Restore canvas snapshot",
   ("restore canvas snapshot", "restore board snapshot",
    "roll back canvas"),
   "kanban", ("kanban",), args=(_I("snapshot_id"),),
   fixed=(("action", "snapshot_restore"),), confirm=True,
   examples=("qubit, restore canvas snapshot 7",),
   help="Restore widgets, layout, roles, and positions (confirmed).")
_E("kanban_swarm", "Swarm board",
   ("swarm board", "swarm the board"),
   "kanban", ("kanban",),
   fixed=(("action", "delegated"), ("subcommand", "swarm")),
   examples=("qubit, swarm board",),
   help="Typed refusal: parallel work goes through the Qubit broker.")
_E("kanban_watch", "Watch board",
   ("watch board", "watch the board"),
   "kanban", ("kanban",),
   fixed=(("action", "delegated"), ("subcommand", "watch")),
   examples=("qubit, watch board",),
   help="Typed refusal: watchers run as monitor jobs.")
_E("kanban_dispatch", "Dispatch board",
   ("dispatch board", "board daemon", "board background task"),
   "kanban", ("kanban",),
   args=(_C("subcommand", ("dispatch", "daemon", "heartbeat", "runs",
                           "notify-subscribe", "notify-list",
                           "notify-unsubscribe"), required=False),),
   fixed=(("action", "delegated"), ("subcommand", "dispatch")),
   examples=("qubit, dispatch board",),
   help="Typed refusal: execution ops delegate to supervised runs.")
_E("kanban_block", "Block task",
   ("block task", "mark task blocked"),
   "kanban", ("kanban",), args=(_I("task_id"),),
   fixed=(("action", "block"),), confirm=True,
   examples=("qubit, block task 3",),
   help="Mark a task blocked (confirmed).")
_E("kanban_unblock", "Unblock task",
   ("unblock task", "mark task ready"),
   "kanban", ("kanban",), args=(_I("task_id"),),
   fixed=(("action", "unblock"),), confirm=True,
   examples=("qubit, unblock task 3",),
   help="Mark a task ready (confirmed).")
_E("kanban_archive", "Archive task",
   ("archive task", "shelve task"),
   "kanban", ("kanban",), args=(_I("task_id"),),
   fixed=(("action", "archive"),), confirm=True,
   examples=("qubit, archive task 3",),
   help="Archive a task (confirmed).")
_E(
    "kanban_restore",
    "Restore task",
    ("restore task", "unarchive task", "bring back task"),
    "kanban",
    ("kanban",),
    args=(_I("task_id"),),
    fixed=(("action", "restore"),),
    confirm=True,
    examples=("qubit, restore task 3",),
    help="Restore an archived task to its prior column (confirmed).",
)
_E("kanban_stats", "Board stats",
   ("board stats", "task counts", "kanban stats"),
   "kanban", ("kanban",), args=(_S("board"),),
   fixed=(("action", "stats"),),
   examples=("qubit, board stats",),
   help="Task counts by status.")
_E("kanban_log", "Board log",
   ("board log", "task history", "kanban log"),
   "kanban", ("kanban",), args=(_S("board"),),
   fixed=(("action", "log"),),
   examples=("qubit, board log",),
   help="Task event history.")
_E("kanban_stores", "Kanban stores",
   ("kanban stores", "which kanban store", "task board stores"),
   "kanban", ("kanban",), fixed=(("action", "stores"),),
   summary="kanban_stores",
   examples=("qubit, which kanban store",),
   help="Both kanban stores and the authoritative one.")
_E("kanban_migrate", "Migrate kanban",
   ("migrate kanban", "unify task boards", "import kanban tasks"),
   "kanban", ("kanban",), args=(_S("dry_run"),),
   fixed=(("action", "migrate"),), confirm=True,
   summary="kanban_migrate",
   examples=("qubit, unify task boards",),
   help="Import legacy kanban tasks into the Qubit store (confirmed).")
_E("kanban_export", "Export board",
   ("export board", "download tasks"),
   "kanban", ("kanban",), fixed=(("action", "export"),),
   examples=("qubit, export board",),
   help="Snapshot all boards and tasks.")
_E(
    "kanban_snapshots",
    "List canvas snapshots",
    (
        "list canvas snapshots",
        "board snapshots",
        "list snapshots",
        "show canvas snapshots",
    ),
    "kanban",
    ("kanban",),
    args=(_I("limit", required=False), _S("board")),
    fixed=(("action", "snapshots"),),
    examples=("qubit, list canvas snapshots",),
    help="Newest-first canvas restore points for a board (read-only; "
    "board defaults to main).",
)
_E("kanban_open", "Open the board",
   ("open the board", "show the board", "open my board",
    "show my board", "board view"),
   "kanban", ("kanban",), args=(_S("board"),),
   fixed=(("action", "open"),), summary="board",
   examples=("qubit, open the board",),
   help="Whole board canvas: columns, widgets, layout, roles.")
_E("kanban_move", "Move this task",
   ("move this task", "move task", "move the task",
    "shift task", "transfer task"),
   "kanban", ("kanban",), args=(_I("task_id"), _S("board"), _S("status")),
   fixed=(("action", "move"),), confirm=True, summary="kanban_task",
   examples=("qubit, move this task",),
   help="Move a task across boards/columns (confirmed).")
_E("kanban_widget_add", "Add board widget",
   ("add board widget", "add widget", "new widget", "add board note"),
   "kanban", ("kanban",),
   args=(_R("title"), _S("board"), _S("kind"), _S("content"), _S("tab")),
   fixed=(("action", "widget_add"),), confirm=True,
   examples=("qubit, add board widget shopping notes",),
   help="Add a sandboxed widget (confirmed; active markup stripped).")
_E("kanban_tab_list", "List board tabs",
   ("list board tabs", "show board tabs", "board tabs"),
   "kanban", ("kanban",), args=(_S("board"),),
   fixed=(("action", "tab_list"),),
   examples=("qubit, list board tabs",),
   help="Ordered tabs (a legacy board reports one virtual default).")
_E("kanban_tab_create", "Create board tab",
   ("create board tab", "new board tab", "add board tab"),
   "kanban", ("kanban",), args=(_S("name"), _S("board"), _S("slug")),
   fixed=(("action", "tab_create"),), confirm=True,
   examples=("qubit, create board tab invoices",),
   help="Create an ordered tab (confirmed).")
_E("kanban_tab_rename", "Rename board tab",
   ("rename board tab",),
   "kanban", ("kanban",),
   args=(_S("tab", True), _S("name", True), _S("board")),
   fixed=(("action", "tab_rename"),), confirm=True,
   examples=("qubit, rename board tab main to overview",),
   help="Rename a tab label (confirmed).")
_E("kanban_tab_delete", "Delete board tab",
   ("delete board tab", "remove board tab"),
   "kanban", ("kanban",), args=(_S("tab", True), _S("board")),
   fixed=(("action", "tab_delete"),), confirm=True,
   examples=("qubit, delete board tab archive",),
   help="Delete a tab, re-homing widgets (confirmed).")
_E("kanban_tab_move", "Reorder board tab",
   ("move board tab", "reorder board tab"),
   "kanban", ("kanban",), args=(_S("tab", True), _I("position"), _S("board")),
   fixed=(("action", "tab_move"),), confirm=True,
   examples=("qubit, move board tab archive to position 2",),
   help="Move a tab to a 0-based position (confirmed).")
_E("kanban_widget_list", "List board widgets",
   ("list board widgets", "show widgets", "board widgets"),
   "kanban", ("kanban",), args=(_S("board"),),
   fixed=(("action", "widget_list"),),
   examples=("qubit, list board widgets",),
   help="Canvas widgets newest-position-first.")
_E("kanban_widget_remove", "Remove board widget",
   ("remove board widget", "delete widget", "remove widget"),
   "kanban", ("kanban",), args=(_I("widget_id"),),
   fixed=(("action", "widget_remove"),), confirm=True,
   examples=("qubit, remove board widget 2",),
   help="Remove a widget (confirmed; snapshot rollback).")
_E("kanban_layout_show", "Show board layout",
   ("show board layout", "board layout", "board columns"),
   "kanban", ("kanban",), args=(_S("board"),),
   fixed=(("action", "layout_show"),),
   examples=("qubit, show board layout",),
   help="Board column layout.")
_E("runtime_images", "List runtime images",
   ("list runtime images", "runtime images", "container images",
    "pinned images", "list containers"),
   "runtime", ("runtime",), fixed=(("action", "images"),),
   examples=("qubit, list runtime images",),
   help="Pinned confined runtimes (no daemon, no pulls).")
_E("runtime_status", "Runtime status",
   ("runtime status", "docker status", "container status",
    "runtime health", "are runtimes ready"),
   "runtime", ("runtime",), fixed=(("action", "status"),),
   summary="runtime",
   examples=("qubit, runtime status",),
   help="Installed state per pinned image, re-verified.")
_E("runtime_doctor", "Runtime diagnosis",
   ("runtime doctor", "diagnose runtimes", "container doctor",
    "check runtimes"),
   "runtime", ("runtime",), fixed=(("action", "doctor"),),
   summary="runtime",
   examples=("qubit, diagnose runtimes",),
   help="Resource/permission/hygiene diagnosis.")
_E("runtime_install", "Install runtime",
   ("install runtime", "install container", "set up runtime"),
   "runtime", ("runtime",), args=(_R("name"), _S("source")),
   fixed=(("action", "install"),), confirm=True, summary="runtime",
   examples=("qubit, install runtime opencode-binary",),
   help="Install a pinned runtime from staged artifacts (confirmed; never downloads).")
_E("runtime_upgrade", "Upgrade runtime",
   ("upgrade runtime", "upgrade container", "refresh runtime"),
   "runtime", ("runtime",), args=(_R("name"),),
   fixed=(("action", "upgrade"),), confirm=True, summary="runtime",
   examples=("qubit, upgrade runtime opencode-binary",),
   help="Re-verify and refresh a pinned runtime (confirmed).")
_E("runtime_rollback", "Roll back runtime",
   ("roll back runtime", "rollback runtime", "restore runtime",
    "downgrade runtime"),
   "runtime", ("runtime",), args=(_R("name"),),
   fixed=(("action", "rollback"),), confirm=True, summary="runtime",
   examples=("qubit, roll back runtime opencode-binary",),
   help="Restore the previous install receipt (confirmed).")
_E("runtime_remove", "Remove runtime",
   ("remove runtime", "remove container", "uninstall runtime"),
   "runtime", ("runtime",), args=(_R("name"),),
   fixed=(("action", "remove"),), confirm=True, summary="runtime",
   examples=("qubit, remove runtime opencode-binary",),
   help="Remove an installed runtime (confirmed; audited).")
_E("project_rename", "Rename project",
   ("rename project", "retitle project"),
   "projects", ("project",), args=(_R("slug"), _R("name")),
   fixed=(("action", "rename"),), confirm=True,
   examples=("qubit, rename project shop",),
   help="Rename a workspace (confirmed).")
_E("project_add_folder", "Add project folder",
   ("add project folder", "include folder in project"),
   "projects", ("project",), args=(_R("slug"), _R("folder")),
   fixed=(("action", "add_folder"),), confirm=True,
   examples=("qubit, add project folder",),
   help="Add a validated folder (confirmed).")
_E("project_remove_folder", "Remove project folder",
   ("remove project folder", "drop folder from project"),
   "projects", ("project",), args=(_R("slug"), _R("folder")),
   fixed=(("action", "remove_folder"),), confirm=True,
   examples=("qubit, remove project folder",),
   help="Remove a folder binding (confirmed).")
_E("project_set_primary", "Set primary folder",
   ("set primary folder", "main project folder"),
   "projects", ("project",), args=(_R("slug"), _R("folder")),
   fixed=(("action", "set_primary"),), confirm=True,
   examples=("qubit, set primary folder",),
   help="Set the primary folder (confirmed).")
_E("project_restore", "Restore project",
   ("restore project", "unarchive project"),
   "projects", ("project",), args=(_R("slug"),),
   fixed=(("action", "restore"),), confirm=True,
   examples=("qubit, restore project shop",),
   help="Restore an archived workspace (confirmed).")
_E("project_rollback", "Rollback projects",
   ("rollback projects", "restore project backup"),
   "projects", ("project",), fixed=(("action", "rollback"),), confirm=True,
   examples=("qubit, rollback projects",),
   help="Restore the audited backup snapshot (confirmed).")
_E("project_bind_board", "Bind board",
   ("bind board", "link board to project"),
   "projects", ("project",), args=(_R("slug"), _R("board")),
   fixed=(("action", "bind_board"),), confirm=True,
   examples=("qubit, bind board",),
   help="Bind a real kanban board (confirmed).")
_E("skills_remove", "Remove skill",
   ("remove skill", "uninstall skill", "delete skill"),
   "skills", ("skills/bundles", "plugins/plugins-list",),
   args=(_R("skill_id"),), fixed=(("action", "remove"),), confirm=True,
   examples=("qubit, remove skill notes",),
   help="Remove a skill (confirmed).")
_E("skills_rollback", "Rollback skill",
   ("rollback skill", "restore skill"),
   "skills", ("skills/bundles",), args=(_R("skill_id"),),
   fixed=(("action", "rollback"),), confirm=True,
   examples=("qubit, rollback skill notes",),
   help="Restore a skill snapshot (confirmed).")
_E("themes_tweak_set", "Tweak theme color",
   ("tweak theme color", "adjust theme token"),
   "themes", ("pets/skin",), args=(_R("tweak"), _R("color")),
   fixed=(("action", "tweak_set"),), confirm=True,
   examples=("qubit, tweak theme color",),
   help="Override one theme token (confirmed).")
_E("themes_tweak_clear", "Clear theme tweak",
   ("clear theme tweak", "reset theme token"),
   "themes", ("pets/skin",), args=(_R("tweak"),),
   fixed=(("action", "tweak_clear"),), confirm=True,
   examples=("qubit, clear theme tweak",),
   help="Drop a token override (confirmed).")
_E("themes_reset", "Reset theme",
   ("reset theme", "default theme"),
   "themes", ("pets/skin",), fixed=(("action", "reset"),), confirm=True,
   examples=("qubit, reset theme",),
   help="Restore the default preset (confirmed).")
_E("pets_downloads_refusal", "Download gallery pet",
   ("download pet", "pet gallery", "install pet", "get a pet"),
   "themes", ("pets/skin",), fixed=(("action", "pets"),),
   examples=("qubit, pet gallery",),
   help="Typed refusal: gallery downloads never fetched; presets apply.")
_E("computer_doctor", "Desktop health",
   ("desktop health", "computer doctor", "check desktop access"),
   "computer", ("computer-use",), fixed=(("action", "doctor"),),
   examples=("qubit, desktop health",),
   help="Display/accessibility/driver probes.")
_E("computer_permissions", "Desktop permissions",
   ("desktop permissions", "computer permissions", "what can you touch"),
   "computer", ("computer-use",), fixed=(("action", "permissions"),),
   examples=("qubit, desktop permissions",),
   help="Bounded desktop scope (control disabled).")
_E("computer_install_refusal", "Desktop driver install",
   ("install desktop driver", "enable computer control"),
   "computer", ("computer-use",), fixed=(("action", "install"),),
   examples=("qubit, install desktop driver",),
   help="Typed refusal: drivers are never auto-installed.")
_E("browser_close_refusal", "Close browser profile",
   ("close browser profile", "close the browser"),
   "browser", ("browser",), fixed=(("action", "close"),),
   examples=("qubit, close browser profile",),
   help="Typed refusal: profiles are never touched.")
_E("proxy_start_refusal", "Start proxy",
   ("start proxy", "run proxy server"),
   "proxy", ("proxy",), fixed=(("action", "start"),),
   examples=("qubit, start proxy",),
   help="Typed refusal: no forwarding server runs.")
_E("proxy_providers", "Proxy providers",
   ("proxy providers", "which providers need proxy"),
   "proxy", ("proxy",), fixed=(("action", "providers"),),
   examples=("qubit, proxy providers",),
   help="Per-provider eligibility (references only).")
_E("acp_serve_refusal", "ACP serve",
   ("start acp server", "serve acp"),
   "acp", ("acp/lsp",), fixed=(("action", "serve"),),
   examples=("qubit, start acp server",),
   help="Typed refusal: no ACP server is shipped.")
_E("acp_accept_hooks_refusal", "ACP bypass flags",
   ("acp accept hooks", "accept hooks mode"),
   "acp", ("acp/lsp", "--accept-hooks/--yolo/--ignore-user-config/--ignore-rules",),
   fixed=(("action", "accept_hooks"),),
   examples=("qubit, acp accept hooks",),
   help="Typed refusal: bypass flags are never honored.")
_E("lsp_restart", "Restart language servers",
   ("restart language servers", "restart lsp"),
   "lsp", ("acp/lsp",), fixed=(("action", "restart"),), confirm=True,
   examples=("qubit, restart language servers",),
   help="Restart local servers (confirmed).")
_E("lsp_install_refusal", "Install language server",
   ("install language server", "download lsp server"),
   "lsp", ("acp/lsp",), args=(_S("server"),),
   fixed=(("action", "install"),),
   examples=("qubit, install language server",),
   help="Typed refusal: binaries are never downloaded.")
_E("lessons_list", "List lessons",
   ("list lessons", "show lessons", "what have you learned", "pending reviews"),
   "lessons", ("curator/journey/learning/memory-graph",),
   fixed=(("action", "list"),),
   examples=("qubit, list lessons",),
   help="Learned preferences with provenance.")
_E("lessons_promote", "Promote lesson",
   ("promote lesson", "approve lesson"),
   "lessons", ("curator/journey/learning/memory-graph",),
   args=(_R("lesson_id"),), fixed=(("action", "promote"),), confirm=True,
   examples=("qubit, promote lesson",),
   help="Promote a reviewed lesson (confirmed).")
_E("lessons_remove", "Remove lesson",
   ("remove lesson", "delete lesson", "forget lesson"),
   "lessons", ("curator/journey/learning/memory-graph",),
   args=(_R("lesson_id"),), fixed=(("action", "remove"),), confirm=True,
   examples=("qubit, remove lesson",),
   help="Tombstone a lesson (confirmed).")
_E("power_status", "Power profile",
   ("power status", "capability states", "what is authorized"),
   "power_status", ("monitor/monitoring",),
   examples=("qubit, power status",),
   help="Typed capability states (read-only).")
_E("websites_list", "List websites",
   ("list websites", "show websites", "tracked sites"),
   "websites", ("monitor/monitoring",), fixed=(("action", "list"),),
   examples=("qubit, list websites",),
   help="Integration records (pending by default).")
_E("websites_show", "Show website",
   ("show website", "describe website"),
   "websites", ("monitor/monitoring",), args=(_R("site_id"),),
   fixed=(("action", "show"),),
   examples=("qubit, show website",),
   help="One integration record.")
_E("web_fetch", "Fetch a web page",
   ("fetch a web page", "fetch the page", "open the web page",
    "read a web page"),
   "websites", ("websites",), args=(_R("url"),),
   fixed=(("action", "fetch"),), summary="web",
   examples=("qubit, fetch a web page https://example.com",),
   help="Read-only, SSRF-guarded bounded fetch.")
_E("web_search", "Search the web",
   ("search the web", "web search", "look something up online"),
   "websites", ("websites",), args=(_R("query"),),
   fixed=(("action", "search"),), summary="web",
   examples=("qubit, search the web for rust async",),
   help="Configured provider search; typed unavailable when none.")


# ------------------------------------------- task-mandated natural commands
# "Qubit, list my projects" / "show my sessions" are aliases on the entries
# above; "run the task" dispatches one typed task through the Qubit agent
# (same agent the CLI `task` path uses), confirm-gated.
_E("task_run", "Run the task",
   ("run the task", "run task", "run that task", "execute the task",
    "do the task"),
   "task_run", ("chat",), args=(_R("task_text"),),
   fixed=(("action", "run"),), confirm=True,
   summary="task",
   examples=("qubit, run the task summarize my notes",),
   help="Dispatch one task to the agent runtime (confirmed).")
_E("task_run_current", "Run this task",
   ("run this task", "run the current task", "run my task",
    "run current task", "run this coding task"),
   "task_run", ("chat",),
   fixed=(("action", "run"), ("from_current", "1")), confirm=True,
   summary="task",
   examples=("qubit, run this task",),
   help="Dispatch the current kanban task to the agent runtime "
        "(confirmed; typed empty state when nothing is actionable).")
# ------------------------------------------- schedules (user cron native)
_E("schedules_create", "Schedule this task",
   ("schedule this task", "schedule this", "schedule task", "create schedule",
    "new schedule", "schedule for me"),
   "schedules", ("cron",),
   args=(_R("task_text",
            clarifier="What task should I schedule?"),
         _R("when",
            clarifier="When should it run? Say every 30 minutes, "
                      "daily 9am, or once in 10 minutes.")),
   fixed=(("action", "create"),), confirm=True, split="task-when",
   summary="schedules",
   examples=("qubit, schedule this task daily 9am",),
   help="Schedule a task on interval/daily/once (confirmed).")
_E("schedules_list", "List schedules",
   ("list schedules", "show my schedules", "what is scheduled",
    "my schedules", "list scheduled tasks"),
   "schedules", ("cron",),
   fixed=(("action", "list"),), summary="schedules",
   examples=("qubit, list schedules",),
   help="Scheduled jobs with states (read-only).")
_E("schedules_show", "Show schedule",
   ("show schedule", "describe schedule", "schedule details"),
   "schedules", ("cron",), args=(_I("job_id"),),
   fixed=(("action", "show"),), summary="schedules",
   examples=("qubit, show schedule 2",),
   help="One job with recent runs.")
_E("schedules_pause", "Pause schedule",
   ("pause schedule", "stop schedule", "unschedule", "pause my schedule"),
   "schedules", ("cron",), args=(_I("job_id"),),
   fixed=(("action", "pause"),), confirm=True, summary="schedules",
   examples=("qubit, pause schedule 2",),
   help="Pause a job (confirmed).")
_E("schedules_resume", "Resume schedule",
   ("resume schedule", "restart schedule", "unpause schedule"),
   "schedules", ("cron",), args=(_I("job_id"),),
   fixed=(("action", "resume"),), confirm=True, summary="schedules",
   examples=("qubit, resume schedule 2",),
   help="Resume a job (confirmed).")
_E("schedules_delete", "Delete schedule",
   ("delete schedule", "remove schedule", "cancel schedule"),
   "schedules", ("cron",), args=(_I("job_id"),),
   fixed=(("action", "delete"),), confirm=True, summary="schedules",
   examples=("qubit, delete schedule 2",),
   help="Delete a job, history retained (confirmed).")
_E("schedules_history", "Schedule history",
   ("schedule history", "show schedule runs", "scheduled runs"),
   "schedules", ("cron",),
   fixed=(("action", "history"),), summary="schedules",
   examples=("qubit, schedule history",),
   help="Run history, retained after delete.")
_E("schedules_run_due", "Run scheduled tasks",
   ("run scheduled tasks", "run due schedules", "run my schedules",
    "run due jobs"),
   "schedules", ("cron",),
   fixed=(("action", "run_due"),), confirm=True, summary="schedules",
   examples=("qubit, run due schedules",),
   help="Execute due jobs now, typed per-job results (confirmed).")
# ------------------------------ OpenClaw automation (OC-31/OC-D05/OC-D25)
# Standing orders / auto-reply, flows, heartbeat, QA scenarios, and subagent
# yield handoffs. Writes compose the canonical `automation: <verb> key=value`
# directive and run it through the ONE production directive dispatcher the
# single schedules tick calls; reads answer from the same automation store.
_AUTO = "automation"
_E("automation_status", "Automation status",
   ("automation status", "standing orders status", "automation report"),
   _AUTO, ("automation",), fixed=(("action", "status"),),
   summary="generic", examples=("qubit, automation status",),
   help="Standing-order/flow/heartbeat/QA/yield counts (read-only).")
_E("automation_orders", "List standing orders",
   ("list standing orders", "show standing orders", "list auto replies",
    "list autoresponders"),
   _AUTO, ("automation",), fixed=(("action", "orders"),),
   summary="generic", examples=("qubit, list standing orders",),
   help="Configured auto-reply standing orders (read-only).")
_E("automation_heartbeats", "List heartbeats",
   ("list heartbeats", "show heartbeats", "list heartbeat monitors"),
   _AUTO, ("automation",), fixed=(("action", "heartbeats"),),
   summary="generic", examples=("qubit, list heartbeats",),
   help="Configured liveness heartbeat monitors (read-only).")
_E("automation_flows", "List automation flows",
   ("list automation flows", "show automation flows", "list flows"),
   _AUTO, ("automation",), fixed=(("action", "flows"),),
   summary="generic", examples=("qubit, list automation flows",),
   help="Configured bounded automation flows (read-only).")
_E("automation_scenarios", "List QA scenarios",
   ("list qa scenarios", "show qa scenarios", "qa lab scenarios"),
   _AUTO, ("automation",), fixed=(("action", "scenarios"),),
   summary="generic", examples=("qubit, list qa scenarios",),
   help="Persisted QA Lab scenarios and last state (read-only).")
_E("automation_yield_pending", "List pending handoffs",
   ("list pending handoffs", "show pending yields", "pending handoffs"),
   _AUTO, ("automation",), fixed=(("action", "yield_pending"),),
   summary="generic", examples=("qubit, list pending handoffs",),
   help="Unconsumed subagent yield handoffs (read-only).")
_E("automation_order_add", "Add standing order",
   ("add standing order", "create standing order", "add auto reply",
    "add autoresponder"),
   _AUTO, ("automation",), args=(_R("spec",
       clarifier="Say the order as key=value pairs, for example "
                 "name=greet match=hello reply=hi account=team to=C1."),),
   fixed=(("action", "directive"), ("verb", "order.add")),
   confirm=True, summary="generic",
   examples=("qubit, add standing order name=greet match=hello reply=hi "
             "account=team to=C1",),
   help="Register an auto-reply standing order (confirmed).")
_E("automation_order_enable", "Enable standing order",
   ("enable standing order", "turn on standing order", "enable auto reply"),
   _AUTO, ("automation",), args=(_R("spec",
       clarifier="Which standing order? Say name=greet."),),
   fixed=(("action", "directive"), ("verb", "order.enable")),
   confirm=True, summary="generic",
   examples=("qubit, enable standing order name=greet",),
   help="Re-enable a standing order (confirmed).")
_E("automation_order_disable", "Disable standing order",
   ("disable standing order", "turn off standing order",
    "disable auto reply"),
   _AUTO, ("automation",), args=(_R("spec",
       clarifier="Which standing order? Say name=greet."),),
   fixed=(("action", "directive"), ("verb", "order.disable")),
   confirm=True, summary="generic",
   examples=("qubit, disable standing order name=greet",),
   help="Disable a standing order without deleting it (confirmed).")
_E("automation_order_remove", "Remove standing order",
   ("remove standing order", "delete standing order", "remove auto reply"),
   _AUTO, ("automation",), args=(_R("spec",
       clarifier="Which standing order? Say name=greet."),),
   fixed=(("action", "directive"), ("verb", "order.remove")),
   confirm=True, summary="generic",
   examples=("qubit, remove standing order name=greet",),
   help="Delete a standing order (confirmed).")
_E("automation_heartbeat_add", "Add heartbeat",
   ("add heartbeat", "create heartbeat", "add heartbeat monitor"),
   _AUTO, ("automation",), args=(_R("spec",
       clarifier="Say the heartbeat as key=value pairs, for example "
                 "name=hb every=5 note=liveness."),),
   fixed=(("action", "directive"), ("verb", "heartbeat.add")),
   confirm=True, summary="generic",
   examples=("qubit, add heartbeat name=hb every=5 note=liveness",),
   help="Register a liveness heartbeat (confirmed).")
_E("automation_flow_add", "Add automation flow",
   ("add automation flow", "create automation flow", "add flow"),
   _AUTO, ("automation",), args=(_R("spec",
       clarifier="Say the flow as key=value pairs, for example "
                 "name=wake steps='[{\"action\":\"notify\",\"text\":\"up\"}]'."),),
   fixed=(("action", "directive"), ("verb", "flow.add")),
   confirm=True, summary="generic",
   examples=("qubit, add automation flow name=wake steps='[{\"action\":"
             "\"notify\",\"text\":\"up\"}]'",),
   help="Register a bounded automation flow (confirmed).")
_E("automation_flow_run", "Run automation flow",
   ("run automation flow", "execute automation flow", "run flow"),
   _AUTO, ("automation",), args=(_R("spec",
       clarifier="Which flow? Say name=wake."),),
   fixed=(("action", "directive"), ("verb", "flow.run")),
   confirm=True, summary="generic",
   examples=("qubit, run automation flow name=wake",),
   help="Run a stored automation flow (confirmed).")
_E("automation_qa_add", "Add QA scenario",
   ("add qa scenario", "create qa scenario", "add qa test"),
   _AUTO, ("automation",), args=(_R("spec",
       clarifier="Say the scenario as key=value pairs, for example "
                 "name=smoke scenario='[{\"name\":\"ok\",\"action\":\"check\"}]'."),),
   fixed=(("action", "directive"), ("verb", "qa.add")),
   confirm=True, summary="generic",
   examples=("qubit, add qa scenario name=smoke scenario='[{\"name\":\"ok\","
             "\"action\":\"check\"}]'",),
   help="Persist a named QA Lab scenario (confirmed).")
_E("automation_qa_run", "Run QA scenario",
   ("run qa scenario", "execute qa scenario", "run qa test"),
   _AUTO, ("automation",), args=(_R("spec",
       clarifier="Which scenario? Say name=smoke."),),
   fixed=(("action", "directive"), ("verb", "qa.run")),
   confirm=True, summary="generic",
   examples=("qubit, run qa scenario name=smoke",),
   help="Run a stored QA scenario and persist the report (confirmed).")
_E("automation_yield_handoff", "Create yield handoff",
   ("yield handoff", "create yield handoff", "handoff session"),
   _AUTO, ("automation",), args=(_R("spec",
       clarifier="Say the handoff as key=value pairs, for example "
                 "session=sess-1 reason=review context=continue depth=1."),),
   fixed=(("action", "yield_handoff"),),
   confirm=True, summary="generic",
   examples=("qubit, create yield handoff session=sess-1 reason=review "
             "context=continue depth=1",),
   help="Publish bounded continuation context for a session (confirmed).")
_E("automation_yield_resume", "Resume yield handoff",
   ("resume yield handoff", "consume yield handoff", "resume handoff"),
   _AUTO, ("automation",), args=(_R("spec",
       clarifier="Which session? Say session=sess-1 consume=1."),),
   fixed=(("action", "yield_resume"),),
   confirm=True, summary="generic",
   examples=("qubit, resume yield handoff session=sess-1 consume=1",),
   help="Consume a pending yield handoff (confirmed).")
# ------------------------------------------- scoped files + IDE launcher
_E("files_inspect", "Inspect this project folder",
   ("inspect this project folder", "inspect this folder",
    "inspect project folder",
    "show project folder", "inspect the project folder",
    "what is in this project"),
   "files", ("files",),
   args=(_S("path"),), fixed=(("action", "inspect"),), summary="files",
   examples=("qubit, inspect this project folder",),
   help="Active project folder, or a typed empty state.")
_E("file_patch_rollback", "Roll back a file patch",
   ("roll back the patch", "undo the patch", "rollback file patch",
    "revert the file patch"),
   "files", ("files",), args=(_S("target", True),),
   fixed=(("action", "rollback"),), confirm=True, summary="file_patch",
   examples=("qubit, roll back the patch on /srv/project/app.py",),
   help="Restore the prior content of the last applied patch (confirmed).")
_E("files_list", "List project files",
   ("list project files", "show project files", "list files here"),
   "files", ("files",),
   args=(_S("path"),), fixed=(("action", "list"),), summary="files",
   examples=("qubit, list project files",),
   help="Bounded folder listing inside project roots.")
_E("files_read", "Read project file",
   ("read project file", "show me the file", "read this file"),
   "files", ("files",),
   args=(_R("path"),), fixed=(("action", "read"),), split="path",
   summary="files",
   examples=("qubit, read project file notes",),
   help="Bounded redacted read inside project roots.")
_E("git_status", "Git status",
   ("git status", "working tree status", "what changed", "uncommitted changes"),
   "git", ("git",),
   fixed=(("action", "status"),),
   examples=("qubit, git status",),
   help="Porcelain status of the active project repo (read-only).")
_E(
    "git_log",
    "Recent commits",
    ("recent commits", "git log", "commit history", "show commits"),
    "git",
    ("git",),
    args=(_I("limit", required=False),),
    fixed=(("action", "log"),),
    examples=("qubit, recent commits", "qubit, recent commits 20"),
    help="Bounded commit log of the active project repo (read-only; "
    f"default {GIT_LOG_DEFAULT}, max 50).",
)
_E("git_diff", "Show uncommitted diff",
   ("show diff", "git diff", "uncommitted diff", "show changes"),
   "git", ("git",),
   args=(_S("path"),), fixed=(("action", "diff"),),
   examples=("qubit, show diff",),
   help="Bounded unstaged diff, optionally one file (read-only).")
_E("git_show", "Show committed file",
   ("show committed file", "git show file", "file at head",
    "last committed version"),
   "git", ("git",),
   args=(_R("path"),), fixed=(("action", "show"),), split="path",
   examples=("qubit, show committed file notes",),
   help="One committed file version from HEAD (read-only).")
_E("ide_status", "Editor status",
   ("editor status", "which editor", "ide status", "what editor",
    "is my editor ready"),
   "ide", ("ide",),
   fixed=(("action", "status"),), summary="ide",
   examples=("qubit, which editor",),
   help="Detected editors and default (read-only).")
_E("ide_open", "Open my IDE",
   ("open my ide", "open the ide", "launch my editor", "open editor",
    "open ide"),
   "ide", ("ide",),
   fixed=(("action", "open"),), confirm=True, summary="ide",
   examples=("qubit, open my ide",),
   help="Open the workspace in the editor (confirmed; open only).")
# ------------------------------------------- 8F/8N/8P/8Q/product coverage
# One entry per CLI capability family that previously had CLI (+dashboard
# Tier-2) but no voice path. Each reuses the exact backend module function
# the CLI calls; mutations are confirm-gated through the shared journal.
_E("actions_list", "List actions",
   ("list actions", "show actions", "what actions", "action status"),
   "actions", ("actions",), fixed=(("action", "list"),),
   examples=("qubit, list actions",),
   help="Brokered action inventory.")
_E("actions_show", "Show action",
   ("show action", "describe action", "action details"),
   "actions", ("actions",), args=(_R("action_id"),),
   fixed=(("action", "show"),),
   examples=("qubit, show action",),
   help="One action contract.")
_E("actions_check", "Check action",
   ("check action", "can action run", "action permission"),
   "actions", ("actions",), args=(_R("action_id"), _S("scope")),
   fixed=(("action", "check"),), split="first-rest",
   examples=("qubit, check action",),
   help="Read-only permission decision.")
_E("actions_receipts", "Action receipts",
   ("action receipts", "list action receipts", "recent actions"),
   "actions", ("actions",), fixed=(("action", "receipts"),),
   examples=("qubit, action receipts",),
   help="Execution receipts.")
_E("actions_execute", "Execute action",
   ("execute action", "run action"),
   "actions", ("actions",), args=(_R("action_id"),),
   fixed=(("action", "execute"),), confirm=True,
   examples=("qubit, execute action",),
   help="Execute a default-args action (confirmed).")
_E("actions_cancel", "Cancel action",
   ("cancel action", "stop action"),
   "actions", ("actions",), args=(_R("receipt_id"),),
   fixed=(("action", "cancel"),), confirm=True,
   examples=("qubit, cancel action",),
   help="Cancel a running action (confirmed).")
_E("actions_receipt", "Show action receipt",
   ("show action receipt", "action receipt"),
   "actions", ("actions",), args=(_R("receipt_id"),),
   fixed=(("action", "receipt"),),
   examples=("qubit, show action receipt",),
   help="One execution receipt.")
_E("ghost_list", "List Ghost capabilities",
   ("list ghost capabilities", "ghost capabilities", "ghost list"),
   "ghost", ("ghost",), fixed=(("action", "list"),),
   examples=("qubit, list ghost capabilities",),
   help="Supervised adapter inventory.")
_E("ghost_status", "Ghost status",
   ("ghost status", "ghost adapter status"),
   "ghost", ("ghost",), fixed=(("action", "status"),),
   examples=("qubit, ghost status",),
   help="Adapter status (read-only).")
_E("ghost_show", "Show Ghost capability",
   ("show ghost capability", "describe ghost action"),
   "ghost", ("ghost",), args=(_R("action_id"),),
   fixed=(("action", "show"),),
   examples=("qubit, show ghost capability",),
   help="One Ghost contract.")
_E("ghost_check", "Check Ghost capability",
   ("check ghost capability", "can ghost run"),
   "ghost", ("ghost",), args=(_R("action_id"), _S("scope")),
   fixed=(("action", "check"),), split="first-rest",
   examples=("qubit, check ghost capability",),
   help="Read-only permission decision.")
_E("ghost_grant", "Grant Ghost session",
   ("grant ghost session", "allow ghost capability"),
   "ghost", ("ghost",),
   args=(_R("capability"), _S("scope"), _S("target"), _S("ttl")),
   fixed=(("action", "grant"),), split="first-rest", confirm=True,
   examples=("qubit, grant ghost session",),
   help="Bounded expiring target-bound session grant (confirmed).")
_E("ghost_action_preview", "Preview Ghost action",
   ("preview ghost action", "preview desktop action",
    "what would ghost do", "ghost preview"),
   "ghost", ("ghost",),
   args=(_R("action_id"), _S("target"), _S("text"), _S("role")),
   fixed=(("action", "preview"),),
   examples=("qubit, preview ghost action",),
   summary="ghost",
   help="No-side-effect preview with exact args.")
_E("ghost_click", "Click this button",
   ("click this button", "click the button", "click it",
    "press this button", "click this link"),
   "ghost", ("ghost",),
   args=(_R("target"), _S("role"), _S("scope")),
   fixed=(("action", "click"),), confirm=True,
   examples=("qubit, click this button",),
   summary="ghost",
   help="Click the exact semantic target (confirmed, grant-bound).")
_E("ghost_type", "Fill this form",
   ("type into this field", "fill this field", "enter this text",
    "dictate into this field"),
   "ghost", ("ghost",),
   args=(_R("target"), _R("text"), _S("role"), _S("scope")),
   fixed=(("action", "type"),), confirm=True, split="first-rest",
   summary="ghost",
   examples=("qubit, type into this field name hello",),
   help="Type into the exact semantic target (confirmed, grant-bound).")
_E("ghost_read_tab", "Read the browser tab",
   ("read the browser tab", "ghost read this tab", "read the tab",
    "what is in the tab", "list browser tabs"),
   "ghost", ("ghost",), args=(_S("role"), _S("scope")),
   fixed=(("action", "read_tab"),),
   examples=("qubit, read the browser tab",),
   summary="ghost",
   help="Read-only tab inventory for one browser role (grant-gated).")
_E("ghost_action_retry", "Retry Ghost action",
   ("retry ghost action", "retry desktop action", "try ghost again"),
   "ghost", ("ghost",),
   args=(_R("receipt_id"), _S("target"), _S("text"), _S("role"),
         _S("scope")),
   fixed=(("action", "retry"),), confirm=True,
   examples=("qubit, retry ghost action",),
   summary="ghost",
   help="Re-execute with the SAME args/target/scope (confirmed).")
_E("ghost_action_recover", "Recover Ghost action",
   ("recover ghost action", "undo ghost action", "roll back ghost"),
   "ghost", ("ghost",), args=(_R("receipt_id"),),
   fixed=(("action", "recover"),), confirm=True,
   examples=("qubit, recover ghost action",),
   summary="ghost",
   help="Compensating rollback, honest when impossible (confirmed).")
_E("ghost_revoke", "Revoke Ghost grant",
   ("revoke ghost grant", "remove ghost grant"),
   "ghost", ("ghost",), args=(_R("grant_id"),),
   fixed=(("action", "revoke"),), confirm=True,
   examples=("qubit, revoke ghost grant",),
   help="Revoke a session grant (confirmed).")
_E("ghost_execute", "Execute Ghost action",
   ("execute ghost action", "run ghost action"),
   "ghost", ("ghost",), args=(_R("action_id"),),
   fixed=(("action", "execute"),), confirm=True,
   examples=("qubit, execute ghost action",),
   help="Execute a default-args Ghost action (confirmed).")
_E("ghost_cancel", "Cancel Ghost action",
   ("cancel ghost action", "stop ghost action"),
   "ghost", ("ghost",), args=(_R("receipt_id"),),
   fixed=(("action", "cancel"),), confirm=True,
   examples=("qubit, cancel ghost action",),
   help="Cancel a running Ghost action (confirmed).")
_E("ghost_receipts", "Ghost receipts",
   ("ghost receipts", "list ghost receipts"),
   "ghost", ("ghost",), fixed=(("action", "receipts"),),
   examples=("qubit, ghost receipts",),
   help="Ghost execution receipts.")
_E("ghost_enable", "Enable Ghost adapter",
   ("enable ghost", "turn on ghost"),
   "ghost", ("ghost",), fixed=(("action", "enable"),), confirm=True,
   examples=("qubit, enable ghost",),
   help="Enable the adapter (confirmed).")
_E("ghost_disable", "Disable Ghost adapter",
   ("disable ghost", "turn off ghost"),
   "ghost", ("ghost",), fixed=(("action", "disable"),), confirm=True,
   examples=("qubit, disable ghost",),
   help="Disable the adapter (confirmed).")
_E("ghost_estop", "Stop Ghost adapter",
   ("stop ghost", "ghost emergency stop"),
   "ghost", ("ghost",), fixed=(("action", "emergency-stop"),), confirm=True,
   examples=("qubit, stop ghost",),
   help="Emergency-stop the adapter (confirmed).")
_E("ghost_reset", "Reset Ghost adapter",
   ("reset ghost", "restart ghost adapter"),
   "ghost", ("ghost",), fixed=(("action", "reset"),), confirm=True,
   examples=("qubit, reset ghost",),
   help="Reset the adapter (confirmed).")
_E("opencode_status", "OpenCode status",
   ("opencode status", "coding adapter status"),
   "opencode", ("opencode",), fixed=(("action", "status"),),
   examples=("qubit, opencode status",),
   help="Adapter status (read-only).")
_E("opencode_list", "List OpenCode capabilities",
   ("list opencode capabilities", "opencode capabilities"),
   "opencode", ("opencode",), fixed=(("action", "list"),),
   examples=("qubit, list opencode capabilities",),
   help="Adapter inventory (read-only).")
_E("opencode_show", "Show OpenCode capability",
   ("show opencode capability", "describe opencode action"),
   "opencode", ("opencode",), args=(_R("action_id"),),
   fixed=(("action", "show"),),
   examples=("qubit, show opencode capability",),
   help="One adapter contract.")
_E("opencode_check", "Check OpenCode capability",
   ("check opencode capability", "can opencode run"),
   "opencode", ("opencode",), args=(_R("action_id"), _S("scope")),
   fixed=(("action", "check"),), split="first-rest",
   examples=("qubit, check opencode capability",),
   help="Read-only permission decision.")
_E("opencode_grant", "Grant OpenCode capability",
   ("grant opencode capability", "allow opencode run",
    "authorize coding run"),
   "opencode", ("opencode",),
   args=(_S("capability"), _S("scope"), _S("ttl")),
   fixed=(("action", "grant"),), confirm=True, summary="opencode",
   examples=("qubit, allow opencode run",),
   help="Grant a coding capability in the 7E store (confirmed).")
_E("opencode_revoke", "Revoke OpenCode grant",
   ("revoke opencode grant", "remove opencode grant"),
   "opencode", ("opencode",), args=(_R("grant_id"),),
   fixed=(("action", "revoke"),), confirm=True, summary="opencode",
   examples=("qubit, revoke opencode grant",),
   help="Revoke a coding grant (confirmed).")
_E("opencode_preview", "Preview coding task",
   ("preview coding task", "plan this coding task",
    "preview opencode run", "what would opencode do"),
   "opencode", ("opencode",),
   args=(_R("task"), _S("project"), _S("timeout")),
   fixed=(("action", "preview"),), summary="opencode",
   examples=("qubit, preview coding task add tests",),
   help="No-side-effect run preview on a disposable worktree.")
_E("opencode_run", "Run this coding task",
   ("run this opencode task", "code this with opencode",
    "start opencode run", "opencode this task", "run coding task now"),
   "opencode", ("opencode",),
   args=(_R("task"), _S("project"), _S("timeout")),
   fixed=(("action", "run"),), confirm=True, summary="opencode",
   examples=("qubit, run this opencode task add tests",),
   help="Disposable-worktree coding run (confirmed; in-project stays CLI/dashboard).")
_E("opencode_cancel", "Cancel coding run",
   ("cancel coding run", "stop coding run", "abort opencode run"),
   "opencode", ("opencode",), args=(_R("receipt_id"),),
   fixed=(("action", "cancel"),), confirm=True, summary="opencode",
   examples=("qubit, cancel coding run",),
   help="Cancel an in-flight coding run (confirmed).")
_E("opencode_retry", "Retry coding run",
   ("retry coding run", "retry opencode run", "try coding again"),
   "opencode", ("opencode",),
   args=(_R("receipt_id"), _R("task"), _S("project")),
   fixed=(("action", "retry"),), confirm=True, summary="opencode",
   examples=("qubit, retry coding run",),
   help="Re-run with the SAME task/project (confirmed).")
_E("opencode_recover", "Recover coding run",
   ("recover coding run", "roll back coding run",
    "undo coding run"),
   "opencode", ("opencode",), args=(_R("receipt_id"),),
   fixed=(("action", "recover"),), confirm=True, summary="opencode",
   examples=("qubit, recover coding run",),
   help="Compensating rollback, honest when impossible (confirmed).")
_E("opencode_receipts", "Coding receipts",
   ("coding receipts", "list coding runs", "opencode receipts",
    "recent coding runs"),
   "opencode", ("opencode",), args=(_S("limit"),),
   fixed=(("action", "receipts"),), summary="opencode",
   examples=("qubit, list coding runs",),
   help="Recent coding-run receipts.")
_E("verified_list", "List verified skills",
   ("list verified skills", "verified skills", "curated skills"),
   "verified", ("skills",), args=(_C("action", ("list",), required=False),
                                  _S("status")),
   fixed=(("action", "list"),),
   examples=("qubit, list verified skills",),
   help="Curated skill inventory.")
_E("verified_show", "Show verified skill",
   ("show verified skill", "describe verified skill"),
   "verified", ("skills",), args=(_R("skill_id"),),
   fixed=(("action", "show"),),
   examples=("qubit, show verified skill",),
   help="One curated skill.")
_E("verified_recall", "Recall verified skills",
   ("recall skills for", "find skill for", "which skill handles"),
   "verified", ("skills",), args=(_R("query"),),
   fixed=(("action", "recall"),),
   examples=("qubit, recall skills for pdf reports",),
   help="Search curated skills.")
_E("verified_verdict", "Judge verified skill",
   ("verify skill", "refute skill", "judge skill"),
   "verified", ("skills",),
   args=(_R("skill_id"), _C("decision", ("verified", "refuted"))),
   fixed=(("action", "verdict"),), split="first-rest", confirm=True,
   examples=("qubit, verify skill",),
   help="Record a review verdict (confirmed).")
_E("verified_pin", "Pin verified skill",
   ("pin skill", "pin verified skill"),
   "verified", ("skills",), args=(_R("skill_id"),),
   fixed=(("action", "pin"),), confirm=True,
   examples=("qubit, pin skill",),
   help="Pin a skill (confirmed).")
_E("verified_unpin", "Unpin verified skill",
   ("unpin skill", "unpin verified skill"),
   "verified", ("skills",), args=(_R("skill_id"),),
   fixed=(("action", "unpin"),), confirm=True,
   examples=("qubit, unpin skill",),
   help="Unpin a skill (confirmed).")
_E("experiments_status", "Experiment status",
   ("experiment status", "experiments status", "trial status"),
   "experiments", ("experiments",), fixed=(("action", "status"),),
   examples=("qubit, experiment status",),
   help="Store status + journal verification.")
_E("experiments_list", "List experiments",
   ("list experiments", "show experiments", "list candidates",
    "experiment candidates"),
   "experiments", ("experiments",), fixed=(("action", "list"),),
   examples=("qubit, list experiments",),
   help="Experiments and candidates.")
_E("experiments_show", "Show experiment",
   ("show experiment", "describe experiment"),
   "experiments", ("experiments",), args=(_R("experiment_id"),),
   fixed=(("action", "show"),),
   examples=("qubit, show experiment",),
   help="One experiment + evaluation.")
_E("experiments_evaluate", "Evaluate experiment",
   ("evaluate experiment", "score experiment"),
   "experiments", ("experiments",), args=(_R("experiment_id"),),
   fixed=(("action", "evaluate"),),
   examples=("qubit, evaluate experiment",),
   help="Score an experiment (read-only).")
_E("experiments_register", "Register candidate",
   ("register candidate", "add experiment candidate"),
   "experiments", ("experiments",),
   args=(_C("role", ("champion", "challenger", "historical")), _R("label"),
         _S("model"), _S("description")),
   fixed=(("action", "register_candidate"),), confirm=True,
   examples=("qubit, register candidate",),
   help="Register a prompt/model candidate (confirmed).")
_E("experiments_start", "Start experiment",
   ("start experiment", "begin experiment"),
   "experiments", ("experiments",),
   args=(_R("champion"), _R("challenger")),
   fixed=(("action", "start"),), split="first-rest", confirm=True,
   examples=("qubit, start experiment",),
   help="Open an experiment for a pair (confirmed).")
_E("experiments_trial", "Start trial",
   ("start trial", "begin trial"),
   "experiments", ("experiments",),
   args=(_R("experiment_id"), _R("variant")),
   fixed=(("action", "trial"),), split="first-rest", confirm=True,
   examples=("qubit, start trial",),
   help="Open one running trial (confirmed).")
_E("experiments_finish", "Finish trial",
   ("finish trial", "complete trial", "fail trial"),
   "experiments", ("experiments",),
   args=(_R("trial_id"), _C("outcome", ("completed", "failed", "cancelled")),
         _S("note")),
   fixed=(("action", "finish_trial"),), split="first-rest", confirm=True,
   examples=("qubit, finish trial",),
   help="Close a trial with an outcome (confirmed).")
_E("experiments_signal", "Record signal",
   ("record signal", "add trial signal"),
   "experiments", ("experiments",),
   args=(_R("trial_id"), _C("kind", ("explicit", "implicit")), _S("value")),
   fixed=(("action", "signal"),), confirm=True,
   examples=("qubit, record signal",),
   help="Record an outcome signal 0..1 (confirmed).")
_E("experiments_abort", "Abort experiment",
   ("abort experiment", "stop experiment"),
   "experiments", ("experiments",), args=(_R("experiment_id"),),
   fixed=(("action", "abort"),), confirm=True,
   examples=("qubit, abort experiment",),
   help="Abort a running experiment (confirmed).")
_E("experiments_snapshot", "Snapshot experiments",
   ("snapshot experiments", "back up experiments"),
   "experiments", ("experiments",), fixed=(("action", "snapshot"),),
   confirm=True,
   examples=("qubit, snapshot experiments",),
   help="Write a state snapshot file (confirmed).")
_E("experiments_promote", "Promote experiment",
   ("promote experiment", "approve experiment"),
   "experiments", ("experiments",), args=(_R("experiment_id"),),
   fixed=(("action", "promote"),), confirm=True,
   examples=("qubit, promote experiment",),
   help="Owner-approved promotion (confirmed).")
_E("product_status", "Product status",
   ("product status", "installation overview", "install status",
    "product capabilities"),
   "product", ("product",), fixed=(("action", "status"),),
   examples=("qubit, product status",),
   help="Paths, capabilities, migration, boundary, service.")
_E("product_probe", "Probe install",
   ("probe install", "installation check", "prereq check"),
   "product", ("product",), fixed=(("action", "probe"),),
   examples=("qubit, probe install",),
   help="Dry-run prerequisite probe (read-only).")
_E("product_first_run", "First-run check",
   ("first run check", "beginner setup status"),
   "product", ("product",), fixed=(("action", "first_run"),),
   examples=("qubit, first run check",),
   help="Dry-run first-run flow (read-only).")
_E("product_install", "Install product",
   ("install product", "apply installation"),
   "product", ("product",), fixed=(("action", "install"),), confirm=True,
   examples=("qubit, install product",),
   help="Stage config/resources (confirmed).")
_E("product_migrate", "Migrate install",
   ("migrate install", "migrate legacy install"),
   "product", ("product",), fixed=(("action", "migrate"),), confirm=True,
   examples=("qubit, migrate install",),
   help="Backed-up legacy migration (confirmed).")
_E("product_rollback", "Roll back product",
   ("roll back product", "undo migration"),
   "product", ("product",), fixed=(("action", "rollback"),), confirm=True,
   examples=("qubit, roll back product",),
   help="Restore from backup (confirmed).")
_E("product_repair", "Repair install",
   ("repair install", "fix installation"),
   "product", ("product",), fixed=(("action", "repair"),), confirm=True,
   examples=("qubit, repair install",),
   help="Diagnostic repair (confirmed).")
_E("package_certify", "Certify release",
   ("certify release", "release checks", "package certification"),
   "package", ("package",), fixed=(("action", "certify"),),
   examples=("qubit, certify release",),
   help="Static release checks (read-only).")
_E("package_status", "Package status",
   ("package status", "distribution status", "release manifest"),
   "package", ("package",), fixed=(("action", "status"),),
   examples=("qubit, package status",),
   help="Manifest, doctor, upgrade/uninstall plans (read-only).")
_E("webui_status", "Dashboard status",
   ("dashboard status", "is dashboard running", "web dashboard",
    "dashboard running", "open the dashboard", "show the dashboard",
    "open dashboard page"),
   "webui", ("webui",), fixed=(("action", "status"),),
   summary="dashboard",
   examples=("qubit, open the dashboard",),
   help="Loopback probe with URL (token never in URL).")
_E("reports_list", "Show my reports",
   ("show my reports", "list my reports", "show reports", "my reports",
    "list reports", "what are my reports"),
   "reports", ("webui",),
   fixed=(("action", "list"),), summary="reports",
   examples=("qubit, show my reports",),
   help="Published report bundles, newest first (read-only).")
_E("frontier_chat", "Ask frontier model",
   ("ask frontier", "frontier completion", "ask cloud model"),
   "frontier_chat", ("model/moa/fallback", "-m/--model/--provider",),
   args=(_R("prompt"), _C("route", ("local", "frontier", "auto"),
                          required=False)),
   fixed=(("route", "local"),), confirm=True,
   examples=("qubit, ask frontier why is the sky blue",),
   help="Bounded gateway completion, local by default (confirmed).")
_E("frontier_show", "Show model entry",
   ("show model", "describe model", "model details"),
   "frontier_show", ("model/moa/fallback", "-m/--model/--provider",),
   args=(_R("model_id"),),
   examples=("qubit, show model",),
   help="One provider/model catalog entry.")
_E("commands_list", "What can you do",
   ("what can you do", "list voice commands", "voice commands",
    "help voice", "available commands"),
   "commands", ("chat",),
   examples=("qubit, what can you do",),
   help="Every voice command with help.")
_E("device_enroll", "Enroll this device",
   ("enroll this device", "authorize device", "trust this device"),
   "device_enroll", ("device",), args=(_S("model_alias"),),
   fixed=(("action", "enroll"),), confirm=True,
   examples=("qubit, enroll this device",),
   help="Record explicit device authorization (confirmed).")
_E("branding_plan", "Branding plan",
   ("branding plan", "migration plan", "rebrand status"),
   "branding", ("product",), fixed=(("action", "plan"),),
   examples=("qubit, branding plan",),
   help="Dry-run migration preview (read-only).")
_E("branding_verify", "Verify branding",
   ("verify branding", "check migration"),
   "branding", ("product",), fixed=(("action", "verify"),),
   examples=("qubit, verify branding",),
   help="Verify canonical paths (read-only).")
_E("branding_migrate", "Apply branding migration",
   ("apply migration", "run migration"),
   "branding", ("product",), fixed=(("action", "migrate"),), confirm=True,
   examples=("qubit, apply migration",),
   help="Backed-up migration apply (confirmed).")
_E("mic_test", "Microphone test",
   ("microphone test", "mic test", "check mic levels"),
   "audio_hw", ("status",), fixed=(("action", "mic_test"),),
   examples=("qubit, microphone test",),
   help="Real bounded audio-hardware probe (operator-gated live run).",
   summary="audio_hw")
_E("wake_status", "Wake-word status",
   ("wake word status", "is wake word ready", "wake status",
    "hands free status", "wake word"),
   "wake", ("status",),
   summary="wake",
   examples=("qubit, is wake word ready",),
   help="Spotter readiness or the exact fetch step (read-only).")
_E("asr_test", "Transcription test",
   ("transcription test", "test transcription"),
   "audio_hw", ("status",), fixed=(("action", "asr_test"),),
   examples=("qubit, transcription test",),
   help="Real bounded audio-hardware probe (operator-gated live run).",
   summary="audio_hw")
_E("tts_test", "Speech test",
   ("speech test", "test speech", "test voice output"),
   "audio_hw", ("status",), fixed=(("action", "tts_test"),),
   examples=("qubit, speech test",),
   help="Real bounded audio-hardware probe (operator-gated live run).",
   summary="audio_hw")
_E("enroll_voice", "Enroll my voice",
   ("enroll my voice", "voice enrollment", "learn my voice"),
   "audio_hw", ("status",), fixed=(("action", "enroll"),),
   examples=("qubit, enroll my voice",),
   help="Real bounded audio-hardware probe (operator-gated live run).",
   summary="audio_hw")
_E("verify_voice", "Verify my voice",
   ("verify my voice", "check my voice"),
   "audio_hw", ("status",), fixed=(("action", "verify"),),
   examples=("qubit, verify my voice",),
   help="Real bounded audio-hardware probe (operator-gated live run).",
   summary="audio_hw")
_E("roundtrip_test", "Voice self-test",
   ("voice self test", "test yourself", "self test"),
   "audio_hw", ("status",), fixed=(("action", "roundtrip"),),
   examples=("qubit, test yourself",),
   help="Real bounded audio-hardware probe (operator-gated live run).",
   summary="audio_hw")
_E("loop_start", "Start the voice loop",
   ("start listening", "start voice loop", "begin push to talk"),
   "loop", ("chat",), fixed=(("action", "start"),),
   examples=("qubit, start listening",),
   help="Loop how-to (you are inside it).",
   summary="guidance")
_E("profile_show", "Show config profile",
   ("show config profile kind", "describe profile kind"),
   "config_profiles", ("product",), args=(_R("kind"),),
   fixed=(("action", "show"),),
   examples=("qubit, show config profile kind audio",),
   help="One profile manifest.")
_E("profile_check", "Check profile",
   ("check profile", "validate profile", "profile gates"),
   "config_profiles", ("product",),
   args=(_R("kind"), _S("profile")),
   fixed=(("action", "check"),), split="first-rest",
   examples=("qubit, check profile",),
   help="Run every gate for a profile (read-only).")
_E("profile_select", "Select profile",
   ("select profile", "activate profile", "switch profile"),
   "config_profiles", ("product",),
   args=(_R("kind"), _R("profile")),
   fixed=(("action", "select"),), split="first-rest", confirm=True,
   examples=("qubit, select profile",),
   help="Activate a profile (confirmed).")
_E("profile_reset", "Reset profile",
   ("reset profile", "restore default profile"),
   "config_profiles", ("product",),
   args=(_R("kind"),),
   fixed=(("action", "reset"),), confirm=True,
   examples=("qubit, reset profile",),
   help="Reset one component to default (confirmed).")
_E("profile_upgrade", "Upgrade profile",
   ("upgrade profile", "update profile"),
   "config_profiles", ("product",),
   args=(_R("kind"), _S("profile")),
   fixed=(("action", "upgrade"),), split="first-rest", confirm=True,
   examples=("qubit, upgrade profile",),
   help="Upgrade a profile (confirmed).")
_E("profile_remove", "Remove profile",
   ("remove profile", "delete profile"),
   "config_profiles", ("product",),
   args=(_R("kind"), _R("profile")),
   fixed=(("action", "remove"),), split="first-rest", confirm=True,
   examples=("qubit, remove profile",),
   help="Remove a profile (confirmed).")
_E("profile_rollback", "Roll back profile",
   ("roll back profile", "undo profile change"),
   "config_profiles", ("product",),
   args=(_R("kind"),),
   fixed=(("action", "rollback"),), confirm=True,
   examples=("qubit, roll back profile",),
   help="Roll back a profile change (confirmed).")
# ------------------------------------------------------------------ node host
_E("node_host_status", "Paired node host status",
   ("node host status", "paired node status", "node host"),
   "node_host", (),
   fixed=(("action", "status"),),
   examples=("qubit, node host status",),
   help="Enrolled node count, host state, and supported capabilities.")
_E("node_host_list", "List paired nodes",
   ("list nodes", "list paired nodes", "show paired nodes"),
   "node_host", (),
   fixed=(("action", "list"),),
   examples=("qubit, list nodes",),
   help="Enrolled node records (secrets redacted).")
_E("node_host_show", "Show paired node",
   ("show node", "show paired node", "node details"),
   "node_host", (),
   args=(_R("node_id"),),
   fixed=(("action", "show"),),
   examples=("qubit, show node node_abc123",),
   help="One enrolled node record (secrets redacted).")
_E("node_host_capabilities", "Node capabilities",
   ("node capabilities", "show node capabilities",
    "what can this node do"),
   "node_host", (),
   args=(_R("node_id"),),
   fixed=(("action", "capabilities"),),
   examples=("qubit, node capabilities node_abc123",),
   help="Declared capabilities with honest typed availability.")
_E("node_host_enroll", "Enroll paired node",
   ("enroll node", "register node", "pair a node", "add paired node"),
   "node_host", (),
   args=(_R("name"), _R("endpoint")),
   fixed=(("action", "enroll"),), confirm=True, split="first-rest",
   examples=("qubit, enroll node desk http://127.0.0.1:8443/",),
   help="Record a paired node endpoint (confirmed; no listener opened).")
_E("node_host_revoke", "Revoke paired node",
   ("revoke node", "remove paired node", "unpair node"),
   "node_host", (),
   args=(_R("node_id"),),
   fixed=(("action", "revoke"),), confirm=True,
   examples=("qubit, revoke node node_abc123",),
   help="Confirmation-gated removal of an enrolled node.")
_E("node_host_dispatch", "Dispatch node capability",
   ("dispatch node capability", "run node capability",
    "call node capability"),
   "node_host", (),
   args=(_R("node_id"), _R("capability")),
   fixed=(("action", "dispatch"),), split="first-rest",
   examples=("qubit, dispatch node capability node_abc123 device.inventory",),
   help="Route a capability; only local-safe reads execute, everything "
        "else is the module's typed refusal (never a shell).")
# ------------------------------------------------------------- task ledger
_E("task_ledger_status", "Task ledger status",
   ("task ledger status", "background task status", "task ledger"),
   "task_ledger", (),
   fixed=(("action", "status"),),
   examples=("qubit, task ledger status",),
   help="Ledger counts by source and canonical state.")
_E("task_ledger_list", "List background tasks",
   ("list background tasks", "list ledger tasks", "show task ledger"),
   "task_ledger", (),
   args=(_S("source"), _S("state"), _I("limit", required=False)),
   fixed=(("action", "list"),),
   examples=("qubit, list background tasks",),
   help="Merged normalized tasks across the real stores, newest-first.")
_E("task_ledger_show", "Show background task",
   ("show ledger task", "show background task", "task ledger show"),
   "task_ledger", (),
   args=(_R("task_id"), _S("source")),
   fixed=(("action", "show"),),
   examples=("qubit, show background task kanban:1",),
   help="One normalized task plus its redacted raw source record.")
_E("task_ledger_timeline", "Task timeline",
   ("task timeline", "ledger timeline", "show task timeline"),
   "task_ledger", (),
   args=(_R("task_id"), _S("source")),
   fixed=(("action", "timeline"),),
   examples=("qubit, task timeline kanban:1",),
   help="Ordered real events correlated to one task (never fabricated).")
# -------------------------------------------------------------- media gen
_E("media_gen_status", "Media generation status",
   ("media status", "media generation status", "image generation status"),
   "media_gen", (),
   fixed=(("action", "status"),),
   examples=("qubit, media status",),
   help="Provider/kind readiness derived from credential references.")
_E("media_gen_catalog", "Media provider catalog",
   ("media catalog", "list media providers", "media providers"),
   "media_gen", (),
   fixed=(("action", "catalog"),),
   examples=("qubit, media catalog",),
   help="Flat honest {kind, provider, state, reason} rows.")
_E("media_gen_generate", "Generate media",
   ("generate image", "generate music", "generate video",
    "generate speech", "create an image"),
   "media_gen", (),
   args=(_C("kind", ("image", "music", "video", "speech")),
         _R("prompt"), _S("provider")),
   fixed=(("action", "generate"),), confirm=True,
   examples=("qubit, generate image a red fox",),
   help="Validate, gate and (only with a real transport) generate; "
        "costly kinds require confirmation.")
# ------------------------------------------------------------ local models
_E("local_models_status", "Local models status",
   ("local models status", "local model status", "local llm status"),
   "local_models", (),
   fixed=(("action", "status"),),
   examples=("qubit, local models status",),
   help="Local runtime state, honestly derived from real Qubit state.")
_E("local_models_hardware", "Local model hardware",
   ("local model hardware", "gpu hardware status", "local hardware status"),
   "local_models", (),
   fixed=(("action", "hardware"),),
   examples=("qubit, local model hardware",),
   help="GPU / VRAM / accelerator inventory from the real gauges.")
_E("local_models_catalog", "Local model catalog",
   ("local model catalog", "list local models", "local models"),
   "local_models", (),
   fixed=(("action", "catalog"),),
   examples=("qubit, local model catalog",),
   help="Known/installed local models from real config + runtime state.")
_E("local_models_jobs", "Local model jobs",
   ("local model jobs", "model install jobs", "local download jobs"),
   "local_models", (),
   fixed=(("action", "jobs"),),
   examples=("qubit, local model jobs",),
   help="Install/download job history (typed EMPTY; no such store).")
_E("local_models_runtime_install", "Install local runtime",
   ("install local runtime", "install model runtime"),
   "local_models", (),
   args=(_S("backend"),),
   fixed=(("action", "runtime_install"),),
   examples=("qubit, install local runtime",),
   help="Typed gate: pinned-runtime install is brokered; no download.")
_E("local_models_download", "Download local model",
   ("download local model", "download a local model"),
   "local_models", (),
   args=(_R("model"),),
   fixed=(("action", "download"),),
   examples=("qubit, download local model qubit-brain4",),
   help="Typed gate: model download is brokered; nothing is fetched.")
_E("local_models_sideload", "Sideload local model",
   ("sideload local model", "register local gguf", "load local gguf"),
   "local_models", (),
   args=(_R("path"),),
   fixed=(("action", "sideload"),),
   examples=("qubit, sideload local model /tmp/model.gguf",),
   help="Typed gate: registering an on-disk GGUF is brokered.")
_E("local_models_server", "Local model server",
   ("start local model server", "stop local model server",
    "local model server"),
   "local_models", (),
   args=(_C("server_action", ("start", "stop")),),
   fixed=(("action", "server"),),
   examples=("qubit, start local model server",),
   help="Typed gate: local server start/stop is brokered; nothing runs.")
_E("local_models_eject", "Eject local model",
   ("eject local model", "unload local model"),
   "local_models", (),
   args=(_R("model"),),
   fixed=(("action", "eject"),),
   examples=("qubit, eject local model qubit-brain4",),
   help="Typed gate: ejecting a resident model is brokered.")
_E("local_models_activate", "Activate local model",
   ("activate local model", "set local model default", "use local model"),
   "local_models", (),
   args=(_R("model"),),
   fixed=(("action", "activate"),),
   examples=("qubit, activate local model qubit-brain4",),
   help="Typed gate: making a model the default is a brokered write.")
_E("local_models_quickstart", "Local model quickstart",
   ("local model quickstart", "set up local model",
    "quickstart local model"),
   "local_models", (),
   args=(_S("model"),),
   fixed=(("action", "quickstart"),),
   examples=("qubit, local model quickstart",),
   help="Typed gate: one-click runtime+model setup is brokered.")


# --- OpenClaw backends: plugin host / inbound channels / ACP harness client
# (Packet 8U). Every operation executes the SAME backend module function the
# CLI and dashboard call; raw-byte receive is a typed refusal guard.
from .acp_client import HARNESSES as _AC_HARNESSES  # noqa: E402
from .channels_inbound import CHANNELS as _CI_CHANNELS  # noqa: E402

_E("plugins_status", "Plugin runtime status",
   ("plugin runtime status", "plugin host status", "plugins status",
    "plugin status"),
   "plugin_runtime", ("plugins-install",),
   fixed=(("action", "status"),),
   examples=("qubit, plugin runtime status",),
   help="In-process host state; external provider runtime is unavailable.")
_E("plugins_list", "List plugins",
   ("list plugins", "plugin list", "show plugins", "list installed plugins"),
   "plugin_runtime", ("plugins-install",),
   fixed=(("action", "list"),),
   examples=("qubit, list plugins",),
   help="Registered manifests + built-ins (redacted).")
_E("plugins_show", "Show plugin",
   ("show plugin", "inspect plugin", "plugin details"),
   "plugin_runtime", ("plugins-install",),
   args=(_R("plugin_id"),), fixed=(("action", "show"),),
   examples=("qubit, show plugin builtin.echo",),
   help="One plugin record (redacted).")
_E("plugins_register", "Register plugin manifest",
   ("register plugin", "register plugin manifest", "record plugin manifest"),
   "plugin_runtime", ("plugins-install",),
   args=(_R("manifest"),), fixed=(("action", "register"),), confirm=True,
   split="path",
   examples=("qubit, register plugin manifest /tmp/acme.json",),
   help="Record a validated manifest (metadata only; confirmed).")
_E("plugins_invoke", "Invoke plugin",
   ("invoke plugin", "run plugin", "execute plugin"),
   "plugin_runtime", ("plugins-install",),
   args=(_R("plugin_id"), _S("text")), fixed=(("action", "invoke"),),
   confirm=True, split="first-rest",
   examples=("qubit, invoke plugin builtin.echo hello",),
   help="In-process handlers only; confirmed, never code from a manifest.")
_E("plugins_unregister", "Unregister plugin",
   ("unregister plugin", "deregister plugin", "remove plugin registration"),
   "plugin_runtime", ("plugins-install",),
   args=(_R("plugin_id"),), fixed=(("action", "unregister"),), confirm=True,
   examples=("qubit, unregister plugin acme.tool",),
   help="Remove a registered manifest (built-ins refused; confirmed).")
_E("plugins_doctor", "Plugin runtime health",
   ("plugin doctor", "plugin health", "plugin runtime health"),
   "plugin_runtime", ("plugins-install",),
   fixed=(("action", "doctor"),),
   examples=("qubit, plugin doctor",),
   help="Store health, handlers, and the emergency-stop latch.")
_E("channels_status", "Inbound channel status",
   ("channels status", "channel inbound status", "inbound channel status"),
   "channels", (),
   fixed=(("action", "status"),),
   examples=("qubit, channels status",),
   help="Listener NONE; connected is never fabricated.")
_E("channels_catalog", "Channel catalog",
   ("list channels", "channel catalog", "list inbound channels",
    "channels catalog"),
   "channels", (),
   fixed=(("action", "catalog"),),
   examples=("qubit, list channels",),
   help="Every known channel with an honest typed state.")
_E("channels_accounts", "Configured channels",
   ("list configured channels", "channel accounts", "configured channels",
    "channels accounts"),
   "channels", (),
   fixed=(("action", "accounts"),),
   examples=("qubit, list configured channels",),
   help="Configured references only (never values).")
_E("channels_configure", "Configure inbound channel",
   ("configure channel", "add inbound channel", "set up channel"),
   "channels", (),
   args=(_C("channel", tuple(_CI_CHANNELS)), _R("secret_ref")),
   fixed=(("action", "configure"),), confirm=True, split="first-rest",
   examples=("qubit, configure channel discord env:q8u_secret",),
   help="Record an env:/file: reference (never a value; confirmed).")
_E("channels_unconfigure", "Unconfigure inbound channel",
   ("unconfigure channel", "remove inbound channel"),
   "channels", (),
   args=(_C("channel", tuple(_CI_CHANNELS)),),
   fixed=(("action", "unconfigure"),), confirm=True,
   examples=("qubit, unconfigure channel discord",),
   help="Remove a configured channel reference (confirmed).")
_E("channels_receive_refusal", "Receive channel payload (refusal)",
   ("receive channel payload", "ingest channel delivery",
    "receive an inbound channel payload"),
   "channels", (),
   fixed=(("action", "receive"),),
   summary="guidance",
   examples=("qubit, receive an inbound channel payload",),
   help="Typed VOICE_BYTES_UNSUPPORTED: raw payloads cannot be spoken.")
_E("channels_doctor", "Inbound channel health",
   ("channels doctor", "channel health", "inbound channel health"),
   "channels", (),
   fixed=(("action", "doctor"),),
   examples=("qubit, channels doctor",),
   help="Store/listener/latch diagnostics (read-only).")
_E("acp_client_status", "Harness client status",
   ("harness status", "acp client status", "agent harness status"),
   "acp_client", (),
   fixed=(("action", "status"),),
   examples=("qubit, harness status",),
   help="Configured/present harnesses; no live sessions ever claimed.")
_E("acp_client_catalog", "Harness catalog",
   ("list harnesses", "harness catalog", "list agent harnesses"),
   "acp_client", (),
   fixed=(("action", "catalog"),),
   examples=("qubit, list harnesses",),
   help="PATH presence + configured state (honest).")
_E("acp_client_configure", "Configure harness",
   ("configure harness", "register harness credential"),
   "acp_client", (),
   args=(_C("harness", tuple(_AC_HARNESSES)), _S("credential_ref")),
   fixed=(("action", "configure"),), confirm=True, split="first-rest",
   examples=("qubit, configure harness claude-code env:acp_key",),
   help="Record a credential reference (never a value; confirmed).")
_E("acp_client_unconfigure", "Unconfigure harness",
   ("unconfigure harness", "remove harness credential"),
   "acp_client", (),
   args=(_C("harness", tuple(_AC_HARNESSES)),),
   fixed=(("action", "unconfigure"),), confirm=True,
   examples=("qubit, unconfigure harness claude-code",),
   help="Remove a configured harness (confirmed).")
_E("acp_client_handshake", "Harness handshake",
   ("harness handshake", "acp handshake", "initialize harness"),
   "acp_client", (),
   args=(_C("harness", tuple(_AC_HARNESSES)),),
   fixed=(("action", "handshake"),), confirm=True,
   examples=("qubit, harness handshake claude-code",),
   help="Shape ACP initialize; typed unavailable, never executed.")
_E("acp_client_send", "Send to harness",
   ("send to harness", "acp send", "prompt harness"),
   "acp_client", (),
   args=(_C("harness", tuple(_AC_HARNESSES)), _R("prompt")),
   fixed=(("action", "send"),), confirm=True, split="first-rest",
   examples=("qubit, send to harness claude-code hello",),
   help="Typed ACP_REFUSED: Qubit never executes a harness.")
_E("acp_client_receipts", "Harness receipts",
   ("harness receipts", "acp receipts", "harness log"),
   "acp_client", (),
   args=(_S("harness"),), fixed=(("action", "receipts"),),
   examples=("qubit, harness receipts",),
   help="Redacted ACP receipts (bounded tail).")


# --- OpenClaw delta Wave 3 (D3/D4/D20/D21/D24): node-host file transfer,
# active-recall memory, bounded document extraction, structured question
# prompts, and Qubit-safe session sharing. Every entry executes the SAME
# backend module function the CLI and dashboard call; raw-byte/secret/nested
# ops get typed guards instead of binding the unsafe payload.

_E("transfers_status", "File transfer status",
   ("transfer status", "file transfer status", "node transfer status"),
   "file_transfer", (),
   fixed=(("action", "status"),),
   examples=("qubit, transfer status",),
   help="Node transport inventory; remote transport is UNAVAILABLE.")
_E("transfers_list", "List node files",
   ("list node files", "list transfer folder", "list node directory"),
   "file_transfer", (),
   args=(_R("path"),), fixed=(("action", "list"),), split="path",
   examples=("qubit, list node files /tmp/work",),
   help="Bounded, confined directory listing for a local node.")
_E("transfers_fetch", "Fetch file chunk",
   ("fetch file chunk", "download file chunk", "fetch node file"),
   "file_transfer", (),
   args=(_R("path"),), fixed=(("action", "fetch"),), confirm=True,
   split="path",
   examples=("qubit, fetch file chunk /tmp/work/notes.txt",),
   help="Confirmed bounded chunk read; summary reports size/digest only.")
_E("transfers_plan", "Plan file transfer",
   ("plan file transfer", "plan file stream", "plan file download"),
   "file_transfer", (),
   args=(_R("path"),), fixed=(("action", "plan"),), confirm=True,
   split="path",
   examples=("qubit, plan file transfer /tmp/work/notes.txt",),
   help="Confirmed resumable plan (digests only; no payload).")
_E("transfers_resume", "Resume file transfer",
   ("resume file transfer", "resume transfer"),
   "file_transfer", (),
   args=(_R("transfer_id"),), fixed=(("action", "resume"),),
   examples=("qubit, resume file transfer xfer_abc",),
   help="Validate a persisted plan and return the next chunk spec.")

_E("recall_status", "Active recall status",
   ("active recall status", "recall status", "active memory status"),
   "active_memory", (),
   fixed=(("action", "status"),),
   examples=("qubit, active recall status",),
   help="Policy/store health; never fabricates a count.")
_E("recall_policy_get", "Show recall policy",
   ("show recall policy", "active memory policy", "recall policy status"),
   "active_memory", (),
   fixed=(("action", "policy_get"),),
   examples=("qubit, show recall policy",),
   help="Persisted active-recall policy (redacted).")
_E("recall_policy_set", "Set recall policy",
   ("set recall policy", "turn active recall on", "turn active recall off",
    "recall policy on", "recall policy off"),
   "active_memory", (),
   args=(_C("enabled", ("on", "off")),
         _I("max_items", required=False),
         _C("session_scoped", ("session", "global"), required=False)),
   fixed=(("action", "policy_set"),), confirm=True,
   examples=("qubit, turn active recall on",),
   help="Confirmed bounded policy update (enable/disable, cap, scope).")
_E("recall_query", "Active recall",
   ("recall memories", "search active memory", "active memory recall"),
   "active_memory", (),
   args=(_R("query"),), fixed=(("action", "recall"),),
   examples=("qubit, recall memories invoices",),
   help="Policy-scoped, bounded, redacted recall results.")
_E("recall_resolve", "Resolve recall context",
   ("resolve recall context", "active recall context",
    "resolve active memory"),
   "active_memory", (),
   args=(_R("query"),), fixed=(("action", "resolve"),),
   examples=("qubit, resolve recall context invoices",),
   help="Bounded prompt-context blocks from active recall.")
_E("recall_escalate", "Escalate memory",
   ("escalate memory", "promote memory"),
   "active_memory", (),
   args=(_I("memory_id"), _R("reason")), fixed=(("action", "escalate"),),
   confirm=True, split="int-rest",
   examples=("qubit, escalate memory 3 reason important",),
   help="Confirmed one-step tier escalation toward core.")

_E("documents_status", "Document tools status",
   ("document status", "document tools status",
    "document extraction status"),
   "doc_extract", (),
   fixed=(("action", "status"),),
   examples=("qubit, document status",),
   help="Supported kinds and optional PDF backend state.")
_E("documents_kinds", "Document kinds",
   ("list document kinds", "supported documents", "document kinds"),
   "doc_extract", (),
   fixed=(("action", "kinds"),),
   examples=("qubit, list document kinds",),
   help="Kinds supported for bounded extraction.")
_E("documents_extract", "Extract document",
   ("extract document", "read document", "extract pdf"),
   "doc_extract", (),
   args=(_R("path"),), fixed=(("action", "extract"),), confirm=True,
   split="path",
   examples=("qubit, extract document /tmp/work/notes.txt",),
   help="Confirmed bounded, redacted extraction inside approved roots.")

_E("questions_status", "Question prompt status",
   ("question status", "question prompt status", "ask user status"),
   "question_prompt", (),
   fixed=(("action", "status"),),
   examples=("qubit, question status",),
   help="Pending count, kinds, TTL, and latch.")
_E("questions_ask", "Ask a question",
   ("ask a question", "ask me a question", "create a question prompt"),
   "question_prompt", (),
   args=(_R("question"),), fixed=(("action", "ask"),), confirm=True,
   examples=("qubit, ask a question which branch",),
   help="Confirmed single free-text question contract.")
_E("questions_pending", "List pending questions",
   ("list pending questions", "pending questions", "list question prompts"),
   "question_prompt", (),
   fixed=(("action", "pending"),),
   examples=("qubit, list pending questions",),
   help="Unanswered, unexpired prompt contracts.")

_E("shares_status", "Session share status",
   ("share status", "session share status", "sharing status"),
   "session_share", (),
   fixed=(("action", "status"),),
   examples=("qubit, share status",),
   help="Share-store health and counts (never tokens).")
_E("shares_publish", "Publish session share",
   ("publish session share", "share session", "publish a session share"),
   "session_share", (),
   args=(_R("session_id"),), fixed=(("action", "publish"),), confirm=True,
   examples=("qubit, share session 12",),
   help="Confirmed redacted transcript snapshot; token shown once.")
_E("shares_list", "List session shares",
   ("list session shares", "list shares", "show session shares"),
   "session_share", (),
   fixed=(("action", "list"),),
   examples=("qubit, list session shares",),
   help="Redacted share records; no tokens or hashes.")
_E("shares_revoke", "Revoke session share",
   ("revoke session share", "revoke share", "unshare session"),
   "session_share", (),
   args=(_R("share_id"),), fixed=(("action", "revoke"),), confirm=True,
   examples=("qubit, revoke share shr_abc",),
   help="Confirmed tombstone; record retained.")
_E("shares_purge", "Purge expired shares",
   ("purge expired shares", "clean up session shares", "purge shares"),
   "session_share", (),
   fixed=(("action", "purge"),), confirm=True,
   examples=("qubit, purge expired shares",),
   help="Confirmed removal of expired share records.")


# ---------------------------- integration wave: Agents 5-10 real backends
# One op id per capability, exposed to voice AND (Tier 1) the dashboard
# console; reads execute immediately, mutations stage the shared journal
# confirmation. Every backend is the same module function the CLI calls.
_E("browser_profiles", "Browser profiles",
   ("browser profiles", "list browser profiles", "managed browser profiles"),
   "browser_profiles", ("browser",), fixed=(("action", "list"),),
   examples=("qubit, list browser profiles",),
   help="Qubit-managed dedicated profiles only.")
_E("browser_profile_create", "Create browser profile",
   ("create browser profile", "new browser profile"),
   "browser_profiles", ("browser",), args=(_R("profile"),),
   fixed=(("action", "create"),), confirm=True,
   examples=("qubit, create browser profile work",),
   help="Create a Qubit-dedicated profile (confirmed).")
_E("browser_profile_open", "Open browser profile",
   ("open browser profile", "launch browser profile"),
   "browser_profiles", ("browser",), args=(_R("profile"),),
   fixed=(("action", "open"),), confirm=True,
   examples=("qubit, open browser profile work",),
   help="Launch via the approved launcher seam only (confirmed).")
_E("browser_profile_close", "Close managed profile",
   ("close managed profile", "stop managed browser profile"),
   "browser_profiles", ("browser",), args=(_R("profile"),),
   fixed=(("action", "close"),), confirm=True,
   examples=("qubit, close managed profile work",),
   help="Close a Qubit-managed profile (confirmed).")
_E("browser_profile_remove", "Remove browser profile",
   ("remove browser profile", "delete managed browser profile"),
   "browser_profiles", ("browser",), args=(_R("profile"),),
   fixed=(("action", "remove"),), confirm=True,
   examples=("qubit, remove browser profile work",),
   help="Delete a Qubit-managed profile (confirmed).")
_E("computer_screen", "Control UI layout",
   ("screen layout", "show control ui layout", "control ui panes"),
   "computer_screen", ("computer-use",), fixed=(("action", "status"),),
   examples=("qubit, show control ui layout",),
   help="Read the persisted Control UI arrangement.")
_E("computer_screen_panes", "Control UI pane catalog",
   ("screen pane catalog", "list screen panes", "control ui pane catalog"),
   "computer_screen", ("computer-use",), fixed=(("action", "panes"),),
   examples=("qubit, list screen panes",),
   help="Bounded pane/layout catalog (read-only).")
_E("computer_screen_arrange", "Arrange Control UI",
   ("arrange screen", "arrange control ui", "set screen panes"),
   "computer_screen", ("computer-use",),
   args=(_R("panes"),),
   fixed=(("action", "arrange"),), confirm=True,
   examples=("qubit, arrange screen main board",),
   help="Persist a bounded Control UI arrangement (confirmed).")
_E("local_models_fallback", "Model fallback chain",
   ("model fallback chain", "local model fallback", "list fallbacks"),
   "local_models", ("local-models",), fixed=(("action", "fallback"),),
   examples=("qubit, model fallback chain",),
   help="Ordered configured fallback chain (read-only).")
_E("local_models_resolve", "Resolve model fallback",
   ("resolve model fallback", "pick fallback model"),
   "local_models", ("local-models",), args=(_S("model"),),
   fixed=(("action", "resolve_fallback"),),
   examples=("qubit, resolve model fallback",),
   help="Pick the requested or first available loopback model.")
_E("gateway_supervision_request", "Request gateway supervision",
   ("request gateway supervision", "supervise the gateway"),
   "gateway", ("gateway",),
   args=(_S("gateway_id"),
         _S("target_action")),
   fixed=(("action", "supervision_request"),), confirm=True, split="first-rest",
   examples=("qubit, request gateway supervision team start",),
   help="Approval-gated supervised transition (confirmed).")
_E("gateway_supervision_revert", "Revert gateway supervision",
   ("revert gateway supervision", "undo gateway supervision"),
   "gateway", ("gateway",), args=(_R("gateway_id"),),
   fixed=(("action", "supervision_revert"),), confirm=True,
   examples=("qubit, revert gateway supervision team",),
   help="Restore the pre-supervision process state (confirmed).")
_E("gateway_discovery", "Gateway discovery",
   ("gateway discovery", "discover gateways", "find gateways"),
   "gateway", ("gateway",), fixed=(("action", "discovery"),),
   examples=("qubit, gateway discovery",),
   help="Read-only gateways/peers/providers discovery.")
_E("pairing_setup_code", "Pairing setup code",
   ("generate pairing setup code", "new setup code", "pairing setup code"),
   "pairing", ("pairing",), args=(_S("label"), _S("scopes")),
   fixed=(("action", "setup_code"),), confirm=True,
   examples=("qubit, generate pairing setup code",),
   help="One-time code + join URL/QR, shown once (confirmed).")
_E("pairing_accept", "Accept setup code",
   ("accept setup code", "redeem pairing setup code"),
   "pairing", ("pairing",), args=(_R("setup"),),
   fixed=(("action", "accept"),), confirm=True,
   examples=("qubit, accept setup code AB12CD",),
   help="Redeem a setup code and bind the DM account (confirmed).")
_E("runtime_recover", "Recover runtime",
   ("recover runtime", "restore runtime install"),
   "runtime", ("runtime",), args=(_R("name"),),
   fixed=(("action", "recover"),), confirm=True,
   examples=("qubit, recover runtime opencode-binary",),
   help="Reconcile/re-stage from verified local state (confirmed).")
_E("runtime_repair", "Repair runtime state",
   ("repair runtime", "fix runtime state"),
   "runtime", ("runtime",), fixed=(("action", "repair"),), confirm=True,
   examples=("qubit, repair runtime",),
   help="Bounded state repair; never downloads (confirmed).")
_E("runtime_history", "Runtime update history",
   ("runtime update history", "show runtime history"),
   "runtime", ("runtime",), args=(_S("name"),),
   fixed=(("action", "history"),),
   examples=("qubit, runtime update history",),
   help="Append-only bounded local update history.")
_E("runtime_discovery", "Runtime discovery",
   ("runtime discovery", "discover runtime", "runtime inventory discovery"),
   "runtime", ("runtime",), fixed=(("action", "discovery"),),
   examples=("qubit, runtime discovery",),
   help="Read-only gateway/peer/provider discovery counts.")
_E("runtime_marketplace", "Runtime marketplace",
   ("runtime marketplace", "show runtime marketplace", "plugin marketplace"),
   "runtime", ("runtime",), fixed=(("action", "marketplace"),),
   examples=("qubit, runtime marketplace",),
   help="Local reviewed plugins only; remote installs refused.")
_E("runtime_update_report", "Runtime update report",
   ("runtime update report", "show runtime update report",
    "runtime recovery report"),
   "runtime", ("runtime",), fixed=(("action", "update_report"),),
   examples=("qubit, runtime update report",),
   help="Status + history + recovery modes (read-only).")
_E("egress_status", "Egress status",
   ("egress status", "egress guard status", "network egress status"),
   "egress", ("egress",), fixed=(("action", "status"),),
   examples=("qubit, egress status",),
   help="Loopback-only egress guard state.")
_E("egress_check", "Egress check",
   ("egress check", "check egress host", "is host allowed"),
   "egress", ("egress",), args=(_R("host"),),
   fixed=(("action", "check"),),
   examples=("qubit, egress check api.example.com",),
   help="Local allowlist check; sends no traffic.")
_E("substitute_egress", "Substitute egress secrets",
   ("substitute egress secrets", "apply egress secret substitution",
    "materialize egress request"),
   "egress", ("egress",), args=(_S("host"), _R("request")),
   fixed=(("action", "substitute"),), confirm=True, split="first-rest",
   examples=("qubit, substitute egress secrets api.example.com "
             "authorization {{secret:api}}",),
   help="Confirmed allowlisted-only substitution; value never receipted.")
_E("policy_conformance", "Policy conformance",
   ("policy conformance", "check policy conformance", "conformance doctor"),
   "plugin_runtime", ("policy",), args=(_S("workspace"),),
   fixed=(("action", "policy_doctor"),),
   examples=("qubit, policy conformance",),
   help="Workspace policy conformance findings (read-only).")
_E("policy_attest", "Policy attestation",
   ("policy attest", "attest conformance", "attest policy"),
   "plugin_runtime", ("policy",), args=(_S("workspace"), _S("note")),
   fixed=(("action", "policy_attest"),), confirm=True,
   examples=("qubit, policy attest",),
   help="Content-addressed conformance attestation (confirmed).")
_E("marketplace_discovery", "Marketplace discovery",
   ("marketplace discovery", "discover marketplace", "browse marketplace"),
   "plugin_runtime", ("marketplace",), args=(_S("source"),),
   fixed=(("action", "marketplace_discover"),),
   examples=("qubit, marketplace discovery",),
   help="Local reviewed sources only; remote fetches refused.")
_E("hooks_emit", "Emit hook event",
   ("emit hook event", "emit lifecycle event"),
   "hooks", ("hooks",), args=(_R("event"),),
   fixed=(("action", "emit"),),
   examples=("qubit, emit lifecycle event task.completed",),
   help="Core lifecycle emission; shell never executes.")
_E("skills_workshop", "Skill workshop",
   ("skill workshop", "workshop collection", "show skill workshop"),
   "workshop", ("skills/workshop",), fixed=(("action", "collection"),),
   examples=("qubit, skill workshop",),
   help="Quarantined collection + contracts (read-only).")
_E("skills_workshop_backups", "Workshop backups",
   ("workshop backups", "list workshop backups", "skill backups"),
   "workshop", ("skills/workshop",), fixed=(("action", "backups"),),
   examples=("qubit, workshop backups",),
   help="Digest-signed bounded backups (read-only).")
_E("skills_workshop_curator", "Workshop curator",
   ("workshop curator", "workshop curator status"),
   "workshop", ("skills/workshop",), fixed=(("action", "curator"),),
   examples=("qubit, workshop curator",),
   help="Curator status over the verified store (read-only).")
_E("skills_workshop_backup", "Back up workshop",
   ("back up workshop", "create workshop backup", "backup skills"),
   "workshop", ("skills/workshop",), args=(_S("label"),),
   fixed=(("action", "backup"),), confirm=True,
   examples=("qubit, back up workshop",),
   help="Create a signed workshop backup (confirmed).")
_E("skills_workshop_restore", "Restore workshop",
   ("restore workshop backup", "restore workshop", "restore skill backup"),
   "workshop", ("skills/workshop",), args=(_R("backup_id"),),
   fixed=(("action", "restore"),), confirm=True,
   examples=("qubit, restore workshop backup abc123",),
   help="Human-only restore with pre-restore protection (confirmed).")
_E("skills_workshop_rollback", "Rollback workshop",
   ("rollback workshop", "undo workshop restore"),
   "workshop", ("skills/workshop",), args=(_S("backup_id"),),
   fixed=(("action", "rollback"),), confirm=True,
   examples=("qubit, rollback workshop",),
   help="Revert to the previous workshop snapshot (confirmed).")
_E("usage_summary", "Usage analytics",
   ("usage analytics", "show usage", "token usage", "cost usage"),
   "usage", ("usage",), args=(_S("limit"),),
   examples=("qubit, usage analytics",),
   help="Token/cost usage aggregation (read-only).")
_E("evaluation_telemetry", "Telemetry status",
   ("telemetry status", "evaluation telemetry", "check telemetry"),
   "telemetry", ("telemetry",),
   fixed=(("action", "status"),),
   examples=("qubit, telemetry status",),
   help="Opt-in only; typed DISABLED with no transport by default.")
_E("receipts_ledger", "Execution receipts",
   ("execution receipts", "verify receipts", "receipt ledger"),
   "receipts_ledger", ("receipts",),
   examples=("qubit, execution receipts",),
   help="Execution-identity receipt ledger + verification.")
_E("logbook_entries", "Work logbook",
   ("work logbook", "show logbook", "logbook entries"),
   "logbook", ("logbook",), fixed=(("action", "entries"),),
   examples=("qubit, work logbook",),
   help="Append-only work journal (read-only).")
_E("logbook_summary", "Logbook summary",
   ("logbook summary", "summarize logbook"),
   "logbook", ("logbook",), fixed=(("action", "summary"),),
   examples=("qubit, logbook summary",),
   help="Windowed logbook aggregation (read-only).")



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
    "H-sqlite-txn": "kanban_claim",
    "H-stream": "roundtrip_test",
    "H-stt": "asr_test",
    "H-tts": "tts_test",
    "H-wake": "wake_status",
    "H-browser": "browser_tabs",
    "H-files": "files_inspect",
    "H-terminal": "tui_help",
    "H-acp": "acp",
    "H-sessions": "sessions",
    "H-gateway": "gateway",
    "H-cloudmem": "memory_search",
    "H-desktop": "ghost_status",
    "H-cloudbrowser": "browser_status",
    "H-installer": "package_status",
    "O-frames": "gateway",
    "O-browser": "browser_tabs",
    "O-board": "kanban_open",
    "O-docker": "runtime_status",
    "O-clawhub": "plugins_install",
}


# ---------------------------------------------------------------- CLI parity
# Every top-level CLI command family maps to voice registry entry ids, a
# Tier-2 dashboard extra id (dashboard_ops.EXTRA_OPS), or a tested reason
# why voice is impossible. The coverage test fails on any unmapped family.
# Families marked here with voice ids execute the SAME backend module
# function the CLI calls (never a voice-only path); guidance entries speak
# the exact CLI spelling for operator-only surfaces.
CLI_COVERAGE = {
    # loop primitives: you are inside the loop; typed equivalents exist
    "run": ("loop_start",),
    "once": ("loop_start",),
    "task": ("task_run", "task_run_current"),
    "schedules": (
        "schedules_create",
        "schedules_list",
        "schedules_show",
        "schedules_pause",
        "schedules_resume",
        "schedules_delete",
        "schedules_history",
        "schedules_run_due",
        "automation_status",
        "automation_orders",
        "automation_heartbeats",
        "automation_flows",
        "automation_scenarios",
        "automation_yield_pending",
        "automation_order_add",
        "automation_order_enable",
        "automation_order_disable",
        "automation_order_remove",
        "automation_heartbeat_add",
        "automation_flow_add",
        "automation_flow_run",
        "automation_qa_add",
        "automation_qa_run",
        "automation_yield_handoff",
        "automation_yield_resume",
    ),
    "files": ("files_inspect", "files_list", "files_read", "file_patch_rollback"),
    "git": ("git_status", "git_log", "git_diff", "git_show"),
    "ide": ("ide_status", "ide_open"),
    "voice": ("commands_list",),
    "completion": ("completions",),
    "status": ("status",),
    "auth": ("auth_status", "auth_login", "auth_verify", "auth_logout"),
    "mcp": ("mcp",),
    "worker": ("worker",),
    "approvals": ("approvals",),
    "verify-release": ("verify_release",),
    "checkpoints": ("checkpoints_list", "checkpoints_write"),
    "sessions": (
        "sessions",
        "sessions_search",
        "session_show",
        "session_rename",
        "session_archive",
        "session_delete",
        "session_resume",
    ),
    "capability": ("capability", "capabilities_list"),
    "roundtrip": ("roundtrip_test",),
    "enroll": ("enroll_voice",),
    "verify": ("auth_verify", "verify_voice", "verify_release", "branding_verify"),
    "flags": ("flags",),
    "flag-drill": ("flags",),
    "package": ("package_certify", "package_status"),
    "product": (
        "product_status",
        "product_probe",
        "product_first_run",
        "product_install",
        "product_migrate",
        "product_rollback",
        "product_repair",
        "migrate_status",
    ),
    "audio": ("audio", "mic_test", "asr_test", "tts_test", "wake_status"),
    "mic-test": ("mic_test",),
    "asr-test": ("asr_test",),
    "tts-test": ("tts_test",),
    "profile": (
        "config_profiles",
        "profile_show",
        "profile_check",
        "profile_select",
        "profile_reset",
        "profile_upgrade",
        "profile_remove",
        "profile_rollback",
    ),
    "branding": (
        "branding_plan",
        "branding_verify",
        "branding_migrate",
        "migrate_status",
    ),
    "permissions": ("permissions",),
    "skills": (
        "skills_list",
        "skills_show",
        "skills_enable",
        "skills_disable",
        "skills_remove",
        "skills_rollback",
        "plugins_install",
        "plugins_remote_refusal",
        "verified_list",
        "verified_show",
        "verified_recall",
        "verified_verdict",
        "verified_pin",
        "verified_unpin",
        "skills_workshop",
        "skills_workshop_backups",
        "skills_workshop_curator",
        "skills_workshop_backup",
        "skills_workshop_restore",
        "skills_workshop_rollback",
    ),
    "verified-skills": (
        "verified_list",
        "verified_show",
        "verified_recall",
        "verified_verdict",
        "verified_pin",
        "verified_unpin",
    ),
    "project": ("projects_list", "projects_show", "projects_active"),
    "kanban": (
        "kanban_list",
        "kanban_show",
        "kanban_current",
        "kanban_role_grant_voice",
        "kanban_role_revoke_voice",
        "kanban_widget_move_voice",
        "kanban_snapshot_restore_voice",
        "kanban_tab_list",
        "kanban_tab_create",
        "kanban_tab_rename",
        "kanban_tab_delete",
        "kanban_tab_move",
        "kanban_widget_add",
        "kanban_snapshots",
        "kanban_swarm",
        "kanban_watch",
        "kanban_dispatch",
        "kanban_restore",
    ),
    "computer-use": (
        "computer_status",
        "computer_screen",
        "computer_screen_panes",
        "computer_screen_arrange",
    ),
    "browser": (
        "browser_status",
        "browser_doctor",
        "browser_tabs",
        "browser_read",
        "browser_find",
        "browser_screenshot",
        "browser_open_browser",
        "browser_activate",
        "browser_open",
        "browser_navigate",
        "browser_fill",
        "browser_click",
        "browser_close_tab",
        "browser_profiles",
        "browser_profile_create",
        "browser_profile_open",
        "browser_profile_close",
        "browser_profile_remove",
    ),
    "acp": (
        "acp",
        "acp_client_status",
        "acp_client_catalog",
        "acp_client_configure",
        "acp_client_unconfigure",
        "acp_client_handshake",
        "acp_client_send",
        "acp_client_receipts",
    ),
    "lsp": ("lsp",),
    "plugins": (
        "plugins_status",
        "plugins_list",
        "plugins_show",
        "plugins_register",
        "plugins_invoke",
        "plugins_unregister",
        "plugins_doctor",
    ),
    "channels": (
        "channels_status",
        "channels_catalog",
        "channels_accounts",
        "channels_configure",
        "channels_unconfigure",
        "channels_receive_refusal",
        "channels_doctor",
    ),
    "theme": ("themes", "pets_downloads_refusal"),
    "proxy": ("proxy",),
    "hooks": ("hooks_list", "hooks_doctor", "hooks_emit"),
    "sync": ("sync_list", "sync_revoke_voice"),
    "pairing": (
        "pairing_list",
        "pairing_create",
        "pairing_approve",
        "pairing_revoke",
        "pairing_setup_code",
        "pairing_accept",
    ),
    "webhook": ("webhooks",),
    "msg": ("msg_accounts",),
    "brain": ("brain",),
    "gateway": (
        "gateway",
        "gateway_supervision_refusal",
        "gateway_supervision_request",
        "gateway_supervision_revert",
        "gateway_discovery",
    ),
    "peer": ("peer_list", "peer_dm", "peer_run"),
    "tui": ("tui_help",),
    "vision": ("vision_status",),
    "import-agent": ("import_inspect",),
    "lessons": ("lessons_list", "lessons_promote", "lessons_remove"),
    "memory": ("memory_search",),
    "websites": ("websites_list", "websites_show", "web_fetch", "web_search"),
    "actions": (
        "actions_list",
        "actions_show",
        "actions_check",
        "actions_receipts",
        "actions_receipt",
        "actions_execute",
        "actions_cancel",
    ),
    "ghost": (
        "ghost_list",
        "ghost_status",
        "ghost_show",
        "ghost_check",
        "ghost_grant",
        "ghost_revoke",
        "ghost_execute",
        "ghost_cancel",
        "ghost_receipts",
        "ghost_enable",
        "ghost_disable",
        "ghost_estop",
        "ghost_reset",
    ),
    "power": ("power_status",),
    "experiments": (
        "experiments_status",
        "experiments_list",
        "experiments_show",
        "experiments_register",
        "experiments_start",
        "experiments_trial",
        "experiments_finish",
        "experiments_signal",
        "experiments_abort",
        "experiments_snapshot",
        "experiments_promote",
        "experiments_evaluate",
    ),
    "export-state": (),
    "import-state": (),
    "device": ("device", "device_enroll"),
    "monitor": ("monitor",),
    "webui": ("webui_status",),
    "setup": ("setup_check",),
    "frontier": ("model", "frontier_show", "frontier_chat"),
    "opencode": (
        "opencode_status",
        "opencode_list",
        "opencode_show",
        "opencode_check",
        "opencode_grant",
        "opencode_revoke",
        "opencode_preview",
        "opencode_run",
        "opencode_cancel",
        "opencode_retry",
        "opencode_recover",
        "opencode_receipts",
    ),
    "runtime": (
        "runtime_images",
        "runtime_status",
        "runtime_doctor",
        "runtime_install",
        "runtime_upgrade",
        "runtime_rollback",
        "runtime_remove",
        "runtime_recover",
        "runtime_repair",
        "runtime_history",
        "runtime_discovery",
        "runtime_marketplace",
        "runtime_update_report",
    ),
    "egress": ("egress_status", "egress_check", "substitute_egress"),
    "policy": ("policy_conformance", "policy_attest"),
    "autonomy": (
        "autonomy_status",
        "autonomy_list",
        "autonomy_show",
        "autonomy_pause",
        "autonomy_resume",
        "autonomy_revoke",
        "autonomy_expire",
    ),
    "usage": ("usage_summary", "evaluation_telemetry"),
    "receipts": ("receipts_ledger",),
    "logbook": ("logbook_entries", "logbook_summary"),
    "node": (
        "node_host_status",
        "node_host_list",
        "node_host_show",
        "node_host_capabilities",
        "node_host_enroll",
        "node_host_revoke",
        "node_host_dispatch",
    ),
    "tasks": (
        "task_ledger_status",
        "task_ledger_list",
        "task_ledger_show",
        "task_ledger_timeline",
    ),
    "media": ("media_gen_status", "media_gen_catalog", "media_gen_generate"),
    "local-models": (
        "local_models_status",
        "local_models_hardware",
        "local_models_catalog",
        "local_models_jobs",
        "local_models_runtime_install",
        "local_models_download",
        "local_models_sideload",
        "local_models_server",
        "local_models_eject",
        "local_models_activate",
        "local_models_quickstart",
        "local_models_fallback",
        "local_models_resolve",
    ),
    "transfers": (
        "transfers_status",
        "transfers_list",
        "transfers_fetch",
        "transfers_plan",
        "transfers_resume",
    ),
    "recall": (
        "recall_status",
        "recall_policy_get",
        "recall_policy_set",
        "recall_query",
        "recall_resolve",
        "recall_escalate",
    ),
    "documents": ("documents_status", "documents_kinds", "documents_extract"),
    "ask": ("questions_status", "questions_ask", "questions_pending"),
    "shares": (
        "shares_status",
        "shares_publish",
        "shares_list",
        "shares_revoke",
        "shares_purge",
    ),
    "agent": (),
}

# Guard rows: a guard's typed refusal is the voice surface for a capability
# that already has a CLI/dashboard production path, so map each guard op id to
# the real CLI family it guards. The ledger derives the CLI surface from this
# mapping, so a guard with a genuine CLI equivalent is not left PARTIAL.
GUARD_CLI_FAMILY = {
    "guard_actions_prepare": "actions",
    "guard_actions_preview": "actions",
    "guard_actions_retry": "actions",
    "guard_actions_recover": "actions",
    "guard_ask_answer": "ask",
    "guard_backup_create": "export-state",
    "guard_backup_restore": "import-state",
    "guard_brain_onboard_apply": "brain",
    "guard_file_patch": "files",
    "guard_ghost_preview": "ghost",
    "guard_kanban_import": "kanban",
    "guard_opencode_prepare": "opencode",
    "guard_opencode_run_confirmed": "opencode",
    "guard_shares_resolve": "shares",
    "guard_sync_accept": "sync",
    "guard_sync_offer": "sync",
    "guard_transfers_fetch_raw": "transfers",
    "guard_webhook_verify": "webhook",
}
for _guard_id, _guard_family in GUARD_CLI_FAMILY.items():
    CLI_COVERAGE[_guard_family] = tuple(
        CLI_COVERAGE.get(_guard_family, ())) + (_guard_id,)

# Non-guard voice rows whose capability genuinely has a CLI production path
# (a family or family.subcommand) that the spoken entry mirrors. Mapping the
# op id here lets the ledger count the CLI surface it actually has instead of
# leaving the row PARTIAL. Voice-only guidance/status rows with no CLI
# equivalent are intentionally absent.
VOICE_CLI_FAMILY = {
    "pause": "power",
    "resume": "power",
    "memory_remember": "memory",
    "memory_forget": "memory",
    "projects_create": "project",
    "projects_use": "project",
    "projects_archive": "project",
    "themes_set": "theme",
    "themes_reset": "theme",
    "themes_tweak_set": "theme",
    "themes_tweak_clear": "theme",
    "ghost_action_preview": "ghost",
    "ghost_action_retry": "ghost",
    "ghost_action_recover": "ghost",
    "ghost_click": "ghost",
    "ghost_type": "ghost",
    "ghost_read_tab": "ghost",
    "acp_serve_refusal": "acp",
    "acp_accept_hooks_refusal": "acp",
    "lsp_install_refusal": "lsp",
    "proxy_start_refusal": "proxy",
    "import_refusal": "import-agent",
    "brain_onboard_plan": "brain",
    "curator": "verified-skills",
    "tui_help": "tui",
    "mic_help": "mic-test",
    "console_help": "run",
    "backup_info": "export-state",
    "export_state_voice": "export-state",
    "import_state_voice": "import-state",
    "diagnostics": "setup",
    "doctor": "setup",
    "security": "verify-release",
    "insights": "sessions",
    "update_status": "product",
    "worktree": "opencode",
    # Secret-reference status: `qubit auth status` is the same
    # auth_status.auth_status backend (reference kinds, never values).
    "secrets": "auth",
}
for _voice_id, _voice_family in VOICE_CLI_FAMILY.items():
    CLI_COVERAGE[_voice_family] = tuple(
        CLI_COVERAGE.get(_voice_family, ())) + (_voice_id,)

# CLI families with no voice entry: explicit tested reason + where to run
# them instead. Keys must stay in sync with CLI_COVERAGE (empty tuple).
VOICE_IMPOSSIBLE = {
    # passphrases must never be spoken (transcripts/journal/audio history)
    "export-state.voice": "passphrase-sealed bundles: use the dashboard form or "
                          "`qubit export-state` in a terminal",
    "import-state.voice": "passphrase-sealed bundles: use the dashboard form or "
                          "`qubit import-state <bundle> [--apply]` in a terminal",
    # nested JSON envelopes cannot be spoken reliably (dashboard/CLI only)
    "actions.preview": "structured args envelopes: use `qubit actions "
                       "preview` or the dashboard form",
    "actions.prepare": "structured args envelopes: use `qubit actions "
                       "prepare` or the dashboard form",
    "actions.retry": "structured args envelopes: use `qubit actions "
                     "retry` or the dashboard form",
    "actions.recover": "structured recovery: use `qubit actions recover` "
                       "or the dashboard form",
    "ghost.preview": "structured args envelopes: use `qubit ghost "
                     "preview` or the dashboard form",
    # opencode adapter power controls stay CLI/dashboard-only (adapter
    # policy/ownership changes, not single executions); voice covers the
    # full disposable-worktree coding workflow (preview/run/cancel/retry/
    # recover/receipts/grant/revoke) through the same runner instead.
    # In-project voice writes stay refused (VOICE_DISPOSABLE_ONLY):
    # preview-hash apply flow uses `qubit opencode execute --confirm`.
    "opencode.execute": "in-project preview-hash flow: use `qubit opencode "
                        "execute --confirm` or the dashboard",
    "opencode.prepare": "preview-hash flow: use `qubit opencode prepare`",
    "opencode.enable": "adapter control: use `qubit opencode enable`",
    "opencode.disable": "adapter control: use `qubit opencode disable`",
    "opencode.emergency-stop": "adapter control: use `qubit opencode "
                               "emergency-stop`",
    "opencode.reset": "adapter control: use `qubit opencode reset`",
    # standing authorization policy never changes by voice (safety rule:
    # voice authorizes single executions, never widens permissions)
    "permissions.grant": "standing policy change: use "
                         "`qubit permissions grant --confirm` in a terminal",
    "permissions.revoke": "standing policy change: use "
                          "`qubit permissions revoke --confirm` in a terminal",
    "power.grant": "standing policy change: use `qubit power grant` "
                   "in a terminal",
    "power.revoke": "standing policy change: use `qubit power revoke` "
                    "in a terminal",
    # structured curator proposals need the full form surface (CLI only)
    "verified-skills.submit": "structured skill proposals: use "
                              "`qubit verified-skills submit`",
    "verified-skills.record_failure": "structured failure records: use "
                                      "`qubit verified-skills record-failure`",
    "verified-skills.reinforce": "structured outcomes: use "
                                 "`qubit verified-skills reinforce`",
    # profile manifests are JSON documents (CLI only)
    "profile.install": "JSON manifests: use `qubit profile install`",
    # desktop-session integration needs a desktop, not a voice turn
    "webui.open": "browser launch needs a desktop session: open the "
                  "dashboard URL shown by `dashboard status` yourself",
    "product.shortcut": "desktop integration: use `qubit product "
                        "shortcut --apply` in a desktop terminal",
    # raw byte payloads cannot be spoken (HMAC-verified channel ingress only)
    "channels.receive": "raw signed channel payloads: use the dashboard "
                        "channel ingress endpoint (POST /channels/<channel>) "
                        "or `qubit channels receive` in a terminal",
    # raw file bytes are base64 payloads: the confirmed voice fetch reports
    # only size/digest; the actual bytes stay on CLI/dashboard
    "transfers.fetch": "raw file bytes (base64) cannot be spoken: the "
                       "confirmed voice fetch reports size and digest only. "
                       "Use `qubit transfers fetch` or the dashboard for "
                       "the bytes",
    # a multi-question answer map is nested JSON and cannot be spoken
    "ask.answer": "structured answer maps (nested JSON) cannot be spoken: "
                  "use `qubit ask answer <prompt_id> --answers-file <json>` "
                  "or the dashboard question form",
    # a share bearer token is a secret and must never be spoken
    "shares.resolve": "a share bearer token is a secret and is never spoken: "
                      "open the /share/<id> URL with the token, or use "
                      "`qubit shares resolve <id> --token-file <path>` in a "
                      "terminal",
    # the Qubit Agent runtime is a raw CLI passthrough (verbatim argv);
    # a spoken turn cannot name it unambiguously or safely
    "agent": "the Qubit Agent runtime is a raw CLI passthrough: run "
             "`qubit agent <args...>` in a terminal",
}


# Voice guards: every op excluded from voice execution (passphrase-sealed,
# nested structured data, preview-hash binding, raw bytes) still has a REAL
# spoken surface: a typed, audited refusal that names the exact CLI/dashboard
# counterpart. This replaces silence/UNKNOWN with a tested policy outcome and
# never binds the unsafe payload. Keys are dashboard_ops op ids.
VOICE_GUARDS: Dict[str, Dict[str, Any]] = {
    "actions_prepare": {
        "aliases": ("prepare a structured action",
                    "prepare an action envelope"),
        "guidance": VOICE_IMPOSSIBLE["actions.prepare"],
        "code": "VOICE_NESTED_UNSUPPORTED",
    },
    "actions_preview": {
        "aliases": ("preview a structured action",
                    "preview an action envelope"),
        "guidance": VOICE_IMPOSSIBLE["actions.preview"],
        "code": "VOICE_NESTED_UNSUPPORTED",
    },
    "actions_retry": {
        "aliases": ("retry a structured action",),
        "guidance": VOICE_IMPOSSIBLE["actions.retry"],
        "code": "VOICE_NESTED_UNSUPPORTED",
    },
    "actions_recover": {
        "aliases": ("recover a structured action",),
        "guidance": VOICE_IMPOSSIBLE["actions.recover"],
        "code": "VOICE_NESTED_UNSUPPORTED",
    },
    "ghost_preview": {
        "aliases": ("preview a ghost action",),
        "guidance": VOICE_IMPOSSIBLE["ghost.preview"],
        "code": "VOICE_NESTED_UNSUPPORTED",
    },
    "backup_create": {
        "aliases": ("create an encrypted backup",
                    "make an encrypted backup"),
        "guidance": "passphrase-sealed bundles: use the dashboard password "
                    "form or `qubit export-state` in a terminal",
        "code": "PASSPHRASE_NEVER_SPOKEN",
    },
    "backup_restore": {
        "aliases": ("restore an encrypted backup",
                    "restore an encrypted bundle"),
        "guidance": "passphrase-sealed bundles: use the dashboard password "
                    "form or `qubit import-state <bundle>` in a terminal",
        "code": "PASSPHRASE_NEVER_SPOKEN",
    },
    "brain_onboard_apply": {
        "aliases": ("apply brain onboarding answers",
                    "apply onboarding answers"),
        "guidance": "structured onboarding answers: use the dashboard "
                    "onboarding form or `qubit brain onboard`",
        "code": "VOICE_NESTED_UNSUPPORTED",
    },
    "kanban_import": {
        "aliases": ("import a kanban snapshot",
                    "import a board snapshot"),
        "guidance": "structured snapshots: use the dashboard boards form or "
                    "`qubit kanban import` in a terminal",
        "code": "VOICE_NESTED_UNSUPPORTED",
    },
    "opencode_prepare": {
        "aliases": ("prepare an in-project coding change",
                    "prepare an in-project coding workflow"),
        "guidance": VOICE_IMPOSSIBLE["opencode.prepare"],
        "code": "VOICE_PREVIEW_HASH_ONLY",
    },
    "opencode_run_confirmed": {
        "aliases": ("run a confirmed in-project coding change",
                    "apply a confirmed coding change"),
        "guidance": VOICE_IMPOSSIBLE["opencode.execute"],
        "code": "VOICE_PREVIEW_HASH_ONLY",
    },
    "sync_offer": {
        "aliases": ("offer a sync bundle", "create a sync offer"),
        "guidance": "passphrase-sealed sync bundles: use the dashboard sync "
                    "form or `qubit sync offer` in a terminal",
        "code": "PASSPHRASE_NEVER_SPOKEN",
    },
    "sync_accept": {
        "aliases": ("accept a sync bundle", "accept a sync offer"),
        "guidance": "passphrase-sealed sync bundles: use the dashboard sync "
                    "form or `qubit sync accept` in a terminal",
        "code": "PASSPHRASE_NEVER_SPOKEN",
    },
    "webhook_verify": {
        "aliases": ("verify a webhook signature",
                    "verify a webhook payload"),
        "guidance": "raw byte payloads: use the dashboard webhooks form or "
                    "`qubit webhook verify` in a terminal",
        "code": "VOICE_BYTES_UNSUPPORTED",
    },
    "file_patch": {
        "aliases": ("apply a file patch", "apply a unified diff"),
        "guidance": "structured diffs: use the dashboard file form or "
                    "`qubit files patch --patch-file` in a terminal",
        "code": "VOICE_NESTED_UNSUPPORTED",
    },
    "transfers_fetch_raw": {
        "aliases": ("read raw file bytes", "speak raw file bytes",
                    "show raw file bytes"),
        "guidance": VOICE_IMPOSSIBLE["transfers.fetch"],
        "code": "VOICE_BYTES_UNSUPPORTED",
    },
    "ask_answer": {
        "aliases": ("answer with structured answers",
                    "record structured answers"),
        "guidance": VOICE_IMPOSSIBLE["ask.answer"],
        "code": "VOICE_NESTED_UNSUPPORTED",
    },
    "shares_resolve": {
        "aliases": ("open a shared session", "view a shared session"),
        "guidance": VOICE_IMPOSSIBLE["shares.resolve"],
        "code": "PASSPHRASE_NEVER_SPOKEN",
    },
}


def _register_voice_guards() -> None:
    for op_id, spec in VOICE_GUARDS.items():
        _E(
            f"guard_{op_id}",
            f"{op_id.replace('_', ' ').title()} (voice guard)",
            tuple(spec["aliases"]),
            "voice_unsupported",
            ("voice-guard",),
            fixed=(("op", op_id), ("guidance", spec["guidance"]),
                   ("code", spec["code"])),
            summary="guidance",
            examples=(f"qubit, {spec['aliases'][0]}",),
            help="Typed refusal with guidance: this op is CLI/dashboard-only "
                 "for safety; the spoken attempt never binds the unsafe data.",
        )


_register_voice_guards()


# ------------------------------------------------- wave 4: plugin fleet
# The pinned Hermes dashboard plugin fleet (``hermes-achievements`` + the full
# kanban plugin API) has a real in-process Qubit backend since wave 3
# (``voicebridge/hermes_plugins.py``). Those routes are dashboard/compat
# surfaces, so they are NOT part of the frozen CLI-parity ``REGISTRY`` whose
# example table ``tests/test_voice_commands8tf.py`` pins exactly. They live in
# their own registry and still flow through the one resolve/execute/confirm
# pipeline, one journal, one redactor, and one e-stop.
#
# ``PLUGIN_FLEET_OPS`` maps a spoken entry to the exact ``hermes_compat``
# (method, path, fixed-body) the dashboard SPA calls — same backend, same
# listener. ``PLUGIN_FLEET_VOICE`` declares the compat route handler -> voice
# entry so the wave-4 audit can prove there is no silently unspoken route.
PLUGIN_FLEET_REGISTRY: Dict[str, VoiceCommand] = {}

PLUGIN_FLEET_OPS: Dict[str, Tuple[str, str, Optional[Dict[str, Any]]]] = {
    "achievements_list":
        ("GET", "/api/plugins/hermes-achievements/achievements", None),
    "achievements_scan_status":
        ("GET", "/api/plugins/hermes-achievements/scan-status", None),
    "achievements_recent":
        ("GET", "/api/plugins/hermes-achievements/recent-unlocks", None),
    "achievements_session_badges":
        ("GET",
         "/api/plugins/hermes-achievements/sessions/{session_id}/badges",
         None),
    "achievements_rescan":
        ("POST", "/api/plugins/hermes-achievements/rescan", {}),
    "achievements_reset":
        ("POST", "/api/plugins/hermes-achievements/reset-state",
         {"confirm": True}),
    "plugin_fleet_list":
        ("GET", "/api/dashboard/plugins/fleet", None),
    "kanban_fleet_board": ("GET", "/api/plugins/kanban/board", None),
    "kanban_fleet_task":
        ("GET", "/api/plugins/kanban/tasks/{task_id}", None),
    "kanban_fleet_stats": ("GET", "/api/plugins/kanban/stats", None),
    "kanban_fleet_assignees": ("GET", "/api/plugins/kanban/assignees", None),
    "kanban_fleet_boards": ("GET", "/api/plugins/kanban/boards", None),
    "kanban_fleet_config": ("GET", "/api/plugins/kanban/config", None),
    "kanban_fleet_projects": ("GET", "/api/plugins/kanban/projects", None),
    "kanban_fleet_workers": ("GET", "/api/plugins/kanban/workers/active", None),
    "kanban_fleet_diagnostics":
        ("GET", "/api/plugins/kanban/diagnostics", None),
    "kanban_fleet_model_options":
        ("GET", "/api/plugins/kanban/model-options", None),
    "kanban_fleet_home_channels":
        ("GET", "/api/plugins/kanban/home-channels", None),
}

PLUGIN_FLEET_VOICE: Dict[str, str] = {
    "h_plugin_achievements": "achievements_list",
    "h_plugin_achievements_scan_status": "achievements_scan_status",
    "h_plugin_achievements_recent_unlocks": "achievements_recent",
    "h_plugin_achievements_session_badges": "achievements_session_badges",
    "h_plugin_achievements_rescan": "achievements_rescan",
    "h_plugin_achievements_reset_state": "achievements_reset",
    "h_plugin_fleet": "plugin_fleet_list",
    "h_plugin_kanban_board": "kanban_fleet_board",
    "h_plugin_kanban_task": "kanban_fleet_task",
    "h_plugin_kanban_stats": "kanban_fleet_stats",
    "h_plugin_kanban_assignees": "kanban_fleet_assignees",
    "h_plugin_kanban_boards": "kanban_fleet_boards",
    "h_plugin_kanban_config": "kanban_fleet_config",
    "h_plugin_kanban_projects": "kanban_fleet_projects",
    "h_plugin_kanban_workers": "kanban_fleet_workers",
    "h_plugin_kanban_diagnostics": "kanban_fleet_diagnostics",
    "h_plugin_kanban_model_options": "kanban_fleet_model_options",
    "h_plugin_kanban_home_channels": "kanban_fleet_home_channels",
}


def _FE(entry_id: str, title: str, aliases: Tuple[str, ...],
        args: Tuple[VoiceArg, ...] = (), confirm: bool = False,
        summary: str = "plugin_fleet", examples: Tuple[str, ...] = (),
        split: str = "", help: str = "") -> VoiceCommand:
    """Register a wave-4 plugin-fleet entry (same pipeline, own registry)."""
    entry = VoiceCommand(id=entry_id, title=title, aliases=tuple(aliases),
                         args=tuple(args), backend="plugin_fleet",
                         fixed=(("op", entry_id),), scope=("plugin-fleet",),
                         confirm=confirm, summary=summary,
                         examples=tuple(examples), parity=(), help=help,
                         split=split)
    PLUGIN_FLEET_REGISTRY[entry_id] = entry
    return entry


_FE("achievements_list", "Show achievements",
    ("show my achievements", "list achievements", "list my achievements",
     "show achievements", "what achievements do i have"),
    examples=("qubit, show my achievements",),
    help="Unlocked/discovered/total badges from real session history.")
_FE("achievements_scan_status", "Achievement scan status",
    ("achievement scan status", "achievements scan status",
     "achievement scanner status", "status of achievement scan"),
    examples=("qubit, achievement scan status",),
    help="Vendored scanner bookkeeping (idle/running, run count).")
_FE("achievements_recent", "Recently unlocked achievements",
    ("recent achievements", "show recent achievements",
     "latest achievements", "recently unlocked achievements"),
    examples=("qubit, recent achievements",),
    help="Newest unlocked badges with timestamps.")
_FE("achievements_session_badges", "Session badges",
    ("rx:achievements for session (.+)", "rx:session badges for (.+)"),
    args=(_S("session_id", True),),
    examples=("qubit, achievements for session 12",),
    help="Badges unlocked by one session (same session history).")
_FE("achievements_rescan", "Rescan achievements",
    ("rescan achievements", "recompute achievements", "refresh achievements",
     "rescan my badges"),
    examples=("qubit, rescan achievements",),
    help="Bounded recompute over local history; e-stop probed.")
_FE("achievements_reset", "Reset achievements",
    ("reset my achievements", "reset achievement progress",
     "clear my achievements", "reset achievements"),
    confirm=True, examples=("qubit, reset my achievements",),
    help="Clear unlock history and cached scans (confirmed).")
_FE("plugin_fleet_list", "List dashboard plugins",
    ("list dashboard plugins", "show the plugin fleet",
     "list the plugin fleet", "which dashboard plugins"),
    examples=("qubit, list dashboard plugins",),
    help="Vendored dashboard plugin manifests (discovery view).")
_FE("kanban_fleet_board", "Vendored kanban board",
    ("fleet kanban board", "show the vendored board",
     "read the vendored board", "show the plugin board"),
    examples=("qubit, fleet kanban board",),
    help="Vendored kanban plugin board (read-only, real kanban_db).")
_FE("kanban_fleet_task", "Vendored kanban task",
    ("rx:fleet kanban task (.+)", "rx:hermes kanban task (.+)"),
    args=(_S("task_id", True),),
    examples=("qubit, fleet kanban task abc123",),
    help="One vendored kanban task by id.")
_FE("kanban_fleet_stats", "Vendored kanban stats",
    ("fleet kanban stats", "vendored kanban stats", "plugin kanban stats"),
    examples=("qubit, fleet kanban stats",),
    help="Vendored kanban counts by status.")
_FE("kanban_fleet_assignees", "Vendored kanban assignees",
    ("fleet kanban assignees", "plugin kanban assignees"),
    examples=("qubit, fleet kanban assignees",),
    help="Known assignees on the vendored board.")
_FE("kanban_fleet_boards", "Vendored kanban boards",
    ("fleet kanban boards", "list vendored boards"),
    examples=("qubit, fleet kanban boards",),
    help="Vendored kanban boards.")
_FE("kanban_fleet_config", "Vendored kanban config",
    ("fleet kanban config", "plugin kanban config"),
    examples=("qubit, fleet kanban config",),
    help="Vendored kanban plugin configuration.")
_FE("kanban_fleet_projects", "Vendored kanban projects",
    ("fleet kanban projects", "plugin kanban projects"),
    examples=("qubit, fleet kanban projects",),
    help="Kanban projects registered by the vendored plugin.")
_FE("kanban_fleet_workers", "Vendored kanban workers",
    ("fleet kanban workers", "plugin kanban workers"),
    examples=("qubit, fleet kanban workers",),
    help="Active vendored kanban workers (read-only).")
_FE("kanban_fleet_diagnostics", "Vendored kanban diagnostics",
    ("fleet kanban diagnostics", "plugin kanban diagnostics"),
    examples=("qubit, fleet kanban diagnostics",),
    help="Vendored kanban plugin diagnostics.")
_FE("kanban_fleet_model_options", "Vendored kanban model options",
    ("fleet kanban model options", "plugin kanban model options"),
    examples=("qubit, fleet kanban model options",),
    help="Model options the vendored kanban plugin reports.")
_FE("kanban_fleet_home_channels", "Vendored kanban channels",
    ("fleet kanban channels", "plugin kanban channels",
     "fleet home channels"),
    examples=("qubit, fleet kanban channels",),
    help="Home channel bindings of the vendored kanban plugin.")


# ------------------------------------------------------ model-profile voice
# Canonical resolver-backed spoken model/provider intents. A dedicated
# registry keeps the dashboard-catalog mirror (REGISTRY) untouched while the
# entries flow through the one resolve/execute/confirm pipeline, one journal,
# one redactor, and one e-stop. Mutating selections are confirm-gated; voice
# never accepts, echoes, or logs a raw API key (see execute()).
MODEL_PROFILE_REGISTRY: Dict[str, VoiceCommand] = {}


def _MP(entry_id: str, title: str, aliases: Tuple[str, ...], backend: str,
        args: Tuple[VoiceArg, ...] = (),
        fixed: Tuple[Tuple[str, str], ...] = (), confirm: bool = False,
        examples: Tuple[str, ...] = (), help: str = "") -> VoiceCommand:
    entry = VoiceCommand(id=entry_id, title=title, aliases=tuple(aliases),
                         args=tuple(args), backend=backend,
                         fixed=tuple(fixed), scope=("model-profile",),
                         confirm=confirm, summary="model_profile",
                         examples=tuple(examples), parity=(), help=help)
    MODEL_PROFILE_REGISTRY[entry_id] = entry
    return entry


_MP("model_profile_local", "Use local brain",
    ("use my local brain", "switch to my local brain", "use the local brain",
     "go back to my local brain"),
    "model_profile_local", fixed=(("role", "chat"),), confirm=True,
    examples=("qubit, use my local brain",),
    help="Select the shipped local model profile for this session (confirmed).")
_MP("model_profile_research", "Use a named model for this session",
    (r"rx:use (?:my )?([a-z0-9_\-]+) model for this session$",
     r"rx:switch to (?:my )?([a-z0-9_\-]+) model for this session$"),
    "model_profile_use", args=(_S("profile_id", True),), confirm=True,
    examples=("qubit, use my research model for this session",),
    help="Select a registered profile by name for this session (confirmed).")
_MP("model_profile_active", "Active model and voice",
    ("what model and voice are active", "which model and voice are active",
     "what model is active", "what voice is active"),
    "model_profile_status",
    examples=("qubit, what model and voice are active?",),
    help="Active model profile plus the active voice/STT/TTS records.")
_MP("model_profile_test", "Test model profile",
    ("test my model profile", "test the model profile",
     "test my brain profile"),
    "model_profile_test", args=(_S("profile_id"),),
    examples=("qubit, test my model profile",),
    help="Preflight the active (or named) language-model profile, read-only.")
_MP("speech_profile_test", "Test speech profile",
    ("test my speech profile", "test my voice profile",
     "test the speech profile"),
    "speech_profile_test", args=(_S("profile_id"), _S("kind")),
    examples=("qubit, test my speech profile",),
    help="Preflight the active (or named) speech profile (ASR/TTS/realtime), "
         "read-only.")
_MP("model_profile_realtime", "Use realtime voice profile",
    ("use the realtime voice profile", "use my realtime voice profile",
     "switch to the realtime voice profile", "use realtime voice"),
    "model_profile_realtime", confirm=True,
    examples=("qubit, use the realtime voice profile",),
    help="Select the registered realtime voice profile (confirmed).")
_MP("model_profile_coding", "Route this task with my coding model",
    ("run this task with my coding model", "use my coding model for this task",
     "run this with my coding model", "switch to my coding model",
     "use my coding model"),
    "model_profile_coding", args=(_S("profile_id"),), confirm=True,
    examples=("qubit, run this task with my coding model",),
    help="Select the coding-role profile for this session (confirmed).")
_MP("model_profile_local_only", "Return to local-only mode",
    ("return to local only mode", "return to local-only mode", "go local only",
     "local only mode", "reset this session to defaults"),
    "model_profile_local_only", confirm=True,
    examples=("qubit, return to local-only mode",),
    help="Clear this session's model overrides and reselect the local brain.")


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
    # Model-profile intents must never take a raw key by voice. A pasted
    # secret is refused typed BEFORE it can reach a journal, receipt, spoken
    # summary, or audit meta — the secret stays out of every spoken path.
    if entry.id in MODEL_PROFILE_REGISTRY:
        from .providers.resolver import looks_like_raw_secret as _looks
        for _v in merged.values():
            if isinstance(_v, str) and _looks(_v):
                _audit("rejected", ctx.actor,
                       {"entry": entry.id, "reason": "raw_secret_argument",
                        "session": ctx.session})
                return Outcome(FAILED, entry,
                               "I can't take an API key by voice. Store it as "
                               "env:NAME or file:PATH, then run `qubit profile "
                               "model create --key-ref env:NAME` or use the "
                               "dashboard secret flow.", {})
    if entry.confirm:
        if entry.backend not in ESTOP_EXEMPT_BACKENDS and _estop(ctx.cfg):
            _audit("estop", ctx.actor,
                   {"entry": entry.id, "session": ctx.session})
            return Outcome(REFUSED, entry,
                           "Emergency stop is latched. Reset it first.", {})
        journal = _journal(ctx.cfg)
        _prune_session_pending(ctx, journal)
        # Credential references (or worse, a pasted raw value) must never
        # reach staged summaries, speech, or the journal wording: backend
        # outputs carry the kind only, and summaries follow the same rule.
        shown = ", ".join(f"{k} {_redact(str(v))[:40]}" for k, v in
                          sorted(merged.items())
                          if k not in ("action", "key_ref"))[:160]
        # Fail fast on reference-shaped args: a raw pasted secret must be
        # refused BEFORE anything is staged or journaled (no receipt, no
        # wording, no meta.args carrying the value).
        if entry.id in ("gateway_add", "msg_account_add"):
            ref = str(merged.get("key_ref", "") or "")
            if not (ref.startswith("env:") or ref.startswith("file:")):
                _audit("rejected", ctx.actor,
                       {"entry": entry.id, "reason": "key_ref_not_reference",
                        "session": ctx.session})
                return Outcome(FAILED, entry,
                               "key_ref must be an env:VAR or file:PATH "
                               "reference, never a secret value.", {})
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
                       f"That failed: {_redact(str(exc))[:160]}.",
                       {"entry": entry.id, "session": ctx.session,
                        "code": str(code)[:60]})
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
    entry = _lookup_entry(entry_id)
    if entry is None:
        return Outcome(FAILED, None, "That confirmation is stale.", {})
    if ctx.source not in ALLOWED_SOURCES:
        return Outcome(REFUSED, entry, "That request did not come from you.", {})
    if entry.backend not in ESTOP_EXEMPT_BACKENDS and _estop(ctx.cfg):
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
        code = getattr(exc, "code", None) or type(exc).__name__
        _audit("confirmed_failed", ctx.actor,
               {"entry": entry.id, "confirmation_id": target.id,
                "session": ctx.session})
        return Outcome(FAILED, entry,
                       f"That failed: {_redact(str(exc))[:160]}.",
                       {"entry": entry.id, "session": ctx.session,
                        "confirmation_id": target.id,
                        "code": str(code)[:60]})
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


def voice_say(cfg: Any, text: str, *, actor: str = "voice:user",
              session: str = "voice",
              source: str = "user") -> Dict[str, Any]:
    """One spoken turn routed through the SAME production op dispatcher
    the CLI and dashboard console share (``dashboard_ops.run_op``).

    Voice never gets a private execution path: the spoken text is resolved
    to a registry entry, then dispatched by op id with the resolved args,
    exactly as the dashboard page would. Confirm-tier entries return
    ``{"state": "needs-confirm", "confirmation_id": ...}`` and are settled
    with the shared ``confirm_op``/``cancel_op`` (one journal, one
    settle-once path) so confirmation, cancellation, receipts, audit, and
    redaction are identical on every surface.

    Read/guidance/refusal entries execute immediately. Unresolved text
    returns ``clarify``/``ambiguous``/``unknown`` without touching a
    backend. Only user-issued sources ever execute; a model/page/tool
    source is refused before dispatch (defence in depth on top of the
    registry's own source gate and the dispatcher's coercion)."""
    if source not in ALLOWED_SOURCES:
        _audit("refused_source", actor,
               {"source": str(source)[:40], "session": session})
        return {"state": REFUSED, "code": "SOURCE_REFUSED",
                "speech": "That request did not come from you."}
    from . import dashboard_ops as OPS

    try:
        resolution = resolve(text)
    except Exception as exc:
        _audit("turn_error", actor, {"error": str(exc)[:160]})
        return {"state": FAILED, "speech": "I didn't understand that."}
    if resolution.kind != MATCH or resolution.entry is None:
        out: Dict[str, Any] = {"state": resolution.kind,
                               "speech": resolution.speech}
        if resolution.candidates:
            out["candidates"] = [e.id for e in resolution.candidates]
        return out
    entry = resolution.entry
    if entry.backend == "plugin_fleet" or entry.id in MODEL_PROFILE_REGISTRY:
        # Wave-4 vendored-plugin entries and the resolver-backed model-profile
        # intents are not dashboard_ops rows; they run through the shared
        # execute/confirm pipeline so staging, session isolation, e-stop,
        # redaction, and single-use confirmations are the exact same code
        # path — not a parallel dispatcher.
        ctx = make_ctx(cfg, actor=actor, session=session, source=source)
        out = execute(ctx, resolution)
        payload: Dict[str, Any] = {
            "state": out.kind,
            "speech": out.speech,
            "route": entry.id,
            "entry": entry.id,
        }
        if out.receipt:
            payload["receipt"] = out.receipt
        if out.confirmation_id:
            payload["confirmation_id"] = out.confirmation_id
        return payload
    out = OPS.run_op(cfg, entry.id, dict(resolution.args),
                     actor=actor, session=session)
    out.setdefault("route", entry.id)
    return out


# ------------------------------------------------- turn integration
def handle_turn(orch: Any, intent_kind: str, text: str) -> bool:
    """Voice-first routing for one turn; True when fully handled.

    Takes a duck-typed orchestrator (``cfg``/``speak``/``feed_push``) so
    the branch is unit-testable without the audio graph. MATCH executes
    (or stages confirmation) through ``voice_say`` -> the shared
    production dispatcher, CLARIFY/AMBIGUOUS speak the question, and
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
        out = voice_say(orch.cfg, text, actor="voice:user", session="voice",
                        source="user")
        orch.speak(str(out.get("speech", "")) or "Done.")
        try:
            route = str(out.get("route", "") or "")
            orch.feed_push({"kind": "voice-command", "text": text[:160],
                            "route": f"voice:{route}" if route else "voice",
                            "detail": str(out.get("state", ""))})
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
