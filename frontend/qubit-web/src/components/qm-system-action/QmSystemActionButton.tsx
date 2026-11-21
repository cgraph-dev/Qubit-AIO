import { useState, type FocusEvent, type MouseEvent, type RefObject } from "react";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { ImportantTooltip } from "@/components/ImportantTooltip";
import { SidebarTooltip } from "@/components/SidebarTooltip";
import { cn } from "@/lib/utils";
import "./qm-system-action.css";

/**
 * The owner-supplied Restart Gateway plate, shared by both system actions.
 *
 * Manga-only by contract: App renders this in place of the Q5T/Sakuga system
 * controls while `html[data-qubit-manga="on"]`, so classic themes keep their
 * existing buttons. Expanded and collapsed share the same plate language;
 * collapsed uses the square icon-only cut of the artwork.
 */
export type QmSystemActionKind = "restart" | "update";

interface QmSystemActionButtonProps {
  action: QmSystemActionKind;
  collapsed: boolean;
  disabled: boolean;
  isPending: boolean;
  isRunning: boolean;
  label: string;
  runningLabel: string;
  spin: boolean;
  disabledReason?: string;
  onClick: () => void;
  tooltipWarmRef: RefObject<number>;
}

export function QmSystemActionButton({
  action,
  collapsed,
  disabled,
  isPending,
  isRunning,
  label,
  runningLabel,
  spin,
  disabledReason,
  onClick,
  tooltipWarmRef,
}: QmSystemActionButtonProps) {
  const [tooltipAnchor, setTooltipAnchor] = useState<HTMLElement | null>(null);
  const busy = isPending || isRunning;
  const displayLabel = isRunning ? runningLabel : label;
  const showTooltip = (
    event: MouseEvent<HTMLElement> | FocusEvent<HTMLElement>,
  ) => {
    setTooltipAnchor(event.currentTarget);
  };
  const hideTooltip = () => {
    setTooltipAnchor(null);
  };
  const showSpinner = busy && (isPending || spin);

  return (
    <li
      className="qm-manga-system-actions__item"
      data-system-action={action}
      onMouseEnter={collapsed ? showTooltip : undefined}
      onMouseLeave={collapsed ? hideTooltip : undefined}
    >
      <ImportantTooltip
        active={Boolean(disabledReason)}
        title={displayLabel}
        detail={disabledReason}
      >
        <button
          type="button"
          className={cn(
            "qm-system-action",
            collapsed ? "qm-system-action--compact" : "qm-system-action--plate",
            `qm-system-action--${action}`,
            busy && "is-busy",
          )}
          disabled={disabled}
          aria-busy={busy}
          aria-label={displayLabel}
          onClick={onClick}
          onFocus={collapsed ? showTooltip : undefined}
          onBlur={collapsed ? hideTooltip : undefined}
        >
          <span className="qm-system-action__art" aria-hidden="true" />
          <span className="qm-system-action__icon" aria-hidden="true">
            {showSpinner ? (
              <Spinner className="qm-system-action__spinner" />
            ) : action === "restart" ? (
              <RestartGlyph />
            ) : (
              <UpdateGlyph isRunning={isRunning} />
            )}
          </span>
          {!collapsed && (
            <span className="qm-system-action__label">{displayLabel}</span>
          )}
        </button>
      </ImportantTooltip>

      {collapsed && tooltipAnchor && !disabledReason && (
        <SidebarTooltip
          anchor={tooltipAnchor}
          label={displayLabel}
          warmRef={tooltipWarmRef}
        />
      )}
    </li>
  );
}

function RestartGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
    >
      <path d="M21 3v6h-6M3 21v-6h6M4 9a8.5 8.5 0 0 1 14-4l3 4M3 15l3 4a8.5 8.5 0 0 0 14-4" />
    </svg>
  );
}

function UpdateGlyph({ isRunning }: { isRunning: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
      className={cn(isRunning && "qm-system-action__icon--pulse")}
    >
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <path d="m7 10 5 5 5-5" />
      <path d="M12 15V3" />
    </svg>
  );
}
