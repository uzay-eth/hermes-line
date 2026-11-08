#!/usr/bin/env node
// Capture the Hermes Line pane during a live trial call, inside the sandboxed real Hermes Desktop.
//
//   Live trial (read-only on the trial's store):
//     node tools/pane-preview/live-capture.mjs --store <trial run dir | .../plugin-data/hermes-line/line.db> \
//          [--call call_...] [--out <dir>] [--every 5] [--max-minutes 90]
//
//   Offline proof (replays a recorded trial into a scratch store, then captures it the same way):
//     node tools/pane-preview/live-capture.mjs --replay evidence/trial/<call>/trial.json [--out <dir>]
//
// What it does:
//  - launches the real Hermes Desktop in its own sandbox (desktop-run.mjs machinery, separate sandbox
//    dir ~/.hermes/cache/scratch/hermes-line-l3/live/), plugin enabled, right sidebar open, 1440 x 900;
//  - live_mirror.py opens the trial store with SQLite mode=ro and copies only the call's task, call
//    and transcript rows (phone numbers replaced by their masked form) into the sandbox store, which
//    the sandbox gateway's REST API serves to the pane. The capture Desktop never writes the trial
//    store: pane controls pressed in it reach only the sandbox store, which no engine reads;
//  - screenshots the 1440 px window every --every seconds, and on every state change as soon as
//    the pane shows the new state (dialing, menu, hold, human, owner ring, bridged or fallback,
//    ended/failed). The new state's screenshot is taken when the pane's live status reads it;
//  - stops after the call ends (final screenshots), on --max-minutes, or on Ctrl-C; cleans up the
//    sandbox processes. No network: the sandbox uses a dead proxy, as desktop-run.mjs does.
//
// Output (default tools/pane-preview/screenshots/live/<call or replay>-<time>/):
//   state-NN-<state>.png        one per state change, 1440 x 900 window at 2x
//   state-NN-<state>-pane.png   the pane with a 28 px app strip
//   tick-NNNN.png               every --every seconds
//   capture.json                every shot: file, kind, state, call time, wall time
//   states-sheet.png            the state crops side by side

import { spawn } from 'node:child_process'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, statSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { basename, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { STATE_LABELS, STATE_SLUGS } from './live-states.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = resolve(HERE, '../..')
process.env.PANE_DESKTOP_SANDBOX = process.env.PANE_DESKTOP_SANDBOX || join(homedir(), '.hermes/cache/scratch/hermes-line-l3/live')
const D = await import('./desktop-run.mjs')

function parseArgs(argv) {
  const out = { every: 5, maxMinutes: 90 }
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i]
    const v = () => argv[++i]
    if (k === '--store') out.store = v()
    else if (k === '--call') out.call = v()
    else if (k === '--out') out.out = v()
    else if (k === '--every') out.every = Number(v())
    else if (k === '--max-minutes') out.maxMinutes = Number(v())
    else if (k === '--replay') out.replay = v()
    else if (k === '--help' || k === '-h') out.help = true
    else throw new Error(`unknown argument ${k}`)
  }
  return out
}

/** Accept a trial run dir (…/run-…/), a Hermes home, or the line.db itself. */
function resolveStore(p) {
  const abs = resolve(p)
  for (const c of [abs, join(abs, 'line.db'), join(abs, 'plugin-data/hermes-line/line.db'), join(abs, 'home/plugin-data/hermes-line/line.db')]) {
    if (existsSync(c) && statSync(c).isFile()) return c
  }
  // A live run may not have created its store yet: accept the expected path under a run dir.
  return existsSync(join(abs, 'home')) || !abs.endsWith('.db') ? join(abs, 'home/plugin-data/hermes-line/line.db') : abs
}

function stamp() {
  return new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
}

function lines(child, onLine) {
  let buf = ''
  child.stdout.on('data', (d) => {
    buf += String(d)
    let i
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim()
      buf = buf.slice(i + 1)
      if (line) onLine(line)
    }
  })
}

