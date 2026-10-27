import type { HudState, PreviewPayload } from './types';

export function truncate(value: string, limit: number): string {
  const text = String(value ?? '');
  return text.length <= limit ? text : `${text.slice(0, Math.max(0, limit - 1))}…`;
}

export function levelPercent(level: number): number {
  if (!Number.isFinite(level)) return 0;
  return Math.round(Math.min(1, Math.max(0, level)) * 100);
}

export function elapsedLabel(payload: PreviewPayload | null): string {
  if (!payload) return '—';
  const raw = payload.elapsed_ms ?? payload.latency_ms;
  if (typeof raw !== 'number' || !Number.isFinite(raw)) return '—';
  if (raw < 1000) return `${Math.round(raw)} ms`;
  return `${(raw / 1000).toFixed(2)} s`;
}

export function factsDroppedCount(payload: PreviewPayload | null): number {
  if (!payload) return 0;
  if (typeof payload.facts_dropped === 'number') return payload.facts_dropped;
  if (Array.isArray(payload.facts_dropped)) return payload.facts_dropped.length;
  return 0;
}

export function charCount(payload: PreviewPayload | null): number {
  return payload?.proposed?.length ?? 0;
}

export function clockLabel(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function historyStamp(ts: string): string {
  if (!ts) return '—';
  const numeric = Number(ts);
  if (Number.isFinite(numeric) && numeric > 1_000_000_000) {
    const ms = numeric > 1_000_000_000_000 ? numeric : numeric * 1000;
    return clockLabel(new Date(ms));
  }
  if (/^\d{4}-\d{2}-\d{2}T/.test(ts)) {
    const parsed = new Date(ts);
    if (!Number.isNaN(parsed.getTime())) return clockLabel(parsed);
  }
  return ts;
}

export const STATE_LABELS: Record<string, string> = {
  idle: 'Ready',
  listening: 'Listening',
  processing: 'Thinking',
  preview: 'Review',
  done: 'Applied',
  error: 'Error',
  clarify: 'Needs you',
  disabled: 'Disabled',
  interrupted: 'Stopped',
};

export function stateLabel(state: HudState): string {
  return STATE_LABELS[state] ?? String(state || 'Unknown');
}

export function stateDetail(state: HudState, detail: string): string {
  if (detail) return detail;
  switch (state) {
    case 'idle':
      return 'Standing by. The helper starts a turn on the hotkey.';
    case 'listening':
      return 'Speak the edit — the unsettled words stay highlighted.';
    case 'processing':
      return 'Rewriting through the configured model.';
    case 'preview':
      return 'Review the edit, then apply — or copy, edit, retry.';
    case 'done':
      return 'The edit has been applied.';
    case 'error':
      return 'The turn stopped. Read the refusal and retry or cancel.';
    case 'clarify':
      return 'The model asked a question — edit or retry.';
    default:
      return 'Waiting for the voice helper.';
  }
}

export function kindIcon(kind: string): string {
  switch (kind) {
    case 'dictate':
      return '✎';
    case 'answer':
    case 'chat':
      return '◆';
    case 'edit':
      return '✦';
    case 'task':
      return '⚙';
    case 'apply':
    case 'copy':
      return '✓';
    case 'retry':
      return '↺';
    case 'revert':
      return '↩';
    case 'cancel':
      return '×';
    case 'error':
      return '!';
    default:
      return '·';
  }
}

export function privacyLabel(payload: PreviewPayload | null): string {
  if (!payload) return '—';
  const privacy = payload.privacy_mode || 'auto';
  const locality = payload.locality || 'unknown';
  return `${privacy} / ${locality}`;
}
