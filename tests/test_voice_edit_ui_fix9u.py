"""VoiceEdit UI fix (9u) — source-level binding assertions.

Parses the frontend sources + the settings backend (read-only) and asserts the
new editors are bound to real PUT keys. No services, no browser, no dist.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TSX = REPO / "frontend/qubit-web/src/pages/VoiceEditPage.tsx"
TS = REPO / "frontend/qubit-web/src/lib/voiceEdit.ts"
SETTINGS = REPO / "voicebridge/voice_edit/service.py"
SETTINGS_PY = REPO / "voicebridge/voice_edit/settings.py"

NEW_TESTIDS = [
    "voice-edit-local-profile",
    "voice-edit-wake-phrase",
    "voice-edit-wake-silence",
    "voice-edit-wake-rms",
    "voice-edit-wake-engine",
    "voice-edit-wake-kws",
    "voice-edit-per-app",
    "voice-edit-advanced",
    "voice-edit-request_ttl_s",
    "voice-edit-brain_timeout_s",
    "voice-edit-release_tail_ms",
    "voice-edit-disable_key_repeat",
    "voice-edit-text_commands",
    "voice-edit-mic_target_peak",
    "voice-edit-stt_engine",
    "voice-edit-default_prompt",
]

# PUT keys the new editors send (per_app rides a dedicated branch, not
# SETTINGS_KEYS — see test_per_app_payload_shape).
SETTINGS_KEYED = [
    "local_profile",
    "wake",
    "request_ttl_s",
    "brain_timeout_s",
    "release_tail_ms",
    "disable_key_repeat",
    "text_commands",
    "mic_target_peak",
    "stt_engine",
    "default_prompt",
]

WAKE_SUBKEYS = ["phrase", "silence_ms", "rms_gate", "engine", "kws_threshold"]


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _settings_keys(src: str) -> set:
    m = re.search(r"SETTINGS_KEYS\s*=\s*\((.*?)\)", src, re.S)
    assert m, "SETTINGS_KEYS tuple not found in settings.py"
    return set(re.findall(r'"([a-z_0-9]+)"', m.group(1)))


def test_each_new_testid_exists_in_tsx():
    tsx = _read(TSX)
    for tid in NEW_TESTIDS:
        assert tid in tsx, f"testid {tid!r} missing from VoiceEditPage.tsx"


def test_local_profile_keeps_plain_text_input():
    tsx = _read(TSX)
    i = tsx.find("voice-edit-local-profile")
    assert i != -1
    window = tsx[max(0, i - 600): i + 300]
    assert "<input" in window, "local_profile must stay a plain-text <input>"
    assert "<select" not in window.split("<input")[0][-200:], \
        "local_profile must not become a select"


def test_put_keys_in_settings_keys_or_dedicated_branch():
    src = _read(SETTINGS_PY)
    keys = _settings_keys(src)
    for k in SETTINGS_KEYED:
        assert k in keys, f"PUT key {k!r} not in SETTINGS_KEYS"
    # per_app is accepted via its own branch (deep-merge dict of app->action).
    assert 'if "per_app" in update' in src, "per_app branch missing in settings.py"


def test_put_keys_in_ts_types():
    ts = _read(TS)
    for k in ["local_profile", "request_ttl_s", "brain_timeout_s",
              "release_tail_ms", "disable_key_repeat", "text_commands",
              "mic_target_peak", "stt_engine", "default_prompt",
              "default_action", "per_app"]:
        assert k in ts, f"key {k!r} missing from voiceEdit.ts types"
    for k in ["engine", "kws_threshold"]:
        assert k in ts, f"wake sub-key {k!r} missing from voiceEdit.ts wake type"
    assert "VOICE_EDIT_ACTIONS" in ts, "VOICE_EDIT_ACTIONS list missing"


def test_wake_payload_shape():
    tsx = _read(TSX)
    for k in WAKE_SUBKEYS:
        assert k in tsx, f"wake sub-key {k!r} missing from VoiceEditPage.tsx"
    # One merged wake:{...} PUT on save (backend deep-merges the wake object).
    assert re.search(r"wake:\s*\{", tsx), "no merged wake:{...} PUT in tsx"
    assert "saveWake" in tsx, "saveWake handler missing"
    # Backend accepts each sub-key and clamps to the documented ranges.
    src = _read(SETTINGS_PY)
    for k in WAKE_SUBKEYS:
        assert f'"{k}"' in src, f"wake.{k} not handled in settings.py"
    assert "300.0" in src and "5000.0" in src, "silence_ms clamp missing"
    assert "0.05" in src and "0.9" in src, "kws_threshold clamp missing"
    # Client clamps mirror the backend ranges.
    assert "WAKE_LIMITS" in _read(TS), "WAKE_LIMITS missing from voiceEdit.ts"


def test_per_app_payload_shape():
    tsx = _read(TSX)
    # add-row: per_app merge with the new app key
    assert re.search(r"per_app:\s*\{\s*\.\.\.\(settings\.per_app", tsx), \
        "per-app add/delete merge PUT missing"
    # delete-row: PUT "" for that key (backend keeps it as inert, never typed)
    assert '[app]: ""' in tsx, "per-app delete (empty-string pin) missing"
    # clear-all: PUT per_app:{} (backend sibling makes this actually clear)
    assert re.search(r"update\(\{\s*per_app:\s*\{\}\s*\}", tsx), \
        "per-app clear-all PUT per_app:{} missing"
    # backend keeps "" as inert rather than validating it as an action
    src = _read(SETTINGS_PY)
    assert "if v else" in src or "if v else \"\"" in src, \
        "per_app empty-pin inert path missing in settings.py"
    # action select draws from the shared action list
    assert "VOICE_EDIT_ACTIONS" in tsx, "per-app action select options missing"


def test_default_prompt_wire_key():
    tsx = _read(TSX)
    assert re.search(r"update\(\{\s*default_prompt:", tsx), \
        "default_prompt must PUT the default_prompt wire key"
    src = _read(SETTINGS_PY)
    assert 'if "default_prompt" in clean' in src, \
        "backend default_prompt wire-key branch missing"


def test_labels_are_honest():
    tsx = _read(TSX)
    assert "Auto (default source)" in tsx, "capture_source Auto label not fixed"
    assert "browser-grade AGC" not in tsx, "stale Auto label still present"
    assert "match browser capture" not in tsx, "stale noise label still present"


def test_answer_questions_omitted_by_design():
    """answer_questions is persistence-only (no consumer in service.py), so no
    checkbox is rendered — a control that cannot act would be dishonest."""
    service = _read(SETTINGS)
    assert "answer_questions" not in service, \
        "answer_questions gained a service consumer: add the checkbox"
    tsx = _read(TSX)
    assert "voice-edit-answer" not in tsx, \
        "answer_questions checkbox must stay omitted while unwired"


def test_infra_keys_stay_api_only():
    tsx = _read(TSX)
    assert "voice-edit-service_url" not in tsx, "service_url must stay API-only"
    assert "voice-edit-stt_ws_url" not in tsx, "stt_ws_url must stay API-only"
    assert "service_url, stt_ws_url" in tsx or "service_url" in tsx, \
        "Advanced section should note the infra keys stay API-only"
