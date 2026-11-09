// The .desktop file's registered URL scheme is a WIRE CONTRACT with the app:
// clicking `hermes://...` only reaches Qubit if the packaged MimeType and the
// scheme the app calls setAsDefaultProtocolClient with are the same string.
// electron-builder derives `MimeType=x-scheme-handler/<scheme>;` for Linux from
// config.protocols (LinuxTargetHelper.computeDesktopEntry) and the same list
// feeds macOS CFBundleURLTypes and the MSIX windows.protocol extension. These
// tests pin that list to the identity the runtime reads, so packaging can never
// advertise a scheme the app will not claim.

import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

import type { Configuration } from 'app-builder-lib'
import { afterEach, test } from 'vitest'

import type { ProductIdentity } from './product-identity'

const require: NodeJS.Require = createRequire(import.meta.url)

const CONFIG = '../electron-builder.config.cjs'
const IDENTITY = '../product-identity.cjs'

const ENV_KEYS = [
  'HERMES_DESKTOP_VARIANT',
  'HERMES_DESKTOP_DEV_SERVER',
  'HERMES_PAYLOAD_TAG',
  'HERMES_BUILD_COMMIT',
  '_HERMES_CHANNEL_REQUEST_JSON'
] as const

afterEach((): void => {
  for (const key of ENV_KEYS) {
    delete process.env[key]
  }

  for (const file of [IDENTITY, CONFIG]) {
    delete require.cache[require.resolve(file)]
  }
})

interface Loaded {
  identity: ProductIdentity
  config: Configuration
}

function load(env: Record<string, string> = {}): Loaded {
  for (const key of ENV_KEYS) {
    delete process.env[key]
  }

  Object.assign(process.env, env)

  for (const file of [IDENTITY, CONFIG]) {
    delete require.cache[require.resolve(file)]
  }

  return { identity: require(IDENTITY), config: require(CONFIG) }
}

/** Every scheme electron-builder will register for this build. */
function packagedSchemes(config: Configuration): string[] {
  const { protocols } = config

  if (protocols == null) {
    return []
  }

  return (Array.isArray(protocols) ? protocols : [protocols]).flatMap(protocol => {
    const { schemes } = protocol

    return schemes == null ? [] : Array.isArray(schemes) ? schemes : [schemes]
  })
}

test('packaged scheme matches the scheme the app registers at launch', (): void => {
  const { identity, config }: Loaded = load()

  assert.deepEqual(
    packagedSchemes(config),
    [identity.deepLinkScheme],
    'electron-builder registers config.protocols on every target; main.ts registers identity.deepLinkScheme'
  )
})

test('the default Linux build registers the standard scheme, not a retired one', (): void => {
  const { config }: Loaded = load()

  assert.deepEqual(packagedSchemes(config), ['hermes'])
})

test('a dev-server build advertises the dev scheme it will actually claim', (): void => {
  const { identity, config }: Loaded = load({ HERMES_DESKTOP_DEV_SERVER: 'http://127.0.0.1:5174' })

  assert.equal(identity.deepLinkScheme, 'hermes-dev')
  assert.deepEqual(packagedSchemes(config), ['hermes-dev'])
})

test('the scheme survives canary and light variants unchanged', (): void => {
  const canary: Loaded = load({ HERMES_PAYLOAD_TAG: 'v0.28.0+canary.20260818T000000Z' })

  assert.deepEqual(packagedSchemes(canary.config), [canary.identity.deepLinkScheme])

  const light: Loaded = load({ HERMES_DESKTOP_VARIANT: 'light' })

  assert.deepEqual(packagedSchemes(light.config), [light.identity.deepLinkScheme])
})
