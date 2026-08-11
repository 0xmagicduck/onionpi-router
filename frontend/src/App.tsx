import { useEffect, useState } from 'react'
import { AlertCircle, CheckCircle2, X } from 'lucide-react'
import { api, ApiError, setCsrf } from './api'
import { Sidebar, type Page } from './components/Sidebar'
import { Topbar } from './components/Topbar'
import { DeviceTable } from './components/DeviceTable'
import { useChat } from './hooks/useChat'
import { usePolling } from './hooks/usePolling'
import { CircumventionPage } from './pages/CircumventionPage'
import { DashboardPage } from './pages/DashboardPage'
import { FilesPage } from './pages/FilesPage'
import { LoginPage } from './pages/LoginPage'
import { ChatPage, LogsPage, NetworkPage, SettingsPage, TorPage } from './pages/OperationalPages'
import { ProtectionPage } from './pages/ProtectionPage'
import type { Session } from './types'

const pageIds: Page[] = ['dashboard', 'network', 'tor', 'bridges', 'devices', 'protection', 'files', 'chat', 'logs', 'settings']

function initialPage(): Page {
  const hash = window.location.hash.replace('#/', '') as Page
  return pageIds.includes(hash) ? hash : 'dashboard'
}

export default function App() {
  const [session, setSession] = useState<Session>()
  const [booting, setBooting] = useState(true)

  useEffect(() => {
    api.session().then(setSession).catch(() => setSession(undefined)).finally(() => setBooting(false))
  }, [])

  if (booting) return <div className="app-loader"><img src="/onionpi-mark.png" alt="" /><span>OnionPi démarre…</span></div>
  if (!session) return <LoginPage onLogin={async (username, password) => setSession(await api.login(username, password))} />
  return <AuthenticatedApp session={session} onSignedOut={() => setSession(undefined)} />
}

function AuthenticatedApp({ session, onSignedOut }: { session: Session; onSignedOut: () => void }) {
  const [page, setPageState] = useState<Page>(initialPage)
  const [collapsed, setCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [identityBusy, setIdentityBusy] = useState(false)
  const [toast, setToast] = useState<{ message: string; error?: boolean }>()
  const status = usePolling(api.status, 10_000)
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

  const setPage = (next: Page) => {
    setPageState(next)
    window.location.hash = `/${next}`
  }
  const notify = (message: string, error = false) => {
    setToast({ message, error })
    window.setTimeout(() => setToast(undefined), 4200)
  }
  const newIdentity = async () => {
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
  }
  const logout = async () => {
    try { await api.logout() } finally { onSignedOut() }
  }
  const authExpired = [status.error, devices.error, traffic.error].some((error) => error.includes('Authentification'))
  useEffect(() => {
    if (authExpired) void logout()
  }, [authExpired])

  return (
    <div className={`app-shell ${collapsed ? 'app-collapsed' : ''}`}>
      <Sidebar page={page} collapsed={collapsed} mobileOpen={mobileOpen} onSelect={setPage} onCollapse={() => setCollapsed((value) => !value)} onMobileClose={() => setMobileOpen(false)} />
      <div className="app-body">
        <Topbar user={session.user} status={status.data} onLogout={() => void logout()} onMenu={() => setMobileOpen(true)} />
        {(status.error || devices.error) && <div className="global-warning"><AlertCircle size={16} />Certaines données système sont indisponibles.</div>}
        {page === 'dashboard' && <DashboardPage user={session.user} status={status.data} devices={devices.data ?? []} traffic={traffic.data ?? []} onNewIdentity={newIdentity} identityBusy={identityBusy} />}
        {page === 'network' && <NetworkPage status={status.data} devices={devices.data ?? []} />}
        {page === 'tor' && <TorPage status={status.data} busy={identityBusy} onNewIdentity={newIdentity} onOpenBridges={() => setPage('bridges')} notify={notify} />}
        {page === 'bridges' && <CircumventionPage status={status.data} notify={notify} onStatusRefresh={() => void status.refresh()} />}
        {page === 'devices' && <div className="page operational-page"><div className="page-title"><h1>Appareils</h1><p>Clients actuellement visibles sur le point d’accès OnionPi.</p></div><DeviceTable devices={devices.data ?? []} /></div>}
        {page === 'protection' && <ProtectionPage devices={devices.data ?? []} notify={notify} onDevicesRefresh={() => void devices.refresh()} />}
        {page === 'files' && <FilesPage activities={status.data?.activities ?? []} chat={chat} />}
        {page === 'chat' && <ChatPage {...chat} />}
        {page === 'logs' && <LogsPage />}
        {page === 'settings' && <SettingsPage status={status.data} onPasswordChanged={onSignedOut} notify={notify} />}
      </div>
      {toast && <div className={`toast ${toast.error ? 'toast-error' : ''}`} role="status">{toast.error ? <AlertCircle size={18} /> : <CheckCircle2 size={18} />}{toast.message}<button onClick={() => setToast(undefined)} aria-label="Fermer"><X size={15} /></button></div>}
    </div>
  )
}
