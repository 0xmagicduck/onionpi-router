import { Check, Copy, RefreshCw, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { formatDuration } from '../lib'
import type { StatusPayload } from '../types'
import { Panel } from './Panel'

type Props = {
  status: StatusPayload
  onNewIdentity: () => Promise<void>
  busy: boolean
}

const RADIUS = 30
const CIRCUMFERENCE = 2 * Math.PI * RADIUS

const PLACEHOLDER = [
  { role: 'Entrée', name: 'En attente' },
  { role: 'Relais', name: 'En attente' },
  { role: 'Sortie', name: 'En attente' },
]

export function TorStatus({ status, onNewIdentity, busy }: Props) {
  const [copied, setCopied] = useState(false)
  const nodes = status.tor.circuit.length ? status.tor.circuit : PLACEHOLDER
  const progress = status.tor.connected ? 100 : Math.max(0, Math.min(100, status.tor.bootstrap))

  const copyExitIp = async () => {
    if (!status.tor.exit_ip) return
    try {
      await navigator.clipboard?.writeText(status.tor.exit_ip)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      // Clipboard access is denied outside a secure context; the address stays
      // selectable on screen.
    }
  }

  return (
    <Panel
      title="État Tor"
      className="tor-panel"
      action={
        <button
          className="button button-outline"
          onClick={() => void onNewIdentity()}
          disabled={busy || !status.tor.connected}
        >
          <RefreshCw size={15} className={busy ? 'spin' : undefined} />
          {busy ? 'En cours…' : 'Nouvelle identité'}
        </button>
      }
    >
      <div className="tor-content">
        <div className={`tor-state ${status.tor.connected ? 'tor-state-connected' : ''}`}>
          <span
            className="tor-gauge"
            role="img"
            aria-label={`Démarrage de Tor à ${Math.round(progress)} %`}
          >
            <svg viewBox="0 0 68 68" aria-hidden="true">
              <circle className="gauge-track" cx="34" cy="34" r={RADIUS} />
              <circle
                className="gauge-value"
                cx="34"
                cy="34"
                r={RADIUS}
                strokeDasharray={CIRCUMFERENCE}
                strokeDashoffset={CIRCUMFERENCE * (1 - progress / 100)}
              />
            </svg>
            <ShieldCheck aria-hidden="true" />
          </span>
          <div>
            <strong>{status.tor.connected ? 'Connecté' : `${Math.round(progress)} %`}</strong>
            <p>{status.tor.summary}</p>
          </div>
        </div>
        <div className="circuit-block">
          <p className="circuit-label">Circuit Tor · 3 sauts</p>
          <ol className="circuit-line">
            {nodes.slice(0, 3).map((node, index) => (
              <li className="circuit-node" key={`${node.role}-${index}`}>
                <i aria-hidden="true" />
                <small>{node.role}</small>
                <strong title={node.name}>{node.name}</strong>
              </li>
            ))}
          </ol>
          <div className="circuit-details">
            <div>
              <span>Durée de connexion</span>
              <strong className="mono tabular">{formatDuration(status.system.uptime_seconds)}</strong>
            </div>
            <div>
              <span>IP de sortie actuelle</span>
              <strong className="mono">{status.tor.exit_ip ?? 'Indisponible'}</strong>
            </div>
            {status.tor.exit_ip && (
              <button
                className="icon-button"
                onClick={() => void copyExitIp()}
                aria-label={copied ? 'Adresse copiée' : 'Copier l’adresse IP de sortie'}
              >
                {copied ? <Check size={15} /> : <Copy size={15} />}
              </button>
            )}
          </div>
        </div>
      </div>
    </Panel>
  )
}
