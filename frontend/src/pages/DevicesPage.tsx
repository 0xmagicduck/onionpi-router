import { FormEvent, useState } from 'react'
import {
  Ban,
  CalendarClock,
  Laptop,
  Pause,
  Play,
  Search,
  ShieldCheck,
  ShieldOff,
  Smartphone,
  SlidersHorizontal,
  Undo2,
  Wifi,
} from 'lucide-react'
import { api } from '../api'
import { Modal } from '../components/Modal'
import { Panel } from '../components/Panel'
import { Badge, EmptyState } from '../components/ui'
import { relativeTime } from '../lib'
import type { AccessState, Device, DeviceAccessState, DeviceSchedule } from '../types'

const STATE_LABELS: Record<AccessState, { label: string; tone: 'success' | 'warning' | 'danger' }> = {
  allowed: { label: 'Tor', tone: 'success' },
  paused: { label: 'En pause', tone: 'warning' },
  outside: { label: 'Hors plage', tone: 'warning' },
  blocked: { label: 'Bloqué', tone: 'danger' },
}

const DEFAULT_SCHEDULE: DeviceSchedule = {
  enabled: true,
  days: [0, 1, 2, 3, 4],
  start: '07:00',
  end: '21:00',
}

type Props = {
  devices: Device[]
  access?: DeviceAccessState
  notify: (message: string, error?: boolean) => void
  onRefresh: () => void
}

function scheduleSummary(schedule: DeviceSchedule | null | undefined, weekdays: string[]): string {
  if (!schedule?.enabled) return 'Aucune plage horaire'
  const days = schedule.days.map((day) => weekdays[day]?.slice(0, 3) ?? '?').join(', ')
  return `${schedule.start} – ${schedule.end} · ${days}`
}

