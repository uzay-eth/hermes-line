// Hermes Line: Desktop pane.
//
// Loaded uncompiled by Hermes Desktop (disk / unified-package door), so this file is plain ESM with
// no JSX and imports only `@hermes/plugin-sdk` and `react`. It renders the live call list, the
// selected call's state, the masked transcript, the Take over / Say this / Hang up controls and the
// call summary. It never owns telephony: every control is a POST to the plugin backend
// (`/api/plugins/hermes-line/calls/{id}/...`), and the pane only shows what the backend snapshot
// says happened. Closing the pane does not touch a call.
//
// Data flow (docs/CONTRACT.md, "Desktop REST and events"):
//   GET  /calls                    -> {revision, calls: [Call]}
//   POST /calls/{id}/take_over     body {idempotency_key, revision}
//   POST /calls/{id}/say           body {idempotency_key, revision, text}
//   POST /calls/{id}/hang_up       body {idempotency_key, revision}
//   event plugin.hermes-line.call.updated {id, revision} -> re-fetch the snapshot
// A bounded poll (ctx.setInterval) covers a dropped event stream.

import {
  Button,
  Codicon,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  PANES_AREA,
  Skeleton,
  Textarea,
  atom,
  host,
  isSubmitEnter,
  useValue
} from '@hermes/plugin-sdk'
import { createElement as h, Fragment, useEffect, useLayoutEffect, useRef, useState } from 'react'

export const PLUGIN_ID = 'hermes-line'
export const PANE_ID = 'calls'
export const CALL_UPDATED_EVENT = 'plugin.hermes-line.call.updated'
export const PANE_WIDTH = '380px'
// While a call is live the pane re-reads the snapshot every second: the call.updated event only
// reaches Desktop clients of the process that owns the line, and a state such as "a person
// answered" can last about a second before the owner's phone rings (trial call_2243b7f205194ab5).
export const POLL_LIVE_MS = 1000
export const POLL_IDLE_MS = 30000
export const SAY_MAX_CHARS = 280

// ---------------------------------------------------------------------------------------------
// Copy. Every user-facing string lives here so a later i18n pass has one place to look.
// ---------------------------------------------------------------------------------------------

export const COPY = {
  paneTitle: 'Calls',
  listLabel: 'Calls',
  transcript: 'Transcript',
  transcriptEmpty: 'Nothing said yet.',
  summary: 'Summary',
  after: 'after',
  speaker: { hermes: 'Hermes', business: 'Business', owner: 'You', system: 'Line' },
  direction: { outbound: 'Outbound call to', inbound: 'Callback from' },
  started: 'started',
  totalSuffix: 'total',
  hidden: 'hidden',
  takeOver: 'Take over',
  sayThis: 'Say this',
  hangUp: 'Hang up',
  cancelCall: 'Cancel call',
  send: 'Send',
  cancel: 'Cancel',
  sayLabel: 'What should Hermes say?',
  sayHint: 'Enter sends. Shift+Enter adds a line.',
  sayPlaceholder: 'For example: ask whether a window seat is free',
  hangUpTitle: 'Hang up this call?',
  hangUpBody: 'Hermes ends the call for everyone on it.',
  hangUpBodyHold: (held) => `You will lose your place in the queue after ${held} on hold.`,
  cancelTitle: 'Cancel this call?',
  cancelBody: 'Hermes will not place it.',
  hangingUp: 'Hanging up',
  hungUp: 'Hung up',
  emptyTitle: 'No calls yet',
  emptyBody: 'Ask Hermes in chat to call a business. The live call shows up here.',
  errorTitle: 'Could not load calls',
  errorBody: 'Hermes Line did not answer. Check that the plugin is enabled for this profile, then retry.',
  retry: 'Retry',
  stale: (at) => `Lost contact with Hermes Line. Showing the state from ${at}. Retrying.`,
  sent: {
    take_over: 'Asked Hermes to bring you in. Your phone rings next.',
    say: 'Hermes will say it at the next pause.',
    hang_up: 'Hanging up.'
  },
  conflict: 'The call changed before that reached Hermes. This is the latest state; try again if it still applies.',
  failedAction: 'That did not reach Hermes Line. Check the connection and try again.',
  notify: {
    humanTitle: 'A person answered',
    ringingTitle: 'Your phone is ringing',
    ringingBody: (task) => `Answer to join: ${task}`
  },
  failedFallback: 'The call ended before it could finish.',
  failedNext: 'Ask Hermes in chat to try again.',
  intoCall: 'into the call',
  otherCalls: 'Other calls',
  controls: 'Call controls',
  latest: 'Latest',
  latestFirst: 'Transcript, latest first',
  callTimeNote: 'Times are call time',
  startedAt: (at) => `Started ${at}`,
  jumpLatest: 'Jump to latest',
  endedAfter: 'Call ended after',
  timeStrip: 'Time in each part of the call',
  clockFor: {
    dialing: 'Dialing for',
    ivr: 'In the phone menu for',
    on_hold: 'On hold for',
    ringing_owner: 'Ringing for',
    owner_connected: 'You have been talking for',
    agent_talking: 'Hermes has been talking for'
  },
  shortPhase: { menu: 'Menu', hold: 'Hold', person: 'Person', outcome: 'Outcome' },
  now: 'now',
  signal: {
    human: 'Someone picked up.',
    ringing: 'Pick up your phone.',
    saidAt: (at) => `Said ${at} into the call`,
    afterHold: (held) => `After ${held} on hold.`,
    lastHeard: (at) => `Last heard ${at} into the call:`
  }
}

// ---------------------------------------------------------------------------------------------
// State model
// ---------------------------------------------------------------------------------------------

// tone: waiting (Hermes is working, nothing for you to do) | attention (a person, or you, is
// needed now) | active (someone is talking for you) | done | failed.
export const STATE_META = {
  awaiting_approval: { label: 'Waiting for your approval', short: 'Needs approval', tone: 'attention', live: true, clock: false },
  dialing: { label: 'Dialing', short: 'Dialing', tone: 'waiting', live: true, clock: true },
  ivr: { label: 'In the phone menu', short: 'Phone menu', tone: 'waiting', live: true, clock: true },
  on_hold: { label: 'On hold', short: 'On hold', tone: 'waiting', live: true, clock: true },
  human_detected: { label: 'A person answered', short: 'Person answered', tone: 'attention', live: true, clock: true },
  ringing_owner: { label: 'Ringing your phone', short: 'Ringing you', tone: 'attention', live: true, clock: true },
  owner_connected: { label: 'You are on the call', short: 'You are on', tone: 'active', live: true, clock: true },
  agent_talking: { label: 'Hermes is talking', short: 'Hermes talking', tone: 'active', live: true, clock: true },
  ended: { label: 'Call ended', short: 'Ended', tone: 'done', live: false, clock: true },
  failed: { label: 'Call did not go through', short: 'Failed', tone: 'failed', live: false, clock: false }
}

const UNKNOWN_META = { label: 'Updating', short: 'Updating', tone: 'waiting', live: true, clock: false }

export function stateMeta(state) {
  return STATE_META[state] || UNKNOWN_META
}

// Which control each state allows. Unknown states allow only Hang up.
export const ACTION_STATES = {
  take_over: new Set(['ivr', 'on_hold', 'human_detected', 'agent_talking']),
  say: new Set(['ivr', 'human_detected', 'ringing_owner', 'agent_talking']),
  hang_up: new Set([
    'awaiting_approval',
    'dialing',
    'ivr',
    'on_hold',
    'human_detected',
    'ringing_owner',
    'owner_connected',
    'agent_talking'
  ])
}

