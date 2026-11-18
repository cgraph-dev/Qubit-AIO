import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Label } from "@nous-research/ui/ui/components/label";
import { api } from "@/lib/api";
import type {
  McpHttpAuth,
  McpServerCreate,
  SkillInfo,
  SkillHubResult,
} from "@/lib/api";
import {
  buildMcpServerCreate,
  emptyMcpServerDraft,
  type McpServerDraft,
  type McpTransport,
} from "@/lib/mcp-server-create";
import { cn } from "@/lib/utils";
import { Button, Checkbox, Input, emitFx, showSakugaToast } from "@/sakuga/ui";

// Profile name rule mirrors the backend (`^[a-z0-9][a-z0-9_-]{0,63}$`).
const PROFILE_NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;

type StepId = "identity" | "model" | "skills" | "mcp" | "review";

const STEPS: { id: StepId; label: string }[] = [
  { id: "identity", label: "Identity" },
  { id: "model", label: "Model" },
  { id: "skills", label: "Skills" },
  { id: "mcp", label: "MCPs" },
  { id: "review", label: "Review" },
];

interface ModelChoice {
  provider: string;
  model: string;
  label: string;
}

/**
 * Dashboard-native, full-featured profile builder.
 *
 * Composes the same elements the standalone Models / Skills / MCP pages
 * manage — Name, Description, Model+Provider, Skills (built-in/optional +
 * hub), MCP servers — into one stepped create flow. Nothing is written to
 * disk until "Create profile" on the final step; the single POST /api/profiles
 * call commits model + MCPs + skill selection synchronously and spawns any
 * hub-skill installs (which the success toast reports as in-progress).
 *
 * Skills use REPLACE semantics: the default bundle is seeded server-side, then
 * every seeded skill the user did NOT keep is disabled. The "Start from full
 * bundle" toggle keeps everything (sends no keep list).
 */
