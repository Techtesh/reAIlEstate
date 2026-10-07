import { useSyncExternalStore } from 'react'
import { Moon, Sun } from 'lucide-react'
import { setTheme, subscribeTheme, themePreference, type ThemePreference } from './theme'

export function ThemeControl() {
  const preference = useSyncExternalStore(subscribeTheme, themePreference)
  return <label className="theme-control"><Sun size={16} aria-hidden="true" /><span className="sr-only">Color theme</span><select aria-label="Color theme" value={preference} onChange={event => setTheme(event.target.value as ThemePreference)}><option value="auto">System</option><option value="light">Light</option><option value="dark">Dark</option></select><Moon size={16} aria-hidden="true" /></label>
}
