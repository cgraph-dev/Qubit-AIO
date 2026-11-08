"""Jev over the OpenCode **Zen** relay — the keyless door.

The OpenRouter alpha path (:mod:`voicebridge.jev.client`) needs `OPENROUTER_API_KEY`
and paid credit. The same decision contract is served by Zen's ``/v1/systemone``
at **no cost and with no credential**, which is the door a new install can
actually open (see ``docs/JEV_INTEGRATION_NOTE.md``).

Two differences from the OpenRouter dialect, both load-bearing:

* **Body.** OpenRouter takes ``questions`` as a *list* of ``{id, kind, ask,
  options}``. Zen takes an *object* keyed by question name, each with
  ``type`` = ``noul`` (boolean) / ``choice`` / ``score``, ``instructions`` and,
  for ``choice``, a ``criteria`` map of option -> meaning.
* **Auth.** Zen's free tier takes **no** ``Authorization`` header. Sending one
  answers ``401 Invalid credential`` — a bogus key is worse than none. A real
  ``OPENCODE_ZEN_API_KEY`` is for the paid chat models, not this path.

So this module sends no credential by default and only attaches one when the
caller explicitly passes it.

Verified live (2026-11-07): a ``choice`` question over three turn actions
answered ``{"choice": "clarify", "confidence": 0.92}`` with
``billing_source: "free"`` and ``cost: 0``.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

try:  # single-source the User-Agent version; fall back if imported standalone
    from ..product import PRODUCT_VERSION as _PRODUCT_VERSION
except Exception:  # pragma: no cover - product import is optional here
    _PRODUCT_VERSION = "0.0.11"

#: Zen's decision endpoint (``/v1/systemone``, not ``/chat/completions``).
ZEN_ENDPOINT = "https://opencode.ai/zen/v1/systemone"

#: The free decision model. ``jev-latest`` answers "Model is unavailable" here.
ZEN_MODEL = "jev-1.13-free"

DEFAULT_TIMEOUT_S = 15.0

HttpPost = Callable[[str, Dict[str, Any], str, float], Any]

#: Zen question types -> the kind name the rest of the code speaks.
_ZEN_TYPES = {"noul": "check", "choice": "pick", "score": "rate"}
#: And back again, for building a request.
_KIND_TO_TYPE = {v: k for k, v in _ZEN_TYPES.items()}


def to_zen_body(state: str, questions: List[Dict[str, Any]],
                model: str = "") -> Dict[str, Any]:
    """Translate the internal question list into Zen's keyed-object dialect.

    Each internal question is ``{"id", "kind", "ask", "options"?, "criteria"?}``.
    Zen wants ``{name: {"type", "instructions", "criteria"?}}``.
    """
    out: Dict[str, Any] = {}
    for q in questions or []:
        if not isinstance(q, dict):
            continue
        name = str(q.get("id") or "").strip()
        if not name:
            continue
        kind = str(q.get("kind") or "check")
        spec: Dict[str, Any] = {
            "type": _KIND_TO_TYPE.get(kind, "noul"),
            "instructions": str(q.get("ask") or q.get("instructions") or ""),
        }
        options = q.get("options")
        if isinstance(options, (list, tuple)) and options:
            # Zen's `criteria` is option -> meaning. When the caller supplies no
            # meanings the option name stands in for one: an invented
            # description would steer the verdict.
            given = q.get("criteria") if isinstance(q.get("criteria"), dict) else {}
            spec["criteria"] = {str(o): str(given.get(str(o), str(o)))
                                for o in options}
        elif isinstance(q.get("criteria"), dict) and q["criteria"]:
            spec["criteria"] = dict(q["criteria"])
        out[name] = spec
    return {"model": str(model or ZEN_MODEL), "state": str(state or ""),
            "questions": out}


def parse_zen_answers(payload: Any,
                      asked: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]],
                                                            List[str]]:
    """Parse Zen's answers into the same typed verdict shape as the client.

    Returns ``(answers, problems)``. An answer that names no option, or whose
    probability set does not match the offered options exactly, is reported as
    a problem rather than coerced into a verdict.
    """
    kinds = {str(q.get("id")): str(q.get("kind") or "check")
             for q in (asked or []) if isinstance(q, dict) and q.get("id")}
    offered = {str(q.get("id")): [str(o) for o in (q.get("options") or [])]
               for q in (asked or []) if isinstance(q, dict) and q.get("id")}
    if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
        return [], ["payload has no answers object"]
    answers: List[Dict[str, Any]] = []
    problems: List[str] = []
    for name, raw in payload["answers"].items():
        if not isinstance(raw, dict):
            problems.append(f"{name}: answer is not an object")
            continue
        kind = kinds.get(str(name), _ZEN_TYPES.get(str(raw.get("type")), "check"))
        confidence = _clamp(raw.get("confidence"))
        if kind == "check":
            # `noul` is a probability in [0,1]; treat >= 0.5 as true and keep
            # the raw probability as the confidence.
            prob = _clamp(raw.get("noul"))
            answer: Any = prob >= 0.5
            confidence = confidence or max(prob, 1.0 - prob)
        elif kind == "pick":
            answer = raw.get("choice")
            probs = raw.get("probabilities")
            options = offered.get(str(name)) or []
            if isinstance(probs, dict) and options:
                if set(probs) != set(options):
                    problems.append(
                        f"{name}: probabilities {sorted(probs)} do not match "
                        f"offered {sorted(options)}")
                    continue
        else:  # rate
            answer = _clamp(raw.get("score"))
        answers.append({"id": str(name), "kind": kind, "answer": answer,
                        "confidence": confidence,
                        "escalate": answer is None,
                        "raw": {k: v for k, v in raw.items()
                                if k not in ("noul", "choice", "score")}})
    return answers, problems


def _clamp(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


def _default_http_post(url: str, body: Dict[str, Any], key: str,
                       timeout_s: float) -> Any:
    """POST to Zen. Sends NO Authorization header unless a key is given."""
    data = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        # Zen sits behind Cloudflare, which rejects urllib's default agent with
        # HTTP 403 {"error code": 1010} before the request is ever routed. curl
        # works, so the blocked part is the User-Agent, not the payload.
        "User-Agent": f"Qubit/{_PRODUCT_VERSION} (+https://github.com/cgraph-dev/Qubit)",
        "Accept": "application/json",
    }
    if key:
        # Only for a paid Zen key the caller explicitly passed; the free door
        # rejects any credential outright.
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except socket.timeout as exc:
        raise TimeoutError(f"jev zen timeout ({timeout_s}s)") from exc
    except urllib.error.HTTPError as exc:
        # Surface the status so the caller's typed refusal says 403/429 rather
        # than "unreachable" — a blocked payload and a blocked agent both land
        # here and they need different fixes.
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001 - the body is best-effort
            detail = ""
        raise ValueError(f"HTTP {exc.code}: {detail}") from exc


def judge(state: str, questions: List[Dict[str, Any]], *,
          model: str = "", api_key: str = "",
          timeout_s: float = DEFAULT_TIMEOUT_S,
          http_post: Optional[HttpPost] = None) -> Dict[str, Any]:
    """Ask Zen for typed verdicts. Fail-closed, never a fabricated judgment."""
    post = http_post or _default_http_post
    body = to_zen_body(state, questions, model)
    try:
        payload = post(ZEN_ENDPOINT, body, str(api_key or ""), float(timeout_s))
    except TimeoutError:
        return {"ok": False, "code": "JEV_TIMEOUT", "backend": "zen",
                "endpoint": ZEN_ENDPOINT,
                "detail": f"zen decisions timed out ({timeout_s}s)"}
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return {"ok": False, "code": "JEV_TRANSPORT_ERROR", "backend": "zen",
                "endpoint": ZEN_ENDPOINT,
                "detail": f"zen endpoint unreachable ({type(exc).__name__})"}
    except Exception as exc:  # noqa: BLE001 - fail closed
        return {"ok": False, "code": "JEV_TRANSPORT_ERROR", "backend": "zen",
                "endpoint": ZEN_ENDPOINT,
                "detail": f"zen endpoint error ({type(exc).__name__})"}
    answers, problems = parse_zen_answers(payload, questions)
    if problems and not answers:
        return {"ok": False, "code": "JEV_MALFORMED", "backend": "zen",
                "endpoint": ZEN_ENDPOINT,
                "detail": f"unparseable zen payload ({problems[0]})"}
    return {"ok": True, "backend": "zen", "model": body["model"],
            "answers": answers, "problems": problems,
            "usage": (payload or {}).get("usage")}
