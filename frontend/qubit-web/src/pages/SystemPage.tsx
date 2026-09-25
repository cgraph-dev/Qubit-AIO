import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import {
  Activity,
  Brain,
  Check,
  Clock,
  Copy,
  Cpu,
  Database,
  Download,
  Globe,
  HardDrive,
  KeyRound,
  Link2,
  Play,
  Plus,
  Power,
  RotateCw,
  Server,
  Share2,
  ShieldCheck,
  Sparkles,
  Stethoscope,
  Terminal,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Checkbox } from "@nous-research/ui/ui/components/checkbox";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { Select, SelectOption } from "@nous-research/ui/ui/components/select";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { useConfirmDelete } from "@nous-research/ui/hooks/use-confirm-delete";
import { ConfirmDialog } from "@nous-research/ui/ui/components/confirm-dialog";
import { useModalBehavior } from "@/hooks/useModalBehavior";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import { QubitConsoleModal } from "@/components/QubitConsoleModal";
import { PageErrorBoundary } from "@/components/PageErrorBoundary";
import { cn, themedBody } from "@/lib/utils";
import { api, ConfirmRequiredError } from "@/lib/api";
import { parsePolicyRefusal, policyRefusalMessage, refusalOrError } from "@/lib/policy-refusal";
import { copyTextToClipboard } from "@/lib/clipboard";
import type {
  StatusResponse,
  MemoryStatus,
  MemoryProviderInfo,
  CredentialPoolProvider,
  CheckpointsResponse,
  HooksResponse,
  HookEntry,
  SystemStats,
  UpdateCheckResponse,
  CuratorStatus,
  PortalStatus,
  DebugShareResponse,
  PowerStatusResponse,
  PowerAction,
} from "@/lib/api";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function formatDuration(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

type BackupImportTarget =
  | { kind: "upload"; file: File }
  | { kind: "path"; path: string };

function backupImportLabel(target: BackupImportTarget | null): string {
  if (!target) return "the archive";
  return target.kind === "upload" ? target.file.name : target.path;
}

function backupFileName(path: string | null): string {
  if (!path) return "No backup created yet";
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path;
}

/**
 * Live action-log viewer for the spawn-based admin actions (doctor, audit,
 * backup, import, skills update, checkpoints prune, gateway start/stop).
 * Polls /api/actions/<name>/status until the process exits.
 */
function ActionLogViewer({
  action,
  onClose,
  onComplete,
}: {
  action: string;
  onClose: () => void;
  onComplete?: (action: string, exitCode: number | null) => void;
}) {
  const [lines, setLines] = useState<string[]>([]);
  const [running, setRunning] = useState(true);
  const [exitCode, setExitCode] = useState<number | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const completeRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    completeRef.current = false;
    const poll = async () => {
      try {
        const st = await api.getActionStatus(action, 400);
        if (cancelled) return;
        setLines(st.lines ?? []);
        setRunning(st.running);
        setExitCode(st.exit_code);
        if (!st.running && !completeRef.current) {
          completeRef.current = true;
          onComplete?.(action, st.exit_code);
        }
        if (st.running) timer.current = setTimeout(poll, 1200);
      } catch {
        if (!cancelled) setRunning(false);
      }
    };
    poll();
    return () => {
      cancelled = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [action, onComplete]);

  return (
    <Card>
      <CardContent className="py-4">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Terminal className="h-4 w-4 text-muted-foreground" />
            <span className="font-mono text-sm">{action}</span>
            {running ? (
              <Badge tone="warning">running</Badge>
            ) : (
              <Badge tone={exitCode === 0 ? "success" : "destructive"}>
                {exitCode === 0 ? "done" : `exit ${exitCode}`}
              </Badge>
            )}
          </div>
          <Button ghost size="icon" onClick={onClose} aria-label="Close log">
            <X />
          </Button>
        </div>
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words bg-background/50 border border-border p-3 text-xs font-mono text-muted-foreground">
          {lines.length ? lines.join("\n") : "Starting…"}
        </pre>
      </CardContent>
    </Card>
  );
}

const HOOK_EVENTS_FALLBACK = [
  "pre_tool_call",
  "post_tool_call",
  "pre_llm_call",
  "post_llm_call",
  "on_session_start",
  "on_session_end",
];

const MEMORY_STATUS_LABEL: Record<MemoryProviderInfo["status"], string> = {
  ready: "ready",
  needs_config: "needs setup",
  unavailable: "unavailable",
  missing: "missing",
};

const MEMORY_STATUS_TONE: Record<
  MemoryProviderInfo["status"],
  "success" | "warning" | "destructive" | "secondary"
> = {
  ready: "success",
  needs_config: "warning",
  unavailable: "destructive",
  missing: "destructive",
};

export default function SystemPage() {
  const { toast, showToast } = useToast();

  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [memory, setMemory] = useState<MemoryStatus | null>(null);
  const [pool, setPool] = useState<CredentialPoolProvider[]>([]);
  const [poolRefusal, setPoolRefusal] = useState<string | null>(null);
  const [checkpoints, setCheckpoints] = useState<CheckpointsResponse | null>(
    null,
  );
  const [hooks, setHooks] = useState<HooksResponse | null>(null);
  const [curator, setCurator] = useState<CuratorStatus | null>(null);
  const [portal, setPortal] = useState<PortalStatus | null>(null);
  const [power, setPower] = useState<PowerStatusResponse | null>(null);
  const [powerBusy, setPowerBusy] = useState(false);
  const [confirmEStop, setConfirmEStop] = useState(false);
  // The global-disable latch is the same class of shared, cross-surface
  // control as the emergency stop, so it goes through the same confirm gate
  // (and audit). `enable` is the de-escalation and needs no confirmation.
  const [confirmGlobalDisable, setConfirmGlobalDisable] = useState(false);
  const [loading, setLoading] = useState(true);

  const [activeAction, setActiveAction] = useState<string | null>(null);
  const [consoleOpen, setConsoleOpen] = useState(false);

  // Add-credential form.
  const [credProvider, setCredProvider] = useState("openrouter");
  const [credKey, setCredKey] = useState("");
  const [credLabel, setCredLabel] = useState("");
  const [addingCred, setAddingCred] = useState(false);

  const [pendingBackupArchive, setPendingBackupArchive] = useState<string | null>(
    null,
  );
  const [downloadableBackupArchive, setDownloadableBackupArchive] = useState<
    string | null
  >(null);
  const [downloadingBackup, setDownloadingBackup] = useState(false);
  // B-08: portable backups are passphrase-encrypted; the operator supplies it
  // here and it is sent as a secret arg (never journaled or echoed back).
  const [backupPassphrase, setBackupPassphrase] = useState("");
  const importUploadInputRef = useRef<HTMLInputElement | null>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importPath, setImportPath] = useState("");
  // Restore-from-backup is destructive (overwrites the live config) and the
  // spawned `qubit import` runs non-interactively (stdin is /dev/null), so
  // its CLI "Continue? [y/N]" prompt would auto-abort. The dashboard owns the
  // consent: confirm here, then call the endpoint with force=true.
  const [importingBackup, setImportingBackup] = useState(false);
  const [importConfirmTarget, setImportConfirmTarget] =
    useState<BackupImportTarget | null>(null);

  // Create-hook modal.
  const [hookModalOpen, setHookModalOpen] = useState(false);
  const closeHookModal = useCallback(() => setHookModalOpen(false), []);
  const hookModalRef = useModalBehavior({
    open: hookModalOpen,
    onClose: closeHookModal,
  });
  const [hookEvent, setHookEvent] = useState("pre_tool_call");
  const [hookCommand, setHookCommand] = useState("");
  const [hookMatcher, setHookMatcher] = useState("");
  const [hookTimeout, setHookTimeout] = useState("");
  const [hookApprove, setHookApprove] = useState(true);
  const [creatingHook, setCreatingHook] = useState(false);

  // ── Update check ───────────────────────────────────────────────────
  const [updateInfo, setUpdateInfo] = useState<UpdateCheckResponse | null>(
    null,
  );
  const [checkingUpdate, setCheckingUpdate] = useState(false);
  const [updateConfirmOpen, setUpdateConfirmOpen] = useState(false);
  // Expected typed policy refusal (e.g. UPDATE_OPERATOR_OWNED) for the
  // dashboard self-update path — rendered as honest guidance, never as a
  // raw `403: {…}` error string.
  const [updateRefusal, setUpdateRefusal] = useState<string | null>(null);
  // GATEWAY_NO_SUPERVISION explanation from GET /api/gateway: process control
  // is operator-owned, so the Start/Restart/Stop controls render meaningfully
  // disabled instead of failing on click.
  const [gatewayRefusal, setGatewayRefusal] = useState<string | null>(null);

  const loadAll = useCallback(() => {
    Promise.allSettled([
      api.getStatus(),
      api.getSystemStats(),
      api.getMemory(),
      api.getCredentialPool(),
      api.getCheckpoints(),
      api.getHooks(),
      api.getCurator(),
      api.getPortal(),
      // Cached (non-forced) check so the version row shows update status on
      // load without a separate effect / a forced network round-trip.
      api.checkQubitUpdate(false),
      // Supervision policy: Qubit refuses in-dashboard process control.
      api.getGatewayStatus(),
      // The shared emergency-stop / global-disable latch (one path for
      // CLI/voice/dashboard).
      api.getPowerStatus(),
    ])
      .then(([s, st, m, p, c, h, cur, prt, upd, gw, pw]) => {
        if (s.status === "fulfilled") setStatus(s.value);
        if (st.status === "fulfilled") setStats(st.value);
        if (m.status === "fulfilled") setMemory(m.value);
        if (p.status === "fulfilled") {
          setPool(p.value.providers);
          setPoolRefusal(null);
        } else {
          // A gated read must not silently render as an empty pool.
          const refusal = parsePolicyRefusal(p.reason);
          setPoolRefusal(refusal ? policyRefusalMessage(refusal) : null);
        }
        if (c.status === "fulfilled") setCheckpoints(c.value);
        if (h.status === "fulfilled") setHooks(h.value);
        if (cur.status === "fulfilled") setCurator(cur.value);
        if (prt.status === "fulfilled") setPortal(prt.value);
        if (upd.status === "fulfilled") setUpdateInfo(upd.value);
        if (gw.status === "fulfilled") {
          const supervision = gw.value?.supervision ?? "";
          setGatewayRefusal(
            // Any non-empty supervision string means process control is not
            // available on this host. Keep the legacy GATEWAY_NO_SUPERVISION
            // token and accept the real supervisor's GATEWAY_SUPERVISION_
            // UNAVAILABLE so Start/Restart render disabled instead of failing
            // on click.
            supervision &&
              (supervision.includes("GATEWAY_NO_SUPERVISION") ||
                supervision.includes("GATEWAY_SUPERVISION_UNAVAILABLE"))
              ? supervision
              : null,
          );
        }
        if (pw.status === "fulfilled") setPower(pw.value);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // ── Gateway lifecycle ──────────────────────────────────────────────
  // Every gateway transition (start/stop/restart) is confirm-gated by the
  // supervisor: the bare POST stages a typed 409 GATEWAY_CONFIRM_REQUIRED.
  // api.startGateway/stopGateway/restartGateway own the shared settle
  // round-trip; this in-app ConfirmDialog (not window.confirm) collects the
  // human decision through the deferred promise, matching ConfigPage.
  const gatewayDecisionRef = useRef<((ok: boolean) => void) | null>(null);
  const [gatewayConfirm, setGatewayConfirm] = useState<{
    verb: "start" | "stop" | "restart";
    detail: string;
  } | null>(null);

  const askGatewayConfirm = useCallback(
    (verb: "start" | "stop" | "restart", detail: string) =>
      new Promise<boolean>((resolve) => {
        gatewayDecisionRef.current = resolve;
        setGatewayConfirm({ verb, detail });
      }),
    [],
  );

  const decideGatewayConfirm = (ok: boolean) => {
    setGatewayConfirm(null);
    gatewayDecisionRef.current?.(ok);
    gatewayDecisionRef.current = null;
  };

  const runGateway = async (verb: "start" | "stop" | "restart") => {
    const confirm = (detail: string) => askGatewayConfirm(verb, detail);
    try {
      if (verb === "start") {
        await api.startGateway(confirm);
        setActiveAction("gateway-start");
      } else if (verb === "stop") {
        await api.stopGateway(confirm);
        setActiveAction("gateway-stop");
      } else {
        await api.restartGateway(confirm);
        setActiveAction("gateway-restart");
      }
      showToast(`Gateway ${verb} started`, "success");
      setTimeout(loadAll, 3000);
    } catch (e) {
      // A declined in-app confirmation rethrows the typed ConfirmRequiredError;
      // nothing was executed, so report it as cancelled — never as a failure.
      if (e instanceof ConfirmRequiredError) {
        showToast(`Gateway ${verb} cancelled — confirmation declined`, "error");
        return;
      }
      const refusal = parsePolicyRefusal(e);
      if (refusal) {
        const message = policyRefusalMessage(refusal);
        setGatewayRefusal(message);
        showToast(message, "success");
      } else {
        showToast(`Gateway ${verb} failed: ${e}`, "error");
      }
    } finally {
      decideGatewayConfirm(false);
    }
  };

  // ── Power / emergency stop ─────────────────────────────────────────
  // One shared latch (`/api/power`): the SPA, classic dashboard, CLI, and
  // voice all observe and set the same durable control state. Read the live
  // status after each action so the badge never shows a fabricated value.
  const runPowerAction = async (action: PowerAction, label: string) => {
    setPowerBusy(true);
    try {
      const res = await api.powerAction(action);
      if (res.state === "denied") {
        showToast(`${label} refused: ${res.detail ?? res.code ?? ""}`, "error");
      } else {
        showToast(`${label} ✓`, "success");
      }
      setPower(await api.getPowerStatus());
      loadAll();
    } catch (e) {
      showToast(`${label} failed: ${e}`, "error");
    } finally {
      setPowerBusy(false);
    }
  };

  const estopLatched = Boolean(power?.control?.emergency_stop);
  const globallyDisabled = Boolean(power?.control?.global_disable);

  // ── Curator ────────────────────────────────────────────────────────
  const toggleCuratorPaused = async () => {
    if (!curator) return;
    try {
      await api.setCuratorPaused(!curator.paused);
      showToast(curator.paused ? "Curator resumed" : "Curator paused", "success");
      loadAll();
    } catch (e) {
      showToast(refusalOrError(e, `Curator toggle failed: ${e}`), "error");
    }
  };

  // ── Memory ─────────────────────────────────────────────────────────
  // Memory provider selection lives on the /plugins page now (see the
  // read-only display + link below); the dropdown was intentionally
  // dropped from this card during the admin-panel refresh.
  const memoryReset = useConfirmDelete({
    onDelete: useCallback(
      async (target: string) => {
        try {
          const res = await api.resetMemory(
            target as "all" | "memory" | "user",
          );
          showToast(`Reset: ${res.deleted.join(", ") || "nothing"}`, "success");
          loadAll();
        } catch (e) {
          showToast(`Reset failed: ${e}`, "error");
          throw e;
        }
      },
      [loadAll, showToast],
    ),
  });

  // ── Credential pool ────────────────────────────────────────────────
  const addCredential = async () => {
    if (!credProvider.trim() || !credKey.trim()) {
      showToast("Provider and API key required", "error");
      return;
    }
    setAddingCred(true);
    try {
      await api.addCredentialPoolEntry(
        credProvider.trim(),
        credKey.trim(),
        credLabel.trim() || undefined,
      );
      showToast("Credential added", "success");
      setCredKey("");
      setCredLabel("");
      loadAll();
    } catch (e) {
      showToast(`Failed to add credential: ${e}`, "error");
    } finally {
      setAddingCred(false);
    }
  };

  const credDelete = useConfirmDelete({
    onDelete: useCallback(
      async (key: string) => {
        const [provider, idxStr] = key.split("|");
        try {
          await api.removeCredentialPoolEntry(provider, Number(idxStr));
          showToast("Credential removed", "success");
          loadAll();
        } catch (e) {
          showToast(`Failed to remove: ${e}`, "error");
          throw e;
        }
      },
      [loadAll, showToast],
    ),
  });

  // ── Operations ─────────────────────────────────────────────────────
  const runOp = async (fn: () => Promise<{ name: string }>, label: string) => {
    try {
      const res = await fn();
      setActiveAction(res.name);
      showToast(`${label} started`, "success");
    } catch (e) {
      showToast(`${label} failed: ${e}`, "error");
    }
  };

  const runDashboardBackup = async () => {
    // B-08: encrypted backup requires an operator passphrase; without it the
    // backend returns BAD_ARGS. Confirm only on a real returned archive path.
    if (!backupPassphrase.trim()) {
      showToast("A backup passphrase is required", "error");
      return;
    }
    try {
      const res = await api.runBackup(backupPassphrase);
      const path =
        typeof res.result?.path === "string"
          ? (res.result.path as string)
          : res.archive;
      if (!res.ok || !path) {
        showToast(res.detail || res.code || "Backup failed", "error");
        return;
      }
      setActiveAction(res.name);
      setPendingBackupArchive(path);
      setDownloadableBackupArchive(path);
      showToast("Backup ready to download", "success");
    } catch (e) {
      showToast(refusalOrError(e, `Backup failed: ${e}`), "error");
    }
  };

  const handleActionComplete = useCallback(
    (action: string, exitCode: number | null) => {
      if (action === "backup" && pendingBackupArchive) {
        if (exitCode === 0) {
          setDownloadableBackupArchive(pendingBackupArchive);
          showToast("Backup ready to download", "success");
        } else {
          setPendingBackupArchive(null);
        }
      }
    },
    [pendingBackupArchive, showToast],
  );

  const downloadBackup = async () => {
    const archive = downloadableBackupArchive;
    if (!archive) return;
    setDownloadingBackup(true);
    try {
      const res = await api.downloadBackup(archive);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = backupFileName(archive);
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      showToast(refusalOrError(e, `Download failed: ${e}`), "error");
    } finally {
      setDownloadingBackup(false);
    }
  };

  const clearImportFile = () => {
    setImportFile(null);
    if (importUploadInputRef.current) importUploadInputRef.current.value = "";
  };

  const runBackupImport = async (target: BackupImportTarget) => {
    // B-08: restore needs the same passphrase; without it the backend returns
    // BAD_ARGS, so require it before sending and confirm only on a real result.
    if (!backupPassphrase.trim()) {
      showToast("A backup passphrase is required", "error");
      return;
    }
    setImportingBackup(true);
    try {
      const res =
        target.kind === "upload"
          ? await api.runImportUpload(target.file, true)
          : await api.runImport(target.path, true, backupPassphrase);
      if (!res.ok) {
        showToast(res.detail || res.code || "Import failed", "error");
        return;
      }
      setActiveAction(res.name);
      showToast("Import complete", "success");
      if (target.kind === "upload") clearImportFile();
    } catch (e) {
      showToast(refusalOrError(e, `Import failed: ${e}`), "error");
    } finally {
      setImportingBackup(false);
    }
  };

  // ── Debug share ────────────────────────────────────────────────────
  // Unlike the fire-and-forget ops above, `debug share` produces shareable
  // paste URLs that are the whole point — so we surface them as real,
  // copyable links rather than a log tail.
  const [shareRedact, setShareRedact] = useState(true);
  const [sharing, setSharing] = useState(false);
  const [shareResult, setShareResult] = useState<DebugShareResponse | null>(
    null,
  );
  const [copiedLabel, setCopiedLabel] = useState<string | null>(null);

  const copyToClipboard = useCallback(
    async (text: string, label: string) => {
      if (await copyTextToClipboard(text)) {
        setCopiedLabel(label);
        setTimeout(
          () => setCopiedLabel((cur) => (cur === label ? null : cur)),
          1500,
        );
      } else {
        showToast("Couldn't copy to clipboard", "error");
      }
    },
    [showToast],
  );

  const runDebugShare = useCallback(async () => {
    setSharing(true);
    setShareResult(null);
    try {
      const res = await api.runDebugShare({ redact: shareRedact });
      setShareResult(res);
      const n = Object.keys(res.urls).length;
      showToast(
        `Uploaded ${n} paste${n === 1 ? "" : "s"}${
          res.redacted ? " (redacted)" : ""
        }`,
        "success",
      );
    } catch (e) {
      showToast(`Debug share failed: ${e}`, "error");
    } finally {
      setSharing(false);
    }
  }, [shareRedact, showToast]);


  // ── Update check / apply ───────────────────────────────────────────
  const checkForUpdate = useCallback(
    async (force = false) => {
      if (status?.can_update_qubit === false) return;
      setCheckingUpdate(true);
      try {
        const info = await api.checkQubitUpdate(force);
        setUpdateInfo(info);
        setUpdateRefusal(null);
        if (force) {
          if (info.update_available) {
            showToast(
              info.behind && info.behind > 0
                ? `Update available — ${info.behind} commit${info.behind === 1 ? "" : "s"} behind`
                : "Update available",
              "success",
            );
          } else if (info.behind === 0) {
            showToast("You're on the latest version", "success");
          } else if (info.message) {
            showToast(info.message, "error");
          }
        }
      } catch (e) {
        // The dashboard self-update is refused by policy in some installs.
        // That is an expected typed state: render the Qubit path, not a 403.
        const refusal = parsePolicyRefusal(e);
        if (refusal) {
          const message = policyRefusalMessage(refusal);
          setUpdateRefusal(message);
          if (force) showToast(message, "success");
        } else {
          showToast(`Update check failed: ${e}`, "error");
        }
      } finally {
        setCheckingUpdate(false);
      }
    },
    [showToast, status?.can_update_qubit],
  );

  // Auto-check (cached) runs inside loadAll on mount; this is the
  // user-triggered forced re-check from the "Check for updates" button.
  const applyUpdate = async () => {
    setUpdateConfirmOpen(false);
    if (status?.can_update_qubit === false) {
      showToast(
        "Qubit updates are managed outside this dashboard.",
        "success",
      );
      return;
    }
    try {
      const resp = await api.updateQubit();
      if (!resp.ok) {
        showToast(
          resp.message ??
            "Updates don't apply from this dashboard.",
          "success",
        );
        return;
      }
      setActiveAction(resp.name ?? "qubit-update");
      showToast("Update started", "success");
    } catch (e) {
      const refusal = parsePolicyRefusal(e);
      if (refusal) {
        const message = policyRefusalMessage(refusal);
        setUpdateRefusal(message);
        showToast(message, "success");
      } else {
        showToast(`Update failed: ${e}`, "error");
      }
    }
  };

  const checkpointsPrune = useConfirmDelete({
    onDelete: useCallback(async () => {
      try {
        const res = await api.pruneCheckpoints();
        setActiveAction(res.name);
        showToast("Checkpoint prune started", "success");
      } catch (e) {
        showToast(refusalOrError(e, `Prune failed: ${e}`), "error");
        throw e;
      }
    }, [showToast]),
  });

  // ── Hooks ──────────────────────────────────────────────────────────
  const createHook = async () => {
    if (!hookCommand.trim()) {
      showToast("Command is required", "error");
      return;
    }
    setCreatingHook(true);
    try {
      await api.createHook({
        event: hookEvent,
        command: hookCommand.trim(),
        matcher: hookMatcher.trim() || undefined,
        timeout: hookTimeout.trim() ? Number(hookTimeout) : undefined,
        approve: hookApprove,
      });
      showToast("Hook created", "success");
      setHookCommand("");
      setHookMatcher("");
      setHookTimeout("");
      setHookModalOpen(false);
      loadAll();
    } catch (e) {
      showToast(`Failed to create hook: ${e}`, "error");
    } finally {
      setCreatingHook(false);
    }
  };

  const hookDelete = useConfirmDelete({
    onDelete: useCallback(
      async (key: string) => {
        const sep = key.indexOf("|");
        const event = key.slice(0, sep);
        const command = key.slice(sep + 1);
        try {
          await api.deleteHook(event, command);
          showToast("Hook removed", "success");
          loadAll();
        } catch (e) {
          showToast(`Failed to remove hook: ${e}`, "error");
          throw e;
        }
      },
      [loadAll, showToast],
    ),
  });

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  const gatewayRunning = status?.gateway_running;
  const canUpdateQubit = status?.can_update_qubit !== false;
  const activeMemoryProvider = memory?.active
    ? memory.providers.find((provider) => provider.name === memory.active)
    : null;
  const validEvents = hooks?.valid_events?.length
    ? hooks.valid_events
    : HOOK_EVENTS_FALLBACK;

  return (
    <PageErrorBoundary label="System">
    <div className="flex flex-col gap-8">
      <Toast toast={toast} />
      <input
        ref={importUploadInputRef}
        type="file"
        accept=".zip,application/zip,application/x-zip-compressed"
        className="hidden"
        onChange={(event) => {
          setImportFile(event.currentTarget.files?.[0] ?? null);
        }}
      />

      <ConfirmDialog
        open={gatewayConfirm !== null}
        onCancel={() => decideGatewayConfirm(false)}
        onConfirm={() => decideGatewayConfirm(true)}
        title={`${gatewayConfirm?.verb === "stop" ? "Stop" : gatewayConfirm?.verb === "start" ? "Start" : "Restart"} the gateway?`}
        description={
          gatewayConfirm
            ? `${gatewayConfirm.detail} This changes the Qubit service unit and is recorded on the audit journal.`
            : ""
        }
        confirmLabel={
          gatewayConfirm?.verb === "stop"
            ? "Stop gateway"
            : gatewayConfirm?.verb === "start"
              ? "Start gateway"
              : "Restart gateway"
        }
        destructive={gatewayConfirm?.verb === "stop"}
      />

      <ConfirmDialog
        open={canUpdateQubit && updateConfirmOpen}
        onCancel={() => setUpdateConfirmOpen(false)}
        onConfirm={() => void applyUpdate()}
        title="Update Qubit?"
        description={
          updateInfo && updateInfo.behind && updateInfo.behind > 0
            ? `This will run the Qubit update (${updateInfo.update_command}) and pull ${updateInfo.behind} new commit${updateInfo.behind === 1 ? "" : "s"}. The gateway restarts when the update finishes; the current session keeps its prompt cache until then.`
            : `This will run the Qubit update (${updateInfo?.update_command ?? "qubit update"}) and restart the gateway when it finishes.`
        }
        confirmLabel="Update now"
      />

      <DeleteConfirmDialog
        open={memoryReset.isOpen}
        onCancel={memoryReset.cancel}
        onConfirm={memoryReset.confirm}
        title="Reset memory"
        description="This permanently erases the selected built-in memory files. This cannot be undone."
        loading={memoryReset.isDeleting}
      />
      <DeleteConfirmDialog
        open={credDelete.isOpen}
        onCancel={credDelete.cancel}
        onConfirm={credDelete.confirm}
        title="Remove credential"
        description="Remove this pooled API key? The agent will no longer rotate through it."
        loading={credDelete.isDeleting}
      />
      <DeleteConfirmDialog
        open={checkpointsPrune.isOpen}
        onCancel={checkpointsPrune.cancel}
        onConfirm={checkpointsPrune.confirm}
        title="Prune checkpoints"
        description="Delete the rollback checkpoint shadow store? Existing /rollback points will be lost."
        loading={checkpointsPrune.isDeleting}
      />
      <ConfirmDialog
        open={confirmEStop}
        onCancel={() => setConfirmEStop(false)}
        onConfirm={() => {
          setConfirmEStop(false);
          void runPowerAction("emergency-stop", "Emergency stop");
        }}
        title="Latch the emergency stop?"
        description="This immediately refuses new agent turns, sessions, and brokered actions across every surface until it is reset. It does not kill the backend process."
        confirmLabel="Latch emergency stop"
        destructive
      />
      <ConfirmDialog
        open={confirmGlobalDisable}
        onCancel={() => setConfirmGlobalDisable(false)}
        onConfirm={() => {
          setConfirmGlobalDisable(false);
          void runPowerAction("disable", "Global disable");
        }}
        title="Globally disable the agent?"
        description="This latches the shared global-disable control the CLI, voice gateway, and this dashboard read; new agent work is refused everywhere until it is re-enabled. It is recorded on the audit journal."
        confirmLabel="Globally disable"
        destructive
      />
      <DeleteConfirmDialog
        open={hookDelete.isOpen}
        onCancel={hookDelete.cancel}
        onConfirm={hookDelete.confirm}
        title="Remove shell hook"
        description="Remove this hook from config and revoke its consent? It stops firing on the next restart."
        loading={hookDelete.isDeleting}
      />
      <QubitConsoleModal
        open={consoleOpen}
        onClose={() => setConsoleOpen(false)}
      />

      {/* Create-hook modal */}
      {hookModalOpen && (
        <div
          ref={hookModalRef}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/85 p-4"
          onClick={(e) => e.target === e.currentTarget && setHookModalOpen(false)}
          role="dialog"
          aria-modal="true"
        >
          <div className={cn(themedBody, "relative w-full max-w-lg border border-border bg-card shadow-2xl flex flex-col")}>
            <Button
              ghost
              size="icon"
              onClick={() => setHookModalOpen(false)}
              className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
              aria-label="Close"
            >
              <X />
            </Button>
            <header className="p-5 pb-3 border-b border-border">
              <h2 className="font-mondwest text-display text-base tracking-wider">
                New shell hook
              </h2>
            </header>
            <div className="p-5 grid gap-4">
              <div className="grid gap-2">
                <Label htmlFor="hook-event">Event</Label>
                <Select
                  id="hook-event"
                  value={hookEvent}
                  onValueChange={(v) => setHookEvent(v)}
                >
                  {validEvents.map((ev) => (
                    <SelectOption key={ev} value={ev}>
                      {ev}
                    </SelectOption>
                  ))}
                </Select>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="hook-command">Command (absolute path)</Label>
                <Input
                  id="hook-command"
                  autoFocus
                  placeholder="/usr/local/bin/my-hook.sh"
                  value={hookCommand}
                  onChange={(e) => setHookCommand(e.target.value)}
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="grid gap-2">
                  <Label htmlFor="hook-matcher">Matcher (optional)</Label>
                  <Input
                    id="hook-matcher"
                    placeholder="e.g. terminal"
                    value={hookMatcher}
                    onChange={(e) => setHookMatcher(e.target.value)}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="hook-timeout">Timeout (s)</Label>
                  <Input
                    id="hook-timeout"
                    placeholder="10"
                    value={hookTimeout}
                    onChange={(e) => setHookTimeout(e.target.value)}
                  />
                </div>
              </div>
              <div className="flex items-center gap-2.5">
                <Checkbox
                  checked={hookApprove}
                  id="hook-approve"
                  onCheckedChange={(checked) => setHookApprove(checked === true)}
                />

                <Label
                  className="cursor-pointer text-sm font-normal normal-case tracking-normal text-muted-foreground"
                  htmlFor="hook-approve"
                >
                  Approve now (grant consent so it fires; otherwise it stays
                  configured but inactive)
                </Label>
              </div>
              <p className="text-xs text-warning">
                Shell hooks run arbitrary commands on this host. Only add scripts
                you trust. Takes effect on the next gateway/session restart.
              </p>
              <div className="flex justify-end">
                <Button
                  className="uppercase"
                  size="sm"
                  onClick={createHook}
                  disabled={creatingHook}
                  prefix={creatingHook ? <Spinner /> : undefined}
                >
                  {creatingHook ? "Creating" : "Create hook"}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Live action log */}
      {activeAction && (
        <ActionLogViewer
          action={activeAction}
          onComplete={handleActionComplete}
          onClose={() => setActiveAction(null)}
        />
      )}

      {/* ── Host / system stats ───────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <Server className="h-4 w-4" /> Host
        </H2>
        <Card>
          <CardContent className="py-4">
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-y-3 gap-x-6 text-sm">
              <div>
                <div className="text-xs uppercase tracking-wider text-muted-foreground">OS</div>
                <div>{stats?.os} {stats?.os_release}</div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wider text-muted-foreground">Arch</div>
                <div>{stats?.arch}</div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wider text-muted-foreground">Host</div>
                <div className="truncate">{stats?.hostname}</div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wider text-muted-foreground">Python</div>
                <div>{stats?.python_impl} {stats?.python_version}</div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wider text-muted-foreground">Qubit</div>
                <div className="flex items-center gap-2">
                  <span>v{stats?.qubit_version}</span>
                  {canUpdateQubit &&
                    updateInfo &&
                    (updateInfo.update_available ? (
                      <Badge tone="warning">
                        {updateInfo.behind && updateInfo.behind > 0
                          ? `${updateInfo.behind} behind`
                          : "update available"}
                      </Badge>
                    ) : updateInfo.behind === 0 ? (
                      <Badge tone="success">latest</Badge>
                    ) : null)}
                </div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wider text-muted-foreground flex items-center gap-1">
                  <Cpu className="h-3 w-3" /> CPU
                </div>
                <div>
                  {stats?.cpu_count ?? "—"} cores
                  {typeof stats?.cpu_percent === "number"
                    ? ` · ${stats.cpu_percent.toFixed(0)}%`
                    : ""}
                </div>
              </div>
              {stats?.memory && (
                <div>
                  <div className="text-xs uppercase tracking-wider text-muted-foreground">Memory</div>
                  <div>
                    {formatBytes(stats.memory.used)} / {formatBytes(stats.memory.total)} ({stats.memory.percent}%)
                  </div>
                </div>
              )}
              {stats?.disk && (
                <div>
                  <div className="text-xs uppercase tracking-wider text-muted-foreground flex items-center gap-1">
                    <HardDrive className="h-3 w-3" /> Disk
                  </div>
                  <div>
                    {formatBytes(stats.disk.used)} / {formatBytes(stats.disk.total)} ({stats.disk.percent}%)
                  </div>
                </div>
              )}
              {typeof stats?.uptime_seconds === "number" && (
                <div>
                  <div className="text-xs uppercase tracking-wider text-muted-foreground">Uptime</div>
                  <div>{formatDuration(stats.uptime_seconds)}</div>
                </div>
              )}
              {stats?.load_avg && stats.load_avg.length >= 3 && (
                <div>
                  <div className="text-xs uppercase tracking-wider text-muted-foreground">Load avg</div>
                  <div>{stats.load_avg.map((n) => n.toFixed(2)).join(" / ")}</div>
                </div>
              )}
            </div>
            {stats && !stats.psutil && (
              <p className="mt-3 text-xs text-muted-foreground">
                Install the <span className="font-mono">psutil</span> extra for
                CPU / memory / disk metrics.
              </p>
            )}
            {updateRefusal ? (
              <p
                className="mt-4 border-t border-border pt-4 text-xs text-muted-foreground"
                role="note"
                data-state="unavailable"
              >
                {updateRefusal}
              </p>
            ) : null}
            {canUpdateQubit && !updateRefusal && (
              <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border pt-4">
                <Button
                  size="sm"
                  ghost
                  disabled={checkingUpdate}
                  prefix={
                    checkingUpdate ? (
                      <Spinner className="h-3.5 w-3.5" />
                    ) : (
                      <RotateCw className="h-3.5 w-3.5" />
                    )
                  }
                  onClick={() => void checkForUpdate(true)}
                >
                  Check for updates
                </Button>
                {updateInfo?.update_available && updateInfo.can_apply && (
                  <Button
                    size="sm"
                    prefix={<Download className="h-3.5 w-3.5" />}
                    onClick={() => setUpdateConfirmOpen(true)}
                  >
                    Update now
                  </Button>
                )}
                {updateInfo &&
                  !updateInfo.can_apply &&
                  updateInfo.update_available && (
                    <span className="text-xs text-muted-foreground">
                      Update with{" "}
                      <span className="font-mono">{updateInfo.update_command}</span>
                    </span>
                  )}
                {updateInfo?.message && !updateInfo.update_available && (
                  <span className="text-xs text-muted-foreground">
                    {updateInfo.message}
                  </span>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </section>

      {/* ── Portal ────────────────────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <Globe className="h-4 w-4" /> Qubit Labs
        </H2>
        <Card>
          <CardContent className="flex flex-col gap-3 py-4">
            <div className="flex items-center gap-3">
              <Badge tone={portal?.logged_in ? "success" : "secondary"}>
                {portal?.logged_in ? "logged in" : "not logged in"}
              </Badge>
              {portal?.provider && (
                <span className="text-sm text-muted-foreground">
                  inference provider: {portal.provider}
                </span>
              )}
              <a
                href={portal?.subscription_url || (portal?.portal_url ? `${portal.portal_url}/manage-subscription` : "")}
                target="_blank"
                rel="noreferrer"
                className="ml-auto text-xs text-primary underline"
              >
                Manage subscription
              </a>
            </div>
            {portal?.features && portal.features.length > 0 && (
              <div className="flex flex-col gap-1 border-t border-border pt-3">
                <span className="text-xs uppercase tracking-wider text-muted-foreground">
                  Tool Gateway routing
                </span>
                {portal.features.map((f) => (
                  <div key={f.label} className="flex items-center justify-between text-sm">
                    <span>{f.label}</span>
                    <span className="text-muted-foreground">{f.state}</span>
                  </div>
                ))}
              </div>
            )}
            {!portal?.logged_in && (
              <p className="text-xs text-muted-foreground">
                Log in with <span className="font-mono">qubit portal</span>.
              </p>
            )}
          </CardContent>
        </Card>
      </section>

      {/* ── Curator ───────────────────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <Sparkles className="h-4 w-4" /> Skill curator
        </H2>
        <Card>
          <CardContent className="flex items-center justify-between py-4">
            <div className="flex items-center gap-3">
              <Badge tone={curator?.paused ? "warning" : curator?.enabled ? "success" : "secondary"}>
                {curator?.paused ? "paused" : curator?.enabled ? "active" : "disabled"}
              </Badge>
              <span className="text-sm text-muted-foreground">
                {curator?.interval_hours ? `every ${curator.interval_hours}h` : ""}
                {curator?.last_run_at ? ` · last run ${new Date(curator.last_run_at).toLocaleString()}` : " · never run"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Button size="sm" ghost onClick={toggleCuratorPaused}>
                {curator?.paused ? "Resume" : "Pause"}
              </Button>
              <Button
                size="sm"
                ghost
                prefix={<Play className="h-3.5 w-3.5" />}
                onClick={() => runOp(api.runCurator, "Curator review")}
              >
                Run now
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>

      {/* ── Gateway ───────────────────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <Power className="h-4 w-4" /> Gateway
        </H2>
        <Card>
          <CardContent className="flex items-center justify-between py-4">
            <div className="flex items-center gap-3">
              <Badge tone={gatewayRunning ? "success" : "secondary"}>
                {gatewayRunning ? "running" : "stopped"}
              </Badge>
              <span className="text-sm text-muted-foreground">
                {status?.gateway_state ?? "—"}
                {status?.gateway_pid ? ` · pid ${status.gateway_pid}` : ""}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                className="uppercase"
                onClick={() => runGateway("start")}
                disabled={gatewayRunning || !!gatewayRefusal}
                title={gatewayRefusal ?? undefined}
                prefix={<Play className="h-3.5 w-3.5" />}
              >
                Start
              </Button>
              <Button
                size="sm"
                className="uppercase"
                onClick={() => runGateway("restart")}
                disabled={!!gatewayRefusal}
                title={gatewayRefusal ?? undefined}
                prefix={<RotateCw className="h-3.5 w-3.5" />}
              >
                Restart
              </Button>
              <Button
                size="sm"
                className="uppercase text-warning"
                ghost
                onClick={() => runGateway("stop")}
                disabled={!gatewayRunning || !!gatewayRefusal}
                title={gatewayRefusal ?? undefined}
                prefix={<Power className="h-3.5 w-3.5" />}
              >
                Stop
              </Button>
            </div>
          </CardContent>
          {gatewayRefusal ? (
            <p
              className="px-4 pb-3 text-xs text-muted-foreground"
              role="note"
              data-state="unavailable"
            >
              {gatewayRefusal}
            </p>
          ) : null}
        </Card>
      </section>

      {/* ── Power / emergency stop ────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <Power className="h-4 w-4" /> Power control
        </H2>
        <Card>
          <CardContent className="flex flex-col gap-4 py-4">
            <div className="flex flex-wrap items-center gap-3">
              <Badge tone={estopLatched ? "destructive" : "success"}>
                {estopLatched ? "emergency stop latched" : "operational"}
              </Badge>
              {globallyDisabled && <Badge tone="warning">globally disabled</Badge>}
              {power?.control?.persisted && (
                <span className="text-xs text-muted-foreground">
                  durable across restarts
                </span>
              )}
              <span className="text-xs text-muted-foreground">
                {power?.control?.active ?? 0} active ·{" "}
                {power?.control?.max_concurrency ?? "—"} max concurrency
              </span>
            </div>
            <p className="text-xs text-muted-foreground">
              The emergency stop is the single shared latch the CLI, voice
              gateway, and this dashboard read; latching it refuses new agent
              work everywhere until reset.
            </p>
            <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
              {estopLatched ? (
                <Button
                  size="sm"
                  className="uppercase"
                  disabled={powerBusy}
                  prefix={powerBusy ? <Spinner /> : <RotateCw className="h-3.5 w-3.5" />}
                  onClick={() => void runPowerAction("reset", "Emergency stop reset")}
                >
                  Reset emergency stop
                </Button>
              ) : (
                <Button
                  size="sm"
                  className="uppercase text-destructive"
                  disabled={powerBusy}
                  prefix={powerBusy ? <Spinner /> : <Power className="h-3.5 w-3.5" />}
                  onClick={() => setConfirmEStop(true)}
                >
                  Emergency stop
                </Button>
              )}
              {globallyDisabled ? (
                <Button
                  size="sm"
                  ghost
                  disabled={powerBusy || estopLatched}
                  onClick={() => void runPowerAction("enable", "Global enable")}
                >
                  Global enable
                </Button>
              ) : (
                  <Button
                    size="sm"
                    ghost
                    className="text-warning"
                    disabled={powerBusy || estopLatched}
                    onClick={() => setConfirmGlobalDisable(true)}
                  >
                    Global disable
                  </Button>
              )}
            </div>
          </CardContent>
        </Card>
      </section>

      {/* ── Memory ────────────────────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <Brain className="h-4 w-4" /> Memory
        </H2>
        <Card>
          <CardContent className="flex flex-col gap-4 py-4">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span>
                External provider:{" "}
                <span className="font-mono text-foreground">
                  {memory?.active || "built-in only"}
                </span>
              </span>
              {activeMemoryProvider && (
                <Badge tone={MEMORY_STATUS_TONE[activeMemoryProvider.status]}>
                  {MEMORY_STATUS_LABEL[activeMemoryProvider.status]}
                </Badge>
              )}
              <Link to="/plugins" className="underline">
                Change in Plugins →
              </Link>
              <span className="ml-auto">
                Provider setup:{" "}
                <Link to="/plugins" className="underline">
                  configure in Plugins
                </Link>
              </span>
            </div>

            {activeMemoryProvider?.status === "missing" && (
              <p className="border border-destructive/50 px-3 py-2 text-xs text-destructive">
                The configured provider is no longer installed. Switch to built-in memory or configure another provider in Plugins.
              </p>
            )}

            <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
              <span className="text-xs text-muted-foreground">
                Built-in files — MEMORY.md:{" "}
                {formatBytes(memory?.builtin_files.memory ?? 0)} · USER.md:{" "}
                {formatBytes(memory?.builtin_files.user ?? 0)}
              </span>
              <div className="flex items-center gap-2 ml-auto">
                <Button size="sm" ghost className="text-destructive" onClick={() => memoryReset.requestDelete("memory")}>
                  Reset MEMORY.md
                </Button>
                <Button size="sm" ghost className="text-destructive" onClick={() => memoryReset.requestDelete("user")}>
                  Reset USER.md
                </Button>
                <Button size="sm" ghost className="text-destructive" onClick={() => memoryReset.requestDelete("all")}>
                  Reset all
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </section>

      {/* ── Credential pool ───────────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <KeyRound className="h-4 w-4" /> Credential pool
        </H2>
        <Card>
          <CardContent className="flex flex-col gap-4 py-4">
            <div className="grid grid-cols-1 sm:grid-cols-4 gap-3 items-end">
              <div className="grid gap-2">
                <Label htmlFor="cred-provider">Provider</Label>
                <Input id="cred-provider" value={credProvider} onChange={(e) => setCredProvider(e.target.value)} placeholder="openrouter" />
              </div>
              <div className="grid gap-2 sm:col-span-2">
                <Label htmlFor="cred-key">API key</Label>
                <Input id="cred-key" type="text" value={credKey} onChange={(e) => setCredKey(e.target.value)} placeholder="env:OPENROUTER_API_KEY or file:/path/to/key" />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="cred-label">Label</Label>
                <Input id="cred-label" value={credLabel} onChange={(e) => setCredLabel(e.target.value)} placeholder="optional" />
              </div>
            </div>
            <div className="flex justify-end">
              <Button size="sm" className="uppercase" onClick={addCredential} disabled={addingCred} prefix={addingCred ? <Spinner /> : undefined}>
                Add key
              </Button>
            </div>
            {poolRefusal ? (
              <p className="text-sm text-muted-foreground" role="status">
                {poolRefusal}
              </p>
            ) : pool.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No pooled credentials. Add one above to enable key rotation.
              </p>
            ) : null}
            {pool.map((prov) => (
              <div key={prov.provider} className="flex flex-col gap-2">
                <span className="text-xs uppercase tracking-wider text-muted-foreground">
                  {prov.provider}
                </span>
                {prov.entries.map((entry) => (
                  <div key={`${prov.provider}-${entry.index}`} className="flex items-center gap-3 border border-border bg-background/40 px-3 py-2">
                    <span className="text-sm font-medium">{entry.label}</span>
                    <span className="font-mono text-xs text-muted-foreground">{entry.token_preview}</span>
                    <Badge tone="outline">{entry.auth_type}</Badge>
                    {entry.last_status && <Badge tone="secondary">{entry.last_status}</Badge>}
                    <Button ghost size="icon" className="ml-auto text-destructive" aria-label="Remove credential" onClick={() => credDelete.requestDelete(`${prov.provider}|${entry.index}`)}>
                      <Trash2 />
                    </Button>
                  </div>
                ))}
              </div>
            ))}
          </CardContent>
        </Card>
      </section>

      {/* ── Operations ────────────────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <Activity className="h-4 w-4" /> Operations
        </H2>
        <Card>
          <CardContent className="flex flex-wrap gap-2 py-4">
            <Button size="sm" ghost prefix={<Terminal className="h-3.5 w-3.5" />} onClick={() => setConsoleOpen(true)}>
              Open console
            </Button>
            <Button size="sm" ghost prefix={<Stethoscope className="h-3.5 w-3.5" />} onClick={() => runOp(api.runDoctor, "Doctor")}>
              Run doctor
            </Button>
            <Button size="sm" ghost prefix={<ShieldCheck className="h-3.5 w-3.5" />} onClick={() => runOp(api.runSecurityAudit, "Security audit")}>
              Security audit
            </Button>
            <Button size="sm" ghost prefix={<RotateCw className="h-3.5 w-3.5" />} onClick={() => runOp(async () => ({ name: (await api.updateSkillsFromHub()).name ?? "skills-update" }), "Skills update")}>
              Update skills
            </Button>
            <Button size="sm" ghost prefix={<Activity className="h-3.5 w-3.5" />} onClick={() => runOp(api.runPromptSize, "Prompt size")}>
              Prompt size
            </Button>
            <Button size="sm" ghost prefix={<Database className="h-3.5 w-3.5" />} onClick={() => runOp(api.runDump, "Support dump")}>
              Support dump
            </Button>
            <Button size="sm" ghost prefix={<RotateCw className="h-3.5 w-3.5" />} onClick={() => runOp(api.runConfigMigrate, "Config migrate")}>
              Migrate config
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-4 py-4">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
              <div className="grid min-w-0 flex-1 gap-2">
                <Label>Full backup</Label>
                <Input
                  type="password"
                  value={backupPassphrase}
                  onChange={(e) => setBackupPassphrase(e.target.value)}
                  placeholder="Backup passphrase (encrypts the portable bundle)"
                  autoComplete="new-password"
                />
                <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
                  <Button
                    size="sm"
                    ghost
                    prefix={<Database className="h-3.5 w-3.5" />}
                    onClick={() => void runDashboardBackup()}
                  >
                    Create backup
                  </Button>
                  <Button
                    size="sm"
                    ghost
                    disabled={!downloadableBackupArchive || downloadingBackup}
                    prefix={
                      downloadingBackup ? (
                        <Spinner className="h-3.5 w-3.5" />
                      ) : (
                        <Download className="h-3.5 w-3.5" />
                      )
                    }
                    onClick={() => void downloadBackup()}
                  >
                    Download backup
                  </Button>
                  <span
                    className="min-w-0 truncate text-xs text-muted-foreground"
                    title={pendingBackupArchive ?? "No backup created yet"}
                  >
                    {backupFileName(pendingBackupArchive)}
                  </span>
                </div>
              </div>
            </div>

            <div className="flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-end">
              <div className="grid min-w-0 flex-1 gap-2">
                <Label>Restore from backup upload</Label>
                <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
                  <Button
                    type="button"
                    size="sm"
                    ghost
                    disabled={importingBackup}
                    prefix={<Upload className="h-3.5 w-3.5" />}
                    onClick={() => importUploadInputRef.current?.click()}
                  >
                    Choose restore zip
                  </Button>
                  <span
                    className="min-w-0 truncate text-xs text-muted-foreground"
                    title={importFile?.name ?? "No backup archive selected"}
                  >
                    {importFile?.name ?? "No backup archive selected"}
                  </span>
                </div>
              </div>
              <Button
                size="sm"
                ghost
                disabled={!importFile || importingBackup}
                prefix={importingBackup ? <Spinner /> : undefined}
                onClick={() => {
                  if (!importFile) return;
                  setImportConfirmTarget({ kind: "upload", file: importFile });
                }}
              >
                Restore upload
              </Button>
            </div>

            <div className="flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-end">
              <div className="grid min-w-0 flex-1 gap-2">
                <Label htmlFor="import-path">Restore from backups path</Label>
                <Input
                  id="import-path"
                  value={importPath}
                  onChange={(e) => setImportPath(e.target.value)}
                  placeholder="$QUBIT_HOME/backups/qubit-backup.zip"
                />
              </div>
              <Button
                size="sm"
                ghost
                disabled={!importPath.trim() || importingBackup}
                prefix={importingBackup ? <Spinner /> : undefined}
                onClick={() => {
                  const path = importPath.trim();
                  if (!path) return;
                  setImportConfirmTarget({ kind: "path", path });
                }}
              >
                Restore path
              </Button>
            </div>
            <ConfirmDialog
              open={!!importConfirmTarget}
              title="Restore full Qubit backup?"
              description={`This will overwrite your current Qubit configuration, skills, sessions, and data with the contents of ${backupImportLabel(importConfirmTarget)}. This cannot be undone.`}
              destructive
              confirmLabel="Restore"
              cancelLabel="Cancel"
              onCancel={() => setImportConfirmTarget(null)}
              onConfirm={() => {
                const target = importConfirmTarget;
                setImportConfirmTarget(null);
                if (target) void runBackupImport(target);
              }}
            />
          </CardContent>
        </Card>

        {/* Debug share — uploads a redacted report + logs, returns shareable
            links. Separated from the buttons above because its output is
            persistent, copyable URLs, not a fire-and-forget log tail. */}
        <Card>
          <CardContent className="flex flex-col gap-3 py-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-start gap-2">
                <Share2 className="h-4 w-4 mt-0.5 text-muted-foreground" />
                <div className="flex flex-col">
                  <span className="text-sm font-medium">Share debug report</span>
                  <span className="text-xs text-muted-foreground max-w-prose">
                    Uploads system info + logs to a public paste service and
                    returns links to send the Qubit team. Pastes auto-delete
                    after 6 hours.
                  </span>
                </div>
              </div>
              <Button
                size="sm"
                disabled={sharing}
                prefix={
                  sharing ? (
                    <Spinner className="h-3.5 w-3.5" />
                  ) : (
                    <Share2 className="h-3.5 w-3.5" />
                  )
                }
                onClick={() => void runDebugShare()}
              >
                {sharing ? "Uploading…" : "Generate share link"}
              </Button>
            </div>

            <div className="flex items-center gap-2.5">
              <Checkbox
                checked={shareRedact}
                disabled={sharing}
                id="share-redact"
                onCheckedChange={(checked) => setShareRedact(checked === true)}
              />

              <Label
                className="cursor-pointer select-none text-xs font-normal normal-case tracking-normal text-muted-foreground"
                htmlFor="share-redact"
              >
                Redact credential-shaped tokens before upload (recommended)
              </Label>
            </div>

            {shareResult && (
              <div className="flex flex-col gap-2 border-t border-border pt-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Badge tone="success">uploaded</Badge>
                    {shareResult.redacted ? (
                      <Badge tone="outline">redacted</Badge>
                    ) : (
                      <Badge tone="warning">not redacted</Badge>
                    )}
                    <span className="flex items-center gap-1 text-xs text-muted-foreground">
                      <Clock className="h-3 w-3" />
                      auto-deletes in{" "}
                      {Math.round(shareResult.auto_delete_seconds / 3600)}h
                    </span>
                  </div>
                  {Object.keys(shareResult.urls).length > 1 && (
                    <Button
                      size="sm"
                      ghost
                      prefix={
                        copiedLabel === "__all__" ? (
                          <Check className="h-3.5 w-3.5" />
                        ) : (
                          <Copy className="h-3.5 w-3.5" />
                        )
                      }
                      onClick={() =>
                        void copyToClipboard(
                          Object.entries(shareResult.urls)
                            .map(([label, url]) => `${label}: ${url}`)
                            .join("\n"),
                          "__all__",
                        )
                      }
                    >
                      Copy all
                    </Button>
                  )}
                </div>

                {Object.entries(shareResult.urls).map(([label, url]) => (
                  <div
                    key={label}
                    className="flex items-center gap-2 bg-background/50 border border-border px-3 py-2"
                  >
                    <Link2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    <span className="font-mono text-xs shrink-0 w-24 truncate text-muted-foreground">
                      {label}
                    </span>
                    <a
                      href={url}
                      target="_blank"
                      rel="noreferrer"
                      className="font-mono text-xs truncate flex-1 text-primary hover:underline"
                    >
                      {url}
                    </a>
                    <Button
                      ghost
                      size="icon"
                      aria-label={`Copy ${label} link`}
                      onClick={() => void copyToClipboard(url, label)}
                    >
                      {copiedLabel === label ? <Check /> : <Copy />}
                    </Button>
                  </div>
                ))}

                {shareResult.failures.length > 0 && (
                  <span className="text-xs text-destructive">
                    Some logs failed to upload: {shareResult.failures.join("; ")}
                  </span>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </section>

      {/* ── Checkpoints ───────────────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <Database className="h-4 w-4" /> Checkpoints
        </H2>
        <Card>
          <CardContent className="flex items-center justify-between py-4">
            <span className="text-sm text-muted-foreground">
              {checkpoints?.sessions.length ?? 0} session(s) ·{" "}
              {formatBytes(checkpoints?.total_bytes ?? 0)}
            </span>
            <Button size="sm" ghost className="text-destructive" disabled={!checkpoints?.sessions.length} prefix={<Trash2 className="h-3.5 w-3.5" />} onClick={() => checkpointsPrune.requestDelete("all")}>
              Prune
            </Button>
          </CardContent>
        </Card>
      </section>

      {/* ── Shell hooks ───────────────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
            <Terminal className="h-4 w-4" /> Shell hooks
          </H2>
          <Button size="sm" className="uppercase" prefix={<Plus className="h-3.5 w-3.5" />} onClick={() => setHookModalOpen(true)}>
            New hook
          </Button>
        </div>
        {(!hooks || hooks.hooks.length === 0) && (
          <Card>
            <CardContent className="py-6 text-center text-sm text-muted-foreground">
              No shell hooks configured.
            </CardContent>
          </Card>
        )}
        {hooks?.hooks.map((h: HookEntry, i) => (
          <Card key={`${h.event}-${i}`}>
            <CardContent className="flex items-center gap-3 py-3">
              <Badge tone="outline">{h.event}</Badge>
              {h.matcher && (
                <span className="text-xs text-muted-foreground">matcher: {h.matcher}</span>
              )}
              <span className="font-mono text-xs truncate flex-1">{h.command}</span>
              {h.executable === false && (
                <Badge tone="destructive">not executable</Badge>
              )}
              <Badge tone={h.allowed ? "success" : "warning"}>
                {h.allowed ? "allowed" : "not approved"}
              </Badge>
              <Button
                ghost
                size="icon"
                className="text-destructive"
                aria-label="Remove hook"
                onClick={() =>
                  hookDelete.requestDelete(`${h.event}|${h.command ?? ""}`)
                }
              >
                <Trash2 />
              </Button>
            </CardContent>
          </Card>
        ))}
      </section>
    </div>
    </PageErrorBoundary>
  );
}
