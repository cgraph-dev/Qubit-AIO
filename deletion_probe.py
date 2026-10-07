#!/usr/bin/env python3
"""Deletion-test probe: exercise Qubit with NO hermes anywhere.

Runs inside an isolated sandbox (empty HOME, no ~/.hermes, no ~/.hermes/hermes-agent).
Prints PASS/FAIL per check with raw evidence. Exit 0 iff all checks pass.

Invoked by deletion_test.sh with env -i. Reads config from env:
  DT_SANDBOX  path to sandbox root (contains qubit_runtime/, voicebridge/, ...)
"""
from __future__ import annotations

import importlib
import io
import os
import subprocess
import sys
import traceback

SANDBOX = os.environ["DT_SANDBOX"]
QR = os.path.join(SANDBOX, "qubit_runtime")
HOME = os.environ.get("HOME", "")

# --- hard isolation -------------------------------------------------------
# The sandbox shares the repo's .venv, whose editable-install .pth injects the
# REAL checkout onto sys.path. Left in place, every "import probe" below would
# grade the live tree and the deletion test would be a lie. Drop any sys.path
# entry that resolves outside the sandbox, then pin the sandbox first.
_BAD = ("/home/trick/Projects/Repos", os.path.expanduser("~/Projects/Repos"))
sys.path[:] = [p for p in sys.path if not any(b in (p or "") for b in _BAD)]
for _p in (QR, SANDBOX):
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

failures: list[str] = []
passes = 0


def check(name: str, ok: bool, evidence: str) -> None:
    global passes
    tag = "PASS" if ok else "FAIL"
    if ok:
        passes += 1
    else:
        failures.append(name)
    print(f"[{tag}] {name}")
    for line in str(evidence).splitlines()[:8]:
        print(f"        | {line}")


# ---------------------------------------------------------------- environment
def check_environment() -> None:
    herm = os.path.join(HOME, ".hermes")
    check("env: no ~/.hermes in HOME", not os.path.exists(herm),
          f"HOME={HOME}  exists({herm})={os.path.exists(herm)}")

    # The sandbox venv is an editable install whose .pth points at the REAL
    # checkout (/home/trick/Projects/Repos/qubit-voicebridge) *and* contains a
    # bare `import _virtualenv` glue line. Both must not resolve product modules,
    # or the "deletion test" silently grades the live tree instead of the sandbox.
    leaked = [p for p in sys.path
              if "/home/trick/Projects/Repos" in p
              or p.rstrip("/") == os.path.expanduser("~/Projects/Repos/qubit-voicebridge")]
    check("env: real checkout NOT on sys.path", not leaked,
          "leaked: " + ", ".join(leaked) if leaked
          else "\n".join(p for p in sys.path if "qb-n5" in p) or "(sandbox paths)")


# ------------------------------------------------------- module import sweep
def enumerate_modules() -> list[str]:
    mods: list[str] = []
    for f in sorted(os.listdir(QR)):
        if f.endswith(".py") and not f.startswith("_") and f != "setup.py":
            mods.append(f[:-3])
    for d in sorted(os.listdir(QR)):
        dp = os.path.join(QR, d)
        if os.path.isdir(dp) and os.path.exists(os.path.join(dp, "__init__.py")):
            if d == "tests":
                continue
            mods.append(d)
    return mods


def check_imports() -> None:
    mods = enumerate_modules()
    ok_cnt = 0
    errs: list[tuple[str, str]] = []
    for m in mods:
        try:
            mod = importlib.import_module(m)
            # prove it came from the sandbox, not the live checkout
            f = getattr(mod, "__file__", None) or ""
            if f and SANDBOX not in f:
                errs.append((m, f"RESOLVED OUTSIDE SANDBOX: {f}"))
                continue
            ok_cnt += 1
        except BaseException as e:  # noqa: BLE001
            errs.append((m, f"{type(e).__name__}: {e}"))
    total = len(mods)
    ev = "\n".join(f"{m}: {e}" for m, e in errs) if errs else "all clean"
    check(f"imports: {ok_cnt}/{total} first-level modules import clean",
          not errs, ev or "all clean")
    if errs:
        print("\n--- IMPORT FAILURES (module: error) ---")
        for m, e in errs:
            print(f"  {m}: {e}")
        print()


