
import sys, os
sys.path.insert(0, "/tmp/wt-deleg-502")
sys.path.insert(0, "/tmp/wt-deleg-502/qubit_runtime")
os.environ.setdefault("QUBIT_HOME", "/home/trick/.qubit")
try:
    from qubit_cli.config import load_config_readonly
    cfg_all = load_config_readonly()
    print("== top-level config keys ==", sorted(cfg_all.keys()))
    print("== delegation block ==", cfg_all.get("delegation"))
    print("== fallback_providers ==", cfg_all.get("fallback_providers"))
    print("== model block ==", cfg_all.get("model"))
    print("== providers ==", list((cfg_all.get("providers") or {}).keys()))
except Exception as e:
    print("load_config_readonly FAILED:", type(e).__name__, e)
