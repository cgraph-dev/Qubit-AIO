"""Behavioural probe: the 14 dead-symbol wirings on their REAL entry paths.

Run from qubit_runtime/ with THIS worktree PREPENDED -- the venv's editable .pth maps the package
roots to the MAIN checkout, so a plain ``PYTHONPATH=.`` still imports the main tree and would probe
the wrong bytes (a stale ``accept_result`` silently reported the ledger missing). ``PYTHONPATH=$PWD``
with ``cd qubit_runtime`` and ``sys.path.insert(0, ...)`` in-process is what makes this the worktree:

    cd qubit_runtime
    PYTHONPATH=. ../.venv/bin/python -W ignore scripts/_probe_laneA_wiring_runtime.py

The unit tests for each symbol pass; that is necessary, not sufficient. This repo has shipped
a symbol whose test passed while the live path threw (the split-module ``bind_module`` rebind
made ``_profile_configured_cwd`` a NameError its own tests did not catch). Each arm below drives
the production entry point and prints literal output, so a regression shows up here even when the
function-level test stays green:

  1. ``_endpoint_owner``        -- SECURITY: a saved session whose provider entry MOVED must not
                                   resume on the old host carrying the moved entry's credential.
  2. ``_persisted_turn`` /
     ``_LEDGER_PASSTHROUGH_KEYS`` -- the receipt and the ledger keys must reach what
                                   ``prompt.receipt`` returns, not only the turn-result dict.
  3. ``_needs_extra``           -- extra-credential configs report connected; partial ones do not.
  4. ``_HOME_TARGET_ENV_VARS``  -- every built-in home target resolves a non-empty env var.
  5. ``_watch_notification_metadata`` -- the live send path keeps automation identities, the local
                                   async-result card, the proven session and the completion ids.
  6. tui_gateway wirings        -- through a real ``server`` import (a module-only import is the
                                   false-NameError trap), and ``entry.main()`` claiming its home.
"""
import os
import sys
import tempfile

# The venv's editable .pth puts the MAIN checkout on sys.path; force THIS worktree first so the
# probe reads the bytes under test, not the main tree (this bit the probe once -- a stale
# ``accept_result`` from the main checkout silently reported the ledger missing). Drop the main
# checkout, then prepend this worktree's qubit_runtime.
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MAIN = "/home/trick/Projects/Repos/qubit-voicebridge"
_STALE = {_MAIN, os.path.join(_MAIN, "qubit_runtime")}


def _abs(path: "str | None") -> str:
    """``abspath`` for values typed ``str | None``.

    Built-in and namespace modules carry no ``__file__`` (or a ``None`` one), so a raw
    ``os.path.abspath(m.__file__)`` raises ``AttributeError``/``TypeError`` on exactly those.
    '' is a real path here but, like the main checkout in ``_STALE``, a prefix of nothing probed."""
    return os.path.abspath(path or "")


sys.path[:] = [p for p in sys.path if _abs(p) not in _STALE]
sys.path.insert(0, _HERE)

_HOME = tempfile.mkdtemp(prefix="laneA-wiring-")
os.environ["QUBIT_HOME"] = _HOME
os.environ["HERMES_HOME"] = _HOME

from tui_gateway import entry, methods_free_tier, server, session_workdir  # noqa: E402

# Importing the packages re-inserts the MAIN checkout's qubit_runtime (the venv is editable-installed
# against the main tree). Re-assert this worktree first, and drop `agent`/`gateway` if they were
# already bound to the other tree, so every probe below reads the bytes under test.
sys.path[:] = [p for p in sys.path if _abs(p) not in _STALE]
sys.path.insert(0, _HERE)
for _name in [n for n in sys.modules if n == "agent" or n.startswith(("agent.", "gateway", "tui_gateway"))
              and _abs(getattr(sys.modules[n], "__file__", ""))
              and not _abs(getattr(sys.modules[n], "__file__", None)).startswith(_HERE + os.sep)]:
    del sys.modules[_name]


