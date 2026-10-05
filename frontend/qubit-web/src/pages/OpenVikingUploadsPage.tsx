/**
 * Open Viking uploads (`/open-viking/uploads`).
 *
 * Qubit-owned add-resource surface over the local context engine:
 * resource ingest and memory writes are staged and confirm-gated
 * through `@/lib/ocFamilyVikingApi` (`resource_add`, `memory_add`).
 * Nothing is sent without confirmation. A missing bridge row fails
 * honestly with reason — never invented success.
 *
 * Structure follows the shared settings contract
 * (`.settings-page` > `.settings-section` > `.settings-group` > `.settings-row`)
 * composed with the uniform Open Viking shell (`SettingsPageIntro`) and the
 * sibling-view switcher; the layout owns the page title (see ROUTE_TITLES),
 * so this page renders no `h1`.
 */

import { useCallback, useState } from "react";
import { useSearchParams } from "react-router";
import { Database, FolderUp } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Input } from "@nous-research/ui/ui/components/input";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { cn } from "@/lib/utils";
import {
  SettingsEmpty,
  SettingsPageIntro,
  SettingsRow,
  SettingsSection,
} from "@/components/viking-settings";
import {
  addVikingMemory,
  addVikingResource,
  isBridgeRowMissing,
} from "@/lib/ocFamilyVikingApi";

type Tab = "resource" | "memory";

const TABS = [
  { key: "resource", label: "Add resource", icon: FolderUp },
  { key: "memory", label: "Add memory", icon: Database },
] as const;

/** A staged write kept in page memory after it settles. This is a real
 *  record of what this session sent — never a fabricated history. */
interface StagedWrite {
  kind: Tab;
  label: string;
  at: string;
}

