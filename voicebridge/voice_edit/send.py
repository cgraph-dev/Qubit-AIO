"""Voice reply sending: parse, draft, resolve, execute.

A spoken "reply to X saying Y" becomes a staged draft
(``reply|<channel>|<to>|<href>|<body>``), confirmed by voice ("yes"),
then executed with send-time re-verification (recipient re-resolved
fresh; the staged href must still match). Sending is IRREVERSIBLE, so
every layer fails closed and typed: unparseable, ambiguous, missing
recipient, missing sender mechanism, or failed verification all refuse
without touching anything.

Channels: gmail (CDP reply-in-thread; himalaya compose fallback when a
CLI account exists), messenger + instagram (CDP thread composer).
Computer-control stays DISABLED by policy except this narrow,
owner-authorized, dictated-and-confirmed reply path (STATUS §62).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

CHANNELS = ("gmail", "messenger", "instagram")

_DRAFT_RE = re.compile(r"^reply\|([^|]*)\|([^|]*)\|([^|]*)\|(.*)$", re.DOTALL)

_REPLY_VERBS = re.compile(
    r"\b(reply|respond|answer back|write back|send(\s+\S+)?\s+(a\s+)?message"
    r"|\bemail\b|\bmessage\b|\btext\b"
    r"|\btell\b|\blet\b.+\bknow\b)", re.IGNORECASE)

_CHANNEL_WORDS = (
    (re.compile(r"\bgmail\b|\bemail\b|\be-?mail\b", re.IGNORECASE), "gmail"),
    (re.compile(r"\bmessenger\b|\bfacebook\b|\bfb\b", re.IGNORECASE),
     "messenger"),
    (re.compile(r"\binstagram\b|\binsta\b", re.IGNORECASE), "instagram"),
)

_TO_SPLIT = re.compile(
    r"\breply\s+to\s+(?P<to>.+?)\s+(?:saying|say|that|:)\s*(?P<body>.+)$"
    r"|\btell\s+(?P<to2>.+)\s+that\s+(?P<body2>.+)$"
    r"|\btell\s+(?P<to2b>.+?)\s+(?P<body2b>.+)$"
    r"|\banswer\s+(?P<to3>.+?)\s+(?:with\s+)?(?P<body3>.+)$"
    r"|\bsend\s+(?:a\s+message\s+to\s+)?(?P<to4>.+?)\s+saying\s+(?P<body4>.+)$"
    r"|\b(?:email|message|text)\s+(?P<to5>\S+?)\s+(?:saying|say|that|:)\s*"
    r"(?P<body5>.+)$",
    re.IGNORECASE | re.DOTALL)

_ADDR_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def parse_reply(question: str) -> Optional[Dict[str, str]]:
    """Parse a spoken reply into {channel, to_hint, body}.

    Returns None when the utterance is not a reply-with-content (reads,
    bare "reply to X" with no body, or no channel). Never raises.
    """
    try:
        q = (question or "").strip()
        if not q or not _REPLY_VERBS.search(q):
            return None
        channel = ""
        for rx, name in _CHANNEL_WORDS:
            if rx.search(q):
                channel = name
                break
        if not channel:
            return None
        m = _TO_SPLIT.search(q)
        if not m:
            return None
        to = (m.group("to") or m.group("to2") or m.group("to2b")
              or m.group("to3") or m.group("to4") or m.group("to5")
              or "").strip()
        body = (m.group("body") or m.group("body2") or m.group("body2b")
                or m.group("body3") or m.group("body4") or m.group("body5")
                or "").strip()
        # Strip channel/address scaffolding from the recipient ("Pro Nume
        # on messenger", "the GitHub email" -> "Pro Nume", "GitHub").
        to = re.sub(r"\s+on\s+(gmail|messenger|facebook|fb|instagram|insta)$",
                    "", to, flags=re.IGNORECASE).strip()
        to = re.sub(r"\s+(email|e-mail|mail|message|chat|thread|dm)$", "", to,
                    flags=re.IGNORECASE).strip()
        to = re.sub(r"^(the|my)\s+", "", to, flags=re.IGNORECASE).strip()
        if not to or not body:
            return None
        addr = _ADDR_RE.search(to)
        out = {"channel": channel, "to_hint": to, "body": body}
        if addr and channel == "gmail":
            out["to_hint"] = addr.group(0)
            out["address"] = addr.group(0)
        return out
    except Exception:
        return None


def match_recipient(rows: List[Tuple[str, str]],
                    hint: str) -> Optional[Tuple[str, str]]:
    """Match a recipient hint against [(name, href-or-extra)] rows.

    Exactly-one case-insensitive substring hit wins; zero or ambiguous
    refuse with None (never guess between people). Never raises.
    """
    try:
        h = (hint or "").strip().lower()
        if not h or not rows:
            return None
        hits = [(n, x) for n, x in rows
                if h in str(n or "").lower()]
        if len(hits) != 1:
            return None
        return hits[0]
    except Exception:
        return None


def build_draft(channel: str, to: str, href: str, body: str) -> str:
    """Canonical staged draft (machine-readable, no re-interpretation)."""
    clean = lambda s: str(s or "").replace("|", "/").strip()
    return f"reply|{clean(channel)}|{clean(to)}|{clean(href)}|{body.strip()}"


def parse_draft(text: str) -> Optional[Dict[str, str]]:
    """Split a canonical draft; None when malformed. Never raises."""
    try:
        m = _DRAFT_RE.match((text or "").strip())
        if not m:
            return None
        channel, to, href, body = m.groups()
        if channel not in CHANNELS or not to.strip() or not body.strip():
            return None
        return {"channel": channel, "to": to.strip(),
                "href": href.strip(), "body": body.strip()}
    except Exception:
        return None


def default_skill_dir() -> str:
    """QUBIT_HOME skills dir without needing a cfg object."""
    try:
        from .. import qubit_runtime as _HR
        home = Path(_HR.default_home())
        skills = home / "skills"
        if skills.is_dir():
            return str(skills)
    except Exception:
        pass
    return ""


def _script(name: str) -> str:
    base = default_skill_dir()
    if not base:
        return ""
    cand = Path(base) / "communication" / "social-inbox-check" / "scripts" / name
    return str(cand) if cand.is_file() else ""


def read_rows(script: str, timeout: float = 45.0) -> Optional[list]:
    """Run a reader script; parsed JSON list or None. Never raises."""
    if not script:
        return None
    try:
        proc = subprocess.run([sys.executable, script, "5"],
                              capture_output=True, text=True,
                              timeout=timeout)
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout or "")
        return data if isinstance(data, list) else None
    except Exception:
        return None


_READER = {"gmail": "read_gmail.py", "messenger": "read_messenger.py",
           "instagram": "read_instagram.py"}


def resolve(channel: str, hint: str,
            readers: Optional[Dict[str, Callable[[], Any]]] = None
            ) -> Optional[Tuple[str, str]]:
    """Resolve (name, href) for a hint on a channel (fresh read).

    ``readers`` overrides per channel (tests / service-bound readers);
    default readers run the skill scripts. Gmail rows carry sender in
    [0] and thread identity in the row dict (third element when
    available). Never raises; None = unresolvable.
    """
    try:
        if channel not in CHANNELS or not (hint or "").strip():
            return None
        rows: List[Tuple[str, str]] = []
        def _norm(items: Any, *, loc_is_name: bool = False
                  ) -> List[Tuple[str, str, str]]:
            """Normalize dict rows {name,sender,href,...} or (a, b) tuples
            into (name, locator, extra). Gmail locates by sender name, so
            its tuples normalize locator=name. Never raises."""
            out: List[Tuple[str, str, str]] = []
            for it in items or []:
                if isinstance(it, dict):
                    name = str(it.get("name") or it.get("sender") or ""
                               ).strip()
                    loc = str(it.get("href") or it.get("name")
                              or it.get("sender") or "").strip()
                    extra = str(it.get("preview") or it.get("text")
                                or it.get("subject") or "").strip()
                elif isinstance(it, (list, tuple)) and len(it) >= 1:
                    name = str(it[0] or "").strip()
                    loc = (name if loc_is_name else str(
                        it[1] if len(it) > 1 and it[1] else name).strip())
                    extra = ""
                else:
                    continue
                if name and loc:
                    out.append((name, loc, extra))
            return out

        norm = _norm(readers[channel]() if readers and channel in readers
                     else read_rows(_script(_READER[channel])),
                     loc_is_name=(channel == "gmail"))
        hit = match_recipient([(n, loc) for n, loc, _e in norm], hint)
        return hit
    except Exception:
        return None


def _sender_script() -> str:
    cand = Path(__file__).resolve().parents[2] / "scripts" / "send_social.py"
    return str(cand) if cand.is_file() else ""


def _run_sender(script: str, args: List[str]) -> Dict[str, Any]:
    """Run the sender script; typed result dict. Never raises."""
    try:
        proc = subprocess.run([sys.executable, script] + list(args or []),
                              capture_output=True, text=True, timeout=90)
    except Exception as exc:
        return {"ok": False, "code": "SEND_TRANSPORT",
                "detail": str(exc)[:160]}
    try:
        out = json.loads(proc.stdout or "")
    except Exception:
        return {"ok": False, "code": "SEND_BAD_RESPONSE",
                "detail": (proc.stdout or proc.stderr or "")[:160]}
    if proc.returncode != 0 or not out.get("ok"):
        return {"ok": False,
                "code": str(out.get("code") or "SEND_FAILED"),
                "detail": str(out.get("detail") or "")[:200]}
    return {"ok": True, "detail": str(out.get("detail") or "")[:200]}


def execute(draft: Dict[str, str], *, dry_run: bool = False) -> Dict[str, Any]:
    """Send a staged draft (or dry-run it). Typed result, never raises.

    Re-resolves the recipient fresh and requires the staged href to still
    match; any mismatch refuses without sending (no TOCTOU).
    """
    try:
        channel = draft.get("channel", "")
        to = draft.get("to", "")
        href = draft.get("href", "")
        body = draft.get("body", "")
        if channel not in CHANNELS or not to or not body:
            return {"ok": False, "code": "SEND_MALFORMED",
                    "detail": "staged draft is incomplete"}
        if channel == "gmail" and href.startswith("addr:"):
            address = href[len("addr:"):]
            if not _ADDR_RE.fullmatch(address):
                return {"ok": False, "code": "SEND_MALFORMED",
                        "detail": "bad address draft"}
            script = _sender_script()
            if not script:
                return {"ok": False, "code": "SEND_UNAVAILABLE",
                        "detail": "sender script missing"}
            out = _run_sender(script, ["--channel", "gmail",
                                       "--to", address, "--href", "",
                                       "--body", body]
                              + (["--dry-run"] if dry_run else ["--send"]))
            if out.get("ok"):
                out = dict(out, channel=channel, to=to)
            return out
        # Locator semantics per channel: messenger = thread href,
        # instagram = thread name (clicked), gmail = sender name
        # (thread re-resolved by sender at send time).
        fresh = resolve(channel, to)
        if fresh is None:
            return {"ok": False, "code": "SEND_RECIPIENT_GONE",
                    "detail": f"could not re-resolve {to!r} on {channel}"}
        _fresh_name, fresh_href = fresh
        if href and channel != "gmail" and fresh_href != href:
            return {"ok": False, "code": "SEND_TARGET_CHANGED",
                    "detail": "thread changed since staging; re-confirm"}
        script = _sender_script()
        if not script:
            return {"ok": False, "code": "SEND_UNAVAILABLE",
                    "detail": "sender script missing"}
        out = _run_sender(script, ["--channel", channel,
                                   "--to", to, "--href", fresh_href,
                                   "--body", body]
                          + (["--dry-run"] if dry_run else ["--send"]))
        if out.get("ok"):
            out = dict(out, channel=channel, to=to)
        return out
    except Exception as exc:
        return {"ok": False, "code": "SEND_FAILED",
                "detail": str(exc)[:160]}


__all__ = ["CHANNELS", "parse_reply", "match_recipient", "build_draft",
           "parse_draft", "resolve", "execute", "read_rows"]
