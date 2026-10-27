import type {
  ActionMessage,
  ClientAction,
  ParsedServerMessage,
  PreviewMessage,
  PreviewPayload,
  UnknownServerMessage,
} from './types';

/** Build the same-origin WebSocket URL for the frozen contract. */
export function buildWsUrl(
  token: string,
  locationLike: Pick<Location, 'protocol' | 'host'> = window.location,
): string {
  const proto = locationLike.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${locationLike.host}/voice-edit/ws?token=${encodeURIComponent(token)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Accept every payload shape we have seen from helpers while keeping the
 * frozen contract canonical: `{type:'preview', payload:{...}}`.
 */
export function normalizePreview(message: PreviewMessage): PreviewPayload {
  const nested = isRecord(message.payload) ? message.payload : {};
  const data = isRecord(message.data) ? message.data : {};
  const direct: PreviewPayload = {};
  if (typeof message.original === 'string') direct.original = message.original;
  if (typeof message.proposed === 'string') direct.proposed = message.proposed;
  return { ...data, ...nested, ...direct } as PreviewPayload;
}

/**
 * Parse one text frame. Unknown/invalid frames resolve to `null` so the
 * consumer can ignore them; never throws on a malformed feed.
 */
export function parseServerMessage(raw: string): ParsedServerMessage | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(value) || typeof value.type !== 'string') return null;

  switch (value.type) {
    case 'hello':
      return {
        type: 'hello',
        version: typeof value.version === 'string' ? value.version : undefined,
        state: typeof value.state === 'string' ? value.state : undefined,
      };
    case 'status':
      return {
        type: 'status',
        state: typeof value.state === 'string' ? value.state : 'idle',
        detail: typeof value.detail === 'string' ? value.detail : undefined,
        level: typeof value.level === 'number' ? value.level : undefined,
      };
    case 'partial':
      return {
        type: 'partial',
        text: typeof value.text === 'string' ? value.text : '',
        final: value.final === true,
      };
    case 'preview': {
      const payload = isRecord(value.payload)
        ? value.payload
        : isRecord(value.data)
          ? value.data
          : {};
      return {
        type: 'preview',
        payload: normalizePreview({
          type: 'preview',
          payload: payload as PreviewPayload,
          original: typeof value.original === 'string' ? value.original : undefined,
          proposed: typeof value.proposed === 'string' ? value.proposed : undefined,
        }),
      };
    }
    case 'error':
      return {
        type: 'error',
        code: typeof value.code === 'string' ? value.code : 'unknown_error',
        detail: typeof value.detail === 'string' ? value.detail : undefined,
      };
    case 'history':
      return {
        type: 'history',
        kind: typeof value.kind === 'string' ? value.kind : 'turn',
        heard: typeof value.heard === 'string' ? value.heard : undefined,
        result: typeof value.result === 'string' ? value.result : undefined,
        ts:
          typeof value.ts === 'string' || typeof value.ts === 'number'
            ? value.ts
            : undefined,
      };
    case 'level':
      return {
        type: 'level',
        value: typeof value.value === 'number' ? value.value : 0,
      };
    case 'ping':
      return { type: 'ping' };
    default:
      return value as UnknownServerMessage;
  }
}

/** One client frame. `payload` is omitted when the action carries none. */
export function buildActionMessage(
  action: ClientAction,
  payload?: Record<string, unknown>,
): ActionMessage {
  return payload === undefined ? { action } : { action, payload };
}

export function serializeAction(
  action: ClientAction,
  payload?: Record<string, unknown>,
): string {
  return JSON.stringify(buildActionMessage(action, payload));
}
