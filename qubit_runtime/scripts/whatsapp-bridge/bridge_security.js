import path from 'path';
import { chmodSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from 'fs';
import { randomBytes, timingSafeEqual } from 'crypto';

export const BRIDGE_TOKEN_HEADER = 'x-qubit-bridge-token';
export const BRIDGE_TOKEN_FILE = 'bridge.token';

export function tokenFilePath(sessionDir) {
  return path.join(String(sessionDir || ''), BRIDGE_TOKEN_FILE);
}

export function persistBridgeToken(sessionDir, token) {
  const value = String(token || '').trim();
  if (!value) throw new Error('bridge token must not be empty');
  mkdirSync(String(sessionDir), { recursive: true, mode: 0o700 });
  const file = tokenFilePath(sessionDir);
  writeFileSync(file, `${value}\n`, { mode: 0o600 });
  try { chmodSync(file, 0o600); } catch {}
  return file;
}

export function loadOrCreateBridgeToken(sessionDir, env = process.env) {
  const fromEnv = String((env && env.WHATSAPP_BRIDGE_TOKEN) || '').trim();
  if (fromEnv) {
    persistBridgeToken(sessionDir, fromEnv);
    return fromEnv;
  }
  const file = tokenFilePath(sessionDir);
  try {
    const existing = readFileSync(file, 'utf8').trim();
    if (existing) {
      try { chmodSync(file, 0o600); } catch {}
      return existing;
    }
  } catch {}
  const token = randomBytes(32).toString('hex');
  persistBridgeToken(sessionDir, token);
  return token;
}

export function bridgeTokenMatches(provided, expected) {
  const want = String(expected || '');
  if (!want) return false;
  const a = Buffer.from(String(provided || ''), 'utf8');
  const b = Buffer.from(want, 'utf8');
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

export function parseMediaRoots(raw, defaults = []) {
  const separator = process.platform === 'win32' ? ';' : ':';
  const out = [];
  const add = (value) => {
    const text = String(value || '').trim();
    if (!text) return;
    const resolved = path.resolve(text);
    if (!out.includes(resolved)) out.push(resolved);
  };
  for (const value of String(raw || '').split(separator)) add(value);
  for (const value of defaults) add(value);
  return out;
}

export function resolveMediaPath(filePath, roots) {
  if (!filePath || typeof filePath !== 'string' || !path.isAbsolute(filePath)) {
    return { ok: false, reason: 'filePath must be an absolute path' };
  }
  let resolved;
  try {
    resolved = realpathSync(filePath);
  } catch {
    return { ok: false, reason: 'file not found' };
  }
  for (const root of roots || []) {
    let resolvedRoot;
    try {
      resolvedRoot = realpathSync(root);
    } catch {
      continue;
    }
    const rel = path.relative(resolvedRoot, resolved);
    if (rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel))) {
      return { ok: true, path: resolved, root: resolvedRoot };
    }
  }
  return { ok: false, reason: 'path is outside the bridge media roots' };
}
