/**
 * Kitchen (`/kitchen`).
 *
 * The intake surface for absorbing other agents/apps into Qubit
 * (`docs/IDENTITY.md`): an operator pastes a repo or points at a folder, and the
 * Kitchen decomposes the source, maps its features/capabilities, and drafts a
 * 1:1 plan for integrating them into Qubit — how people upgrade Qubit from
 * other people's work.
 *
 * WIRED (stages 1-2). "Analyze" calls `POST /api/qubit/kitchen/analyze`, whose
 * backend is `voicebridge/kitchen.py`. Stage 1 (decompose) returns a real file
 * inventory + entry points; stage 2 (map capabilities) runs REA — the
 * reverse-engineering engine, used ONLY here — over the source. Analysis is
 * local: a pasted repo is shallow-cloned to a confined runtime dir and removed
 * afterwards; nothing is uploaded.
 *
 * Stages 3 (draft a 1:1 plan) and 4 (absorb) are NOT implemented. The backend
 * says so explicitly and the UI renders that as a stated gap, never a dead
 * button or a fake result.
 */

import { useCallback, useMemo, useRef, useState } from "react";
import {
  Boxes,
  ChefHat,
  FileCode2,
  FolderUp,
  GitBranch,
  ListChecks,
  Loader2,
  Sparkles,
  Trash2,
} from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Input } from "@nous-research/ui/ui/components/input";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { cn } from "@/lib/utils";
import { fetchJSON } from "@/lib/api";

type Mode = "repo" | "folder";

/** One inventory row from the decompose stage. */
interface ExtCount {
  ext: string;
  count: number;
}
interface EntryPoint {
  path: string;
  kind: string;
}
interface KitchenResult {
  ok: true;
  mode: Mode;
  source: string;
  root: string;
  cloned: boolean;
  stage1_decompose: {
    inventory: {
      file_count: number;
      total_bytes: number;
      by_extension: ExtCount[];
      sample_files: string[];
    };
    entry_points: EntryPoint[];
  };
  stage2_map_capabilities: {
    rea_javascript: { ok?: boolean; skipped?: string; error?: string };
    rea_environment: {
      ok?: boolean;
      available?: string[];
      unavailable?: { operation: string; code?: string }[];
    };
    note?: string;
  };
  stage3_plan: { implemented: boolean; detail: string };
  stage4_absorb: { implemented: boolean; detail: string };
}

function humanBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** A source staged in this session. UI-only: never leaves the page. */
interface StagedSource {
  id: string;
  kind: Mode;
  name: string;
  source: string;
  fileCount: number;
  at: string;
}

const MODES: { key: Mode; label: string; icon: typeof GitBranch }[] = [
  { key: "repo", label: "Paste a repo", icon: GitBranch },
  { key: "folder", label: "Upload a folder", icon: FolderUp },
];

/** The intended pipeline, shown so the page states its own shape. */
const PIPELINE = [
  {
    icon: Boxes,
    title: "1 · Decompose",
    body: "Read the source and split it into modules, services, and entry points.",
  },
  {
    icon: FileCode2,
    title: "2 · Map capabilities",
    body: "List every feature the source offers and where it lives in the code.",
  },
  {
    icon: ListChecks,
    title: "3 · Draft a 1:1 plan",
    body: "Produce a file-by-file integration plan against Qubit's own source.",
  },
  {
    icon: Sparkles,
    title: "4 · Absorb",
    body: "Apply the plan so the capability becomes Qubit's own — rebranded, verified.",
  },
];

function id() {
  return Math.random().toString(36).slice(2, 10);
}

