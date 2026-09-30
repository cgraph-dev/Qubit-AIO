import { describe, expect, it } from 'vitest'

import {
  normalizeQubitOpenString,
  pathFromQubitDeepLink,
  pathFromOpenDeepLink,
  resolveQubitOpenPath
} from './hermes-open-target'

describe('normalizeHermesOpenString', () => {
  it('accepts hash-router paths and strips a leading hash', () => {
    expect(normalizeQubitOpenString('/index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizeQubitOpenString('#/index-network/intent/1')).toBe('/index-network/intent/1')
  })

  it('maps plugin-scoped hermes:// deep links to the same path', () => {
    expect(normalizeQubitOpenString('hermes://index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizeQubitOpenString('hermes://index-network/intent/1?focus=true')).toBe(
      '/index-network/intent/1?focus=true'
    )
  })

  it('maps hermes://open/… deep links by stripping the open host', () => {
    expect(normalizeQubitOpenString('hermes://open/index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizeQubitOpenString('hermes://open/settings/plugins')).toBe('/settings/plugins')
  })

  it('rejects reserved hermes kinds and unsafe paths', () => {
    expect(normalizeQubitOpenString('hermes://blueprint/morning-brief')).toBeNull()
    expect(normalizeQubitOpenString('hermes://plugin/install')).toBeNull()
    expect(normalizeQubitOpenString('https://example.com/x')).toBeNull()
    expect(normalizeQubitOpenString('/../etc/passwd')).toBeNull()
    expect(normalizeQubitOpenString('index-network')).toBeNull()
  })
})

describe('resolveHermesOpenPath', () => {
  it('merges structured path + params', () => {
    expect(resolveQubitOpenPath({ path: '/index-network/intent/1', params: { focus: 'true' } })).toBe(
      '/index-network/intent/1?focus=true'
    )
  })

  it('resolves href the same as a bare string', () => {
    expect(resolveQubitOpenPath({ href: 'hermes://index-network/intent/1' })).toBe('/index-network/intent/1')
  })
})

describe('pathFromHermesDeepLink', () => {
  it('builds the navigate path from a plugin-scoped deep-link payload', () => {
    expect(pathFromQubitDeepLink('index-network', 'intent/1')).toBe('/index-network/intent/1')
  })

  it('builds the navigate path from hermes://open/… payloads', () => {
    expect(pathFromOpenDeepLink('index-network/intent/1')).toBe('/index-network/intent/1')
    expect(pathFromQubitDeepLink('open', 'agent/42')).toBe('/agent/42')
  })

  it('ignores reserved kinds', () => {
    expect(pathFromQubitDeepLink('blueprint', 'morning-brief')).toBeNull()
    expect(pathFromQubitDeepLink('plugin', 'install')).toBeNull()
  })
})
