import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  Keyboard,
  LogOut,
  Moon,
  PanelLeft,
  RefreshCw,
  Sun,
} from 'lucide-react'
import { api, setCsrf } from './api'
import { CommandPalette, type Command } from './components/CommandPalette'
import { DeviceTable } from './components/DeviceTable'
import { Modal } from './components/Modal'
import { Sidebar } from './components/Sidebar'
import { Toaster } from './components/Toaster'
import { Topbar } from './components/Topbar'
import { useChat } from './hooks/useChat'
import { usePolling } from './hooks/usePolling'
import { useToasts } from './hooks/useToasts'
import { PAGE_IDS, pageTitle, type Page } from './navigation'
import { CircumventionPage } from './pages/CircumventionPage'
import { DashboardPage } from './pages/DashboardPage'
import { FilesPage } from './pages/FilesPage'
import { LoginPage } from './pages/LoginPage'
import { OnboardingPage } from './pages/OnboardingPage'
import { ChatPage, LogsPage, NetworkPage, SettingsPage, TorPage } from './pages/OperationalPages'
import { ProtectionPage } from './pages/ProtectionPage'
import { useTheme, type ThemePreference } from './theme'
import type { Session } from './types'

const COLLAPSE_KEY = 'onionpi.sidebar-collapsed'

function initialPage(): Page {
  const hash = window.location.hash.replace('#/', '') as Page
  return PAGE_IDS.includes(hash) ? hash : 'dashboard'
}

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === '1'
  } catch {
    return false
  }
}

export default function App() {
  const [session, setSession] = useState<Session>()
  const [booting, setBooting] = useState(true)
  // Held at the root so the login screen and the shell share one preference.
  const [theme, setTheme] = useTheme()

  useEffect(() => {
    api.session().then(setSession).catch(() => setSession(undefined)).finally(() => setBooting(false))
  }, [])

  if (booting) return <AppLoader label="OnionPi démarre…" />
  if (!session) return <LoginPage onLogin={async (username, password) => setSession(await api.login(username, password))} />
  return (
    <AuthenticatedApp
      session={session}
      theme={theme}
      onThemeChange={setTheme}
      onSignedOut={() => setSession(undefined)}
    />
  )
}

function AppLoader({ label }: { label: string }) {
  return (
    <div className="app-loader" role="status">
      <img src="/onionpi-mark.png" alt="" />
      <span>{label}</span>
    </div>
  )
}

type ShellProps = {
  session: Session
  theme: ThemePreference
  onThemeChange: (theme: ThemePreference) => void
  onSignedOut: () => void
}

