/**
 * Composer — the workbench's input row and footer control cluster.
 *
 * Layout ported from the absorbed console's composer footer
 * (`static/index.html:643-780`): attach, mic, image, divider, profile chip,
 * workspace chip, model chip, reasoning chip, context ring, circular send.
 *
 * **Every control here is either real or visibly disabled with a reason.**
 *
 * - attach, mic and image are DISABLED on this page. Voice lives on
 *   `/qubit-chat` (push-to-talk, the Right-Ctrl hotkey, read-aloud) and
 *   attachment upload has no workbench route — so each carries a title saying
 *   exactly that, instead of being a draw-only button that swallows clicks.
 * - the profile chip shows the dashboard's active profile (a real read) and is
 *   read-only: switching profiles remounts the route tree and belongs to the
 *   shell, not to one page.
 * - the workspace chip names the active workspace when the tree read returned
 *   one, and toggles the right panel. When no workspace is named it is disabled
 *   with the backend's own words in the tooltip.
 * - the model chip shows the model recorded for the selected session. Sending a
 *   one-off override is not wired: `POST /turn` accepts `model`, but a chip that
 *   silently changed the model for a single message would be a setting with no
 *   visible confirmation.
 * - the reasoning chip shows a stored level when the seam reports one; today it
 *   renders "default" and is disabled, because that is the transport's default
 *   rather than a measured setting.
 * - the context ring is the house `ContextRing`: last prompt over window, `·`
 *   when unmeasured. This seam carries no token measurement yet, so it renders
 *   the honest unmeasured state instead of a zero.
 */

import { CornerDownLeft, Image, Mic, Paperclip, User } from "lucide-react";
import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";
import { Link } from "react-router";

import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { ContextRing } from "@/components/chat/ContextRing";

/** A footer chip: one icon, one label, optional trailing affordance. */
function Chip({
  label,
  value,
  title,
  icon,
  disabled,
  testId,
  onClick,
  trailing,
}: {
  label: string;
  value: string;
  title: string;
  icon: ReactNode;
  disabled?: boolean;
  testId: string;
  onClick?: () => void;
  trailing?: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={`${label}: ${value}`}
      data-testid={testId}
      className="inline-flex h-6 min-w-0 shrink items-center gap-1 rounded-md border border-border bg-background-base px-1.5 text-[11px] text-text-secondary transition-colors hover:border-border hover:bg-secondary/40 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-background-base [&>svg]:h-3 [&>svg]:w-3"
    >
      <span className="shrink-0 opacity-70" aria-hidden="true">
        {icon}
      </span>
      <span className="max-w-[7.5rem] truncate">{value}</span>
      {trailing}
    </button>
  );
}

/** The circular send affordance. */
function SendButton({ canSend, submitting }: { canSend: boolean; submitting: boolean }) {
  return (
    <button
      type="submit"
      disabled={!canSend}
      aria-label="Send message"
      title={canSend ? "Send message" : "Type a message to send"}
      data-testid="workbench-send"
      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40 [&>svg]:h-4 [&>svg]:w-4"
    >
      {submitting ? <Spinner className="text-sm" /> : <CornerDownLeft aria-hidden="true" />}
    </button>
  );
}

/** A disabled cluster control that explains itself in its title. */
function InertIconButton({
  label,
  title,
  testId,
  children,
}: {
  label: string;
  title: string;
  testId: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      disabled
      aria-label={label}
      title={title}
      data-testid={testId}
      className="inline-flex h-6 w-6 shrink-0 cursor-not-allowed items-center justify-center rounded-md text-text-tertiary opacity-40 [&>svg]:h-3.5 [&>svg]:w-3.5"
    >
      {children}
    </button>
  );
}

