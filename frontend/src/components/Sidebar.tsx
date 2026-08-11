import {
  ChevronLeft,
  FileText,
  Folder,
  Gauge,
  HardDrive,
  Logs,
  Menu,
  MessageCircle,
  Network,
  Settings,
  Shield,
  ShieldBan,
  Waypoints,
  Wifi,
  X,
} from 'lucide-react'
import { Logo } from './Logo'

export type Page =
  | 'dashboard'
  | 'network'
  | 'tor'
  | 'bridges'
  | 'devices'
  | 'protection'
  | 'files'
  | 'chat'
  | 'logs'
  | 'settings'

const items: Array<{ id: Page; label: string; icon: typeof Gauge }> = [
  { id: 'dashboard', label: 'Vue d’ensemble', icon: Gauge },
  { id: 'network', label: 'Réseau', icon: Wifi },
  { id: 'tor', label: 'Tor', icon: Shield },
  { id: 'bridges', label: 'Contournement', icon: Waypoints },
  { id: 'devices', label: 'Appareils', icon: HardDrive },
  { id: 'protection', label: 'Protection', icon: ShieldBan },
  { id: 'files', label: 'Fichiers', icon: Folder },
  { id: 'chat', label: 'Chat', icon: MessageCircle },
  { id: 'logs', label: 'Journaux', icon: Logs },
  { id: 'settings', label: 'Paramètres', icon: Settings },
]

type Props = {
  page: Page
  collapsed: boolean
  mobileOpen: boolean
  onSelect: (page: Page) => void
  onCollapse: () => void
  onMobileClose: () => void
}

export function Sidebar({ page, collapsed, mobileOpen, onSelect, onCollapse, onMobileClose }: Props) {
  return (
    <>
      {mobileOpen && <button className="sidebar-backdrop" aria-label="Fermer le menu" onClick={onMobileClose} />}
      <aside className={`sidebar ${collapsed ? 'sidebar-collapsed' : ''} ${mobileOpen ? 'sidebar-open' : ''}`}>
        <div className="sidebar-head">
          <Logo compact={collapsed} />
          <button className="icon-button mobile-sidebar-close" onClick={onMobileClose} aria-label="Fermer le menu">
            <X size={20} />
          </button>
        </div>
        <nav aria-label="Navigation principale">
          {items.map((item) => {
            const Icon = item.icon
            return (
              <button
                key={item.id}
                className={`nav-item ${page === item.id ? 'nav-item-active' : ''}`}
                aria-current={page === item.id ? 'page' : undefined}
                title={collapsed ? item.label : undefined}
                onClick={() => {
                  onSelect(item.id)
                  onMobileClose()
                }}
              >
                <Icon size={21} strokeWidth={1.75} />
                {!collapsed && <span>{item.label}</span>}
              </button>
            )
          })}
        </nav>
        <button className="collapse-button" onClick={onCollapse}>
          {collapsed ? <Menu size={19} /> : <ChevronLeft size={19} />}
          {!collapsed && <span>Réduire le menu</span>}
        </button>
      </aside>
    </>
  )
}

export function MobileMenuButton({ onClick }: { onClick: () => void }) {
  return (
    <button className="icon-button mobile-menu" onClick={onClick} aria-label="Ouvrir le menu">
      <Menu size={21} />
    </button>
  )
}
