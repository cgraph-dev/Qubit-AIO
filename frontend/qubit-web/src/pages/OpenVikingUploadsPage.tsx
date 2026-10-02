/**
 * Open Viking uploads (`/open-viking/uploads`).
 *
 * Qubit-owned add-resource surface over the local context engine:
 * resource ingest and memory writes are staged and confirm-gated
 * through `@/lib/ocFamilyVikingApi` (`resource_add`, `memory_add`).
 * Nothing is sent without confirmation. A missing bridge row fails
 * honestly with reason — never invented success.
 */

import { useState } from "react";
import { Link } from "react-router";
import { ArrowLeft, Database, FolderUp } from "lucide-react";
import {
  addVikingMemory,
  addVikingResource,
  isBridgeRowMissing,
} from "@/lib/ocFamilyVikingApi";

type Tab = "resource" | "memory";

export default function OpenVikingUploadsPage() {
  const [tab, setTab] = useState<Tab>("resource");
  const [uri, setUri] = useState("");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [notice, setNotice] = useState("");
  const [failed, setFailed] = useState("");
  const [busy, setBusy] = useState(false);

  async function submitResource() {
    const value = uri.trim();
    if (!value || busy) return;
    setBusy(true);
    setNotice("");
    setFailed("");
    try {
      await addVikingResource(
        { uri: value, title: title.trim() || undefined },
        () => window.confirm(`Add this resource to the VikingBot context?\n${value}`),
      );
      setNotice("Resource staged and confirmed.");
      setUri("");
      setTitle("");
    } catch (e) {
      if (isBridgeRowMissing(e)) {
        setFailed("Bridge row missing — the J1 lane has not landed resource-add yet.");
      } else {
        setFailed(e instanceof Error ? e.message : "resource add failed");
      }
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
      await addVikingMemory({ text: value }, () =>
        window.confirm("Add this memory to the VikingBot context?"),
      );
      setNotice("Memory staged and confirmed.");
      setText("");
    } catch (e) {
      if (isBridgeRowMissing(e)) {
        setFailed("Bridge row missing — the J1 lane has not landed memory-add yet.");
      } else {
        setFailed(e instanceof Error ? e.message : "memory add failed");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4 p-4" data-testid="open-viking-uploads-page">
      <header className="flex items-center gap-3">
        <Link to="/open-viking" className="flex items-center gap-1 text-sm hover:underline">
          <ArrowLeft className="h-4 w-4" aria-hidden /> Open Viking
        </Link>
        <h1 className="text-xl font-semibold">Uploads</h1>
      </header>

      <div className="flex gap-1" role="tablist" aria-label="Upload type">
        {(
          [
            { key: "resource", label: "Add resource", icon: FolderUp },
            { key: "memory", label: "Add memory", icon: Database },
          ] as const
        ).map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center gap-2 rounded px-3 py-1.5 text-sm ${
              tab === t.key ? "bg-primary text-primary-foreground" : "hover:bg-accent"
            }`}
          >
            <t.icon className="h-4 w-4" aria-hidden /> {t.label}
          </button>
        ))}
      </div>

      {notice && (
        <p className="rounded-md border border-green-500/40 bg-green-500/10 p-3 text-sm" role="status">
          {notice}
        </p>
      )}
      {failed && (
        <p className="rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm" role="alert">
          {failed}
        </p>
      )}

      {tab === "resource" ? (
        <form
          className="flex max-w-xl flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void submitResource();
          }}
        >
          <label className="text-sm font-medium" htmlFor="viking-upload-uri">
            Resource URI
          </label>
          <input
            id="viking-upload-uri"
            className="rounded border px-3 py-2 text-sm"
            placeholder="viking://… or https://…"
            value={uri}
            onChange={(e) => setUri(e.target.value)}
          />
          <label className="text-sm font-medium" htmlFor="viking-upload-title">
            Title (optional)
          </label>
          <input
            id="viking-upload-title"
            className="rounded border px-3 py-2 text-sm"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <button
            type="submit"
            disabled={busy}
            className="w-fit rounded bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-50"
          >
            {busy ? "Staging…" : "Add resource (confirm-gated)"}
          </button>
        </form>
      ) : (
        <form
          className="flex max-w-xl flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void submitMemory();
          }}
        >
          <label className="text-sm font-medium" htmlFor="viking-memory-text">
            Memory text
          </label>
          <textarea
            id="viking-memory-text"
            className="min-h-28 rounded border px-3 py-2 text-sm"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <button
            type="submit"
            disabled={busy}
            className="w-fit rounded bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-50"
          >
            {busy ? "Staging…" : "Add memory (confirm-gated)"}
          </button>
        </form>
      )}
    </div>
  );
}
