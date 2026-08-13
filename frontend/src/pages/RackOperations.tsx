import type { ReactNode } from 'react'
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Clock3,
  KeyRound,
  Laptop,
  Network,
  Radio,
  RefreshCw,
  Route,
  Server,
  ServerCog,
  ShieldCheck,
} from 'lucide-react'
import { Badge } from '../components/ui'
import { relativeTime } from '../lib'
import type { RackNode } from '../types'
import { EGRESS_LABELS, STATUS } from './rackShared'

export type FabricView = {
  remote: RackNode[]
  enrolled: number
  authenticated: number
  torOnly: number
  synchronized: number
  torReady: number
  online: number
  lastProbe: number
  coverage: number
  syncRate: number
  status: 'empty' | 'degraded' | 'healthy'
}

export function summarizeFabric(nodes: RackNode[]): FabricView {
  const remote = nodes.filter((node) => node.kind === 'remote')
  const enrolled = remote.filter((node) => Boolean(node.address))
  const authenticated = enrolled.filter((node) => node.client_auth)
  const torOnly = remote.filter((node) => node.rules.egress === 'tor-only')
  const synchronized = remote.filter(
    (node) => Boolean(node.policy_digest) && node.state.policy?.digest === node.policy_digest,
  )
  const torReady = enrolled.filter((node) => node.state.tor?.connected)
  const online = enrolled.filter((node) => node.status === 'online')
  const lastProbe = Math.max(0, ...remote.map((node) => node.last_seen))
  const denominator = Math.max(remote.length, 1)
  const degraded = remote.some(
    (node) =>
      node.status === 'offline' ||
      node.status === 'pending' ||
      node.status === 'unknown' ||
      node.rules.egress === 'direct' ||
      (Boolean(node.address) && !node.client_auth) ||
      node.alerts.some((alert) => alert.level !== 'info'),
  )
  return {
    remote,
    enrolled: enrolled.length,
    authenticated: authenticated.length,
    torOnly: torOnly.length,
    synchronized: synchronized.length,
    torReady: torReady.length,
    online: online.length,
    lastProbe,
    coverage: Math.round((torOnly.length / denominator) * 100),
    syncRate: Math.round((synchronized.length / denominator) * 100),
    status: remote.length === 0 ? 'empty' : degraded ? 'degraded' : 'healthy',
  }
}

