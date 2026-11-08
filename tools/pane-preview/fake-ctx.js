// A fake PluginContext for the offline preview and the L3 tests. It serves one fixture snapshot
// for GET /calls and records every POST, without any network.
//
// mode:
//   ok        GET returns the snapshot; POSTs succeed.
//   loading   GET never settles.
//   error     GET rejects (backend missing).
//   stale     first GET succeeds, later GETs reject.
//   conflict  GET succeeds; POSTs reject with a 409 like the desktop bridge does ("409: ...").

export function createFakeCtx({ snapshot, mode = 'ok', storage: stored = {} } = {}) {
  const contributions = []
  const posts = []
  const notifications = []
  const listeners = new Map()
  const disposers = []
  let gets = 0
  let current = snapshot

  function track(dispose) {
    disposers.push(dispose)
    return dispose
  }

  const ctx = {
    source: 'plugin:hermes-line',
    register(contribution) {
      contributions.push(contribution)
      return () => {
        const i = contributions.indexOf(contribution)
        if (i >= 0) contributions.splice(i, 1)
      }
    },
    registerMany(list) {
      const offs = list.map((c) => ctx.register(c))
      return () => offs.forEach((off) => off())
    },
    async rest(path, opts = {}) {
      const method = (opts.method || 'GET').toUpperCase()
      if (method === 'GET') {
        gets += 1
        if (mode === 'loading') return new Promise(() => {})
        if (mode === 'error' || (mode === 'stale' && gets > 1)) {
          const err = new Error('502: plugin backend unavailable')
          err.statusCode = 502
          throw err
        }
        if (path !== '/calls') throw new Error(`404: ${path}`)
        return JSON.parse(JSON.stringify(current))
      }
      posts.push({ path, method, body: opts.body })
      if (mode === 'conflict') {
        const err = new Error('409: stale revision')
        err.statusCode = 409
        throw err
      }
      return { ok: true }
    },
    socket() {
      return () => {}
    },
    onEvent(type, listener) {
      if (!listeners.has(type)) listeners.set(type, new Set())
      listeners.get(type).add(listener)
      return track(() => listeners.get(type).delete(listener))
    },
    onDispose(fn) {
      disposers.push(fn)
    },
    setTimeout(fn, ms) {
      const id = setTimeout(fn, ms)
      return track(() => clearTimeout(id))
    },
    setInterval(fn, ms) {
      const id = setInterval(fn, ms)
      return track(() => clearInterval(id))
    },
    addEventListener(target, type, listener, options) {
      target.addEventListener(type, listener, options)
      return track(() => target.removeEventListener(type, listener, options))
    },
    os: {
      notify(n) {
        notifications.push(n)
        return Promise.resolve(true)
      }
    },
    storage: {
      get: (k, fallback) => (Object.prototype.hasOwnProperty.call(stored, k) ? stored[k] : fallback),
      set: () => {},
      remove: () => {}
    }
  }

  return {
    ctx,
    contributions,
    posts,
    notifications,
    get gets() {
      return gets
    },
    setSnapshot(next) {
      current = next
    },
    emit(type, payload) {
      for (const fn of listeners.get(type) || []) fn({ type, payload, session_id: '' })
    },
    dispose() {
      while (disposers.length) disposers.pop()()
    }
  }
}
