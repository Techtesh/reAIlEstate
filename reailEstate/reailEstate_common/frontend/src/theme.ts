export type ThemePreference = 'auto' | 'light' | 'dark'
const preferenceKey = 'theme'
const themeEvent = 'mmr-theme-change'
let memoryPreference: ThemePreference | null = null

export function themePreference(): ThemePreference {
  if (memoryPreference) return memoryPreference
  try { const value = localStorage.getItem(preferenceKey); if (value === 'light' || value === 'dark') return value } catch {}
  return 'auto'
}

export function applyTheme() {
  const preference = themePreference()
  document.documentElement.dataset.theme = preference === 'auto'
    ? window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
    : preference
}

export function setTheme(preference: ThemePreference) {
  memoryPreference = preference
  try { localStorage.setItem(preferenceKey, preference); memoryPreference = null } catch {}
  // Theme changes still work when persistent browser storage is unavailable.
  document.documentElement.dataset.theme = preference === 'auto'
    ? window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
    : preference
  window.dispatchEvent(new Event(themeEvent))
}

export function initializeTheme() {
  applyTheme()
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (themePreference() === 'auto') { applyTheme(); window.dispatchEvent(new Event(themeEvent)) }
  })
  window.addEventListener('storage', event => {
    if (event.key === preferenceKey || event.key === null) { memoryPreference = null; applyTheme(); window.dispatchEvent(new Event(themeEvent)) }
  })
}

export function subscribeTheme(listener: () => void) {
  window.addEventListener(themeEvent, listener)
  return () => window.removeEventListener(themeEvent, listener)
}