export function FabricPanel({
  fabric,
  onOpenNode,
}: {
  fabric: FabricView
  onOpenNode: (id: string) => void
}) {
  const state =
    fabric.status === 'healthy'
      ? { label: 'Opérationnel', tone: 'success' as const }
      : fabric.status === 'degraded'
        ? { label: 'Dégradé', tone: 'warning' as const }
        : { label: 'À configurer', tone: 'neutral' as const }

  return (
    <section className="rack-fabric" aria-labelledby="rack-fabric-title">
      <header>
        <span>
          <Network size={17} />
          <strong id="rack-fabric-title">Fabric Tor</strong>
        </span>
        <Badge tone={state.tone} dot>{state.label}</Badge>
      </header>

      <div className="rack-fabric-path" aria-label="Chemin de contrôle privé">
        <FabricStep icon={<ShieldCheck />} label="OnionPi" />
        <i aria-hidden="true">→</i>
        <FabricStep icon={<Radio />} label="Circuit Tor" />
        <i aria-hidden="true">→</i>
        <FabricStep icon={<Route />} label="Service onion" />
        <i aria-hidden="true">→</i>
        <FabricStep icon={<ServerCog />} label="Agent" />
      </div>

      <dl className="rack-fabric-metrics">
        <div><dt>Agents enrôlés</dt><dd>{fabric.enrolled} / {fabric.remote.length}</dd></div>
        <div><dt>Canaux en ligne</dt><dd>{fabric.online} / {fabric.enrolled}</dd></div>
        <div><dt>Sortie Tor forcée</dt><dd>{fabric.torOnly} / {fabric.remote.length}</dd></div>
        <div><dt>Dernière sonde</dt><dd>{fabric.lastProbe ? relativeTime(fabric.lastProbe) : 'Jamais'}</dd></div>
      </dl>

      <div className="rack-fabric-links">
        <div className="rack-fabric-links-head">
          <strong>Liens distants</strong>
          <small>{fabric.remote.length} nœud{fabric.remote.length > 1 ? 's' : ''}</small>
        </div>
        {fabric.remote.length ? (
          <ul>
            {fabric.remote.slice(0, 5).map((node) => (
              <li key={node.id}>
                <button type="button" onClick={() => onOpenNode(node.id)}>
                  <span className={`rack-fabric-link-state rack-fabric-link-${node.status}`} aria-hidden="true" />
                  <span>
                    <strong>{node.name}</strong>
                    <small>{node.address ? `${node.address.slice(0, 8)}….onion` : 'Enrôlement incomplet'}</small>
                  </span>
                  <em>{node.last_seen ? relativeTime(node.last_seen).replace('Il y a ', '') : '—'}</em>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="rack-fabric-empty">
            Ajoutez une machine distante pour établir un canal de contrôle à travers Tor.
          </p>
        )}
      </div>
    </section>
  )
}

function FabricStep({ icon, label }: { icon: ReactNode; label: string }) {
  return <span><b>{icon}</b><small>{label}</small></span>
}

export function DatacenterOperations({
  nodes,
  alerts,
  busy,
  onOpenNode,
  onRefreshNode,
}: {
  nodes: RackNode[]
  alerts: Array<{ node: RackNode; alert: RackNode['alerts'][number] }>
  busy: boolean
  onOpenNode: (id: string) => void
  onRefreshNode: (node: RackNode) => void
}) {
  return (
    <div className="rack-operations">
      <section className="rack-node-table-panel" aria-labelledby="rack-node-table-title">
        <header>
          <span>
            <strong id="rack-node-table-title">Nœuds et services</strong>
            <small>{nodes.length} résultat{nodes.length > 1 ? 's' : ''}</small>
          </span>
          <span className="rack-table-legend"><KeyRound size={13} /> canal onion privé</span>
        </header>
        {nodes.length ? (
          <div className="rack-table-scroll">
            <table className="rack-node-table">
              <thead>
                <tr><th>Nœud</th><th>Type</th><th>Accès</th><th>Sortie</th><th>Services</th><th>État</th><th><span className="sr-only">Actions</span></th></tr>
              </thead>
              <tbody>
                {nodes.map((node) => {
                  const activeServices = (node.state.services ?? []).filter((service) => service.active)
                  const status = STATUS[node.status]
                  const access = node.kind === 'remote'
                    ? node.client_auth
                      ? 'Onion privé'
                      : node.address
                        ? 'Onion non chiffré'
                        : 'À enrôler'
                    : node.link?.ip || 'Wi-Fi local'
                  return (
                    <tr key={node.id}>
                      <td>
                        <button className="rack-node-table-open" type="button" onClick={() => onOpenNode(node.id)}>
                          {node.kind === 'remote' ? <Server size={15} /> : <Laptop size={15} />}
                          <span><strong>{node.name}</strong><small>{node.role || 'Sans rôle'}</small></span>
                        </button>
                      </td>
                      <td>{node.kind === 'remote' ? 'Distant' : 'Local'}</td>
                      <td><span className={node.kind === 'remote' && !node.client_auth ? 'rack-cell-warning' : ''}>{access}</span></td>
                      <td>
                        <span className={node.rules.egress === 'direct' ? 'rack-cell-danger' : ''}>
                          {node.kind === 'local' ? 'Via OnionPi' : EGRESS_LABELS[node.rules.egress]}
                        </span>
                      </td>
                      <td>
                        {node.kind === 'local'
                          ? 'TCP · DNS'
                          : activeServices.length
                            ? activeServices.map((service) => service.label).join(' · ')
                            : node.address
                              ? 'Agent sans télémétrie'
                              : 'En attente'}
                      </td>
                      <td><Badge tone={status.tone} dot>{status.label}</Badge></td>
                      <td>
                        <button
                          className="icon-button"
                          type="button"
                          aria-label={`Interroger ${node.name}`}
                          disabled={busy || node.kind === 'local' || !node.address}
                          onClick={() => onRefreshNode(node)}
                        >
                          <RefreshCw size={14} />
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="rack-table-empty">Aucun nœud ne correspond aux filtres actifs.</div>
        )}
      </section>

      <section className="rack-attention" aria-labelledby="rack-attention-title">
        <header>
          <span><AlertTriangle size={16} /><strong id="rack-attention-title">À traiter</strong></span>
          <small>{alerts.length}</small>
        </header>
        {alerts.length ? (
          <ul>
            {alerts.slice(0, 5).map(({ node, alert }, index) => (
              <li key={`${node.id}-${index}`}>
                <button type="button" className={`rack-attention-${alert.level}`} onClick={() => onOpenNode(node.id)}>
                  {alert.level === 'danger'
                    ? <Ban size={16} />
                    : alert.level === 'warning'
                      ? <AlertTriangle size={16} />
                      : <Clock3 size={16} />}
                  <span><strong>{node.name}</strong><small>{alert.message}</small></span>
                  <em>Ouvrir</em>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <div className="rack-attention-clear">
            <CheckCircle2 size={22} />
            <strong>Aucune action requise</strong>
            <span>Les nœuds connus ne signalent aucun écart.</span>
          </div>
        )}
      </section>
    </div>
  )
}
