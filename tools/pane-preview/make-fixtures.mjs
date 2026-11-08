#!/usr/bin/env node
// Writes tools/pane-preview/fixtures/*.json from ONE sample day, so every scene tells the same story:
// each scene is a call's transcript cut at that scene's `now`, with `state_since`, `duration_s` and
// the phase times all derived from the same timestamps. Run: node tools/pane-preview/make-fixtures.mjs
//
// SAMPLE DATA. Businesses are "Sample ...", numbers are in the fictional 555-01xx range and arrive
// masked, codes arrive as [CODE] the way the engine sends them. Times are UTC; the preview renders
// them in America/Los_Angeles (UTC-8 on this date), so 17:01:40Z reads as 9:01 AM.

import { writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const OUT = join(dirname(fileURLToPath(import.meta.url)), 'fixtures')
const DAY = '2026-11-07'
const t = (hms, day = DAY) => `${day}T${hms}Z`
const secs = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 1000)
const MASKED = '+1 555-01••'

function script(lines) {
  return lines.map(([hms, speaker, text], i) => ({ seq: i + 1, ts: t(hms), speaker, text }))
}

// --- The airline call, 9:01:40 AM to 9:42:31 AM: menu 0:41, hold 38:32, person 1:38, total 40:51.
const AIRLINE_START = t('17:01:40')
const AIRLINE = script([
  ['17:01:40', 'system', 'Call placed'],
  ['17:01:44', 'business', 'Thank you for calling Sample Airline. For new reservations, press 1. To change an existing trip, press 2.'],
  ['17:01:52', 'system', 'Pressed 2'],
  ['17:01:58', 'business', 'Please say or enter your six-character confirmation code.'],
  ['17:02:03', 'hermes', '[CODE]'],
  ['17:02:12', 'business', 'Thank you. All of our agents are helping other customers. Your wait time is more than 30 minutes.'],
  ['17:02:21', 'system', 'On hold'],
  ['17:24:50', 'business', 'Thank you for your patience. Please stay on the line and an agent will be with you shortly.'],
  // The person's first words are the moment the engine detects them: both at 39:13 call time.
  ['17:40:53', 'business', 'Thanks for holding, this is Sample Airline reservations. How can I help you today?'],
  ['17:40:53', 'system', 'Person answered'],
  ['17:40:56', 'hermes', "Hi, this is Hermes, an AI assistant calling for a customer about changing a flight. I'm bringing them on the line now, one moment."],
  ['17:41:02', 'system', 'Ringing your phone'],
  ['17:41:06', 'business', "No problem, I'll wait."],
  ['17:41:19', 'system', 'You joined the call'],
  ['17:41:22', 'owner', "Hi, I'd like to move my Thursday flight to Friday morning if there's anything."],
  ['17:41:31', 'business', 'Let me look. I have a 9:40 a.m. departure on Friday with seats open. The change fee is $75.'],
  ['17:41:44', 'owner', 'That works, go ahead.'],
  ['17:42:18', 'business', "Done. Your new confirmation code is [CODE]. We'll text the itinerary to [NUMBER]."],
  ['17:42:24', 'owner', 'Thank you.'],
  ['17:42:27', 'business', "You're welcome, have a good trip."],
  ['17:42:31', 'system', 'Call ended']
])
const AIRLINE_SUMMARY =
  'Moved the flight to Friday, 9:40 a.m. departure. You approved the $75 change fee on the call. The new confirmation code is masked here; the airline is emailing it and texting the itinerary.'

// --- The bistro call, 11:05:10 AM to 11:06:10 AM (no hold).
const BISTRO_START = t('19:05:10')
const BISTRO = script([
  ['19:05:10', 'system', 'Call placed'],
  ['19:05:14', 'business', 'Sample Bistro, how can I help?'],
  ['19:05:15', 'system', 'Person answered'],
  ['19:05:18', 'hermes', "Hi, this is Hermes, an AI assistant calling for a customer. I'd like to book a table for four tonight at 7."],
  ['19:05:29', 'business', "We're full at 7. I can do 7:30 or 8:15."],
  ['19:05:32', 'hermes', 'One moment, let me check the calendar.'],
  ['19:05:37', 'hermes', '7:30 works. A table for four at 7:30, please.'],
  ['19:05:44', 'business', 'Great. What name should I put it under?'],
  ['19:05:48', 'hermes', 'Please put it under Sample Customer.'],
  ['19:05:59', 'business', 'Got it. A table for four at 7:30 tonight under Sample Customer. We hold tables for 15 minutes.'],
  ['19:06:05', 'hermes', 'Perfect, thank you. Goodbye.'],
  ['19:06:10', 'system', 'Call ended']
])
const BISTRO_SUMMARY =
  'Booked a table for 4 at Sample Bistro tonight at 7:30 p.m. under Sample Customer; 7:00 was full. They hold the table for 15 minutes.'

// --- The clinic call that never connected, 12:30 PM to 12:50:41 PM (three tries).
const CLINIC_START = t('20:30:00')
const CLINIC = script([
  ['20:30:00', 'system', 'Call placed'],
  ['20:30:41', 'system', 'No answer'],
  ['20:40:00', 'system', 'Call placed'],
  ['20:40:41', 'system', 'No answer'],
  ['20:50:00', 'system', 'Call placed'],
  ['20:50:41', 'system', 'No answer after 3 tries. Hermes stopped retrying.']
])

// --- The pharmacy call, asked for at 2:00 PM.
const PHARMACY_START = t('22:00:00')
const PHARMACY = script([['22:00:20', 'system', 'Call placed']])

