import assert from 'node:assert/strict'

import { test } from 'vitest'

import { hasWindowsPathPrefix, isQubitOwnedVenvDaemon } from './venv-holder-select'

const SCRIPTS = 'C:\\Qubit\\venv\\Scripts'

test('matches the hindsight daemon shim (exe under venv Scripts + hindsight cmdline)', () => {
  assert.equal(
    isQubitOwnedVenvDaemon(
      'C:\\Qubit\\venv\\Scripts\\pythonw.exe',
      'C:\\Qubit\\venv\\Scripts\\pythonw.exe -m hindsight_api.main --daemon --idle-timeout 300 --port 9177',
      SCRIPTS
    ),
    true
  )
})

test('Windows path prefix match is ordinal case-insensitive', () => {
  assert.equal(
    isQubitOwnedVenvDaemon(
      'c:\\hermes\\venv\\scripts\\python.exe',
      'python.exe -m hindsight_api.main --daemon',
      'C:\\Qubit\\venv\\Scripts'
    ),
    true
  )
})

test('excludes external venv holders that are not the hindsight daemon', () => {
  // a user terminal running the qubit CLI from the venv — must NOT be killed
  assert.equal(isQubitOwnedVenvDaemon('C:\\Qubit\\venv\\Scripts\\hermes.exe', 'qubit chat -q "hi"', SCRIPTS), false)
  // an unrelated python script using the venv interpreter
  assert.equal(
    isQubitOwnedVenvDaemon('C:\\Qubit\\venv\\Scripts\\python.exe', 'python C:\\tools\\import.py', SCRIPTS),
    false
  )
})

test('excludes exes outside the venv even when the cmdline mentions hindsight', () => {
  assert.equal(
    isQubitOwnedVenvDaemon('C:\\Other\\pythonw.exe', 'pythonw -m hindsight_api.main --daemon', SCRIPTS),
    false
  )
})

test('prefix boundary: sibling dirs (ScriptsX) do not match', () => {
  assert.equal(hasWindowsPathPrefix('C:\\Qubit\\venv\\ScriptsX\\python.exe', SCRIPTS), false)
  assert.equal(hasWindowsPathPrefix('C:\\Qubit\\venv\\Scripts\\python.exe', SCRIPTS), true)
})

test('null/undefined fields never match', () => {
  assert.equal(isQubitOwnedVenvDaemon(null, 'x', SCRIPTS), false)
  assert.equal(isQubitOwnedVenvDaemon('C:\\Qubit\\venv\\Scripts\\pythonw.exe', null, SCRIPTS), false)
  assert.equal(isQubitOwnedVenvDaemon(undefined, undefined, SCRIPTS), false)
})
