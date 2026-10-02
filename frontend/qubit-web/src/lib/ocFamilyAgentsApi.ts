// ROUTES MANIFEST — Qubit agents/nodes/devices family (integrator implements verbatim).
// Every HTTP path used by this module, with its method. All paths live under
// /api/qubit/<family>/<action>. No other paths are fetched from here.
//
//   Agent workspace (read-only browse + run status):
//     GET  /api/qubit/agent-workspace/list        (agents.workspace.list)
//     GET  /api/qubit/agent-workspace/get         (agents.workspace.get, ?path=)
//     GET  /api/qubit/agent-workspace/run-status  (agent.wait, ?run_id=)
//
//   Nodes (list/describe + confirm-gated mutations):
//     GET  /api/qubit/nodes/list                  (nodes.list)
//     GET  /api/qubit/nodes/describe              (nodes.describe, ?node_id=)
//     POST /api/qubit/nodes/enroll                (nodes.enroll, confirm-gated)
//     POST /api/qubit/nodes/approve               (nodes.approve, confirm-gated)
//     POST /api/qubit/nodes/revoke                (nodes.revoke, confirm-gated)
//     POST /api/qubit/nodes/remove                (nodes.remove, confirm-gated)
//     GET  /api/qubit/nodes/capabilities          (nodes.capabilities, ?node_id=)
//     POST /api/qubit/nodes/dispatch              (nodes.dispatch, confirm-gated)
//     POST /api/qubit/nodes/rename                (nodes.rename, confirm-gated)
//
//   Devices (setup codes + pairings + tokens, confirm-gated mutations):
//     GET  /api/qubit/devices/setup-status        (devices.setup-status)
//     POST /api/qubit/devices/setup-code-mint     (devices.setup-code-mint, confirm-gated)
//     GET  /api/qubit/devices/pairings            (devices.pairings, includes direct-message threads)
//     POST /api/qubit/devices/approve             (devices.approve, confirm-gated)
//     POST /api/qubit/devices/reject              (devices.reject, confirm-gated)
//     POST /api/qubit/devices/remove              (devices.remove, confirm-gated)
//     POST /api/qubit/devices/token-rotate        (devices.token-rotate, confirm-gated)
//     POST /api/qubit/devices/token-revoke        (devices.token-revoke, confirm-gated)
//     POST /api/qubit/devices/enroll              (devices.enroll, confirm-gated)
//     GET  /api/qubit/devices/identity            (devices.identity, ?device_id=)
//
// Secret-value rule: no endpoint in this family ever returns a raw secret, and
// no caller here ever renders one. Presence is reported as booleans
// (code_present / secret_set) plus metadata (id, label, scopes, expiry).

import { fetchJSON, runMutationWithConfirm } from "@/lib/api";

/** Prompt shown when the backend stages a mutation (defaults to window.confirm). */
export type ConfirmPrompt = (
  detail: string,
) => boolean | Promise<boolean>;

// ── Agent workspace ─────────────────────────────────────────────────────

export interface WorkspaceFile {
  path: string;
  size_bytes?: number;
  readable: boolean;
  unreadable_reason?: string;
}

export interface WorkspaceListResponse {
  files: WorkspaceFile[];
  authority?: string;
}

export interface WorkspaceFileResponse {
  path: string;
  content: string;
  truncated?: boolean;
}

export interface AgentRunStatus {
  run_id: string;
  state: string;
  detail?: string;
}

/** Browse the agent workspace file roster (agents.workspace.list). */
export function listWorkspaceFiles(): Promise<WorkspaceListResponse> {
  return fetchJSON<WorkspaceListResponse>("/api/qubit/agent-workspace/list");
}

/** Open one workspace file for reading (agents.workspace.get). */
export function getWorkspaceFile(path: string): Promise<WorkspaceFileResponse> {
  return fetchJSON<WorkspaceFileResponse>(
    `/api/qubit/agent-workspace/get?path=${encodeURIComponent(path)}`,
  );
}

/** Read one agent run's status (agent.wait). */
export function getAgentRunStatus(runId: string): Promise<AgentRunStatus> {
  return fetchJSON<AgentRunStatus>(
    `/api/qubit/agent-workspace/run-status?run_id=${encodeURIComponent(runId)}`,
  );
}

// ── Nodes ───────────────────────────────────────────────────────────────

export interface NodeRow {
  id: string;
  name?: string;
  state?: string;
  manageable: boolean;
  manage_reason?: string;
}

export interface NodeDetail {
  id: string;
  name?: string;
  state?: string;
  capabilities?: string[];
  last_seen?: string;
}

export interface NodesListResponse {
  nodes: NodeRow[];
  authority?: string;
}

export interface NodeMutationResponse {
  ok: boolean;
  node_id: string;
  state?: string;
}

/** List enrolled nodes (nodes.list). */
export function listNodes(): Promise<NodesListResponse> {
  return fetchJSON<NodesListResponse>("/api/qubit/nodes/list");
}

/** Describe one node (nodes.describe). */
export function describeNode(nodeId: string): Promise<{ node: NodeDetail }> {
  return fetchJSON<{ node: NodeDetail }>(
    `/api/qubit/nodes/describe?node_id=${encodeURIComponent(nodeId)}`,
  );
}

/** Enroll a node (nodes.enroll, confirm-gated). */
export function enrollNode(
  input: { node_id: string; label?: string },
  confirm?: ConfirmPrompt,
): Promise<NodeMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<NodeMutationResponse>("/api/qubit/nodes/enroll", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      }),
    confirm,
  );
}

/** Approve a pending node (nodes.approve, confirm-gated). */
export function approveNode(
  nodeId: string,
  confirm?: ConfirmPrompt,
): Promise<NodeMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<NodeMutationResponse>("/api/qubit/nodes/approve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ node_id: nodeId }),
      }),
    confirm,
  );
}