async function main() {
  const args = parseArgs(process.argv.slice(2))
  if (args.help || (!args.store && !args.replay)) {
    console.log('usage: live-capture.mjs --store <trial run dir | line.db> [--call id] [--out dir] [--every 5] [--max-minutes 90]\n       live-capture.mjs --replay <evidence trial.json> [--out dir]')
    return
  }
  D.assertDesktopTools()
  const { home, userData } = D.prepareSandbox()
  const sandboxDb = join(home, 'plugin-data/hermes-line/line.db')
  const label = args.replay ? `replay-${basename(dirname(resolve(args.replay)))}` : (args.call || 'live')
  const outDir = resolve(args.out || join(HERE, 'screenshots/live', `${label}-${stamp()}`))
  mkdirSync(outDir, { recursive: true })
  const log = []
  const shots = []
  const children = []
  let stopping = false

  const cleanup = async (app) => {
    stopping = true
    for (const c of children) if (c.exitCode === null) c.kill('SIGTERM')
    if (app) await app.close().catch(() => {})
    await D.stopSandboxProcesses()
    writeFileSync(join(outDir, 'capture.json'), `${JSON.stringify({ label, store: args.replay ? 'replay' : args.store, shots, log }, null, 2)}\n`)
  }

  console.log(`[live-capture] launching the sandboxed Hermes Desktop (about 1-2 minutes)…`)
  const { app, page } = await D.launchDesktopWithPane({ home, userData, label })
  process.on('SIGINT', async () => {
    console.log('[live-capture] stopping')
    await cleanup(app)
    process.exit(130)
  })

  try {
    // Source store: the live trial's, or a replay written in real time into a scratch store.
    let source = args.store ? resolveStore(args.store) : null
    if (args.replay) {
      source = join(D.SANDBOX, 'replay-trial/home/plugin-data/hermes-line/line.db')
      mkdirSync(dirname(source), { recursive: true })
      const replay = spawn(D.PYTHON, ['-I', join(HERE, 'replay_trial.py'), '--evidence', resolve(args.replay), '--db', source], { stdio: ['ignore', 'pipe', 'inherit'] })
      children.push(replay)
      lines(replay, (l) => log.push({ at: Date.now(), replay: JSON.parse(l) }))
      // Let the replay create the store before the mirror's first read.
      await page.waitForTimeout(800)
    }
    console.log(`[live-capture] mirroring (read-only) ${source}`)
    const mirrorArgs = ['-I', join(HERE, 'live_mirror.py'), '--source', source, '--dest', sandboxDb, '--interval', '0.5']
    if (args.call) mirrorArgs.push('--call', args.call)
    const mirror = spawn(D.PYTHON, mirrorArgs, { stdio: ['ignore', 'pipe', 'inherit'] })
    children.push(mirror)

    const changes = []
    let firstSeen = false
    let ended = null
    lines(mirror, (l) => {
      const m = JSON.parse(l)
      log.push({ at: Date.now(), mirror: m })
      if (m.event === 'appeared' || m.event === 'state') changes.push(m)
    })

    let n = 0
    let tick = 0
    let nextTick = Date.now()
    const deadline = Date.now() + args.maxMinutes * 60000
    const snap = async (file, kind, extra = {}) => {
      await D.capture(app, page, file, [], { raise: 'on-blank' })
      const info = await page.evaluate(() => ({
        status: document.querySelector('.hlp-sr')?.textContent || null,
        clock: document.querySelector('.hlc-clock')?.textContent || null,
        // Transcript entries fully inside the visible scroll area (the 1440 x 900 window).
        visibleTranscript: (() => {
          const box = document.querySelector('.hlp-lines')?.getBoundingClientRect()
          if (!box) return null
          const items = [...document.querySelectorAll('.hlp-lines .hlp-group, .hlp-lines .hlp-event')]
          const inside = items.filter((el) => {
            const r = el.getBoundingClientRect()
            return r.top >= box.top && r.bottom <= Math.min(box.bottom, window.innerHeight)
          })
          return { count: inside.length, first: inside[0] ? inside[0].textContent.slice(0, 60) : null, viewportTop: Math.round(box.top), viewportHeight: Math.round(Math.min(box.bottom, window.innerHeight) - box.top) }
        })()
      }))
      shots.push({ file: basename(file), kind, at: new Date().toISOString(), ...info, ...extra })
      return info
    }

    while (!stopping && Date.now() < deadline) {
      // State changes first: wait until the pane shows the new state, then shoot.
      while (changes.length) {
        const c = changes.shift()
        const want = STATE_LABELS[c.state] || null
        const seen = async () => (await page.locator('.hlp-sr').first().textContent({ timeout: 1000 }).catch(() => null)) === want
        // One pane poll (5 s on a live call) is the most the pane can lag; if a newer state is already
        // waiting, this one may never be displayed, so do not hold the newer one back for it.
        let shown = await waitFor(page, seen, changes.length ? 1500 : 6500)
        if (!shown && !firstSeen) {
          // Before any call exists the pane polls every 30 s: refresh the view once for the first appearance.
          await page.reload()
          await page.locator('.hlp-root').first().waitFor({ timeout: 60000 })
          shown = await waitFor(page, seen, 10000)
        }
        n += 1
        const slug = STATE_SLUGS[c.state] || c.state
        // A state the pane never displayed is named so: the shot shows what the pane showed instead.
        const file = join(outDir, `state-${String(n).padStart(2, '0')}-${slug}${shown ? '' : '-not-shown'}.png`)
        await page.evaluate(() => document.fonts.ready)
        const info = await snap(file, 'state', { state: c.state, shownOnPane: shown, mirroredAt: new Date(c.at * 1000).toISOString() })
        cropPane(file)
        console.log(`[live-capture] ${c.state.padEnd(16)} ${shown ? 'shown' : 'NOT SHOWN'}  ${basename(file)}  clock=${info.clock ?? '-'}  transcript visible=${info.visibleTranscript ? info.visibleTranscript.count : '-'}`)
        firstSeen = true
        if (c.state === 'ended' || c.state === 'failed') ended = ended || Date.now()
      }
      if (Date.now() >= nextTick) {
        tick += 1
        nextTick += args.every * 1000
        await snap(join(outDir, `tick-${String(tick).padStart(4, '0')}.png`), 'tick')
      }
      if (ended && Date.now() - ended > 6000) break
      if (mirror.exitCode !== null && !changes.length && (ended || !firstSeen)) break
      await page.waitForTimeout(200)
    }
    statesSheet(outDir)
    console.log(`[live-capture] ${shots.filter((s) => s.kind === 'state').length} state shots, ${tick} periodic shots in ${outDir}`)
  } finally {
    await cleanup(app)
  }
}