export function actionsFor(state) {
  const known = Object.prototype.hasOwnProperty.call(STATE_META, state)
  return {
    take_over: ACTION_STATES.take_over.has(state),
    say: ACTION_STATES.say.has(state),
    hang_up: known ? ACTION_STATES.hang_up.has(state) : true
  }
}

export function stateNote(call) {
  switch (call.state) {
    case 'awaiting_approval':
      return 'Hermes will not dial until you approve this call in chat.'
    case 'dialing':
      return 'Placing the call.'
    case 'ivr':
      return 'Hermes is working through the phone menu.'
    case 'on_hold':
      return 'Hermes is listening for a person and will notify you when someone answers.'
    case 'human_detected':
      return call.mode === 'owner'
        ? 'Hermes is greeting them. Your phone rings next.'
        : 'Hermes is handling the call. Take over to join it yourself.'
    case 'ringing_owner':
      return 'Answer your phone to join. Hermes is keeping them on the line.'
    case 'owner_connected':
      return 'Hermes is listening and posts a summary when the call ends.'
    case 'agent_talking':
      return 'Hermes is speaking for you. Take over, or tell it what to say.'
    default:
      return ''
  }
}

const PRIORITY = { attention: 0, active: 1, waiting: 2, done: 3, failed: 3 }

// ---------------------------------------------------------------------------------------------
// Pure helpers (exported for tests/l3)
// ---------------------------------------------------------------------------------------------

/** Epoch milliseconds from an ISO string or epoch seconds/milliseconds; null when unreadable. */
export function parseTs(ts) {
  if (typeof ts === 'number' && Number.isFinite(ts)) return ts < 1e12 ? ts * 1000 : ts
  if (typeof ts === 'string' && ts) {
    const ms = Date.parse(ts)
    return Number.isNaN(ms) ? null : ms
  }
  return null
}

/** 0:07, 38:12, 1:02:09. Negative or missing input reads as 0:00. */
export function formatClock(seconds) {
  const total = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0))
  const hrs = Math.floor(total / 3600)
  const mins = Math.floor((total % 3600) / 60)
  const secs = total % 60
  const ss = String(secs).padStart(2, '0')
  return hrs > 0 ? `${hrs}:${String(mins).padStart(2, '0')}:${ss}` : `${mins}:${ss}`
}

/** "38 minutes", "1 hour 4 minutes", "45 seconds" for confirmation copy. */
export function formatSpoken(seconds) {
  const total = Math.max(0, Math.floor(seconds || 0))
  if (total < 60) return `${total} second${total === 1 ? '' : 's'}`
  const hrs = Math.floor(total / 3600)
  const mins = Math.floor((total % 3600) / 60)
  const parts = []
  if (hrs) parts.push(`${hrs} hour${hrs === 1 ? '' : 's'}`)
  if (mins) parts.push(`${mins} minute${mins === 1 ? '' : 's'}`)
  return parts.join(' ')
}

const timeFormat = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' })

const timeFormatSeconds = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', second: '2-digit' })

export function formatTimeOfDaySeconds(ms) {
  return ms == null ? '' : timeFormatSeconds.format(new Date(ms))
}

export function formatTimeOfDay(ms) {
  return ms == null ? '' : timeFormat.format(new Date(ms))
}

/**
 * When the call entered its current state. The contract has no field for it yet, so this prefers a
 * forward-compatible `state_since`, then the newest `system` transcript line (the engine logs a
 * system line on each transition), then `started_at`.
 */
export function stateSince(call) {
  const explicit = parseTs(call.state_since)
  if (explicit != null) return explicit
  const lines = Array.isArray(call.transcript) ? call.transcript : []
  for (let i = lines.length - 1; i >= 0; i--) {
    if (lines[i] && lines[i].speaker === 'system') {
      const ms = parseTs(lines[i].ts)
      if (ms != null) return ms
    }
  }
  return parseTs(call.started_at)
}

/**
 * Seconds for the call as a whole and for its current state, at client time `now`.
 * The server's `duration_s` is the base; a live call advances from the moment the snapshot arrived.
 * The state clock is derived from server timestamps only (state start minus call start), so a
 * skewed desktop clock cannot make it jump.
 */
export function callClocks(call, receivedAt, now) {
  const live = stateMeta(call.state).live
  const base = Number.isFinite(call.duration_s) ? call.duration_s : 0
  const total = live ? base + Math.max(0, (now - receivedAt) / 1000) : base
  const started = parseTs(call.started_at)
  const since = stateSince(call)
  const offset = started != null && since != null ? Math.max(0, (since - started) / 1000) : 0
  const inState = Math.max(0, total - offset)
  return { total, inState }
}

export function sortCalls(calls) {
  return [...calls].sort((a, b) => {
    const pa = PRIORITY[stateMeta(a.state).tone]
    const pb = PRIORITY[stateMeta(b.state).tone]
    if (pa !== pb) return pa - pb
    return (parseTs(b.started_at) || 0) - (parseTs(a.started_at) || 0)
  })
}

/** The call the pane shows when the user has not picked one. */
export function pickFocusCall(calls) {
  return calls.length ? sortCalls(calls)[0] : null
}

const MASK_TOKEN = /\[[A-Z][A-Z_]{1,15}\]/g

/** Split already-masked text into plain runs and mask tokens such as `[CODE]`. */
export function splitMasked(text) {
  const out = []
  const source = typeof text === 'string' ? text : ''
  let last = 0
  for (const match of source.matchAll(MASK_TOKEN)) {
    if (match.index > last) out.push({ kind: 'text', value: source.slice(last, match.index) })
    out.push({ kind: 'mask', value: match[0].slice(1, -1).toLowerCase() })
    last = match.index + match[0].length
  }
  if (last < source.length) out.push({ kind: 'text', value: source.slice(last) })
  return out
}

export const GROUP_GAP_MS = 60000

/** Consecutive lines from the same speaker share one header, unless more than a minute apart. */
export function groupTranscript(lines) {
  const groups = []
  for (const line of Array.isArray(lines) ? lines : []) {
    if (!line || typeof line !== 'object') continue
    const prev = groups[groups.length - 1]
    const prevLast = prev && prev.lines[prev.lines.length - 1]
    const a = prevLast ? parseTs(prevLast.ts) : null
    const b = parseTs(line.ts)
    const close = a == null || b == null || b - a <= GROUP_GAP_MS
    if (line.speaker !== 'system' && prev && prev.speaker === line.speaker && close) prev.lines.push(line)
    else groups.push({ speaker: line.speaker, lines: [line] })
  }
  return groups
}

export function failureReason(call) {
  if (call.summary) return call.summary
  const lines = Array.isArray(call.transcript) ? call.transcript : []
  for (let i = lines.length - 1; i >= 0; i--) {
    if (lines[i] && lines[i].speaker === 'system' && lines[i].text) return lines[i].text
  }
  return COPY.failedFallback
}

/** A 409 from the backend (stale revision). The desktop bridge keeps "409: ..." on the message. */
export function isConflict(error) {
  if (!error) return false
  if (error.statusCode === 409 || error.status === 409) return true
  return /^409\b/.test(String(error.message || ''))
}

