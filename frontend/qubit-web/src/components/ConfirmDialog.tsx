import { Button } from "@/sakuga/ui";
import { AlertTriangle } from "lucide-react";
import { useId } from "react";
import { createPortal } from "react-dom";
import { useModalBehavior } from "@/hooks/useModalBehavior";
import { cn, themedBody } from "@/lib/utils";

interface ConfirmDialogProps {
  cancelLabel?: string;
  confirmLabel?: string;
  description?: string;
  destructive?: boolean;
  loading?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  open: boolean;
  title: string;
}

export function ConfirmDialog({
  cancelLabel = "Cancel",
  confirmLabel = "Confirm",
  description,
  destructive = false,
  loading = false,
  onCancel,
  onConfirm,
  open,
  title,
}: ConfirmDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useModalBehavior({
    open,
    onClose: onCancel,
    initialFocusSelector: destructive ? ".qm-confirm-dialog__cancel" : "[data-confirm]",
  });

  if (!open) return null;

  return createPortal(
    <div
      data-confirm-backdrop=""
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
      className="fixed inset-0 z-[200] flex items-center justify-center bg-background/85 p-4"
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        className={cn(
          themedBody,
          "qm-confirm-dialog relative max-h-[calc(100dvh-2rem)] w-full max-w-md overflow-y-auto border border-border bg-card shadow-2xl",
        )}
      >
        <div className="qm-confirm-dialog__header flex items-start gap-3 p-4 border-b border-border">
          {destructive && (
            <div aria-hidden className="mt-0.5 shrink-0 text-destructive">
              <AlertTriangle className="h-4 w-4" />
            </div>
          )}

          <div className="flex-1 min-w-0 flex flex-col gap-1">
            <h2
              id={titleId}
              className="qm-confirm-dialog__title font-mondwest text-display text-base tracking-wider"
            >
              {title}
            </h2>

            {description && (
              <p
                id={descriptionId}
                className="qm-confirm-dialog__description text-xs text-muted-foreground leading-relaxed whitespace-pre-line"
              >
                {description}
              </p>
            )}
          </div>
        </div>

        <div className="qm-confirm-dialog__actions flex items-center justify-end gap-2 p-3">
          <Button className="qm-confirm-dialog__cancel" variant="secondary" type="button" onClick={onCancel} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button
            data-confirm
            className="qm-confirm-dialog__confirm"
            type="button"
            variant="primary"
            onClick={onConfirm}
            disabled={loading}
          >
            {loading ? "…" : confirmLabel}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