def verify_endpoint_owner():
    """SECURITY: a saved session must not resume on a MOVED endpoint with the moved entry's key."""
    from gateway.session_local_migration import _endpoint_owner

    saved = "https://api.old-host.example/v1"

    def cfg(url):
        return {"model": {"provider": "custom:work", "base_url": url},
                "providers": {"work": {"name": "Work", "base_url": url, "api_key": "sk-work"}}}

    print("[1] _endpoint_owner  saved '%s'" % saved)
    for label, url in (("entry still serves saved URL", saved),
                       ("re-spelled same URL (trailing /)", saved + "/"),
                       ("entry MOVED to new host", "https://api.new-host.example/v1")):
        try:
            owner = _endpoint_owner({"provider": "custom:work", "base_url": saved}, cfg(url))
            print("    %-32s -> keep/rebind %s" % (label, owner))
        except Exception as exc:
            print("    %-32s -> REFUSED %s (%s)" % (label, getattr(exc, "reason", exc),
                                                    getattr(exc, "detail", "")))


def verify_receipt_and_ledger():
    """The receipt ledger must reach captured['usage'] -- what prompt.receipt returns."""
    from agent.managed_worker import accept_result
    from gateway import run_turn_runner as rtr


    result = {"final_response": "hi", "provider": "custom:work", "estimated_cost_usd": 0.004,
              "cost_status": "ok", "cost_source": "provider", "cache_read_tokens": 7,
              "cache_write_tokens": 2, "reasoning_tokens": 1, "total_tokens": 15,
              "turn_exit_reason": "complete", "service_tier": "standard"}
    captured = {"usage": {"input_tokens": 11, "output_tokens": 4, "total_tokens": 15,
                          **{k: result.get(k) for k in rtr._LEDGER_PASSTHROUGH_KEYS
                             if k != "total_tokens" and k in result}}}
    print("[2] ledger keys in captured['usage'] (what prompt.receipt returns):",
          sorted(captured["usage"]))
    print("    ledger keys MISSING there:",
          [k for k in rtr._LEDGER_PASSTHROUGH_KEYS if k not in captured["usage"]])
    # The worker frame carries the ledger keys the turn reported; project the same set.
    frame = {k: v for k, v in result.items() if k != "total_tokens"}
    frame.update(prompt_tokens=11, completion_tokens=4)
    _accepted, worker_usage = accept_result(frame)
    # Guard against the venv's editable install resolving `agent` to the MAIN checkout instead of
    # this worktree: a probe reading the wrong bytes reported a false "ledger missing" once.
    _resolved = accept_result.__code__.co_filename
    if not _abs(_resolved).startswith(_HERE + os.sep):
        raise SystemExit(f"PROBE ERROR: accept_result resolved to {_resolved}, not this worktree "
                         f"({_HERE}). Run with the worktree first on sys.path.")
    # accept_result returns ``(result, usage)``; usage is ``{}`` only when the frame carried NO
    # token counts, so the None-union is narrowed here instead of assumed away.
    if worker_usage is None:
        raise SystemExit("PROBE ERROR: accept_result returned no usage mapping")
    print("    managed-worker leg missing:",
          [k for k in rtr._LEDGER_PASSTHROUGH_KEYS if k not in worker_usage])
    print("    managed-worker usage:", worker_usage)
    receipt = rtr._persisted_turn(
        type("A", (), {"_persist_user_message_idx": 1, "context_compressor": None})(),
        [{"role": "user", "_db_persisted": True, "_row_id": 1}],
        {"messages": [{"role": "user", "_db_persisted": True, "_row_id": 1},
                      {"role": "user", "_db_persisted": True, "_row_id": 2, "content": "q"},
                      {"role": "assistant", "_db_persisted": True, "_row_id": 3, "content": "a"}],
         "final_response": "a"}, 0)
    print("    persisted_turn receipt:", receipt)


