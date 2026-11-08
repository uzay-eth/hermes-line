// Harness-only theme step. In the app, themes/context.tsx `applyTheme()` paints the active skin's
// seed colours onto :root at runtime; the compiled stylesheet alone only carries the light seeds.
// This mirrors that step for the default skin (Nous) using the app's own preset palette, so the
// dark captures use the real dark seeds. --dt-primary-solid is deepened with the app's own
// ensureContrast, as applyTheme does; the --ui-success harmonising is skipped (the pane does not use it).

import { ensureContrast } from '@hermes/shared/color'
import { nousTheme } from '@/themes/presets'

const PRIMARY_SOLID_FOREGROUND = '#fcfcfc' // context.tsx PRIMARY_SOLID_FOREGROUND

// Same values as context.tsx `mixesFor`.
const MIXES = {
  light: { chrome: '92%', sidebar: '100%', card: '22%', elevated: '28%', bubble: '0%' },
  dark: { chrome: '74%', sidebar: '100%', card: '38%', elevated: '46%', bubble: '46%' }
}

export function applyPreviewTheme(mode) {
  const root = document.documentElement
  const dark = mode === 'dark'
  const c = dark ? nousTheme.darkColors || nousTheme.colors : nousTheme.colors
  const midground = c.midground || c.ring
  const vars = {
    '--theme-foreground': c.foreground,
    '--theme-primary': c.primary,
    '--theme-secondary': c.secondary,
    '--theme-accent-soft': c.accent,
    '--theme-midground': midground,
    '--theme-warm': c.primary,
    '--theme-background-seed': c.background,
    '--theme-sidebar-seed': c.sidebarBackground || c.background,
    '--theme-card-seed': c.card,
    '--theme-elevated-seed': c.popover,
    '--theme-bubble-seed': c.userBubble || c.popover,
    '--theme-mix-chrome': MIXES[mode].chrome,
    '--theme-mix-sidebar': MIXES[mode].sidebar,
    '--theme-mix-card': MIXES[mode].card,
    '--theme-mix-elevated': MIXES[mode].elevated,
    '--theme-mix-bubble': MIXES[mode].bubble,
    '--dt-primary-foreground': c.primaryForeground,
    '--dt-secondary-foreground': c.secondaryForeground,
    '--dt-accent-foreground': c.accentForeground,
    '--dt-border': c.border,
    '--dt-input': c.input,
    '--dt-ring': c.ring,
    '--dt-muted': c.muted,
    '--dt-destructive': c.destructive,
    '--dt-destructive-foreground': c.destructiveForeground,
    '--dt-primary-solid': ensureContrast(c.primary, PRIMARY_SOLID_FOREGROUND, 4.5),
    '--dt-primary-solid-foreground': PRIMARY_SOLID_FOREGROUND
  }
  root.classList.toggle('dark', dark)
  root.style.setProperty('color-scheme', dark ? 'dark' : 'light')
  root.dataset.hermesTheme = 'nous'
  root.dataset.hermesMode = dark ? 'dark' : 'light'
  for (const [k, v] of Object.entries(vars)) if (v) root.style.setProperty(k, v)
}
