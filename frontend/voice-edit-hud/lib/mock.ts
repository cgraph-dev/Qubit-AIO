import type {
  ClientAction,
  HistoryMessage,
  PreviewPayload,
  ServerMessage,
  StatusMessage,
} from './types';

export const MOCK_ORIGINAL =
  'hey can you send me the notes from the meeting i think they are in the folder somewhere';

export const MOCK_PROPOSED =
  'Could you send me the notes from the meeting? I believe they are in the shared folder.';

export const MOCK_RETRY_PROPOSED =
  'Hi — please send over the meeting notes when you get a chance. I think they’re in the shared folder.';

export const MOCK_PREVIEW: PreviewPayload = {
  original: MOCK_ORIGINAL,
  proposed: MOCK_PROPOSED,
  detected: {
    action: 'rewrite',
    style: 'professional',
    language: 'en',
    audience: 'colleague',
  },
  model: 'qubit-local-3b',
  provider: 'ollama',
  privacy_mode: 'strict',
  locality: 'local',
  target_app: 'Mail',
  elapsed_ms: 842,
  injection_suspected: false,
  clarification_returned: false,
  facts_dropped: [],
  prompt_version: 'mock-v1',
};

export const MOCK_RETRY_PREVIEW: PreviewPayload = {
  ...MOCK_PREVIEW,
  proposed: MOCK_RETRY_PROPOSED,
  detected: { ...MOCK_PREVIEW.detected, style: 'concise' },
  elapsed_ms: 611,
  // The retry deliberately exercises the review-note surface.
  injection_suspected: true,
  clarification_returned: false,
  facts_dropped: ['2026-10-27', 'projector'],
};

const SPEED = 1;

function truncate(value: string, limit: number): string {
  return value.length <= limit ? value : `${value.slice(0, limit - 1)}…`;
}

/**
 * Deterministic fixture feed used by `?mock=1`.
 *
 * It emits exactly the frozen server messages (hello/status/partial/preview/
 * error/history/level) and answers every client action, so the whole HUD is
 * viewable and testable without the desktop helper. One demo auto-apply runs
 * after the preview if the operator does nothing, so the animation also shows
 * the `preview -> done` ending.
 */
