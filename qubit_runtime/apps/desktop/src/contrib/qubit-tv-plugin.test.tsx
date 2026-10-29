import { afterEach, describe, expect, it, vi } from 'vitest'

import { discoverBundledPlugins } from './plugins'
import { $pluginDecisions, $pluginRecords, setPluginEnabled } from './plugins-store'
import { registry } from './registry'

vi.mock('./runtime-loader', () => ({ watchRuntimePlugins: vi.fn() }))

afterEach(async () => {
  await setPluginEnabled('qubit-tv', false)
  vi.restoreAllMocks()
})

describe('bundled QubitTV plugin discovery', () => {
  it('inventories off by default and loads through the ordinary enable path', async () => {
    $pluginDecisions.set({})
    discoverBundledPlugins()

    expect($pluginRecords.get()['qubit-tv']).toMatchObject({
      kind: 'bundled',
      name: 'QubitTV',
      status: 'disabled'
    })
    expect(registry.getArea('palette').some(item => item.source === 'plugin:qubit-tv')).toBe(false)

    await setPluginEnabled('qubit-tv', true)

    expect($pluginRecords.get()['qubit-tv']?.status).toBe('loaded')
    expect(
      registry.getArea('palette').some(item => item.source === 'plugin:qubit-tv' && item.id === 'qubit-tv:open')
    ).toBe(true)
    expect(
      registry.getArea('titleBar.right').some(item => item.source === 'plugin:qubit-tv')
    ).toBe(true)

    await setPluginEnabled('qubit-tv', false)

    expect(registry.getArea('palette').some(item => item.source === 'plugin:qubit-tv')).toBe(false)
    expect(registry.getArea('titleBar.right').some(item => item.source === 'plugin:qubit-tv')).toBe(false)
  })
})
