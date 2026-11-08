// L3 Desktop pane tests. Offline; run with:  node --test tests/l3/
// Uses esbuild, React and the Hermes Desktop UI kit from the read-only Hermes checkout
// (HERMES_AGENT_DIR, default ~/.hermes/hermes-agent). Nothing is installed.

import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { after, before, describe, test } from 'node:test'

import { HERMES, loadRender, REPO } from './bundle.mjs'

const PLUGIN_PATH = join(REPO, 'plugin/desktop/plugin.js')
const SOURCE = readFileSync(PLUGIN_PATH, 'utf8')
const FIXTURES = join(REPO, 'tools/pane-preview/fixtures')
const INDEX = JSON.parse(readFileSync(join(FIXTURES, 'index.json'), 'utf8'))
const loadScene = (id) => JSON.parse(readFileSync(join(FIXTURES, `${id}.json`), 'utf8'))

const STATES = [
  'awaiting_approval',
  'dialing',
  'ivr',
  'on_hold',
  'human_detected',
  'ringing_owner',
  'owner_connected',
  'agent_talking',
  'ended',
  'failed'
]

let R
before(async () => {
  R = await loadRender()
})

const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms))

// Load a snapshot into a store without starting its timers.
async function storeFor(snapshot, mode = 'ok') {
  const fake = R.createFakeCtx({ snapshot, mode })
  const store = R.pane.createCallStore(fake.ctx)
  await store.refresh()
  return { fake, store }
}

async function render(snapshot, mode = 'ok') {
  const { store } = await storeFor(snapshot, mode)
  return R.renderToStaticMarkup(R.createElement(R.pane.CallsPane, { store }))
}

function button(html, label) {
  const re = new RegExp(`<button([^>]*)>(?:(?!</button>).)*?${label}(?:(?!<button).)*?</button>`)
  const m = html.match(re)
  return m ? { disabled: /\sdisabled=""/.test(m[1]) } : null
}

