// Live-capture path (tools/pane-preview/live-capture.mjs): offline checks, no Desktop, no network.
// Run with: node --test tests/l3/pane.test.mjs tests/l3/browser.test.mjs tests/l3/live.test.mjs
import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, rmSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import { STATE_LABELS } from '../../tools/pane-preview/live-states.mjs'
import { HERMES, loadRender, REPO } from './bundle.mjs'

const PY = join(HERMES, 'venv/bin/python')
const EVIDENCE = join(REPO, 'evidence/trial/call_2243b7f205194ab5/trial.json')
const sha = (p) => createHash('sha256').update(readFileSync(p)).digest('hex')

test('the capture waits for exactly the labels the pane announces', async () => {
  const R = await loadRender()
  const fromPlugin = Object.fromEntries(Object.entries(R.pane.STATE_META).map(([k, v]) => [k, v.label]))
  assert.deepEqual(STATE_LABELS, fromPlugin)
})

test('replayed trial call mirrors read-only: source untouched, states in order, no real number in the sandbox', { timeout: 120000 }, () => {
  const root = join(homedir(), '.hermes/cache/scratch/hermes-line-l3/test-live')
  rmSync(root, { recursive: true, force: true })
  mkdirSync(join(root, 'trial'), { recursive: true })
  mkdirSync(join(root, 'sandbox'), { recursive: true })
  const source = join(root, 'trial/line.db')
  const dest = join(root, 'sandbox/line.db')
  execFileSync(PY, ['-I', join(REPO, 'tools/pane-preview/replay_trial.py'), '--evidence', EVIDENCE, '--db', source, '--speed', '50'], { stdio: 'ignore' })
  // Checkpoint the replay's WAL so the main file is the whole store, then fingerprint it.
  execFileSync(PY, ['-I', '-c', `import sqlite3; c=sqlite3.connect(${JSON.stringify(source)}); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()`])
  const before = sha(source)
  const run = spawnSync(PY, ['-I', join(REPO, 'tools/pane-preview/live_mirror.py'), '--source', source, '--dest', dest, '--interval', '0.05', '--linger', '0'], { encoding: 'utf8', timeout: 60000 })
  assert.equal(run.status, 0, run.stderr)
  assert.equal(sha(source), before, 'the mirror never writes the trial store')
  const events = run.stdout.trim().split('\n').map((l) => JSON.parse(l))
  assert.equal(events[0].event, 'appeared')
  assert.equal(events.at(-1).state, 'ended')
  const check = execFileSync(PY, ['-I', '-c', `
import json, sqlite3, sys
sys.path.insert(0, ${JSON.stringify(REPO)})
from plugin.line.store.db import Store
from plugin.line.server.desktop_api import snapshot
c = snapshot(Store(${JSON.stringify(dest)}))['calls'][0]
con = sqlite3.connect(${JSON.stringify(dest)})
nums = [r[0] for r in con.execute('select counterpart_e164 from calls')] + [r[0] for r in con.execute('select destination_e164 from tasks')]
print(json.dumps({'state': c['state'], 'lines': len(c['transcript']), 'end_reason': c['end_reason'], 'numbers': nums, 'phases': [p['phase'] for p in c['phases']], 'summary': c['summary']}))
`], { encoding: 'utf8' })
  const got = JSON.parse(check)
  assert.equal(got.state, 'ended')
  assert.deepEqual(got.phases, ['dialing', 'person', 'owner_ringing', 'fallback', 'ended'], 'L1 round 37 phase list, mirrored')
  assert.match(got.summary, /^Can did not pick up, so Hermes asked the fallback question/)
  assert.equal(got.lines, 19)
  assert.equal(got.end_reason, 'completed')
  for (const n of got.numbers) assert.doesNotMatch(n, /\d{3}\D?\d{4}$/, `unmasked number in the sandbox: ${n}`)
  rmSync(root, { recursive: true, force: true })
})

test('the evidence replay produces the recorded state path', () => {
  const trial = JSON.parse(readFileSync(EVIDENCE, 'utf8'))
  const path = trial.events.filter((e) => e.kind === 'state').map((e) => e.data.to)
  assert.deepEqual(path, ['human_detected', 'ringing_owner', 'agent_talking'])
})
