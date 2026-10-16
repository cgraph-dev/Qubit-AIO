
import sys, os
sys.path.insert(0, "/tmp/wt-deleg-502"); sys.path.insert(0, "/tmp/wt-deleg-502/qubit_runtime")
from qubit_cli.fallback_config import scoped_fallback_chain
parent = [{"provider":"omniroute","model":"duckduckgo-web/gpt-5.4-mini"},
          {"provider":"omniroute","model":"cloudflare-playground/auto"}]
# AFTER FIX: delegation.fallback_providers = [] and pinned=True
print("fixed child chain:", scoped_fallback_chain(parent, [], pinned=True, owner="delegation"))
