import { HardDrive, ShieldCheck, Thermometer, Wifi } from 'lucide-react'
import type { User, StatusPayload } from '../types'
import { initials } from '../lib'
import { MobileMenuButton } from './Sidebar'

type Props = {
  user: User
  status?: StatusPayload
  onLogout: () => void
  onMenu: () => void
}

export function Topbar({ user, status, onLogout, onMenu }: Props) {
  const secure = status?.tor.connected ?? false
  return (
    <header className="topbar">
      <MobileMenuButton onClick={onMenu} />
      <div className={`top-status ${secure ? 'status-good' : 'status-warn'}`}>
        <ShieldCheck size={18} />
        <span>{secure ? 'Sécurisé' : 'Tor démarre'}</span>
      </div>
      <span className="top-divider" />
      <div className="top-meta"><Wifi size={18} /><span>{status?.network.ssid ?? 'OnionPi Wi-Fi'}</span></div>
      <span className="top-divider" />
      <div className="top-meta"><Thermometer size={17} /><span>{status?.system.temperature_c ?? '—'} °C</span></div>
      <span className="top-divider" />
      <div className="top-meta"><HardDrive size={17} /><span>{status ? Math.round(100 - status.system.storage_percent) : '—'}% libre</span></div>
      <div className="top-spacer" />
      <button className="user-menu" onClick={onLogout} title="Se déconnecter">
        <span className="avatar">{initials(user.display_name)}</span>
        <span>{user.display_name}</span>
      </button>
    </header>
  )
}
