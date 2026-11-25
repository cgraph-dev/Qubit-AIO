import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import {
  AlertTriangle,
  Check,
  Copy,
  History,
  KeyRound,
  Pencil,
  RefreshCw,
  RotateCw,
  Send,
  Trash2,
  Webhook,
  X,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { api } from "@/lib/api";
import type {
  WebhookDelivery,
  WebhookRoute,
  WebhooksResponse,
} from "@/lib/api";
import { copyTextToClipboard } from "@/lib/clipboard";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import { useConfirmDelete } from "@nous-research/ui/hooks/use-confirm-delete";
import { useModalBehavior } from "@/hooks/useModalBehavior";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Button, Input, SakugaLoading, emitFx, showSakugaToast } from "@/sakuga/ui";
import { Label } from "@nous-research/ui/ui/components/label";
import { usePageHeader } from "@/contexts/usePageHeader";
import { cn, themedBody } from "@/lib/utils";
import "./WebhooksPage.css";

interface CreatedWebhook {
  url: string;
  secret: string;
}

/** Derive the canonical subscription id (``^[a-z0-9][a-z0-9_.-]{0,63}$``). */
function webhookSlug(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, "-")
    .replace(/^[-._]+|[-._]+$/g, "")
    .slice(0, 64);
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(() => {
    void copyTextToClipboard(value).then((copied) => {
      if (!copied) return;
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  }, [value]);
  return (
    <Button
      variant="secondary"
      title="Copy"
      aria-label="Copy"
      onClick={handleCopy}
      className="qm-webhook-icon-btn shrink-0 text-muted-foreground hover:text-foreground"
    >
      {copied ? <Check /> : <Copy />}
    </Button>
  );
}

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null;
  return (
    <p id={id} role="alert" className="text-xs text-destructive">
      {message}
    </p>
  );
}