export function Composer({
  value,
  onChange,
  onSubmit,
  disabled,
  disabledReason,
  submitting,
  contextPercent,
  usedTokens,
  windowTokens,
  profileName,
  workspaceName,
  workspaceWhy,
  modelName,
  reasoningName,
  workspaceOpen,
  onToggleWorkspace,
}: {
  value: string;
  onChange: (next: string) => void;
  onSubmit: () => void;
  /** No way to send right now (no route, or no session). */
  disabled: boolean;
  disabledReason: string | null;
  submitting: boolean;
  contextPercent: number | null;
  usedTokens: number | null;
  windowTokens: number | null;
  /** Active dashboard profile, or `null` when the read has not answered. */
  profileName: string | null;
  /** Active workspace, or `null` when the backend named none. */
  workspaceName: string | null;
  /** Why no workspace is named — the backend's own words. */
  workspaceWhy: string | null;
  /** The selected session's model, if any. */
  modelName: string | null;
  /** Stored reasoning level, if the seam reports one. */
  reasoningName: string | null;
  workspaceOpen: boolean;
  onToggleWorkspace: () => void;
}) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Grow with the content, capped so the transcript keeps the height.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 168)}px`;
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
      className="flex flex-col gap-1 px-3 pb-2 pt-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSend) onSubmit();
      }}
    >
      <div className="rounded-xl border border-border bg-background-base px-2 py-1.5 transition-colors focus-within:border-primary/50 focus-within:ring-2 focus-within:ring-primary/10">
        <label className="block">
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
            className="max-h-[168px] w-full resize-none bg-transparent px-1 py-1 text-sm text-foreground outline-none placeholder:text-text-tertiary disabled:cursor-not-allowed"
          />
        </label>

        <div
          className="flex min-w-0 flex-wrap items-center gap-1 pb-0.5"
          data-testid="workbench-composer-footer"
        >
          <InertIconButton
            label="Attach files — not available on the workbench"
            title="Attach files — attachment upload has no workbench route yet; use /qubit-chat for attachments"
            testId="workbench-attach"
          >
            <Paperclip aria-hidden="true" />
          </InertIconButton>
          <InertIconButton
            label="Dictate — voice lives on the chat surface"
            title="Voice input lives on /qubit-chat: push-to-talk, the Right-Ctrl hotkey and read-aloud are owned there"
            testId="workbench-mic"
          >
            <Mic aria-hidden="true" />
          </InertIconButton>
          <InertIconButton
            label="Attach an image — not available on the workbench"
            title="Image attachments have no workbench route yet; use /qubit-chat"
            testId="workbench-image"
          >
            <Image aria-hidden="true" />
          </InertIconButton>

          <span className="mx-0.5 h-4 w-px shrink-0 bg-border" aria-hidden="true" />

          <Chip
            label="Profile"
            value={profileName ?? "—"}
            title={
              profileName
                ? `Active dashboard profile: ${profileName}. Switch profiles from the dashboard sidebar — it remounts the route tree.`
                : "The active profile has not been read yet."
            }
            icon={<User />}
            disabled
            testId="workbench-profile-chip"
          />
          <Chip
            label="Workspace"
            value={workspaceName ?? "none"}
            title={
              workspaceName
                ? `Workspace: ${workspaceName}. Click to show or hide the workspace panel.`
                : (workspaceWhy ?? "The workspace tree has not reported a root yet.")
            }
            icon={<FolderGlyph />}
            onClick={onToggleWorkspace}
            testId="workbench-workspace-chip"
            trailing={
              <span className="shrink-0 opacity-50" aria-hidden="true">
                {workspaceOpen ? "▾" : "▸"}
              </span>
            }
          />
          <Chip
            label="Model"
            value={modelName ?? "not set"}
            title={
              modelName
                ? `Model recorded for this session: ${modelName}. A per-message model override is not wired on this page.`
                : "No session is open, so no model is recorded."
            }
            icon={<CpuGlyph />}
            disabled
            testId="workbench-model-chip"
          />
          <Chip
            label="Reasoning"
            value={reasoningName ?? "default"}
            title={
              reasoningName
                ? `Reasoning effort for this session: ${reasoningName}`
                : "This seam does not report a reasoning level; the transport uses its own default."
            }
            icon={<BrainGlyph />}
            disabled
            testId="workbench-reasoning-chip"
          />

          <span className="ml-auto flex shrink-0 items-center gap-1.5 pl-1">
            <ContextRing
              percent={contextPercent}
              usedTokens={usedTokens}
              windowTokens={windowTokens}
            />
            <SendButton canSend={canSend} submitting={submitting} />
          </span>
        </div>
      </div>

      {disabled && disabledReason ? (
        <p role="note" className="px-1 text-[11px] text-warning">
          {disabledReason}
        </p>
      ) : (
        <p className="px-1 text-[10px] text-text-tertiary">
          Voice input and read-aloud live on{" "}
          <Link to="/qubit-chat" className="underline underline-offset-2 hover:text-foreground">
            the chat surface
          </Link>
          .
        </p>
      )}
    </form>
  );
}

function FolderGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
    </svg>
  );
}

function CpuGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <rect x="4" y="4" width="16" height="16" rx="2" />
      <rect x="9" y="9" width="6" height="6" />
    </svg>
  );
}

function BrainGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96-.46 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z" />
      <path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96-.46 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z" />
    </svg>
  );
}

export default Composer;
