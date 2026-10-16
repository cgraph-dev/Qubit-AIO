"""The office SSE `notice` frame must match the client's toast contract.

THE DEFECT THIS PINS
--------------------
The vendored office client's toast handler (`app-*.js`, minified) does:

    push(e){ let t = `${e.level}|${e.agentId ?? ""}|${e.text}`;
             let n = this.toasts.find(n => n.key === t && e.at - n.notice.at < QC);
             ...
             this.arm(r, $C[e.level]) }          // $C = {info:5e3,success:6e3,warn:9e3,alert:1/0}
    arm(e,t){ ... Number.isFinite(t) && (... setTimeout(()=>this.dismiss(e), t)) }

So the badge/toast lifetime is chosen by `$C[e.level]`. The server emitted only
`{"kind": "error", "text": ...}` -> `e.level` was `undefined` -> `$C[undefined]`
was `undefined` -> `Number.isFinite(undefined)` false -> NO timer armed -> the
error toast never dismissed. The dedup clock `e.at - n.notice.at` was also `NaN`
(e.at absent), so repeated notices stacked instead of collapsing.

These tests assert the RELATIONSHIP (every emitted notice carries a level the
client understands and an `at`), not a snapshot of today's strings.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from voicebridge import office_stream as OS

REPO = Path(__file__).resolve().parents[1]
BUNDLE = REPO / "frontend/qubit-app/plugins/qubit-office/habblaud/bundle"


def _client_levels() -> set[str]:
    """Read the client's own `$C={...}` duration map out of the bundle.

    This keeps the test honest: if the client's toast vocabulary changes, the
    assertion follows it rather than pinning a stale literal.
    """
    for f in BUNDLE.glob("app-*.js"):
        src = f.read_text(errors="ignore")
        m = re.search(r"\$C=\{([^}]*)\}", src)
        if m:
            levels = set()
            for part in m.group(1).split(","):
                k = part.split(":")[0].strip()
                if k:
                    levels.add(k)
            if levels:
                return levels
    pytest.skip("client toast level map not found in bundle")


def test_notice_carries_a_level_the_client_understands():
    levels = _client_levels()
    for lvl in ("info", "warn", "success", "alert"):
        n = OS.notice("boom", level=lvl)
        assert n["level"] in levels, (n["level"], levels)


def test_notice_always_has_at_and_text():
    n = OS.notice("the office store could not be read: x", level="warn",
                  kind="error")
    assert isinstance(n["at"], int) and n["at"] > 0
    assert n["text"]
    # `kind` is kept for Qubit-side readers; the client ignores unknown keys.
    assert n["kind"] == "error"


def test_an_unknown_level_cannot_make_an_immortal_toast():
    """A level outside the client's vocabulary arms NO timer.

    The producer must coerce, not pass through: otherwise a future caller can
    re-introduce the exact defect this module fixed.
    """
    levels = _client_levels()
    n = OS.notice("x", level="catastrophe")
    assert n["level"] in levels


def test_the_producer_uses_the_notice_builder():
    """The SSE route must not hand-roll a `{kind,text}` notice again."""
    src = (REPO / "voicebridge/webui.py").read_text()
    # find the office stream handler body
    i = src.find("def _serve_office_stream")
    j = src.find("def _serve_office_access", i)
    body = src[i:j]
    assert 'OS.sse("notice", OS.notice(' in body, (
        "the office SSE notice must go through office_stream.notice() so it "
        "carries the client's `level` + `at` contract")


def test_the_builder_output_is_json_serialisable_and_framed():
    frame = OS.sse("notice", OS.notice("store read failed", level="warn"))
    assert frame.startswith(b"event: notice\ndata: ")
    assert frame.endswith(b"\n\n")
    import json
    payload = json.loads(frame.split(b"data: ", 1)[1].rstrip(b"\n"))
    assert set(("kind", "level", "text", "at")) <= set(payload)
