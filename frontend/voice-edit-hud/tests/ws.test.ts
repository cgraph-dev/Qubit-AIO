import { describe, expect, it } from 'vitest';
import {
  buildActionMessage,
  buildWsUrl,
  parseServerMessage,
} from '@/lib/ws';

describe('buildWsUrl', () => {
  it('uses ws on http and wss on https', () => {
    expect(
      buildWsUrl('a b', { protocol: 'http:', host: '127.0.0.1:8890' }),
    ).toBe('ws://127.0.0.1:8890/voice-edit/ws?token=a%20b');
    expect(
      buildWsUrl('tok', { protocol: 'https:', host: 'example.test' }),
    ).toBe('wss://example.test/voice-edit/ws?token=tok');
  });
});

describe('parseServerMessage', () => {
  it('parses every frozen server frame', () => {
    expect(parseServerMessage('{"type":"hello","version":"1"}')).toEqual({
      type: 'hello',
      version: '1',
      state: undefined,
    });
    expect(
      parseServerMessage('{"type":"status","state":"listening","level":0.4}'),
    ).toEqual({
      type: 'status',
      state: 'listening',
      detail: undefined,
      level: 0.4,
    });
    expect(
      parseServerMessage('{"type":"partial","text":"hi","final":false}'),
    ).toEqual({ type: 'partial', text: 'hi', final: false });
    expect(parseServerMessage('{"type":"ping"}')).toEqual({ type: 'ping' });
    expect(
      parseServerMessage('{"type":"level","value":0.3}'),
    ).toEqual({ type: 'level', value: 0.3 });
  });

  it('normalises a preview payload and direct fields', () => {
    const parsed = parseServerMessage(
      JSON.stringify({
        type: 'preview',
        payload: { proposed: 'b', detected: { action: 'rewrite' } },
        original: 'a',
        proposed: 'c',
      }),
    );
    expect(parsed).toMatchObject({
      type: 'preview',
      payload: { original: 'a', proposed: 'c', detected: { action: 'rewrite' } },
    });
  });

  it('never throws on malformed frames', () => {
    expect(parseServerMessage('not json')).toBeNull();
    expect(parseServerMessage('[]')).toBeNull();
    expect(parseServerMessage('{"no":"type"}')).toBeNull();
    expect(parseServerMessage('{"type":5}')).toBeNull();
  });

  it('keeps unknown typed frames for forward compatibility', () => {
    expect(parseServerMessage('{"type":"future","x":1}')).toEqual({
      type: 'future',
      x: 1,
    });
  });
});

describe('buildActionMessage', () => {
  it('passes a payload through untouched', () => {
    expect(buildActionMessage('edit_submit', { text: 'x' })).toEqual({
      action: 'edit_submit',
      payload: { text: 'x' },
    });
  });
});