/** Recursively collect files from a drag-drop, walking directory entries. */
async function collectDroppedFiles(items: DataTransferItemList): Promise<File[]> {
  const out: File[] = [];
  const walk = async (entry: FileSystemEntry): Promise<void> => {
    if (entry.isFile) {
      const file = await new Promise<File | null>((resolve) =>
        (entry as FileSystemFileEntry).file(resolve, () => resolve(null)),
      );
      if (file) out.push(file);
      return;
    }
    if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      const entries = await new Promise<FileSystemEntry[]>((resolve) =>
        reader.readEntries(resolve, () => resolve([])),
      );
      for (const child of entries) await walk(child);
    }
  };
  const roots: FileSystemEntry[] = [];
  for (const item of Array.from(items)) {
    const entry = item.webkitGetAsEntry?.();
    if (entry) roots.push(entry);
  }
  for (const root of roots) await walk(root);
  return out;
}

export default function KitchenPage() {
  const [mode, setMode] = useState<Mode>("repo");
  const [repoUrl, setRepoUrl] = useState("");
  const [repoName, setRepoName] = useState("");
  const [folderFiles, setFolderFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [staged, setStaged] = useState<StagedSource[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  // Analysis state (stages 1-2). Real backend result, real errors.
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<KitchenResult | null>(null);
  const dirInputRef = useRef<HTMLInputElement>(null);
  const { toast, showToast } = useToast();

  const folderName = useMemo(() => {
    const first = folderFiles[0] as (File & { webkitRelativePath?: string }) | undefined;
    const rel = first?.webkitRelativePath ?? "";
    return rel.includes("/") ? rel.split("/")[0] : "uploaded folder";
  }, [folderFiles]);

  const record = useCallback(
    (entry: Omit<StagedSource, "id" | "at">) => {
      const item: StagedSource = {
        ...entry,
        id: id(),
        at: new Date().toLocaleTimeString(),
      };
      setStaged((prev) => [item, ...prev].slice(0, 20));
      setSelected(item.id);
    },
    [],
  );

  function stageRepo() {
    const value = repoUrl.trim();
    if (!value) return;
    const name =
      repoName.trim() || value.replace(/\.git$/, "").split("/").filter(Boolean).pop() || value;
    record({ kind: "repo", name, source: value, fileCount: 0 });
    showToast("Repo staged for decomposition.", "success");
    setRepoUrl("");
    setRepoName("");
  }

  function stageFolder() {
    if (folderFiles.length === 0) return;
    record({
      kind: "folder",
      name: folderName,
      source: folderName,
      fileCount: folderFiles.length,
    });
    showToast(`Folder staged — ${folderFiles.length} files.`, "success");
    setFolderFiles([]);
    if (dirInputRef.current) dirInputRef.current.value = "";
  }

  /** Stages 1-2: call the backend and hold its real result. */
  async function analyze(s: StagedSource) {
    setSelected(s.id);
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await fetchJSON<KitchenResult>("/api/qubit/kitchen/analyze", {
        method: "POST",
        body: JSON.stringify({ mode: s.kind, source: s.source }),
      });
      setResult(r);
      showToast(`Decomposed ${r.stage1_decompose.inventory.file_count} files.`, "success");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  async function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const files = await collectDroppedFiles(e.dataTransfer.items);
    if (files.length) {
      setMode("folder");
      setFolderFiles(files);
    }
  }

  const active = staged.find((s) => s.id === selected) ?? null;

  return (
    <div className="qb-upstream kitchen-page settings-page settings-page--wide" data-testid="kitchen-page">
      <Toast toast={toast} />

      <header className="kitchen-intro">
        <div className="flex items-center gap-3">
          <ChefHat className="h-6 w-6 text-midground" aria-hidden />
          <h1 className="text-lg font-bold tracking-[0.04em] text-midground">Kitchen</h1>
        </div>
        <p className="mt-2 max-w-3xl text-sm text-text-secondary">
          Bring in another agent or app and turn it into Qubit. Paste a repo or
          upload a folder — the Kitchen decomposes the source, maps every feature
          it offers, and drafts a 1:1 plan for absorbing those capabilities into
          Qubit. This is how Qubit is upgraded from other people's work.
        </p>
        <p className="kitchen-note mt-3" role="note">
          Stages 1-2 run locally: <strong>Analyze</strong> decomposes the source
          and maps its capabilities through REA, the reverse-engineering engine.
          Stages 3 (draft a 1:1 plan) and 4 (absorb) are not implemented yet, and
          are shown as such below.
        </p>
      </header>

      <section className="kitchen-panel" aria-label="Add a source">
        <div className="kitchen-modes" role="tablist">
          {MODES.map((m) => (
            <button
              key={m.key}
              type="button"
              role="tab"
              aria-selected={mode === m.key}
              onClick={() => setMode(m.key)}
              className={cn("kitchen-mode", mode === m.key && "is-active")}
            >
              <m.icon className="h-4 w-4" aria-hidden /> {m.label}
            </button>
          ))}
        </div>

        {mode === "repo" ? (
          <form
            className="kitchen-form"
            onSubmit={(e) => {
              e.preventDefault();
              stageRepo();
            }}
          >
            <label className="kitchen-field">
              <span className="kitchen-field__label">Repository URL</span>
              <Input
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                placeholder="https://github.com/owner/repo"
                aria-label="Repository URL"
                className="font-courier"
              />
            </label>
            <label className="kitchen-field">
              <span className="kitchen-field__label">Name (optional)</span>
              <Input
                value={repoName}
                onChange={(e) => setRepoName(e.target.value)}
                placeholder="How it should appear in the Kitchen"
                aria-label="Source name"
              />
            </label>
            <div className="kitchen-actions">
              <Button type="submit" size="sm" className="uppercase" disabled={!repoUrl.trim()}>
                <GitBranch className="h-4 w-4" /> Stage repo
              </Button>
            </div>
          </form>
        ) : (
          <div className="kitchen-form">
            <div
              className={cn("kitchen-drop", dragging && "is-dragging")}
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
            >
              <FolderUp className="h-7 w-7 text-midground" aria-hidden />
              <p className="mt-2 text-sm text-text-secondary">
                Drop a folder here, or pick one.
              </p>
              <input
                ref={dirInputRef}
                type="file"
                multiple
                className="hidden"
                aria-label="Choose a folder"
                {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
                onChange={(e) => setFolderFiles(Array.from(e.target.files ?? []))}
              />
              <div className="mt-3">
                <Button
                  type="button"
                  size="sm"
                  className="uppercase"
                  onClick={() => dirInputRef.current?.click()}
                >
                  <FolderUp className="h-4 w-4" /> Choose folder
                </Button>
              </div>
              {folderFiles.length > 0 && (
                <p className="kitchen-drop__count" data-testid="kitchen-folder-count">
                  {folderName} — {folderFiles.length} files
                </p>
              )}
            </div>
            <div className="kitchen-actions">
              <Button
                type="button"
                size="sm"
                className="uppercase"
                disabled={folderFiles.length === 0}
                onClick={stageFolder}
              >
                <Sparkles className="h-4 w-4" /> Stage folder
              </Button>
            </div>
          </div>
        )}
      </section>

      <section className="kitchen-panel" aria-label="Staged sources">
        <div className="kitchen-panel__head">
          <h2 className="kitchen-panel__title">Staged sources</h2>
          <span className="kitchen-count">{staged.length}</span>
        </div>
        {staged.length === 0 ? (
          <p className="kitchen-empty">
            Nothing staged yet. Paste a repo or upload a folder to begin.
          </p>
        ) : (
          <ul className="kitchen-list" data-testid="kitchen-staged">
            {staged.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  className={cn("kitchen-item", s.id === selected && "is-selected")}
                  onClick={() => setSelected(s.id)}
                >
                  {s.kind === "repo" ? (
                    <GitBranch className="h-4 w-4 shrink-0" aria-hidden />
                  ) : (
                    <FolderUp className="h-4 w-4 shrink-0" aria-hidden />
                  )}
                  <span className="kitchen-item__name">{s.name}</span>
                  <span className="kitchen-item__meta">
                    {s.kind === "repo" ? "repo" : `${s.fileCount} files`} · {s.at}
                  </span>
                </button>
                <button
                  type="button"
                  className="kitchen-item__analyze"
                  aria-label={`Analyze ${s.name}`}
                  disabled={busy}
                  onClick={() => void analyze(s)}
                >
                  {busy && s.id === selected ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  ) : (
                    <Boxes className="h-3.5 w-3.5" aria-hidden />
                  )}
                  Analyze
                </button>
                <button
                  type="button"
                  className="kitchen-item__remove"
                  aria-label={`Remove ${s.name}`}
                  onClick={() =>
                    setStaged((prev) => prev.filter((x) => x.id !== s.id))
                  }
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="kitchen-panel" aria-label="Integration plan">
        <div className="kitchen-panel__head">
          <h2 className="kitchen-panel__title">
            Integration plan{active ? ` — ${active.name}` : ""}
          </h2>
        </div>
        {active ? (
          <>
            {error && (
              <p className="kitchen-error" role="alert">
                Analysis failed: {error}
              </p>
            )}
            {result && (
              <>
                <p className="kitchen-empty">
                  Decomposed <strong>{result.stage1_decompose.inventory.file_count}</strong> files
                  ({humanBytes(result.stage1_decompose.inventory.total_bytes)})
                  {result.cloned ? " — cloned to a confined temp dir, removed after analysis" : ""}.
                </p>
                <table className="kitchen-table" data-testid="kitchen-inventory">
                  <thead>
                    <tr>
                      <th scope="col">Extension</th>
                      <th scope="col">Files</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.stage1_decompose.inventory.by_extension.map((e) => (
                      <tr key={e.ext}>
                        <td className="font-courier">{e.ext}</td>
                        <td>{e.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {result.stage1_decompose.entry_points.length > 0 && (
                  <ul className="kitchen-list" data-testid="kitchen-entry-points">
                    {result.stage1_decompose.entry_points.slice(0, 8).map((e) => (
                      <li key={e.path}>
                        <span className="font-courier">{e.path}</span>
                        <span className="kitchen-item__meta">{e.kind}</span>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="kitchen-note" role="note">
                  {result.stage2_map_capabilities.rea_javascript.ok
                    ? "REA mapped the JavaScript/Electron surface."
                    : `REA JavaScript analysis: ${
                        result.stage2_map_capabilities.rea_javascript.skipped ??
                        result.stage2_map_capabilities.rea_javascript.error ??
                        "not run"
                      }`}
                  {typeof result.stage2_map_capabilities.rea_environment.available?.length === "number"
                    ? ` Engine: ${result.stage2_map_capabilities.rea_environment.available.length} operations available on this host.`
                    : ""}
                </p>
              </>
            )}
            <ol className="kitchen-pipeline" data-testid="kitchen-pipeline">
              {PIPELINE.map((step) => {
                // Stages 1-2 are live; 3-4 are stated gaps, not fake steps.
                const live = step.title.startsWith("1") || step.title.startsWith("2");
                return (
                  <li key={step.title} className="kitchen-step">
                    <step.icon className="h-4 w-4 shrink-0 text-midground" aria-hidden />
                    <div>
                      <p className="kitchen-step__title">
                        {step.title}
                        <span className="kitchen-item__meta">
                          {live ? (result ? " · done" : " · ready") : " · not implemented"}
                        </span>
                      </p>
                      <p className="kitchen-step__body">{step.body}</p>
                    </div>
                  </li>
                );
              })}
            </ol>
          </>
        ) : (
          <p className="kitchen-empty">
            Stage a source, then press <strong>Analyze</strong> to decompose it and
            map its capabilities. Stages 3-4 remain unimplemented.
          </p>
        )}
      </section>
    </div>
  );
}
