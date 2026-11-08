// The pane's live-region label per call state (plugin/desktop/plugin.js STATE_META[state].label).
// live-capture.mjs waits for this text before a state-change screenshot; tests/l3 checks the table
// against the plugin, so the two cannot drift.
export const STATE_LABELS = {
  awaiting_approval: 'Waiting for your approval',
  dialing: 'Dialing',
  ivr: 'In the phone menu',
  on_hold: 'On hold',
  human_detected: 'A person answered',
  ringing_owner: 'Ringing your phone',
  owner_connected: 'You are on the call',
  agent_talking: 'Hermes is talking',
  ended: 'Call ended',
  failed: 'Call did not go through'
}

// Short names for file names, in the order a call can move through them.
export const STATE_SLUGS = {
  awaiting_approval: 'approval',
  dialing: 'dialing',
  ivr: 'menu',
  on_hold: 'hold',
  human_detected: 'human',
  ringing_owner: 'owner-ring',
  owner_connected: 'bridged',
  agent_talking: 'fallback-agent',
  ended: 'ended',
  failed: 'failed'
}
