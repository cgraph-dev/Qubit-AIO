import type { StatusResponse } from "@/lib/api";
import { useI18n } from "@/i18n";

/**
 * Version / org colophon at the very foot of the rail. The wrapper
 * (`qagent-sidebar__colophon`) owns the border, padding and type — this
 * component only answers "which version" and "whose product".
 */
export function SidebarFooter({ status }: SidebarFooterProps) {
  const { t } = useI18n();

  return (
    <div className="flex w-full items-center justify-between gap-2">
      <span className="tabular-nums">
        {status?.version != null ? `v${status.version}` : "—"}
      </span>

      <span>{t.app.footer.org}</span>
    </div>
  );
}

interface SidebarFooterProps {
  status: StatusResponse | null;
}