export function newIdempotencyKey() {
  const c = globalThis.crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  const bytes = new Uint8Array(16)
  if (c && typeof c.getRandomValues === 'function') c.getRandomValues(bytes)
  else for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

/** Drop anything that is not a well-formed snapshot instead of rendering half a call. */
export function normalizeSnapshot(raw) {
  if (!raw || typeof raw !== 'object' || !Array.isArray(raw.calls)) return null
  const calls = raw.calls.filter((c) => c && typeof c === 'object' && typeof c.id === 'string' && c.id)
  return { revision: Number.isFinite(raw.revision) ? raw.revision : 0, calls }
}

// ---------------------------------------------------------------------------------------------
// Store: snapshot, fetch coalescing, polling, transition notifications, actions
// ---------------------------------------------------------------------------------------------

export function createCallStore(ctx) {
  const $snapshot = atom(null) // {revision, calls, receivedAt}
  const $status = atom('loading') // loading | ready | error
  const $stale = atom(false) // last refresh failed after a good snapshot
  const $now = atom(Date.now())

  const lastState = new Map()
  const notified = new Set()
  let inflight = null
  let dirty = false
  let disposed = false
  let lastFetch = 0

  const hasLive = () => {
    const snap = $snapshot.get()
    return Boolean(snap && snap.calls.some((c) => stateMeta(c.state).live))
  }

  function notifyTransition(call, prev) {
    if (!prev || prev === call.state || notified.has(call.id)) return
    if (call.state !== 'human_detected' && call.state !== 'ringing_owner') return
    notified.add(call.id)
    const title = call.state === 'ringing_owner' ? COPY.notify.ringingTitle : COPY.notify.humanTitle
    const body = call.state === 'ringing_owner' ? COPY.notify.ringingBody(call.task_title || '') : call.task_title || ''
    try {
      if (ctx.os && typeof ctx.os.notify === 'function') ctx.os.notify({ title, body })
    } catch {
      // A missing OS bridge must never break the pane.
    }
  }

  function accept(raw) {
    const snap = normalizeSnapshot(raw)
    if (!snap) throw new Error('Malformed calls snapshot')
    for (const call of snap.calls) {
      notifyTransition(call, lastState.get(call.id))
      lastState.set(call.id, call.state)
    }
    $snapshot.set({ ...snap, receivedAt: Date.now() })
    $now.set(Date.now())
    $status.set('ready')
    $stale.set(false)
  }

  function refresh() {
    if (disposed) return Promise.resolve()
    if (inflight) {
      dirty = true
      return inflight
    }
    inflight = (async () => {
      do {
        dirty = false
        lastFetch = Date.now()
        try {
          const raw = await ctx.rest('/calls')
          if (disposed) return
          accept(raw)
        } catch {
          if (disposed) return
          if ($snapshot.get()) $stale.set(true)
          else $status.set('error')
        }
      } while (dirty && !disposed)
    })().finally(() => {
      inflight = null
    })
    return inflight
  }

  function retry() {
    if (!$snapshot.get()) $status.set('loading')
    return refresh()
  }

  async function act(call, action, extra) {
    const snap = $snapshot.get()
    const body = { idempotency_key: newIdempotencyKey(), revision: snap ? snap.revision : 0, ...(extra || {}) }
    try {
      await ctx.rest(`/calls/${encodeURIComponent(call.id)}/${action}`, { method: 'POST', body })
      refresh()
      return { ok: true }
    } catch (error) {
      if (isConflict(error)) {
        await refresh()
        return { ok: false, conflict: true }
      }
      return { ok: false, conflict: false }
    }
  }

  function tick() {
    const now = Date.now()
    const live = hasLive()
    // A stale snapshot must not keep a hold clock running as if nothing changed.
    if (live && !$stale.get()) $now.set(now)
    const every = live ? POLL_LIVE_MS : POLL_IDLE_MS
    if (now - lastFetch >= every) refresh()
  }

  function start() {
    refresh()
    ctx.setInterval(tick, 1000)
    ctx.onEvent(CALL_UPDATED_EVENT, (event) => {
      const payload = event && event.payload
      const snap = $snapshot.get()
      if (!snap || !payload || typeof payload.revision !== 'number' || payload.revision !== snap.revision) refresh()
    })
    // Calls belong to a profile: drop the old profile's calls and fetch again on a switch.
    const profile = host && host.state && host.state.profile
    if (profile && typeof profile.listen === 'function') {
      const off = profile.listen(() => {
        lastState.clear()
        notified.clear()
        $snapshot.set(null)
        $status.set('loading')
        refresh()
      })
      ctx.onDispose(off)
    }
    ctx.onDispose(() => {
      disposed = true
    })
  }

  const later = (fn, ms) => ctx.setTimeout(fn, ms)

  return { $snapshot, $status, $stale, $now, refresh, retry, act, start, later }
}

// ---------------------------------------------------------------------------------------------
// Phases: the call's story (menu, hold, person, outcome), derived from the engine's system lines
// plus `state_since`. Shared by every direction (hold time after a person answers, the strip, the
// story rail).
// ---------------------------------------------------------------------------------------------

export const PHASE_ORDER = ['menu', 'hold', 'person', 'outcome']

export function phaseOfState(state) {
  if (state === 'awaiting_approval' || state === 'dialing' || state === 'ivr') return 'menu'
  if (state === 'on_hold') return 'hold'
  if (state === 'human_detected' || state === 'ringing_owner' || state === 'owner_connected' || state === 'agent_talking')
    return 'person'
  if (state === 'ended' || state === 'failed') return 'outcome'
  return null
}

// System lines that open a phase. Contract assumption (docs/lanes/L3.md): the engine writes one
// system line per transition; when a boundary line is missing, `state_since` opens the current phase.
function boundaryOf(text) {
  const t = String(text || '')
  if (/\bon hold\b/i.test(t)) return 'hold'
  if (/\b(person answered|answered a callback|picked up)\b/i.test(t)) return 'person'
  if (/\b(call ended|stopped retrying|hung up)\b/i.test(t)) return 'outcome'
  return null
}

const rank = (key) => PHASE_ORDER.indexOf(key)

/**
 * [{key, start, end, lines, opener}] for the phases that happened, in order. `end` is null for the
 * live phase. Boundary system lines become the phase's `opener` instead of a transcript line, so
 * the phase name is never printed twice.
 */
export function callPhases(call) {
  return explicitPhases(call) || derivedPhases(call)
}

// Phase names, mapped to the pane's four phases. L1 round 37 (docs/lanes/L1.md section 49) writes
// dialing, menu, assistant, hold, person, owner_ringing, bridged, fallback, ended; the other names
// are tolerated aliases.
export const PHASE_ALIASES = {
  dialing: 'menu', menu: 'menu', assistant: 'menu', ivr: 'menu', dial: 'menu',
  hold: 'hold', on_hold: 'hold',
  person: 'person', owner_ringing: 'person', bridged: 'person', fallback: 'person',
  talk: 'person', human: 'person', human_detected: 'person', owner: 'person', agent: 'person',
  ended: 'outcome', outcome: 'outcome', end: 'outcome', failed: 'outcome'
}

/**
 * The engine's explicit phase list (L1: one entry per phase with when it began, e.g.
 * `phases: [{phase: "menu"|"hold"|"person"|"outcome", since: ISO 8601}]`), normalised to the same
 * shape as derivedPhases. Null when the call carries no usable list, so the pane falls back to
 * deriving phases from the transcript. Entries without a known phase or a readable time are
 * skipped; consecutive repeats merge; an explicit end (`until`/`end`/`ended_at`) is honoured.
 */
export function explicitPhases(call) {
  if (!Array.isArray(call.phases) || call.phases.length === 0) return null
  const callStart = parseTs(call.started_at)
  const items = []
  for (const p of call.phases) {
    if (!p || typeof p !== 'object') continue
    const key = PHASE_ALIASES[String(p.phase ?? p.name ?? p.kind ?? '').toLowerCase()]
    const since = parseTs(p.since ?? p.start ?? p.started_at ?? p.at)
    if (!key || since == null) continue
    // The strip is call time, like the header and the transcript: a phase opened before the call
    // started (dialing) counts from started_at.
    const start = callStart != null ? Math.max(since, callStart) : since
    items.push({ key, start, end: parseTs(p.until ?? p.end ?? p.ended_at), lines: [], opener: null })
  }
  if (!items.length) return null
  items.sort((a, b) => a.start - b.start)
  const phases = []
  for (const item of items) {
    const prev = phases[phases.length - 1]
    if (prev && prev.key === item.key) {
      if (item.end != null) prev.end = item.end
      continue
    }
    if (prev && prev.end == null) prev.end = item.start
    phases.push(item)
  }
  // The state can run ahead of the list (or the list may stop before the end): close with state_since.
  const want = phaseOfState(call.state)
  let last = phases[phases.length - 1]
  if (want && rank(want) > rank(last.key)) {
    const at = parseTs(call.state_since) ?? last.start
    if (last.end == null || last.end > at) last.end = at
    last = { key: want, start: at, end: null, lines: [], opener: null }
    phases.push(last)
  }
  if (last.key === 'outcome') last.end = last.start
  else if (stateMeta(call.state).live) last.end = null
  const lines = Array.isArray(call.transcript) ? call.transcript : []
  for (const line of lines) {
    const at = line && parseTs(line.ts)
    if (at == null) continue
    let home = phases[0]
    for (const ph of phases) if (ph.start <= at) home = ph
    home.lines.push(line)
  }
  return phases
}

function derivedPhases(call) {
  const started = parseTs(call.started_at)
  const lines = Array.isArray(call.transcript) ? call.transcript : []
  let current = { key: 'menu', start: started, end: null, lines: [], opener: null }
  const phases = [current]
  for (const line of lines) {
    if (!line || typeof line !== 'object') continue
    const next = line.speaker === 'system' ? boundaryOf(line.text) : null
    if (next && rank(next) > rank(current.key)) {
      const at = parseTs(line.ts)
      current.end = at
      current = { key: next, start: at, end: null, lines: [], opener: line }
      phases.push(current)
      continue
    }
    current.lines.push(line)
  }
  const want = phaseOfState(call.state)
  if (want && rank(want) > rank(current.key)) {
    const at = parseTs(call.state_since) ?? parseTs(call.started_at)
    current.end = at
    current = { key: want, start: at, end: null, lines: [], opener: null }
    phases.push(current)
  }
  if (current.key === 'outcome') current.end = current.start
  return phases
}

/** Seconds spent in a phase; the live phase is anchored to server time like the other clocks. */
export function phaseSeconds(phase, call, clocks) {
  const started = parseTs(call.started_at)
  if (phase.end != null && phase.start != null) return Math.max(0, (phase.end - phase.start) / 1000)
  if (phase.start == null || started == null) return 0
  return Math.max(0, clocks.total - (phase.start - started) / 1000)
}

export function holdSeconds(call) {
  const hold = callPhases(call).find((p) => p.key === 'hold' && p.end != null)
  return hold ? (hold.end - hold.start) / 1000 : null
}

/**
 * The one action that deserves the primary button. Take over leads only when nothing else will
 * bring the owner in; when the owner's phone is about to ring (owner plan), nothing is primary.
 */
export function primaryAction(call) {
  if (call.state === 'agent_talking') return 'take_over'
  if (call.state === 'human_detected' && call.mode !== 'owner') return 'take_over'
  return null
}

/** The system line that announced the current state; the stage already says it, so it is hidden. */
export function currentStateLineTs(call) {
  return parseTs(call.state_since)
}

export function newestUtterance(call) {
  const lines = Array.isArray(call.transcript) ? call.transcript : []
  for (let i = lines.length - 1; i >= 0; i--) if (lines[i] && lines[i].speaker !== 'system') return lines[i]
  return null
}

/** First sentence of the summary, for direction C's outcome headline. */
export function splitSummary(summary) {
  const text = String(summary || '').trim()
  // A sentence ends at . ! ? followed by a capital, so "9:40 a.m. departure" stays whole.
  const m = text.match(/^(.+?[.!?])\s+(?=[A-Z])/)
  if (!m) return { lead: text, rest: '' }
  return { lead: m[1], rest: text.slice(m[0].length).trim() }
}

/**
 * The line the signal field quotes, so the transcript can leave it out: the person's own words once
 * someone is on the line, the last thing heard while on hold. Null in other states.
 */
export function quotedLine(call) {
  const lines = Array.isArray(call.transcript) ? call.transcript : []
  if (call.state === 'human_detected' || call.state === 'ringing_owner') {
    for (let i = lines.length - 1; i >= 0; i--) if (lines[i] && lines[i].speaker === 'business') return lines[i]
    return null
  }
  if (call.state === 'on_hold') return newestUtterance(call)
  return null
}

/**
 * What the ended card shows under the outcome sentence (round 9, option A): the rest of the summary
 * without L1's log line ("Hermes Line: … (…, 0m57s).") and without "On the call: …", which the
 * transcript below already holds. For L1's round-37 summary that leaves the engine's fallback note.
 */
export function cardRest(summary) {
  const { rest } = splitSummary(summary)
  return rest
    .replace(/Hermes Line: .*?, \d+m\d{2}s\)\.\s*/s, '')
    .replace(/\s*On the call:.*$/s, '')
    .trim()
}