export function DevicesPage({ devices, access, notify, onRefresh }: Props) {
  const [query, setQuery] = useState('')
  const [busyMac, setBusyMac] = useState('')
  const [editing, setEditing] = useState<Device>()

  const weekdays = access?.weekdays ?? []
  const pauseChoices = access?.pause_choices ?? [15, 60]
  const needle = query.trim().toLocaleLowerCase('fr')
  const rows = needle
    ? devices.filter((device) =>
        `${device.name} ${device.ip} ${device.mac}`.toLocaleLowerCase('fr').includes(needle),
      )
    : devices

  const run = async (mac: string, action: () => Promise<unknown>, message: string) => {
    setBusyMac(mac)
    try {
      await action()
      notify(message)
      onRefresh()
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Action refusée', true)
    } finally {
      setBusyMac('')
    }
  }

  return (
    <div className="page operational-page">
      <div className="page-title">
        <h1>Appareils</h1>
        <p>
          Chaque client du Wi-Fi peut être renommé, suspendu quelques minutes ou limité à une plage
          horaire. Les règles sont appliquées par le pare-feu, pas seulement affichées ici.
        </p>
      </div>

      <Panel
        title={`Clients du point d’accès (${devices.length})`}
        className="device-panel"
        action={
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
        }
      >
        <div className="data-table access-table" role="table">
          <div className="table-row table-head" role="row">
            <span role="columnheader">Appareil</span>
            <span role="columnheader">Adresse IP</span>
            <span role="columnheader">Accès</span>
            <span role="columnheader">Plage horaire</span>
            <span role="columnheader"><span className="sr-only">Actions</span></span>
          </div>
          {rows.map((device, index) => {
            const state = STATE_LABELS[device.access_state ?? (device.blocked ? 'blocked' : 'allowed')]
            const paused = (device.paused_until ?? 0) > Date.now() / 1000
            return (
              <div
                className={`table-row ${device.blocked ? 'row-blocked' : ''}`}
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
                <span role="cell">
                  <Badge tone={state.tone}>
                    {device.blocked ? <ShieldOff size={13} /> : paused ? <Pause size={13} /> : <ShieldCheck size={13} />}
                    {state.label}
                  </Badge>
                  {paused && (
                    <small className="muted"> jusqu’à {new Date((device.paused_until ?? 0) * 1000).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}</small>
                  )}
                </span>
                <span className="muted" role="cell">{scheduleSummary(device.schedule, weekdays)}</span>
                <span className="access-actions" role="cell">
                  {paused ? (
                    <button
                      className="button button-small button-secondary"
                      disabled={busyMac === device.mac}
                      onClick={() => void run(device.mac, () => api.pauseDevice(device.mac, 0), `${device.name} rétabli`)}
                    >
                      <Play size={14} /> Reprendre
                    </button>
                  ) : (
                    pauseChoices.slice(0, 2).map((minutes) => (
                      <button
                        key={minutes}
                        className="button button-small button-secondary"
                        disabled={busyMac === device.mac || !device.mac}
                        onClick={() => void run(device.mac, () => api.pauseDevice(device.mac, minutes), `${device.name} suspendu ${minutes} min`)}
                      >
                        <Pause size={14} /> {minutes} min
                      </button>
                    ))
                  )}
                  <button
                    className="button button-small button-ghost"
                    disabled={!device.mac}
                    onClick={() => setEditing(device)}
                  >
                    <SlidersHorizontal size={14} /> Régler
                  </button>
                  <button
                    className={`button button-small ${device.blocked ? 'button-secondary' : 'button-danger'}`}
                    disabled={busyMac === device.mac || !device.mac}
                    onClick={() =>
                      void run(
                        device.mac,
                        () => api.setDeviceBlocked(device.mac, !device.blocked, device.name),
                        device.blocked ? `${device.name} débloqué` : `${device.name} bloqué`,
                      )
                    }
                  >
                    {device.blocked ? <><Undo2 size={14} /> Débloquer</> : <><Ban size={14} /> Bloquer</>}
                  </button>
                </span>
              </div>
            )
          })}
          {!rows.length && (
            <EmptyState icon={Wifi} title={query ? 'Aucun appareil ne correspond' : 'Aucun appareil détecté'}>
              {query
                ? 'Essayez un autre nom, une autre adresse IP ou une autre adresse MAC.'
                : 'Connectez un téléphone ou un ordinateur au Wi-Fi OnionPi : il apparaîtra ici en moins d’une minute.'}
            </EmptyState>
          )}
        </div>
      </Panel>

      {access && access.rules.length > 0 && (
        <Panel title={`Règles enregistrées (${access.rules.length})`}>
          <ul className="rule-list">
            {access.rules.map((rule) => (
              <li key={rule.mac}>
                <div>
                  <strong>{rule.alias || rule.mac}</strong>
                  <span className="muted">{scheduleSummary(rule.schedule, weekdays)}</span>
                </div>
                <div>
                  {rule.paused_until > 0 && (
                    <span className="muted">Pause jusqu’à {relativeTime(rule.paused_until)}</span>
                  )}
                  <button
                    className="button button-small button-ghost"
                    disabled={busyMac === rule.mac}
                    onClick={() => void run(rule.mac, () => api.removeDeviceAccess(rule.mac), 'Règle supprimée')}
                  >
                    Supprimer
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {editing && (
        <AccessEditor
          device={editing}
          weekdays={weekdays}
          onClose={() => setEditing(undefined)}
          onSaved={() => {
            setEditing(undefined)
            onRefresh()
          }}
          notify={notify}
        />
      )}
    </div>
  )
}

function AccessEditor({
  device,
  weekdays,
  onClose,
  onSaved,
  notify,
}: {
  device: Device
  weekdays: string[]
  onClose: () => void
  onSaved: () => void
  notify: (message: string, error?: boolean) => void
}) {
  const [alias, setAlias] = useState(device.alias || '')
  const [schedule, setSchedule] = useState<DeviceSchedule>(device.schedule ?? { ...DEFAULT_SCHEDULE, enabled: false })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const toggleDay = (day: number) =>
    setSchedule((current) => ({
      ...current,
      days: current.days.includes(day)
        ? current.days.filter((item) => item !== day)
        : [...current.days, day].sort((left, right) => left - right),
    }))

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api.setDeviceAccess({
        mac: device.mac,
        alias,
        schedule: schedule.enabled ? schedule : null,
      })
      notify('Règle d’accès enregistrée')
      onSaved()
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Enregistrement impossible'
      setError(message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={`Accès de ${device.name}`}
      description={device.mac}
      icon={<CalendarClock size={22} />}
      onClose={onClose}
      onSubmit={submit}
      actions={
        <>
          <button type="button" className="button button-secondary" onClick={onClose}>Annuler</button>
          <button className="button button-primary" disabled={busy}>
            {busy ? 'Enregistrement…' : 'Enregistrer'}
          </button>
        </>
      }
    >
      <div className="settings-form">
        <label>
          Nom affiché
          <input
            value={alias}
            maxLength={64}
            placeholder={device.name}
            onChange={(event) => setAlias(event.target.value)}
          />
        </label>
        <label className="choice-card">
          <input
            type="checkbox"
            checked={schedule.enabled}
            onChange={(event) => setSchedule({ ...schedule, enabled: event.target.checked })}
          />
          <div>
            <strong>Limiter l’accès à une plage horaire</strong>
            <p>En dehors de la plage, l’appareil est coupé par le pare-feu comme s’il était bloqué.</p>
          </div>
        </label>
        {schedule.enabled && (
          <>
            <div className="two-column">
              <label>
                Début
                <input type="time" value={schedule.start} onChange={(event) => setSchedule({ ...schedule, start: event.target.value })} required />
              </label>
              <label>
                Fin
                <input type="time" value={schedule.end} onChange={(event) => setSchedule({ ...schedule, end: event.target.value })} required />
              </label>
            </div>
            <fieldset className="weekday-picker">
              <legend>Jours concernés</legend>
              {weekdays.map((label, day) => (
                <label key={label} className={schedule.days.includes(day) ? 'weekday-active' : ''}>
                  <input
                    type="checkbox"
                    checked={schedule.days.includes(day)}
                    onChange={() => toggleDay(day)}
                  />
                  {label.slice(0, 3)}
                </label>
              ))}
            </fieldset>
            <p className="prose">
              Une plage qui se termine avant son début, par exemple 22:00 – 06:00, appartient à la
              nuit du jour choisi.
            </p>
          </>
        )}
        {error && <div className="form-error" role="alert">{error}</div>}
      </div>
    </Modal>
  )
}
