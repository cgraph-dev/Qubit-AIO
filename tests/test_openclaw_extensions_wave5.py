"""OpenClaw wave-5 extensions — disposition + typed-gate proving test.

The wave-4 extension map covered all 154 ``extensions/*`` plugins, but the
direct source inventory still carried 30 ``inv:openclaw-ext:*`` rows as
``MISSING`` because the categorizer resolves an extension's parent from the
*pinned* ledger (where ``OC-19`` "Broad channel set" is honestly ``PARTIAL``)
and from the upstream plugin category -- it cannot see the wave-4 map.

This test pins the authoritative disposition for exactly those 30. Every one
either

* maps to an audited Qubit capability row (``parent``),
* is a real bounded adapter behind an injected boundary with a typed
  credential/hardware/owner gate (``gate``), or
* is a distinct Qubit form proven by its own test (``distinct``).

It also proves the new channel adapters behave (normalize + signed ingest over
an injected sink), that every gate is typed, and that no second
gateway/router/listener/scheduler/policy authority appears. No network is
opened and no credential is required.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

SECTION_PATH = REPO_ROOT / "docs" / "QUBIT_GAP_LEDGER.openclaw.json"
QUBIT_SECTION_PATH = REPO_ROOT / "docs" / "QUBIT_GAP_LEDGER.qubit.json"

SECRET = "wave5-channel-secret"
SECRET_ENV = "WAVE5_CHANNEL_SECRET"

_TYPED_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{3,}$")

# The 30 remaining OpenClaw extensions, exactly as enumerated by the source
# inventory after the wave-4 map.
WAVE5 = (
    "a2a", "buzz", "clickclack", "cua-computer", "device-pair", "diffs",
    "geolocation", "googlechat", "image-generation-core", "imap", "irc",
    "line", "llm-task", "lobster", "mattermost", "msteams",
    "nextcloud-talk", "nostr", "oc-path", "qa-channel", "raft", "reef",
    "sms", "synology-chat", "test-support", "tlon", "twitch",
    "visitor-access", "zalo", "zalouser",
)

_CHANNEL_PAYLOADS = {
    "a2a": {"from": "u1", "text": "hi", "task_id": "1"},
    "buzz": {"from": "u1", "text": "hi", "id": "1"},
    "clickclack": {"sender": "u1", "text": "hi", "id": "1"},
    "googlechat": {"sender": "u1", "text": "hi", "id": "1"},
    "imap": {"from": "u1@example.test", "body": "hi", "uid": "1"},
    "irc": {"sender": "u1", "text": "hi", "id": "1"},
    "line": {"sender": "u1", "text": "hi", "id": "1"},
    "mattermost": {"sender": "u1", "text": "hi", "id": "1"},
    "msteams": {"sender": "u1", "text": "hi", "id": "1"},
    "nextcloud-talk": {"actor": {"id": "u1"}, "message": "hi", "id": "1"},
    "nostr": {"sender": "u1", "text": "hi", "id": "1"},
    "qa-channel": {"sender": "u1", "text": "hi", "id": "1"},
    "raft": {"from": "u1", "text": "hi", "id": "1"},
    "reef": {"from": "u1", "text": "hi", "id": "1"},
    "sms": {"sender": "u1", "text": "hi", "id": "1"},
    "synology-chat": {"sender": "u1", "text": "hi", "id": "1"},
    "tlon": {"author": "u1", "content": "hi", "id": "1"},
    "twitch": {"username": "u1", "message": "hi", "id": "1"},
    "zalo": {"from_id": "u1", "message": "hi", "message_id": "1"},
    "zalouser": {"from_id": "u1", "message": "hi", "message_id": "1"},
}


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rebuild = _load_module("rebuild_ledger_openclaw_wave5",
                       "scripts/rebuild_ledger_openclaw.py")


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _no_network(monkeypatch):
    import socket

    def _boom(*_a, **_k):
        raise AssertionError("wave-5 disposition must not touch the network")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)


def _sign(secret: str, body: bytes, ts: float) -> str:
    return hmac.new(secret.encode(), f"{ts}.".encode() + body,
                    hashlib.sha256).hexdigest()


def _records() -> dict:
    return {r["name"]: r for r in rebuild.wave5_records()}


def _channel_names() -> list:
    return [r["name"] for r in rebuild.wave5_records()
            if r["gate"] == "CHANNEL_CREDENTIAL_REQUIRED"]


# ---------------------------------------------------------------------------
# Coverage: the table is exactly the remaining inventory rows
# ---------------------------------------------------------------------------
def test_wave5_is_exactly_the_remaining_inventory_rows() -> None:
    assert len(WAVE5) == 30
    assert tuple(rebuild.WAVE5_EXTENSIONS) == WAVE5
    # Every currently-MISSING inv:openclaw-ext row must be dispositioned.
    if QUBIT_SECTION_PATH.is_file():
        section = json.loads(QUBIT_SECTION_PATH.read_text(encoding="utf-8"))
        named = {row["id"][len("inv:openclaw-ext:"):] for row in section["rows"]
                 if row["id"].startswith("inv:openclaw-ext:")}
        assert named <= set(WAVE5), sorted(named - set(WAVE5))


def test_every_wave5_extension_is_mapped_and_typed() -> None:
    records = _records()
    assert set(records) == set(WAVE5)
    ids = {row["id"] for row in
           json.loads(SECTION_PATH.read_text(encoding="utf-8"))["rows"]}
    for name, rec in records.items():
        assert rec["disposition"] in rebuild.EXTENSION_DISPOSITIONS, name
        assert rec["needs"] in rebuild.EXTENSION_NEEDS, name
        assert rec["form"], name
        assert rec["proving"], name
        if rec["disposition"] == "gate":
            # The only gate with no product parent is upstream test
            # scaffolding, which is not a capability at all.
            if rec["gate"] != "TEST_SUPPORT_ONLY":
                assert rec["parent"], name
            assert _TYPED_CODE_RE.match(rec["gate"] or ""), (name, rec["gate"])
        elif rec["disposition"] == "parent":
            assert rec["parent"] in ids, (name, rec["parent"])
            assert not rec["gate"], name
        else:  # distinct
            assert rec["parent"] in ids, (name, rec["parent"])


def test_wave5_summary_is_consistent() -> None:
    records = rebuild.wave5_records()
    summary = rebuild.wave5_summary()
    from collections import Counter

    assert summary["total"] == 30
    assert summary["covered"] is True and summary["unmapped"] == []
    disp = Counter(r["disposition"] for r in records)
    needs = Counter(r["needs"] for r in records)
    assert summary["by_disposition"] == {
        k: disp.get(k, 0) for k in rebuild.EXTENSION_DISPOSITIONS}
    assert summary["by_needs"] == {
        k: needs.get(k, 0) for k in rebuild.EXTENSION_NEEDS}
    assert len(summary["extensions"]) == 30


def test_wave5_ledger_meta_matches_the_generator() -> None:
    section = json.loads(SECTION_PATH.read_text(encoding="utf-8"))
    assert section["extension_wave5"] == rebuild.wave5_summary()
    assert rebuild._render(rebuild.build_ledger()) == \
        SECTION_PATH.read_text(encoding="utf-8")


def test_forms_and_proving_tests_exist_on_disk() -> None:
    for name, rec in _records().items():
        proving = rec["proving"].split("::", 1)[0]
        assert (REPO_ROOT / proving).is_file(), (name, proving)
        form = rec["form"]
        if ".py " not in form:
            continue
        module_part, _, symbols_text = form.partition(" ")
        assert (REPO_ROOT / module_part).is_file(), (name, module_part)
        symbols = symbols_text.split(" (")[0].split("/")
        source = (REPO_ROOT / module_part).read_text(encoding="utf-8")
        for symbol in symbols:
            assert f"def {symbol}" in source, (name, module_part, symbol)


# ---------------------------------------------------------------------------
# Runtime: channel adapters are real, bounded, and honestly unavailable
# ---------------------------------------------------------------------------
def test_channel_adapters_are_registered_and_unavailable() -> None:
    from voicebridge import channels_inbound as CI

    names = _channel_names()
    assert len(names) == 20, names
    for name in names:
        assert name in CI.CHANNELS, name
        caps = CI.CHANNEL_CAPS[name]
        assert caps["available"] is False, name
        assert caps["credential_kind"], name
        assert caps["transport"] not in ("", "https"), name


def test_channel_adapter_normalizes_and_delivers_to_injected_sink(
        tmp_path, monkeypatch) -> None:
    from voicebridge import channels_inbound as CI

    monkeypatch.setenv(SECRET_ENV, SECRET)
    cfg = _cfg(tmp_path, monkeypatch)
    _no_network(monkeypatch)
    seen = []
    for name in _channel_names():
        CI.configure(cfg, name, secret_ref=f"env:{SECRET_ENV}",
                     events=["message"], confirmed=True)
        body = json.dumps(_CHANNEL_PAYLOADS[name]).encode()
        ts = time.time()
        normalized = CI.normalize_event(name, body)
        assert normalized["text"] == "hi", (name, normalized)
        out = CI.ingest(cfg, channel=name, payload=body,
                        signature=_sign(SECRET, body, ts), timestamp=ts,
                        sink=lambda m, _seen=seen: (_seen.append(m),
                                                    {"ok": True})[1])
        assert out["state"] == "DELIVERED", (name, out)
        assert out["message"]["text"] == "hi", (name, out)
    assert len(seen) == len(_channel_names())


def test_channel_adapter_without_sink_stays_typed_unavailable(
        tmp_path, monkeypatch) -> None:
    from voicebridge import channels_inbound as CI

    monkeypatch.setenv(SECRET_ENV, SECRET)
    cfg = _cfg(tmp_path, monkeypatch)
    _no_network(monkeypatch)
    name = _channel_names()[0]
    CI.configure(cfg, name, secret_ref=f"env:{SECRET_ENV}",
                 events=["message"], confirmed=True)
    body = json.dumps(_CHANNEL_PAYLOADS[name]).encode()
    ts = time.time()
    out = CI.receive(cfg, channel=name, payload=body,
                     signature=_sign(SECRET, body, ts), timestamp=ts)
    assert out["state"] == "UNAVAILABLE" and out["code"] == CI.UNAVAILABLE_CODE
    with pytest.raises(CI.ChannelError) as exc:
        CI.normalize_event(name, b"not-json")
    assert exc.value.code == CI.BAD_INPUT_CODE


# ---------------------------------------------------------------------------
# Runtime: non-channel forms and typed gates
# ---------------------------------------------------------------------------
def test_cua_computer_is_a_typed_driver_refusal() -> None:
    from voicebridge import computer_use as CU

    refusal = CU.refuse_install()
    assert refusal["code"] == CU.NO_DRIVER_CODE == "COMPUTER_USE_NO_DRIVER"
    assert refusal["state"] == "REJECTED"


def test_device_pair_and_visitor_access_use_expiring_tokens(
        tmp_path, monkeypatch) -> None:
    from voicebridge import device as DEV

    cfg = _cfg(tmp_path, monkeypatch)
    issued = DEV.token_issue(cfg, device_id="visitor-a", scopes=["node.read"],
                             ttl_s=60.0, confirmed=True)
    assert issued["state"] == "OK" and issued["token"]
    DEV.token_approve(cfg, issued["token_id"], confirmed=True)
    assert DEV.token_verify(cfg, issued["token"], required_scopes=["node.read"])[
        "state"] == "OK"
    blob = json.dumps(DEV.token_list(cfg))
    assert issued["token"] not in blob
    # an expired visitor grant never authenticates
    later = time.time() + 3600.0
    monkeypatch.setattr(DEV.time, "time", lambda: later)
    assert DEV.token_verify(cfg, issued["token"])[
        "code"] == DEV.TOKEN_EXPIRED_CODE


def test_geolocation_and_llm_task_are_injectable_boundaries() -> None:
    from voicebridge import local_models as LM
    from voicebridge import node_host as NH

    assert NH.geolocate("127.0.0.1")["code"] == NH.NODE_GEO_BAD_INPUT
    assert NH.geolocate("93.184.216.34")["code"] == NH.NODE_GEO_UNAVAILABLE
    ok = NH.geolocate("93.184.216.34",
                      resolver=lambda _ip: {"city": "Example"})
    assert ok["state"] == "OK" and ok["city"] == "Example"
    assert LM.structured_task(None, prompt="")["code"] == \
        LM.TASK_BAD_INPUT_CODE


def test_media_and_web_forms_are_bounded() -> None:
    from voicebridge import media_gen as MG
    from voicebridge import web_tool as WT

    shaped = MG.shape_media_request("fal", "image", "a cat")
    assert shaped["endpoint"] == "fal:image"
    out = WT.readable_text("<article><p>Hello <b>world</b></p></article>")
    assert out["state"] == WT.OK and out["text"] == "Hello world"


def test_diffs_and_oc_path_are_real_forms(tmp_path, monkeypatch) -> None:
    from voicebridge import file_patch as FP
    from voicebridge import files as F
    from voicebridge import projects as PJ

    cfg = _cfg(tmp_path, monkeypatch)
    assert callable(FP.apply_patch)
    # oc-path: confined file addressing only (unapproved roots typed-refused),
    # and an approved project folder reads normally.
    outside = tmp_path / "outside.txt"
    outside.write_text("nope\n", encoding="utf-8")
    with pytest.raises(F.FilesError) as exc:
        F.read_file(cfg, str(outside))
    assert exc.value.code == F.OUTSIDE_ROOT_CODE

    folder = tmp_path / "work" / "site"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "note.txt").write_text("hello\n", encoding="utf-8")
    created = PJ.create(cfg, "Wave5", [str(folder)], requested_by="cli:user",
                        confirmed=True)
    PJ.use(cfg, created["project"]["slug"], requested_by="cli:user",
           confirmed=True)
    listed = F.list_dir(cfg, str(folder))
    assert listed["state"] == "OK"
    assert F.read_file(cfg, str(folder / "note.txt"))["state"] == "OK"


# ---------------------------------------------------------------------------
# No second authority
# ---------------------------------------------------------------------------
def test_no_second_listener_or_router_is_created(tmp_path, monkeypatch) -> None:
    from voicebridge import channels_inbound as CI
    from voicebridge import messaging as MG

    cfg = _cfg(tmp_path, monkeypatch)
    _no_network(monkeypatch)
    assert CI.LISTENER["state"] == "NONE"
    status = CI.status(cfg)
    assert status["listener"] == "NONE" and status["connected"] == 0
    assert CI.catalog(cfg)["connected"] == []
    # the send-side availability set is unchanged: no channel fabricated as live
    catalog = MG.provider_catalog()
    assert set(catalog["available"]) == {"whatsapp-cloud", "slack"}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
