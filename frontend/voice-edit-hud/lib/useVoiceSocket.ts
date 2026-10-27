'use client';

import { useCallback, useEffect, useReducer, useRef } from 'react';
import { MockEngine } from './mock';
import { hudReducer, initialHudState } from './reducer';
import type { ClientAction, HudStateData, ServerMessage } from './types';
import {
  buildWsUrl,
  parseServerMessage,
  serializeAction,
} from './ws';

export interface VoiceSocketApi {
  state: HudStateData;
  send: (action: ClientAction, payload?: Record<string, unknown>) => void;
  restartMock: () => void;
  simulateRefusal: () => void;
  clearHistory: () => void;
  dismissError: () => void;
  dismissNotice: () => void;
  isMock: boolean;
  connected: boolean;
}

interface UseVoiceSocketOptions {
  /** Force the fixture feed (tests / static preview). */
  mock?: boolean;
  /** Override the `?token=` query parameter. */
  token?: string;
}

const RECONNECT_BASE_MS = 800;
const RECONNECT_MAX_MS = 10_000;

export function useVoiceSocket(
  options: UseVoiceSocketOptions = {},
): VoiceSocketApi {
  const [state, dispatch] = useReducer(hudReducer, initialHudState);
  const wsRef = useRef<WebSocket | null>(null);
  const mockRef = useRef<MockEngine | null>(null);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const params = new URLSearchParams(window.location.search);
    const useMock =
      options.mock ??
      (params.get('mock') === '1' ||
        process.env.NEXT_PUBLIC_MOCK === '1');
    const token = options.token ?? params.get('token') ?? '';

    if (useMock) {
      dispatch({ type: 'connection', status: 'mock' });
      const engine = new MockEngine((message) =>
        dispatch({ type: 'server', message }),
      );
      mockRef.current = engine;
      engine.start();
      return () => {
        engine.stop();
        mockRef.current = null;
      };
    }

    if (!token) {
      dispatch({ type: 'connection', status: 'no-token' });
      return;
    }

    let disposed = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let socket: WebSocket | null = null;

    const scheduleReconnect = () => {
      if (disposed) return;
      const delay = Math.min(
        RECONNECT_MAX_MS,
        RECONNECT_BASE_MS * 2 ** attempt,
      );
      attempt += 1;
      timer = setTimeout(connect, delay);
    };

    const connect = () => {
      if (disposed) return;
      dispatch({ type: 'connection', status: 'connecting' });
      try {
        socket = new WebSocket(buildWsUrl(token));
      } catch {
        dispatch({ type: 'connection', status: 'closed' });
        scheduleReconnect();
        return;
      }
      wsRef.current = socket;

      socket.onopen = () => {
        attempt = 0;
        dispatch({ type: 'connection', status: 'open' });
      };
      socket.onmessage = (event: MessageEvent) => {
        if (typeof event.data !== 'string') return;
        const message = parseServerMessage(event.data);
        if (message) {
          dispatch({ type: 'server', message: message as ServerMessage });
        }
      };
      socket.onerror = () => {
        try {
          socket?.close();
        } catch {
          /* ignore */
        }
      };
      socket.onclose = () => {
        if (disposed) return;
        dispatch({ type: 'connection', status: 'closed' });
        scheduleReconnect();
      };
    };

    connect();

    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      try {
        socket?.close();
      } catch {
        /* ignore */
      }
      wsRef.current = null;
    };
  }, [options.mock, options.token]);

  const send = useCallback(
    (action: ClientAction, payload?: Record<string, unknown>) => {
      dispatch({ type: 'local', kind: 'action-sent', action });
      const engine = mockRef.current;
      if (engine) {
        engine.handle(action, payload);
        return;
      }
      const socket = wsRef.current;
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(serializeAction(action, payload));
      }
    },
    [],
  );

  const restartMock = useCallback(() => {
    dispatch({ type: 'local', kind: 'reset' });
    dispatch({ type: 'connection', status: 'mock' });
    mockRef.current?.restart();
  }, []);

  const simulateRefusal = useCallback(() => {
    mockRef.current?.injectRefusal();
  }, []);

  const clearHistory = useCallback(() => {
    dispatch({ type: 'local', kind: 'clear-history' });
  }, []);

  const dismissError = useCallback(() => {
    dispatch({ type: 'local', kind: 'dismiss-error' });
  }, []);

  const dismissNotice = useCallback(() => {
    dispatch({ type: 'local', kind: 'clear-notice' });
  }, []);

  return {
    state,
    send,
    restartMock,
    simulateRefusal,
    clearHistory,
    dismissError,
    dismissNotice,
    isMock: state.connection === 'mock',
    connected:
      state.connection === 'open' || state.connection === 'mock',
  };
}