export default function WebhooksPage() {
  const [data, setData] = useState<WebhooksResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [enabling, setEnabling] = useState(false);
  const [restartNeeded, setRestartNeeded] = useState(false);
  const [restartMessage, setRestartMessage] = useState<string | null>(null);
  const [restartError, setRestartError] = useState<string | null>(null);
  const [restarting, setRestarting] = useState(false);
  const showToast = useCallback((message: string, kind?: "success" | "error") => {
    showSakugaToast(message);
    emitFx(kind === "error" ? "error" : "success");
  }, []);
  const { setEnd } = usePageHeader();

  // New subscription modal state (canonical webhook contract: id/url/events/
  // secret_ref — the same record the CLI/voice write).
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [editing, setEditing] = useState<WebhookRoute | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState("");
  const [secretRef, setSecretRef] = useState("");
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<CreatedWebhook | null>(null);
  // Inline validation for the create dialog. A validation failure must be
  // actionable where the operator is looking — the shared toast renders at
  // z-50, beneath the modal backdrop (z-[100]), so feedback routed only to it
  // is invisible and the dialog looks dead. Field-level errors render inside
  // the dialog and focus the first invalid field.
  const [formErrors, setFormErrors] = useState<{
    name?: string;
    url?: string;
    events?: string;
    secretRef?: string;
  }>({});

  // Per-subscription operations (rotate/test/deliveries/retry). ``actionBusy``
  // keys the in-flight action as ``<name>:<action>`` so only that button spins.
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [deliveriesFor, setDeliveriesFor] = useState<string | null>(null);
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([]);
  const [deliveriesLoading, setDeliveriesLoading] = useState(false);
  const closeDeliveries = useCallback(() => setDeliveriesFor(null), []);
  const deliveriesModalRef = useModalBehavior({
    open: deliveriesFor !== null,
    onClose: closeDeliveries,
  });

  const closeCreateModal = useCallback(() => {
    setCreateModalOpen(false);
    setCreated(null);
    setEditing(null);
  }, []);
  const dismissCreateModal = useCallback(() => {
    if (!creating) closeCreateModal();
  }, [closeCreateModal, creating]);
  const createModalRef = useModalBehavior({
    open: createModalOpen,
    onClose: dismissCreateModal,
  });

  const enabled = data?.enabled ?? false;
  const subscriptions = data?.subscriptions ?? [];

  const loadWebhooks = useCallback(() => {
    return api
      .getWebhooks()
      .then(setData)
      .catch(() => showToast("Failed to load webhooks", "error"))
      .finally(() => setLoading(false));
  }, [showToast]);

  useEffect(() => {
    loadWebhooks();
  }, [loadWebhooks]);

  // The wrapper renders a real input inside an open shadow root, so the
  // browser's `autoFocus` cannot reach it. Focus it once the element has
  // upgraded; the retry covers the lazy component load.
  useEffect(() => {
    if (!createModalOpen) return;
    let tries = 0;
    let timer = 0;
    const attempt = () => {
      const host = document.getElementById(editing ? "webhook-url" : "webhook-name");
      const input = host?.shadowRoot?.querySelector("input");
      if (input) {
        input.focus();
        window.clearInterval(timer);
      } else if (++tries > 40) {
        window.clearInterval(timer);
      }
    };
    attempt();
    timer = window.setInterval(attempt, 10);
    return () => window.clearInterval(timer);
  }, [createModalOpen, editing]);

  const watchRestartOutcome = useCallback(async () => {
    for (let i = 0; i < 20; i++) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      try {
        const st = await api.getActionStatus("gateway-restart", 5);
        if (st.running) continue;
        if (st.exit_code !== 0 && st.exit_code !== null) {
          setRestartMessage(null);
          setRestartNeeded(true);
          setRestartError(`Gateway restart failed with exit ${st.exit_code}.`);
          showToast(
            `Gateway restart failed (exit ${st.exit_code}) — restart manually`,
            "error",
          );
        } else {
          setRestartMessage(null);
          setRestartNeeded(false);
          setRestartError(null);
        }
        return;
      } catch {
        // The dashboard may briefly lose its connection while the gateway restarts.
      }
    }
    setRestartMessage(null);
  }, [showToast]);

  const handleRestart = useCallback(async () => {
    setRestarting(true);
    try {
      await api.restartGateway();
      setRestartNeeded(false);
      setRestartError(null);
      setRestartMessage("Gateway restarting…");
      showToast("Gateway restarting…", "success");
      setTimeout(() => void loadWebhooks(), 4000);
      void watchRestartOutcome();
    } catch (e) {
      setRestartNeeded(true);
      setRestartError(String(e));
      showToast(`Failed to restart: ${e}`, "error");
    } finally {
      setRestarting(false);
    }
  }, [loadWebhooks, showToast, watchRestartOutcome]);

  const handleEnableWebhooks = useCallback(async () => {
    setEnabling(true);
    setRestartNeeded(false);
    setRestartError(null);
    try {
      // Button press is the explicit confirmation; settle the staged gate.
      const result = await api.enableWebhooks(() => true);
      await loadWebhooks();
      if (result.restart_started) {
        setRestartMessage("Webhooks enabled; gateway restarting…");
        showToast("Webhooks enabled; gateway restarting…", "success");
        setTimeout(() => void loadWebhooks(), 4000);
        void watchRestartOutcome();
      } else if (result.needs_restart) {
        const detail = result.restart_error ? `: ${result.restart_error}` : ".";
        setRestartMessage(null);
        setRestartNeeded(true);
        setRestartError(`Gateway restart failed${detail}`);
        showToast(`Webhooks enabled; gateway restart failed${detail}`, "error");
      } else {
        setRestartMessage(null);
        showToast("Webhooks enabled.", "success");
      }
    } catch (e) {
      showToast(`Failed to enable webhooks: ${e}`, "error");
    } finally {
      setEnabling(false);
    }
  }, [loadWebhooks, showToast, watchRestartOutcome]);

  const resetForm = useCallback(() => {
    setName("");
    setUrl("");
    setEvents("");
    setSecretRef("");
    setFormErrors({});
    setSubmitError(null);
  }, []);

  const openEdit = (subscription: WebhookRoute) => {
    resetForm();
    setCreated(null);
    setEditing(subscription);
    setEvents(subscription.events.join(", "));
    setCreateModalOpen(true);
  };

  const handleSaveSubscription = async () => {
    setSubmitError(null);
    const subId = webhookSlug(name);
    const deliveryUrl = url.trim();
    const eventsList = events
      .split(",")
      .map((e) => e.trim().toLowerCase())
      .filter(Boolean);
    const secretReference = secretRef.trim();
    const errors: {
      name?: string;
      url?: string;
      events?: string;
      secretRef?: string;
    } = {};
    if (!editing && !subId) {
      errors.name = "Name required — use a lowercase id (e.g. github-push).";
    }
    if (!editing && !deliveryUrl) {
      errors.url = "Delivery URL required.";
    }
    if (!eventsList.length) {
      errors.events = "At least one event is required (comma-separated).";
    }
    if ((!editing || secretReference) && !/^(env|file):/.test(secretReference)) {
      errors.secretRef =
        "Secret must be an env:VAR or file:PATH reference.";
    }
    setFormErrors(errors);
    const firstInvalid = (
      ["name", "url", "events", "secretRef"] as const
    ).find((key) => errors[key]);
    if (firstInvalid) {
      const inputIds = {
        name: "webhook-name",
        url: "webhook-url",
        events: "webhook-events",
        secretRef: "webhook-secret-ref",
      };
      document.getElementById(inputIds[firstInvalid])?.focus();
      return;
    }
    setCreating(true);
    try {
      if (editing) {
        const originalEvents = [...editing.events].sort().join(",");
        const updatedEvents = [...new Set(eventsList)].sort().join(",");
        const changes = {
          ...(deliveryUrl && { url: deliveryUrl }),
          ...(updatedEvents !== originalEvents && { events: eventsList }),
          ...(secretReference && { secret_ref: secretReference }),
        };
        if (Object.keys(changes).length === 0) {
          closeCreateModal();
          return;
        }
        await api.updateWebhook(editing.name, changes, editing.created_at);
        showToast("Subscription updated", "success");
        closeCreateModal();
      } else {
        const res = await api.createWebhook(
          { name: subId, url: deliveryUrl, events: eventsList, secret_ref: secretReference },
          () => true,
        );
        showToast("Created ✓", "success");
        setCreated({ url: res.url, secret: res.secret });
      }
      resetForm();
      loadWebhooks();
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  };

  const [togglingName, setTogglingName] = useState<string | null>(null);

  const handleToggleEnabled = useCallback(
    async (subName: string, nextEnabled: boolean) => {
      setTogglingName(subName);
      try {
        await api.setWebhookEnabled(subName, nextEnabled);
        showToast(
          nextEnabled ? `Enabled: "${subName}"` : `Disabled: "${subName}"`,
          "success",
        );
        loadWebhooks();
      } catch (e) {
        showToast(`Error: ${e}`, "error");
      } finally {
        setTogglingName(null);
      }
    },
    [loadWebhooks, showToast],
  );

  const handleRotateSecret = useCallback(
    async (sub: WebhookRoute) => {
      setActionBusy(`${sub.name}:rotate`);
      try {
        const res = await api.rotateWebhookSecret(sub.name, () => true);
        showToast(
          `Rotated signing secret for "${sub.name}" → ${res.reference_kind ?? "file:"}`,
          "success",
        );
        loadWebhooks();
      } catch (e) {
        showToast(`Rotate failed: ${e}`, "error");
      } finally {
        setActionBusy(null);
      }
    },
    [loadWebhooks, showToast],
  );

  const handleTestDelivery = useCallback(
    async (sub: WebhookRoute) => {
      setActionBusy(`${sub.name}:test`);
      try {
        const res = await api.testWebhook(sub.name, () => true);
        if (res.verdict === "DELIVERED") {
          showToast(
            `Test delivered to "${sub.name}" (HTTP ${res.http_status ?? "—"})`,
            "success",
          );
        } else {
          showToast(
            `Test ${res.verdict ?? "FAILED"}: ${res.error ?? "delivery failed"}`,
            "error",
          );
        }
      } catch (e) {
        showToast(`Test failed: ${e}`, "error");
      } finally {
        setActionBusy(null);
      }
    },
    [showToast],
  );

  const handleRetryDelivery = useCallback(
    async (sub: WebhookRoute) => {
      setActionBusy(`${sub.name}:retry`);
      try {
        const res = await api.retryWebhook(sub.name, () => true);
        if (res.verdict === "DELIVERED") {
          showToast(`Retry delivered for "${sub.name}"`, "success");
        } else {
          showToast(
            `Retry ${res.verdict ?? "FAILED"}: ${res.error ?? "delivery failed"}`,
            "error",
          );
        }
      } catch (e) {
        showToast(`Retry failed: ${e}`, "error");
      } finally {
        setActionBusy(null);
      }
    },
    [showToast],
  );

  const openDeliveries = useCallback(
    async (sub: WebhookRoute) => {
      setDeliveriesFor(sub.name);
      setDeliveries([]);
      setDeliveriesLoading(true);
      try {
        const res = await api.getWebhookDeliveries(sub.name);
        setDeliveries(res.deliveries);
      } catch (e) {
        showToast(`Could not load deliveries: ${e}`, "error");
      } finally {
        setDeliveriesLoading(false);
      }
    },
    [showToast],
  );

  const webhookDelete = useConfirmDelete({
    onDelete: useCallback(
      async (name: string) => {
        try {
          // The row's Delete dialog is the human confirmation; the backend
          // still stages its own single-use confirmation, settled here.
          await api.deleteWebhook(name, () => true);
          showToast(`Deleted: "${name}"`, "success");
          loadWebhooks();
        } catch (e) {
          showToast(`Error: ${e}`, "error");
          throw e;
        }
      },
      [loadWebhooks, showToast],
    ),
  });

  // Put "New subscription" button in page header
  useLayoutEffect(() => {
    setEnd(
      <Button
        className="uppercase"
        disabled={!enabled || enabling}
        onClick={() => {
          resetForm();
          setEditing(null);
          setCreated(null);
          setCreateModalOpen(true);
        }}
      >
        New subscription
      </Button>,
    );
    return () => {
      setEnd(null);
    };
  }, [setEnd, enabled, enabling, loading, resetForm]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24" aria-busy="true">
        <SakugaLoading label="Loading webhooks…" />
      </div>
    );
  }

  const pendingName = webhookDelete.pendingId ?? "";

  return (
    <div className="qm-webhooks-page flex flex-col gap-6">

      <DeleteConfirmDialog
        open={webhookDelete.isOpen}
        onCancel={webhookDelete.cancel}
        onConfirm={webhookDelete.confirm}
        title="Delete webhook"
        description={
          pendingName
            ? `"${pendingName}" — this will permanently remove this webhook subscription.`
            : "This will permanently remove this webhook subscription."
        }
        loading={webhookDelete.isDeleting}
      />

      {/* Create subscription modal */}
      {createModalOpen && (
        <div
          ref={createModalRef}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/85 p-4"
          onClick={(e) => e.target === e.currentTarget && dismissCreateModal()}
          role="dialog"
          aria-modal="true"
          aria-labelledby="create-webhook-title"
        >
          <div data-qm-form-panel="" className={cn(themedBody, "relative w-full max-w-lg border border-border bg-card shadow-2xl flex flex-col max-h-[90vh] overflow-y-auto")}>
            <Button
              variant="secondary"
              onClick={dismissCreateModal}
              disabled={creating}
              className="qm-webhook-icon-btn absolute right-2 top-2 text-muted-foreground hover:text-foreground"
              aria-label="Close"
            >
              <X />
            </Button>

            <header className="p-5 pb-3 border-b border-border">
              <h2
                id="create-webhook-title"
                className="break-words pr-8 font-mondwest text-display text-base tracking-wider"
              >
                {editing ? `Edit subscription · ${editing.name}` : "New subscription"}
              </h2>
            </header>

            {created ? (
              <div className="p-5 grid gap-4">
                <p className="text-sm text-muted-foreground">
                  Subscription created in the canonical webhook registry.
                </p>

                <div className="grid gap-2">
                  <Label>Delivery URL</Label>
                  <div className="flex items-center gap-2 border border-border bg-background/40 px-3 py-2">
                    <span className="flex-1 min-w-0 truncate font-mono text-xs">
                      {created.url}
                    </span>
                    <CopyButton value={created.url} />
                  </div>
                </div>

                <div className="grid gap-2">
                  <Label>Secret reference</Label>
                  <div className="flex items-center gap-2 border border-warning/40 bg-warning/10 px-3 py-2">
                    <span className="flex-1 min-w-0 truncate font-mono text-xs">
                      {created.secret}
                    </span>
                    <CopyButton value={created.secret} />
                  </div>
                </div>

                <div className="flex justify-end">
                  <Button
                    className="uppercase"
                    onClick={closeCreateModal}
                  >
                    Done
                  </Button>
                </div>
              </div>
            ) : (
              <div className="p-5 grid gap-4">
                {!editing && (
                  <div className="grid gap-2">
                    <Label htmlFor="webhook-name">Name</Label>
                    <Input
                      label="Name"
                      id="webhook-name"
                      placeholder="e.g. github-push"
                      value={name}
                      aria-invalid={formErrors.name ? "true" : undefined}
                      aria-describedby={
                        formErrors.name ? "webhook-name-error" : undefined
                      }
                      onChange={(value) => {
                        setName(value);
                        if (formErrors.name)
                          setFormErrors((prev) => ({ ...prev, name: undefined }));
                      }}
                    />
                    <FieldError id="webhook-name-error" message={formErrors.name} />
                  </div>
                )}

                <div className="grid gap-2">
                  <Label htmlFor="webhook-url">Delivery URL</Label>
                  <Input
                    label="Delivery URL"
                    id="webhook-url"
                    placeholder="https://hooks.example.com/agent"
                    value={url}
                    aria-invalid={formErrors.url ? "true" : undefined}
                    aria-describedby={
                      formErrors.url ? "webhook-url-error" : editing ? "webhook-url-help" : undefined
                    }
                    onChange={(value) => {
                      setUrl(value);
                      if (formErrors.url)
                        setFormErrors((prev) => ({ ...prev, url: undefined }));
                    }}
                  />
                  {editing && (
                    <p id="webhook-url-help" className="text-sm text-muted-foreground">
                      Leave blank to keep the current URL.
                    </p>
                  )}
                  <FieldError id="webhook-url-error" message={formErrors.url} />
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="webhook-events">Events</Label>
                  <Input
                    label="Events"
                    id="webhook-events"
                    placeholder="comma-separated, e.g. task.completed"
                    value={events}
                    aria-invalid={formErrors.events ? "true" : undefined}
                    aria-describedby={
                      formErrors.events ? "webhook-events-error" : undefined
                    }
                    onChange={(value) => {
                      setEvents(value);
                      if (formErrors.events)
                        setFormErrors((prev) => ({ ...prev, events: undefined }));
                    }}
                  />
                  <FieldError
                    id="webhook-events-error"
                    message={formErrors.events}
                  />
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="webhook-secret-ref">Signing secret reference</Label>
                  <Input
                    label="Signing secret reference"
                    id="webhook-secret-ref"
                    placeholder="env:WEBHOOK_SIGNING_SECRET or file:/run/secrets/hook"
                    value={secretRef}
                    aria-invalid={formErrors.secretRef ? "true" : undefined}
                    aria-describedby={
                      formErrors.secretRef ? "webhook-secret-ref-error" : editing ? "webhook-secret-ref-help" : undefined
                    }
                    onChange={(value) => {
                      setSecretRef(value);
                      if (formErrors.secretRef)
                        setFormErrors((prev) => ({
                          ...prev,
                          secretRef: undefined,
                        }));
                    }}
                  />
                  {editing && (
                    <p id="webhook-secret-ref-help" className="text-sm text-muted-foreground">
                      Leave blank to keep the current reference.
                    </p>
                  )}
                  <FieldError
                    id="webhook-secret-ref-error"
                    message={formErrors.secretRef}
                  />
                </div>

                {submitError && <p role="alert" className="text-sm text-destructive break-words">{submitError}</p>}
                <div className="flex justify-end">
                  <Button
                    className="uppercase"
                    onClick={handleSaveSubscription}
                    disabled={creating}
                  >
                    {editing ? (creating ? "Saving…" : "Save changes") : (creating ? "Creating…" : "Create")}
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Delivery receipts modal (redacted) */}
      {deliveriesFor && (
        <div
          ref={deliveriesModalRef}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/85 p-4"
          onClick={(e) => e.target === e.currentTarget && closeDeliveries()}
          role="dialog"
          aria-modal="true"
          aria-labelledby="webhook-deliveries-title"
        >
          <div data-qm-form-panel=""
            className={cn(
              themedBody,
              "relative w-full max-w-2xl border border-border bg-card shadow-2xl flex flex-col max-h-[90vh]",
            )}
          >
            <Button
              variant="secondary"
              onClick={closeDeliveries}
              className="qm-webhook-icon-btn absolute right-2 top-2 text-muted-foreground hover:text-foreground"
              aria-label="Close"
            >
              <X />
            </Button>
            <header className="p-5 pb-3 border-b border-border">
              <h2
                id="webhook-deliveries-title"
                className="font-mondwest text-display text-base tracking-wider"
              >
                Deliveries — {deliveriesFor}
              </h2>
            </header>
            <div className="p-5 overflow-y-auto">
              {deliveriesLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Spinner className="text-xl text-primary" />
                </div>
              ) : deliveries.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-8">
                  No delivery receipts on record.
                </p>
              ) : (
                <div className="flex flex-col gap-2">
                  {deliveries.map((d, i) => (
                    <div
                      key={`${d.ts ?? "d"}-${i}`}
                      className="flex items-start gap-3 border border-border bg-background/40 px-3 py-2 text-xs"
                    >
                      <Badge
                        className="qm-webhook-badge"
                        tone={
                          d.verdict === "DELIVERED"
                            ? "success"
                            : d.verdict === "FAILED"
                              ? "destructive"
                              : "secondary"
                        }
                      >
                        {d.verdict ?? "UNKNOWN"}
                      </Badge>
                      <div className="flex-1 min-w-0">
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground">
                          <span className="font-mono">
                            {d.event ?? "—"}
                          </span>
                          {typeof d.status === "number" && (
                            <span>HTTP {d.status}</span>
                          )}
                          {d.ts && <span>{d.ts}</span>}
                        </div>
                        {d.error && (
                          <p className="mt-1 text-destructive break-words">
                            {d.error}
                          </p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Always render the receiver state. The reference only surfaces the
          disabled warning, which made the enabled state invisible (and with it
          any indication the receiver is accepting events). */}
      <Card className={enabled ? "border-success/40" : "border-warning/50"}>
        <CardContent className="flex flex-col gap-4 py-6 text-sm sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <Webhook
              className={cn(
                "h-5 w-5 shrink-0",
                enabled ? "text-success" : "text-warning",
              )}
            />
            <div className="flex flex-col gap-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">Webhook receiver</span>
                <Badge tone={enabled ? "success" : "warning"}>
                  {enabled ? "enabled" : "disabled"}
                </Badge>
              </div>
              <span className="text-muted-foreground">
                {enabled
                  ? "Incoming HTTP events are accepted. Subscription changes hot-reload without a gateway restart."
                  : "Webhooks are their own gateway platform. Enable them here to accept incoming HTTP events; chat channels are only needed when a subscription delivers to Telegram, Discord, Slack, or another channel."}
              </span>
            </div>
          </div>
          {!enabled && (
            <Button
              className="uppercase shrink-0"
              onClick={handleEnableWebhooks}
              disabled={enabling}
            >
              {enabling ? "Enabling…" : "Enable webhooks"}
            </Button>
          )}
        </CardContent>
      </Card>

      {restartMessage && !restartNeeded && (
        <Card className="border-border">
          <CardContent className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
            <RotateCw className="h-4 w-4 shrink-0 text-warning" />
            <span>{restartMessage}</span>
          </CardContent>
        </Card>
      )}

      {restartNeeded && (
        <Card className="border-warning/50">
          <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-2 text-sm">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
              <span>
                {restartError ??
                  "Webhooks are enabled, but the gateway still needs a restart before the receiver can come online."}
              </span>
            </div>
            <Button
              className="uppercase shrink-0"
              onClick={handleRestart}
              disabled={restarting}
            >
              {restarting ? "Restarting…" : "Restart gateway"}
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="flex flex-col gap-3">
        <H2
          variant="sm"
          className="flex items-center gap-2 text-muted-foreground"
        >
          <Webhook className="h-4 w-4" />
          Subscriptions ({subscriptions.length})
        </H2>

        <p className="text-xs text-muted-foreground -mt-1">
          Subscription changes hot-reload once the webhook receiver is running.
          Disabled subscriptions reject incoming events.
        </p>

        {subscriptions.length === 0 && (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No webhook subscriptions yet.
            </CardContent>
          </Card>
        )}

        {subscriptions.map((sub: WebhookRoute) => (
          <Card key={sub.name}>
            <CardContent className="flex flex-col gap-3 py-4 sm:flex-row sm:items-start sm:gap-4">
              <div className={cn("flex-1 min-w-0", !sub.enabled && "opacity-60")}>
                <div className="flex items-center gap-2 mb-1 flex-wrap min-w-0">
                  <span className="font-medium text-sm break-words min-w-0">
                    {sub.name}
                  </span>
                  <Badge className="qm-webhook-badge" tone="outline">
                    {sub.deliver}
                  </Badge>
                  {sub.deliver_only && (
                    <Badge className="qm-webhook-badge" tone="secondary">
                      deliver only
                    </Badge>
                  )}
                  {!sub.enabled && (
                    <Badge className="qm-webhook-badge" tone="warning">
                      disabled
                    </Badge>
                  )}
                </div>

                {sub.description && (
                  <p className="text-xs text-muted-foreground mb-2">
                    {sub.description}
                  </p>
                )}

                <div className="flex items-center gap-1 flex-wrap mb-2 min-w-0">
                  {sub.events.length === 0 ? (
                    <Badge className="qm-webhook-badge" tone="secondary">
                      (all)
                    </Badge>
                  ) : (
                    sub.events.map((evt) => (
                      <Badge key={evt} className="qm-webhook-badge" tone="secondary">
                        {evt}
                      </Badge>
                    ))
                  )}
                </div>

                <div className="flex items-center gap-2 text-xs text-muted-foreground min-w-0">
                  <span className="flex-1 min-w-0 break-all font-mono">
                    {sub.url}
                  </span>
                  <CopyButton value={sub.url} />
                </div>
              </div>

              <div className="qm-webhook-actions flex flex-wrap items-center gap-1.5 sm:justify-end">
                <Button
                  variant="secondary"
                  className="qm-webhook-icon-btn"
                  title="Edit subscription"
                  aria-label={`Edit subscription ${sub.name}`}
                  onClick={() => openEdit(sub)}
                >
                  <Pencil />
                </Button>
                <Button
                  variant="secondary"
                  className="qm-webhook-icon-btn"
                  title="Rotate signing secret"
                  aria-label="Rotate signing secret"
                  disabled={actionBusy === `${sub.name}:rotate`}
                  onClick={() => handleRotateSecret(sub)}
                >
                  {actionBusy === `${sub.name}:rotate` ? (
                    <Spinner />
                  ) : (
                    <KeyRound />
                  )}
                </Button>
                <Button
                  variant="secondary"
                  className="qm-webhook-icon-btn"
                  title="Send test delivery"
                  aria-label="Send test delivery"
                  disabled={actionBusy === `${sub.name}:test` || !sub.enabled}
                  onClick={() => handleTestDelivery(sub)}
                >
                  {actionBusy === `${sub.name}:test` ? <Spinner /> : <Send />}
                </Button>
                <Button
                  variant="secondary"
                  className="qm-webhook-icon-btn"
                  title="Delivery history"
                  aria-label="Delivery history"
                  onClick={() => openDeliveries(sub)}
                >
                  <History />
                </Button>
                <Button
                  variant="secondary"
                  className="qm-webhook-icon-btn"
                  title="Retry last failed delivery"
                  aria-label="Retry last failed delivery"
                  disabled={actionBusy === `${sub.name}:retry` || !sub.enabled}
                  onClick={() => handleRetryDelivery(sub)}
                >
                  {actionBusy === `${sub.name}:retry` ? (
                    <Spinner />
                  ) : (
                    <RefreshCw />
                  )}
                </Button>
                <Button
                  variant="secondary"
                  className="qm-webhook-toggle uppercase"
                  disabled={togglingName === sub.name}
                  onClick={() => handleToggleEnabled(sub.name, !sub.enabled)}
                >
                  {sub.enabled ? "Disable" : "Enable"}
                </Button>
                <Button
                  variant="secondary"
                  className="qm-webhook-icon-btn"
                  title="Delete"
                  aria-label="Delete"
                  onClick={() => webhookDelete.requestDelete(sub.name)}
                >
                  <Trash2 />
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