// -------------------------------------------------------------------------------------------
describe('admission: Desktop SDK surface only', () => {
  test('passes the Hermes catalog "desktop surface" lint', () => {
    const script = [
      'import sys, json',
      `sys.path.insert(0, ${JSON.stringify(HERMES)})`,
      'from hermes_cli.plugin_validate_desktop import desktop_surface_findings',
      `print(json.dumps(desktop_surface_findings(open(${JSON.stringify(PLUGIN_PATH)}).read())))`
    ].join('\n')
    const out = execFileSync('python3', ['-c', script], { encoding: 'utf8' })
    assert.deepEqual(JSON.parse(out), [])
  })

  test('imports only @hermes/plugin-sdk and react', () => {
    const specs = [...SOURCE.matchAll(/^\s*(?:import|export)\b[^'"]*?from\s+['"]([^'"]+)['"]/gm)].map((m) => m[1])
    assert.ok(specs.length > 0)
    for (const s of specs) assert.ok(['@hermes/plugin-sdk', 'react', 'react/jsx-runtime'].includes(s), `bad import ${s}`)
    assert.doesNotMatch(SOURCE, /\bimport\s*\(/, 'no dynamic import')
  })

  test('no app DOM reach, bare timers, eval or injected scripts', () => {
    assert.doesNotMatch(SOURCE, /\bdocument\.|\bwindow\./)
    assert.doesNotMatch(SOURCE, /(?<![\w.])(setInterval|setTimeout)\(/, 'timers go through ctx')
    assert.doesNotMatch(SOURCE, /(?<![\w.])eval\(|new Function\(|['"]script['"]/)
    assert.doesNotMatch(SOURCE, /dangerouslySetInnerHTML|innerHTML/)
  })

  test('theme tokens only: no literal colours', () => {
    const css = R.pane.PANE_CSS
    assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/)
    assert.doesNotMatch(css, /\b(rgb|rgba|hsl|hsla|oklch)\(/)
    assert.doesNotMatch(css, /:\s*(black|white)\b/)
  })

  test('respects prefers-reduced-motion and avoids transition: all', () => {
    assert.match(R.pane.PANE_CSS, /@media \(prefers-reduced-motion: reduce\)/)
    assert.doesNotMatch(R.pane.PANE_CSS, /transition:\s*all/)
  })

  test('no em or en dashes in the pane or the fixtures', () => {
    assert.doesNotMatch(SOURCE, /[–—]/)
    for (const f of readdirSync(FIXTURES)) assert.doesNotMatch(readFileSync(join(FIXTURES, f), 'utf8'), /[–—]/, f)
  })

  test('registers exactly one right-hand pane and nothing else', () => {
    const fake = R.createFakeCtx({ snapshot: { revision: 0, calls: [] } })
    R.plugin.register(fake.ctx)
    try {
      assert.equal(R.plugin.id, 'hermes-line')
      assert.equal(fake.contributions.length, 1)
      const [pane] = fake.contributions
      assert.equal(pane.area, 'panes')
      assert.equal(pane.data.placement, 'right')
      assert.equal(pane.data.width, R.pane.PANE_WIDTH)
      assert.equal(typeof pane.render, 'function')
    } finally {
      fake.dispose()
    }
  })
})

// -------------------------------------------------------------------------------------------
describe('fixtures follow the contract and are labelled sample', () => {
  const keys = ['id', 'task_title', 'direction', 'counterpart_masked', 'state', 'state_since', 'started_at', 'duration_s', 'mode', 'transcript', 'summary']

  test('index marks the data as sample and lists every file', () => {
    assert.equal(INDEX.sample, true)
    assert.match(INDEX.notice, /SAMPLE/)
    const files = readdirSync(FIXTURES).filter((f) => f !== 'index.json').sort()
    assert.deepEqual(INDEX.scenes.map((s) => s.file).sort(), files)
  })

  test('every snapshot matches the Call shape', () => {
    for (const scene of INDEX.scenes) {
      const snap = loadScene(scene.id)
      assert.equal(typeof snap.revision, 'number', scene.id)
      assert.ok(Array.isArray(snap.calls))
      for (const call of snap.calls) {
        assert.deepEqual(Object.keys(call).sort(), [...keys].sort(), `${scene.id}/${call.id}`)
        assert.ok(['outbound', 'inbound'].includes(call.direction))
        assert.ok(['agent', 'owner'].includes(call.mode))
        assert.ok(STATES.includes(call.state), call.state)
        assert.equal(typeof call.duration_s, 'number')
        for (const k of ['started_at', 'state_since']) assert.match(call[k], /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)$/, k)
        assert.ok(call.summary === null || typeof call.summary === 'string')
        let prev = 0
        for (const line of call.transcript) {
          assert.deepEqual(Object.keys(line).sort(), ['seq', 'speaker', 'text', 'ts'])
          assert.ok(['hermes', 'business', 'owner', 'system'].includes(line.speaker))
          assert.ok(line.seq > prev, 'seq increases')
          prev = line.seq
          assert.ok(!Number.isNaN(Date.parse(line.ts)))
        }
      }
    }
  })

  test('all ten states are covered', () => {
    const seen = new Set(INDEX.scenes.flatMap((s) => loadScene(s.id).calls.map((c) => c.state)))
    for (const s of STATES) assert.ok(seen.has(s), s)
  })

  test('sample numbers and names only, codes masked', () => {
    for (const scene of INDEX.scenes) {
      for (const call of loadScene(scene.id).calls) {
        assert.equal(call.counterpart_masked, '+1 555-01••')
        assert.match(call.task_title, /Sample /)
        for (const line of call.transcript) assert.doesNotMatch(line.text, /\d(?:[ -]?\d){3,}/, 'unmasked digit run')
      }
    }
  })
})

// -------------------------------------------------------------------------------------------
describe('pure helpers', () => {
  test('formatClock', () => {
    const f = R.pane.formatClock
    assert.equal(f(0), '0:00')
    assert.equal(f(7.9), '0:07')
    assert.equal(f(2292), '38:12')
    assert.equal(f(3729), '1:02:09')
    assert.equal(f(-5), '0:00')
    assert.equal(f(NaN), '0:00')
  })

  test('parseTs accepts ISO and epoch seconds or milliseconds', () => {
    const p = R.pane.parseTs
    assert.equal(p('2026-11-07T17:00:00Z'), Date.UTC(2026, 10, 7, 17))
    assert.equal(p(1_800_000_000), 1_800_000_000_000)
    assert.equal(p(1_800_000_000_000), 1_800_000_000_000)
    assert.equal(p('nope'), null)
    assert.equal(p(undefined), null)
  })

  test('hold clock comes from server timestamps, not the desktop clock', () => {
    const call = loadScene('on_hold').calls[0]
    const atReceive = R.pane.callClocks(call, 1000, 1000)
    assert.equal(R.pane.formatClock(atReceive.inState), '38:12')
    assert.equal(R.pane.formatClock(R.pane.callClocks(call, 1000, 61_000).inState), '39:12')
    const ended = loadScene('ended').calls[0]
    assert.equal(R.pane.callClocks(ended, 0, 999_999).total, ended.duration_s, 'ended calls do not tick')
  })

  test('state_since wins when the backend sends it', () => {
    const call = { ...loadScene('on_hold').calls[0], state_since: '2026-11-07T17:30:33Z' }
    assert.equal(R.pane.formatClock(R.pane.callClocks(call, 0, 0).inState), '10:00')
  })

  test('actionsFor per state', () => {
    const a = R.pane.actionsFor
    assert.deepEqual(a('on_hold'), { take_over: true, say: false, hang_up: true })
    assert.deepEqual(a('human_detected'), { take_over: true, say: true, hang_up: true })
    assert.deepEqual(a('ringing_owner'), { take_over: false, say: true, hang_up: true })
    assert.deepEqual(a('owner_connected'), { take_over: false, say: false, hang_up: true })
    assert.deepEqual(a('ended'), { take_over: false, say: false, hang_up: false })
    assert.deepEqual(a('failed'), { take_over: false, say: false, hang_up: false })
    assert.deepEqual(a('something_new'), { take_over: false, say: false, hang_up: true })
  })

  test('splitMasked finds mask tokens only', () => {
    assert.deepEqual(R.pane.splitMasked('code is [CODE].'), [
      { kind: 'text', value: 'code is ' },
      { kind: 'mask', value: 'code' },
      { kind: 'text', value: '.' }
    ])
    assert.deepEqual(R.pane.splitMasked('press [1]'), [{ kind: 'text', value: 'press [1]' }])
    assert.deepEqual(R.pane.splitMasked(null), [])
  })

  test('transcript groups break on speaker, system lines and gaps over a minute', () => {
    const lines = loadScene('human_detected').calls[0].transcript
    const groups = R.pane.groupTranscript(lines)
    const hold = groups.filter((g) => g.speaker === 'business' && g.lines.some((l) => l.seq === 8 || l.seq === 9))
    assert.equal(hold.length, 2, '9:24 and 9:40 business lines are separate groups')
    assert.ok(groups.filter((g) => g.speaker === 'system').every((g) => g.lines.length === 1))
  })

  test('pickFocusCall prefers a call that needs the owner', () => {
    const calls = [
      { id: 'a', state: 'ended', started_at: '2026-11-07T18:00:00Z' },
      { id: 'b', state: 'on_hold', started_at: '2026-11-07T17:00:00Z' },
      { id: 'c', state: 'ringing_owner', started_at: '2026-11-07T16:00:00Z' }
    ]
    assert.equal(R.pane.pickFocusCall(calls).id, 'c')
    assert.equal(R.pane.pickFocusCall([]), null)
  })

  test('isConflict reads the bridge 409 shape', () => {
    assert.equal(R.pane.isConflict({ statusCode: 409 }), true)
    assert.equal(R.pane.isConflict(new Error('409: stale revision')), true)
    assert.equal(R.pane.isConflict(new Error('500: boom')), false)
    assert.equal(R.pane.isConflict(null), false)
  })

  test('failureReason falls back to the last system line', () => {
    assert.match(R.pane.failureReason(loadScene('failed').calls[0]), /No answer after 3 tries/)
    assert.equal(R.pane.failureReason({ summary: null, transcript: [] }), R.pane.COPY.failedFallback)
  })

  test('normalizeSnapshot rejects malformed payloads', () => {
    assert.equal(R.pane.normalizeSnapshot(null), null)
    assert.equal(R.pane.normalizeSnapshot({ calls: 'x' }), null)
    assert.deepEqual(R.pane.normalizeSnapshot({ revision: 2, calls: [{ id: 'a' }, null, { id: '' }] }).calls, [{ id: 'a' }])
  })
})

// -------------------------------------------------------------------------------------------
describe('store: controls, revisions, events, notifications', () => {
  test('controls POST to the contract paths with idempotency key and revision', async () => {
    const snap = loadScene('agent_talking')
    const { fake, store } = await storeFor(snap)
    const call = store.$snapshot.get().calls[0]
    assert.deepEqual(await store.act(call, 'take_over'), { ok: true })
    assert.deepEqual(await store.act(call, 'say', { text: 'Ask for a window table.' }), { ok: true })
    assert.deepEqual(await store.act(call, 'hang_up'), { ok: true })
    assert.deepEqual(
      fake.posts.map((p) => p.path),
      ['/calls/call_sample_bistro/take_over', '/calls/call_sample_bistro/say', '/calls/call_sample_bistro/hang_up']
    )
    for (const p of fake.posts) {
      assert.equal(p.method, 'POST')
      assert.equal(p.body.revision, snap.revision)
      assert.match(p.body.idempotency_key, /^[0-9a-f-]{32,36}$/)
    }
    assert.equal(new Set(fake.posts.map((p) => p.body.idempotency_key)).size, 3, 'one key per action')
    assert.equal(fake.posts[1].body.text, 'Ask for a window table.')
  })

  test('a 409 reports a conflict and re-fetches the snapshot', async () => {
    const { fake, store } = await storeFor(loadScene('agent_talking'), 'conflict')
    const before = fake.gets
    const result = await store.act(store.$snapshot.get().calls[0], 'take_over')
    assert.deepEqual(result, { ok: false, conflict: true })
    assert.ok(fake.gets > before)
  })

  test('clocks freeze while the snapshot is stale', async () => {
    const fake = R.createFakeCtx({ snapshot: loadScene('on_hold'), mode: 'stale' })
    const store = R.pane.createCallStore(fake.ctx)
    store.start()
    try {
      await tick(10)
      await store.refresh()
      assert.equal(store.$stale.get(), true)
      const frozen = store.$now.get()
      await tick(1100)
      assert.equal(store.$now.get(), frozen)
    } finally {
      fake.dispose()
    }
  })

  test('backend unavailable: error before any snapshot, stale after one', async () => {
    const { store: s1 } = await storeFor(loadScene('on_hold'), 'error')
    assert.equal(s1.$status.get(), 'error')
    const { store: s2 } = await storeFor(loadScene('on_hold'), 'stale')
    await s2.refresh()
    assert.equal(s2.$stale.get(), true)
    assert.ok(s2.$snapshot.get(), 'last known state is kept')
  })

  test('call.updated event re-fetches only when the revision moved', async () => {
    const snap = loadScene('on_hold')
    const fake = R.createFakeCtx({ snapshot: snap })
    const store = R.pane.createCallStore(fake.ctx)
    store.start()
    try {
      await tick(10)
      const base = fake.gets
      fake.emit('plugin.hermes-line.call.updated', { id: 'call_sample_airline', revision: snap.revision })
      await tick(10)
      assert.equal(fake.gets, base)
      fake.emit('plugin.hermes-line.call.updated', { id: 'call_sample_airline', revision: snap.revision + 1 })
      await tick(10)
      assert.equal(fake.gets, base + 1)
    } finally {
      fake.dispose()
    }
  })

  test('a person answering raises one OS notification, never on first load', async () => {
    const { fake, store } = await storeFor(loadScene('human_detected'))
    assert.equal(fake.notifications.length, 0, 'state seen on first load does not notify')

    const { fake: f2, store: s2 } = await storeFor(loadScene('on_hold'))
    f2.setSnapshot(loadScene('human_detected'))
    await s2.refresh()
    f2.setSnapshot(loadScene('ringing_owner'))
    await s2.refresh()
    assert.equal(f2.notifications.length, 1)
    assert.equal(f2.notifications[0].title, 'A person answered')
    assert.match(f2.notifications[0].body, /Sample Airline/)
    assert.ok(store && fake)
  })

  test('disposing the plugin stops polling', async () => {
    const fake = R.createFakeCtx({ snapshot: loadScene('on_hold') })
    R.plugin.register(fake.ctx)
    await tick(20)
    fake.dispose()
    const gets = fake.gets
    await tick(1200)
    assert.equal(fake.gets, gets)
  })
})

// -------------------------------------------------------------------------------------------
describe('phases and shared rules', () => {
  test('the ended airline call reads as menu, hold, person, outcome', () => {
    const call = loadScene('ended').calls[0]
    const phases = R.pane.callPhases(call)
    assert.deepEqual(phases.map((p) => p.key), ['menu', 'hold', 'person', 'outcome'])
    const secs = phases.map((p) => R.pane.phaseSeconds(p, call, { total: call.duration_s }))
    assert.deepEqual(secs.slice(0, 3).map(R.pane.formatClock), ['0:41', '38:32', '1:38'])
    assert.ok(phases.every((p) => p.lines.every((l) => !/^(On hold|Person answered|Call ended)$/.test(l.text))), 'openers are not lines')
  })

  test('a call with no hold skips the hold phase', () => {
    const call = loadScene('agent_talking').calls[0]
    assert.deepEqual(R.pane.callPhases(call).map((p) => p.key), ['menu', 'person'])
  })

  test('state_since opens the live phase when no boundary line exists', () => {
    const call = { ...loadScene('on_hold').calls[0], transcript: [] }
    const phases = R.pane.callPhases(call)
    assert.deepEqual(phases.map((p) => p.key), ['menu', 'hold'])
    assert.equal(phases[1].start, Date.parse(call.state_since))
  })

  test('hold time after a person answers', () => {
    assert.equal(R.pane.formatClock(R.pane.holdSeconds(loadScene('human_detected').calls[0])), '38:32')
    assert.equal(R.pane.holdSeconds(loadScene('agent_talking').calls[0]), null)
  })

  test('primary action follows the state and the plan', () => {
    const c = (state, mode) => ({ state, mode })
    assert.equal(R.pane.primaryAction(c('human_detected', 'owner')), null, 'owner phone is about to ring')
    assert.equal(R.pane.primaryAction(c('ringing_owner', 'owner')), null)
    assert.equal(R.pane.primaryAction(c('human_detected', 'agent')), 'take_over')
    assert.equal(R.pane.primaryAction(c('agent_talking', 'agent')), 'take_over')
    assert.equal(R.pane.primaryAction(c('on_hold', 'owner')), null)
    assert.equal(R.pane.primaryAction(c('owner_connected', 'owner')), null)
  })

  test('summary lead sentence survives abbreviations', () => {
    assert.deepEqual(R.pane.splitSummary('Moved it to 9:40 a.m. departure. Fee was $75.'), {
      lead: 'Moved it to 9:40 a.m. departure.',
      rest: 'Fee was $75.'
    })
    assert.deepEqual(R.pane.splitSummary('One line only'), { lead: 'One line only', rest: '' })
  })

  test('mask tokens from the contract all render as chips', () => {
    assert.deepEqual(
      R.pane.splitMasked('[CODE] [CARD] [NUMBER]').filter((p) => p.kind === 'mask').map((p) => p.value),
      ['code', 'card', 'number']
    )
  })
})

// -------------------------------------------------------------------------------------------
describe('round 4: one coherent sample timeline', () => {
  const scenes = INDEX.scenes.filter((sc) => loadScene(sc.id).calls.length)
  const airlineAt = (id) => loadScene(id).calls.find((c) => c.id === 'call_sample_airline')

  test('every call agrees with its scene clock: state_since, last line and duration', () => {
    for (const sc of scenes) {
      const now = Date.parse(sc.now)
      for (const call of loadScene(sc.id).calls) {
        const start = Date.parse(call.started_at)
        const since = Date.parse(call.state_since)
        const last = call.transcript.length ? Date.parse(call.transcript.at(-1).ts) : start
        assert.ok(start <= since && since <= now, `${sc.id}/${call.id}: started <= state_since <= now`)
        assert.ok(last <= now, `${sc.id}/${call.id}: nothing said after now`)
        let prev = start
        for (const l of call.transcript) {
          assert.ok(Date.parse(l.ts) >= prev, `${sc.id}/${call.id}: times only move forward`)
          prev = Date.parse(l.ts)
        }
        const live = R.pane.stateMeta(call.state).live
        if (live && call.state !== 'awaiting_approval') assert.equal(call.duration_s, (now - start) / 1000, `${sc.id}/${call.id}: live duration = now - start`)
        if (call.state === 'ended') assert.equal(call.duration_s, (last - start) / 1000, `${sc.id}/${call.id}: ended duration = last line - start`)
      }
    }
  })

  test('the airline call is one story: each state is a cut of the same transcript', () => {
    const full = airlineAt('ended').transcript
    for (const id of ['ivr', 'on_hold', 'human_detected', 'ringing_owner', 'owner_connected']) {
      const t = airlineAt(id).transcript
      assert.deepEqual(t, full.slice(0, t.length), id)
    }
    const order = ['ivr', 'on_hold', 'human_detected', 'ringing_owner', 'owner_connected', 'ended'].map((id) => Date.parse(INDEX.scenes.find((x) => x.id === id).now))
    assert.deepEqual(order, [...order].sort((a, b) => a - b), 'scenes in the order of the day')
  })

  test('phase times add up to the call length; replies are seconds apart', () => {
    const call = airlineAt('ended')
    const phases = R.pane.callPhases(call).filter((p) => p.key !== 'outcome')
    const sum = phases.reduce((a, p) => a + R.pane.phaseSeconds(p, call, { total: call.duration_s }), 0)
    assert.equal(sum, call.duration_s)
    assert.equal(R.pane.formatClock(call.duration_s), '40:51')
    const answered = call.transcript.find((l) => l.text === 'Person answered')
    const reply = call.transcript.find((l) => l.speaker === 'hermes' && Date.parse(l.ts) > Date.parse(answered.ts))
    const gap = (Date.parse(reply.ts) - Date.parse(answered.ts)) / 1000
    assert.ok(gap > 0 && gap <= 10, `Hermes replies ${gap}s after the answer`)
  })

  test('the quote is the person-answer moment: same call time as the strip and the transcript', async () => {
    const call = airlineAt('human_detected')
    const quoted = R.pane.quotedLine(call)
    assert.equal(quoted.ts, call.state_since, 'quoted line time = person answered (state_since)')
    const phases = R.pane.callPhases(call)
    const hold = phases.find((p) => p.key === 'hold')
    assert.equal(Date.parse(quoted.ts), hold.end, 'hold ends when they speak')
    assert.equal(R.pane.callTime(call, quoted.ts), '39:13')
    const html = await render(loadScene('human_detected'))
    assert.match(html, /Said 39:13 into the call/)
    assert.match(html, /After 38:32 on hold\./)
    assert.match(html, /Menu 0:41/)
  })

  test('transcript and quote times are call time, matching the strip', async () => {
    const call = airlineAt('human_detected')
    assert.equal(R.pane.callTime(call, '2026-11-07T17:02:21Z'), '0:41', 'hold starts where Menu 0:41 ends')
    assert.equal(R.pane.callTime(call, '2026-11-07T17:40:53Z'), '39:13', 'answered = 0:41 + 38:32')
    const html = await render(loadScene('human_detected'))
    assert.match(html, /Started 9:01 AM/)
    assert.match(html, /Said 39:13 into the call/)
    assert.match(html, /<time dateTime="2026-11-07T17:40:56.000Z" title="9:40:56 AM">39:16<\/time>/, 'Hermes reply 3 s after the answer')
    assert.match(html, /<span class="hlc-label">Hold 38:32<\/span>/)
    assert.match(html, /Times are call time/)
  })

  test('the transcript ends in a fade, not a hard cut, with room to scroll the last row clear', () => {
    const rule = R.pane.PANE_CSS.match(/\.hlp-lines \{([^}]*)\}/)[1]
    assert.match(rule, /mask-image: linear-gradient\(to bottom, transparent 0, black 0\.75rem, black calc\(100% - 3\.75rem\), transparent calc\(100% - 0\.75rem\), transparent\)/)
    assert.match(rule, /padding: 0\.375rem 1rem 4\.25rem/)
  })

  test('card ink and track come from card-scoped tokens (measured by preview.mjs contrast)', () => {
    const css = R.pane.PANE_CSS
    assert.match(css, /--hlc-ink-2: color-mix\(in srgb, var\(--ui-base\) 80%, transparent\)/)
    assert.match(css, /--hlc-track: color-mix\(in srgb, var\(--ui-base\) 56%, transparent\)/)
    for (const sel of ['.hlc-task-meta', '.hlc-quote-by', '.hlc-sub', '.hlc-heard-at', '.hlc-label']) {
      assert.match(css, new RegExp(`${sel.replace('.', '\\.')} \\{[^}]*color: var\\(--hlc-ink-2\\)`), sel)
    }
    assert.match(css, /\.hlc-bar \{[^}]*background: var\(--hlc-track\)/)
  })
})

// -------------------------------------------------------------------------------------------
describe('round 7: live polling (B7), explicit phases (B9), bubble order (B12)', () => {
  const DELTA2 = JSON.parse(readFileSync(join(REPO, 'evidence/trial/call_2243b7f205194ab5/trial.json'), 'utf8')).call

  test('B7: the snapshot is re-read every second while a call is live, every 30 s when idle', async () => {
    assert.equal(R.pane.POLL_LIVE_MS, 1000)
    assert.equal(R.pane.POLL_IDLE_MS, 30000)
    const live = R.createFakeCtx({ snapshot: loadScene('human_detected') })
    const s1 = R.pane.createCallStore(live.ctx)
    s1.start()
    const idle = R.createFakeCtx({ snapshot: loadScene('empty') })
    const s2 = R.pane.createCallStore(idle.ctx)
    s2.start()
    try {
      await tick(3300)
      assert.ok(live.gets >= 3, `live: ${live.gets} fetches in 3.3 s`)
      assert.equal(idle.gets, 1, 'idle: only the first fetch')
    } finally {
      live.dispose()
      idle.dispose()
    }
  })

  // Delta 2 as it ended (57 s; owner did not answer; fallback), with an explicit phase list.
  const deltaEnded = (phases) => ({ ...DELTA2, transcript: DELTA2.transcript, phases })
  const strip = async (call) => {
    const html = await render({ revision: 1, calls: [call] })
    return [...html.matchAll(/<span class="hlc-label">([^<]*)<\/span>/g)].map((m) => m[1])
  }

  test('B9: the strip comes from the explicit phase list when present', async () => {
    const call = deltaEnded([
      { phase: 'menu', since: '2026-11-06T01:37:02.361+00:00' },
      { phase: 'person', since: '2026-11-06T01:37:17.545+00:00' },
      { phase: 'outcome', since: '2026-11-06T01:37:59.881+00:00' }
    ])
    assert.deepEqual(await strip(call), ['Menu 0:15', 'Person 0:42'])
    assert.deepEqual(await strip({ ...call, phases: undefined }), ['Menu 0:57'], 'absent: today\'s derivation')
  })

  test('B9: engine phase names, repeats and a state ahead of the list', () => {
    const call = {
      state: 'agent_talking', started_at: '2026-11-06T01:37:02Z', state_since: '2026-11-06T01:37:38Z', duration_s: 40, transcript: [],
      phases: [{ name: 'ivr', start: '2026-11-06T01:37:02Z' }, { name: 'ivr', start: '2026-11-06T01:37:05Z' }, { name: 'talk', start: '2026-11-06T01:37:17Z' }]
    }
    const ph = R.pane.callPhases(call)
    assert.deepEqual(ph.map((p) => p.key), ['menu', 'person'])
    assert.equal(ph[1].end, null, 'the live phase stays open')
    const ahead = R.pane.callPhases({ ...call, state: 'ended', state_since: '2026-11-06T01:37:59Z' })
    assert.deepEqual(ahead.map((p) => p.key), ['menu', 'person', 'outcome'])
    assert.equal(ahead[1].end, Date.parse('2026-11-06T01:37:59Z'))
  })

  test('B9: an unusable list falls back to the derivation', () => {
    const call = { ...loadScene('ended').calls[0] }
    const derived = R.pane.callPhases(call).map((p) => p.key)
    for (const bad of [[], [{ phase: 'lunch', since: 'x' }], [{ since: '2026-11-07T17:01:40Z' }], 'menu', null]) {
      assert.deepEqual(R.pane.callPhases({ ...call, phases: bad }).map((p) => p.key), derived, JSON.stringify(bad))
    }
  })

  test('round 8: L1 round 37 phase names map onto the strip', () => {
    assert.deepEqual(
      Object.fromEntries(['dialing', 'menu', 'assistant', 'hold', 'person', 'owner_ringing', 'bridged', 'fallback', 'ended'].map((n) => [n, R.pane.PHASE_ALIASES[n]])),
      { dialing: 'menu', menu: 'menu', assistant: 'menu', hold: 'hold', person: 'person', owner_ringing: 'person', bridged: 'person', fallback: 'person', ended: 'outcome' }
    )
  })

  test('round 8: Delta 2 with L1 phases reads Menu 0:15, Person 0:42; dialing before started_at is clamped', async () => {
    const t = (s) => `2026-11-06T01:37:${s}+00:00`
    const call = deltaEnded([
      { phase: 'dialing', since: t('00.000') },
      { phase: 'person', since: t('17.545') },
      { phase: 'owner_ringing', since: t('18.743') },
      { phase: 'fallback', since: t('38.749') },
      { phase: 'ended', since: t('59.881') }
    ])
    assert.deepEqual(await strip(call), ['Menu 0:15', 'Person 0:42'])
    assert.equal(R.pane.callPhases(call)[0].start, Date.parse(call.started_at), 'clamped to started_at')
  })

  test('round 8: a returning phase gets its own column (hold, owner ring, back to hold)', async () => {
    const t = (s) => `2026-11-07T17:${s}Z`
    const call = {
      id: 'c', task_title: 'Sample', direction: 'outbound', counterpart_masked: '+1 555-01••', mode: 'owner',
      state: 'on_hold', started_at: t('00:00'), state_since: t('20:30'), duration_s: 1500, transcript: [], summary: null,
      phases: [{ phase: 'dialing', since: t('00:00') }, { phase: 'menu', since: t('00:05') }, { phase: 'hold', since: t('01:00') },
        { phase: 'owner_ringing', since: t('20:00') }, { phase: 'hold', since: t('20:30') }]
    }
    assert.deepEqual(R.pane.callPhases(call).map((p) => p.key), ['menu', 'hold', 'person', 'hold'])
    const labels = await strip(call)
    assert.equal(labels.length, 4)
    assert.deepEqual(labels.slice(0, 3), ['Menu 1:00', 'Hold 19:00', 'Person 0:30'])
    assert.equal(labels[3], 'Hold now')
  })

  test('round 9: the ended card shows the outcome sentence and the fallback note only', async () => {
    const summary = 'Can did not pick up, so Hermes asked the fallback question and got a reply (not verified); the call ended after 57 s. Hermes Line: Call completed (+1 800-•••-1212, "Delta reservations (hold-line test)", 0m57s). Can did not pick up within 20 s, so Hermes asked: "What is the checked-bag fee?" The reply was: "You\'re welcome." On the call: They: "What can I help you with today?" / Hermes: "Thank you."'
    assert.equal(R.pane.cardRest(summary), 'Can did not pick up within 20 s, so Hermes asked: "What is the checked-bag fee?" The reply was: "You\'re welcome."')
    const html = await render({ revision: 1, calls: [{ ...DELTA2, summary }] })
    const card = html.slice(html.indexOf('class="hlc-field"'), html.indexOf('class="hlc-strip"'))
    assert.doesNotMatch(card, /Hermes Line:|On the call:/)
    assert.match(card, /class="hlc-rest">Can did not pick up within 20 s/)
    // Older summaries without those parts keep their whole rest.
    assert.equal(R.pane.cardRest(loadScene('ended').calls[0].summary), 'You approved the $75 change fee on the call. The new confirmation code is masked here; the airline is emailing it and texting the itinerary.')
    assert.equal(R.pane.cardRest('No answer; the call ended after 30 s. Hermes Line: No answer (+1 555-01••, "x", 0m30s).'), '')
  })

  test('round 8: the ended headline is only the outcome sentence of the round-37 summary', async () => {
    const summary = 'Can did not pick up, so Hermes asked the fallback question and got a reply (not verified); the call ended after 57 s. Hermes Line: Call completed (+1 800-•••-1212, "Delta reservations (hold-line test)", 0m57s). On the call: They: "What can I help you with today?" / Hermes: "Thank you."'
    const html = await render({ revision: 1, calls: [{ ...DELTA2, summary }] })
    const head = html.match(/class="hlc-headline">([^<]*)</)[1]
    assert.equal(head, 'Can did not pick up, so Hermes asked the fallback question and got a reply (not verified); the call ended after 57 s.')
    assert.doesNotMatch(head, /Hermes Line:/)
    for (const s of ['No person reached; Delta’s automated assistant ended the call after 57 s. Hermes Line: x.', 'A person answered and Sam was connected; the call ended after 2 min 5 s. Hermes Line: x.']) {
      assert.match(R.pane.splitSummary(s).lead, /after (57 s|2 min 5 s)\.$/)
    }
  })

  test('B12: hidden lines never merge their neighbours; bubbles read oldest first', async () => {
    // Delta 2 while the owner's phone rings: the field quotes the business line (seq 6).
    const ringing = { ...DELTA2, state: 'ringing_owner', state_since: DELTA2.transcript[7].ts, transcript: DELTA2.transcript.slice(0, 8) }
    const quoted = R.pane.quotedLine(ringing)
    assert.equal(quoted.seq, 6)
    const groups = R.pane.transcriptGroups(ringing.transcript, (l) => l.seq === quoted.seq || l.ts === ringing.state_since)
    const hermes = groups.filter((g) => g.speaker === 'hermes').map((g) => g.lines.map((l) => l.seq))
    assert.ok(hermes.some((seqs) => seqs.length === 1 && seqs[0] === 7), 'seq 7 is its own bubble')
    assert.ok(!hermes.some((seqs) => seqs.includes(5) && seqs.includes(7)), 'seq 5 and 7 never share a bubble')
    for (const g of groups) assert.deepEqual(g.lines.map((l) => l.seq), [...g.lines.map((l) => l.seq)].sort((a, b) => a - b), 'oldest first inside')
    assert.ok(groups[0].lines[0].seq > groups.at(-1).lines[0].seq, 'groups newest first')
    const html = await render({ revision: 1, calls: [ringing] })
    const i7 = html.indexOf('Thank you. Please hold for just a moment')
    const i5 = html.indexOf("Sorry, I didn&#x27;t catch that")
    assert.ok(i7 > 0 && i5 > i7, 'the later Hermes line is shown above the earlier one, in separate bubbles')
    const between = html.slice(i7, i5)
    assert.match(between, /hlp-who-name/, 'a new bubble header separates them')
  })
})

// -------------------------------------------------------------------------------------------
describe('render: Signal (the shipped direction) for every scene, through the real UI kit', () => {
  // true = shown and enabled, false = not rendered (only what the state allows is shown).
  const buttons = {
    awaiting_approval: { 'Cancel call': true, 'Take over': false, 'Say this': false },
    dialing: { 'Take over': false, 'Say this': false, 'Hang up': true },
    ivr: { 'Take over': true, 'Say this': true, 'Hang up': true },
    on_hold: { 'Take over': true, 'Say this': false, 'Hang up': true },
    human_detected: { 'Take over': true, 'Say this': true, 'Hang up': true },
    ringing_owner: { 'Take over': false, 'Say this': true, 'Hang up': true },
    owner_connected: { 'Take over': false, 'Say this': false, 'Hang up': true },
    agent_talking: { 'Take over': true, 'Say this': true, 'Hang up': true },
    ended: { 'Take over': false, 'Say this': false, 'Hang up': false },
    failed: { 'Take over': false, 'Say this': false, 'Hang up': false }
  }
  const primary = (html) => /<button[^>]*bg-primary[^>]*>(?:(?!<\/button>).)*Take over/.test(html)

  for (const [id, want] of Object.entries(buttons)) {
    test(id, async () => {
      const snap = loadScene(id)
      const html = await render(snap)
      const call = R.pane.pickFocusCall(snap.calls)
      assert.match(html, new RegExp(`class="hlc-field" data-tone="${R.pane.stateMeta(call.state).tone}"`))
      assert.match(html, new RegExp(`role="status" aria-live="polite">${R.pane.stateMeta(call.state).label}<`), 'one live status')
      for (const [label, shown] of Object.entries(want)) {
        const b = button(html, label)
        if (shown) assert.ok(b && !b.disabled, `${label} shown and enabled`)
        else assert.equal(b, null, `${label} not rendered`)
      }
      if (want['Hang up']) assert.match(html, /<button[^>]*bg-destructive[^>]*>(?:(?!<\/button>).)*Hang up/, 'Hang up is destructive')
      assert.equal(primary(html), R.pane.primaryAction(call) === 'take_over', 'primary follows the state')
      // The line the field quotes is not repeated in the transcript.
      const quoted = R.pane.quotedLine(call)
      if (quoted) {
        const plain = quoted.text.replace(/\[[A-Z_]+\]/g, '').replace(/'/g, '&#x27;')
        assert.equal(html.split(plain.slice(0, 40)).length - 1, 1, 'quoted line appears once')
      }
      // "Latest" marks the newest thing said, and only when the transcript shows it.
      const newest = R.pane.newestUtterance(call)
      const latestShown = newest && !(quoted && quoted.seq === newest.seq)
      assert.equal((html.match(/data-newest="true"/g) || []).length, latestShown ? 1 : 0, 'newest marker')
      // The line that announced the current state is not repeated in the transcript.
      const hidden = call.transcript.find((l) => l.speaker === 'system' && l.ts === call.state_since)
      if (hidden) assert.doesNotMatch(html, new RegExp(`<span>${hidden.text}</span>`))
      assert.equal(html.split(call.task_title).length - 1, 1, 'title once')
      assert.doesNotMatch(html, /\[(CODE|CARD|NUMBER)\]/, 'mask tokens never render raw')
      assert.doesNotMatch(html, /[–—]/)
    })
  }

  test('round 3: the person-answered field is an accent wash, not a solid fill', () => {
    const css = R.pane.PANE_CSS
    const rule = css.match(/\.hlc-field\[data-tone='attention'\] \{([^}]*)\}/)[1]
    assert.match(rule, /background: color-mix\(in srgb, var\(--ui-accent\) 13%, transparent\)/)
    assert.doesNotMatch(css, /--dt-primary-solid/)
    assert.match(css, /\.hlc-field\[data-tone='attention'\] \.hlc-headline \{[^}]*font-size: 1\.875rem/, 'largest type in the pane')
  })

  test('round 3: the person-answered moment quotes them, keeps the hold and names what happens next', async () => {
    const html = await render(loadScene('human_detected'))
    assert.match(html, /class="hlc-headline">Someone picked up\.</)
    assert.match(html, /class="hlc-quote">“Thanks for holding, this is Sample Airline reservations/)
    assert.match(html, /After 38:32 on hold\. Hermes is greeting them\. Your phone rings next\./)
  })

  test('round 3: each strip label sits in its own segment column; the live phase reads "now"', async () => {
    const html = await render(loadScene('owner_connected'))
    const cols = [...html.matchAll(/<div class="hlc-col"[^>]*data-current="(true|false)"[^>]*><span class="hlc-bar"[^>]*><\/span><span class="hlc-label">([^<]*)<\/span><\/div>/g)]
    assert.deepEqual(cols.map((m) => [m[1], m[2]]), [['false', 'Menu 0:41'], ['false', 'Hold 38:32'], ['true', 'Person now']])
    const hold = await render(loadScene('on_hold'))
    assert.doesNotMatch(hold, /Hold 38:12/, 'the headline clock is not repeated')
  })

  test('round 3: other calls scroll with the transcript, nothing is pinned over it', async () => {
    const html = await render(loadScene('on_hold'))
    assert.match(html, /<div[^>]*class="hlp-lines"[^>]*>(?:(?!<\/section>).)*<nav class="hlp-others"/)
    assert.doesNotMatch(await render(loadScene('human_detected')), /<nav class="hlp-others"/, 'only when there are other calls')
  })

  test('round 3: small text uses the secondary token, never opacity or tertiary/quaternary', () => {
    assert.doesNotMatch(R.pane.PANE_CSS, /opacity: 0\.\d/)
    assert.doesNotMatch(R.pane.PANE_CSS, /--ui-text-(tertiary|quaternary)/)
  })

  test('round 3: only direction C ships', () => {
    assert.doesNotMatch(SOURCE, /\bhl[ab]-|QuietDetail|StoryDetail|VARIANTS|storage\.get\('variant'/)
  })

  test('C ended: the outcome sentence leads, the call length follows', async () => {
    const html = await render(loadScene('ended'))
    assert.match(html, /class="hlc-headline">Moved the flight to Friday, 9:40 a\.m\. departure\.</)
    assert.match(html, /Call ended after 40:51/)
  })

  test('failed shows the reason from the last system line and a next step', async () => {
    const html = await render(loadScene('failed'))
    assert.match(html, /No answer after 3 tries/)
    assert.match(html, /Ask Hermes in chat to try again/)
  })

  test('empty, loading and error states', async () => {
    assert.match(await render(loadScene('empty')), /No calls yet/)
    assert.match(await render(loadScene('empty'), 'error'), /Could not load calls/)
    const fake = R.createFakeCtx({ snapshot: loadScene('empty'), mode: 'loading' })
    const store = R.pane.createCallStore(fake.ctx)
    store.refresh()
    assert.match(R.renderToStaticMarkup(R.createElement(R.pane.CallsPane, { store })), /aria-busy="true"/)
  })
})

after(() => {})
