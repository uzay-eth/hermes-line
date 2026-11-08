// Rendered-pixel tests for the pane (offline: local Hermes checkout, cached headless Chromium).
// Run with: node --test tests/l3/pane.test.mjs tests/l3/browser.test.mjs
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { checkEdges } from '../../tools/pane-preview/preview.mjs'

test('every state, light and dark: the transcript bottom edge is background only, strip labels sit under their own segment', { timeout: 300000 }, async () => {
  const results = await checkEdges({ quiet: true })
  assert.equal(results.length, 20)
  for (const r of results) {
    const where = `${r.theme} ${r.scene}`
    assert.equal(r.offPixels, 0, `${where}: bottom 12 px of the transcript must be background (max delta ${r.worstDelta})`)
    assert.ok(r.labelsInside, `${where}: strip labels inside their segments (${r.strip.join(' | ')})`)
    assert.ok(r.menuShorter, `${where}: Menu segment shorter than Hold`)
  }
})