// ---------------------------------------------------------------------------------------------
// Styles: scoped under .hlp, theme tokens only (no literal colours), rendered inside the pane.
// ---------------------------------------------------------------------------------------------

export const PANE_CSS = `
.hlp {
  --hlp-ease: cubic-bezier(0.23, 1, 0.32, 1);
  --hlp-accent: var(--ui-accent);
  --hlp-hair: var(--ui-stroke-tertiary);
  --hlp-danger: color-mix(in srgb, var(--ui-red) 82%, var(--ui-text-primary));
  --hlp-newest: color-mix(in srgb, var(--ui-accent) 6%, transparent);
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  color: var(--ui-text-primary);
  font-size: 0.75rem;
  line-height: 1rem;
}
.hlp :where(button) { font: inherit; }
.hlp :where(p) { margin: 0; }
.hlp-tnum { font-variant-numeric: tabular-nums; }
.hlp-sr { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }

.hlp-stale { margin: 0.5rem 0.75rem 0; padding: 0.5rem 0.625rem; border-radius: 0.5rem; background: var(--ui-bg-quaternary); color: var(--ui-text-secondary); }

/* Signal field: its colour is the call state. Quiet while Hermes waits; an accent wash, a top rule
   and the largest type in the pane when a person is on the line. */
.hlc-field {
  flex: none;
  padding: 1rem 1rem 0.875rem;
  background: color-mix(in srgb, var(--ui-text-primary) 3.5%, transparent);
  border-bottom: 1px solid var(--hlp-hair);
  box-shadow: inset 0 0 0 transparent;
  /* Secondary ink and strip track measured against the card colour itself, not the pane: AA for
     text (4.5:1) and 3:1 for the segments, light and dark (tools/pane-preview: preview.mjs contrast). */
  --hlc-ink-2: color-mix(in srgb, var(--ui-base) 80%, transparent);
  --hlc-track: color-mix(in srgb, var(--ui-base) 56%, transparent);
  transition: background-color 320ms var(--hlp-ease), box-shadow 320ms var(--hlp-ease);
}
.hlc-field[data-tone='attention'] {
  background: color-mix(in srgb, var(--ui-accent) 13%, transparent);
  box-shadow: inset 0 0.1875rem 0 var(--ui-accent);
}
.hlc-field[data-tone='active'] { background: color-mix(in srgb, var(--ui-accent) 6%, transparent); }
.hlc-field[data-tone='failed'] { background: color-mix(in srgb, var(--ui-red) 8%, transparent); }
.hlc-body[data-enter='true'] { animation: hlp-rise 240ms var(--hlp-ease) both; }
.hlc-task { display: flex; align-items: baseline; gap: 0.5rem; font-weight: 600; }
.hlc-task-title { flex: 1; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.hlc-task-meta { flex: none; font-weight: 400; color: var(--hlc-ink-2); font-variant-numeric: tabular-nums; }
.hlc-headline { margin-top: 0.875rem; font-size: 1.5rem; line-height: 1.875rem; font-weight: 600; letter-spacing: -0.015em; text-wrap: balance; }
.hlc-field[data-tone='attention'] .hlc-headline { font-size: 1.875rem; line-height: 2.25rem; letter-spacing: -0.02em; color: var(--hlp-accent); }
.hlc-field[data-tone='failed'] .hlc-headline { color: var(--hlp-danger); }
.hlc-clock { display: block; font-size: 3rem; line-height: 3.25rem; font-weight: 500; letter-spacing: -0.03em; font-variant-numeric: tabular-nums; }
.hlc-quote { margin-top: 0.75rem; font-size: 0.9375rem; line-height: 1.375rem; font-weight: 500; text-wrap: pretty; }
.hlc-quote-by { display: block; margin-top: 0.25rem; font-size: 0.75rem; font-weight: 400; color: var(--hlc-ink-2); }
.hlc-sub { margin-top: 0.5rem; color: var(--hlc-ink-2); line-height: 1.0625rem; text-wrap: pretty; }
.hlc-heard { margin-top: 0.5rem; line-height: 1.0625rem; text-wrap: pretty; }
.hlc-heard-at { color: var(--hlc-ink-2); }
.hlc-rest { margin-top: 0.5rem; font-size: 0.875rem; line-height: 1.375rem; }

/* Phase strip: each phase is a column, its label under its own segment. Columns grow with the
   phase's time; a short phase keeps room for its label, so widths are proportional above that floor. */
.hlc-strip { display: flex; gap: 0.375rem; margin-top: 1rem; }
.hlc-col { display: flex; flex-direction: column; gap: 0.3125rem; min-width: max-content; flex-shrink: 1; flex-basis: 0; }
.hlc-bar { height: 0.375rem; border-radius: 999px; background: var(--hlc-track); }
.hlc-col[data-current='true'] .hlc-bar { background: var(--hlp-accent); }
.hlc-label { font-size: 0.6875rem; line-height: 1rem; color: var(--hlc-ink-2); font-variant-numeric: tabular-nums; white-space: nowrap; }
.hlc-col[data-current='true'] .hlc-label { color: var(--ui-text-primary); font-weight: 600; }

.hlc-controls { flex: none; padding: 0.75rem 1rem; border-bottom: 1px solid var(--hlp-hair); }

/* Controls */
.hlp-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 0.375rem; }
.hlp-actions-end { margin-left: auto; }
.hlp-compose { padding-top: 0.75rem; }
.hlp-compose[data-enter='true'] { animation: hlp-rise 180ms var(--hlp-ease) both; }
.hlp-compose-label { display: block; margin-bottom: 0.375rem; font-weight: 500; color: var(--ui-text-secondary); }
.hlp-compose-row { display: flex; align-items: center; gap: 0.375rem; margin-top: 0.375rem; }
.hlp-compose-hint { flex: 1; color: var(--ui-text-secondary); }
.hlp-feedback { margin-top: 0.625rem; color: var(--ui-text-secondary); }
.hlp-feedback[data-kind='warn'],
.hlp-feedback[data-kind='error'] { padding: 0.5rem 0.625rem; border-radius: 0.5rem; color: var(--ui-text-primary); }
.hlp-feedback[data-kind='warn'] { background: color-mix(in srgb, var(--ui-yellow) 14%, transparent); }
.hlp-feedback[data-kind='error'] { background: color-mix(in srgb, var(--ui-red) 10%, transparent); }

/* Transcript: newest first; other calls scroll with it, so nothing is pinned over a line. */
.hlp-transcript { position: relative; display: flex; flex-direction: column; flex: 1; min-height: 7.5rem; }
.hlp-transcript-head { flex: none; display: flex; align-items: baseline; gap: 0.5rem; padding: 0.625rem 1rem 0.125rem; font-weight: 500; color: var(--ui-text-secondary); }
.hlp-transcript-note { margin-left: auto; font-weight: 400; }
.hlp-lines {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0.375rem 1rem 4.25rem;
  overscroll-behavior-y: contain;
  /* The last row never ends in a hard cut: rows fade out over 3 rem and the bottom 12 px are empty,
     so no glyph touches the edge; the end padding lets the final row scroll fully clear of the fade. */
  mask-image: linear-gradient(to bottom, transparent 0, black 0.75rem, black calc(100% - 3.75rem), transparent calc(100% - 0.75rem), transparent);
}
.hlp-group { margin-top: 0.5rem; padding: 0.375rem 0.5rem; margin-left: -0.5rem; margin-right: -0.5rem; border-radius: 0.5rem; }
.hlp-group[data-newest='true'] { background: var(--hlp-newest); }
.hlp-who { display: flex; align-items: baseline; gap: 0.5rem; font-size: 0.6875rem; line-height: 1rem; }
.hlp-who-name { font-weight: 600; color: var(--ui-text-primary); }
.hlp-group[data-speaker='owner'] .hlp-who-name,
.hlp-group[data-speaker='hermes'] .hlp-who-name { color: var(--hlp-accent); }
.hlp-who-time { color: var(--ui-text-secondary); font-variant-numeric: tabular-nums; }
.hlp-who-latest { color: var(--hlp-accent); font-weight: 500; }
.hlp-line { margin-top: 0.125rem; font-size: 0.8125rem; line-height: 1.25rem; color: var(--ui-text-primary); overflow-wrap: anywhere; }
.hlp-line[data-fresh='true'], .hlp-event[data-fresh='true'] { animation: hlp-rise 200ms var(--hlp-ease) both; }
.hlp-event { display: flex; align-items: baseline; gap: 0.5rem; margin-top: 0.625rem; font-size: 0.75rem; line-height: 1rem; color: var(--ui-text-secondary); }
.hlp-event-time { font-size: 0.6875rem; font-variant-numeric: tabular-nums; }
.hlp-mask { display: inline-block; padding: 0 0.3125rem; border-radius: 0.25rem; background: var(--ui-bg-quaternary); color: var(--ui-text-secondary); font-size: 0.6875rem; line-height: 1.125rem; font-weight: 500; }
.hlp-empty-line { padding-top: 0.625rem; color: var(--ui-text-secondary); }
.hlp-jump { position: absolute; left: 50%; top: 2.25rem; transform: translateX(-50%); }

/* Other calls, at the end of the scroll: never repeats the open call. */
.hlp-others { margin-top: 1.25rem; padding-top: 0.5rem; border-top: 1px solid var(--hlp-hair); }
.hlp-others-title { padding: 0.25rem 0 0.25rem; font-weight: 500; color: var(--ui-text-secondary); }
.hlp-row {
  all: unset;
  box-sizing: border-box;
  display: grid;
  grid-template-columns: 1rem minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.5rem;
  width: calc(100% + 1rem);
  margin: 0 -0.5rem;
  min-height: 1.875rem;
  padding: 0.375rem 0.5rem;
  border-radius: 0.25rem;
  color: var(--ui-text-secondary);
  cursor: pointer;
  transition: background-color 100ms ease;
}
@media (hover: hover) and (pointer: fine) {
  .hlp-row:hover { background: var(--ui-row-hover-background); transition: none; }
}
.hlp-row:focus-visible { outline: 2px solid color-mix(in srgb, var(--ui-accent) 55%, transparent); outline-offset: -2px; }
.hlp-row-title { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; color: var(--ui-text-primary); }
.hlp-row-state { white-space: nowrap; color: var(--ui-text-secondary); }
.hlp-row[data-tone='attention'] .hlp-row-state { color: var(--hlp-accent); font-weight: 500; }
.hlp-row[data-tone='failed'] .hlp-row-state { color: var(--hlp-danger); }

.hlp-loading { display: grid; gap: 0.625rem; padding: 1rem; }
.hlp-center { display: grid; flex: 1; place-items: center; padding: 1.5rem 1rem; }
.hlp-retry { display: flex; justify-content: center; }
.hlp-empty-body { margin-top: 0.25rem; max-width: 30ch; text-align: center; color: var(--ui-text-secondary); text-wrap: pretty; }

@keyframes hlp-rise { from { opacity: 0; transform: translateY(0.1875rem); } to { opacity: 1; transform: none; } }
@keyframes hlp-fade { from { opacity: 0; } to { opacity: 1; } }

@media (prefers-reduced-motion: reduce) {
  .hlc-body[data-enter='true'],
  .hlp-compose[data-enter='true'],
  .hlp-line[data-fresh='true'],
  .hlp-event[data-fresh='true'] { animation: hlp-fade 160ms ease both; }
}
`