function AuthenticatedApp({ session, theme, onThemeChange, onSignedOut }: ShellProps) {
  const [page, setPageState] = useState<Page>(initialPage)
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [identityBusy, setIdentityBusy] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const { toasts, notify, dismiss } = useToasts()
  const status = usePolling(api.status, 10_000)
  const onboarding = usePolling(api.onboarding, 15_000)
  const devices = usePolling(api.devices, 12_000)
  const traffic = usePolling(api.traffic, 5_000)
  const chat = useChat(true)

  useEffect(() => {
    setCsrf(session.csrf)
  }, [session.csrf])

  useEffect(() => {
    const handler = () => setPageState(initialPage())
    window.addEventListener('hashchange', handler)
    return () => window.removeEventListener('hashchange', handler)
  }, [])

  useEffect(() => {
    document.title = page === 'dashboard' ? 'OnionPi' : `${pageTitle(page)} · OnionPi`
  }, [page])

  useEffect(() => {
    try {
      window.localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0')
    } catch {
      // The layout preference is a nicety, not something to fail over.
    }
  }, [collapsed])

  const setPage = useCallback((next: Page) => {
    setPageState(next)
    window.location.hash = `/${next}`
  }, [])

  const refreshAll = useCallback(() => {
    void status.refresh()
    void devices.refresh()
    void traffic.refresh()
  }, [status, devices, traffic])

  const newIdentity = useCallback(async () => {
    setIdentityBusy(true)
    try {
      const result = await api.newIdentity()
      notify(result.message)
      window.setTimeout(() => void status.refresh(), 2000)
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Commande Tor refusée', true)
    } finally {
      setIdentityBusy(false)
    }
  }, [notify, status])

  const logout = useCallback(async () => {
    try {
      await api.logout()
    } finally {
      onSignedOut()
    }
  }, [onSignedOut])

  const authExpired = [status.error, devices.error, traffic.error].some((error) => error.includes('Authentification'))
  useEffect(() => {
    if (authExpired) void logout()
  }, [authExpired, logout])

  // Shortcuts are global on purpose: the palette is the fastest route to any
  // screen, and « ? » documents the rest of them.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      const typing = target?.tagName === 'INPUT' || target?.tagName === 'TEXTAREA' || target?.isContentEditable
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setPaletteOpen((open) => !open)
        return
      }
      if (event.key === 'Escape') {
        setMobileOpen(false)
        return
      }
      if (typing || event.ctrlKey || event.metaKey || event.altKey) return
      if (event.key === '?') {
        event.preventDefault()
        setShortcutsOpen(true)
      }
      if (event.key === 'r') {
        event.preventDefault()
        refreshAll()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [refreshAll])

  const commands = useMemo<Command[]>(() => [
    {
      id: 'action:new-identity',
      label: 'Demander une nouvelle identité Tor',
      icon: RefreshCw,
      group: 'Actions',
      hint: 'Change le circuit de sortie',
      keywords: 'newnym circuit ip sortie changer',
      run: () => void newIdentity(),
    },
    {
      id: 'action:refresh',
      label: 'Actualiser les données',
      icon: RefreshCw,
      group: 'Actions',
      hint: 'Touche R',
      keywords: 'recharger rafraichir',
      run: refreshAll,
    },
    {
      id: 'action:theme',
      label: theme === 'dark' ? 'Passer en thème clair' : 'Passer en thème sombre',
      icon: theme === 'dark' ? Sun : Moon,
      group: 'Affichage',
      keywords: 'theme clair sombre apparence contraste',
      run: () => onThemeChange(theme === 'dark' ? 'light' : 'dark'),
    },
    {
      id: 'action:collapse',
      label: collapsed ? 'Déplier le menu latéral' : 'Réduire le menu latéral',
      icon: PanelLeft,
      group: 'Affichage',
      keywords: 'sidebar menu navigation',
      run: () => setCollapsed((value) => !value),
    },
    {
      id: 'action:shortcuts',
      label: 'Voir les raccourcis clavier',
      icon: Keyboard,
      group: 'Affichage',
      hint: 'Touche ?',
      keywords: 'aide clavier raccourcis',
      run: () => setShortcutsOpen(true),
    },
    {
      id: 'action:logout',
      label: 'Se déconnecter',
      icon: LogOut,
      group: 'Compte',
      keywords: 'quitter session deconnexion',
      run: () => void logout(),
    },
  ], [collapsed, logout, newIdentity, onThemeChange, refreshAll, theme])

  if (!onboarding.data && !onboarding.error) return <AppLoader label="Vérification de la configuration…" />
  if (onboarding.data && !onboarding.data.complete) {
    return <OnboardingPage state={onboarding.data} refresh={onboarding.refresh} onPasswordChanged={onSignedOut} />
  }

  return (
    <div className={`app-shell ${collapsed ? 'app-collapsed' : ''}`}>
      <a className="skip-link" href="#contenu">Aller au contenu</a>
      <Sidebar
        page={page}
        collapsed={collapsed}
        mobileOpen={mobileOpen}
        onSelect={setPage}
        onCollapse={() => setCollapsed((value) => !value)}
        onMobileClose={() => setMobileOpen(false)}
      />
      <div className="app-body">
        <Topbar
          user={session.user}
          status={status.data}
          theme={theme}
          onThemeChange={onThemeChange}
          onLogout={() => void logout()}
          onMenu={() => setMobileOpen(true)}
          onCommandPalette={() => setPaletteOpen(true)}
          onShortcuts={() => setShortcutsOpen(true)}
        />
        {(status.error || devices.error) && (
          <p className="global-warning" role="status">
            <AlertCircle size={16} />
            Certaines données système sont indisponibles. Dernière valeur connue affichée.
          </p>
        )}
        <main id="contenu">
          {page === 'dashboard' && (
            <DashboardPage
              user={session.user}
              status={status.data}
              devices={devices.data ?? []}
              traffic={traffic.data ?? []}
              onNewIdentity={newIdentity}
              identityBusy={identityBusy}
              onOpenPage={setPage}
            />
          )}
          {page === 'network' && <NetworkPage status={status.data} devices={devices.data ?? []} />}
          {page === 'tor' && (
            <TorPage
              status={status.data}
              busy={identityBusy}
              onNewIdentity={newIdentity}
              onOpenBridges={() => setPage('bridges')}
              notify={notify}
            />
          )}
          {page === 'bridges' && (
            <CircumventionPage status={status.data} notify={notify} onStatusRefresh={() => void status.refresh()} />
          )}
          {page === 'devices' && (
            <div className="page operational-page">
              <div className="page-title">
                <h1>Appareils</h1>
                <p>Clients actuellement visibles sur le point d’accès OnionPi.</p>
              </div>
              <DeviceTable devices={devices.data ?? []} searchable />
            </div>
          )}
          {page === 'protection' && (
            <ProtectionPage
              status={status.data}
              devices={devices.data ?? []}
              notify={notify}
              onDevicesRefresh={() => void devices.refresh()}
            />
          )}
          {page === 'files' && <FilesPage activities={status.data?.activities ?? []} chat={chat} notify={notify} />}
          {page === 'chat' && <ChatPage {...chat} />}
          {page === 'logs' && <LogsPage />}
          {page === 'settings' && <SettingsPage status={status.data} onPasswordChanged={onSignedOut} notify={notify} />}
        </main>
      </div>
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onNavigate={setPage}
        actions={commands}
      />
      {shortcutsOpen && <ShortcutsDialog onClose={() => setShortcutsOpen(false)} />}
      <Toaster toasts={toasts} onDismiss={dismiss} />
    </div>
  )
}

const SHORTCUTS: Array<[string, string[]]> = [
  ['Ouvrir la palette de commandes', ['Ctrl', 'K']],
  ['Actualiser les données', ['R']],
  ['Afficher cette aide', ['?']],
  ['Fermer une fenêtre ou le menu', ['Échap']],
]

function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  return (
    <Modal
      title="Raccourcis clavier"
      description="Ils fonctionnent partout dans l’interface, sauf pendant la saisie d’un texte."
      icon={<Keyboard size={22} />}
      onClose={onClose}
      actions={<button type="button" className="button button-secondary" onClick={onClose}>Fermer</button>}
    >
      <div className="shortcut-list">
        {SHORTCUTS.map(([label, keys]) => (
          <div key={label}>
            <span>{label}</span>
            <div>{keys.map((key) => <span className="kbd" key={key}>{key}</span>)}</div>
          </div>
        ))}
      </div>
    </Modal>
  )
}
