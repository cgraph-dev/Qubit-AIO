import type {
  ClientAction,
  ConnectionState,
  HistoryEntry,
  HudStateData,
  ServerMessage,
  TranscriptState,
} from './types';

export type HudAction =
  | { type: 'server'; message: ServerMessage }
  | { type: 'connection'; status: ConnectionState }
  | { type: 'local'; kind: 'reset' }
  | { type: 'local'; kind: 'dismiss-error' }
  | { type: 'local'; kind: 'clear-history' }
  | { type: 'local'; kind: 'notice'; notice: string }
  | { type: 'local'; kind: 'clear-notice' }
  | { type: 'local'; kind: 'action-sent'; action: ClientAction };

export const initialHudState: HudStateData = {
  connection: 'connecting',
  version: '',
  state: 'idle',
  detail: '',
  level: 0,
  transcript: { committed: '', pending: '' },
  preview: null,
  previewRevision: 0,
  error: null,
  notice: null,
  history: [],
};

export function clampLevel(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

function historyEntry(
  index: number,
  kind: string,
  heard: string | undefined,
  result: string | undefined,
  ts: string | number | undefined,
): HistoryEntry {
  const stamp =
    typeof ts === 'string' ? ts : typeof ts === 'number' ? String(ts) : '';
  return {
    id: `${kind}:${stamp}:${index}`,
    kind: kind || 'turn',
    heard: heard ?? '',
    result: result ?? '',
    ts: stamp,
  };
}

/**
 * Merge a partial/final STT frame into the transcript.
 *
 * The backend contract says `partial{text,final}` but real STT lanes have
 * historically sent either **segment** text (the pending tail only) or
 * **cumulative** text (everything heard so far). Both are accepted:
 * - a frame that starts with what we already have is cumulative;
 * - a shorter stale frame never erases a longer pending tail;
 * - anything else replaces the pending tail (a correction).
 */
export function mergePartial(
  transcript: TranscriptState,
  text: string,
  final: boolean,
): TranscriptState {
  const incoming = typeof text === 'string' ? text : '';

  if (final) {
    let piece = incoming || transcript.pending;
    if (transcript.committed && piece.startsWith(transcript.committed)) {
      piece = piece.slice(transcript.committed.length);
    } else if (
      transcript.pending &&
      piece.length < transcript.pending.length &&
      transcript.pending.startsWith(piece)
    ) {
      piece = transcript.pending;
    }
    const joined = [transcript.committed.trim(), piece.trim()]
      .filter(Boolean)
      .join(' ');
    return { committed: joined, pending: '' };
  }

  let pending = incoming;
  if (transcript.pending && incoming.startsWith(transcript.pending)) {
    pending = incoming;
  } else if (transcript.committed && incoming.startsWith(transcript.committed)) {
    pending = incoming.slice(transcript.committed.length);
  } else if (
    transcript.pending &&
    incoming.trim() &&
    transcript.pending.startsWith(incoming)
  ) {
    pending = transcript.pending;
  }
  return {
    committed: transcript.committed,
    pending: pending.replace(/^\s+/, ''),
  };
}

function withServer(state: HudStateData, message: ServerMessage): HudStateData {
  switch (message.type) {
    case 'hello':
      return {
        ...state,
        version: typeof message.version === 'string' ? message.version : '',
        state:
          typeof message.state === 'string' && message.state
            ? message.state
            : state.state,
      };

    case 'status': {
      const nextState = message.state || 'idle';
      const startsNewTurn =
        nextState === 'listening' && state.state !== 'listening';
      const clearsPreview = nextState === 'idle';
      const level =
        typeof message.level === 'number'
          ? clampLevel(message.level)
          : nextState === 'preview' || nextState === 'done' || nextState === 'idle'
            ? 0
            : state.level;

      const error =
        nextState === 'error' && !state.error
          ? {
              code: 'turn_failed',
              detail: message.detail || 'The turn failed.',
            }
          : state.error;

      return {
        ...state,
        state: nextState,
        detail:
          typeof message.detail === 'string' ? message.detail : '',
        level,
        transcript: startsNewTurn
          ? { committed: '', pending: '' }
          : state.transcript,
        preview: startsNewTurn || clearsPreview ? null : state.preview,
        error: startsNewTurn ? null : error,
      };
    }

    case 'partial':
      return {
        ...state,
        transcript: mergePartial(
          state.transcript,
          message.text,
          message.final === true,
        ),
      };

    case 'preview':
      return {
        ...state,
        state: 'preview',
        preview: message.payload,
        previewRevision: state.previewRevision + 1,
        level: 0,
        error: null,
      };

    case 'error':
      return {
        ...state,
        state: 'error',
        level: 0,
        error: {
          code: message.code || 'unknown_error',
          detail: message.detail || '',
        },
      };

    case 'history': {
      const entry = historyEntry(
        state.history.length,
        message.kind,
        message.heard,
        message.result,
        message.ts,
      );
      return {
        ...state,
        history: [entry, ...state.history].slice(0, 120),
      };
    }

    case 'level':
      return { ...state, level: clampLevel(message.value) };

    case 'ping':
      return state;

    default:
      return state;
  }
}

export function hudReducer(
  state: HudStateData,
  action: HudAction,
): HudStateData {
  switch (action.type) {
    case 'server':
      return withServer(state, action.message);

    case 'connection':
      return { ...state, connection: action.status };

    case 'local':
      switch (action.kind) {
        case 'reset':
          return { ...initialHudState, connection: state.connection };
        case 'dismiss-error':
          return {
            ...state,
            error: null,
            state: state.state === 'error' ? 'idle' : state.state,
          };
        case 'clear-history':
          return { ...state, history: [] };
        case 'notice':
          return { ...state, notice: action.notice };
        case 'clear-notice':
          return { ...state, notice: null };
        case 'action-sent':
          return { ...state, error: null, notice: null };
        default:
          return state;
      }

    default:
      return state;
  }
}
