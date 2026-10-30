import { build } from 'esbuild'
import { mkdir, readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// Bundles the bundled Qubit desktop plugin into this package's runtime door.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const runtimeRoot = resolve(root, '../..')
const outfile = resolve(root, 'desktop/plugin.js')

await mkdir(dirname(outfile), { recursive: true })
await build({
  entryPoints: [resolve(runtimeRoot, 'apps/desktop/src/plugins/qubit-tv/plugin.jsx')],
  outfile,
  bundle: true,
  format: 'esm',
  platform: 'browser',
  target: 'es2022',
  jsx: 'automatic',
  external: ['@qubit/plugin-sdk', 'react', 'react/jsx-runtime'],
  loader: { '.png': 'dataurl', '.jpg': 'dataurl', '.svg': 'dataurl' },
  legalComments: 'none'
})

const code = await readFile(outfile, 'utf8')
const imports = [...code.matchAll(/^\s*import\s+(?:[^'"\n]+\s+from\s+)?['"]([^'"]+)['"]/gm)].map(m => m[1])
const allowed = new Set(['@qubit/plugin-sdk', 'react', 'react/jsx-runtime'])
const invalid = imports.filter(item => !allowed.has(item))
if (invalid.length) throw new Error(`Unexpected runtime imports: ${invalid.join(', ')}`)
if (code.length < 1000) throw new Error('Built desktop/plugin.js is unexpectedly empty')
console.log(`Built desktop/plugin.js (${code.length} bytes, ${imports.length} allowed runtime imports)`)
