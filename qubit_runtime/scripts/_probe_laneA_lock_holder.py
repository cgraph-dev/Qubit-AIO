"""Disposable-owner child for the launch-home claim probe.

Stands in for the live gateway: takes the home's ``gateway.lock`` exactly as the gateway does
(``process_ownership.reserve``), says READY, and holds it until stdin closes.
"""
import sys

from pathlib import Path

from gateway.runtime_ownership import process_ownership

home = Path(sys.argv[1])
process_ownership.reserve([home])
print("READY", flush=True)
sys.stdin.read()
process_ownership.release(home)
