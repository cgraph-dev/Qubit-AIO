import { describe, expect, it } from 'vitest';
import {
  hudReducer,
  initialHudState,
  mergePartial,
} from '@/lib/reducer';
import type { HudStateData, ServerMessage } from '@/lib/types';

function feed(state: HudStateData, ...messages: ServerMessage[]): HudStateData {
  return messages.reduce(
    (current, message) => hudReducer(current, { type: 'server', message }),
    state,
  );
}

describe('mergePartial', () => {
  it('grows a segment-style pending tail', () => {
    const a = mergePartial({ committed: '', pending: '' }, 'make this', false);
    expect(a).toEqual({ committed: '', pending: 'make this' });
    const b = mergePartial(a, 'make this sound', false);
    expect(b.pending).toBe('make this sound');
    const c = mergePartial(b, 'make this sound more confident', true);
    expect(c).toEqual({
      committed: 'make this sound more confident',
      pending: '',
    });
  });

  it('accepts cumulative frames that include the committed text', () => {
    const committed = mergePartial(
      { committed: '', pending: '' },
      'hello',
      true,
    );
    const pending = mergePartial(committed, 'hello world', false);
    expect(pending).toEqual({ committed: 'hello', pending: 'world' });
    const final = mergePartial(pending, 'hello world', true);
    expect(final).toEqual({ committed: 'hello world', pending: '' });
  });

  it('ignores a stale shorter pending frame', () => {
    const state = mergePartial(
      { committed: '', pending: 'make this sound' },
      'make this',
      false,
    );
    expect(state.pending).toBe('make this sound');
  });

  it('replaces the tail on a correction', () => {
    const state = mergePartial(
      { committed: '', pending: 'make this load' },
      'make it loud',
      false,
    );
    expect(state.pending).toBe('make it loud');
  });

  it('uses the pending tail when a final frame is empty', () => {
    const state = mergePartial(
      { committed: 'hello', pending: 'world' },
      '',
      true,
    );
    expect(state).toEqual({ committed: 'hello world', pending: '' });
  });
});

describe('hudReducer server messages', () => {
  it('hydrates hello version/state', () => {
    const state = feed(initialHudState, {
      type: 'hello',
      version: 'hud-v1',
      state: 'idle',
    });
    expect(state.version).toBe('hud-v1');
    expect(state.state).toBe('idle');
  });

  it('tracks status and clamps levels', () => {
    const state = feed(
      initialHudState,
      { type: 'status', state: 'listening', detail: 'Go', level: 4 },
    );
    expect(state.state).toBe('listening');
    expect(state.detail).toBe('Go');
    expect(state.level).toBe(1);
    const down = feed(state, { type: 'level', value: -2 });
    expect(down.level).toBe(0);
  });

  it('stores a preview and counts revisions', () => {
    const preview = {
      original: 'a',
      proposed: 'b',
      detected: { action: 'rewrite' },
      elapsed_ms: 12,
    };
    const state = feed(
      initialHudState,
      { type: 'preview', payload: preview },
      { type: 'preview', payload: { ...preview, proposed: 'c' } },
    );
    expect(state.state).toBe('preview');
    expect(state.preview?.proposed).toBe('c');
    expect(state.previewRevision).toBe(2);
  });

  it('raises and dismisses a typed error', () => {
    const state = feed(initialHudState, {
      type: 'error',
      code: 'refused',
      detail: 'nope',
    });
    expect(state.state).toBe('error');
    expect(state.error).toEqual({ code: 'refused', detail: 'nope' });
    const dismissed = hudReducer(state, {
      type: 'local',
      kind: 'dismiss-error',
    });
    expect(dismissed.error).toBeNull();
    expect(dismissed.state).toBe('idle');
  });

  it('prepends history entries and caps them', () => {
    const state = feed(
      initialHudState,
      { type: 'history', kind: 'dictate', heard: 'one', result: 'ok' },
      { type: 'history', kind: 'apply', heard: 'two', result: 'done' },
    );
    expect(state.history.map((row) => row.kind)).toEqual(['apply', 'dictate']);
    expect(state.history[0].heard).toBe('two');
  });

  it('clears the preview and transcript when a new turn listens', () => {
    const state = feed(
      initialHudState,
      { type: 'partial', text: 'hello', final: true },
      { type: 'preview', payload: { original: 'a', proposed: 'b' } },
      { type: 'status', state: 'done', detail: 'applied' },
      { type: 'status', state: 'listening', detail: 'go' },
    );
    expect(state.transcript).toEqual({ committed: '', pending: '' });
    expect(state.preview).toBeNull();
    expect(state.state).toBe('listening');
  });

  it('clears the preview on idle but keeps the settled transcript', () => {
    const state = feed(
      initialHudState,
      { type: 'partial', text: 'hello', final: true },
      { type: 'preview', payload: { original: 'a', proposed: 'b' } },
      { type: 'status', state: 'idle', detail: 'cancelled' },
    );
    expect(state.preview).toBeNull();
    expect(state.transcript.committed).toBe('hello');
  });

  it('ignores ping and unknown frames', () => {
    const state = feed(
      initialHudState,
      { type: 'ping' },
      { type: 'future_event', value: 1 } as unknown as ServerMessage,
    );
    expect(state).toEqual(initialHudState);
  });

  it('clears history locally', () => {
    const populated = feed(initialHudState, {
      type: 'history',
      kind: 'apply',
    });
    const cleared = hudReducer(populated, {
      type: 'local',
      kind: 'clear-history',
    });
    expect(cleared.history).toEqual([]);
  });
});
