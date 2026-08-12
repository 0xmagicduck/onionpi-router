import { useMemo, useState } from 'react'
import {
  ArrowDown,
  ArrowUp,
  Ban,
  ChevronsUpDown,
  Laptop,
  Search,
  ShieldCheck,
  ShieldOff,
  Smartphone,
  Undo2,
  Wifi,
} from 'lucide-react'
import { formatBytes } from '../lib'
import type { Device } from '../types'
import { Panel } from './Panel'
import { Badge, EmptyState } from './ui'

type SortKey = 'name' | 'ip' | 'traffic'
type Props = {
  devices: Device[]
  limit?: number
  /** Given, the table gains a block/unblock control on every row. */
  onToggleBlock?: (device: Device) => void
  busyMac?: string
  /** Adds the search field; pointless on the dashboard extract. */
  searchable?: boolean
}

/** Sorts an IP so 10.42.0.9 comes before 10.42.0.10. */
function ipOrder(ip: string): number {
  return ip.split('.').reduce((total, part) => total * 256 + (Number(part) || 0), 0)
}

export function DeviceTable({ devices, limit, onToggleBlock, busyMac, searchable = false }: Props) {
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: 'traffic', desc: true })

  const rows = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase('fr')
    const filtered = needle
      ? devices.filter((device) =>
          `${device.name} ${device.ip} ${device.mac}`.toLocaleLowerCase('fr').includes(needle),
        )
      : devices
    const sorted = [...filtered].sort((left, right) => {
      const direction = sort.desc ? -1 : 1
      if (sort.key === 'name') return direction * left.name.localeCompare(right.name, 'fr')
      if (sort.key === 'ip') return direction * (ipOrder(left.ip) - ipOrder(right.ip))
      return direction * (left.download + left.upload - (right.download + right.upload))
    })
    return typeof limit === 'number' ? sorted.slice(0, limit) : sorted
  }, [devices, query, sort, limit])

  const toggleSort = (key: SortKey) =>
    setSort((current) => (current.key === key ? { key, desc: !current.desc } : { key, desc: key !== 'name' }))

  const header = (key: SortKey, label: string) => {
    const state = sort.key === key ? (sort.desc ? 'descending' : 'ascending') : 'none'
    const Icon = sort.key !== key ? ChevronsUpDown : sort.desc ? ArrowDown : ArrowUp
    return (
      <button className="column-sort" aria-sort={state} onClick={() => toggleSort(key)}>
        {label}
        <Icon size={12} />
      </button>
    )
  }

  const blocked = devices.filter((device) => device.blocked).length
  return (
    <Panel
      title={`Appareils connectés (${devices.length})`}
      subtitle={blocked ? `${blocked} bloqué${blocked > 1 ? 's' : ''} par le pare-feu` : undefined}
      className="device-panel"
      action={
        searchable ? (
          <label className="search-field">
            <Search size={16} />
            <span className="sr-only">Rechercher un appareil</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Nom, IP ou adresse MAC"
            />
          </label>
        ) : undefined
      }
    >
      <div className={`data-table device-table ${onToggleBlock ? 'device-table-actions' : ''}`} role="table">
        <div className="table-row table-head" role="row">
          <span role="columnheader">{header('name', 'Appareil')}</span>
          <span role="columnheader">{header('ip', 'Adresse IP')}</span>
          <span role="columnheader">{header('traffic', 'Trafic')}</span>
          <span role="columnheader">Protection</span>
          {onToggleBlock && <span role="columnheader"><span className="sr-only">Actions</span></span>}
        </div>
        {rows.map((device, index) => (
          <div
            className={`table-row table-row-interactive ${device.blocked ? 'row-blocked' : ''}`}
            role="row"
            key={device.mac || `${device.ip}-${index}`}
          >
            <span className="device-name" role="cell">
              {device.name.toLowerCase().includes('phone') ? <Smartphone size={19} /> : <Laptop size={19} />}
              <div>
                <strong title={device.name}>{device.name}</strong>
                <em className="mono device-mac">{device.mac || 'MAC inconnue'}</em>
              </div>
            </span>
            <span className="mono tabular" role="cell">{device.ip}</span>
            <span className="traffic-values" role="cell">
              <em className="traffic-down">↓ {formatBytes(device.download)}</em>
              <em className="traffic-up">↑ {formatBytes(device.upload)}</em>
            </span>
            <span role="cell">
              {device.blocked
                ? <Badge tone="danger"><ShieldOff size={13} /> Bloqué</Badge>
                : <Badge tone="success"><ShieldCheck size={13} /> Tor</Badge>}
            </span>
            {onToggleBlock && (
              <span role="cell">
                <button
                  className={`button button-small ${device.blocked ? 'button-secondary' : 'button-danger'}`}
                  disabled={busyMac === device.mac || !device.mac}
                  onClick={() => onToggleBlock(device)}
                >
                  {device.blocked ? <><Undo2 size={14} /> Débloquer</> : <><Ban size={14} /> Bloquer</>}
                </button>
              </span>
            )}
          </div>
        ))}
        {!rows.length && (
          <EmptyState icon={Wifi} title={query ? 'Aucun appareil ne correspond' : 'Aucun appareil détecté'}>
            {query
              ? 'Essayez un autre nom, une autre adresse IP ou une autre adresse MAC.'
              : 'Connectez un téléphone ou un ordinateur au Wi-Fi OnionPi : il apparaîtra ici en moins d’une minute.'}
          </EmptyState>
        )}
      </div>
    </Panel>
  )
}
