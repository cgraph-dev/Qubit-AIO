"""Packet 8U/D21 — structured multi-question prompts (``ask_user``).

A bounded, persisted question contract: ``ask`` validates and stores a
prompt (choice or free-text questions) under the state dir with 0600
permissions; ``answer`` validates the replies against the stored contract
and rejects unknown, expired, and already-answered (replay) prompts. Voice
and CLI render the choices through :func:`render`.

Provenance: tool/media delta D21 in
``docs/archive/2026-09-21/AUDIT_OPENCLAW_V2026_9_4_DELTA.md`` (upstream
``src/agents/tools/question-prompt-send.ts``).
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

CONFIRM_CODE = "QUESTION_CONFIRM_REQUIRED"
BAD_INPUT_CODE = "QUESTION_BAD_INPUT"
TOO_MANY_CODE = "QUESTION_TOO_MANY"
ESTOP_CODE = "QUESTION_ESTOP"
UNKNOWN_CODE = "QUESTION_UNKNOWN"
EXPIRED_CODE = "QUESTION_EXPIRED"

SCHEMA_VERSION = 1
MAX_QUESTIONS = 10
MAX_QUESTION_CHARS = 500
MAX_CHOICES = 12
MAX_CHOICE_CHARS = 80
MAX_ANSWER_CHARS = 2000
MAX_PENDING = 50
DEFAULT_CHOICE_LIMIT = 12
DEFAULT_TTL_S = 600.0
MIN_TTL_S = 5.0
MAX_TTL_S = 86400.0
KINDS = ("choice", "text")

_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class QuestionError(Exception):
    """Typed question-prompt failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _now() -> float:
    return time.time()


def _redact(text: Any) -> str:
    from .redaction import redact_text

    try:
        return redact_text(str(text or ""))
    except Exception:
        return "?"


def _audit(op: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event

        log_event(f"question.{op}",
                  **{k: str(v)[:160] for k, v in detail.items()})
    except Exception:
        pass


def _estop(cfg: Any) -> bool:
    try:
        from .plugins import estop_engaged

        return bool(estop_engaged(cfg))
    except Exception:
        return True


def _settings(cfg: Any) -> Dict[str, float]:
    get = (lambda *p, default=None: cfg.get(*p, default=default)) \
        if hasattr(cfg, "get") else (lambda *p, default=None: default)
    try:
        ttl = float(get("questions", "ttl_s", default=DEFAULT_TTL_S))
    except (TypeError, ValueError):
        ttl = DEFAULT_TTL_S
    return {"ttl_s": max(MIN_TTL_S, min(ttl, MAX_TTL_S))}


def _store_path(cfg: Any) -> str:
    get = (lambda *p, default=None: cfg.get(*p, default=default)) \
        if hasattr(cfg, "get") else (lambda *p, default=None: default)
    configured = get("questions", "store_path", default="") or ""
    if configured:
        return os.fspath(configured)
    return os.fspath(Path(cfg.state_dir()) / "question-prompts.json")


def _lock(path: str):
    import fcntl

    lock_path = path + ".lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise QuestionError(BAD_INPUT_CODE,
                            f"cannot open prompt lock ({exc})")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError as exc:
        os.close(fd)
        raise QuestionError(BAD_INPUT_CODE,
                            f"cannot lock prompt store ({exc})")
    return fd


def _unlock(fd) -> None:
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _load(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"schema_version": SCHEMA_VERSION, "prompts": {}}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise QuestionError(UNKNOWN_CODE,
                            f"prompt store unreadable ({_redact(exc)[:120]})")
    if not isinstance(raw, dict) or not isinstance(raw.get("prompts"), dict):
        raise QuestionError(UNKNOWN_CODE, "prompt store is malformed")
    return raw


def _save(path: str, data: Dict[str, Any]) -> None:
    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise QuestionError(BAD_INPUT_CODE,
                            f"cannot create prompt dir ({exc})")
    payload = json.dumps(data, sort_keys=True).encode("utf-8")
    tmp = f"{path}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        raise QuestionError(BAD_INPUT_CODE,
                            f"cannot persist prompt ({exc})")