# ------------------------------------------------------------------ CLI checks
def run_cli(args: list[str]) -> subprocess.CompletedProcess:
    env = {
        "HOME": HOME,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": SANDBOX + ":" + QR,
        "QUBIT_HOME": os.environ.get("QUBIT_HOME", ""),
        "LANG": "C.UTF-8",
    }
    return subprocess.run(
        [sys.executable, "-m", "qubit_cli.main", *args],
        env=env, capture_output=True, text=True, timeout=180, cwd=SANDBOX,
    )


def check_cli_help() -> None:
    r = run_cli(["--help"])
    ev = (r.stdout or r.stderr or "").strip().splitlines()
    check("cli: `python -m qubit_cli.main --help` exits 0", r.returncode == 0,
          f"rc={r.returncode}\n" + "\n".join(ev[:6]))
    if r.returncode != 0:
        low = (r.stdout + r.stderr).lower()
        if "close-preview" in low:
            print("        >> HINT: --close-preview argparse conflict (known killer)")


def check_cli_version() -> None:
    r = run_cli(["--version"])
    out = (r.stdout or r.stderr or "").strip()
    check("cli: `python -m qubit_cli.main --version` exits 0", r.returncode == 0,
          f"rc={r.returncode} out={out[:200]!r}")


def check_subcommand_count() -> None:
    """Build the FULL CLI parser the way main.py does and count subcommands."""
    try:
        import argparse
        parser = None
        # qubit_cli/main.py::_build_cli_parser() is the canonical full-tree builder.
        for modname in ("qubit_cli.main", "qubit_cli._parser"):
            try:
                m = importlib.import_module(modname)
            except BaseException:  # noqa: BLE001
                continue
            fn = getattr(m, "_build_cli_parser", None) or \
                 getattr(m, "build_top_level_parser", None)
            if callable(fn):
                try:
                    res = fn()
                except BaseException as e:  # noqa: BLE001
                    check("cli: subcommands > 70", False,
                          f"{modname}.{fn.__name__}() raised {type(e).__name__}: {e}")
                    return
                parser = res[0] if isinstance(res, tuple) else res
                break
        if parser is None or not hasattr(parser, "_actions"):
            check("cli: subcommands > 70", False,
                  f"could not build a full parser (got {type(parser).__name__})")
            return
        subs = [a for a in parser._actions
                if isinstance(a, argparse._SubParsersAction)]
        names = list(subs[0].choices.keys()) if subs else []
        check(f"cli: subcommands > 70 (found {len(names)})", len(names) > 70,
              ", ".join(names[:25]) + (" ..." if len(names) > 25 else ""))
    except BaseException as e:  # noqa: BLE001
        check("cli: subcommands > 70", False,
              f"{type(e).__name__}: {e}\n{traceback.format_exc()[-600:]}")


def check_cli_object() -> None:
    try:
        import cli as clim  # top-level cli.py holds QubitCLI
        cls = getattr(clim, "QubitCLI", None) or getattr(clim, "HermesCLI", None)
        if cls is None:
            check("cli: construct cli.QubitCLI()", False,
                  "neither QubitCLI nor HermesCLI found in top-level cli.py")
            return
        obj = cls()
        check("cli: construct cli.QubitCLI()", obj is not None,
              f"{cls.__name__}() -> {type(obj).__name__}")
    except BaseException as e:  # noqa: BLE001
        check("cli: construct cli.QubitCLI()", False,
              f"{type(e).__name__}: {e}\n{traceback.format_exc()[-600:]}")


def check_gateway_imports() -> None:
    targets = [
        "tui_gateway.server",
        "tui_gateway.entry",
        "qubit_cli.gateway",
        "plugins.plugin_loader",
    ]
    for t in targets:
        try:
            importlib.import_module(t)
            check(f"import: {t}", True, "ok")
        except BaseException as e:  # noqa: BLE001
            check(f"import: {t}", False, f"{type(e).__name__}: {e}")