async function waitFor(page, check, ms) {
  const end = Date.now() + ms
  while (Date.now() < end) {
    if (await check()) return true
    await page.waitForTimeout(250)
  }
  return false
}

function cropPane(file) {
  // The pane sits at x = 1060..1440 in the 1440 px window (2x capture); keep a 28 px app strip.
  execFileSync(D.MAGICK, [file, '-crop', `${(380 + 28) * 2}x${D.H * 2}+${(1060 - 28) * 2}+0`, '+repage', file.replace(/\.png$/, '-pane.png')])
}

function statesSheet(outDir) {
  const crops = execFileSync('/bin/ls', [outDir], { encoding: 'utf8' }).split('\n').filter((f) => /^state-\d+-.*-pane\.png$/.test(f)).sort()
  if (!crops.length) return
  const args = ['montage']
  for (const f of crops) args.push('-label', f.replace(/^state-\d+-/, '').replace(/-pane\.png$/, '').replace(/-not-shown$/, ' (not shown by the pane)'), join(outDir, f))
  args.push('-font', '/System/Library/Fonts/Helvetica.ttc', '-tile', `${crops.length}x1`, '-geometry', '+16+12', '-pointsize', '26', '-background', '#e9e9ec', join(outDir, 'states-sheet.png'))
  execFileSync(D.MAGICK, args)
}

await main()
