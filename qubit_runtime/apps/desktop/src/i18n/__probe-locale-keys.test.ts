import { describe, expect, it } from 'vitest'

import { TRANSLATIONS } from './catalog'
import type { BundledLocale } from './types'

type Leaf = { path: string; value: unknown }

function leaves(value: unknown, path = ''): Leaf[] {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return Object.entries(value).flatMap(([key, child]) => leaves(child, path ? `${path}.${key}` : key))
  }
  return [{ path, value }]
}

const textOf = (v: unknown) => (typeof v === 'function' ? String(v('a', 'b', 'c')) : JSON.stringify(v))

// `intro` is display-only: English lives in intro-copy.jsonl, so en's entry is an
// empty shell while every translation ships its real rotation. Excluded, as the
// committed catalog test does.
const isIntro = (p: string) => p.startsWith('intro.')

// `messaging.platformIntro` is a Record<string,string> of per-platform onboarding
// hints. English keeps its copy in a component table and ships an empty shell;
// zh/zh-hant/ja/ar translate the hints inline, so those leaves are translations,
// not orphans.
const localizableExtra = (p: string) => p.startsWith('messaging.platformIntro.')

const leafMap = (locale: BundledLocale) =>
  new Map(leaves(TRANSLATIONS[locale]).filter(l => !isIntro(l.path)).map(l => [l.path, l.value]))

const english = leafMap('en')
const LOCALES = ['zh', 'zh-hant', 'ja', 'ar', 'ru', 'fr', 'de', 'es'] as const satisfies readonly BundledLocale[]

describe.each(LOCALES)('%s', locale => {
  const catalog = leafMap(locale)

  it('has every English key', () => {
    expect([...english.keys()].filter(p => !catalog.has(p)).sort(), `${locale} missing keys`).toEqual([])
  })

  it('has no keys English lacks', () => {
    const extra = [...catalog.keys()].filter(p => !english.has(p) && !localizableExtra(p)).sort()
    expect(extra, `${locale} orphan keys`).toEqual([])
  })

  it('renders no empty string where English has text', () => {
    const blank = [...english]
      .filter(([path, v]) => textOf(v) !== '""' && textOf(catalog.get(path)) === '""')
      .map(([path]) => path)
      .sort()
    expect(blank, `${locale} blank labels`).toEqual([])
  })
})
