/**
 * ChatWorkspacePicker — choose which host directory a FRESH dashboard chat
 * starts in.
 *
 * The dashboard is the "drive Qubit from a phone or any browser" surface,
 * yet every new /chat used to spawn in the dashboard process's launch
 * directory with no way to say "work in ~/code/foo". The Desktop sidebar
 * already knows the user's projects and discovered repos; this exposes the
 * same list here (GET /api/chat/workspaces) so a fresh chat can be aimed at
 * one of them, or at a typed path. The choice is sent as `/api/pty?cwd=` and
 * only affects fresh chats — a resumed session keeps its own workspace.
 *
 * The value is owned by ChatPage (persisted per profile) so the picker can be
 * changed at any time and applies on the next "New chat".
 */

import { Button, Input, Select } from "@/sakuga/ui";
import { FolderGit2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useI18n } from "@/i18n";
import { api, type ChatWorkspacesResponse } from "@/lib/api";
import { abbreviateHomePath, workspaceOptions } from "@/lib/chat-workspaces";
import { cn } from "@/lib/utils";

const CUSTOM_VALUE = "\u0000custom";

interface ChatWorkspacePickerProps {
  /** Management profile the chat is scoped to (its projects.db + sessions). */
  profile?: string;
  /** Picked workspace (absolute host path) or "" for the server default. */
  value: string;
  onChange: (cwd: string) => void;
  className?: string;
}

export function ChatWorkspacePicker({
  profile,
  value,
  onChange,
  className,
}: ChatWorkspacePickerProps) {
  const { t } = useI18n();
  const [data, setData] = useState<ChatWorkspacesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [customOpen, setCustomOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const requestRef = useRef(0);

  const load = useCallback(
    (scan: boolean) => {
      const token = ++requestRef.current;
      void api
        .getChatWorkspaces(profile, scan)
        .then((res) => {
          if (token !== requestRef.current) return;
          setData(res);
        })
        .catch(() => {
          // Best-effort: the picker degrades to default + custom path.
        })
        .finally(() => {
          if (token === requestRef.current) setLoading(false);
        });
    },
    [profile],
  );
  const rescan = useCallback(() => {
    setLoading(true);
    load(true);
  }, [load]);

  useEffect(() => {
    load(false);
  }, [load]);

  const options = useMemo(() => (data ? workspaceOptions(data) : []), [data]);
  const home = data?.home ?? "";
  // A persisted custom path (or a repo that dropped out of discovery) still
  // renders as the selected row instead of snapping back to the default.
  const valueListed = !value || options.some((o) => o.value === value);
  const defaultLabel = data?.default_cwd
    ? `${t.sessions.workspaceDefault} · ${abbreviateHomePath(data.default_cwd, home)}`
    : t.sessions.workspaceDefault;

  const onSelect = useCallback(
    (next: string) => {
      if (next === CUSTOM_VALUE) {
        setDraft(value);
        setCustomOpen(true);
        return;
      }
      setCustomOpen(false);
      onChange(next);
    },
    [onChange, value],
  );

  const commitDraft = useCallback(() => {
    const trimmed = draft.trim();
    setCustomOpen(false);
    if (trimmed !== value) onChange(trimmed);
  }, [draft, onChange, value]);

  return (
    <div className={cn("flex flex-col gap-1.5 px-2 pb-2", className)}>
      <div className="flex items-center gap-2 text-xs">
        <div className="flex items-center gap-1.5 text-text-tertiary">
          <FolderGit2 className="h-3.5 w-3.5" />
          <span className="text-display tracking-wider">{t.sessions.workspace}</span>
        </div>
        <Button
          variant="secondary"
          onClick={rescan}
          disabled={loading}
          aria-label={t.sessions.workspaceRescan}
          title={t.sessions.workspaceRescan}
          className="ml-auto text-text-secondary hover:text-foreground"
        >
          <RefreshCw className={cn(loading && "animate-spin")} />
        </Button>
      </div>
      <Select
        label={t.sessions.workspace}
        className="qsk-field min-w-0"
        onChange={onSelect}
        value={customOpen ? CUSTOM_VALUE : value}
        options={[
          { value: "", label: defaultLabel },
          ...options,
          ...(!valueListed && !customOpen
            ? [{ value, label: abbreviateHomePath(value, home) }]
            : []),
          { value: CUSTOM_VALUE, label: t.sessions.workspaceCustom },
        ]}
      />
      {customOpen && (
        <Input
          label={t.sessions.workspaceCustom}
          value={draft}
          placeholder={home ? `${home}/…` : "/path/to/project"}
          onChange={setDraft}
          onBlur={commitDraft}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitDraft();
            if (e.key === "Escape") setCustomOpen(false);
          }}
          className="h-8 text-xs"
        />
      )}
    </div>
  );
}
