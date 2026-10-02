/**
 * Open Viking uploads (`/open-viking/uploads`).
 *
 * Qubit-owned add-resource surface over the local context engine:
 * resource ingest and memory writes are staged and confirm-gated
 * through `@/lib/ocFamilyVikingApi` (`resource_add`, `memory_add`).
 * Nothing is sent without confirmation. A missing bridge row fails
 * honestly with reason — never invented success.
 *
 * Visual language matches SessionsPage/ChannelsPage: the layout owns the
 * page title (see ROUTE_TITLES), so this page renders no `h1`; fields
 * use `Label` + `Input`, submits use `Button`, confirmation goes through
 * the shared `ConfirmDialog` round-trip (same resolver pattern as
 * ChannelsPage's broker confirm), and feedback uses `Toast`.
 */

import { useCallback, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { Database, FolderUp } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import {
  Card,
  CardContent,
} from "@nous-research/ui/ui/components/card";
import { ConfirmDialog } from "@nous-research/ui/ui/components/confirm-dialog";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { useToast } from "@nous-research/ui/hooks/use-toast";
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
  const { toast, showToast } = useToast();

  // ConfirmDialog round-trip (ChannelsPage broker-confirm pattern):
  // submit stages the payload, the dialog settles it, and only an
  // explicit confirm issues the bridge write.
  const [pending, setPending] = useState<{
    kind: Tab;
    title: string;
    description: string;
  } | null>(null);
  const confirmResolverRef = useRef<((ok: boolean) => void) | null>(null);

  const askConfirm = useCallback(
    (kind: Tab, title: string, description: string) =>
      new Promise<boolean>((resolve) => {
        confirmResolverRef.current = resolve;
        setPending({ kind, title, description });
      }),
    [],
  );

  const settleConfirm = useCallback((ok: boolean) => {
    const resolve = confirmResolverRef.current;
    confirmResolverRef.current = null;
    setPending(null);
    resolve?.(ok);
  }, []);

  async function submitResource() {
    const value = uri.trim();
    if (!value || busy) return;
    setBusy(true);
    setNotice("");
    setFailed("");
    try {
      const input = { uri: value, title: title.trim() || undefined };
      const ok = await askConfirm(
        "resource",
        "Add this resource?",
        `Stage ${value} into the VikingBot context. Nothing is sent until you confirm here.`,
      );
      if (!ok) return;
      await addVikingResource(input, () => true);
      setNotice("Resource staged and confirmed.");
      showToast("Resource staged and confirmed.", "success");
      setUri("");
      setTitle("");
    } catch (e) {
      const message = isBridgeRowMissing(e)
        ? "Bridge row missing — the J1 lane has not landed resource-add yet."
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
        "memory",
        "Add this memory?",
        "Stage this note into the VikingBot context. Nothing is sent until you confirm here.",
      );
      if (!ok) return;
      await addVikingMemory({ text: value }, () => true);
      setNotice("Memory staged and confirmed.");
      showToast("Memory staged and confirmed.", "success");
      setText("");
    } catch (e) {
      const message = isBridgeRowMissing(e)
        ? "Bridge row missing — the J1 lane has not landed memory-add yet."
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
    <div className="flex flex-col gap-6" data-testid="open-viking-uploads-page">
      <Toast toast={toast} />
      <p className="text-xs text-muted-foreground">
        Stage a resource or a memory into the local context engine. Every write
        asks for confirmation first — nothing sends silently.
      </p>

      <div className="flex gap-1" role="tablist" aria-label="Upload type">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center gap-2 px-3 py-1.5 text-xs uppercase tracking-[0.12em] ${
              tab === t.key
                ? "bg-midground text-background"
                : "text-muted-foreground hover:bg-midground/10"
            }`}
          >
            <t.icon className="h-4 w-4" aria-hidden /> {t.label}
          </button>
        ))}
      </div>

      {notice && (
        <p
          className="border border-success/30 bg-success/15 px-3 py-2 text-sm text-success"
          role="status"
        >
          {notice}
        </p>
      )}
      {failed && (
        <p
          className="border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          role="alert"
        >
          {failed}
        </p>
      )}

      {tab === "resource" ? (
        <Card className="max-w-xl">
          <CardContent className="flex flex-col gap-3 p-4">
            <form
              className="flex flex-col gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                void submitResource();
              }}
            >
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="viking-upload-uri">Resource URI</Label>
                <Input
                  id="viking-upload-uri"
                  className="font-courier"
                  placeholder="viking://… or https://…"
                  value={uri}
                  onChange={(e) => setUri(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="viking-upload-title">Title (optional)</Label>
                <Input
                  id="viking-upload-title"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </div>
              <Button
                type="submit"
                size="sm"
                className="w-fit uppercase"
                disabled={busy}
                prefix={busy ? <Spinner /> : <FolderUp className="h-4 w-4" />}
              >
                {busy ? "Staging…" : "Add resource"}
              </Button>
            </form>
          </CardContent>
        </Card>
      ) : (
        <Card className="max-w-xl">
          <CardContent className="flex flex-col gap-3 p-4">
            <form
              className="flex flex-col gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                void submitMemory();
              }}
            >
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="viking-memory-text">Memory text</Label>
                <textarea
                  id="viking-memory-text"
                  className="min-h-28 w-full border border-midground/15 bg-background/40 px-3 py-2 font-courier text-sm transition-colors placeholder:text-midground/50 focus-visible:border-midground/25 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground/30 disabled:cursor-not-allowed disabled:opacity-50"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                />
              </div>
              <Button
                type="submit"
                size="sm"
                className="w-fit uppercase"
                disabled={busy}
                prefix={busy ? <Spinner /> : <Database className="h-4 w-4" />}
              >
                {busy ? "Staging…" : "Add memory"}
              </Button>
            </form>
          </CardContent>
        </Card>
      )}

      <ConfirmDialog
        open={pending !== null}
        onCancel={() => settleConfirm(false)}
        onConfirm={() => settleConfirm(true)}
        title={pending?.title ?? "Confirm write"}
        description={
          pending?.description ??
          "This write requires explicit confirmation."
        }
        confirmLabel="Confirm"
      />
    </div>
  );
}
