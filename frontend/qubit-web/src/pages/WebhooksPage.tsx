import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import {
  AlertTriangle,
  Check,
  Copy,
  History,
  KeyRound,
  Plus,
  RefreshCw,
  RotateCw,
  Send,
  Trash2,
  Webhook,
  X,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
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
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { useConfirmDelete } from "@nous-research/ui/hooks/use-confirm-delete";
import { useModalBehavior } from "@/hooks/useModalBehavior";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { usePageHeader } from "@/contexts/usePageHeader";
import { cn, themedBody } from "@/lib/utils";

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
      ghost
      size="icon"
      title="Copy"
      aria-label="Copy"
      onClick={handleCopy}
      className="text-muted-foreground hover:text-foreground"
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
  const { toast, showToast } = useToast();
  const { setEnd } = usePageHeader();

  // New subscription modal state (canonical webhook contract: id/url/events/
  // secret_ref — the same record the CLI/voice write).
  const [createModalOpen, setCreateModalOpen] = useState(false);
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
  }, []);
  const createModalRef = useModalBehavior({
    open: createModalOpen,
    onClose: closeCreateModal,
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
      } else {
        const detail = result.restart_error ? `: ${result.restart_error}` : ".";
        setRestartMessage(null);
        setRestartNeeded(true);
        setRestartError(`Gateway restart failed${detail}`);
        showToast(`Webhooks enabled; gateway restart failed${detail}`, "error");
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
  }, []);

  const handleCreate = async () => {
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
    if (!subId) {
      errors.name = "Name required — use a lowercase id (e.g. github-push).";
    }
    if (!deliveryUrl) {
      errors.url = "Delivery URL required.";
    }
    if (!eventsList.length) {
      errors.events = "At least one event is required (comma-separated).";
    }
    if (!/^(env|file):/.test(secretReference)) {
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
      // The New-subscription modal is the confirmation; settle the staged gate.
      const res = await api.createWebhook(
        {
          name: subId,
          url: deliveryUrl,
          events: eventsList,
          secret_ref: secretReference,
        },
        () => true,
      );
      showToast("Created ✓", "success");
      setCreated({ url: res.url, secret: res.secret });
      resetForm();
      loadWebhooks();
    } catch (e) {
      showToast(`Failed to create: ${e}`, "error");
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
        size="sm"
        disabled={!enabled || enabling}
        prefix={<Plus />}
        onClick={() => {
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
  }, [setEnd, enabled, enabling, loading]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  const pendingName = webhookDelete.pendingId ?? "";

  return (
    <div className="flex flex-col gap-6">
      <Toast toast={toast} />

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
          onClick={(e) => e.target === e.currentTarget && closeCreateModal()}
          role="dialog"
          aria-modal="true"
          aria-labelledby="create-webhook-title"
        >
          <div className={cn(themedBody, "relative w-full max-w-lg border border-border bg-card shadow-2xl flex flex-col max-h-[90vh] overflow-y-auto")}>
            <Button
              ghost
              size="icon"
              onClick={closeCreateModal}
              className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
              aria-label="Close"
            >
              <X />
            </Button>

            <header className="p-5 pb-3 border-b border-border">
              <h2
                id="create-webhook-title"
                className="font-mondwest text-display text-base tracking-wider"
              >
                New subscription
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
                    size="sm"
                    onClick={closeCreateModal}
                  >
                    Done
                  </Button>
                </div>
              </div>
            ) : (
              <div className="p-5 grid gap-4">
                <div className="grid gap-2">
                  <Label htmlFor="webhook-name">Name</Label>
                  <Input
                    id="webhook-name"
                    autoFocus
                    placeholder="e.g. github-push"
                    value={name}
                    aria-invalid={formErrors.name ? true : undefined}
                    aria-describedby={
                      formErrors.name ? "webhook-name-error" : undefined
                    }
                    onChange={(e) => {
                      setName(e.target.value);
                      if (formErrors.name)
                        setFormErrors((prev) => ({ ...prev, name: undefined }));
                    }}
                  />
                  <FieldError id="webhook-name-error" message={formErrors.name} />
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="webhook-url">Delivery URL</Label>
                  <Input
                    id="webhook-url"
                    placeholder="https://hooks.example.com/agent"
                    value={url}
                    aria-invalid={formErrors.url ? true : undefined}
                    aria-describedby={
                      formErrors.url ? "webhook-url-error" : undefined
                    }
                    onChange={(e) => {
                      setUrl(e.target.value);
                      if (formErrors.url)
                        setFormErrors((prev) => ({ ...prev, url: undefined }));
                    }}
                  />
                  <FieldError id="webhook-url-error" message={formErrors.url} />
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="webhook-events">Events</Label>
                  <Input
                    id="webhook-events"
                    placeholder="comma-separated, e.g. task.completed"
                    value={events}
                    aria-invalid={formErrors.events ? true : undefined}
                    aria-describedby={
                      formErrors.events ? "webhook-events-error" : undefined
                    }
                    onChange={(e) => {
                      setEvents(e.target.value);
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
                    id="webhook-secret-ref"
                    placeholder="env:WEBHOOK_SIGNING_SECRET or file:/run/secrets/hook"
                    value={secretRef}
                    aria-invalid={formErrors.secretRef ? true : undefined}
                    aria-describedby={
                      formErrors.secretRef ? "webhook-secret-ref-error" : undefined
                    }
                    onChange={(e) => {
                      setSecretRef(e.target.value);
                      if (formErrors.secretRef)
                        setFormErrors((prev) => ({
                          ...prev,
                          secretRef: undefined,
                        }));
                    }}
                  />
                  <FieldError
                    id="webhook-secret-ref-error"
                    message={formErrors.secretRef}
                  />
                </div>

                <div className="flex justify-end">
                  <Button
                    className="uppercase"
                    size="sm"
                    onClick={handleCreate}
                    disabled={creating}
                    prefix={creating ? <Spinner /> : undefined}
                  >
                    {creating ? "Creating…" : "Create"}
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
          <div
            className={cn(
              themedBody,
              "relative w-full max-w-2xl border border-border bg-card shadow-2xl flex flex-col max-h-[90vh]",
            )}
          >
            <Button
              ghost
              size="icon"
              onClick={closeDeliveries}
              className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
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

      {!enabled && (
        <Card className="border-warning/50">
          <CardContent className="flex flex-col gap-4 py-6 text-sm sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <Webhook className="h-5 w-5 shrink-0 text-warning" />
              <div className="flex flex-col gap-1">
                <span className="font-medium">Webhook receiver disabled</span>
                <span className="text-muted-foreground">
                  Webhooks are their own gateway platform. Enable them here to
                  accept incoming HTTP events; chat channels are only needed
                  when a subscription delivers to Telegram, Discord, Slack, or
                  another channel.
                </span>
              </div>
            </div>
            <Button
              size="sm"
              className="uppercase shrink-0"
              onClick={handleEnableWebhooks}
              disabled={enabling}
              prefix={enabling ? <Spinner /> : <Webhook className="h-4 w-4" />}
            >
              {enabling ? "Enabling…" : "Enable webhooks"}
            </Button>
          </CardContent>
        </Card>
      )}

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
              size="sm"
              className="uppercase shrink-0"
              onClick={handleRestart}
              disabled={restarting}
              prefix={restarting ? <Spinner /> : <RotateCw className="h-4 w-4" />}
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
            <CardContent className="flex items-start gap-4 py-4">
              <div className={cn("flex-1 min-w-0", !sub.enabled && "opacity-60")}>
                <div className="flex items-center gap-2 mb-1 flex-wrap">
                  <span className="font-medium text-sm truncate">
                    {sub.name}
                  </span>
                  <Badge tone="outline">{sub.deliver}</Badge>
                  {sub.deliver_only && (
                    <Badge tone="secondary">deliver only</Badge>
                  )}
                  {!sub.enabled && <Badge tone="warning">disabled</Badge>}
                </div>

                {sub.description && (
                  <p className="text-xs text-muted-foreground mb-2">
                    {sub.description}
                  </p>
                )}

                <div className="flex items-center gap-1 flex-wrap mb-2">
                  {sub.events.length === 0 ? (
                    <Badge tone="secondary">(all)</Badge>
                  ) : (
                    sub.events.map((evt) => (
                      <Badge key={evt} tone="secondary">
                        {evt}
                      </Badge>
                    ))
                  )}
                </div>

                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="flex-1 min-w-0 truncate font-mono">
                    {sub.url}
                  </span>
                  <CopyButton value={sub.url} />
                </div>
              </div>

              <div className="flex items-center gap-1 shrink-0">
                <Button
                  ghost
                  size="icon"
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
                  ghost
                  size="icon"
                  title="Send test delivery"
                  aria-label="Send test delivery"
                  disabled={actionBusy === `${sub.name}:test` || !sub.enabled}
                  onClick={() => handleTestDelivery(sub)}
                >
                  {actionBusy === `${sub.name}:test` ? <Spinner /> : <Send />}
                </Button>
                <Button
                  ghost
                  size="icon"
                  title="Delivery history"
                  aria-label="Delivery history"
                  onClick={() => openDeliveries(sub)}
                >
                  <History />
                </Button>
                <Button
                  ghost
                  size="icon"
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
                  ghost
                  size="sm"
                  className="uppercase"
                  disabled={togglingName === sub.name}
                  onClick={() => handleToggleEnabled(sub.name, !sub.enabled)}
                >
                  {sub.enabled ? "Disable" : "Enable"}
                </Button>
                <Button
                  ghost
                  destructive
                  size="icon"
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