def check_providers() -> None:
    """list_authenticated_providers(for_picker=True, fast_custom_probe=True)."""
    fn = None
    where = ""
    for modname in ("qubit_cli.model_switch_providers",
                    "qubit_cli.providers",
                    "providers.resolver"):
        try:
            m = importlib.import_module(modname)
        except BaseException:  # noqa: BLE001
            continue
        f = getattr(m, "list_authenticated_providers", None)
        if callable(f):
            fn, where = f, modname
            break
    if fn is None:
        # search submodules
        import pkgutil
        for modname in ("qubit_cli", "providers"):
            try:
                pkg = importlib.import_module(modname)
            except BaseException:  # noqa: BLE001
                continue
            for info in pkgutil.iter_modules(pkg.__path__):
                try:
                    m = importlib.import_module(f"{modname}.{info.name}")
                except BaseException:  # noqa: BLE001
                    continue
                f = getattr(m, "list_authenticated_providers", None)
                if callable(f):
                    fn, where = f, f"{modname}.{info.name}"
                    break
            if fn:
                break
    if fn is None:
        check("providers: list_authenticated_providers(...) returns providers",
              False, "function not found during sweep")
        return
    try:
        res = fn(for_picker=True, fast_custom_probe=True)
        n = len(res) if hasattr(res, "__len__") else "?"
        check("providers: list_authenticated_providers(...) returns providers",
              bool(res), f"{where} -> {n} provider(s)")
    except BaseException as e:  # noqa: BLE001
        check("providers: list_authenticated_providers(...) returns providers",
              False, f"{where}\n{type(e).__name__}: {e}\n{traceback.format_exc()[-600:]}")


def check_path_stripped() -> None:
    """REMOVAL, not just non-dependency: re-scan with the hermes binary off PATH.

    The rest of the probe runs with the operator's full PATH, on which a `hermes`
    binary still exists at ~/.local/bin/hermes. That proves Qubit does not *call*
    hermes; it does not prove Qubit boots when hermes is not installable at all.
    Here we strip every PATH entry that contains an executable named `hermes` and
    re-resolve the home dir + re-scan for subprocess invocations of it.
    """
    raw_path = os.environ.get("PATH", "")
    parts = [p for p in raw_path.split(os.pathsep) if p]

    def has_hermes(d: str) -> bool:
        exe = os.path.join(d, "hermes")
        return os.path.isfile(exe) and os.access(exe, os.X_OK)

    stripped_dirs = sorted({p for p in parts if has_hermes(p)})
    clean_path = os.pathsep.join(p for p in parts if not has_hermes(p))
    import shutil
    found_after = shutil.which("hermes", path=clean_path)
    check("path: no `hermes` binary resolves on the stripped PATH",
          found_after is None,
          f"stripped {len(stripped_dirs)} dir(s): {', '.join(stripped_dirs) or '(none)'}\n"
          f"which(hermes) after strip = {found_after}")

    # Re-resolve the core home under the stripped PATH, with QUBIT_HOME UNSET so the
    # fallback chain runs: it must reach Qubit's own dir, never a hermes one.
    code = (
        "import qubit_constants as c; print(str(c.get_qubit_home()))"
    )
    env = {"HOME": HOME, "PATH": clean_path, "LANG": "C.UTF-8",
           "PYTHONPATH": SANDBOX + os.pathsep + QR}
    try:
        r = subprocess.run([sys.executable, "-c", code], env=env,
                           capture_output=True, text=True, timeout=60, cwd=SANDBOX)
        home = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else f"<rc={r.returncode}>"
    except BaseException as e:  # noqa: BLE001
        home = f"{type(e).__name__}: {e}"
    check("path: core home still resolves to Qubit's own with hermes off PATH",
          home.endswith("/.qubit"),
          f"QUBIT_HOME unset; get_qubit_home() = {home}")

    # Did Qubit WRITE anything into a hermes install location? (It must not.)
    hermes_home = os.path.join(HOME, ".hermes")
    check("path: no ~/.hermes created with hermes off PATH",
          not os.path.exists(hermes_home),
          f"exists({hermes_home}) = {os.path.exists(hermes_home)}")


def _local_ollama_models() -> list[str]:
    """Models served by a local ollama on :11434 (empty when none/unreachable)."""
    import json as _json
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=4) as fh:
            data = _json.loads(fh.read().decode())
        return [m.get("name", "") for m in data.get("models", [])]
    except BaseException:  # noqa: BLE001
        return []


