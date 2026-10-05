// ROUTES MANIFEST — Qubit computer/ workspace file mutations.
//
// Backed by the ONE confined canvas file host server-side
// (voicebridge/files/canvas_host.py — Qubit-owned state-home `canvas/` root,
// resolve().relative_to() confinement, traversal refused 400 CANVAS_BAD_PATH).
// No parallel filesystem store. Writes are confirm-gated at the shared bridge
// core (409 CONFIRM_REQUIRED -> re-POST with {"confirm": true}) exactly like
// every other mutation.
//
//   GET  /api/qubit/computer/files/list?path=   (computer.files.list, read)
//   POST /api/qubit/computer/files/read         (computer.files.read,  read)
//   POST /api/qubit/computer/files/write        (computer.files.write, confirm-gated)
//   POST /api/qubit/computer/files/mkdir        (computer.files.mkdir, confirm-gated)
//
// The upstream-shaped `/api/computer/files/*` routes serve the SAME operations
// (aliases of the above) for parity with the upstream mobile client.

import { fetchJSON, runMutationWithConfirm } from "@/lib/api";

export interface ComputerEntry {
  name: string;
  path: string;
  kind: "file" | "directory";
  size?: number;
}

export interface ComputerDirectory {
  state?: string;
  path: string;
  entries: ComputerEntry[];
  truncated?: boolean;
  root?: string;
  authority?: string;
}

export interface ComputerFile {
  state?: string;
  path: string;
  size: number;
  content: string;
  truncated?: boolean;
  authority?: string;
}

/** List one folder of the confined computer workspace (empty path = root). */
export function listComputerFiles(path = ""): Promise<ComputerDirectory> {
  const q = path ? `?path=${encodeURIComponent(path)}` : "";
  return fetchJSON<ComputerDirectory>(`/api/qubit/computer/files/list${q}`);
}

/** Read one workspace file (confined, bounded). */
export function readComputerFile(path: string): Promise<ComputerFile> {
  return fetchJSON<ComputerFile>("/api/qubit/computer/files/read", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
}

/** Save (create or replace) one workspace file. Confirm-gated 409 -> confirm. */
export function writeComputerFile(
  path: string,
  text: string,
  confirm?: (detail: string) => boolean | Promise<boolean>,
): Promise<{ state?: string; path: string; size: number }> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<{ state?: string; path: string; size: number }>(
        "/api/qubit/computer/files/write",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path, text }),
        },
      ),
    confirm,
  );
}

/** Create one workspace folder. Confirm-gated 409 -> confirm. */
export function mkdirComputer(
  path: string,
  confirm?: (detail: string) => boolean | Promise<boolean>,
): Promise<{ state?: string; path: string; created: boolean }> {
  return runMutationWithConfirm(
    () =>
      fetchJSON<{ state?: string; path: string; created: boolean }>(
        "/api/qubit/computer/files/mkdir",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path }),
        },
      ),
    confirm,
  );
}
