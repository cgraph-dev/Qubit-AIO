import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useVoiceSocket } from '@/lib/useVoiceSocket';
import { buildWsUrl } from '@/lib/ws';

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  url: string;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 3;
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  emit(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
}

function Probe() {
  const { state, send, connected } = useVoiceSocket({ token: 'tok' });
  return (
    <div>
      <span data-testid="connection">{state.connection}</span>
      <span data-testid="state">{state.state}</span>
      <span data-testid="connected">{String(connected)}</span>
      <button type="button" onClick={() => send('apply')}>
        apply
      </button>
      <button type="button" onClick={() => send('edit')}>
        open editor
      </button>
      <button type="button" onClick={() => send('edit_submit', { text: 'x' })}>
        edit
      </button>
    </div>
  );
}

const originalWebSocket = globalThis.WebSocket;

beforeEach(() => {
  FakeWebSocket.instances = [];
  Object.defineProperty(globalThis, 'WebSocket', {
    writable: true,
    value: FakeWebSocket,
  });
});

afterEach(() => {
  cleanup();
  Object.defineProperty(globalThis, 'WebSocket', {
    writable: true,
    value: originalWebSocket,
  });
});

describe('useVoiceSocket (live contract)', () => {
  it('connects to the frozen endpoint with the token and opens', () => {
    render(<Probe />);
    const socket = FakeWebSocket.instances[0];
    expect(socket).toBeDefined();
    expect(socket.url).toBe(
      buildWsUrl('tok', {
        protocol: window.location.protocol,
        host: window.location.host,
      }),
    );
    expect(screen.getByTestId('connection')).toHaveTextContent('connecting');

    act(() => socket.open());
    expect(screen.getByTestId('connection')).toHaveTextContent('open');
    expect(screen.getByTestId('connected')).toHaveTextContent('true');
  });

  it('feeds every server frame through the reducer', () => {
    render(<Probe />);
    const socket = FakeWebSocket.instances[0];
    act(() => {
      socket.open();
      socket.emit({ type: 'hello', version: 'live-1', state: 'idle' });
      socket.emit({ type: 'status', state: 'preview', detail: 'Review', level: 0 });
      socket.emit({ type: 'preview', payload: { original: 'a', proposed: 'b' } });
    });
    expect(screen.getByTestId('state')).toHaveTextContent('preview');
    expect(screen.getByTestId('connected')).toHaveTextContent('true');
  });

  it('serialises apply and edit_submit actions', () => {
    render(<Probe />);
    const socket = FakeWebSocket.instances[0];
    act(() => socket.open());

    fireEvent.click(screen.getByText('apply'));
    fireEvent.click(screen.getByText('open editor'));
    fireEvent.click(screen.getByText('edit'));
    expect(socket.sent).toEqual([
      JSON.stringify({ action: 'apply' }),
      JSON.stringify({ action: 'edit' }),
      JSON.stringify({ action: 'edit_submit', payload: { text: 'x' } }),
    ]);
  });

  it('never opens a socket without a token', () => {
    function NoToken() {
      const { state } = useVoiceSocket({ token: '' });
      return <span data-testid="connection">{state.connection}</span>;
    }
    render(<NoToken />);
    expect(FakeWebSocket.instances.length).toBe(0);
    expect(screen.getByTestId('connection')).toHaveTextContent('no-token');
  });

  it('schedules a bounded reconnect after a close', () => {
    vi.useFakeTimers();
    try {
      render(<Probe />);
      const first = FakeWebSocket.instances[0];
      act(() => first.open());
      act(() => first.onclose?.());
      expect(screen.getByTestId('connection')).toHaveTextContent('closed');
      act(() => {
        vi.advanceTimersByTime(800);
      });
      expect(FakeWebSocket.instances.length).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });
});
