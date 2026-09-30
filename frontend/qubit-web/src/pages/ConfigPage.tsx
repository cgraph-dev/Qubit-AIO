import { useEffect, useLayoutEffect, useRef, useState, useMemo } from "react";
import {
  Download,
  RotateCcw,
  Search,
  Upload,
  X,
  Settings2,
  Settings,
  Bot,
  Monitor,
  Palette,
  Users,
  Brain,
  Package,
  Lock,
  Globe,
  Mic,
  Volume2,
  Ear,
  ClipboardList,
  MessageCircle,
  Wrench,
  FileQuestion,
  Filter,
  Cloud,
  Sparkles,
  LayoutDashboard,
  BookOpen,
  Route,
  History,
  Shield,
  FileOutput,
  RefreshCw,
} from "lucide-react";
import { api, ConfirmRequiredError } from "@/lib/api";
import { getNestedValue, setNestedValue } from "@/lib/nested";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { AutoField } from "@/components/AutoField";
import { Button } from "@nous-research/ui/ui/components/button";
import { ListItem } from "@nous-research/ui/ui/components/list-item";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Card, CardContent, CardHeader, CardTitle } from "@nous-research/ui/ui/components/card";
import { ConfirmDialog } from "@nous-research/ui/ui/components/confirm-dialog";
import { Input } from "@nous-research/ui/ui/components/input";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { useI18n } from "@/i18n";
import { usePageHeader } from "@/contexts/usePageHeader";
import { PluginSlot } from "@/plugins";

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const CATEGORY_ICONS: Record<
  string,
  React.ComponentType<{ className?: string }>
> = {
  general: Settings,
  agent: Bot,
  terminal: Monitor,
  display: Palette,
  delegation: Users,
  memory: Brain,
  compression: Package,
  security: Lock,
  browser: Globe,
  voice: Mic,
  tts: Volume2,
  stt: Ear,
  logging: ClipboardList,
  discord: MessageCircle,
  auxiliary: Wrench,
  bedrock: Cloud,
  curator: Sparkles,
  kanban: LayoutDashboard,
  model_catalog: BookOpen,
  openrouter: Route,
  sessions: History,
  tool_loop_guardrails: Shield,
  tool_output: FileOutput,
  updates: RefreshCw,
};

