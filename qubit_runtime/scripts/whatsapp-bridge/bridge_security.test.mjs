import test from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync, statSync } from 'node:fs';

import {
  BRIDGE_TOKEN_FILE,
  bridgeTokenMatches,
  loadOrCreateBridgeToken,
  parseMediaRoots,
  resolveMediaPath,
  tokenFilePath,
} from './bridge_security.js';

function tempDir() {
  return mkdtempSync(path.join(os.tmpdir(), 'qubit-wa-bridge-security-'));
}

test('bridge token is created owner-only and is stable', () => {
  const dir = tempDir();
  try {
    const first = loadOrCreateBridgeToken(dir, {});
    assert.match(first, /^[a-f0-9]{64}$/);
    const file = tokenFilePath(dir);
    assert.equal(path.basename(file), BRIDGE_TOKEN_FILE);
    assert.equal(statSync(file).mode & 0o777, 0o600);
    const second = loadOrCreateBridgeToken(dir, {});
    assert.equal(second, first);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('an environment token wins and is persisted for other clients', () => {
  const dir = tempDir();
  try {
    const token = loadOrCreateBridgeToken(dir, { WHATSAPP_BRIDGE_TOKEN: 'env-token' });
    assert.equal(token, 'env-token');
    assert.equal(loadOrCreateBridgeToken(dir, {}), 'env-token');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('token comparison is exact and refuses an empty expectation', () => {
  assert.equal(bridgeTokenMatches('secret', 'secret'), true);
  assert.equal(bridgeTokenMatches('secret', 'secreu'), false);
  assert.equal(bridgeTokenMatches('short', 'longer'), false);
  assert.equal(bridgeTokenMatches('', ''), false);
  assert.equal(bridgeTokenMatches('secret', ''), false);
});

test('media roots parse the platform separator and include defaults', () => {
  const roots = parseMediaRoots(`/a${path.delimiter}/b`, ['/c']);
  assert.deepEqual(roots, ['/a', '/b', '/c']);
});

test('media path resolution accepts inside files and rejects traversal/symlinks', () => {
  const dir = tempDir();
  try {
    const root = path.join(dir, 'root');
    const outside = path.join(dir, 'outside');
    mkdirSync(root);
    mkdirSync(outside);
    const inside = path.join(root, 'ok.png');
    const secret = path.join(outside, 'secret.txt');
    writeFileSync(inside, 'ok');
    writeFileSync(secret, 'secret');
    mkdirSync(path.join(root, 'sub'));
    const link = path.join(root, 'sub', 'escape.txt');
    symlinkSync(secret, link);

    const roots = parseMediaRoots('', [root]);
    assert.equal(resolveMediaPath(inside, roots).ok, true);
    assert.equal(resolveMediaPath(secret, roots).ok, false);
    assert.equal(resolveMediaPath(link, roots).ok, false);
    assert.equal(resolveMediaPath('relative.png', roots).ok, false);
    assert.equal(resolveMediaPath(path.join(root, 'missing.png'), roots).ok, false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
