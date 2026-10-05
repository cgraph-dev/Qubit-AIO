/**
 * Floating draggable voice waveform widget (canvas 2D + rAF).
 *
 * Four animations over one canvas: idle (slow low pulse), listening
 * (reactive bars — live mic level when a caller passes one cheaply via
 * `level`, otherwise stylised), thinking (chase/swirl), speaking
 * (mirrored playback wave). Unknown states never reach the canvas: the
 * `state` prop is already resolved through `resolveWaveformState`
 * (unknown → idle) — see `src/lib/voice/waveform-state.ts`.
 *
 * The widget never opens the microphone itself (no device contention with
 * the real STT capture); `level` is an optional caller-supplied tap.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  clampWaveformPosition,
  defaultWaveformPosition,
  isDragBeyondThreshold,
  loadWaveformPosition,
  saveWaveformPosition,
  VOICE_WAVEFORM_CLICK_DRAG_THRESHOLD_PX,
  type WaveformState,
} from "@/lib/voice/waveform-state";
import "./VoiceWaveform.css";

export type { WaveformState };

export interface VoiceWaveformProps {
  state: WaveformState;
  /** Optional 0..1 mic-level tap (caller-owned; widget never captures). */
  level?: () => number;
  /** Same action as the Voice button: start/stop voice input. */
  onToggle?: () => void;
  storageKey?: string;
}

const CANVAS_PX = 120;

function readThemeAccent(): string {
  if (typeof document === "undefined") return "#78d9d0";
  const root = getComputedStyle(document.documentElement);
  return root.getPropertyValue("--color-primary").trim()
    || root.getPropertyValue("--midground-base").trim()
    || "#78d9d0";
}

function voiceStatusLabel(state: WaveformState): string {
  switch (state) {
    case "listening":
      return "listening";
    case "thinking":
      return "Qubit is thinking";
    case "speaking":
      return "speaking";
    default:
      return "idle";
  }
}

/** Stylised pseudo-level (used when no live tap is supplied). */
function stylisedLevel(t: number, band: number): number {
  const v = Math.sin(t * 2.1 + band * 0.9) * 0.5 +
    Math.sin(t * 3.7 + band * 1.7) * 0.3 +
    Math.sin(t * 0.7 + band * 0.3) * 0.2;
  return Math.min(1, Math.max(0.08, 0.45 + v * 0.45));
}

