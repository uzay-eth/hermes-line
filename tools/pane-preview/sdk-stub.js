// Offline stand-in for `@hermes/plugin-sdk`, used only by tools/pane-preview and tests/l3.
//
// The UI kit is NOT re-implemented here: Button, Textarea, ConfirmDialog, EmptyState, ErrorState,
// Skeleton, Codicon and isSubmitEnter are the Hermes Desktop app's own source files, resolved at
// bundle time from the read-only Hermes checkout (build.mjs aliases `@` to its apps/desktop/src).
// That keeps the preview faithful to the real pane. Only the host-side doors (host.state, notify)
// are faked, because they need a running app.

import { atom } from 'nanostores'

export { Button } from '@/components/ui/button'
export { Codicon } from '@/components/ui/codicon'
export { ConfirmDialog } from '@/components/ui/confirm-dialog'
export { EmptyState } from '@/components/ui/empty-state'
export { ErrorState } from '@/components/ui/error-state'
export { Skeleton } from '@/components/ui/skeleton'
export { Textarea } from '@/components/ui/textarea'
export { isSubmitEnter } from '@/lib/ime'
export { atom, computed } from 'nanostores'
export { useStore as useValue } from '@nanostores/react'

export const PANES_AREA = 'panes'

const toasts = []

export const host = {
  state: {
    profile: atom('default'),
    gateway: atom('open')
  },
  notify(toast) {
    toasts.push(toast)
    return String(toasts.length)
  },
  notifyError(error, fallback) {
    toasts.push({ kind: 'error', message: fallback, detail: String(error) })
  }
}

export const __preview = { toasts }
