export type ThemePreference = 'auto' | 'light' | 'dark'
const preferenceKey = 'theme'
const cookieKey = 'mmr-theme'
const cookieAge = 400 * 24 * 60 * 60
const themeEvent = 'mmr-theme-change'
let memoryPreference: ThemePreference | null = null

function storedPreference(): ThemePreference | null {
  try {
    const value = document.cookie.split('; ').find(item => item.startsWith(`${cookieKey}=`))?.slice(cookieKey.length + 1)
    if (value === 'auto' || value === 'light' || value === 'dark') return value
  } catch {}
  try {
    const value = localStorage.getItem(preferenceKey)
    if (value === 'auto' || value === 'light' || value === 'dark') return value
  } catch {}
  return null
}

export function themePreference(): ThemePreference {
  return memoryPreference ?? storedPreference() ?? 'auto'
}

export function applyTheme() {
  const preference = themePreference()
  document.documentElement.dataset.theme = preference === 'auto'
    ? window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
    : preference
}

export function setTheme(preference: ThemePreference) {
  memoryPreference = preference
  try { document.cookie = `${cookieKey}=${preference}; Path=/; Max-Age=${cookieAge}; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}` } catch {}
  try { localStorage.setItem(preferenceKey, preference) } catch {}
  memoryPreference = storedPreference() === preference ? null : preference
  // Theme changes still work when persistent browser storage is unavailable.
  document.documentElement.dataset.theme = preference === 'auto'
    ? window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
    : preference
  window.dispatchEvent(new Event(themeEvent))
}

export function initializeTheme() {
  applyTheme()
  for (const scheme of ['dark', 'light']) window.matchMedia(`(prefers-color-scheme: ${scheme})`).addEventListener('change', () => {
    if (themePreference() === 'auto') { applyTheme(); window.dispatchEvent(new Event(themeEvent)) }
  })
  const sync = () => { if (storedPreference()) memoryPreference = null; applyTheme(); window.dispatchEvent(new Event(themeEvent)) }
  window.addEventListener('focus', sync)
  document.addEventListener('visibilitychange', () => { if (!document.hidden) sync() })
  window.addEventListener('storage', event => {
    if (event.key === preferenceKey || event.key === null) { memoryPreference = null; applyTheme(); window.dispatchEvent(new Event(themeEvent)) }
  })
}

export function subscribeTheme(listener: () => void) {
  window.addEventListener(themeEvent, listener)
  return () => window.removeEventListener(themeEvent, listener)
}
