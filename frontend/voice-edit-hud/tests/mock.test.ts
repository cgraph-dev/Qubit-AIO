import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  MockEngine,
  MOCK_PROPOSED,
  MOCK_RETRY_PROPOSED,
} from '@/lib/mock';
import type { PreviewMessage, ServerMessage } from '@/lib/types';

function harness(): {
  engine: MockEngine;
  messages: ServerMessage[];
} {
  const messages: ServerMessage[] = [];
  const engine = new MockEngine((message) => messages.push(message));
  return { engine, messages };
}

function previews(messages: ServerMessage[]): PreviewMessage[] {
  return messages.filter(
    (message): message is PreviewMessage => message.type === 'preview',
  );
}

function statuses(messages: ServerMessage[]) {
  return messages.filter(
    (message) => message.type === 'status',
  ) as Extract<ServerMessage, { type: 'status' }>[];
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-10-27T12:00:00Z'));
});

afterEach(() => {
  vi.useRealTimers();
});

describe('MockEngine happy path', () => {
  it('runs listening -> partials -> final -> processing -> preview', () => {
    const { engine, messages } = harness();
    engine.start();
    vi.advanceTimersByTime(1700);

    expect(messages[0]).toMatchObject({ type: 'hello', version: 'mock-1' });
    expect(statuses(messages).some((m) => m.state === 'listening')).toBe(true);
    expect(statuses(messages).some((m) => m.state === 'processing')).toBe(true);

    const partials = messages.filter((m) => m.type === 'partial');
    expect(partials.length).toBeGreaterThanOrEqual(4);
    expect(partials.at(-1)).toMatchObject({ final: true });

    vi.advanceTimersByTime(1100);
    const preview = previews(messages).at(-1);
    expect(preview?.payload.proposed).toBe(MOCK_PROPOSED);
    expect(preview?.payload.detected?.action).toBe('rewrite');
    expect(preview?.payload.locality).toBe('local');
    expect(statuses(messages).some((m) => m.state === 'preview')).toBe(true);
    engine.stop();
  });

  it('auto-applies after the preview if untouched, showing the done state', () => {
    const { engine, messages } = harness();
    engine.start();
    vi.advanceTimersByTime(9500);
    const lastStatus = statuses(messages).at(-1);
    expect(lastStatus?.state).toBe('done');
    expect(
      messages.some(
        (m) => m.type === 'history' && m.kind === 'apply',
      ),
    ).toBe(true);
    engine.stop();
  });
});

describe('MockEngine actions', () => {
  it('apply settles done and records history', () => {
    const { engine, messages } = harness();
    engine.start();
    vi.advanceTimersByTime(2600);
    engine.handle('apply');
    expect(statuses(messages).at(-1)?.state).toBe('done');
    const history = messages.filter((m) => m.type === 'history');
    expect(history.at(-1)).toMatchObject({ kind: 'apply' });
    engine.stop();
  });

  it('retry cycles through processing into the flagged retry preview', () => {
    const { engine, messages } = harness();
    engine.start();
    vi.advanceTimersByTime(2600);
    engine.handle('retry');
    expect(statuses(messages).at(-1)?.state).toBe('processing');
    vi.advanceTimersByTime(1000);
    const preview = previews(messages).at(-1);
    expect(preview?.payload.proposed).toBe(MOCK_RETRY_PROPOSED);
    expect(preview?.payload.injection_suspected).toBe(true);
    expect(preview?.payload.facts_dropped).toEqual(['2026-10-27', 'projector']);
    expect(
      messages.some((m) => m.type === 'history' && m.kind === 'retry'),
    ).toBe(true);
    engine.stop();
  });

  it('edit_submit replaces the proposal and records the edit', () => {
    const { engine, messages } = harness();
    engine.start();
    vi.advanceTimersByTime(2600);
    engine.handle('edit_submit', { text: 'A manual proposal.' });
    expect(previews(messages).at(-1)?.payload.proposed).toBe('A manual proposal.');
    expect(
      messages.some((m) => m.type === 'history' && m.kind === 'edit'),
    ).toBe(true);
    engine.stop();
  });

  it('copy records a receipt without changing the preview', () => {
    const { engine, messages } = harness();
    engine.start();
    vi.advanceTimersByTime(2600);
    const before = previews(messages).length;
    engine.handle('copy');
    expect(previews(messages).length).toBe(before);
    expect(
      messages.some((m) => m.type === 'history' && m.kind === 'copy'),
    ).toBe(true);
    engine.stop();
  });

  it('revert and cancel each settle and log', () => {
    const { engine, messages } = harness();
    engine.start();
    vi.advanceTimersByTime(2600);
    engine.handle('revert');
    expect(statuses(messages).at(-1)).toMatchObject({ state: 'done' });
    expect(
      messages.some((m) => m.type === 'history' && m.kind === 'revert'),
    ).toBe(true);

    engine.handle('cancel');
    expect(statuses(messages).at(-1)).toMatchObject({ state: 'idle' });
    expect(
      messages.some((m) => m.type === 'history' && m.kind === 'cancel'),
    ).toBe(true);
    engine.stop();
  });

  it('injectRefusal emits the typed error and stops the auto-apply', () => {
    const { engine, messages } = harness();
    engine.start();
    vi.advanceTimersByTime(2600);
    engine.injectRefusal();
    expect(
      messages.some(
        (m) => m.type === 'error' && m.code === 'voice_edit_injection_refused',
      ),
    ).toBe(true);
    vi.advanceTimersByTime(12000);
    expect(statuses(messages).at(-1)?.state).toBe('error');
    engine.stop();
  });

  it('restart replays from hello', () => {
    const { engine, messages } = harness();
    engine.start();
    vi.advanceTimersByTime(2600);
    const count = messages.length;
    engine.restart();
    vi.advanceTimersByTime(100);
    expect(messages.length).toBeGreaterThan(count);
    expect(messages.at(-1)).toMatchObject({ type: 'status', state: 'listening' });
    engine.stop();
  });
});
