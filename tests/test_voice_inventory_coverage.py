"""W7 — voice-inventory coverage: every entry is wired, the new CLI-subcommand
bridges resolve, and no new entry leaks a legacy brand.

Scope (additive; does NOT pin a registry count — the frozen wave-1..3 example
table ``test_voice_commands8tf.py`` stays authoritative for ``REGISTRY``):

* ``voice_commands.CLI_BRIDGE_REGISTRY`` is the W7 additive registry holding the
  CLI-subcommand bridges/guards (so the frozen example table is never
  disturbed), alongside the ``cron`` aliases and the ``kanban`` lifecycle
  aliases added to existing entries;
* real bindings reuse existing safe backends (``plugin_runtime`` /
  ``plugins_install`` / ``schedules`` / ``kanban``), refusal guards reuse the
  audited ``voice_unsupported`` backend;
* every entry in every voice registry must declare a real ``_BACKENDS``
  handler (0 dead), and every new alias must resolve to its intended entry.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# The legacy brand tokens that must never appear on a new product surface.
# (The allowlisted legal attribution lives only in LICENSE /
# THIRD_PARTY_NOTICES.md and is never a voice registry string.)
LEGACY_TOKENS = ("hermes", "nousresearch", "nous portal", "nousportal",
                 "openclaw", "nous research")


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _all_registries(VC):
    return {
        "REGISTRY": VC.REGISTRY,
        "PLUGIN_FLEET_REGISTRY": VC.PLUGIN_FLEET_REGISTRY,
        "MODEL_PROFILE_REGISTRY": VC.MODEL_PROFILE_REGISTRY,
        "CLI_BRIDGE_REGISTRY": VC.CLI_BRIDGE_REGISTRY,
    }


# Every new spoken alias -> the entry id it must resolve to. Aliases that take
# a required arg are given one inline so resolution reaches MATCH.
NEW_ALIAS_BINDINGS = {
    # kanban lifecycle aliases onto the existing kanban backend
    "qubit kanban complete 5": "kanban_complete",
    "qubit complete kanban task 5": "kanban_complete",
    "qubit kanban unblock 5": "kanban_unblock",
    "qubit unblock kanban task 5": "kanban_unblock",
    # plugins capabilities onto the existing plugin_runtime backend
    "qubit plugins capabilities": "plugins_capabilities",
    "qubit plugin capabilities": "plugins_capabilities",
    "qubit list plugin capabilities": "plugins_capabilities",
    # skills install onto the existing skills/plugin-manifest backend
    "qubit skills install /tmp/manifest.json": "skills_install",
    "qubit install a skill manifest /tmp/manifest.json": "skills_install",
    # cron family naming onto the existing schedules_* backend
    "qubit cron list": "schedules_list",
    "qubit list cron jobs": "schedules_list",
    "qubit cron jobs": "schedules_list",
    "qubit create cron job": "schedules_create",
    "qubit cron create": "schedules_create",
    "qubit show cron job 3": "schedules_show",
    "qubit cron pause 3": "schedules_pause",
    "qubit pause cron job 3": "schedules_pause",
    "qubit cron resume 3": "schedules_resume",
    "qubit resume cron job 3": "schedules_resume",
    "qubit cron delete 3": "schedules_delete",
    "qubit delete cron job 3": "schedules_delete",
    "qubit cron history": "schedules_history",
    "qubit cron job history": "schedules_history",
    "qubit cron run": "schedules_run_due",
    "qubit run due cron jobs": "schedules_run_due",
    # audited refusal guards for upstream kanban shapes with no Qubit construct
    "qubit kanban request review 5": "guard_kanban_request_review",
    "qubit request review of a kanban task 5": "guard_kanban_request_review",
    "qubit kanban request changes 5": "guard_kanban_request_changes",
    "qubit request changes on a kanban task 5": "guard_kanban_request_changes",
    "qubit kanban reopen review 5": "guard_kanban_reopen_review",
    "qubit reopen review of a kanban task 5": "guard_kanban_reopen_review",
    "qubit kanban link task 5": "guard_kanban_link",
    "qubit link a kanban task 5": "guard_kanban_link",
    "qubit kanban unlink task 5": "guard_kanban_unlink",
    "qubit unlink a kanban task 5": "guard_kanban_unlink",
}

GUARD_IDS = {
    "guard_kanban_request_review",
    "guard_kanban_request_changes",
    "guard_kanban_reopen_review",
    "guard_kanban_link",
    "guard_kanban_unlink",
}


# --------------------------------------------------------------- (a) 0 dead
def test_every_voice_entry_binds_a_real_backend():
    """No entry in any voice registry may reference an unknown backend."""
    from voicebridge import voice_commands as VC

    dead = []
    empty = []
    for name, registry in _all_registries(VC).items():
        for entry in registry.values():
            if not entry.backend:
                empty.append((name, entry.id))
            elif entry.backend not in VC._BACKENDS:
                dead.append((name, entry.id, entry.backend))
    assert empty == [], f"entries without a backend: {empty}"
    assert dead == [], f"entries referencing unknown backends: {dead}"


def test_cli_bridge_ids_and_aliases_are_unique():
    """Idempotent safety net: bridge ids/aliases collide with nothing."""
    from voicebridge import voice_commands as VC

    ids = [e.id for e in VC._iter_entries()]
    assert len(ids) == len(set(ids)), "duplicate voice entry ids"
    owners: dict = {}
    for entry in VC._iter_entries():
        for alias in entry.aliases:
            owners.setdefault(alias, []).append(entry.id)
    collisions = {a: o for a, o in owners.items() if len(o) > 1}
    assert collisions == {}, f"voice alias collisions: {collisions}"


# --------------------------------------------------- (b) new aliases resolve
def test_new_aliases_resolve_to_their_intended_entry():
    from voicebridge import voice_commands as VC

    misrouted = []
    for text, expected in NEW_ALIAS_BINDINGS.items():
        res = VC.resolve(text)
        got = res.entry.id if res.entry is not None else None
        if res.kind not in (VC.MATCH, VC.CLARIFY) or got != expected:
            misrouted.append((text, expected, res.kind, got))
    assert not misrouted, f"new aliases did not resolve: {misrouted}"


def test_cli_bridge_registry_is_mounted_on_the_one_pipeline():
    """Bridges are reachable by id lookup and through resolve(), like every
    other registry (one resolve/execute/confirm pipeline, no side registry)."""
    from voicebridge import voice_commands as VC

    assert VC.CLI_BRIDGE_REGISTRY, "the W7 bridge registry is empty"
    assert len(list(VC._iter_entries())) > len(VC.REGISTRY)
    for entry_id in VC.CLI_BRIDGE_REGISTRY:
        assert VC._lookup_entry(entry_id) is not None, entry_id


def test_kanban_guards_are_real_typed_refusals(tmp_path, monkeypatch):
    """The unbound upstream kanban shapes are audited refusals, not silence."""
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="w7", source="cli")
    for phrase in ("qubit kanban request review 5",
                   "qubit kanban request changes 5",
                   "qubit kanban link task 5",
                   "qubit kanban unlink task 5",
                   "qubit kanban reopen review 5"):
        res = VC.resolve(phrase)
        assert res.kind == VC.MATCH, (phrase, res.kind, res.speech)
        assert res.entry.id in GUARD_IDS, (phrase, res.entry.id)
        out = VC.execute(ctx, res)
        assert out.kind == VC.EXECUTED, (phrase, out.kind, out.speech)
        result = out.receipt.get("result", {})
        assert result.get("state") == "refused", (phrase, result)
        assert result.get("code"), (phrase, result)
        assert out.confirmation_id == "", "a refusal must stage nothing"


def test_real_bindings_execute_or_stage_on_a_safe_backend(
        tmp_path, monkeypatch):
    """plugins capabilities / cron list execute real reads; kanban complete /
    skills install stage exactly one confirmation on the existing backends."""
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="w7", source="cli")

    caps = VC.execute(ctx, VC.resolve("qubit plugins capabilities"))
    assert caps.kind == VC.EXECUTED, (caps.kind, caps.speech)
    body = caps.receipt["result"]["plugins_runtime"]
    assert body.get("state") == "OK" and body.get("schema"), body

    listed = VC.execute(ctx, VC.resolve("qubit cron list"))
    assert listed.kind == VC.EXECUTED, (listed.kind, listed.speech)

    for phrase in ("qubit kanban complete 5", "qubit skills install /tmp/x.json"):
        staged = VC.execute(ctx, VC.resolve(phrase))
        assert staged.kind == VC.NEEDS_CONFIRM, (phrase, staged.kind,
                                                  staged.speech)
        assert staged.confirmation_id, phrase


# ------------------------------------------------------- (c) no legacy brand
def test_new_entry_strings_have_no_legacy_brand():
    from voicebridge import voice_commands as VC

    def strings_of(entry) -> list:
        out = [entry.id, entry.title, entry.help, entry.summary,
               entry.backend, entry.split, *entry.aliases, *entry.examples]
        for key, value in entry.fixed or ():
            out.extend([str(key), str(value)])
        for arg in entry.args:
            out.extend([arg.name, arg.type, arg.clarifier,
                        *arg.choices])
        return out

    offenders = []
    for entry in VC.CLI_BRIDGE_REGISTRY.values():
        for value in strings_of(entry):
            low = str(value).lower()
            for token in LEGACY_TOKENS:
                if token in low:
                    offenders.append((entry.id, token, value))
    # The new aliases grafted onto existing entries must be clean too.
    for text in NEW_ALIAS_BINDINGS:
        low = text.lower()
        for token in LEGACY_TOKENS:
            if token in low:
                offenders.append(("alias", token, text))
    assert offenders == [], f"legacy brand in new voice strings: {offenders}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
