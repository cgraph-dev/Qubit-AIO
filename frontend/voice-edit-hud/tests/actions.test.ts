import { describe, expect, it } from 'vitest';
import {
  canRunAction,
  canSubmitEdit,
  editPayload,
  isTypingTarget,
} from '@/lib/actions';
import {
  buildActionMessage,
  serializeAction,
} from '@/lib/ws';

const base = { hasPreview: true, connected: true };

describe('canRunAction', () => {
  it('keeps every preview action dark until a preview exists', () => {
    for (const action of ['apply', 'copy', 'edit', 'retry', 'revert'] as const) {
      expect(
        canRunAction(action, { ...base, hasPreview: false, state: 'preview' }),
      ).toBe(false);
    }
  });

  it('opens the full set during preview', () => {
    for (const action of ['apply', 'copy', 'edit', 'retry', 'revert'] as const) {
      expect(canRunAction(action, { ...base, state: 'preview' })).toBe(true);
    }
  });

  it('disables actions while a retry processes', () => {
    for (const action of ['apply', 'copy', 'edit', 'retry', 'revert'] as const) {
      expect(canRunAction(action, { ...base, state: 'processing' })).toBe(false);
    }
    expect(canRunAction('cancel', { ...base, state: 'processing' })).toBe(true);
  });

  it('after apply leaves copy/retry/revert', () => {
    expect(canRunAction('apply', { ...base, state: 'done' })).toBe(false);
    expect(canRunAction('edit', { ...base, state: 'done' })).toBe(false);
    expect(canRunAction('copy', { ...base, state: 'done' })).toBe(true);
    expect(canRunAction('retry', { ...base, state: 'done' })).toBe(true);
    expect(canRunAction('revert', { ...base, state: 'done' })).toBe(true);
  });

  it('keeps cancel live whenever the socket is up', () => {
    expect(
      canRunAction('cancel', { state: 'idle', hasPreview: false, connected: true }),
    ).toBe(true);
    expect(
      canRunAction('cancel', { state: 'idle', hasPreview: false, connected: false }),
    ).toBe(false);
  });

  it('refuses every action when disconnected', () => {
    expect(canRunAction('apply', { ...base, connected: false, state: 'preview' })).toBe(false);
  });
});

describe('action payloads', () => {
  it('omits payload for bare actions', () => {
    expect(buildActionMessage('apply')).toEqual({ action: 'apply' });
  });

  it('serialises edit_submit with text', () => {
    expect(JSON.parse(serializeAction('edit_submit', editPayload(' hi ')))).toEqual({
      action: 'edit_submit',
      payload: { text: ' hi ' },
    });
  });

  it('requires non-empty editor text', () => {
    expect(canSubmitEdit('   ', true)).toBe(false);
    expect(canSubmitEdit('x', false)).toBe(false);
    expect(canSubmitEdit('x', true)).toBe(true);
  });

  it('detects typing targets', () => {
    expect(isTypingTarget(document.createElement('textarea'))).toBe(true);
    expect(isTypingTarget(document.createElement('div'))).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });
});
