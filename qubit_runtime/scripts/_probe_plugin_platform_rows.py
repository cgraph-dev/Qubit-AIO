"""Probe: for each PLUGIN-owned platform, what does the CONNECTION CHECK actually do?

For SIGNAL / BLUEBUBBLES / QQBOT / YUANBAO (plugin-owned in this fork) answer, per platform:
  a) which branch of GatewayConfig._is_platform_connected runs (plugin entry vs generic token)
  b) is the registry entry even found (discovery + platform_registry.get)
  c) does partial config report connected (would be silently permissive)?
  d) VERDICT: correct / permissive / broken

Run: cd qubit_runtime && PYTHONPATH=. ../.venv/bin/python -W ignore <this>
"""
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MAIN = "/home/trick/Projects/Repos/qubit-voicebridge"
_STALE = {_MAIN, os.path.join(_MAIN, "qubit_runtime")}
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") not in _STALE]
sys.path.insert(0, _HERE)

_HOME = tempfile.mkdtemp(prefix="platprobe-")
os.environ["QUBIT_HOME"] = _HOME
os.environ["HERMES_HOME"] = _HOME
# No ambient credentials leaking in from the real environment.
for _v in ("SIGNAL_HTTP_URL", "SIGNAL_ACCOUNT", "BLUEBUBBLES_SERVER_URL", "BLUEBUBBLES_PASSWORD",
           "QQ_APP_ID", "QQ_CLIENT_SECRET", "YUANBAO_APP_ID", "YUANBAO_APP_SECRET"):
    os.environ.pop(_v, None)

from gateway.config import (  # noqa: E402
    GatewayConfig, Platform, PlatformConfig, _PLATFORM_CONNECTED_CHECKERS,
    _discovered_registry_entry,
)

PKGS = [
    (Platform.SIGNAL, "signal", {"http_url": "http://127.0.0.1:8080"}),
    (Platform.BLUEBUBBLES, "bluebubbles", {"server_url": "http://x", "password": "p"}),
    (Platform.QQBOT, "qqbot", {"app_id": "a", "client_secret": "s"}),
    (Platform.YUANBAO, "yuanbao", {"app_id": "a", "app_secret": "s"}),
]

print("platforms in _PLATFORM_CONNECTED_CHECKERS:",
      sorted(p.value for p in _PLATFORM_CONNECTED_CHECKERS))

verdicts = {}
for plat, _name, full in PKGS:
    print("\n=== %s (%r)" % (plat.name, plat.value))
    print("  in _PLATFORM_CONNECTED_CHECKERS:", plat in _PLATFORM_CONNECTED_CHECKERS)
    entry = _discovered_registry_entry(plat)
    print("  registry entry found:", entry is not None,
          "| name=%r" % (getattr(entry, "name", None),))
    fn = getattr(entry, "config_connected_fn", None) if entry is not None else None
    print("  entry.config_connected_fn:", fn)
    print("  entry.is_connected:        ", getattr(entry, "is_connected", None))

    def gw(extra):
        return GatewayConfig(platforms={plat: PlatformConfig(enabled=True, extra=extra)})

    full_ok = plat in gw(full).get_connected_platforms()
    print("  FULL extra %-52r -> connected: %s" % (full, full_ok))

    partial_results = {}
    # every proper prefix / single-key subset must read NOT connected
    for i in range(len(full)):
        for j in range(i + 1, len(full) + 1):
            sub = {k: full[k] for k in list(full)[i:j]}
            if sub == full:
                continue
            partial_results[tuple(sorted(sub))] = plat in gw(sub).get_connected_platforms()
    empty_ok = plat in gw({}).get_connected_platforms()
    print("  EMPTY extra -> connected: %s" % empty_ok)
    for keys, ok in sorted(partial_results.items()):
        print("  PARTIAL %-45s -> connected: %s" % (list(keys), ok))

    via_plugin = entry is not None and fn is not None

    # Decoy: a WRONG extra key must not satisfy the check (proves it is key-specific, not merely
    # "any extra present"). Fully-loaded config with the real keys REMOVED one at a time.
    for real in full:
        decoy = {k: v for k, v in full.items() if k != real}
        decoy["decoy_" + real] = "x"
        ok = plat in gw(decoy).get_connected_platforms()
        print("  DECOY  %-45s -> connected: %s" % ("decoy_%s instead of %s" % (real, real), ok))
        if ok:
            partial_results[("decoy", real)] = True
    if not full_ok:
        verdicts[plat.name] = "BROKEN (full config not reported connected)"
    elif empty_ok or any(partial_results.values()):
        bad = [list(k) for k, v in partial_results.items() if v]
        verdicts[plat.name] = "PERMISSIVE (partial/empty reports connected: %s)" % bad
    elif via_plugin:
        verdicts[plat.name] = "CORRECT (plugin entry.config_connected_fn, strict on all keys)"
    else:
        verdicts[plat.name] = "CORRECT-BY-GENERIC (no plugin entry; token/api_key generic branch)"

print("\n===== VERDICTS")
for k, v in verdicts.items():
    print("  %-12s %s" % (k, v))
