import { useCallback, useEffect, useState } from 'react'

export type ThemePreference = 'auto' | 'light' | 'dark'

const STORAGE_KEY = 'onionpi.theme'
const THEME_COLORS: Record<'light' | 'dark', string> = {
  dark: '#04101a',
  light: '#eef3f7',
}

export const THEME_LABELS: Record<ThemePreference, string> = {
  auto: 'Système',
  light: 'Clair',
  dark: 'Sombre',
}

function isPreference(value: string | null): value is ThemePreference {
  return value === 'auto' || value === 'light' || value === 'dark'
}

export function readThemePreference(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return isPreference(stored) ? stored : 'auto'
  } catch {
    // Private browsing modes refuse localStorage; the default still works.
    return 'auto'
  }
}

export function resolveTheme(preference: ThemePreference): 'light' | 'dark' {
  if (preference !== 'auto') return preference
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

/** Writes the preference onto <html> so the CSS token layer can react to it. */
export function applyThemePreference(preference: ThemePreference): void {
  const root = document.documentElement
  if (preference === 'auto') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', preference)
  const meta = document.querySelector('meta[name="theme-color"]')
  meta?.setAttribute('content', THEME_COLORS[resolveTheme(preference)])
}

export function useTheme(): [ThemePreference, (next: ThemePreference) => void] {
  const [preference, setPreference] = useState<ThemePreference>(readThemePreference)

  useEffect(() => {
    applyThemePreference(preference)
    try {
      window.localStorage.setItem(STORAGE_KEY, preference)
    } catch {
      // Nothing to persist to: the choice simply lasts for this session.
    }
    if (preference !== 'auto') return
    // Following the system means following it while the page stays open.
    const media = window.matchMedia('(prefers-color-scheme: light)')
    const listener = () => applyThemePreference('auto')
    media.addEventListener('change', listener)
    return () => media.removeEventListener('change', listener)
  }, [preference])

  return [preference, useCallback((next: ThemePreference) => setPreference(next), [])]
}
