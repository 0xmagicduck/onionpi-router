import type { RackEgress, RackNodeRules, RackNodeStatus } from '../types'

export const STATUS: Record<
  RackNodeStatus,
  { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' }
> = {
  online: { label: 'En ligne', tone: 'success' },
  offline: { label: 'Injoignable', tone: 'danger' },
  isolated: { label: 'Isolé', tone: 'warning' },
  pending: { label: 'En attente', tone: 'neutral' },
  unknown: { label: 'Jamais vu', tone: 'neutral' },
}

export const EGRESS_LABELS: Record<RackEgress, string> = {
  'tor-only': 'Tor uniquement',
  direct: 'Sortie directe',
}

/** La feuille par défaut : elle ne bloque rien et laisse le 22 joignable.
 *  Le maillage y est éteint : y entrer est un choix, jamais un défaut. */
export const DEFAULT_RULES: RackNodeRules = {
  access: 'allowed',
  egress: 'tor-only',
  exit_country: '',
  keep_open_ports: [22],
  schedule: null,
  mesh: { enabled: false, ports: [], forwards: [] },
}
