#!/usr/bin/env node
// Offline preview harness for the Hermes Line Desktop pane.
//
//   node tools/pane-preview/preview.mjs build          bundle into tools/pane-preview/out/
//   node tools/pane-preview/preview.mjs serve          build, then serve on 127.0.0.1 (prints the URL)
//   node tools/pane-preview/preview.mjs shoot [ids..]  build, then screenshot every shot (or the named ones)
//
// Nothing is installed and nothing leaves the machine. esbuild, React, Playwright and its cached
// Chromium come from the local, read-only Hermes checkout (HERMES_AGENT_DIR, default
// ~/.hermes/hermes-agent); the app's compiled stylesheet and fonts come from its desktop build, so
// the pane is drawn with the real theme tokens and the real UI-kit components.

import { execFileSync } from 'node:child_process'
import { createServer } from 'node:http'
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { homedir } from 'node:os'
import { dirname, extname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = resolve(HERE, '../..')
const OUT = join(HERE, 'out')
const SHOTS = join(HERE, 'screenshots')
export const HERMES = resolve(process.env.HERMES_AGENT_DIR || join(homedir(), '.hermes/hermes-agent'))
const DESKTOP = join(HERMES, 'apps/desktop')

const VIEWPORT = { width: 1440, height: 900 }

// One entry per screenshot. `act` drives the page into the state worth seeing.
export const SHOTS_PLAN = [
  { id: 'on_hold', scene: 'on_hold' },
  { id: 'human_detected', scene: 'human_detected' },
  { id: 'ringing_owner', scene: 'ringing_owner' },
  { id: 'owner_connected', scene: 'owner_connected' },
  { id: 'agent_talking', scene: 'agent_talking', act: 'compose' },
  { id: 'ended', scene: 'ended' },
  { id: 'failed', scene: 'failed' },
  { id: 'on_hold-dark', scene: 'on_hold', theme: 'dark' },
  { id: 'human_detected-dark', scene: 'human_detected', theme: 'dark' },
  { id: 'ringing_owner-dark', scene: 'ringing_owner', theme: 'dark' },
  { id: 'owner_connected-dark', scene: 'owner_connected', theme: 'dark' },
  { id: 'agent_talking-dark', scene: 'agent_talking', theme: 'dark', act: 'compose' },
  { id: 'ended-dark', scene: 'ended', theme: 'dark' },
  { id: 'failed-dark', scene: 'failed', theme: 'dark' },
  { id: 'awaiting_approval', scene: 'awaiting_approval' },
  { id: 'dialing', scene: 'dialing' },
  { id: 'ivr', scene: 'ivr' },
  { id: 'empty', scene: 'empty' },
  { id: 'loading', scene: 'empty', mode: 'loading' },
  { id: 'error', scene: 'empty', mode: 'error' },
  { id: 'stale', scene: 'on_hold', mode: 'stale', act: 'event' },
  { id: 'conflict', scene: 'agent_talking', mode: 'conflict', act: 'take_over' },
  { id: 'hang_up-confirm', scene: 'on_hold', act: 'hang_up' }
]

// Review set (direction C): the four review states (light) + human detected (dark), cropped to the
// pane plus a thin strip of app context, and a before/after pair for the person-answered state.
// screenshots/round3/ and screenshots/variants/ are earlier rounds, kept as images.
export const REVIEW_ROUND = 'round4'
export const REVIEW_STATES = ['on_hold', 'human_detected', 'owner_connected', 'ended']
export const REVIEW_SHOTS = [
  ...REVIEW_STATES.map((st) => ({ id: `${REVIEW_ROUND}/${st}`, scene: st, crop: true })),
  { id: `${REVIEW_ROUND}/human_detected-dark`, scene: 'human_detected', theme: 'dark', crop: true }
]
const PANE_PX = 380
const CONTEXT_PX = 28

const requireHermes = createRequire(join(HERMES, 'package.json'))

function need(path, what) {
  if (!existsSync(path)) {
    console.error(`pane-preview: ${what} not found at ${path}. Set HERMES_AGENT_DIR to a Hermes checkout with a desktop build.`)
    process.exit(2)
  }
}

// Playwright's own Chromium revision may not be cached; reuse any headless shell that is, newest
// first, instead of downloading one. PANE_PREVIEW_CHROMIUM overrides the choice.
function cachedChromium() {
  if (process.env.PANE_PREVIEW_CHROMIUM) return process.env.PANE_PREVIEW_CHROMIUM
  const cache = process.env.PLAYWRIGHT_BROWSERS_PATH || join(homedir(), 'Library/Caches/ms-playwright')
  if (!existsSync(cache)) return undefined
  const shells = readdirSync(cache)
    .filter((d) => /^chromium_headless_shell-\d+$/.test(d))
    .sort((x, y) => Number(y.split('-')[1]) - Number(x.split('-')[1]))
  for (const d of shells) {
    for (const arch of ['chrome-headless-shell-mac-arm64', 'chrome-headless-shell-mac-x64', 'chrome-headless-shell-linux64']) {
      const bin = join(cache, d, arch, 'chrome-headless-shell')
      if (existsSync(bin)) return bin
    }
  }
  return undefined
}

// Real Hermes Desktop reserves scrollbar width (8 px on the transcript); Playwright's default
// --hide-scrollbars would render a wider text column than the app does (found in round 5 by
// comparing with the pane running inside Hermes Desktop), so the harness keeps real scrollbars.
const HARNESS_BROWSER = () => ({ executablePath: cachedChromium(), ignoreDefaultArgs: ['--hide-scrollbars'] })

function appStylesheet() {
  const assets = join(DESKTOP, 'dist/assets')
  need(assets, 'Hermes Desktop build (apps/desktop/dist/assets)')
  const css = readdirSync(assets).filter((f) => /^index-.*\.css$/.test(f))
  if (css.length !== 1) throw new Error(`expected one index-*.css in ${assets}, found ${css.length}`)
  return { dir: assets, file: css[0] }
}

export async function build() {
  need(join(HERMES, 'node_modules/esbuild'), 'esbuild in the Hermes checkout')
  const esbuild = requireHermes('esbuild')
  rmSync(OUT, { recursive: true, force: true })
  mkdirSync(join(OUT, 'app'), { recursive: true })
  mkdirSync(join(OUT, 'fixtures'), { recursive: true })

  await esbuild.build({
    entryPoints: [join(HERE, 'harness.js')],
    outfile: join(OUT, 'harness.js'),
    bundle: true,
    format: 'esm',
    platform: 'browser',
    target: 'chrome130',
    jsx: 'automatic',
    minify: false,
    sourcemap: false,
    logLevel: 'warning',
    alias: {
      '@hermes/plugin-sdk': join(HERE, 'sdk-stub.js'),
      '@hermes/shared': join(HERMES, 'apps/shared/src'),
      '@': join(DESKTOP, 'src')
    },
    nodePaths: [join(DESKTOP, 'node_modules'), join(HERMES, 'node_modules')],
    resolveExtensions: ['.tsx', '.ts', '.jsx', '.js', '.mjs', '.json'],
    loader: { '.svg': 'dataurl', '.png': 'dataurl', '.woff2': 'file', '.ttf': 'file', '.woff': 'file' },
    define: {
      'process.env.NODE_ENV': '"production"',
      'import.meta.env': '{"DEV":false,"PROD":true,"MODE":"production","SSR":false}'
    }
  })

  // The app's own compiled stylesheet plus every font it references, served same-origin.
  const sheet = appStylesheet()
  const cssText = readFileSync(join(sheet.dir, sheet.file), 'utf8')
  for (const m of cssText.matchAll(/url\(\s*['"]?(?!data:)([^'")?#]+)[^)]*\)/g)) {
    const name = m[1].replace(/^\.\//, '').replace(/^\/assets\//, '')
    const from = join(sheet.dir, name)
    if (!name.includes('/') && existsSync(from)) copyFileSync(from, join(OUT, 'app', name))
  }
  copyFileSync(join(sheet.dir, sheet.file), join(OUT, 'app', 'app.css'))

  for (const f of readdirSync(join(HERE, 'fixtures'))) copyFileSync(join(HERE, 'fixtures', f), join(OUT, 'fixtures', f))

  const harnessCss = existsSync(join(OUT, 'harness.css')) ? '<link rel="stylesheet" href="./harness.css">' : ''
  writeFileSync(
    join(OUT, 'index.html'),
    `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Hermes Line pane preview (sample data)</title>
<link rel="stylesheet" href="./app/app.css">
${harnessCss}
<link rel="stylesheet" href="./frame.css">
</head>
<body>
<div id="root"></div>
<script type="module" src="./harness.js"></script>
</body>
</html>
`
  )
  copyFileSync(join(HERE, 'frame.css'), join(OUT, 'frame.css'))
  return OUT
}

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.ttf': 'font/ttf',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.png': 'image/png',
  '.svg': 'image/svg+xml'
}

export function serve(port = 0) {
  const server = createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1')
    const rel = decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname)
    const file = resolve(OUT, `.${rel}`)
    if (!file.startsWith(OUT) || !existsSync(file)) {
      res.writeHead(404).end('not found')
      return
    }
    res.writeHead(200, { 'content-type': TYPES[extname(file)] || 'application/octet-stream', 'cache-control': 'no-store' })
    res.end(readFileSync(file))
  })
  return new Promise((ok) => server.listen(port, '127.0.0.1', () => ok(server)))
}

async function settle(page) {
  await page.waitForFunction(() => window.__preview && window.__preview.ready === true)
  await page.evaluate(() => document.fonts.ready)
  await page.waitForTimeout(150)
}

async function act(page, kind) {
  const pane = page.getByTestId('pane')
  if (kind === 'compose') {
    await pane.getByRole('button', { name: 'Say this' }).click()
    await pane.getByLabel('What should Hermes say?').fill('Ask whether they have a table by the window.')
  } else if (kind === 'take_over') {
    await pane.getByRole('button', { name: 'Take over' }).click()
  } else if (kind === 'hang_up') {
    await pane.getByRole('button', { name: 'Hang up' }).click()
    await page.getByRole('dialog').waitFor()
  } else if (kind === 'event') {
    await page.evaluate(() => window.__preview.fake.emit('plugin.hermes-line.call.updated', { id: 'call_sample_airline', revision: 999 }))
  }
  await page.waitForTimeout(400)
}

export async function shoot(only = []) {
  await build()
  need(join(HERMES, 'node_modules/playwright'), 'Playwright in the Hermes checkout')
  const { chromium } = requireHermes('playwright')
  const index = JSON.parse(readFileSync(join(HERE, 'fixtures/index.json'), 'utf8'))
  const server = await serve(0)
  const base = `http://127.0.0.1:${server.address().port}/index.html`
  mkdirSync(SHOTS, { recursive: true })
  const browser = await chromium.launch(HARNESS_BROWSER())
  const written = []
  try {
    const plan = only.length === 1 && only[0] === REVIEW_ROUND ? REVIEW_SHOTS : [...SHOTS_PLAN, ...REVIEW_SHOTS].filter((s) => !only.length || only.includes(s.id))
    for (const shot of plan) {
      const scene = index.scenes.find((s) => s.id === shot.scene)
      const context = await browser.newContext({
        viewport: VIEWPORT,
        deviceScaleFactor: 2,
        locale: index.locale,
        timezoneId: index.timezone,
        colorScheme: shot.theme === 'dark' ? 'dark' : 'light'
      })
      const page = await context.newPage()
      const errors = []
      page.on('pageerror', (e) => errors.push(String(e)))
      await page.clock.setFixedTime(new Date(scene.now))
      const q = new URLSearchParams({ scene: shot.scene, theme: shot.theme || 'light', mode: shot.mode || 'ok' })
      await page.goto(`${base}?${q}`)
      await settle(page)
      if (shot.act) await act(page, shot.act)
      const file = join(SHOTS, `${shot.id}.png`)
      mkdirSync(dirname(file), { recursive: true })
      const clip = shot.crop
        ? { x: VIEWPORT.width - PANE_PX - CONTEXT_PX, y: 0, width: PANE_PX + CONTEXT_PX, height: VIEWPORT.height }
        : undefined
      await page.screenshot({ path: file, animations: 'disabled', clip })
      if (errors.length) throw new Error(`${shot.id}: page errors: ${errors.join(' | ')}`)
      written.push(file)
      await context.close()
    }
  } finally {
    await browser.close()
    server.close()
  }
  if (written.some((f) => f.includes(`/${REVIEW_ROUND}/human_detected.png`))) written.push(...beforeAfter())
  for (const f of written) console.log(f.replace(`${REPO}/`, ''))
  return written
}

// Before/after pair for the person-answered state: <round>/human_detected-before.png (the previous
// round's render, kept as the reference) beside the current render. Uses a local ImageMagick when present.
function beforeAfter() {
  const magick = ['/opt/homebrew/bin/magick', '/usr/local/bin/magick', '/usr/bin/magick'].find((p) => existsSync(p))
  const before = join(SHOTS, REVIEW_ROUND, 'human_detected-before.png')
  const after = join(SHOTS, REVIEW_ROUND, 'human_detected.png')
  if (!magick || !existsSync(before) || !existsSync(after)) {
    console.error('pane-preview: ImageMagick or the before image is missing; pair skipped')
    return []
  }
  const font = ['/System/Library/Fonts/Helvetica.ttc', '/System/Library/Fonts/Supplemental/Arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'].find((p) => existsSync(p))
  const file = join(SHOTS, REVIEW_ROUND, 'human_detected-before-after.png')
  const args = ['montage', '-label', 'Before (round 3)', before, '-label', 'After (round 4)', after]
  if (font) args.push('-font', font)
  args.push('-tile', '2x1', '-geometry', '+24+16', '-pointsize', '26', '-background', '#e9e9ec', file)
  execFileSync(magick, args)
  return [file]
}

// WCAG contrast of the signal card's secondary text and strip segments against the card colour
// itself (the card's own background composited over the pane), per state and theme. Text needs
// 4.5:1, segments 3:1. Prints a table and writes contrast.json next to the screenshots.
export const CONTRAST_SCENES = ['on_hold', 'human_detected', 'ringing_owner', 'owner_connected', 'ended', 'failed']

export function measureCard() {
  const cv = document.createElement('canvas')
  cv.width = cv.height = 1
  const cx = cv.getContext('2d', { willReadFrequently: true })
  const rgba = (css) => {
    cx.clearRect(0, 0, 1, 1)
    cx.fillStyle = '#000'
    cx.fillStyle = css
    cx.fillRect(0, 0, 1, 1)
    const [r, g, b, a] = cx.getImageData(0, 0, 1, 1).data
    return [r, g, b, a / 255]
  }
  const over = (top, under) => top.slice(0, 3).map((c, i) => c * top[3] + under[i] * (1 - top[3])).concat(1)
  const lum = ([r, g, b]) => {
    const f = (c) => ((c /= 255) <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
  }
  const ratio = (a, b) => {
    const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p)
    return (x + 0.05) / (y + 0.05)
  }
  const hex = (c) => '#' + c.slice(0, 3).map((v) => Math.round(v).toString(16).padStart(2, '0')).join('')
  // The card's backdrop: the nearest ancestor with an opaque background (the harness frame's pane, or
  // the real Desktop's pane tile), so the same function runs in both.
  const field = document.querySelector('.hlc-field')
  let host = field.parentElement
  while (host && rgba(getComputedStyle(host).backgroundColor)[3] < 1) host = host.parentElement
  const paneBg = host ? rgba(getComputedStyle(host).backgroundColor) : [255, 255, 255, 1]
  const card = over(rgba(getComputedStyle(field).backgroundColor), paneBg)
  const rows = []
  const textSel = ['.hlc-task-meta', '.hlc-quote-by', '.hlc-sub', '.hlc-heard-at', '.hlc-col[data-current="false"] .hlc-label', '.hlc-col[data-current="true"] .hlc-label', '.hlc-headline']
  for (const sel of textSel) {
    const el = field.querySelector(sel)
    if (!el) continue
    const ink = over(rgba(getComputedStyle(el).color), card)
    rows.push({ kind: 'text', sel, ink: hex(ink), ratio: +ratio(ink, card).toFixed(2), need: 4.5 })
  }
  const bars = [...field.querySelectorAll('.hlc-col')]
  for (const [cur, label] of [['false', 'segment'], ['true', 'segment (live)']]) {
    const bar = bars.find((b) => b.dataset.current === cur)
    if (!bar) continue
    const fill = over(rgba(getComputedStyle(bar.querySelector('.hlc-bar')).backgroundColor), card)
    rows.push({ kind: 'segment', sel: label, ink: hex(fill), ratio: +ratio(fill, card).toFixed(2), need: 3 })
  }
  return { card: hex(card), tone: field.dataset.tone, rows }
}

export async function contrast() {
  await build()
  const { chromium } = requireHermes('playwright')
  const server = await serve(0)
  const base = `http://127.0.0.1:${server.address().port}/index.html`
  const index = JSON.parse(readFileSync(join(HERE, 'fixtures/index.json'), 'utf8'))
  const browser = await chromium.launch(HARNESS_BROWSER())
  const results = []
  try {
    for (const theme of ['light', 'dark']) {
      for (const sceneId of CONTRAST_SCENES) {
        const scene = index.scenes.find((s) => s.id === sceneId)
        const page = await browser.newPage({ viewport: VIEWPORT, colorScheme: theme, timezoneId: index.timezone, locale: index.locale })
        await page.clock.setFixedTime(new Date(scene.now))
        await page.goto(`${base}?${new URLSearchParams({ scene: sceneId, theme })}`)
        await settle(page)
        const m = await page.evaluate(measureCard)
        results.push({ theme, scene: sceneId, ...m })
        await page.close()
      }
    }
  } finally {
    await browser.close()
    server.close()
  }
  let fail = 0
  for (const r of results) {
    for (const row of r.rows) {
      const ok = row.ratio >= row.need
      if (!ok) fail++
      console.log(`${ok ? 'ok  ' : 'FAIL'} ${r.theme.padEnd(5)} ${r.scene.padEnd(16)} card ${r.card}  ${row.sel.padEnd(42)} ${row.ink}  ${row.ratio.toFixed(2)}:1 (need ${row.need})`)
    }
  }
  mkdirSync(join(SHOTS, REVIEW_ROUND), { recursive: true })
  writeFileSync(join(SHOTS, REVIEW_ROUND, 'contrast.json'), `${JSON.stringify(results, null, 2)}\n`)
  console.log(fail ? `${fail} below AA` : 'all card text >= 4.5:1, all segments >= 3:1')
  return fail
}

// Rendered-pixel checks, run by tests/l3/browser.test.mjs and `preview.mjs edges`:
//  - the bottom 12 px of the transcript scroll area are background only (no glyph touches the edge);
//  - each strip label lies inside its own segment's horizontal extent; Menu's segment is shorter than Hold's.
export const EDGE_SCENES = ['ivr', 'on_hold', 'human_detected', 'ringing_owner', 'owner_connected', 'agent_talking', 'ended', 'failed', 'dialing', 'awaiting_approval']

export async function checkEdges({ quiet = false } = {}) {
  await build()
  const { chromium } = requireHermes('playwright')
  const server = await serve(0)
  const base = `http://127.0.0.1:${server.address().port}/index.html`
  const index = JSON.parse(readFileSync(join(HERE, 'fixtures/index.json'), 'utf8'))
  const browser = await chromium.launch(HARNESS_BROWSER())
  const results = []
  try {
    for (const theme of ['light', 'dark']) {
      for (const sceneId of EDGE_SCENES) {
        const scene = index.scenes.find((s) => s.id === sceneId)
        const page = await browser.newPage({ viewport: VIEWPORT, deviceScaleFactor: 2, colorScheme: theme, timezoneId: index.timezone, locale: index.locale })
        await page.clock.setFixedTime(new Date(scene.now))
        await page.goto(`${base}?${new URLSearchParams({ scene: sceneId, theme })}`)
        await settle(page)
        const geo = await page.evaluate(() => {
          const pane = document.querySelector('[data-testid="pane"]')
          const lines = pane.querySelector('.hlp-lines')
          const r = lines.getBoundingClientRect()
          const bottom = Math.min(r.bottom, window.innerHeight)
          const strip = [...pane.querySelectorAll('.hlc-col')].map((c) => {
            const bar = c.querySelector('.hlc-bar').getBoundingClientRect()
            const label = c.querySelector('.hlc-label').getBoundingClientRect()
            return { text: c.querySelector('.hlc-label').textContent, bar: [bar.left, bar.right], label: [label.left, label.right] }
          })
          return { x: r.left, width: r.width, bottom, bg: getComputedStyle(pane).backgroundColor, strip }
        })
        const png = await page.screenshot({ clip: { x: geo.x, y: geo.bottom - 12, width: geo.width, height: 12 }, animations: 'disabled' })
        const pixels = await page.evaluate(
          async ({ b64, bg }) => {
            const img = new Image()
            img.src = `data:image/png;base64,${b64}`
            await img.decode()
            const cv = document.createElement('canvas')
            cv.width = img.width
            cv.height = img.height
            const cx = cv.getContext('2d', { willReadFrequently: true })
            cx.fillStyle = bg
            cx.fillRect(0, 0, 1, 1)
            const ref = [...cx.getImageData(0, 0, 1, 1).data.slice(0, 3)]
            cx.drawImage(img, 0, 0)
            const data = cx.getImageData(0, 0, cv.width, cv.height).data
            let worst = 0
            let off = 0
            for (let i = 0; i < data.length; i += 4) {
              const d = Math.max(Math.abs(data[i] - ref[0]), Math.abs(data[i + 1] - ref[1]), Math.abs(data[i + 2] - ref[2]))
              if (d > worst) worst = d
              if (d > 2) off++
            }
            return { ref, worst, off, total: data.length / 4 }
          },
          { b64: png.toString('base64'), bg: geo.bg }
        )
        const labelsInside = geo.strip.every((c) => c.label[0] >= c.bar[0] - 0.5 && c.label[1] <= c.bar[1] + 0.5)
        const menu = geo.strip.find((c) => c.text.startsWith('Menu'))
        const hold = geo.strip.find((c) => c.text.startsWith('Hold'))
        const menuShorter = !(menu && hold) || menu.bar[1] - menu.bar[0] < hold.bar[1] - hold.bar[0]
        results.push({ theme, scene: sceneId, edgeClean: pixels.off === 0, worstDelta: pixels.worst, offPixels: pixels.off, labelsInside, menuShorter, strip: geo.strip.map((c) => c.text) })
        await page.close()
      }
    }
  } finally {
    await browser.close()
    server.close()
  }
  if (!quiet) for (const r of results) console.log(`${r.edgeClean && r.labelsInside && r.menuShorter ? 'ok  ' : 'FAIL'} ${r.theme.padEnd(5)} ${r.scene.padEnd(18)} bottom 12px off-bg pixels ${r.offPixels} (max delta ${r.worstDelta})  strip ${r.strip.join(' | ')}  labels inside ${r.labelsInside}  menu<hold ${r.menuShorter}`)
  return results
}

const isMain = process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url
if (isMain) {
  const [cmd = 'build', ...rest] = process.argv.slice(2)
  if (cmd === 'build') {
    console.log(await build())
  } else if (cmd === 'serve') {
    await build()
    const server = await serve(Number(process.env.PORT || 0))
    console.log(`http://127.0.0.1:${server.address().port}/index.html?scene=on_hold&theme=light`)
  } else if (cmd === 'edges') {
    const r = await checkEdges()
    process.exitCode = r.every((x) => x.edgeClean && x.labelsInside && x.menuShorter) ? 0 : 1
  } else if (cmd === 'contrast') {
    process.exitCode = (await contrast()) ? 1 : 0
  } else if (cmd === 'shoot') {
    await shoot(rest)
  } else {
    console.error('usage: preview.mjs build | serve | shoot [shot ids | round4] | contrast | edges')
    process.exit(64)
  }
}
