#!/usr/bin/env python3
"""Diagnose the active microphone/output USB devices and warn if any sits on a
slow (USB 1.1 full-speed, 12M) port — USB audio wedges/freezes on such ports
(observed: the HyperX Cloud III mic+headphone froze the moment audio streamed).

Uses `lsusb -t` (reliable: shows the USB speed + snd-usb-audio driver per port).

Usage:
  python scripts/diagnose_audio_usb.py
Prints each audio device + its port speed and warns if too slow. Exit 2 = slow.
"""
from __future__ import annotations

import re
import subprocess
import sys


def _lsusb_tree() -> str:
    try:
        return subprocess.run(["lsusb", "-t"], capture_output=True, text=True, timeout=8).stdout
    except Exception:
        return ""


def _parse(tree: str) -> list[dict]:
    """Return list of {bus, port, driver, speed} for snd-usb-audio devices."""
    found = []
    bus = port = driver = speed = None
    for raw in tree.splitlines():
        line = raw.strip()
        m = re.search(r"Bus (\d+)\.Port (\d+):", line)
        if m:
            bus, port = m.group(1), m.group(2)
            driver = re.search(r"Driver=([^\s,]+)", line)
            driver = driver.group(1) if driver else "?"
            speed = re.search(r"(\d+)M", line)
            speed = (speed.group(1) + "M") if speed else "?"
            continue
        if "snd-usb-audio" in line:
            found.append({"bus": bus, "port": port, "driver": "snd-usb-audio",
                          "speed": speed, "line": line[:80]})
    return found


def _speed_label(speed: str) -> str:
    if speed.startswith("5000") or "5000" in speed:
        return "USB3 (5Gbps)"
    if speed.startswith("480") or "480" in speed:
        return "USB2 (480Mbps)"
    if speed.startswith("12"):
        return "USB1.1 (12Mbps)"
    if speed.startswith("1.5"):
        return "USB1.0 (1.5Mbps)"
    return f"unknown ({speed})"


def main() -> int:
    tree = _lsusb_tree()
    if not tree:
        print("Could not read USB tree (lsusb -t).")
        return 1
    devices = _parse(tree)
    if not devices:
        print("No USB audio device (snd-usb-audio) detected — using onboard audio.")
        return 0
    print("USB audio devices & port speed:")
    slow = []
    for d in devices:
        speed = _speed_label(d["speed"])
        bad = "12" in speed or "1.5" in speed  # USB1.0/1.1 = too slow for audio streaming
        if bad:
            slow.append(d)
        print(f"  Bus {d['bus']} Port {d['port']}  {speed:16} {'⚠ SLOW' if bad else 'ok'}")
    if slow:
        print("\n⚠  WARNING: a USB audio device is on a SLOW port.")
        print("   USB 1.1 full-speed (12M) cannot stream mic+headphone audio reliably —")
        print("   it WEDGES/freezes the moment audio starts (voice capture, playback, browser).")
        print("   FIX: unplug and re-plug into a USB 3.0 (blue) or a direct rear-port slot —")
        print("   avoid front-panel/old/hub ports (they are often 12M).")
        for d in slow:
            print(f"     - Bus {d['bus']} Port {d['port']}: {d['line']}")
        return 2
    print("\n✓ All USB audio devices are on fast ports.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