def check_real_turn() -> None:
    """Exercise an actual agent TURN, not just imports.

    Drives ``QubitCLI()._chat_run_agent(turn, message)`` with the REAL turn type
    (``cli._ChatTurn`` — passing a dict raises AttributeError on ``voice_prefix``,
    the mistake a previous attempt made) against a local ollama model. A turn is a
    model call plus response handling, so a non-empty final response with
    api_calls>=1 is proof the conversation loop runs end-to-end with hermes absent.

    Skips (still PASS with an explicit reason) when ollama or a model is absent —
    a missing local model is an environment fact, not a Qubit defect.
    """
    models = _local_ollama_models()
    if not models:
        check("turn: real _chat_run_agent turn against local ollama", True,
              "SKIP: no local ollama on :11434 (no model to drive a real turn)")
        return
    # Prefer a Qubit-facing local model with a real window; qubit-brain is known-good,
    # qubit-spark is a small 1.7B build that can still trip its own context. Fall back
    # to whatever is offered so the check works on a stock box with any local model.
    _pref = ("qubit-brain", "qubit-d5", "qubit-cand", "qwen3-vl")
    pick = next((m for m in models if m.startswith(_pref)), None) or \
           next((m for m in models if m.startswith("qubit-")), models[0])

    code = r'''
import os, sys, json
from cli import QubitCLI, _ChatTurn
model = sys.argv[1]
cli = QubitCLI(model=model, provider="ollama", base_url="http://localhost:11434/v1")
if not cli._init_agent():
    print(json.dumps({"ok": False, "why": "_init_agent returned False"})); raise SystemExit(0)
agent = cli.agent
# Tool schemas make the request larger than a small local context; a conversational
# turn needs none of them. Strip tools and present the model's true window so the
# turn exercises the loop rather than a context-size refusal.
n_tools = len(getattr(agent, "tools", None) or [])
agent.tools = []
try:
    agent._ollama_num_ctx = int(getattr(agent, "_ollama_num_ctx", 0) or 0)
    if 0 < agent._ollama_num_ctx < 64000:
        agent._ollama_num_ctx = 131072
except Exception:
    pass
turn = _ChatTurn()
msg = "Reply with exactly one word: pong"
cli.conversation_history.append({"role": "user", "content": msg})
cli._chat_run_agent(turn, msg)
r = turn.result or {}
print(json.dumps({
    "ok": True, "model": model, "tools_stripped": n_tools,
    "final_response": str(r.get("final_response", ""))[:200],
    "api_calls": r.get("api_calls"), "completed": r.get("completed"), "failed": r.get("failed"),
}))
'''
    env = {
        "HOME": HOME,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": SANDBOX + os.pathsep + QR,
        "QUBIT_HOME": os.environ.get("QUBIT_HOME", os.path.join(SANDBOX, "qhome")),
        "LANG": "C.UTF-8",
    }
    payload = None
    err = ""
    try:
        r = subprocess.run([sys.executable, "-c", code, pick], env=env,
                           capture_output=True, text=True, timeout=240, cwd=SANDBOX)
        for line in reversed((r.stdout or "").strip().splitlines()):
            if line.startswith("{") and line.endswith("}"):
                import json as _json
                payload = _json.loads(line)
                break
        if payload is None:
            err = (r.stderr or r.stdout or "").strip()[-400:]
    except BaseException as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"

    if payload is None:
        check("turn: real _chat_run_agent turn against local ollama", False,
              f"model={pick} no JSON result\n{err}")
        return
    if not payload.get("ok"):
        check("turn: real _chat_run_agent turn against local ollama", False,
              f"model={pick} {payload.get('why')}")
        return
    fr = payload.get("final_response", "")
    ok = bool(fr) and not payload.get("failed") and (payload.get("api_calls") or 0) >= 1
    check("turn: real _chat_run_agent turn against local ollama", ok,
          f"model={pick} api_calls={payload.get('api_calls')} "
          f"completed={payload.get('completed')} failed={payload.get('failed')}\n"
          f"final_response={fr!r}")


def main() -> int:
    print("=" * 72)
    print("DELETION TEST — Qubit with hermes fully absent")
    print(f"HOME      = {HOME}")
    print(f"SANDBOX   = {SANDBOX}")
    print(f"QUBIT_HOME= {os.environ.get('QUBIT_HOME','')}")
    print("=" * 72)
    check_environment()
    print()
    check_imports()
    print()
    check_cli_help()
    check_cli_version()
    check_subcommand_count()
    check_cli_object()
    print()
    check_gateway_imports()
    print()
    check_providers()
    print()
    check_path_stripped()
    print()
    check_real_turn()
    print()
    print("=" * 72)
    print(f"RESULT: {passes} passed, {len(failures)} failed")
    if failures:
        print("FAILED CHECKS:")
        for f in failures:
            print(f"  - {f}")
    print("=" * 72)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
