"""OpenClaw extension map — coverage and typed-gate proving test.

The direct source inventory enumerates the pinned OpenClaw ``extensions/*``
plugins (154 at the frozen v2026.9.4 audit pin). The canonical generated
inventory tracks the current OpenClaw pin, whose manifests no longer carry the
category metadata the frozen map depends on, so the frozen audit inventory is
read read-only through the generator's own resolver. Agent D maps every one of
them to either

* (a) an existing audited capability row (``disposition == "parent"``),
* (b) a credential/provider/transport gate with a typed reason
  (``disposition == "gate"``), or
* (c) a genuinely distinct capability Qubit implements behind an injected
  boundary (``disposition == "distinct"``).

This test proves the map is complete (zero unexplained), every cited parent id
really exists in the regenerated OpenClaw ledger, every gate carries a typed
code and an allowed ``needs`` value, the (c) implementations exist and behave,
and the provider/transport gates really resolve to typed refusals at runtime
(no fabricated success, no network, no credential).
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

SECTION_PATH = REPO_ROOT / "docs/generated" / "QUBIT_GAP_LEDGER.openclaw.json"

_TYPED_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{3,}$")


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rebuild = _load_module("rebuild_ledger_openclaw_extmap",
                       "rebuild_ledger_openclaw.py")


def _inventory_extensions() -> dict:
    path = rebuild._inventory_path()
    assert path is not None, "no resolvable OpenClaw extension inventory"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(it.get("name")): it for it in data.get("items", [])
            if isinstance(it, dict)
            and it.get("category") == "openclaw_extension"}


def _section() -> dict:
    return json.loads(SECTION_PATH.read_text(encoding="utf-8"))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------
def test_inventory_present_with_expected_extensions() -> None:
    extensions = _inventory_extensions()
    assert len(extensions) == 154, len(extensions)
    assert "a2a" in extensions and "anthropic" in extensions


def test_every_extension_is_mapped_with_zero_unexplained() -> None:
    extensions = set(_inventory_extensions())
    mapped = set(rebuild.EXTENSION_MAP)
    assert mapped == extensions, {
        "unmapped": sorted(extensions - mapped),
        "unknown": sorted(mapped - extensions),
    }
    summary = rebuild.extension_map_summary()
    assert summary["total"] == 154
    assert summary["unexplained"] == []
    assert summary["covered"] is True


def test_every_parent_id_exists_in_openclaw_ledger() -> None:
    ids = {row["id"] for row in _section()["rows"]}
    offenders = []
    for name, rec in rebuild.EXTENSION_MAP.items():
        parent = rec.get("parent", "")
        if parent and parent not in ids:
            offenders.append((name, parent))
    assert offenders == [], offenders


def test_dispositions_and_gates_are_typed() -> None:
    for name, rec in rebuild.EXTENSION_MAP.items():
        disp = rec["disposition"]
        assert disp in rebuild.EXTENSION_DISPOSITIONS, (name, disp)
        assert rec["needs"] in rebuild.EXTENSION_NEEDS, (name, rec["needs"])
        if disp == "parent":
            assert rec["parent"], (name, "parent disposition needs a row id")
            assert not rec["gate"], (name, "parent must not carry a gate code")
        elif disp == "gate":
            # A product capability gate backs onto an audited row; the only
            # exception is upstream test scaffolding, which is not a product
            # capability at all and therefore cites no row.
            if rec["gate"] != "TEST_SUPPORT_ONLY":
                assert rec["parent"], (name, "gate needs a backing row id")
            assert _TYPED_CODE_RE.match(rec["gate"] or ""), (name, rec["gate"])
        elif disp == "distinct":
            assert rec["impl"], (name, "distinct needs an implementation ref")


def test_distinct_implementations_exist_on_disk() -> None:
    for name, rec in rebuild.EXTENSION_MAP.items():
        if rec["disposition"] != "distinct":
            continue
        module_part, _, symbol = rec["impl"].partition(" ")
        assert (REPO_ROOT / module_part).is_file(), (name, module_part)
        source = (REPO_ROOT / module_part).read_text(encoding="utf-8")
        assert f"def {symbol}" in source, (name, symbol)


def test_summary_counts_match_map() -> None:
    summary = rebuild.extension_map_summary()
    from collections import Counter

    disp = Counter(r["disposition"] for r in rebuild.EXTENSION_MAP.values())
    needs = Counter(r["needs"] for r in rebuild.EXTENSION_MAP.values())
    assert summary["by_disposition"] == {
        k: disp.get(k, 0) for k in rebuild.EXTENSION_DISPOSITIONS}
    assert summary["by_needs"] == {
        k: needs.get(k, 0) for k in rebuild.EXTENSION_NEEDS}


# ---------------------------------------------------------------------------
# Ledger regeneration
# ---------------------------------------------------------------------------
def test_regenerated_section_is_deterministic() -> None:
    first = rebuild._render(rebuild.build_ledger())
    second = rebuild._render(rebuild.build_ledger())
    assert first == second


def test_committed_section_carries_the_extension_map() -> None:
    assert rebuild._render(rebuild.build_ledger()) == \
        SECTION_PATH.read_text(encoding="utf-8")
    section = _section()
    ext = section["extension_map"]
    assert ext["total"] == 154
    assert ext["covered"] is True
    assert ext["unexplained"] == []
    assert ext["by_disposition"] == {"parent": 28, "gate": 123, "distinct": 3}


# ---------------------------------------------------------------------------
# Runtime typed-gate proofs (injected boundaries only, no network/creds)
# ---------------------------------------------------------------------------
def test_model_provider_gate_is_typed(tmp_path, monkeypatch) -> None:
    from voicebridge import local_models as LM

    cfg = _cfg(tmp_path, monkeypatch)
    task = LM.structured_task(cfg, prompt="summarise this")
    assert task["state"] == "UNAVAILABLE"
    assert task["code"] == LM.TASK_UNAVAILABLE_CODE


def test_media_provider_gate_is_typed(tmp_path, monkeypatch) -> None:
    from voicebridge import media_gen as MG

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(MG.MediaGenError) as exc:
        MG.generate(cfg, kind="music", provider="fal", prompt="lofi",
                    confirmed=True)
    assert exc.value.code == MG.UNAVAILABLE_CODE
    # shaping still works (the request contract is real, only egress is gated)
    shaped = MG.shape_media_request("fal", "music", "lofi")
    assert shaped["endpoint"] == "fal:music"


def test_web_provider_gate_is_typed(tmp_path, monkeypatch) -> None:
    from voicebridge import web_tool as WT

    cfg = _cfg(tmp_path, monkeypatch)
    out = WT.search(cfg, "qubit")
    assert out["state"] == WT.UNAVAILABLE
    assert out["code"] == WT.SEARCH_UNAVAILABLE_CODE


def test_voice_provider_gate_is_typed(tmp_path, monkeypatch) -> None:
    from voicebridge import vision as VI

    cfg = _cfg(tmp_path, monkeypatch)
    out = VI.transcribe(cfg, b"not-audio")
    assert out["state"] == "UNAVAILABLE"
    assert out["code"] == VI.MEDIA_UNAVAILABLE_CODE


def test_channel_provider_gate_is_typed() -> None:
    from voicebridge import messaging as MS

    catalog = {row["provider"]: row
               for row in MS.provider_catalog()["providers"]}
    for provider in ("signal", "msteams", "imessage"):
        row = catalog[provider]
        assert row["state"] == "UNAVAILABLE", (provider, row)
        assert row["code"] == MS.UNAVAILABLE_CODE, (provider, row)


def test_distinct_web_readability_extracts_bounded_text() -> None:
    from voicebridge import web_tool as WT

    out = WT.readable_text(
        "<html><body><nav>skip</nav><article><p>Hello <b>world</b></p>"
        "</article><script>evil()</script></body></html>")
    assert out["state"] == WT.OK
    assert out["text"] == "Hello world"
    assert "evil" not in out["text"]
    assert WT.readable_text("")["code"] == WT.READABILITY_EMPTY_CODE
    capped = WT.readable_text("<article>" + "word " * 50 + "</article>",
                              max_chars=20)
    assert capped["chars"] <= 20 and capped["truncated"] is True


def test_distinct_llm_task_requires_injected_runner(tmp_path, monkeypatch) -> None:
    from voicebridge import local_models as LM

    cfg = _cfg(tmp_path, monkeypatch)
    assert LM.structured_task(cfg, prompt="")["code"] == \
        LM.TASK_BAD_INPUT_CODE
    gate = LM.structured_task(cfg, prompt="give me json")
    assert gate["code"] == LM.TASK_UNAVAILABLE_CODE
    ok = LM.structured_task(
        cfg, prompt="give me json", schema={"required": ["answer"]},
        runner=lambda _req: '{"answer": 42}')
    assert ok["state"] == "OK" and ok["result"] == {"answer": 42}
    bad = LM.structured_task(cfg, prompt="x",
                             runner=lambda _req: "not json")
    assert bad["code"] == LM.TASK_BAD_INPUT_CODE


def test_distinct_geolocation_requires_injected_resolver() -> None:
    from voicebridge import node_host as NH

    assert NH.geolocate("not-an-ip")["code"] == NH.NODE_GEO_BAD_INPUT
    assert NH.geolocate("127.0.0.1")["code"] == NH.NODE_GEO_BAD_INPUT
    gate = NH.geolocate("93.184.216.34")
    assert gate["code"] == NH.NODE_GEO_UNAVAILABLE
    ok = NH.geolocate("93.184.216.34",
                      resolver=lambda _ip: {"city": "Example",
                                            "country": "US"})
    assert ok["state"] == "OK" and ok["city"] == "Example"
    secret = NH.geolocate(
        "93.184.216.34",
        resolver=lambda _ip: {"city": "Bearer sk-secret-token-value"})
    assert "sk-secret-token-value" not in json.dumps(secret)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