export default function OpenVikingUploadsPage() {
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>(
    searchParams.get("tab") === "memory" ? "memory" : "resource",
  );
  const [uri, setUri] = useState("");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [notice, setNotice] = useState("");
  const [failed, setFailed] = useState("");
  const [busy, setBusy] = useState(false);
  const [staged, setStaged] = useState<StagedWrite[]>([]);
  const { toast, showToast } = useToast();

  // Staged confirm gate: the write is refused unless the operator approves
  // it explicitly. Uses the platform confirm primitive so the gate is a real
  // round-trip in the browser and stubbable in tests — never a silent send.
  const askConfirm = useCallback((description: string) => {
    return Promise.resolve(
      typeof window !== "undefined" && typeof window.confirm === "function"
        ? window.confirm(description)
        : false,
    );
  }, []);

  function recordStaged(kind: Tab, label: string) {
    setStaged((prev) => [{ kind, label, at: new Date().toLocaleTimeString() }, ...prev].slice(0, 20));
  }

  async function submitResource() {
    const value = uri.trim();
    if (!value || busy) return;
    setBusy(true);
    setNotice("");
    setFailed("");
    try {
      const input = { uri: value, title: title.trim() || undefined };
      const ok = await askConfirm(
        `Stage ${value} into the local context engine? Nothing is sent until you confirm.`,
      );
      if (!ok) return;
      await addVikingResource(input, () => true);
      setNotice("Resource staged and confirmed.");
      showToast("Resource staged and confirmed.", "success");
      recordStaged("resource", title.trim() || value);
      setUri("");
      setTitle("");
    } catch (e) {
      const message = isBridgeRowMissing(e)
        ? "Bridge row missing — resource ingest isn't served by the local engine yet."
        : e instanceof Error
          ? e.message
          : "resource add failed";
      setFailed(message);
      showToast(message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function submitMemory() {
    const value = text.trim();
    if (!value || busy) return;
    setBusy(true);
    setNotice("");
    setFailed("");
    try {
      const ok = await askConfirm(
        "Stage this note into the local context engine? Nothing is sent until you confirm.",
      );
      if (!ok) return;
      await addVikingMemory({ text: value }, () => true);
      setNotice("Memory staged and confirmed.");
      showToast("Memory staged and confirmed.", "success");
      recordStaged("memory", value.length > 60 ? `${value.slice(0, 57)}…` : value);
      setText("");
    } catch (e) {
      const message = isBridgeRowMissing(e)
        ? "Bridge row missing — memory writes aren't served by the local engine yet."
        : e instanceof Error
          ? e.message
          : "memory add failed";
      setFailed(message);
      showToast(message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="qb-upstream viking-settings settings-page settings-page--wide" data-testid="open-viking-uploads-page">
      <Toast toast={toast} />

      <SettingsPageIntro currentPath="/open-viking/uploads" showSubnav>
        Stage a resource or a memory into the local context engine. Every write
        asks for confirmation first — nothing sends silently.
      </SettingsPageIntro>

      <SettingsSection
        title="Write mode"
        description="Pick what this session is staging. Both writes are confirm-gated."
        ariaLabel="Upload type"
      >
        <div className="viking-subnav" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={tab === t.key}
              onClick={() => setTab(t.key)}
              className={cn("btn btn--sm", tab === t.key && "active")}
            >
              <t.icon className="h-4 w-4" aria-hidden /> {t.label}
            </button>
          ))}
        </div>
      </SettingsSection>

      {notice && (
        <p className="callout" role="status">
          {notice}
        </p>
      )}
      {failed && (
        <p className="callout danger" role="alert">
          {failed}
        </p>
      )}

      <SettingsSection
        title={tab === "resource" ? "Add resource" : "Add memory"}
        description={
          tab === "resource"
            ? "Ingest a document or URI into the context store. Confirm-gated; nothing is written until you approve."
            : "Append a note to the user memory. Confirm-gated; nothing is written until you approve."
        }
        ariaLabel={tab === "resource" ? "Add resource" : "Add memory"}
      >
        {tab === "resource" ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void submitResource();
            }}
          >
            <SettingsRow
              stacked
              title="Resource URI"
              description="A viking:// path or an https:// link to ingest."
              control={
                <Input
                  id="viking-upload-uri"
                  className="font-courier"
                  placeholder="viking://… or https://…"
                  value={uri}
                  onChange={(e) => setUri(e.target.value)}
                  aria-label="Resource URI"
                />
              }
            />
            <SettingsRow
              stacked
              title="Title"
              description="Optional label shown in the context tree."
              control={
                <Input
                  id="viking-upload-title"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  aria-label="Resource title"
                />
              }
            />
            <SettingsRow
              className="settings-row--actions"
              control={
                <Button
                  type="submit"
                  size="sm"
                  className="uppercase"
                  disabled={busy}
                  prefix={busy ? <Spinner /> : <FolderUp className="h-4 w-4" />}
                >
                  {busy ? "Staging…" : "Add resource"}
                </Button>
              }
            />
          </form>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void submitMemory();
            }}
          >
            <SettingsRow
              stacked
              title="Memory text"
              description="A single fact or preference worth remembering."
              control={
                <textarea
                  id="viking-memory-text"
                  className="min-h-28 w-full border border-midground/15 bg-background/40 px-3 py-2 font-courier text-sm transition-colors placeholder:text-midground/50 focus-visible:border-midground/25 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground/30 disabled:cursor-not-allowed disabled:opacity-50"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  aria-label="Memory text"
                />
              }
            />
            <SettingsRow
              className="settings-row--actions"
              control={
                <Button
                  type="submit"
                  size="sm"
                  className="uppercase"
                  disabled={busy}
                  prefix={busy ? <Spinner /> : <Database className="h-4 w-4" />}
                >
                  {busy ? "Staging…" : "Add memory"}
                </Button>
              }
            />
          </form>
        )}
      </SettingsSection>

      <SettingsSection
        title="Staged this session"
        description="Writes this page confirmed into the local engine, newest first. Cleared on reload."
        count={staged.length}
        ariaLabel="Staged this session"
      >
        {staged.length === 0 ? (
          <SettingsEmpty>
            Nothing staged yet — a failed write or a declined confirm never
            appears here.
          </SettingsEmpty>
        ) : (
          <div data-testid="viking-staged">
            {staged.map((row, i) => (
              <SettingsRow
                key={`${row.kind}-${row.at}-${i}`}
                title={row.label}
                description={
                  <span className="flex flex-wrap items-center gap-2">
                    <span>{row.kind === "resource" ? "resource" : "memory"}</span>
                    <span className="settings-row__value settings-row__value--mono">{row.at}</span>
                  </span>
                }
              />
            ))}
          </div>
        )}
      </SettingsSection>

      <SettingsSection
        title="Write flow"
        description="How a staged write reaches the local engine."
      >
        <SettingsRow
          title="1 · Stage"
          description="Enter the URI or note; the form holds your input locally."
        />
        <SettingsRow
          title="2 · Confirm"
          description="A prompt asks for explicit approval before anything is sent."
        />
        <SettingsRow
          title="3 · Write"
          description="On confirm, the bridge row runs the write against the state-home context store."
        />
      </SettingsSection>
    </div>
  );
}