// ---------------------------------------------------------------------------------------------
// Shared components
// ---------------------------------------------------------------------------------------------

function MaskedText({ text }) {
  return h(
    Fragment,
    null,
    splitMasked(text).map((part, i) =>
      part.kind === 'mask'
        ? h('span', { key: i, className: 'hlp-mask' }, `${part.value} ${COPY.hidden}`)
        : h(Fragment, { key: i }, part.value)
    )
  )
}

function useClocks(store, call) {
  const now = useValue(store.$now)
  const snap = useValue(store.$snapshot)
  return callClocks(call, snap ? snap.receivedAt : now, now)
}

function StateClock({ store, call, className }) {
  const { inState } = useClocks(store, call)
  return h('span', { className, role: 'timer', 'aria-live': 'off' }, formatClock(inState))
}

function RowClock({ store, call }) {
  const { inState } = useClocks(store, call)
  return h('span', { className: 'hlp-tnum' }, formatClock(inState))
}

/** A persistent live region: announces each new state once, outside any keyed subtree. */
function StateAnnouncer({ call }) {
  return h('span', { className: 'hlp-sr', role: 'status', 'aria-live': 'polite' }, stateMeta(call.state).label)
}

function OtherCalls({ store, calls, focusId, onSelect }) {
  const others = sortCalls(calls).filter((c) => c.id !== focusId)
  if (!others.length) return null
  return h(
    'nav',
    { className: 'hlp-others', 'aria-label': COPY.otherCalls },
    h('div', { className: 'hlp-others-title' }, COPY.otherCalls),
    others.map((call) => {
      const meta = stateMeta(call.state)
      return h(
        'button',
        { key: call.id, type: 'button', className: 'hlp-row', 'data-tone': meta.tone, onClick: () => onSelect(call.id) },
        h(Codicon, { name: call.direction === 'inbound' ? 'call-incoming' : 'call-outgoing', size: '0.875rem' }),
        h('span', { className: 'hlp-row-title' }, call.task_title || call.id),
        h(
          'span',
          { className: 'hlp-row-state' },
          meta.short,
          meta.live && meta.clock ? ' ' : null,
          meta.live && meta.clock ? h(RowClock, { store, call }) : null,
          !meta.live && meta.clock ? ` ${formatClock(call.duration_s)}` : null
        )
      )
    })
  )
}

