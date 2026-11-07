"""Vision shadow, cloud half: TypeSafe Jev answers the same picture question a vision tool
just answered, off the hot path, and one row of ids, hashes and answer classes is logged.

This is the Jev-Omni shadow (omni_vision.py) with TypeSafe's own Jev as the model. It takes
the same calls (``vision_analyze`` questions of a decision shape), asks the same closed set
(PASS/FAIL or Yes/No) as a Choice, and logs to the same ``logs/jev-vision.jsonl`` with
``model: typesafe-jev``, so the two read side by side against the helper model's answer.

**The picture leaves the machine.** That is the difference from the local shadow, and why
this is its own switch, ``vision_cloud``, off by default with its own kill switch
(``VISION_CLOUD_OFF``). The question goes through ``privacy.redact`` and a question that
looks sensitive is not sent; the picture itself cannot be screened, so switching this on is
the decision to send screenshots and renders to TypeSafe.

Images are TypeSafe's "Dreamer-ready" preview (docs.typesafe.ai/images): not production-ready,
and the request may change. Here that costs nothing: a shadow never changes a tool result,
and every refusal or error is one logged row.

Nothing is resized or cropped (standard library only): an image outside the API's limits, or
a call with a ``region``, is skipped with a reason.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from . import client, images, ledger, privacy
from .omni_vision import OPTIONS, _append, _digest, _materialize, _sha256, answer_class, omni_question, question_shape

FEATURE = "vision_cloud"
MODEL_NAME = "typesafe-jev"
TIMEOUT_S = 20.0
# The Jev-Omni cut-off, kept until this model has its own A/B on the labelled checks. Jev's
# confidence is calibrated, but a calibrated number on a new input type is still a number
# nobody has measured here, so the row records the threshold it was judged against.
THRESHOLD = 0.6


def _question(text: str, shape: str, ref: str) -> Dict[str, Any]:
    options = OPTIONS[shape]
    meaning = {"PASS": "The image passes the check.", "FAIL": "The image fails the check.",
               "Yes": "The answer to the question is yes.", "No": "The answer to the question is no."}
    return client.choice(f"Look at the image `image`. {omni_question(privacy.redact(text), shape)}",
                         {option: meaning[option] for option in options})


def job(payload: Dict[str, Any], queued: float, mode: Callable[[], str], dropped: Callable[[], int],
        asker: Optional[Callable[..., Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """Ask TypeSafe Jev about one call and log one row. Returns the row (tests)."""
    if mode() != "shadow":
        return None
    asker = asker or client.ask
    question = str(payload.get("question") or "")
    shape = question_shape(question)
    current = answer_class(payload.get("current_text"), shape) if shape != "open" else None
    row: Dict[str, Any] = {
        "tool": payload.get("tool"), "session": _digest(payload.get("session_id")),
        "tool_call_id": payload.get("tool_call_id"), "turn_id": payload.get("turn_id"),
        "question_sha": _digest(question), "shape": shape, "region": bool(payload.get("region")),
        "current_path": payload.get("current_path"), "current_class": current,
        "current_chars": payload.get("current_chars"), "queued_ms": int((time.monotonic() - queued) * 1000),
        "queue_dropped": dropped(), "threshold": THRESHOLD, "model": MODEL_NAME}

    def finish(status: str, **more: Any) -> Dict[str, Any]:
        row.update(status=status, **more)
        _append(row)
        if status in ("ok", "error"):
            local = bool(row.get("handled_locally"))
            ledger.append({"feature": FEATURE, "mode": "shadow", "jev_model": row.get("jev_model") or MODEL_NAME,
                           "provider": "typesafe", "n_questions": 1, "input_tokens": row.get("input_tokens") or 0,
                           **ledger.cost(row.get("input_tokens"), "typesafe", row.get("jev_model")),
                           "latency_ms": row.get("wall_ms"), "status": status, "source": "jev",
                           "error": row.get("error"), "action": row.get("jev_class"), "shadow": True,
                           "fallback_used": status != "ok" or not local, "queue_dropped": row["queue_dropped"],
                           "llm_avoided_est": int((row.get("current_chars") or 0) / 4) if local else 0})
        return row

    if shape == "open":
        return finish("skipped", reason="open_question")
    if payload.get("region"):
        return finish("skipped", reason="region_unsupported")
    if privacy.is_sensitive(question):
        return finish("skipped", reason="sensitive_question")
    path, reason, temp = _materialize(str(payload.get("image") or ""))
    if path is None:
        return finish("skipped", reason=reason)
    try:
        if not temp and payload.get("stat") is not None:
            st = path.stat()
            if (st.st_size, st.st_mtime_ns) != tuple(payload["stat"]):
                return finish("skipped", reason="image_changed")
        row["image_sha256"] = _sha256(path)
        try:
            ref, picture = images.asset(path)
        except images.ImageError as error:
            return finish("skipped", reason=f"image_{error.code}")
        except OSError:
            return finish("skipped", reason="image_gone")
        # A user can disable the feature while a queued image is being read/encoded.
        # Recheck immediately before the outbound call; an already in-flight HTTP request
        # cannot be recalled, but image preparation must not freeze earlier consent.
        if mode() != "shadow":
            return finish("skipped", reason="disabled_before_send")
        started = time.monotonic()
        try:
            out = asker({"image": ref}, {"verdict": _question(question, shape, ref)},
                        assets={ref: picture}, timeout=TIMEOUT_S, retries=1, provider="typesafe")
        except client.JevError as error:
            return finish("error", wall_ms=int((time.monotonic() - started) * 1000), error=error.code[:80])
        wall_ms = int((time.monotonic() - started) * 1000)
        verdict = out["answers"]["verdict"]
        answer, confidence = verdict["choice"], float(verdict["confidence"])
        local = confidence >= THRESHOLD
        agree = None if current in (None, "unclear") else (answer.lower() == current.lower())
        return finish("ok", jev_class=answer, jev_confidence=round(confidence, 4), handled_locally=local,
                      agree=agree, hybrid_class=answer if local else current, wall_ms=wall_ms,
                      jev_model=out.get("jev_model"), input_tokens=out.get("input_tokens"))
    finally:
        if temp:
            try:
                path.unlink()
            except OSError:
                pass
