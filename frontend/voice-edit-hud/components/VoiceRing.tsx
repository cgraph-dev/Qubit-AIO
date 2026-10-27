'use client';

import { useEffect, useRef } from 'react';
import { levelPercent, stateLabel } from '@/lib/format';
import type { HudState } from '@/lib/types';

interface VoiceRingProps {
  state: HudState;
  level: number;
  reduced: boolean;
}

const STATE_COLOR: Record<string, string> = {
  idle: '#6f7fa3',
  listening: '#00e6ff',
  processing: '#ffc24b',
  preview: '#ff2bd6',
  done: '#39ffa6',
  error: '#ff4d6d',
  clarify: '#9b5cff',
  interrupted: '#ffc24b',
  disabled: '#606a88',
};

function rgba(hex: string, alpha: number): string {
  const value = hex.replace('#', '');
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

const TAU = Math.PI * 2;

function drawFrame(
  ctx: CanvasRenderingContext2D,
  size: number,
  time: number,
  targetLevel: number,
  state: HudState,
  smooth: number,
): void {
  const color = STATE_COLOR[state] ?? STATE_COLOR.idle;
  const cx = size / 2;
  const cy = size / 2;
  const radius = size / 2 - 10;
  const active = state === 'listening' || state === 'processing';
  const level = Math.min(1, Math.max(smooth, active ? 0.04 : 0));

  ctx.clearRect(0, 0, size, size);

  // Base rings.
  ctx.lineWidth = 1;
  ctx.strokeStyle = 'rgba(155, 92, 255, 0.16)';
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, TAU);
  ctx.stroke();

  ctx.strokeStyle = 'rgba(155, 92, 255, 0.10)';
  ctx.beginPath();
  ctx.arc(cx, cy, radius * 0.66, 0, TAU);
  ctx.stroke();

  // Outer gauge ticks: lit up to the live level.
  const ticks = 72;
  for (let i = 0; i < ticks; i += 1) {
    const unit = i / ticks;
    const angle = unit * TAU - Math.PI / 2;
    const lit = unit <= level;
    const isMajor = i % 6 === 0;
    const inner = radius * (isMajor ? 0.94 : 0.965);
    const outer = radius * 0.995;
    ctx.strokeStyle = lit
      ? rgba(color, isMajor ? 0.95 : 0.72)
      : rgba('#6b5a8f', isMajor ? 0.3 : 0.16);
    ctx.lineWidth = isMajor ? 1.6 : 1;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(angle) * inner, cy + Math.sin(angle) * inner);
    ctx.lineTo(cx + Math.cos(angle) * outer, cy + Math.sin(angle) * outer);
    ctx.stroke();
  }

  // Live waveform rings.
  const drawWave = (
    phase: number,
    base: number,
    amplitude: number,
    alpha: number,
    width: number,
  ) => {
    ctx.beginPath();
    const points = 128;
    for (let i = 0; i <= points; i += 1) {
      const angle = (i / points) * TAU - Math.PI / 2;
      const wave =
        Math.sin(i * 0.31 + time * 2.1 + phase) * 0.55 +
        Math.sin(i * 0.11 - time * 1.35 + phase * 0.7) * 0.3 +
        Math.sin(i * 0.67 + time * 0.72 + phase * 1.4) * 0.15;
      const r = radius * base + radius * amplitude * wave;
      const x = cx + Math.cos(angle) * r;
      const y = cy + Math.sin(angle) * r;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.strokeStyle = rgba(color, alpha);
    ctx.lineWidth = width;
    ctx.stroke();
  };

  const liveAmp = active
    ? 0.06 + level * 0.18
    : state === 'preview'
      ? 0.045
      : 0.022;
  drawWave(0, 0.79, liveAmp, 0.92, 1.5);
  drawWave(2.1, 0.71, liveAmp * 0.65, 0.38, 1);

  // Level arc (bright core + soft underlay).
  if (level > 0.001) {
    const end = -Math.PI / 2 + TAU * level;
    ctx.lineCap = 'round';
    ctx.lineWidth = 6;
    ctx.strokeStyle = rgba(color, 0.16);
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 0.9, -Math.PI / 2, end);
    ctx.stroke();
    ctx.lineWidth = 2;
    ctx.strokeStyle = rgba(color, 0.95);
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 0.9, -Math.PI / 2, end);
    ctx.stroke();
    ctx.lineCap = 'butt';
  }

  // Processing rotor.
  if (state === 'processing') {
    const spin = time * 2.4;
    ctx.lineWidth = 2;
    ctx.strokeStyle = rgba('#ffc24b', 0.85);
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 0.84, spin, spin + 0.6);
    ctx.stroke();
    ctx.strokeStyle = rgba('#ff2bd6', 0.6);
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 0.84, spin + Math.PI, spin + Math.PI + 0.35);
    ctx.stroke();
  }

  // Cardinal registration marks.
  ctx.strokeStyle = rgba(color, 0.4);
  ctx.lineWidth = 1;
  for (let i = 0; i < 4; i += 1) {
    const angle = (i / 4) * TAU;
    const r1 = radius * 1.012;
    const r2 = radius * 1.045;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(angle) * r1, cy + Math.sin(angle) * r1);
    ctx.lineTo(cx + Math.cos(angle) * r2, cy + Math.sin(angle) * r2);
    ctx.stroke();
  }
}

