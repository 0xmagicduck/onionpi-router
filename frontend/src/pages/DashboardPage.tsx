import { AlertTriangle } from 'lucide-react'
import { ActivityPanel } from '../components/ActivityPanel'
import { DeviceTable } from '../components/DeviceTable'
import { MetricBar, Panel } from '../components/Panel'
import { TorStatus } from '../components/TorStatus'
import { TrafficChart } from '../components/TrafficChart'
import type { Device, StatusPayload, TrafficSample, User } from '../types'

type Props = {
  user: User
  status?: StatusPayload
  devices: Device[]
  traffic: TrafficSample[]
  onNewIdentity: () => Promise<void>
  identityBusy: boolean
}

export function DashboardPage({ user, status, devices, traffic, onNewIdentity, identityBusy }: Props) {
  if (!status) return <DashboardSkeleton />
  return (
    <div className="page dashboard-page">
      <div className="dashboard-grid">
        <main className="dashboard-main">
          <div className="page-title">
            <h1>Bonjour, {user.display_name.split(' ')[0]}</h1>
            <p>{status.tor.connected ? 'Votre réseau est protégé par Tor.' : 'Tor se connecte. Le pare-feu bloque toute sortie directe.'}</p>
          </div>
          <TorStatus status={status} onNewIdentity={onNewIdentity} busy={identityBusy} />
          <TrafficChart samples={traffic} />
          <DeviceTable devices={devices} limit={5} />
        </main>
        <aside className="dashboard-rail">
          <Panel title="Système" className="system-panel">
            <MetricBar label="CPU" value={status.system.cpu_percent} />
            <MetricBar label="Mémoire" value={status.system.memory_percent} />
            <MetricBar label="Température" value={status.system.temperature_c ?? 0} suffix=" °C" />
            <MetricBar label="Stockage" value={status.system.storage_percent} />
          </Panel>
          <Panel title="Services" className="services-panel">
            {status.system.services.map((service) => (
              <div className="service-row" key={service.id}>
                <span>{service.label}</span>
                <strong className={service.active ? 'service-active' : 'service-down'}><i />{service.active ? 'Actif' : 'Arrêté'}</strong>
              </div>
            ))}
          </Panel>
          <ActivityPanel activities={status.activities} />
        </aside>
      </div>
    </div>
  )
}

function DashboardSkeleton() {
  return (
    <div className="page dashboard-page loading-page">
      <div className="dashboard-grid"><main className="dashboard-main">
        <div className="page-title"><span className="skeleton skeleton-title" /><span className="skeleton skeleton-line" /></div>
        <div className="panel loading-panel"><div className="loading-message"><AlertTriangle size={18} /> Lecture de l’état du routeur…</div></div>
      </main></div>
    </div>
  )
}
