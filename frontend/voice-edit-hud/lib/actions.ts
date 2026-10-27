import type { ClientAction, HudState } from './types';

export interface ActionSpec {
  action: ClientAction;
  label: string;
  hint: string;
  primary?: boolean;
  accent?: 'magenta' | 'cyan' | 'violet';
}

export const ACTION_SPECS: readonly ActionSpec[] = [
  { action: 'apply', label: 'Apply', hint: 'Ctrl+Enter', primary: true },
  { action: 'copy', label: 'Copy', hint: 'C', accent: 'cyan' },
  { action: 'edit', label: 'Edit', hint: 'E', accent: 'violet' },
  { action: 'retry', label: 'Retry', hint: 'Ctrl+R' },
  { action: 'revert', label: 'Revert', hint: 'Ctrl+Z' },
  { action: 'cancel', label: 'Cancel', hint: 'Esc' },
] as const;

export interface ActionAvailability {
  state: HudState;
  hasPreview: boolean;
  connected: boolean;
}

/**
 * Which actions the preview exposes.
 *
 * The brief is "actions disabled until preview": Apply/Copy/Edit/Retry/Revert
 * stay dark until a preview exists. Cancel is the escape hatch and is live
 * whenever the socket is. While a retry is processing, every action waits.
 * After a completed apply only Copy/Retry/Revert remain (there is nothing left
 * to apply or edit).
 */
export function canRunAction(
  action: ClientAction,
  { state, hasPreview, connected }: ActionAvailability,
): boolean {
  if (action === 'cancel') return connected;
  if (!connected || !hasPreview) return false;
  if (state === 'processing') return false;
  if (state === 'idle' || state === 'listening') return false;
  if (state === 'done' || state === 'interrupted') {
    return action === 'copy' || action === 'retry' || action === 'revert';
  }
  if (state === 'error') {
    return action !== 'apply' && action !== 'edit';
  }
  return true;
}

export function canSubmitEdit(text: string, connected: boolean): boolean {
  return connected && text.trim().length > 0;
}

export function editPayload(text: string): Record<string, unknown> {
  return { text };
}

/** True when a keyboard shortcut is typing into a form control. */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName.toLowerCase();
  return (
    tag === 'input' ||
    tag === 'textarea' ||
    tag === 'select' ||
    Boolean(target.isContentEditable)
  );
}