export function VoiceWaveform({ state, level, onToggle, storageKey }: VoiceWaveformProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const rafRef = useRef<number>(0);
  const stateRef = useRef<WaveformState>(state);
  const levelRef = useRef(level);
  stateRef.current = state;
  levelRef.current = level;

  const [position, setPosition] = useState(() => {
    const stored = loadWaveformPosition(localStorage);
    if (stored) {
      return clampWaveformPosition(stored, {
        width: window.innerWidth,
        height: window.innerHeight,
      });
    }
    return defaultWaveformPosition({ width: window.innerWidth, height: window.innerHeight });
  });
  const [collapsed, setCollapsed] = useState(() => {
    return loadWaveformPosition(localStorage)?.collapsed === true;
  });
  const [themeAccent, setThemeAccent] = useState("");
  const posRef = useRef(position);
  posRef.current = position;

  const dragRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    originX: number;
    originY: number;
    dragging: boolean;
  } | null>(null);

  useEffect(() => {
    const root = document.documentElement;
    const update = () => setThemeAccent(readThemeAccent());
    update();
    const observer = new MutationObserver(update);
    observer.observe(root, { attributes: true, attributeFilter: ["style"] });
    return () => observer.disconnect();
  }, []);

  // Persist position + collapsed (localStorage only — no settings mirror).
  // Skips the mount write: a pure click must not leave storage behind.
  const persistedFirst = useRef(false);
  useEffect(() => {
    if (!persistedFirst.current) {
      persistedFirst.current = true;
      return;
    }
    saveWaveformPosition(localStorage, { ...posRef.current, collapsed });
    void storageKey;
  }, [position, collapsed, storageKey]);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((c) => !c);
  }, []);

  // ── Drag vs click (movement threshold; click fires onToggle) ─────────────
  const onPointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const root = rootRef.current;
      if (!root) return;
      // Buttons handle their own press — never start a drag from them.
      if ((event.target as HTMLElement).closest("button")) return;
      dragRef.current = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        originX: posRef.current.x,
        originY: posRef.current.y,
        dragging: false,
      };
      try {
        root.setPointerCapture?.(event.pointerId);
      } catch {
        /* jsdom / no capture — move/up listeners on window cover it */
      }
    },
    [],
  );

  useEffect(() => {
    const onMove = (event: PointerEvent | MouseEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const clientX = (event as PointerEvent).clientX ?? 0;
      const clientY = (event as PointerEvent).clientY ?? 0;
      if (
        !drag.dragging &&
        !isDragBeyondThreshold(
          clientX - drag.startX,
          clientY - drag.startY,
          VOICE_WAVEFORM_CLICK_DRAG_THRESHOLD_PX,
        )
      ) {
        return;
      }
      drag.dragging = true;
      const next = clampWaveformPosition(
        {
          x: drag.originX + (clientX - drag.startX),
          y: drag.originY + (clientY - drag.startY),
        },
        { width: window.innerWidth, height: window.innerHeight },
      );
      setPosition(next);
    };
    const onUp = () => {
      const drag = dragRef.current;
      dragRef.current = null;
      if (drag && !drag.dragging) onToggle?.();
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, [onToggle]);

  // ── Canvas animation loop ────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return; // no canvas backend (e.g. jsdom): static DOM dot stands in
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = CANVAS_PX * dpr;
    canvas.height = CANVAS_PX * dpr;

    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
    const accent = themeAccent || readThemeAccent();
    const t0 = performance.now();

    const draw = (now: number) => {
      const t = (now - t0) / 1000;
      const s = stateRef.current;
      const w = canvas.width;
      const h = canvas.height;
      const cx = w / 2;
      const cy = h / 2;
      ctx.clearRect(0, 0, w, h);

      if (reduced || s === "idle") {
        // Idle: slow low pulse. Reduced-motion: a single static dot frame.
        const pulse = reduced ? 0.35 : 0.3 + 0.12 * Math.sin(t * 1.4);
        const r = (w * 0.09) * (reduced ? 1 : 1 + 0.15 * Math.sin(t * 1.4));
        ctx.beginPath();
        ctx.arc(cx, cy, Math.max(1, r), 0, Math.PI * 2);
        ctx.fillStyle = accent;
        ctx.globalAlpha = reduced ? 1 : pulse + 0.35;
        ctx.fill();
        ctx.globalAlpha = 1;
        if (!reduced) {
          ctx.beginPath();
          ctx.arc(cx, cy, w * 0.2 + pulse * w * 0.08, 0, Math.PI * 2);
          ctx.strokeStyle = accent;
          ctx.globalAlpha = pulse * 0.5;
          ctx.lineWidth = Math.max(1, dpr);
          ctx.stroke();
          ctx.globalAlpha = 1;
        }
      } else if (s === "listening") {
        // Listening: reactive bars across the field.
        const bars = 25;
        const tap = levelRef.current;
        for (let i = 0; i < bars; i++) {
          const raw = tap ? Math.min(1, Math.max(0, tap())) : stylisedLevel(t, i);
          const bh = Math.max(2 * dpr, raw * h * 0.42);
          const bx = (i / bars) * w;
          const grad = (cy - bh) / h;
          ctx.fillStyle = accent;
          ctx.globalAlpha = 0.45 + grad * 0.5;
          ctx.fillRect(bx + dpr, cy - bh, w / bars - 2 * dpr, bh * 2);
          ctx.globalAlpha = 1;
        }
      } else if (s === "thinking") {
        // Thinking: chase/swirl — three orbiters + a faint ring.
        ctx.beginPath();
        ctx.arc(cx, cy, w * 0.3, 0, Math.PI * 2);
        ctx.strokeStyle = accent;
        ctx.globalAlpha = 0.25;
        ctx.lineWidth = Math.max(1, dpr);
        ctx.stroke();
        ctx.globalAlpha = 1;
        for (let k = 0; k < 3; k++) {
          const a = t * 2.4 + (k * Math.PI * 2) / 3;
          const ox = cx + Math.cos(a) * w * 0.3;
          const oy = cy + Math.sin(a) * w * 0.3;
          ctx.beginPath();
          ctx.arc(ox, oy, 3 * dpr - k * dpr * 0.5, 0, Math.PI * 2);
          ctx.fillStyle = accent;
          ctx.globalAlpha = 0.9 - k * 0.25;
          ctx.fill();
          ctx.globalAlpha = 1;
        }
      } else {
        // Speaking: mirrored playback wave expanding from the centre.
        const bands = 32;
        for (let i = 0; i < bands; i++) {
          const p = i / bands; // 0..1 across
          const env = Math.sin(p * Math.PI); // centre-loud taper
          const wave = Math.sin(p * Math.PI * 6 - t * 5) * 0.5 + Math.sin(p * Math.PI * 3 + t * 3) * 0.5;
          const amp = (0.25 + 0.35 * Math.abs(wave)) * env;
          const bx = p * w;
          ctx.fillStyle = accent;
          ctx.globalAlpha = 0.35 + env * 0.55;
          ctx.fillRect(bx, cy - amp * h * 0.45, Math.max(1, w / bands - 1), amp * h * 0.9);
          ctx.globalAlpha = 1;
        }
      }
    };

    if (reduced) {
      draw(t0); // one static frame, no loop
      return;
    }

    let running = true;
    const frame = (now: number) => {
      if (!running) return;
      if (!document.hidden) draw(now);
      rafRef.current = window.requestAnimationFrame(frame);
    };
    rafRef.current = window.requestAnimationFrame(frame);
    const onVisibility = () => {
      // Paused implicitly while hidden (frame skips draw); nothing to restart.
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      running = false;
      window.cancelAnimationFrame(rafRef.current);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [collapsed, state, themeAccent]);

  return (
    <div
      ref={rootRef}
      data-testid="voice-waveform"
      data-state={state}
      data-collapsed={collapsed ? "true" : "false"}
      className="vw-root"
      style={{ left: position.x, top: position.y }}
      onPointerDown={onPointerDown}
      role="group"
      aria-label={`Voice waveform — ${voiceStatusLabel(state)}`}
      title={`${voiceStatusLabel(state)} — drag to move, click for voice input`}
    >
      {!collapsed && (
        <canvas
          ref={canvasRef}
          className="vw-canvas"
          width={CANVAS_PX}
          height={CANVAS_PX}
          aria-hidden="true"
        />
      )}
      {collapsed && <span className="vw-dot" aria-hidden="true" />}
      <span className="vw-label" aria-hidden="true">
        {voiceStatusLabel(state)}
      </span>
      <button
        type="button"
        data-testid="voice-waveform-collapse"
        className="vw-collapse"
        onClick={toggleCollapsed}
        aria-label={collapsed ? "Expand voice waveform" : "Collapse voice waveform to a dot"}
        title={collapsed ? "Expand" : "Collapse to a dot"}
      >
        {collapsed ? "◉" : "–"}
      </button>
    </div>
  );
}
