// Browser entry for the offline pane preview. Renders the real plugin.js, through its registered
// pane contribution, at the pane's declared width inside a 1440 px Hermes-Desktop-shaped window.
// URL: index.html?scene=<id>&theme=light|dark&mode=ok|loading|error|stale|conflict

import { createElement as h } from 'react'
import { createRoot } from 'react-dom/client'

import plugin from '../../plugin/desktop/plugin.js'
import { createFakeCtx } from './fake-ctx.js'
import { applyPreviewTheme } from './theme.js'

const params = new URLSearchParams(location.search)
const sceneId = params.get('scene') || 'on_hold'
const theme = params.get('theme') === 'dark' ? 'dark' : 'light'
const mode = params.get('mode') || 'ok'

applyPreviewTheme(theme)

function Frame({ pane, scene }) {
  return h(
    'div',
    { className: 'pv-window' },
    h(
      'aside',
      { className: 'pv-sidebar', 'aria-hidden': 'true' },
      h('div', { className: 'pv-chip' }, 'Hermes Desktop')
    ),
    h(
      'main',
      { className: 'pv-chat', 'aria-hidden': 'true' },
      h('div', { className: 'pv-sample' }, `Offline preview with sample data. Scene: ${scene.title}.`)
    ),
    h(
      'section',
      { className: 'pv-pane', style: { width: pane.data.width }, 'data-testid': 'pane' },
      h('div', { className: 'pv-tabs' }, h('span', { className: 'pv-tab' }, pane.title)),
      h('div', { className: 'pv-pane-body' }, pane.render())
    )
  )
}

async function main() {
  const index = await (await fetch('./fixtures/index.json')).json()
  const scene = index.scenes.find((s) => s.id === sceneId)
  if (!scene) throw new Error(`Unknown scene ${sceneId}`)
  const snapshot = await (await fetch(`./fixtures/${scene.file}`)).json()

  const fake = createFakeCtx({ snapshot, mode })
  plugin.register(fake.ctx)
  const pane = fake.contributions.find((c) => c.area === 'panes')
  if (!pane) throw new Error('plugin registered no pane')

  window.__preview = { scene, fake, ready: false }
  createRoot(document.getElementById('root')).render(h(Frame, { pane, scene }))
  // Ready once the first snapshot has settled (or immediately for loading/error modes).
  await new Promise((r) => setTimeout(r, 120))
  window.__preview.ready = true
}

main().catch((error) => {
  document.getElementById('root').textContent = `Preview failed: ${error.message}`
  window.__preview = { ready: true, error: String(error) }
})