def _validate_questions(questions: Any, choice_limit: Any
                        ) -> List[Dict[str, Any]]:
    if not isinstance(questions, (list, tuple)) or not questions:
        raise QuestionError(BAD_INPUT_CODE,
                            "questions must be a non-empty list")
    if len(questions) > MAX_QUESTIONS:
        raise QuestionError(
            TOO_MANY_CODE,
            f"{len(questions)} questions exceeds the limit of "
            f"{MAX_QUESTIONS}")
    try:
        limit = int(choice_limit)
    except (TypeError, ValueError):
        limit = DEFAULT_CHOICE_LIMIT
    if limit < 1:
        raise QuestionError(BAD_INPUT_CODE, "choice_limit must be positive")
    limit = min(limit, MAX_CHOICES)
    specs: List[Dict[str, Any]] = []
    seen_ids = set()
    for index, raw in enumerate(questions):
        if not isinstance(raw, dict):
            raise QuestionError(BAD_INPUT_CODE, "each question must be an object")
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise QuestionError(BAD_INPUT_CODE, "question text is required")
        text = text.strip()
        if len(text) > MAX_QUESTION_CHARS:
            raise QuestionError(
                BAD_INPUT_CODE,
                f"question text exceeds {MAX_QUESTION_CHARS} chars")
        qid = raw.get("id") or f"q{index + 1}"
        if not isinstance(qid, str) or not _ID_RE.match(qid):
            raise QuestionError(BAD_INPUT_CODE, f"bad question id {qid!r}")
        if qid in seen_ids:
            raise QuestionError(BAD_INPUT_CODE, f"duplicate question id {qid!r}")
        seen_ids.add(qid)
        kind = raw.get("kind", "text")
        if kind not in KINDS:
            raise QuestionError(BAD_INPUT_CODE,
                                f"question kind must be one of {KINDS}")
        spec: Dict[str, Any] = {"id": qid, "text": text, "kind": kind}
        if kind == "choice":
            choices = raw.get("choices")
            if not isinstance(choices, (list, tuple)) or not choices:
                raise QuestionError(BAD_INPUT_CODE,
                                    f"question {qid!r} needs choices")
            if len(choices) > limit:
                raise QuestionError(
                    TOO_MANY_CODE,
                    f"question {qid!r} has {len(choices)} choices "
                    f"(limit {limit})")
            cleaned: List[str] = []
            for choice in choices:
                if not isinstance(choice, str) or not choice.strip():
                    raise QuestionError(BAD_INPUT_CODE,
                                        f"question {qid!r} has a blank choice")
                choice = choice.strip()
                if len(choice) > MAX_CHOICE_CHARS:
                    raise QuestionError(
                        BAD_INPUT_CODE,
                        f"choice exceeds {MAX_CHOICE_CHARS} chars")
                cleaned.append(choice)
            if len(set(cleaned)) != len(cleaned):
                raise QuestionError(BAD_INPUT_CODE,
                                    f"question {qid!r} has duplicate choices")
            spec["choices"] = cleaned
        specs.append(spec)
    return specs


def _validate_answers(questions: List[Dict[str, Any]], answers: Any
                      ) -> Dict[str, Any]:
    if answers is None:
        raise QuestionError(BAD_INPUT_CODE, "answers are required")
    mapping: Dict[str, Any] = {}
    if isinstance(answers, dict):
        mapping = dict(answers)
    elif isinstance(answers, (list, tuple)):
        for item in answers:
            if (not isinstance(item, dict) or "id" not in item
                    or "value" not in item):
                raise QuestionError(
                    BAD_INPUT_CODE,
                    "list answers must be {'id': ..., 'value': ...} objects")
            mapping[str(item["id"])] = item["value"]
    else:
        raise QuestionError(BAD_INPUT_CODE,
                            "answers must be an object or a list")
    known = {spec["id"] for spec in questions}
    extra = set(mapping) - known
    if extra:
        raise QuestionError(BAD_INPUT_CODE,
                            f"unknown answer ids: {sorted(extra)}")
    normalized: Dict[str, Any] = {}
    for spec in questions:
        qid = spec["id"]
        if qid not in mapping:
            raise QuestionError(BAD_INPUT_CODE,
                                f"missing answer for {qid!r}")
        value = mapping[qid]
        if spec["kind"] == "choice":
            if not isinstance(value, str) or value not in spec["choices"]:
                raise QuestionError(
                    BAD_INPUT_CODE,
                    f"answer for {qid!r} must be one of {spec['choices']}")
        else:
            if not isinstance(value, str) or not value.strip():
                raise QuestionError(BAD_INPUT_CODE,
                                    f"answer for {qid!r} must be text")
            if len(value) > MAX_ANSWER_CHARS:
                raise QuestionError(
                    BAD_INPUT_CODE,
                    f"answer for {qid!r} exceeds {MAX_ANSWER_CHARS} chars")
            value = value.strip()
        normalized[qid] = value
    return normalized


def _reap(data: Dict[str, Any], now: float) -> None:
    prompts = data.get("prompts") or {}
    for pid in list(prompts):
        record = prompts[pid]
        if record.get("answered") or now >= float(record.get("expires_at", 0)):
            prompts.pop(pid, None)


