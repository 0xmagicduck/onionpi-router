import { ArrowRight, Clock, Gauge, ShieldCheck, Smartphone } from 'lucide-react'
import { ActivityPanel } from '../components/ActivityPanel'
import { DeviceTable } from '../components/DeviceTable'
import { MetricBar, Panel } from '../components/Panel'
import { TorStatus } from '../components/TorStatus'
import { TrafficChart } from '../components/TrafficChart'
import { Badge, LoadingPanel, Skeleton, StatCard } from '../components/ui'
import { formatUptime } from '../lib'
import type { Page } from '../navigation'
import type { Device, StatusPayload, TrafficSample, User } from '../types'

type Props = {
  user: User
  status?: StatusPayload
  devices: Device[]
  traffic: TrafficSample[]
  onNewIdentity: () => Promise<void>
  identityBusy: boolean
  onOpenPage: (page: Page) => void
}

export function DashboardPage({ user, status, devices, traffic, onNewIdentity, identityBusy, onOpenPage }: Props) {
  if (!status) return <DashboardSkeleton />

  const latest = traffic.at(-1)
  const blocked = devices.filter((device) => device.blocked).length
  const protectedRatio = Math.round(status.protection.metrics.protected_ratio * 100)
  const tone = status.protection.status === 'protected'
    ? 'success'
    : status.protection.status === 'degraded'
      ? 'danger'
      : 'warning'

  return (
    <div className="page dashboard-page">
      <div className="page-title greeting-title">
        <h1>
          Bonjour, {user.display_name.split(' ')[0]}
          <Badge tone={tone} dot>{status.protection.label}</Badge>
        </h1>
        <p>{status.protection.summary}</p>
      </div>

      <div className="stat-grid" style={{ marginBottom: 'var(--space-5)' }}>
        <StatCard
          icon={Smartphone}
          label="Appareils"
          value={devices.length}
          foot={blocked ? `${blocked} bloqué${blocked > 1 ? 's' : ''} par le pare-feu` : 'Tous protégés par Tor'}
        />
        <StatCard
          icon={Gauge}
          label="Débit actuel"
          value={(latest?.download_mbps ?? 0).toFixed(1)}
          unit="Mbps"
          foot={`${(latest?.upload_mbps ?? 0).toFixed(1)} Mbps en émission`}
        />
        <StatCard
          icon={ShieldCheck}
          label="Temps protégé"
          value={protectedRatio}
          unit="%"
          tone={protectedRatio >= 99 ? 'good' : protectedRatio >= 90 ? 'warn' : 'bad'}
          foot={`${status.protection.metrics.contained_entries} passage${status.protection.metrics.contained_entries > 1 ? 's' : ''} en confinement`}
        />
        <StatCard
          icon={Clock}
          label="Disponibilité"
          value={formatUptime(status.system.uptime_seconds)}
          foot={`Version ${status.version}`}
        />
      </div>

      <div className="dashboard-grid">
        <div className="dashboard-main">
          <TorStatus status={status} onNewIdentity={onNewIdentity} busy={identityBusy} />
          <TrafficChart samples={traffic} />
          <DeviceTable devices={devices} limit={5} />
        </div>
        <aside className="dashboard-rail">
          <Panel title="Système" subtitle={status.system.hostname} className="system-panel">
            <MetricBar label="Processeur" value={status.system.cpu_percent} />
            <MetricBar label="Mémoire" value={status.system.memory_percent} />
            <MetricBar label="Température" value={status.system.temperature_c ?? 0} suffix=" °C" thresholds={[70, 80]} />
            <MetricBar label="Stockage" value={status.system.storage_percent} />
          </Panel>
          <Panel
            title="Services"
            className="services-panel"
            action={
              <button className="button button-ghost button-small" onClick={() => onOpenPage('settings')}>
                Maintenance <ArrowRight size={14} />
              </button>
            }
          >
            {status.system.services.map((service) => (
              <div className="service-row" key={service.id}>
                <span>{service.label}</span>
                {service.active
                  ? <Badge tone="success" dot>Actif</Badge>
                  : <Badge tone="danger" dot>Arrêté</Badge>}
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
    <div className="page dashboard-page">
      <div className="page-title">
        <Skeleton className="skeleton-title" />
        <Skeleton className="skeleton-line" />
      </div>
      <div className="stat-grid" style={{ marginBottom: 'var(--space-5)' }}>
        {[0, 1, 2, 3].map((index) => <LoadingPanel key={index} height={104} label="Chargement des indicateurs" />)}
      </div>
      <div className="dashboard-grid">
        <div className="dashboard-main">
          <LoadingPanel height={228} label="Lecture de l’état Tor" />
          <LoadingPanel height={268} label="Lecture du trafic réseau" />
        </div>
        <aside className="dashboard-rail">
          <LoadingPanel height={228} label="Lecture des métriques système" />
        </aside>
      </div>
    </div>
  )
}