def verify_connected_platforms():
    """get_connected_platforms() must report extra-credential configs, and not partial ones."""
    from gateway.config import GatewayConfig, Platform, PlatformConfig

    def gw(extra):
        return GatewayConfig(platforms={
            Platform.WHATSAPP_CLOUD: PlatformConfig(enabled=True, extra=extra)})

    print("[3] whatsapp_cloud full extras -> connected:",
          Platform.WHATSAPP_CLOUD in gw({"phone_number_id": "1", "access_token": "t"}
                                        ).get_connected_platforms())
    for partial in ({"phone_number_id": "1"}, {"access_token": "t"}, {}):
        print("    partial %-32s -> connected:" % (partial,),
              Platform.WHATSAPP_CLOUD in gw(partial).get_connected_platforms())


def verify_home_targets():
    """Built-in platform home targets must resolve a NON-empty env var."""
    from cron import scheduler_delivery as sd

    empty = [name for name in sd._HOME_TARGET_ENV_VARS if not sd._resolve_home_env_var(name)]
    print("[4] built-in home targets:", len(sd._HOME_TARGET_ENV_VARS),
          "| resolving to an EMPTY var:", empty)
    for name in ("telegram", "discord", "whatsapp_cloud", "qqbot"):
        print("    %-15s -> %r" % (name, sd._resolve_home_env_var(name)))
    print("    built-in platforms iterated:",
          sorted(set(sd._iter_home_target_platforms()) & set(sd._HOME_TARGET_ENV_VARS)))


def verify_watch_metadata():
    """The live send path must keep the four keys the inline copy lost."""
    from gateway.config import Platform
    from gateway.run_notifications import _watch_notification_metadata
    from gateway.session import SessionSource

    evt = {"session_key": "agent:main:telegram:group:-100", "proven_session_id": "p-1",
           "completion_ids": ["d-1"], "_automation_identities": ["a-1"]}
    md = _watch_notification_metadata(evt, SessionSource(platform=Platform.TELEGRAM, chat_id="-100"))
    print("[5] watch metadata:",
          {k: md.get(k) for k in ("automation_identities", "gateway_proven_session_id",
                                  "gateway_completion_ids", "notification_origin")})
    md_local = _watch_notification_metadata(
        dict(evt, type="completion", _display_text="done"),
        SessionSource(platform=Platform.LOCAL, chat_id="l"))
    print("    LOCAL async-result card:",
          {k: md_local.get(k) for k in ("display_kind", "display_text")})


def verify_tui_wiring():
    """The tui_gateway wirings -- through a REAL `server` import (the bind_module trap)."""
    # These three live in ``session_workdir`` and reach ``server`` only through
    # ``method_ctx.bind_module`` (rebound onto server's globals at install time), so a checker can
    # never see them as ``server`` attributes -- that absence IS arm 6's subject, not a defect.
    # ``getattr`` makes the runtime lookup the checker's view, so the probe still fails loudly.
    cwd_fns = {n: getattr(server, n) for n in
               ("_profile_default_cwd", "_is_other_profiles_cwd", "_completion_cwd")}
    print("[6] module-only import carries server privates (must be False):",
          "_profile_configured_cwd" in vars(session_workdir))
    print("    server._profile_default_cwd(None)         ->", repr(cwd_fns["_profile_default_cwd"](None)))
    print("    server._is_other_profiles_cwd('/', None)  ->", cwd_fns["_is_other_profiles_cwd"]("/", None))
    print("    server._completion_cwd({})                ->", repr(cwd_fns["_completion_cwd"]({})))
    print("    free_tier._trial_claimable()              ->", methods_free_tier._trial_claimable())
    print("    entry._claim_launch_home callable         ->", callable(entry._claim_launch_home))
    print("    entry._serve callable                     ->", callable(entry._serve))


verify_endpoint_owner()
verify_receipt_and_ledger()
verify_connected_platforms()
verify_home_targets()
verify_watch_metadata()
verify_tui_wiring()
