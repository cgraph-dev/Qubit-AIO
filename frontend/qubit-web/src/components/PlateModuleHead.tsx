import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * A module heading on a plate.
 *
 * The nav rail's sections and the chat side panel's modules are the same
 * primitive: a wide-tracked monospace label, an optional two-digit index, and a
 * hairline rule running to the module's edge. Sharing the component (and the
 * `--qplate-*` tokens behind it) is what keeps the two plates from drifting
 * into separate design languages.
 */
export function PlateModuleHead({
  id,
  label,
  index,
  trailing,
  className,
}: PlateModuleHeadProps) {
  return (
    <span className={cn("qplate-module__head", className)} id={id}>
      {index && <span className="qplate-module__index">{index}</span>}
      <span className="min-w-0 truncate">{label}</span>
      {trailing}
      <span aria-hidden className="qplate-module__rule" />
    </span>
  );
}

interface PlateModuleHeadProps {
  /** DOM id, for `aria-labelledby` on the module's group. */
  id?: string;
  label: string;
  /** Two-digit section index ("02"). */
  index?: string;
  /** Extra content between the label and the rule (a count, a chip). */
  trailing?: ReactNode;
  className?: string;
}