export default function ProfileBuilderPage() {
  const navigate = useNavigate();
  const showToast = useCallback((message: string, kind?: "success" | "error") => {
    showSakugaToast(message);
    emitFx(kind === "error" ? "error" : "success");
  }, []);

  const [step, setStep] = useState<StepId>("identity");

  // ── Step 1: identity ──────────────────────────────────────────────
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  // ── Step 2: model ─────────────────────────────────────────────────
  const [modelChoices, setModelChoices] = useState<ModelChoice[] | null>(null);
  const [modelChoice, setModelChoice] = useState(""); // `${provider}\u0000${model}`
  const [modelFilter, setModelFilter] = useState("");
  const modelLoading = useRef(false);

  // ── Step 3: skills ────────────────────────────────────────────────
  const [skills, setSkills] = useState<SkillInfo[] | null>(null);
  // keepAll = true: don't send a keep list (full bundle stays active).
  const [keepAll, setKeepAll] = useState(true);
  const [keptSkills, setKeptSkills] = useState<Set<string>>(new Set());
  const [skillFilter, setSkillFilter] = useState("");
  const skillsLoading = useRef(false);
  // Hub search
  const [hubQuery, setHubQuery] = useState("");
  const [hubResults, setHubResults] = useState<SkillHubResult[]>([]);
  const [hubSearching, setHubSearching] = useState(false);
  const [hubSkills, setHubSkills] = useState<SkillHubResult[]>([]);

  // ── Step 4: MCPs ──────────────────────────────────────────────────
  const [mcpServers, setMcpServers] = useState<McpServerCreate[]>([]);
  const [mcpDraft, setMcpDraft] = useState<McpServerDraft>(emptyMcpServerDraft);

  // ── Submit ────────────────────────────────────────────────────────
  const [creating, setCreating] = useState(false);

  const nameValid = PROFILE_NAME_RE.test(name.trim());

  // Lazy-load model choices when the model step is first shown.
  const loadModels = useCallback(() => {
    if (modelChoices !== null || modelLoading.current) return;
    modelLoading.current = true;
    api
      .getModelOptions()
      .then((res) => {
        const flat: ModelChoice[] = [];
        for (const prov of res.providers ?? []) {
          for (const m of prov.models ?? []) {
            flat.push({
              provider: prov.slug,
              model: m,
              label: `${prov.name} · ${m}`,
            });
          }
        }
        setModelChoices(flat);
      })
      .catch(() => setModelChoices([]))
      .finally(() => {
        modelLoading.current = false;
      });
  }, [modelChoices]);

  const loadSkills = useCallback(() => {
    if (skills !== null || skillsLoading.current) return;
    skillsLoading.current = true;
    api
      .getSkills()
      .then((res) => {
        setSkills(res);
        // Default keep = all currently-enabled skills (matches the seeded set).
        setKeptSkills(new Set(res.filter((s) => s.enabled).map((s) => s.name)));
      })
      .catch(() => setSkills([]))
      .finally(() => {
        skillsLoading.current = false;
      });
  }, [skills]);

  useEffect(() => {
    if (step === "model") loadModels();
    if (step === "skills") loadSkills();
  }, [step, loadModels, loadSkills]);

  const runHubSearch = useCallback(() => {
    const q = hubQuery.trim();
    if (!q) return;
    setHubSearching(true);
    api
      .searchSkillsHub(q, "all", 20)
      .then((res) => setHubResults(res.results ?? []))
      .catch(() => setHubResults([]))
      .finally(() => setHubSearching(false));
  }, [hubQuery]);

  const toggleKeep = (skillName: string) => {
    setKeptSkills((prev) => {
      const next = new Set(prev);
      if (next.has(skillName)) next.delete(skillName);
      else next.add(skillName);
      return next;
    });
  };

  const addHubSkill = (r: SkillHubResult) => {
    setHubSkills((prev) =>
      prev.some((x) => x.identifier === r.identifier) ? prev : [...prev, r],
    );
  };
  const removeHubSkill = (identifier: string) =>
    setHubSkills((prev) => prev.filter((x) => x.identifier !== identifier));

  const addMcpDraft = () => {
    let entry: McpServerCreate;
    try {
      entry = buildMcpServerCreate(mcpDraft);
    } catch (error) {
      showToast(
        error instanceof Error ? error.message : "Invalid MCP server",
        "error",
      );
      return;
    }
    setMcpServers((prev) => [
      ...prev.filter((server) => server.name !== entry.name),
      entry,
    ]);
    setMcpDraft(emptyMcpServerDraft());
  };
  const removeMcp = (n: string) =>
    setMcpServers((prev) => prev.filter((s) => s.name !== n));

  const setMcpTransport = (transport: McpTransport) => {
    setMcpDraft((draft) =>
      transport === "http"
        ? { ...draft, transport, command: "", args: "", env: "" }
        : {
            ...draft,
            transport,
            url: "",
            httpAuth: "none",
            bearerToken: "",
          },
    );
  };

  const setMcpHttpAuth = (httpAuth: McpHttpAuth) => {
    setMcpDraft((draft) => ({
      ...draft,
      httpAuth,
      bearerToken: httpAuth === "header" ? draft.bearerToken : "",
    }));
  };

  const filteredModels = useMemo(() => {
    if (!modelChoices) return [];
    const f = modelFilter.trim().toLowerCase();
    if (!f) return modelChoices;
    return modelChoices.filter((c) => c.label.toLowerCase().includes(f));
  }, [modelChoices, modelFilter]);

  const filteredSkills = useMemo(() => {
    if (!skills) return [];
    const f = skillFilter.trim().toLowerCase();
    if (!f) return skills;
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(f) ||
        (s.description || "").toLowerCase().includes(f) ||
        (s.category || "").toLowerCase().includes(f),
    );
  }, [skills, skillFilter]);

  const pickedModel = useMemo(
    () =>
      modelChoice
        ? modelChoices?.find(
            (c) => `${c.provider}\u0000${c.model}` === modelChoice,
          )
        : undefined,
    [modelChoice, modelChoices],
  );

  const handleCreate = async () => {
    const n = name.trim();
    if (!PROFILE_NAME_RE.test(n)) {
      showToast("Invalid profile name (lowercase, digits, - and _)", "error");
      setStep("identity");
      return;
    }
    setCreating(true);
    try {
      const res = await api.createProfile({
        name: n,
        clone_from: null,
        description: description.trim() || undefined,
        provider: pickedModel?.provider,
        model: pickedModel?.model,
        mcp_servers: mcpServers.length ? mcpServers : undefined,
        keep_skills: keepAll ? undefined : Array.from(keptSkills),
        hub_skills: hubSkills.length
          ? hubSkills.map((s) => s.identifier)
          : undefined,
      });
      const pending = (res.hub_installs ?? []).filter((h) => h.pid).length;
      showToast(
        pending
          ? `Profile "${n}" created — ${pending} hub skill${pending === 1 ? "" : "s"} installing`
          : `Profile "${n}" created`,
        "success",
      );
      navigate("/profiles");
    } catch (e) {
      showToast(`Create failed: ${e}`, "error");
    } finally {
      setCreating(false);
    }
  };

  const stepIndex = STEPS.findIndex((s) => s.id === step);
  const canAdvance = step !== "identity" || nameValid;

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 p-4">
      <div className="flex items-center justify-between">
        <H2>New profile</H2>
        <Button variant="secondary" onClick={() => navigate("/profiles")}>
          Cancel
        </Button>
      </div>

      {/* Stepper */}
      <div className="flex items-center gap-2 text-sm">
        {STEPS.map((s, i) => (
          <Button
            key={s.id}
            variant={s.id === step ? "primary" : "secondary"}
            // Identity must be valid before jumping ahead.
            disabled={i > 0 && !nameValid}
            onClick={() => setStep(s.id)}
          >
            {i + 1}. {s.label}
          </Button>
        ))}
      </div>

      <Card>
        <CardContent className="space-y-4 p-5">
          {step === "identity" && (
            <div className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="pb-name">Profile name</Label>
                <Input
                  id="pb-name"
                  label="Profile name"
                  placeholder="coder"
                  value={name}
                  onChange={setName}
                  className="qsk-field"
                />
                {name && !nameValid && (
                  <p className="text-xs text-destructive">
                    Lowercase letters, digits, hyphens and underscores; must
                    start with a letter or digit.
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="pb-desc">Description (optional)</Label>
                <Input
                  id="pb-desc"
                  label="Description (optional)"
                  placeholder="What this agent profile is for"
                  value={description}
                  onChange={setDescription}
                  className="qsk-field"
                />
              </div>
            </div>
          )}

          {step === "model" && (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Pick the model+provider for this profile. Skip to use the
                default.
              </p>
              <Input
                label="Filter models"
                placeholder="Filter models…"
                value={modelFilter}
                onChange={setModelFilter}
                className="qsk-field"
              />
              {modelChoices === null ? (
                <p className="text-sm text-muted-foreground">Loading models…</p>
              ) : (
                <div className="max-h-72 space-y-1 overflow-y-auto">
                  <Button
                    variant={modelChoice === "" ? "primary" : "secondary"}
                    className="w-full text-left"
                    onClick={() => setModelChoice("")}
                  >
                    Use default (set later)
                  </Button>
                  {filteredModels.map((c) => {
                    const key = `${c.provider}\u0000${c.model}`;
                    return (
                      <Button
                        key={key}
                        variant={modelChoice === key ? "primary" : "secondary"}
                        className="w-full text-left"
                        onClick={() => setModelChoice(key)}
                      >
                        {c.label}
                      </Button>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {step === "skills" && (
            <div className="space-y-4">
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  label="Start from the full default skill bundle"
                  checked={keepAll}
                  onChange={(v) => setKeepAll(Boolean(v))}
                />
                Start from the full default skill bundle (recommended)
              </label>
              {!keepAll && (
                <div className="space-y-2">
                  <p className="text-xs text-muted-foreground">
                    Choose which built-in / optional skills to keep active.
                    Unchecked skills are disabled in the new profile.
                  </p>
                  <Input
                    label="Filter skills"
                    placeholder="Filter skills…"
                    value={skillFilter}
                    onChange={setSkillFilter}
                    className="qsk-field"
                  />
                  {skills === null ? (
                    <p className="text-sm text-muted-foreground">
                      Loading skills…
                    </p>
                  ) : (
                    <div className="max-h-56 space-y-1 overflow-y-auto">
                      {filteredSkills.map((s) => (
                        <label
                          key={s.name}
                          className="flex items-start gap-2 rounded px-2 py-1.5 text-sm hover:bg-muted"
                        >
                          <Checkbox
                            label={s.name}
                            checked={keptSkills.has(s.name)}
                            onChange={() => toggleKeep(s.name)}
                          />
                          <span className="flex-1">
                            <span className="font-medium">{s.name}</span>
                            {s.category && (
                              <Badge tone="secondary" className="ml-2">
                                {s.category}
                              </Badge>
                            )}
                            {s.description && (
                              <span className="block text-xs text-muted-foreground">
                                {s.description}
                              </span>
                            )}
                          </span>
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Skills hub */}
              <div className="space-y-2 border-t pt-4">
                <Label>Add from the skills hub</Label>
                <div className="flex gap-2">
                  <Input
                    label="Search the skills hub"
                    placeholder="Search the hub (e.g. linear, hyperliquid)…"
                    value={hubQuery}
                    onChange={setHubQuery}
                    onEnter={() => runHubSearch()}
                    className="qsk-field"
                  />
                  <Button variant="secondary" onClick={runHubSearch} disabled={hubSearching}>
                    {hubSearching ? "Searching…" : "Search"}
                  </Button>
                </div>
                {hubResults.length > 0 && (
                  <div className="max-h-48 space-y-1 overflow-y-auto">
                    {hubResults.map((r) => (
                      <div
                        key={r.identifier}
                        className="flex items-center justify-between rounded px-2 py-1.5 text-sm hover:bg-muted"
                      >
                        <span className="flex-1">
                          <span className="font-medium">{r.name}</span>
                          <Badge tone="secondary" className="ml-2">
                            {r.source}
                          </Badge>
                          {r.description && (
                            <span className="block text-xs text-muted-foreground">
                              {r.description}
                            </span>
                          )}
                        </span>
                        <Button variant="secondary" onClick={() => addHubSkill(r)}>
                          Add
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
                {hubSkills.length > 0 && (
                  <div className="flex flex-wrap gap-2 pt-1">
                    {hubSkills.map((r) => (
                      <Badge key={r.identifier} className="gap-1">
                        {r.name}
                        <Button
                          variant="secondary"
                          className="ml-1"
                          onClick={() => removeHubSkill(r.identifier)}
                          aria-label={`Remove ${r.name}`}
                        >
                          ×
                        </Button>
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {step === "mcp" && (
            <div className="space-y-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="space-y-1">
                  <h3 className="font-expanded text-base font-bold tracking-[0.04em]">
                    MCP servers
                  </h3>
                  <p className="text-sm text-muted-foreground">
                    Add MCP servers to give this profile access to external
                    tools and data.
                  </p>
                </div>
                <span
                  className="text-xs text-muted-foreground"
                  aria-live="polite"
                >
                  {mcpServers.length} configured
                </span>
              </div>

              <div className="space-y-4 border border-border bg-background/20 p-4 md:p-5">
                <h4 className="font-medium">Add server</h4>

                <div className="grid gap-4 md:grid-cols-2">
                  <div className="grid gap-1.5">
                    <Label htmlFor="pb-mcp-name">Server name</Label>
                    <Input
                      id="pb-mcp-name"
                      label="Server name"
                      placeholder="Enter server name"
                      value={mcpDraft.name}
                      onChange={(value) => setMcpDraft({ ...mcpDraft, name: value })}
                      className="qsk-field"
                    />
                  </div>
                  <div className="grid gap-1.5">
                    <Label>Transport</Label>
                    <div
                      className="grid grid-cols-2 border border-border bg-background/30 p-0.5"
                      role="group"
                      aria-label="MCP transport"
                    >
                      {(
                        [
                          ["http", "HTTP/SSE"],
                          ["stdio", "stdio"],
                        ] as const
                      ).map(([value, label]) => (
                        <Button
                          key={value}
                          variant={mcpDraft.transport === value ? "primary" : "secondary"}
                          aria-pressed={mcpDraft.transport === value}
                          onClick={() => setMcpTransport(value)}
                        >
                          {label}
                        </Button>
                      ))}
                    </div>
                  </div>
                </div>

                {mcpDraft.transport === "http" ? (
                  <>
                    <div className="grid gap-1.5">
                      <Label htmlFor="pb-mcp-url">URL</Label>
                      <Input
                        id="pb-mcp-url"
                        label="URL"
                        placeholder="https://example.com/mcp"
                        value={mcpDraft.url}
                        onChange={(value) => setMcpDraft({ ...mcpDraft, url: value })}
                        className="qsk-field"
                      />
                    </div>
                    <div className="grid gap-1.5">
                      <Label>Authentication</Label>
                      <div
                        className="grid grid-cols-3 border border-border bg-background/30 p-0.5 md:max-w-md"
                        role="group"
                        aria-label="HTTP authentication"
                      >
                        {(
                          [
                            ["none", "None"],
                            ["header", "Bearer token"],
                            ["oauth", "OAuth"],
                          ] as const
                        ).map(([value, label]) => (
                          <Button
                            key={value}
                            variant={mcpDraft.httpAuth === value ? "primary" : "secondary"}
                            aria-pressed={mcpDraft.httpAuth === value}
                            onClick={() => setMcpHttpAuth(value)}
                          >
                            {label}
                          </Button>
                        ))}
                      </div>
                    </div>
                    {mcpDraft.httpAuth === "header" && (
                      <div className="grid gap-1.5">
                        <Label htmlFor="pb-mcp-bearer-token">
                          Bearer token
                        </Label>
                        <Input
                          id="pb-mcp-bearer-token"
                          label="Bearer token"
                          type="password"
                          autoComplete="new-password"
                          placeholder="Token or Bearer token"
                          value={mcpDraft.bearerToken}
                          onChange={(value) => setMcpDraft({ ...mcpDraft, bearerToken: value })}
                          className="qsk-field"
                        />
                        <p className="text-xs text-muted-foreground">
                          Stored in the new profile&apos;s .env; config.yaml
                          keeps only an environment-variable reference.
                        </p>
                      </div>
                    )}
                    {mcpDraft.httpAuth === "oauth" && (
                      <p className="text-xs text-muted-foreground">
                        After creating the profile, open its MCP page and use
                        Authenticate to complete OAuth.
                      </p>
                    )}
                  </>
                ) : (
                  <>
                    <div className="grid gap-4 md:grid-cols-2">
                      <div className="grid gap-1.5">
                        <Label htmlFor="pb-mcp-command">Command</Label>
                        <Input
                          id="pb-mcp-command"
                          label="Command"
                          placeholder="npx"
                          value={mcpDraft.command}
                          onChange={(value) => setMcpDraft({ ...mcpDraft, command: value })}
                          className="qsk-field"
                        />
                      </div>
                      <div className="grid gap-1.5">
                        <Label htmlFor="pb-mcp-args">Arguments</Label>
                        <Input
                          id="pb-mcp-args"
                          label="Arguments"
                          placeholder="-y @modelcontextprotocol/server"
                          value={mcpDraft.args}
                          onChange={(value) => setMcpDraft({ ...mcpDraft, args: value })}
                          className="qsk-field"
                        />
                      </div>
                    </div>
                    <div className="grid gap-1.5">
                      <Label htmlFor="pb-mcp-env">
                        Environment (KEY=VALUE per line)
                      </Label>
                      <textarea
                        id="pb-mcp-env"
                        className="flex min-h-[80px] w-full border border-border bg-background/40 px-3 py-2 text-sm font-courier shadow-sm placeholder:text-muted-foreground focus-visible:border-foreground/25 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-foreground/30"
                        placeholder={"API_KEY=secret\nDEBUG=1"}
                        value={mcpDraft.env}
                        onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
                          setMcpDraft({ ...mcpDraft, env: e.target.value })
                        }
                      />
                    </div>
                  </>
                )}

                <div className="flex justify-end">
                  <Button onClick={addMcpDraft}>Add server</Button>
                </div>
              </div>

              {mcpServers.length > 0 && (
                <div className="space-y-2">
                  {mcpServers.map((s) => (
                    <div
                      key={s.name}
                      className="flex items-center justify-between gap-4 border border-border bg-muted/40 p-4 text-sm"
                    >
                      <span className="min-w-0">
                        <span className="flex flex-wrap items-center gap-2">
                          <span className="font-medium">{s.name}</span>
                          <Badge tone="outline">
                            {s.url ? "HTTP" : "stdio"}
                          </Badge>
                          {s.auth && (
                            <Badge tone="outline">
                              auth: {s.auth === "header" ? "bearer" : s.auth}
                            </Badge>
                          )}
                        </span>
                        <span className="mt-1 block break-all text-xs text-muted-foreground">
                          {s.url || [s.command, ...(s.args || [])].join(" ")}
                        </span>
                      </span>
                      <Button
                        variant="secondary"
                        className="shrink-0"
                        onClick={() => removeMcp(s.name)}
                      >
                        Remove
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          {step === "review" && (
            <div className="space-y-3 text-sm">
              <ReviewRow label="Name" value={name.trim() || "—"} />
              <ReviewRow
                label="Description"
                value={description.trim() || "—"}
              />
              <ReviewRow
                label="Model"
                value={pickedModel ? pickedModel.label : "Default (set later)"}
              />
              <ReviewRow
                label="Skills"
                value={
                  keepAll
                    ? "Full default bundle"
                    : `${keptSkills.size} built-in/optional kept` +
                      (hubSkills.length ? ` + ${hubSkills.length} hub` : "")
                }
              />
              {!keepAll && hubSkills.length > 0 && (
                <p className="pl-24 text-xs text-muted-foreground">
                  Hub: {hubSkills.map((s) => s.name).join(", ")}
                </p>
              )}
              {keepAll && hubSkills.length > 0 && (
                <ReviewRow
                  label="Hub skills"
                  value={hubSkills.map((s) => s.name).join(", ")}
                />
              )}
              <ReviewRow
                label="MCP servers"
                value={
                  mcpServers.length
                    ? mcpServers.map((s) => s.name).join(", ")
                    : "None"
                }
              />
            </div>
          )}
        </CardContent>
      </Card>

      {/* Nav buttons */}
      <div className="flex items-center justify-between">
        <Button
          variant="secondary"
          disabled={stepIndex === 0}
          onClick={() => setStep(STEPS[Math.max(0, stepIndex - 1)].id)}
        >
          Back
        </Button>
        {step === "review" ? (
          <Button onClick={handleCreate} disabled={creating || !nameValid}>
            {creating ? "Creating…" : "Create profile"}
          </Button>
        ) : (
          <Button
            disabled={!canAdvance}
            onClick={() =>
              setStep(STEPS[Math.min(STEPS.length - 1, stepIndex + 1)].id)
            }
          >
            Next
          </Button>
        )}
      </div>
    </div>
  );
}

function ReviewRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-3">
      <span className="w-24 shrink-0 text-muted-foreground">{label}</span>
      <span className="flex-1 break-words">{value}</span>
    </div>
  );
}