/** Revoke a node (nodes.revoke, confirm-gated). */
export function revokeNode(
  nodeId: string,
  confirm?: ConfirmPrompt,
): Promise<NodeMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<NodeMutationResponse>("/api/qubit/nodes/revoke", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ node_id: nodeId }),
      }),
    confirm,
  );
}

/** Remove a node (nodes.remove, confirm-gated). */
export function removeNode(
  nodeId: string,
  confirm?: ConfirmPrompt,
): Promise<NodeMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<NodeMutationResponse>("/api/qubit/nodes/remove", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ node_id: nodeId }),
      }),
    confirm,
  );
}

/** Refresh one node's capability roster (nodes.capabilities, read-only). */
export function refreshNodeCapabilities(
  nodeId: string,
): Promise<{ node_id: string; capabilities: string[] }> {
  return fetchJSON<{ node_id: string; capabilities: string[] }>(
    `/api/qubit/nodes/capabilities?node_id=${encodeURIComponent(nodeId)}`,
  );
}

/** Dispatch work to a node (nodes.dispatch, confirm-gated). */
export function dispatchNode(
  input: { node_id: string; task: string },
  confirm?: ConfirmPrompt,
): Promise<NodeMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<NodeMutationResponse>("/api/qubit/nodes/dispatch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      }),
    confirm,
  );
}

/** Rename a node (nodes.rename, confirm-gated). Re-enrollment may be required. */
export function renameNode(
  input: { node_id: string; name: string },
  confirm?: ConfirmPrompt,
): Promise<NodeMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<NodeMutationResponse>("/api/qubit/nodes/rename", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      }),
    confirm,
  );
}

// ── Devices ─────────────────────────────────────────────────────────────

export interface DeviceSetupStatus {
  enabled: boolean;
  pending_codes: number;
  paired_devices: number;
}

export interface SetupCodeMintResponse {
  id: string;
  label: string;
  scopes: string[];
  expires_at?: string;
  /** Presence only — the code value itself is never served or rendered. */
  code_present: boolean;
}

export interface DevicePairing {
  id: string;
  label: string;
  /** Pairing channel, including direct-message threads ("dm"). */
  kind: string;
  state: string;
  manageable: boolean;
  manage_reason?: string;
  secret_set: boolean;
}

export interface DeviceIdentity {
  device_id: string;
  label: string;
  enrolled_at?: string;
  scopes: string[];
}

export interface DeviceMutationResponse {
  ok: boolean;
  id: string;
  state?: string;
}

/** Read device setup state (devices.setup-status). */
export function getDeviceSetupStatus(): Promise<DeviceSetupStatus> {
  return fetchJSON<DeviceSetupStatus>("/api/qubit/devices/setup-status");
}

/** Mint a device setup code (devices.setup-code-mint, confirm-gated). */
export function mintDeviceSetupCode(
  input: { label: string; scopes: string[]; ttl_seconds: number },
  confirm?: ConfirmPrompt,
): Promise<SetupCodeMintResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<SetupCodeMintResponse>("/api/qubit/devices/setup-code-mint", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      }),
    confirm,
  );
}

/** List device pairings, including direct-message threads (devices.pairings). */
export function listDevicePairings(): Promise<{ pairings: DevicePairing[] }> {
  return fetchJSON<{ pairings: DevicePairing[] }>("/api/qubit/devices/pairings");
}

/** Approve a pairing (devices.approve, confirm-gated). */
export function approveDevicePairing(
  pairingId: string,
  confirm?: ConfirmPrompt,
): Promise<DeviceMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<DeviceMutationResponse>("/api/qubit/devices/approve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pairing_id: pairingId }),
      }),
    confirm,
  );
}

/** Reject a pairing (devices.reject, confirm-gated). */
export function rejectDevicePairing(
  pairingId: string,
  confirm?: ConfirmPrompt,
): Promise<DeviceMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<DeviceMutationResponse>("/api/qubit/devices/reject", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pairing_id: pairingId }),
      }),
    confirm,
  );
}

/** Remove a pairing (devices.remove, confirm-gated). */
export function removeDevicePairing(
  pairingId: string,
  confirm?: ConfirmPrompt,
): Promise<DeviceMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<DeviceMutationResponse>("/api/qubit/devices/remove", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pairing_id: pairingId }),
      }),
    confirm,
  );
}

/** Rotate a device token (devices.token-rotate, confirm-gated). */
export function rotateDeviceToken(
  pairingId: string,
  confirm?: ConfirmPrompt,
): Promise<DeviceMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<DeviceMutationResponse>("/api/qubit/devices/token-rotate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pairing_id: pairingId }),
      }),
    confirm,
  );
}

/** Revoke a device token (devices.token-revoke, confirm-gated). */
export function revokeDeviceToken(
  pairingId: string,
  confirm?: ConfirmPrompt,
): Promise<DeviceMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<DeviceMutationResponse>("/api/qubit/devices/token-revoke", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pairing_id: pairingId }),
      }),
    confirm,
  );
}

/** Enroll a device (devices.enroll, confirm-gated). */
export function enrollDevice(
  input: { setup_code_id: string; label?: string },
  confirm?: ConfirmPrompt,
): Promise<DeviceMutationResponse> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<DeviceMutationResponse>("/api/qubit/devices/enroll", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      }),
    confirm,
  );
}

/** Read a device identity card (devices.identity). Metadata only, no secrets. */
export function getDeviceIdentity(deviceId: string): Promise<{ identity: DeviceIdentity }> {
  return fetchJSON<{ identity: DeviceIdentity }>(
    `/api/qubit/devices/identity?id=${encodeURIComponent(deviceId)}`,
  );
}
