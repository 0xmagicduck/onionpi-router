import { ChevronLeft, Menu, PanelLeft, X } from 'lucide-react'
import { NAV_GROUPS, type Page } from '../navigation'
import { Logo } from './Logo'

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
      <aside
        className={`sidebar ${collapsed ? 'sidebar-collapsed' : ''} ${mobileOpen ? 'sidebar-open' : ''}`}
        aria-label="Navigation principale"
      >
        <div className="sidebar-head">
          <Logo compact={collapsed} />
          <button className="icon-button mobile-sidebar-close" onClick={onMobileClose} aria-label="Fermer le menu">
            <X size={20} />
          </button>
        </div>
        <div className="sidebar-scroll">
          {NAV_GROUPS.map((group) => (
            <nav className="nav-group" key={group.label} aria-label={group.label}>
              {/* Collapsed, a truncated label reads as noise: a rule keeps the
                  grouping visible while the nav element keeps its name. */}
              {collapsed
                ? <span className="nav-group-rule" aria-hidden="true" />
                : <p className="nav-group-label">{group.label}</p>}
              {group.items.map((item) => {
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
                    <Icon size={19} strokeWidth={1.9} />
                    {!collapsed && <span>{item.label}</span>}
                  </button>
                )
              })}
            </nav>
          ))}
        </div>
        <div className="sidebar-foot">
          <button
            className="collapse-button"
            onClick={onCollapse}
            aria-expanded={!collapsed}
            title={collapsed ? 'Déplier le menu' : 'Réduire le menu'}
          >
            {collapsed ? <PanelLeft size={17} /> : <ChevronLeft size={17} />}
            {!collapsed && <span>Réduire le menu</span>}
          </button>
        </div>
      </aside>
    </>
  )
}

export function MobileMenuButton({ onClick }: { onClick: () => void }) {
  return (
    <button className="icon-button mobile-menu" onClick={onClick} aria-label="Ouvrir le menu">
      <Menu size={20} />
    </button>
  )
}