function CategoryIcon({
  category,
  className,
}: {
  category: string;
  className?: string;
}) {
  const Icon = CATEGORY_ICONS[category] ?? FileQuestion;
  return <Icon className={className ?? "h-4 w-4"} />;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

/** Human-readable error for a failed confirm-gated settings write. */
function describeConfirmError(e: unknown): string {
  if (e instanceof ConfirmRequiredError) {
    return e.hasConfirmationId ? "confirmation declined" : e.detail;
  }
  return `${e}`;
}

/** Extract the backend typed refusal code from a fetchJSON error
 *  (`"<status>: {json envelope}"`). Returns null when there is no code. */
function typedErrorCode(e: unknown): string | null {
  const message = e instanceof Error ? e.message : String(e);
  const match = message.match(/^\d+:\s*(\{[\s\S]*\})$/);
  if (!match) return null;
  try {
    const envelope = JSON.parse(match[1]) as { code?: unknown };
    return typeof envelope.code === "string" ? envelope.code : null;
  } catch {
    return null;
  }
}

export default function ConfigPage() {
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [schema, setSchema] = useState<Record<
    string,
    Record<string, unknown>
  > | null>(null);
  const [categoryOrder, setCategoryOrder] = useState<string[]>([]);
  const [defaults, setDefaults] = useState<Record<string, unknown> | null>(
    null,
  );
  const [saving, setSaving] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [configPath, setConfigPath] = useState<string | null>(null);
  const [activeCategory, setActiveCategory] = useState<string>("");
  const [confirmReset, setConfirmReset] = useState(false);
  // Confirm gates for staged writes: the deferral for api.saveConfig's
  // confirm callback, plus the copy shown in the dialog.
  const [confirmSave, setConfirmSave] = useState(false);
  const [confirmSaveDetail, setConfirmSaveDetail] = useState("");
  // Set on every real save: the dashboard record is live immediately, but
  // running processes read config at boot — the banner below says so and
  // offers the gateway restart. Cleared on reload.
  const [restartNeeded, setRestartNeeded] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [verifyNote, setVerifyNote] = useState("");
  const saveDecisionRef = useRef<((ok: boolean) => void) | null>(null);
  const { toast, showToast } = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { t } = useI18n();
  const { setEnd } = usePageHeader();

  useLayoutEffect(() => {
    if (!config || !schema) {
      setEnd(null);
      return;
    }
    setEnd(
      <div className="relative w-full min-w-0 sm:max-w-xs">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
        <Input
          className="h-8 pl-8 pr-7 text-xs"
          placeholder={t.common.search}
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
        {searchQuery && (
          <Button
            ghost
            size="xs"
            className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            onClick={() => setSearchQuery("")}
            aria-label={t.common.clear}
          >
            <X />
          </Button>
        )}
      </div>,
    );
    return () => setEnd(null);
  }, [config, schema, searchQuery, setEnd, t.common.clear, t.common.search]);

  function prettyCategoryName(cat: string): string {
    const key = cat as keyof typeof t.config.categories;
    if (t.config.categories[key]) return t.config.categories[key];
    return cat.charAt(0).toUpperCase() + cat.slice(1);
  }

  useEffect(() => {
    api
      .getConfig()
      .then(setConfig)
      .catch(() => {});
    api
      .getSchema()
      .then((resp) => {
        // memory.provider has a dedicated management UI on the Plugins page
        // (provider cards + guided setup/switch flow). Hide it from the
        // generic config form so the two surfaces don't fight; the schema
        // keeps the field for other consumers (Desktop settings).
        const fields = { ...resp.fields } as Record<
          string,
          Record<string, unknown>
        >;
        delete fields["memory.provider"];
        setSchema(fields);
        setCategoryOrder(resp.category_order ?? []);
      })
      .catch(() => {});
    api
      .getDefaults()
      .then(setDefaults)
      .catch(() => {});
    // /api/status's config_path is the machine-global path the vendored
    // runtime reads. Raw-YAML export is policy-refused (SECRET_NEVER_SERVED),
    // so the header path comes from status, not /api/config/raw.
    api
      .getStatus()
      .then((resp) => setConfigPath((prev) => prev ?? resp.config_path))
      .catch(() => {});
  }, []);

  // Set active category when categories load
  useEffect(() => {
    if (categoryOrder.length > 0 && !activeCategory) {
      setActiveCategory(categoryOrder[0]);
    }
  }, [categoryOrder, activeCategory]);

  /* ---- Categories ---- */
  const categories = useMemo(() => {
    if (!schema) return [];
    const allCats = [
      ...new Set(
        Object.values(schema).map((s) => String(s.category ?? "general")),
      ),
    ];
    const ordered = categoryOrder.filter((c) => allCats.includes(c));
    const extra = allCats.filter((c) => !categoryOrder.includes(c)).sort();
    return [...ordered, ...extra];
  }, [schema, categoryOrder]);

  /* ---- Category field counts ---- */
  const categoryCounts = useMemo(() => {
    if (!schema) return {};
    const counts: Record<string, number> = {};
    for (const s of Object.values(schema)) {
      const cat = String(s.category ?? "general");
      counts[cat] = (counts[cat] || 0) + 1;
    }
    return counts;
  }, [schema]);

  /* ---- Search ---- */
  const isSearching = searchQuery.trim().length > 0;
  const lowerSearch = searchQuery.toLowerCase();

  const searchMatchedFields = useMemo(() => {
    if (!isSearching || !schema) return [];
    return Object.entries(schema).filter(([key, s]) => {
      const label = key.split(".").pop() ?? key;
      const humanLabel = label.replace(/_/g, " ");
      return (
        key.toLowerCase().includes(lowerSearch) ||
        humanLabel.toLowerCase().includes(lowerSearch) ||
        String(s.category ?? "")
          .toLowerCase()
          .includes(lowerSearch) ||
        String(s.description ?? "")
          .toLowerCase()
          .includes(lowerSearch)
      );
    });
  }, [isSearching, lowerSearch, schema]);

  /* ---- Active tab fields ---- */
  const activeFields = useMemo(() => {
    if (!schema || isSearching) return [];
    return Object.entries(schema).filter(
      ([, s]) => String(s.category ?? "general") === activeCategory,
    );
  }, [schema, activeCategory, isSearching]);

  /* ---- Handlers ---- */
  // Qubit confirm-gates settings writes: the first PUT stages a typed
  // CONFIRM_REQUIRED envelope. api.saveConfig owns the round-trip; the
  // in-app ConfirmDialog (not window.confirm) collects the human decision
  // through this deferred promise.
  const commitConfig = async (next: Record<string, unknown>) => {
    await api.saveConfig(next, undefined, false, (detail) => {
      setConfirmSaveDetail(detail);
      setConfirmSave(true);
      return new Promise<boolean>((resolve) => {
        saveDecisionRef.current = resolve;
      });
    });
    showToast(t.config.configSaved, "success");
  };

  const decideSave = (ok: boolean) => {
    setConfirmSave(false);
    saveDecisionRef.current?.(ok);
    saveDecisionRef.current = null;
  };

  const handleSave = async () => {
    if (!config) return;
    setSaving(true);
    try {
      await commitConfig(config);
      setRestartNeeded(true);
    } catch (e) {
      // CONFIG_NO_FIELDS means the server holds exactly what we sent (an
      // unchanged whole-tree Save): converge on the server record and say so
      // instead of reporting a failure.
      if (typedErrorCode(e) === "CONFIG_NO_FIELDS") {
        try {
          setConfig(await api.getConfig());
        } catch {
          /* keep the local tree when the read-back fails */
        }
        showToast("Settings already up to date.", "success");
        return;
      }
      showToast(`${t.config.failedToSave}: ${describeConfirmError(e)}`, "error");
      return;
    } finally {
      setSaving(false);
    }
    // Read-back proof: the toast above says the server holds the values, so
    // converge the form on what the server actually serves (it drops unchanged
    // leaves and never persists redacted placeholders). Without this the form
    // and a post-refresh render can disagree until the next reload.
    try {
      setConfig(await api.getConfig());
    } catch {
      /* the save succeeded; a failed read-back keeps the local tree */
    }
  };

  const handleReset = () => {
    if (!defaults || !config) return;
    // Scope the reset to what the user is currently looking at:
    //   - search mode → the matched fields
    //   - form mode   → the active category's fields
    // Resetting the whole config here was a footgun (issue reported by @ykmfb001):
    // the button sits next to the category tabs and users reasonably assumed
    // "reset this tab", not "wipe my entire config.yaml".
    const scopedFields = isSearching ? searchMatchedFields : activeFields;
    if (scopedFields.length === 0) return;
    setConfirmReset(true);
  };

  const executeReset = () => {
    if (!defaults || !config) return;
    setConfirmReset(false);
    const scopedFields = isSearching ? searchMatchedFields : activeFields;
    if (scopedFields.length === 0) return;
    const scopeLabel = isSearching
      ? t.config.searchResults
      : prettyCategoryName(activeCategory);
    let next: Record<string, unknown> = config;
    for (const [key] of scopedFields) {
      next = setNestedValue(next, key, getNestedValue(defaults, key));
    }
    setConfig(next);
    showToast(
      t.config.resetScopeToast.replace("{scope}", scopeLabel),
      "success",
    );
  };

  const handleExport = () => {
    if (!config) return;
    const blob = new Blob([JSON.stringify(config, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "qubit-config.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  // Import is not just a client-side preview: the parsed tree is staged to
  // the canonical config through the same confirm-gated write as Save, so an
  // "imported" toast always means the backend holds the values.
  const handleImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async () => {
      let imported: Record<string, unknown>;
      try {
        imported = JSON.parse(reader.result as string) as Record<
          string,
          unknown
        >;
      } catch {
        showToast(t.config.invalidJson, "error");
        return;
      }
      if (!imported || typeof imported !== "object" || Array.isArray(imported)) {
        showToast(t.config.invalidJson, "error");
        return;
      }
      setSaving(true);
      try {
        await commitConfig(imported);
        setRestartNeeded(true);
        // Converge on the server record, not the file bytes: the backend
        // drops unchanged leaves, refuses secret-shaped values, and rejects
        // unknown keys, so an optimistic setConfig(imported) can disagree
        // with the next refresh.
        try {
          setConfig(await api.getConfig());
        } catch {
          setConfig(imported);
        }
        showToast(t.config.configImported, "success");
      } catch (err) {
        showToast(
          `${t.config.failedToSave}: ${describeConfirmError(err)}`,
          "error",
        );
      } finally {
        setSaving(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
    };
    reader.readAsText(file);
  };

  /* ---- Loading ---- */
  if (!config || !schema) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  /* ---- Render field list (shared between search & normal) ---- */
  const renderFields = (
    fields: [string, Record<string, unknown>][],
    showCategory = false,
  ) => {
    let lastSection = "";
    let lastCat = "";
    return fields.map(([key, s]) => {
      const parts = key.split(".");
      const section = parts.length > 1 ? parts[0] : "";
      const cat = String(s.category ?? "general");
      const showCatBadge = showCategory && cat !== lastCat;
      const showSection =
        !showCategory &&
        section &&
        section !== lastSection &&
        section !== activeCategory;
      lastSection = section;
      lastCat = cat;

      return (
        <div key={key}>
          {showCatBadge && (
            <div className="flex items-center gap-2 pt-4 pb-2 first:pt-0">
              <CategoryIcon
                category={cat}
                className="h-4 w-4 text-muted-foreground"
              />
              <span className="font-mondwest text-display text-xs font-semibold tracking-wider text-muted-foreground">
                {prettyCategoryName(cat)}
              </span>
              <div className="flex-1 border-t border-border" />
            </div>
          )}
          {showSection && (
            <div className="flex items-center gap-2 pt-4 pb-2 first:pt-0">
              <span className="font-mondwest text-display text-xs font-semibold tracking-wider text-muted-foreground">
                {section.replace(/_/g, " ")}
              </span>
              <div className="flex-1 border-t border-border" />
            </div>
          )}
          <div className="py-1">
            <AutoField
              schemaKey={key}
              schema={s}
              value={getNestedValue(config, key)}
              onChange={(v) => setConfig(setNestedValue(config, key, v))}
            />
          </div>
        </div>
      );
    });
  };

  return (
    <div className="flex flex-col gap-4">
      <PluginSlot name="config:top" />
      <Toast toast={toast} />

      {(() => {
        // Two model slots by design (terminal /chat pin vs main agent slot):
        // a /chat switch never rewrites the main leaf, so show both live
        // instead of letting the main value look stale.
        const terminalModel = String(
          getNestedValue(config ?? {}, "terminal.model") ?? "",
        );
        const mainModel = String(getNestedValue(config ?? {}, "model") ?? "");
        if (!terminalModel) return null;
        return (
          <Card>
            <CardContent className="flex flex-col gap-1 p-4 text-sm">
              <div>
                Main model (this page):{" "}
                <span className="font-medium">{mainModel || "-"}</span>
              </div>
              <div>
                Terminal (/chat, live):{" "}
                <span className="font-medium">{terminalModel}</span>
                {terminalModel !== mainModel && (
                  <span className="text-muted-foreground">
                    {" "}
                    - separate slots by design; switch /chat from /chat or
                    the Models page
                  </span>
                )}
              </div>
            </CardContent>
          </Card>
        );
      })()}

      {restartNeeded && (
        <Card className="border-warning/50">
          <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-sm">
              Settings saved — the dashboard record is live now. Running
              processes read config at boot: restart the gateway, and restart
              the dashboard service{" "}
              <code className="bg-muted/50 px-1">
                systemctl --user restart qubit-voicebridge
              </code>{" "}
              to apply everywhere.
            </div>
            <Button
              size="sm"
              className="uppercase"
              disabled={restarting}
              onClick={() => {
                setRestarting(true);
                api
                  .restartGateway()
                  .then(() => {
                    showToast("Gateway restarting…", "success");
                    setRestartNeeded(false);
                  })
                  .catch((e) => showToast(`Failed to restart: ${e}`, "error"))
                  .finally(() => setRestarting(false));
              }}
            >
              {restarting ? "Restarting…" : "Restart gateway"}
            </Button>
            <Button
              size="sm"
              className="uppercase"
              disabled={verifying}
              onClick={() => {
                setVerifying(true);
                setVerifyNote("");
                api
                  .getConfigEffective()
                  .then((res) => {
                    const stale = Object.entries(res.services || {})
                      .filter(([, s]) => s.state === "stale")
                      .map(([name]) => name);
                    if (res.verdict === "active") {
                      showToast("Verified live everywhere.", "success");
                      setVerifyNote("Verified live everywhere.");
                      setRestartNeeded(false);
                    } else {
                      const msg =
                        stale.length > 0
                          ? `Still stale: ${stale.join(", ")}. Restart, then verify again.`
                          : `Verify inconclusive (${res.verdict}).`;
                      showToast(msg, "error");
                      setVerifyNote(msg);
                    }
                  })
                  .catch((e) => {
                    showToast(`Verify failed: ${e}`, "error");
                    setVerifyNote(`Verify failed: ${e}`);
                  })
                  .finally(() => setVerifying(false));
              }}
            >
              {verifying ? "Verifying…" : "Verify"}
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
        <div className="flex min-w-0 items-center gap-2 sm:flex-1">
          <Settings2 className="h-4 w-4 shrink-0 text-muted-foreground" />
          <code className="min-w-0 flex-1 break-words text-xs text-muted-foreground bg-muted/50 px-2 py-0.5">
            {configPath ?? t.config.configPath}
          </code>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 sm:shrink-0">
          <Button
            ghost
            size="icon"
            onClick={handleExport}
            title={t.config.exportConfig}
            aria-label={t.config.exportConfig}
          >
            <Download />
          </Button>
          <Button
            ghost
            size="icon"
            onClick={() => fileInputRef.current?.click()}
            title={t.config.importConfig}
            aria-label={t.config.importConfig}
          >
            <Upload />
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".json"
            className="hidden"
            onChange={handleImport}
          />
          {(() => {
            const resetScopeLabel = isSearching
              ? t.config.searchResults
              : prettyCategoryName(activeCategory);
            const resetTitle = t.config.resetScopeTooltip.replace(
              "{scope}",
              resetScopeLabel,
            );
            return (
              <Button
                ghost
                size="icon"
                onClick={handleReset}
                title={resetTitle}
                aria-label={resetTitle}
              >
                <RotateCcw />
              </Button>
            );
          })()}

          <div className="w-px h-5 bg-border mx-1" />

          <Button
            size="sm"
            className="uppercase"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? t.common.saving : t.common.save}
          </Button>
        </div>
      </div>

      <div className="flex flex-col sm:flex-row gap-4">
          <aside aria-label={t.config.filters} className="sm:w-56 sm:shrink-0">
            <div className="sm:sticky sm:top-4">
              <div className="flex flex-col border border-border bg-muted/20">
                <div className="hidden sm:flex items-center gap-2 px-3 py-2 border-b border-border">
                  <Filter className="h-3 w-3 text-text-tertiary" />
                  <span className="font-mondwest text-display text-xs tracking-[0.12em] text-text-secondary">
                    {t.config.filters}
                  </span>
                </div>

                <div className="hidden sm:block px-3 pt-2 pb-1 font-mondwest text-display text-xs tracking-[0.12em] text-text-tertiary">
                  {t.config.sections}
                </div>

                <div className="flex sm:flex-col gap-1 sm:gap-px p-2 sm:pt-1 overflow-x-auto sm:overflow-x-visible scrollbar-none sm:max-h-[calc(100vh-260px)] sm:overflow-y-auto">
                  {categories.map((cat) => {
                    const isActive = !isSearching && activeCategory === cat;

                    return (
                      <ListItem
                        key={cat}
                        active={isActive}
                        onClick={() => {
                          setSearchQuery("");
                          setActiveCategory(cat);
                        }}
                        className="rounded-none whitespace-nowrap px-2 py-1 text-xs"
                      >
                        <CategoryIcon
                          category={cat}
                          className="h-3.5 w-3.5 shrink-0"
                        />
                        <span className="flex-1 truncate">
                          {prettyCategoryName(cat)}
                        </span>
                        <span
                          className={`text-xs tabular-nums ${
                            isActive
                              ? "text-text-secondary"
                              : "text-text-tertiary"
                          }`}
                        >
                          {categoryCounts[cat] || 0}
                        </span>
                      </ListItem>
                    );
                  })}
                </div>
              </div>
            </div>
          </aside>

          <div className="flex-1 min-w-0">
            {isSearching ? (
              <Card>
                <CardHeader className="py-3 px-4">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-sm flex items-center gap-2">
                      <Search className="h-4 w-4" />
                      {t.config.searchResults}
                    </CardTitle>
                    <Badge tone="secondary" className="text-xs">
                      {searchMatchedFields.length}{" "}
                      {t.config.fields.replace(
                        "{s}",
                        searchMatchedFields.length !== 1 ? "s" : "",
                      )}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent className="grid gap-2 px-4 pb-4">
                  {searchMatchedFields.length === 0 ? (
                    <p className="text-sm text-muted-foreground text-center py-8">
                      {t.config.noFieldsMatch.replace("{query}", searchQuery)}
                    </p>
                  ) : (
                    renderFields(searchMatchedFields, true)
                  )}
                </CardContent>
              </Card>
            ) : (
              /* Active category */
              <Card>
                <CardHeader className="py-3 px-4">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-sm flex items-center gap-2">
                      <CategoryIcon
                        category={activeCategory}
                        className="h-4 w-4"
                      />
                      {prettyCategoryName(activeCategory)}
                    </CardTitle>
                    <Badge tone="secondary" className="text-xs">
                      {activeFields.length}{" "}
                      {t.config.fields.replace(
                        "{s}",
                        activeFields.length !== 1 ? "s" : "",
                      )}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent className="grid gap-2 px-4 pb-4">
                  {renderFields(activeFields)}
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      <PluginSlot name="config:bottom" />
      <ConfirmDialog
        open={confirmReset}
        onCancel={() => setConfirmReset(false)}
        onConfirm={executeReset}
        title={t.config.confirmResetScope.replace(
          "{scope}",
          isSearching
            ? t.config.searchResults
            : prettyCategoryName(activeCategory),
        )}
        description={`This will reset ${
          (isSearching ? searchMatchedFields : activeFields).length
        } field(s) to their default values.`}
        destructive
        confirmLabel={t.config.resetDefaults}
      />
      <ConfirmDialog
        open={confirmSave}
        onCancel={() => decideSave(false)}
        onConfirm={() => decideSave(true)}
        title="Apply these settings to Qubit?"
        description={
          confirmSaveDetail || "This stages a confirmed settings write."
        }
        confirmLabel={t.common.save}
      />
    </div>
  );
}
