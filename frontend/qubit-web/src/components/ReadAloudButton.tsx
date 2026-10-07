/**
 * ReadAloudButton — the per-message "Read this to me" control.
 *
 * Speaks the message through the existing TTS path via the shared
 * {@link useReadAloud} controller. Cancellable mid-playback: pressing again
 * while THIS message is being read stops it. Keyboard-reachable (a real
 * `<button>`), no autoplay — audio only ever starts on a user activation.
 */
import { Volume2, Square } from "lucide-react";

import { useReadAloud, defaultReadAloudDeps } from "@/hooks/useReadAloud";
import type { ReadAloudDeps } from "@/lib/voice";

export interface ReadAloudButtonProps {
  /** Stable id for this message (the transcript index). */
  messageId: string;
  /** Text to speak. */
  text: string;
  /** Optional injected deps (tests). */
  deps?: ReadAloudDeps;
  /** Extra class names. */
  className?: string;
}

function shortLabel(state: "idle" | "speaking" | "other"): string {
  if (state === "speaking") return "Stop reading";
  if (state === "other") return "Read this to me (stops current audio)";
  return "Read this to me";
}

export function ReadAloudButton({
  messageId,
  text,
  deps = defaultReadAloudDeps,
  className,
}: ReadAloudButtonProps) {
  const { controller, snapshot } = useReadAloud(deps);
  const speaking = snapshot.speakingId === messageId && snapshot.busy;
  const otherSpeaking = snapshot.busy && snapshot.speakingId !== messageId;
  const state = speaking ? "speaking" : otherSpeaking ? "other" : "idle";

  return (
    <span className={className}>
      <button
        type="button"
        data-testid={`read-aloud-${messageId}`}
        data-state={state}
        aria-pressed={speaking}
        aria-label={shortLabel(state)}
        title={shortLabel(state)}
        disabled={!text.trim()}
        onClick={() =>
          void controller.toggle(messageId, text)
        }
        className="inline-flex items-center gap-1 rounded px-1 py-0.5 text-xs text-text-tertiary hover:bg-current/5 hover:text-foreground disabled:opacity-40"
      >
        {speaking ? (
          <Square className="h-3 w-3" aria-hidden="true" />
        ) : (
          <Volume2 className="h-3 w-3" aria-hidden="true" />
        )}
        {speaking ? "stop" : "read"}
      </button>
      {snapshot.error && (speaking || otherSpeaking) ? (
        <span role="alert" className="ml-1 text-xs text-destructive">
          {snapshot.error}
        </span>
      ) : null}
    </span>
  );
}

export default ReadAloudButton;