/**
 * Transcript and quote times are call time (since `started_at`), the same clock as the strip and
 * the headline, so every number on the pane can be checked against the others. The wall-clock
 * time stays available as the tooltip.
 */
export function callTime(call, ts) {
  const at = parseTs(ts)
  const start = parseTs(call.started_at)
  if (at == null) return ''
  return start == null ? formatTimeOfDay(at) : formatClock((at - start) / 1000)
}

function CallTime({ call, ts }) {
  const at = parseTs(ts)
  return h('time', { dateTime: at == null ? undefined : new Date(at).toISOString(), title: formatTimeOfDaySeconds(at) }, callTime(call, ts))
}

function useFresh(lines) {
  const seen = useRef(null)
  if (seen.current === null) seen.current = new Set(lines.map((l) => l && l.seq))
  useEffect(() => {
    for (const l of lines) if (l) seen.current.add(l.seq)
  })
  return (line) => (seen.current.has(line.seq) ? 'false' : 'true')
}

/**
 * Transcript lines, newest first, grouped by speaker. Leaves out the line that announced the
 * current state (the field says it) and the line the field quotes (no duplicate).
 */
function Lines({ call, lines, fresh }) {
  const hideTs = currentStateLineTs(call)
  const quoted = quotedLine(call)
  const hidden = (l) =>
    (l.speaker === 'system' && hideTs != null && parseTs(l.ts) === hideTs) || Boolean(quoted && l.seq === quoted.seq)
  const visible = lines.filter((l) => l && !hidden(l))
  // "Latest" only marks the newest thing said on the call; when the field quotes it, nothing here is latest.
  const overall = newestUtterance(call)
  const newest = overall && visible.some((l) => l.seq === overall.seq) ? overall : null
  // Group on the full transcript first, then drop the hidden lines: removing a line (the one the
  // field quotes, or the state announcement) must never make the lines around it merge into one
  // bubble. Groups run latest first; the lines inside a bubble stay in the order they were said.
  const groups = transcriptGroups(lines, hidden)
  if (groups.length === 0) return h('div', { className: 'hlp-empty-line' }, COPY.transcriptEmpty)
  return h(
    Fragment,
    null,
    groups.map((group) => {
      const first = group.lines[0]
      const time = h(CallTime, { call, ts: first.ts })
      if (group.speaker === 'system') {
        return h(
          'div',
          { key: `s${first.seq}`, className: 'hlp-event', 'data-fresh': fresh(first) },
          h('span', null, h(MaskedText, { text: first.text })),
          h('span', { className: 'hlp-event-time' }, time)
        )
      }
      const isNewest = Boolean(newest && group.lines.some((l) => l.seq === newest.seq))
      return h(
        'div',
        { key: `g${first.seq}`, className: 'hlp-group', 'data-speaker': group.speaker, 'data-newest': isNewest ? 'true' : 'false' },
        h(
          'div',
          { className: 'hlp-who' },
          h('span', { className: 'hlp-who-name' }, COPY.speaker[group.speaker] || group.speaker),
          h('span', { className: 'hlp-who-time' }, time),
          isNewest ? h('span', { className: 'hlp-who-latest' }, COPY.latest) : null
        ),
        group.lines.map((line) =>
          h('p', { key: line.seq, className: 'hlp-line', 'data-fresh': fresh(line) }, h(MaskedText, { text: line.text }))
        )
      )
    })
  )
}

/**
 * Display groups for the latest-first transcript: speaker groups formed on the full transcript,
 * hidden lines removed afterwards (empty groups dropped), groups newest first, lines inside each
 * group oldest first.
 */
export function transcriptGroups(lines, hidden = () => false) {
  return groupTranscript(lines)
    .map((g) => ({ ...g, lines: g.lines.filter((l) => !hidden(l)) }))
    .filter((g) => g.lines.length)
    .reverse()
}

