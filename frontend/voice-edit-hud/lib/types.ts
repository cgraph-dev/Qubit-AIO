/**
 * Frozen Voice Edit WebSocket contract (client side).
 *
 * Endpoint: `/voice-edit/ws?token=<token>`
 * The backend implements the same shapes; anything unknown is ignored so a
 * newer server can add fields without breaking this HUD.
 */

export type HudState =
  | 'idle'
  | 'listening'
  | 'processing'
  | 'preview'
  | 'done'
  | 'error'
  | 'clarify'
  | 'disabled'
  | 'interrupted'
  | (string & {});

export interface PreviewDetected {
  action?: string;
  style?: string;
  language?: string;
  audience?: string;
}

export interface PreviewPayload {
  original?: string;
  proposed?: string;
  detected?: PreviewDetected;
  model?: string;
  provider?: string;
  privacy_mode?: string;
  locality?: string;
  target_app?: string;
  /** Frozen contract field. `latency_ms` is accepted for older helpers. */
  elapsed_ms?: number;
  latency_ms?: number;
  injection_suspected?: boolean;
  clarification_returned?: boolean;
  /** Backend sends a string[]; a bare number is tolerated. */
  facts_dropped?: string[] | number;
  command?: unknown;
  prompt_version?: string;
  selection_digest?: string;
  clipboard_ok?: boolean;
  clipboard_backend?: string;
  clipboard_code?: string;
  ok?: boolean;
  mode?: string;
  request_id?: string;
}

export interface HelloMessage {
  type: 'hello';
  version?: string;
  state?: HudState;
}

export interface StatusMessage {
  type: 'status';
  state: HudState;
  detail?: string;
  level?: number;
}

export interface PartialMessage {
  type: 'partial';
  text: string;
  final: boolean;
}

export interface PreviewMessage {
  type: 'preview';
  payload: PreviewPayload;
  /** Some servers wrap the payload one level deeper; parsed defensively. */
  data?: PreviewPayload;
  original?: string;
  proposed?: string;
}

export interface ErrorMessage {
  type: 'error';
  code: string;
  detail?: string;
}

export interface HistoryMessage {
  type: 'history';
  kind: string;
  heard?: string;
  result?: string;
  ts?: string | number;
}

export interface LevelMessage {
  type: 'level';
  value: number;
}

export interface PingMessage {
  type: 'ping';
}

export type KnownServerMessage =
  | HelloMessage
  | StatusMessage
  | PartialMessage
  | PreviewMessage
  | ErrorMessage
  | HistoryMessage
  | LevelMessage
  | PingMessage;

/** A newer/unknown frame type: preserved by the parser, ignored by the reducer. */
export interface UnknownServerMessage {
  type: string;
  [key: string]: unknown;
}

export type ParsedServerMessage = KnownServerMessage | UnknownServerMessage;

/** The frames the reducer actually handles. */
export type ServerMessage = KnownServerMessage;

export type ClientAction =
  | 'apply'
  | 'copy'
  | 'edit'
  | 'retry'
  | 'revert'
  | 'cancel'
  | 'edit_submit';

export interface ActionMessage {
  action: ClientAction;
  payload?: Record<string, unknown>;
}

export type ConnectionState =
  | 'connecting'
  | 'open'
  | 'closed'
  | 'no-token'
  | 'mock';

export interface HistoryEntry {
  id: string;
  kind: string;
  heard: string;
  result: string;
  ts: string;
}

export interface TranscriptState {
  /** Text the STT has finalized. */
  committed: string;
  /** The still-unsettled tail (emphasised in the UI). */
  pending: string;
}

export interface HudError {
  code: string;
  detail: string;
}

export interface HudStateData {
  connection: ConnectionState;
  version: string;
  state: HudState;
  detail: string;
  level: number;
  transcript: TranscriptState;
  preview: PreviewPayload | null;
  previewRevision: number;
  error: HudError | null;
  notice: string | null;
  history: HistoryEntry[];
}