export function VoiceRing({ state, level, reduced }: VoiceRingProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const levelRef = useRef(level);
  const stateRef = useRef(state);
  const sizeRef = useRef(240);
  const smoothRef = useRef(0);

  levelRef.current = level;
  stateRef.current = state;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = Math.min(2, window.devicePixelRatio || 1);
    let raf = 0;
    let disposed = false;

    const paint = (time: number) => {
      drawFrame(ctx, sizeRef.current, time, levelRef.current, stateRef.current, smoothRef.current);
    };

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const css = Math.max(
        140,
        Math.round(Math.min(rect.width || 240, rect.height || 240)),
      );
      sizeRef.current = css;
      canvas.width = Math.max(1, Math.round(css * dpr));
      canvas.height = Math.max(1, Math.round(css * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      paint(reduced ? 0 : performance.now() / 1000);
    };

    resize();
    const observer =
      typeof ResizeObserver !== 'undefined'
        ? new ResizeObserver(resize)
        : null;
    observer?.observe(canvas);

    if (!reduced) {
      const start = performance.now();
      let last = 0;
      const loop = (now: number) => {
        if (disposed) return;
        raf = requestAnimationFrame(loop);
        if (document.hidden) return;
        if (now - last < 32) return;
        last = now;
        smoothRef.current += (levelRef.current - smoothRef.current) * 0.16;
        paint((now - start) / 1000);
      };
      raf = requestAnimationFrame(loop);
    }

    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      observer?.disconnect();
    };
  }, [reduced]);

  // Reduced motion: redraw only when the state or level actually changes.
  useEffect(() => {
    if (!reduced) return;
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!ctx) return;
    smoothRef.current = level;
    drawFrame(ctx, sizeRef.current, 0, level, state, level);
  }, [reduced, state, level]);

  const color = STATE_COLOR[state] ?? STATE_COLOR.idle;

  return (
    <div
      className={`ring ring--${state}`}
      style={{ ['--ring-color' as string]: color }}
    >
      <canvas
        ref={canvasRef}
        className="ring__canvas"
        aria-hidden="true"
      />
      <div
        className="ring__center"
        role="img"
        aria-label={`Voice level ${levelPercent(level)} percent, status ${stateLabel(state)}`}
      >
        <span className="ring__state">{stateLabel(state)}</span>
        <span className="ring__level">{levelPercent(level)}</span>
        <span className="ring__unit">INPUT %</span>
        <span className="ring__hair" aria-hidden="true" />
        <span className="ring__hint">
          {state === 'listening'
            ? 'CAPTURING'
            : state === 'processing'
              ? 'SYNTHESIS'
              : state === 'preview'
                ? 'AWAITING REVIEW'
                : state === 'done'
                  ? 'COMMITTED'
                  : state === 'error'
                    ? 'REFUSED'
                    : 'STANDBY'}
        </span>
      </div>
    </div>
  );
}