/** Newest-first transcript: stays at the top while you are there, offers a jump when you are not. */
function Transcript({ call, footer }) {
  const lines = Array.isArray(call.transcript) ? call.transcript : []
  const scroller = useRef(null)
  const atNewest = useRef(true)
  const [away, setAway] = useState(false)
  const fresh = useFresh(lines)
  const toNewest = () => {
    const el = scroller.current
    if (!el) return
    el.scrollTop = 0
    atNewest.current = true
    setAway(false)
  }
  useLayoutEffect(() => {
    if (atNewest.current) toNewest()
  }, [lines.length])
  return h(
    'section',
    { className: 'hlp-transcript', 'aria-label': COPY.transcript },
    h('div', { className: 'hlp-transcript-head' }, COPY.latestFirst, h('span', { className: 'hlp-transcript-note' }, COPY.callTimeNote)),
    h(
      'div',
      {
        ref: scroller,
        className: 'hlp-lines',
        role: 'log',
        'aria-live': 'off',
        tabIndex: 0,
        onScroll: (e) => {
          const gap = e.currentTarget.scrollTop
          atNewest.current = gap < 48
          setAway(gap >= 48)
        }
      },
      h(Lines, { call, lines, fresh }),
      footer
    ),
    away ? h('div', { className: 'hlp-jump' }, h(Button, { variant: 'floating', size: 'xs', onClick: toNewest }, COPY.jumpLatest)) : null
  )
}

function useCallActions(store, call) {
  const [pending, setPending] = useState(null)
  const [feedback, setFeedback] = useState(null) // {kind, text, state}
  const [sayOpen, setSayOpen] = useState(false)
  const allowed = actionsFor(call.state)

  useEffect(() => {
    if (feedback && feedback.kind === 'info' && feedback.state !== call.state) setFeedback(null)
  }, [call.state, feedback])
  useEffect(() => {
    if (!feedback || feedback.kind !== 'info') return undefined
    return store.later(() => setFeedback((f) => (f === feedback ? null : f)), 8000)
  }, [feedback, store])
  useEffect(() => {
    if (!allowed.say) setSayOpen(false)
  }, [allowed.say])

  async function run(action, extra) {
    setPending(action)
    setFeedback(null)
    const atState = call.state
    const result = await store.act(call, action, extra)
    setPending(null)
    if (result.ok) setFeedback({ kind: 'info', text: COPY.sent[action], state: atState })
    else if (result.conflict) setFeedback({ kind: 'warn', text: COPY.conflict, state: atState })
    else setFeedback({ kind: 'error', text: COPY.failedAction, state: atState })
    return result
  }

  return { pending, feedback, sayOpen, setSayOpen, allowed, run }
}

function Controls({ store, call, ctl }) {
  const { allowed, pending, sayOpen, setSayOpen, run } = ctl
  const [confirming, setConfirming] = useState(null)
  const cancelOnly = call.state === 'awaiting_approval'
  const primary = primaryAction(call)
  const openConfirm = () => {
    const snap = store.$snapshot.get()
    const now = Date.now()
    setConfirming(callClocks(call, snap ? snap.receivedAt : now, now).inState)
  }
  if (!(allowed.take_over || allowed.say || allowed.hang_up)) return null

  return h(
    Fragment,
    null,
    h(
      'div',
      { className: 'hlp-actions', role: 'group', 'aria-label': COPY.controls },
      !cancelOnly && allowed.take_over
        ? h(
            Button,
            {
              variant: primary === 'take_over' && !sayOpen ? 'default' : 'secondary',
              size: 'sm',
              disabled: Boolean(pending),
              loading: pending === 'take_over',
              onClick: () => run('take_over')
            },
            COPY.takeOver
          )
        : null,
      !cancelOnly && allowed.say
        ? h(
            Button,
            {
              variant: 'secondary',
              size: 'sm',
              disabled: Boolean(pending),
              'aria-expanded': sayOpen ? 'true' : 'false',
              onClick: () => setSayOpen((open) => !open)
            },
            COPY.sayThis
          )
        : null,
      allowed.hang_up
        ? h(
            'span',
            { className: 'hlp-actions-end' },
            h(
              Button,
              { variant: 'destructive', size: 'sm', disabled: pending === 'hang_up', onClick: openConfirm },
              cancelOnly ? COPY.cancelCall : COPY.hangUp
            )
          )
        : null
    ),
    sayOpen && allowed.say
      ? h(SayComposer, {
          sending: pending === 'say',
          onCancel: () => setSayOpen(false),
          onSend: async (text) => {
            const result = await run('say', { text })
            if (result.ok) setSayOpen(false)
          }
        })
      : null,
    ctl.feedback
      ? h(
          'p',
          { className: 'hlp-feedback', 'data-kind': ctl.feedback.kind, role: ctl.feedback.kind === 'info' ? 'status' : 'alert' },
          ctl.feedback.text
        )
      : null,
    h(ConfirmDialog, {
      open: confirming !== null,
      onClose: () => setConfirming(null),
      destructive: true,
      title: cancelOnly ? COPY.cancelTitle : COPY.hangUpTitle,
      description: cancelOnly
        ? COPY.cancelBody
        : call.state === 'on_hold' && confirming >= 60
          ? COPY.hangUpBodyHold(formatSpoken(confirming))
          : COPY.hangUpBody,
      confirmLabel: cancelOnly ? COPY.cancelCall : COPY.hangUp,
      busyLabel: COPY.hangingUp,
      doneLabel: COPY.hungUp,
      cancelLabel: COPY.cancel,
      onConfirm: async () => {
        const result = await run('hang_up')
        if (!result.ok) throw new Error(result.conflict ? COPY.conflict : COPY.failedAction)
      }
    })
  )
}

function SayComposer({ sending, onSend, onCancel }) {
  const [text, setText] = useState('')
  const ref = useRef(null)
  useEffect(() => {
    if (ref.current) ref.current.focus()
  }, [])
  const trimmed = text.trim()
  const submit = () => {
    if (trimmed && !sending) onSend(trimmed)
  }
  return h(
    'form',
    {
      className: 'hlp-compose',
      'data-enter': 'true',
      onSubmit: (e) => {
        e.preventDefault()
        submit()
      }
    },
    h('label', { className: 'hlp-compose-label', htmlFor: 'hlp-say' }, COPY.sayLabel),
    h(Textarea, {
      id: 'hlp-say',
      ref,
      rows: 2,
      maxLength: SAY_MAX_CHARS,
      value: text,
      placeholder: COPY.sayPlaceholder,
      'aria-describedby': 'hlp-say-hint',
      onChange: (e) => setText(e.target.value),
      onKeyDown: (e) => {
        if (isSubmitEnter(e) && !e.shiftKey) {
          e.preventDefault()
          submit()
        } else if (e.key === 'Escape') {
          e.preventDefault()
          e.stopPropagation()
          onCancel()
        }
      }
    }),
    h(
      'div',
      { className: 'hlp-compose-row' },
      h(
        'span',
        { id: 'hlp-say-hint', className: 'hlp-compose-hint' },
        COPY.sayHint,
        text.length >= SAY_MAX_CHARS - 40 ? h('span', { className: 'hlp-tnum' }, ` ${text.length}/${SAY_MAX_CHARS}`) : null
      ),
      h(Button, { type: 'button', variant: 'text', size: 'sm', onClick: onCancel }, COPY.cancel),
      h(Button, { type: 'submit', size: 'sm', disabled: !trimmed || sending, loading: sending }, COPY.send)
    )
  )
}

/** True after the first render: entrance motion plays on real state changes only. */
function useChanged(value) {
  const first = useRef(value)
  return value !== first.current
}

// ---------------------------------------------------------------------------------------------
// Signal: the colour field is the call state; the strip shows where the time went.
// ---------------------------------------------------------------------------------------------

