import { Copy, RefreshCw, ShieldCheck } from 'lucide-react'
import type { StatusPayload } from '../types'
import { formatDuration } from '../lib'
import { Panel } from './Panel'

type Props = {
  status: StatusPayload
  onNewIdentity: () => Promise<void>
  busy: boolean
}

export function TorStatus({ status, onNewIdentity, busy }: Props) {
  const nodes = status.tor.circuit.length
    ? status.tor.circuit
    : [
        { role: 'Entrée', name: 'En attente' },
        { role: 'Relais', name: 'En attente' },
        { role: 'Sortie', name: 'En attente' },
      ]
  return (
    <Panel
      title="État Tor"
      className="tor-panel"
      action={
        <button className="button button-outline" onClick={() => void onNewIdentity()} disabled={busy || !status.tor.connected}>
          <RefreshCw size={16} className={busy ? 'spin' : ''} />
          Nouvelle identité
        </button>
      }
    >
      <div className="tor-content">
        <div className={`tor-state ${status.tor.connected ? 'connected' : ''}`}>
          <span className="tor-shield"><ShieldCheck size={30} /></span>
          <div>
            <strong>{status.tor.connected ? 'Connecté' : `${status.tor.bootstrap}%`}</strong>
            <p>{status.tor.summary}</p>
          </div>
        </div>
        <div className="circuit-block">
          <p className="circuit-label">Circuit Tor (3 sauts)</p>
          <div className="circuit-line">
            {nodes.slice(0, 3).map((node, index) => (
              <div className="circuit-node" key={`${node.role}-${index}`}>
                <span />
                <small>{node.role}</small>
                <strong>{node.name}</strong>
              </div>
            ))}
          </div>
          <div className="circuit-details">
            <div><span>Durée de connexion</span><strong className="mono">{formatDuration(status.system.uptime_seconds)}</strong></div>
            <div><span>IP de sortie actuelle</span><strong className="mono">{status.tor.exit_ip ?? 'Indisponible'}</strong></div>
            {status.tor.exit_ip && (
              <button className="icon-button" onClick={() => void navigator.clipboard.writeText(status.tor.exit_ip!)} aria-label="Copier l’adresse IP">
                <Copy size={15} />
              </button>
            )}
          </div>
        </div>
      </div>
    </Panel>
  )
}
