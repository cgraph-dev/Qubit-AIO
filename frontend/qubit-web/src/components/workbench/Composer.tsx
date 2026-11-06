/**
 * Composer — the workbench's input row and footer control cluster.
 *
 * Ported layout idea: one footer strip under the input carries the controls
 * (workspace toggle, control center, context ring), instead of scattering them
 * across a header and a side panel.
 *
 * This composer does NOT own voice. `/qubit-chat` owns push-to-talk, the
 * Right-Ctrl hotkey and read-aloud; nothing here touches them.
 */

import { CornerDownLeft, PanelRight, SlidersHorizontal } from "lucide-react";
import { useEffect, useRef, type KeyboardEvent } from "react";

import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { ContextRing } from "@/components/chat/ContextRing";

export function Composer({
  value,
  onChange,
  onSubmit,
  disabled,
  disabledReason,
  submitting,
  workspaceOpen,
  onToggleWorkspace,
  controlsOpen,
  onToggleControls,
  contextPercent,
  usedTokens,
  windowTokens,
}: {
  value: string;
  onChange: (next: string) => void;
  onSubmit: () => void;
  /** No route behind POST /turn, or no session to send to. */
  disabled: boolean;
  disabledReason: string | null;
  submitting: boolean;
  workspaceOpen: boolean;
  onToggleWorkspace: () => void;
  controlsOpen: boolean;
  onToggleControls: () => void;
  contextPercent: number | null;
  usedTokens: number | null;
  windowTokens: number | null;
}) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Grow with the content, capped so the transcript keeps the height.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [value]);

  const canSend = !disabled && !submitting && value.trim().length > 0;

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (canSend) onSubmit();
    }
  };

  return (
    <form
      data-testid="workbench-composer"
      className="flex flex-col gap-1 px-3 py-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSend) onSubmit();
      }}
    >
      <div className="flex items-end gap-2 border border-border bg-background-base px-2 py-1.5 focus-within:border-primary/50">
        <label className="min-w-0 flex-1">
          <span className="sr-only">Message</span>
          <textarea
            ref={textareaRef}
            value={value}
            rows={1}
            onChange={(event) => onChange(event.currentTarget.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              disabled && disabledReason ? disabledReason : "Send a message… (Enter to send)"
            }
            aria-label="Message"
            disabled={disabled || submitting}
            className="w-full resize-none bg-transparent text-sm text-foreground outline-none placeholder:text-text-tertiary"
          />
        </label>
        <Button
          type="submit"
          ghost
          size="icon"
          disabled={!canSend}
          aria-label="Send message"
          title="Send message"
        >
          {submitting ? <Spinner className="text-sm" /> : <CornerDownLeft />}
        </Button>
      </div>

      {/* Footer control cluster */}
      <div
        className="flex flex-wrap items-center gap-1.5 text-[11px] text-text-tertiary"
        data-testid="workbench-composer-footer"
      >
        <Button
          ghost
          size="sm"
          aria-pressed={workspaceOpen}
          aria-label="Toggle workspace files panel"
          title="Workspace files"
          onClick={onToggleWorkspace}
          prefix={<PanelRight />}
        >
          Files
        </Button>
        <Button
          ghost
          size="sm"
          aria-pressed={controlsOpen}
          aria-label="Toggle control center"
          title="Conversation tools and controls"
          onClick={onToggleControls}
          prefix={<SlidersHorizontal />}
        >
          Controls
        </Button>
        <span className="ml-auto flex items-center gap-1.5">
          <span className="text-text-tertiary">Context</span>
          <ContextRing
            percent={contextPercent}
            usedTokens={usedTokens}
            windowTokens={windowTokens}
          />
        </span>
      </div>

      {disabled && disabledReason ? (
        <p role="note" className="text-[11px] text-warning">
          {disabledReason}
        </p>
      ) : null}
    </form>
  );
}

export default Composer;