export class MockEngine {
  private timers = new Set<ReturnType<typeof setTimeout>>();
  private alive = false;
  private proposal = MOCK_PROPOSED;
  private autoApplyTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly emit: (message: ServerMessage) => void,
    private readonly speed: number = SPEED,
  ) {}

  start(): void {
    if (this.alive) this.stop();
    this.alive = true;
    this.proposal = MOCK_PROPOSED;

    this.at(0, { type: 'hello', version: 'mock-1', state: 'idle' });
    this.at(90, {
      type: 'status',
      state: 'listening',
      detail: 'Listening for your edit',
      level: 0.08,
    });

    const levels = [
      0.32, 0.55, 0.41, 0.68, 0.52, 0.81, 0.64, 0.92, 0.71, 0.58, 0.77, 0.46,
      0.63, 0.38,
    ];
    levels.forEach((level, index) => {
      this.at(220 + index * 95, { type: 'level', value: level });
    });

    this.at(380, {
      type: 'partial',
      text: 'make this',
      final: false,
    });
    this.at(720, {
      type: 'partial',
      text: 'make this sound',
      final: false,
    });
    this.at(1080, {
      type: 'partial',
      text: 'make this sound more confident',
      final: false,
    });
    this.at(1420, {
      type: 'partial',
      text: 'make this sound more confident',
      final: true,
    });

    this.at(1620, {
      type: 'status',
      state: 'processing',
      detail: 'Rewriting with qubit-local-3b',
      level: 0,
    });
    this.at(1760, { type: 'level', value: 0 });

    this.at(2440, { type: 'preview', payload: MOCK_PREVIEW });
    this.at(2540, {
      type: 'status',
      state: 'preview',
      detail: 'Review the edit, then apply — or copy, edit, retry.',
      level: 0,
    });

    this.autoApplyTimer = this.at(9400, () => {
      this.emitStatus('done', 'Applied to Mail (demo auto-apply).');
      this.emitHistory(
        'apply',
        'Demo auto-apply',
        truncate(this.proposal, 90),
      );
      this.emit({ type: 'level', value: 0 });
    });
  }

  stop(): void {
    this.alive = false;
    this.timers.forEach((timer) => clearTimeout(timer));
    this.timers.clear();
    this.autoApplyTimer = null;
  }

  restart(): void {
    this.stop();
    this.start();
  }

  /** Demo-only refusal so the error banner can be reviewed without a helper. */
  injectRefusal(): void {
    this.cancelAutoApply();
    this.emit({
      type: 'error',
      code: 'voice_edit_injection_refused',
      detail:
        'Instruction-like text in the selection was treated as data. Nothing was changed.',
    });
    this.emitStatus('error', 'Refused: the selection contained instruction text.');
  }

  handle(
    action: ClientAction,
    payload?: Record<string, unknown>,
  ): void {
    if (!this.alive) return;

    switch (action) {
      case 'apply': {
        this.cancelAutoApply();
        this.emitStatus('done', 'Applied to Mail.');
        this.emitHistory('apply', 'Apply edit', truncate(this.proposal, 90));
        this.emit({ type: 'level', value: 0 });
        break;
      }
      case 'copy': {
        this.emitHistory('copy', 'Copy result', truncate(this.proposal, 90));
        this.emitStatus(
          'preview',
          'Copied the proposal to the clipboard.',
        );
        break;
      }
      case 'edit':
        // UI-only: the editor overlay opens locally.
        break;
      case 'edit_submit': {
        const text =
          typeof payload?.text === 'string' ? payload.text : this.proposal;
        this.proposal = text;
        this.emit({
          type: 'preview',
          payload: { ...MOCK_PREVIEW, proposed: text },
        });
        this.emitHistory('edit', 'Manual edit', truncate(text, 90));
        this.emitStatus('preview', 'Manual edit ready to apply.');
        break;
      }
      case 'retry': {
        this.cancelAutoApply();
        this.emitStatus('processing', 'Retrying with a different phrasing.');
        this.emit({ type: 'level', value: 0 });
        this.at(900, {
          type: 'preview',
          payload: { ...MOCK_RETRY_PREVIEW, proposed: MOCK_RETRY_PROPOSED },
        });
        this.at(1000, {
          type: 'status',
          state: 'preview',
          detail: 'A different phrasing is ready. Review the flagged notes.',
          level: 0,
        });
        this.emitHistory('retry', 'Retry', 'Regenerated proposal.');
        this.proposal = MOCK_RETRY_PROPOSED;
        break;
      }
      case 'revert': {
        this.cancelAutoApply();
        this.emitStatus('done', 'Reverted to the original text.');
        this.emitHistory('revert', 'Revert', 'Original restored.');
        this.emit({ type: 'level', value: 0 });
        break;
      }
      case 'cancel': {
        this.cancelAutoApply();
        this.emitStatus('idle', 'Cancelled. Nothing was applied.');
        this.emitHistory('cancel', 'Cancel', 'No change applied.');
        this.emit({ type: 'level', value: 0 });
        break;
      }
      default:
        break;
    }
  }

  private cancelAutoApply(): void {
    if (this.autoApplyTimer !== null) {
      clearTimeout(this.autoApplyTimer);
      this.timers.delete(this.autoApplyTimer);
      this.autoApplyTimer = null;
    }
  }

  private emitStatus(
    state: StatusMessage['state'],
    detail: string,
  ): void {
    this.emit({ type: 'status', state, detail, level: 0 });
  }

  private emitHistory(
    kind: string,
    heard: string,
    result: string,
  ): void {
    const message: HistoryMessage = {
      type: 'history',
      kind,
      heard,
      result,
      ts: new Date().toISOString(),
    };
    this.emit(message);
  }

  private at(
    delayMs: number,
    messageOrFn: ServerMessage | (() => void),
  ): ReturnType<typeof setTimeout> {
    const timer = setTimeout(() => {
      this.timers.delete(timer);
      if (!this.alive) return;
      if (typeof messageOrFn === 'function') messageOrFn();
      else this.emit(messageOrFn);
    }, Math.max(0, delayMs / this.speed));
    this.timers.add(timer);
    return timer;
  }
}
