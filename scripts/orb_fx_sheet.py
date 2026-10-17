#!/usr/bin/env python3
"""Render the Qt Qubit-mark orb at six phases into a PNG contact sheet.

Self-contained and offline: no service, no display, no config. Drives the Qt
renderer offscreen (``QT_QPA_PLATFORM=offscreen``) into a QImage per phase and
tiles the six frames with their phase labels so a human can eyeball the motion
(ring sway, particle orbit, nucleus heartbeat, depth shading, trails, glints).
PIL is used only to assemble the grid.

Usage:
    python scripts/orb_fx_sheet.py [--out PATH] [--size PX] [--state STATE]
"""
from __future__ import annotations

import argparse
import io
import math
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Six phases spread across ~2 heartbeat periods (0.95s each) and the particle
# cycles, so consecutive cells read as continuous motion.
PHASES = (0.00, 0.32, 0.63, 0.95, 1.27, 1.59)
BG = (11, 13, 18)


def _render_phase(size: int, t: float, state: str):
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

    from voicebridge.orb import ember as E
    from voicebridge.orb import qt_app as Q

    QGuiApplication.instance() or QGuiApplication([])
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(*BG))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, True)
    cx = cy = size / 2.0
    radius = max(4.0, size / 2.0 - 6.0)
    Q._paint_mark(painter, cx, cy, radius, t, state, 1.0,
                  E.family_from_core(E.state_core(state)))
    painter.end()

    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    data = bytes(buf.data())
    buf.close()

    from PIL import Image
    return Image.open(io.BytesIO(data)).convert("RGB")


def build_sheet(out_path: Path, size: int, state: str) -> Path:
    from PIL import Image, ImageDraw

    cell_w, cell_h, label_h = size, size, 22
    cols = 3
    rows = math.ceil(len(PHASES) / cols)
    sheet = Image.new("RGB", (cell_w * cols, (cell_h + label_h) * rows), BG)
    draw = ImageDraw.Draw(sheet)

    for i, t in enumerate(PHASES):
        frame = _render_phase(size, t, state)
        r, c = divmod(i, cols)
        x, y = c * cell_w, r * (cell_h + label_h)
        sheet.paste(frame, (x, y + label_h))
        draw.text((x + 6, y + 5), f"t = {t:.2f}s   [{state}]", fill=(210, 216, 226))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return out_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Qt orb FX contact sheet")
    ap.add_argument("--out", default=str(REPO_ROOT / "orb_fx_sheet.png"))
    ap.add_argument("--size", type=int, default=200)
    ap.add_argument("--state", default="idle")
    args = ap.parse_args(argv)

    out = build_sheet(Path(args.out), max(64, int(args.size)), args.state)
    print(f"wrote {out} ({len(PHASES)} phases, {args.state})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
