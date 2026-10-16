
import sys, os
sys.path.insert(0, "/tmp/wt-deleg-502")
sys.path.insert(0, "/tmp/wt-deleg-502/qubit_runtime")
os.environ.setdefault("QUBIT_HOME", "/home/trick/.qubit")
from qubit_cli.config import load_config_readonly
from qubit_cli.fallback_config import get_fallback_chain, scoped_fallback_chain
cfg = load_config_readonly()
chain = get_fallback_chain(cfg)
print("parent chain (get_fallback_chain):", len(chain))
for e in chain: print("   ", e.get("provider"), e.get("model"))
# What a child gets: delegation.fallback_providers is None, pinned=False
for pinned in (False, True):
    c = scoped_fallback_chain(chain, None, pinned=pinned, owner="delegation")
    print(f"child chain pinned={pinned}:", [(e.get('provider'), e.get('model')) for e in (c or [])])
