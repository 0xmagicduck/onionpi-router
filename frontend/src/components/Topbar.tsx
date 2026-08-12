import { useRef, useState } from 'react'
import {
  ChevronDown,
  Keyboard,
  LogOut,
  Monitor,
  Moon,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sun,
  Thermometer,
  Wifi,
} from 'lucide-react'
import { initials } from '../lib'
import { THEME_LABELS, type ThemePreference } from '../theme'
import { useDismissable } from '../hooks/useDismissable'
import type { StatusPayload, User } from '../types'
import { MobileMenuButton } from './Sidebar'

type Props = {
  user: User
  status?: StatusPayload
  theme: ThemePreference
  onThemeChange: (theme: ThemePreference) => void
  onLogout: () => void
  onMenu: () => void
  onCommandPalette: () => void
  onShortcuts: () => void
}

const THEME_ICONS: Record<ThemePreference, typeof Sun> = { auto: Monitor, light: Sun, dark: Moon }

export function Topbar({
  user,
  status,
  theme,
  onThemeChange,
  onLogout,
  onMenu,
  onCommandPalette,
  onShortcuts,
}: Props) {
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  useDismissable(menuRef, menuOpen, () => setMenuOpen(false))

  const protection = status?.protection
  const tone = protection?.status === 'protected'
    ? 'status-pill-good'
    : protection?.status === 'degraded'
      ? 'status-pill-danger'
      : protection
        ? 'status-pill-warn'
        : ''

  return (
    <header className="topbar">
      <MobileMenuButton onClick={onMenu} />
      <span className={`status-pill ${tone}`} title={protection?.summary}>
        {protection && !protection.safe ? <ShieldAlert /> : <ShieldCheck />}
        <span>{protection?.label ?? 'État inconnu'}</span>
      </span>
      <div className="top-metrics">
        <span className="top-meta"><Wifi /><strong>{status?.network.ssid ?? 'OnionPi Wi-Fi'}</strong></span>
        <span className="top-meta"><Thermometer /><strong>{status?.system.temperature_c ?? '—'} °C</strong></span>
      </div>
      <div className="top-spacer" />
      <button className="command-trigger" onClick={onCommandPalette} aria-label="Ouvrir la palette de commandes">
        <Search size={15} />
        <span>Rechercher…</span>
        <span className="kbd">Ctrl K</span>
      </button>
      <div className="menu-anchor" ref={menuRef}>
        <button
          className="user-menu"
          onClick={() => setMenuOpen((open) => !open)}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
        >
          <span className="avatar">{initials(user.display_name)}</span>
          <span className="user-name">{user.display_name}</span>
          <ChevronDown size={15} />
        </button>
        {menuOpen && (
          <div className="menu" role="menu">
            <span className="menu-label">Apparence</span>
            <div className="menu-row">
              <span>{THEME_LABELS[theme]}</span>
              <div className="segmented" role="group" aria-label="Thème de l’interface">
                {(['auto', 'light', 'dark'] as ThemePreference[]).map((option) => {
                  const Icon = THEME_ICONS[option]
                  return (
                    <button
                      key={option}
                      onClick={() => onThemeChange(option)}
                      aria-pressed={theme === option}
                      title={THEME_LABELS[option]}
                    >
                      <Icon size={15} />
                      <span className="sr-only">{THEME_LABELS[option]}</span>
                    </button>
                  )
                })}
              </div>
            </div>
            <div className="menu-separator" />
            <button className="menu-item" role="menuitem" onClick={() => { setMenuOpen(false); onShortcuts() }}>
              <Keyboard size={16} /> Raccourcis clavier
            </button>
            <button className="menu-item menu-item-danger" role="menuitem" onClick={() => { setMenuOpen(false); onLogout() }}>
              <LogOut size={16} /> Se déconnecter
            </button>
          </div>
        )}
      </div>
    </header>
  )
}