def status(cfg: Any) -> Dict[str, Any]:
    """Read-only pending-prompt report."""
    path = _store_path(cfg)
    pending = 0
    try:
        fd = _lock(path)
        try:
            data = _load(path)
        finally:
            _unlock(fd)
        now = _now()
        pending = sum(1 for rec in data.get("prompts", {}).values()
                      if not rec.get("answered")
                      and now < float(rec.get("expires_at", 0)))
    except QuestionError:
        pending = 0
    return {"state": "OK", "schema": "qubit-question-status/1",
            "kinds": list(KINDS), "pending": pending,
            "max_questions": MAX_QUESTIONS, "max_choices": MAX_CHOICES,
            "ttl_s": _settings(cfg)["ttl_s"], "store": path,
            "estop": _estop(cfg)}


def ask(cfg: Any, questions: Any, *, session: str = "",
        choice_limit: int = DEFAULT_CHOICE_LIMIT,
        confirmed: bool = False) -> Dict[str, Any]:
    """Create and persist a pending multi-question prompt contract."""
    if _estop(cfg):
        raise QuestionError(ESTOP_CODE, "emergency stop is latched")
    if not confirmed:
        raise QuestionError(CONFIRM_CODE,
                            "asking requires explicit confirmation")
    specs = _validate_questions(questions, choice_limit)
    settings = _settings(cfg)
    now = _now()
    prompt_id = "qp" + uuid.uuid4().hex
    record = {"prompt_id": prompt_id, "session": str(session or "")[:64],
              "created_at": now, "expires_at": now + settings["ttl_s"],
              "answered": False, "answered_at": None,
              "questions": specs, "answers": None}
    path = _store_path(cfg)
    fd = _lock(path)
    try:
        data = _load(path)
        if len(data.get("prompts", {})) >= MAX_PENDING:
            _reap(data, now)
        if len(data.setdefault("prompts", {})) >= MAX_PENDING:
            raise QuestionError(TOO_MANY_CODE,
                                "too many pending prompts")
        data["prompts"][prompt_id] = record
        _save(path, data)
    finally:
        _unlock(fd)
    _audit("ask", {"prompt_id": prompt_id, "count": len(specs),
                   "session": record["session"]})
    return {"state": "OK", "schema": "qubit-question-prompt/1",
            "prompt_id": prompt_id, "questions": specs, "answers": None,
            "expires_at": record["expires_at"], "ttl_s": settings["ttl_s"]}


def answer(cfg: Any, prompt_id: str, answers: Any) -> Dict[str, Any]:
    """Validate and record answers; reject unknown/expired/replayed prompts."""
    if _estop(cfg):
        raise QuestionError(ESTOP_CODE, "emergency stop is latched")
    pid = str(prompt_id or "").strip()
    if not pid:
        raise QuestionError(BAD_INPUT_CODE, "prompt_id is required")
    path = _store_path(cfg)
    now = _now()
    fd = _lock(path)
    try:
        data = _load(path)
        record = (data.get("prompts") or {}).get(pid)
        if record is None:
            raise QuestionError(UNKNOWN_CODE, f"no pending prompt {pid!r}")
        if record.get("answered"):
            raise QuestionError(UNKNOWN_CODE,
                                f"prompt {pid!r} was already answered")
        if now >= float(record.get("expires_at", 0)):
            raise QuestionError(EXPIRED_CODE, f"prompt {pid!r} has expired")
        normalized = _validate_answers(record.get("questions", []), answers)
        record["answered"] = True
        record["answered_at"] = now
        record["answers"] = normalized
        _save(path, data)
    finally:
        _unlock(fd)
    _audit("answer", {"prompt_id": pid, "count": len(normalized)})
    return {"state": "OK", "schema": "qubit-question-answer/1",
            "prompt_id": pid, "answers": normalized, "answered_at": now}


def pending(cfg: Any) -> Dict[str, Any]:
    """List pending (unanswered, unexpired) prompt contracts."""
    path = _store_path(cfg)
    now = _now()
    fd = _lock(path)
    try:
        data = _load(path)
    finally:
        _unlock(fd)
    items = [{"prompt_id": rec["prompt_id"],
              "session": rec.get("session", ""),
              "questions": rec.get("questions", []),
              "expires_at": rec.get("expires_at")}
             for rec in (data.get("prompts") or {}).values()
             if not rec.get("answered")
             and now < float(rec.get("expires_at", 0))]
    return {"state": "OK", "schema": "qubit-question-pending/1",
            "pending": items}


def render(contract: Any) -> str:
    """Render prompt questions/choices for voice or CLI (bounded)."""
    if not isinstance(contract, dict):
        return ""
    lines: List[str] = []
    for index, question in enumerate(contract.get("questions", []) or [], 1):
        if not isinstance(question, dict):
            continue
        text = str(question.get("text", ""))[:MAX_QUESTION_CHARS]
        if question.get("kind") == "choice":
            choices = " / ".join(
                f"[{str(c)[:MAX_CHOICE_CHARS]}]"
                for c in question.get("choices", []) or [])
            lines.append(f"{index}. {text} {choices}".strip())
        else:
            lines.append(f"{index}. {text} (free text)")
    return "\n".join(lines)[:4000]
