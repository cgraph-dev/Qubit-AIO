"""Wave B3 Task 4 (backend only): /qubit-chat browser verbs resolve via the shared voice registry.

Plan: docs/superpowers/plans/2026-09-30-qubit-chat-voice-routing.md Task 4 Steps 1-2.
Scope: resolve() checks only — no broker/execute, no CDP, no new clients/stores.
Backends stay on the existing ``browser_tabs`` module (the same module the
CLI/dashboard call through ``_b_tabs``); read-only (read tab) and mutating
(open/close tab) entries stay separate so confirmation tiers differ.
Auth-gated origins answer typed login-needed and unknown sites clarify/fail
typed at execute time — never a fake "opened" (backend ``_check_url`` only
accepts http(s); voice ``execute`` surfaces the typed error).
"""


def test_qubitchat_browser_open_resolves_registry():
    from voicebridge import voice_commands as VC
    r = VC.resolve("open youtube in the browser")
    assert r.kind in ("match", "clarify", "ambiguous"), r


def test_qubitchat_browser_open_site_arg():
    from voicebridge import voice_commands as VC
    r = VC.resolve("open youtube in the browser")
    assert r.kind == "match", r
    assert r.entry is not None and r.entry.id == "browser_open", r
    assert str(r.args.get("url", "")).strip() == "youtube", r


def test_qubitchat_browser_open_full_url():
    from voicebridge import voice_commands as VC
    r = VC.resolve("open https://example.com in the browser")
    assert r.kind == "match", r
    assert r.entry is not None and r.entry.id == "browser_open", r
    assert "example.com" in str(r.args.get("url", "")), r


def test_qubitchat_browser_open_without_the():
    from voicebridge import voice_commands as VC
    r = VC.resolve("open gmail in browser")
    assert r.kind == "match", r
    assert r.entry is not None and r.entry.id == "browser_open", r
    assert str(r.args.get("url", "")).strip() == "gmail", r


def test_qubitchat_browser_close_that_tab():
    from voicebridge import voice_commands as VC
    r = VC.resolve("close that tab")
    assert r.kind == "match", r
    assert r.entry is not None and r.entry.id == "browser_close_tab", r


def test_qubitchat_browser_read_that_tab():
    from voicebridge import voice_commands as VC
    r = VC.resolve("read that tab")
    assert r.kind == "match", r
    assert r.entry is not None and r.entry.id == "browser_read", r


def test_qubitchat_browser_read_close_tiers_differ():
    from voicebridge import voice_commands as VC
    read = VC.resolve("read that tab")
    close = VC.resolve("close that tab")
    opened = VC.resolve("open youtube in the browser")
    assert read.entry is not None and close.entry is not None
    assert opened.entry is not None
    # Read-only entry runs unconfirmed; mutating entries are confirm-gated.
    assert read.entry.confirm is False, read.entry
    assert close.entry.confirm is True, close.entry
    assert opened.entry.confirm is True, opened.entry
    # Same shared backend family (CLI/dashboard/voice call one module).
    assert read.entry.backend == "browser_tabs", read.entry
    assert close.entry.backend == "browser_tabs", close.entry
    assert opened.entry.backend == "browser_tabs", opened.entry


def test_qubitchat_browser_bare_open_not_claimed():
    # Bare "open <name>" stays UNKNOWN in the registry: it may be an app
    # ("open vscode" is the local-action path), so the registry must not
    # hijack it — only site-qualified "open ... in the browser" claims.
    from voicebridge import voice_commands as VC
    r = VC.resolve("open youtube")
    assert r.kind == "unknown", r
