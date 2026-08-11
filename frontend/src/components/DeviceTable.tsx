import { Ban, Laptop, ShieldCheck, ShieldOff, Smartphone, Undo2 } from 'lucide-react'
import { formatBytes } from '../lib'
import type { Device } from '../types'
import { Panel } from './Panel'

type Props = {
  devices: Device[]
  limit?: number
  /** Given, the table gains a block/unblock control on every row. */
  onToggleBlock?: (device: Device) => void
  busyMac?: string
}

export function DeviceTable({ devices, limit, onToggleBlock, busyMac }: Props) {
  const shown = typeof limit === 'number' ? devices.slice(0, limit) : devices
  return (
    <Panel title={`Appareils connectés (${devices.length})`} className="device-panel">
      <div className={`data-table device-table ${onToggleBlock ? 'device-table-actions' : ''}`}>
        <div className="table-row table-head">
          <span>Appareil</span><span>Adresse IP</span><span>Trafic</span><span>Protégé par Tor</span><span />
        </div>
        {shown.map((device, index) => (
          <div className={`table-row ${device.blocked ? 'row-blocked' : ''}`} key={device.mac || `${device.ip}-${index}`}>
            <span className="device-name">
              {device.name.toLowerCase().includes('phone') ? <Smartphone size={19} /> : <Laptop size={19} />}
              <strong>{device.name}</strong>
              <em className="mono device-mac">{device.mac}</em>
            </span>
            <span className="mono">{device.ip}</span>
            <span className="traffic-values"><em>↓ {formatBytes(device.download)}</em><em>↑ {formatBytes(device.upload)}</em></span>
            {device.blocked
              ? <span className="unhealthy"><ShieldOff size={18} /> Bloqué</span>
              : <span className="protected"><ShieldCheck size={18} /> Oui</span>}
            {onToggleBlock ? (
              <button
                className={`button button-small ${device.blocked ? '' : 'button-danger'}`}
                disabled={busyMac === device.mac}
                onClick={() => onToggleBlock(device)}
              >
                {device.blocked ? <><Undo2 size={15} /> Débloquer</> : <><Ban size={15} /> Bloquer</>}
              </button>
            ) : <span />}
          </div>
        ))}
        {!shown.length && <div className="empty-row">Aucun appareil détecté pour le moment.</div>}
      </div>
    </Panel>
  )
}