// --- A callback from the day before.
const CALLBACK = {
  id: 'call_sample_clinic_callback',
  task_title: 'Sample Clinic called back about Thursday',
  direction: 'inbound',
  counterpart_masked: MASKED,
  state: 'ended',
  state_since: '2026-11-06T22:15:41Z',
  started_at: '2026-11-06T22:14:05Z',
  duration_s: 96,
  mode: 'agent',
  transcript: [
    { seq: 1, ts: '2026-11-06T22:14:05Z', speaker: 'system', text: 'Answered a callback' },
    { seq: 2, ts: '2026-11-06T22:14:09Z', speaker: 'business', text: 'Hi, this is Sample Clinic returning a call about an appointment on Thursday.' },
    { seq: 3, ts: '2026-11-06T22:14:12Z', speaker: 'hermes', text: 'Hi, this is Hermes, an AI assistant answering for the patient. Thanks for calling back. Is the 10:30 a.m. slot on Thursday still open?' },
    { seq: 4, ts: '2026-11-06T22:14:30Z', speaker: 'business', text: "Yes, it's confirmed for 10:30. Please arrive ten minutes early." },
    { seq: 5, ts: '2026-11-06T22:15:41Z', speaker: 'system', text: 'Call ended' }
  ],
  summary: 'Sample Clinic confirmed Thursday at 10:30 a.m. Arrive ten minutes early.'
}

/** A call as the engine would report it at `now`: transcript cut at `now`, live duration to `now`. */
function at(base, now, state, stateSince, { summary = null, live = true } = {}) {
  const transcript = base.lines.filter((l) => Date.parse(l.ts) <= Date.parse(now))
  const end = live ? now : transcript[transcript.length - 1].ts
  return {
    id: base.id,
    task_title: base.title,
    direction: 'outbound',
    counterpart_masked: MASKED,
    state,
    state_since: stateSince,
    started_at: base.start,
    duration_s: secs(base.start, end),
    mode: base.mode,
    transcript,
    summary
  }
}

const airline = { id: 'call_sample_airline', title: 'Move Sample Airline flight to Friday', start: AIRLINE_START, lines: AIRLINE, mode: 'owner' }
const bistro = { id: 'call_sample_bistro', title: 'Book a table for 4 at 7 at Sample Bistro', start: BISTRO_START, lines: BISTRO, mode: 'agent' }
const clinic = { id: 'call_sample_clinic', title: 'Confirm Thursday appointment with Sample Clinic', start: CLINIC_START, lines: CLINIC, mode: 'agent' }
const pharmacy = { id: 'call_sample_pharmacy', title: 'Ask Sample Pharmacy whether the refill is ready', start: PHARMACY_START, lines: PHARMACY, mode: 'agent' }

const airlineEnded = at(airline, t('17:44:00'), 'ended', t('17:42:31'), { summary: AIRLINE_SUMMARY, live: false })
const bistroEnded = at(bistro, t('19:08:00'), 'ended', t('19:06:10'), { summary: BISTRO_SUMMARY, live: false })

// Scenes in the order of the day. Each `now` is a few seconds after the last thing that happened.
const scenes = [
  ['ivr', 'Phone menu', t('17:02:06'), [at(airline, t('17:02:06'), 'ivr', t('17:01:44')), CALLBACK]],
  ['on_hold', 'On hold, with an earlier call in the list', t('17:40:33'), [at(airline, t('17:40:33'), 'on_hold', t('17:02:21')), CALLBACK]],
  ['human_detected', 'A person answered', t('17:40:58'), [at(airline, t('17:40:58'), 'human_detected', t('17:40:53'))]],
  ['ringing_owner', 'Ringing the owner', t('17:41:12'), [at(airline, t('17:41:12'), 'ringing_owner', t('17:41:02'))]],
  ['owner_connected', 'Owner connected', t('17:42:26'), [at(airline, t('17:42:26'), 'owner_connected', t('17:41:19'))]],
  ['ended', 'Ended, with summary', t('17:44:00'), [airlineEnded, CALLBACK]],
  ['agent_talking', 'Hermes talking', t('19:05:47'), [at(bistro, t('19:05:47'), 'agent_talking', t('19:05:15')), airlineEnded]],
  ['failed', 'Failed', t('20:51:00'), [at(clinic, t('20:51:00'), 'failed', t('20:50:41'), { live: false }), bistroEnded, airlineEnded]],
  ['awaiting_approval', 'Waiting for approval', t('22:00:05'), [{ ...at(pharmacy, t('22:00:05'), 'awaiting_approval', PHARMACY_START), duration_s: 0 }]],
  ['dialing', 'Dialing', t('22:00:24'), [at(pharmacy, t('22:00:24'), 'dialing', t('22:00:20'))]],
  ['empty', 'No calls', t('16:30:00'), []]
]

const index = {
  sample: true,
  notice:
    'SAMPLE DATA for the offline pane preview, generated by make-fixtures.mjs from one sample day. Businesses are named Sample, numbers are in the fictional 555-01xx range and are shown masked as the engine would send them. Nothing here comes from a real call.',
  contract: 'docs/CONTRACT.md, Desktop REST and events: GET /api/plugins/hermes-line/calls -> {revision, calls: [Call]}',
  timezone: 'America/Los_Angeles',
  locale: 'en-US',
  scenes: []
}

scenes.forEach(([id, title, now, calls], i) => {
  const file = `${id}.json`
  writeFileSync(join(OUT, file), `${JSON.stringify({ revision: 10 + i * 3, calls }, null, 2)}\n`)
  index.scenes.push({ id, title, now, file, required: ['on_hold', 'human_detected', 'owner_connected', 'ended', 'failed', 'agent_talking', 'ringing_owner'].includes(id) })
})
writeFileSync(join(OUT, 'index.json'), `${JSON.stringify(index, null, 2)}\n`)
console.log(`wrote ${scenes.length} scenes to ${OUT}`)