function TimeStrip({ store, call }) {
  const clocks = useClocks(store, call)
  const phases = callPhases(call).filter((p) => p.key !== 'outcome')
  const live = stateMeta(call.state).live
  const secs = phases.map((p) => phaseSeconds(p, call, clocks))
  const total = secs.reduce((a, b) => a + b, 0) || 1
  return h(
    'div',
    { className: 'hlc-strip', role: 'list', 'aria-label': COPY.timeStrip },
    phases.map((p, i) => {
      const current = live && i === phases.length - 1
      return h(
        'div',
        { key: `${i}-${p.key}`, className: 'hlc-col', role: 'listitem', 'data-current': current ? 'true' : 'false', style: { flexGrow: secs[i] / total } },
        h('span', { className: 'hlc-bar', 'aria-hidden': 'true' }),
        // The live phase is timed by the headline clock, so its label names it without a number.
        h('span', { className: 'hlc-label' }, current ? `${COPY.shortPhase[p.key]} ${COPY.now}` : `${COPY.shortPhase[p.key]} ${formatClock(secs[i])}`)
      )
    })
  )
}

function SignalField({ store, call }) {
  const meta = stateMeta(call.state)
  const enter = useChanged(call.state)
  const quoted = quotedLine(call)
  const started = formatTimeOfDay(parseTs(call.started_at))
  let body
  switch (call.state) {
    case 'human_detected':
    case 'ringing_owner': {
      const held = holdSeconds(call)
      const note = stateNote(call)
      body = [
        h('div', { key: 'h', className: 'hlc-headline' }, call.state === 'ringing_owner' ? COPY.signal.ringing : COPY.signal.human),
        quoted
          ? h(
              'p',
              { key: 'q', className: 'hlc-quote' },
              '“',
              h(MaskedText, { text: quoted.text }),
              '”',
              h('span', { className: 'hlc-quote-by' }, COPY.signal.saidAt(callTime(call, quoted.ts)))
            )
          : null,
        h(
          'p',
          { key: 's', className: 'hlc-sub' },
          held != null ? `${COPY.signal.afterHold(formatClock(held))} ` : '',
          note
        )
      ]
      break
    }
    case 'ended': {
      const { lead } = splitSummary(call.summary)
      const rest = cardRest(call.summary)
      body = [
        h('div', { key: 'h', className: 'hlc-headline' }, lead ? h(MaskedText, { text: lead }) : meta.label),
        rest ? h('p', { key: 'r', className: 'hlc-rest' }, h(MaskedText, { text: rest })) : null,
        h('p', { key: 's', className: 'hlc-sub' }, `${COPY.endedAfter} ${formatClock(call.duration_s)}`)
      ]
      break
    }
    case 'failed':
      body = [
        h('div', { key: 'h', className: 'hlc-headline' }, meta.label),
        h('p', { key: 'r', className: 'hlc-rest' }, h(MaskedText, { text: failureReason(call) })),
        h('p', { key: 's', className: 'hlc-sub' }, COPY.failedNext)
      ]
      break
    default:
      body = [
        h('div', { key: 'h', className: 'hlc-headline' }, COPY.clockFor[call.state] || meta.label),
        meta.clock ? h(StateClock, { key: 'c', store, call, className: 'hlc-clock' }) : null,
        call.state === 'on_hold' && quoted
          ? h(
              'p',
              { key: 'q', className: 'hlc-heard' },
              h('span', { className: 'hlc-heard-at' }, `${COPY.signal.lastHeard(callTime(call, quoted.ts))} `),
              h(MaskedText, { text: quoted.text })
            )
          : stateNote(call)
            ? h('p', { key: 's', className: 'hlc-sub' }, stateNote(call))
            : null
      ]
  }
  return h(
    'section',
    { className: 'hlc-field', 'data-tone': meta.tone, 'aria-label': meta.label },
    h(
      'div',
      { className: 'hlc-task' },
      h('span', { className: 'hlc-task-title' }, call.task_title || COPY.paneTitle),
      started ? h('span', { className: 'hlc-task-meta' }, COPY.startedAt(started)) : null
    ),
    h('div', { key: call.state, className: 'hlc-body', 'data-enter': enter ? 'true' : 'false', 'aria-hidden': 'true' }, body),
    h(TimeStrip, { store, call })
  )
}

function CallView({ store, call, calls, onSelect }) {
  const ctl = useCallActions(store, call)
  return h(
    'div',
    { className: 'hlp' },
    h(StateAnnouncer, { call }),
    h(SignalField, { store, call }),
    stateMeta(call.state).live ? h('div', { className: 'hlc-controls' }, h(Controls, { store, call, ctl })) : null,
    h(Transcript, { call, footer: h(OtherCalls, { store, calls, focusId: call.id, onSelect }) })
  )
}

// ---------------------------------------------------------------------------------------------
// Pane shell
// ---------------------------------------------------------------------------------------------

function LoadingView() {
  return h(
    'div',
    { className: 'hlp-loading', 'aria-busy': 'true' },
    h(Skeleton, { style: { height: '1rem', width: '66%' } }),
    h(Skeleton, { style: { height: '0.75rem', width: '50%' } }),
    h(Skeleton, { style: { height: '2.5rem', width: '34%', marginTop: '0.75rem' } }),
    h(Skeleton, { style: { height: '0.75rem', width: '100%', marginTop: '1rem' } }),
    h(Skeleton, { style: { height: '0.75rem', width: '84%' } })
  )
}

export function CallsPane({ store }) {
  const status = useValue(store.$status)
  const snap = useValue(store.$snapshot)
  const stale = useValue(store.$stale)
  const [selectedId, setSelectedId] = useState(null)

  let body
  if (!snap && status === 'error') {
    body = h(
      'div',
      { className: 'hlp-center' },
      h(
        ErrorState,
        { title: COPY.errorTitle, description: COPY.errorBody },
        h('div', { className: 'hlp-retry' }, h(Button, { variant: 'secondary', size: 'sm', onClick: () => store.retry() }, COPY.retry))
      )
    )
  } else if (!snap) {
    body = h(LoadingView)
  } else if (snap.calls.length === 0) {
    // The kit's EmptyState draws its description in a muted grey below AA; keep the primitive for the
    // title and set the description in the secondary token.
    body = h(
      'div',
      { className: 'hlp-center' },
      h('div', null, h(EmptyState, { title: COPY.emptyTitle, className: 'min-h-0' }), h('p', { className: 'hlp-empty-body' }, COPY.emptyBody))
    )
  } else {
    const chosen = snap.calls.find((c) => c.id === selectedId) || pickFocusCall(snap.calls)
    body = h(CallView, { key: chosen.id, store, call: chosen, calls: snap.calls, onSelect: setSelectedId })
  }

  return h(
    'div',
    { className: 'hlp-root', style: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 } },
    h('style', null, PANE_CSS),
    stale && snap ? h('div', { className: 'hlp-stale', role: 'status' }, COPY.stale(formatTimeOfDay(snap.receivedAt))) : null,
    snap && snap.calls.length ? body : h('div', { className: 'hlp' }, body)
  )
}

// ---------------------------------------------------------------------------------------------
// Plugin entry
// ---------------------------------------------------------------------------------------------

export default {
  id: PLUGIN_ID,
  name: 'Hermes Line',
  register(ctx) {
    const store = createCallStore(ctx)
    store.start()
    ctx.register({
      id: PANE_ID,
      area: PANES_AREA,
      title: COPY.paneTitle,
      data: { placement: 'right', width: PANE_WIDTH },
      render: () => h(CallsPane, { store })
    })
  }
}
