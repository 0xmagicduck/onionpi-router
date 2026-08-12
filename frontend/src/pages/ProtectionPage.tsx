import { FormEvent, useEffect, useState } from 'react'
import { AlertTriangle, Download, Filter, RefreshCw, ShieldCheck, ShieldOff } from 'lucide-react'
import { api } from '../api'
import { DeviceTable } from '../components/DeviceTable'
import { Panel } from '../components/Panel'
import { relativeTime } from '../lib'
import type { Device, DeviceTraffic, DnsFilterState, StatusPayload } from '../types'

const STATUS_LABELS: Record<StatusPayload['protection']['status'], string> = {
  protected: 'Réseau protégé',
  contained: 'Confinement actif',
  degraded: 'Intervention requise',
  demo: 'Mode démonstration',
}

type Props = {
  status?: StatusPayload
  devices: Device[]
  traffic?: DeviceTraffic
  notify: (message: string, error?: boolean) => void
  onDevicesRefresh: () => void
}

export function ProtectionPage({ status, devices, traffic, notify, onDevicesRefresh }: Props) {
  const [busyMac, setBusyMac] = useState('')
  const [repairBusy, setRepairBusy] = useState(false)

  const resetTraffic = async () => {
    try {
      await api.resetDeviceTraffic()
      notify('Compteurs de trafic remis à zéro')
      onDevicesRefresh()
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Action refusée', true)
    }
  }

  const toggleBlock = async (device: Device) => {
    setBusyMac(device.mac)
    try {
      await api.setDeviceBlocked(device.mac, !device.blocked, device.name)
      notify(device.blocked ? `${device.name} débloqué` : `${device.name} bloqué`)
      onDevicesRefresh()
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Action refusée', true)
    } finally {
      setBusyMac('')
    }
  }

  const failed = status?.protection.checks.find((check) => !check.ok)
  const repairActions: Record<string, { action: string; label: string }> = {
    firewall: { action: 'restart-firewall', label: 'Relancer le pare-feu' },
    tor: { action: 'restart-tor', label: 'Réparer Tor' },
    dns: { action: 'restart-dnsmasq', label: 'Corriger le DNS' },
    'access-point': { action: 'restart-network', label: 'Relancer le Wi-Fi' },
  }
  const repair = failed ? repairActions[failed.id] : undefined

  const runRepair = async () => {
    if (!repair) return
    setRepairBusy(true)
    try {
      const result = await api.runSystemAction(repair.action)
      notify(result.message)
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Réparation refusée', true)
    } finally {
      setRepairBusy(false)
    }
  }

  const downloadReport = async () => {
    try {
      const report = await api.diagnostics()
      const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }))
      const link = document.createElement('a')
      link.href = url
      link.download = `onionpi-diagnostic-${report.generated_at}.json`
      link.click()
      URL.revokeObjectURL(url)
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Rapport indisponible', true)
    }
  }

  return (
    <div className="page operational-page">
      <div className="page-title">
        <h1>Protection</h1>
        <p>Un état unique, sa cause et l’action sûre à prendre immédiatement.</p>
      </div>
      {status && (
        <section className={`protection-hero protection-${status.protection.status}`}>
          {status.protection.safe ? <ShieldCheck /> : <AlertTriangle />}
          <div>
            <span className="section-label">{STATUS_LABELS[status.protection.status]}</span>
            <h2>{status.protection.label}</h2>
            <p>{status.protection.summary}</p>
            <small>Temps protégé : {Math.round(status.protection.metrics.protected_ratio * 100)} % · entrées en confinement : {status.protection.metrics.contained_entries}</small>
          </div>
          <div className="protection-actions">
            {repair && <button className="button button-primary" disabled={repairBusy} onClick={() => void runRepair()}><RefreshCw size={15} />{repairBusy ? 'Réparation…' : repair.label}</button>}
            <button className="button button-secondary" onClick={() => void downloadReport()}><Download size={15} /> Télécharger le rapport</button>
          </div>
        </section>
      )}
      {status && <ul className="protection-check-grid">{status.protection.checks.map((check) => <li className={check.ok ? 'check-ok' : 'check-failed'} key={check.id}>{check.ok ? <ShieldCheck /> : <AlertTriangle />}<div><strong>{check.label}</strong><span>{check.detail}</span></div></li>)}</ul>}
      <DnsFilterPanel notify={notify} />
      <DeviceTable
        devices={devices}
        onToggleBlock={toggleBlock}
        busyMac={busyMac}
        traffic={traffic}
        onResetTraffic={() => void resetTraffic()}
      />
    </div>
  )
}

function DnsFilterPanel({ notify }: { notify: (message: string, error?: boolean) => void }) {
  const [state, setState] = useState<DnsFilterState>()
  const [profiles, setProfiles] = useState<string[]>([])
  const [blocked, setBlocked] = useState('')
  const [allowed, setAllowed] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = (payload: DnsFilterState) => {
    setState(payload)
    setProfiles(payload.profiles)
    setBlocked(payload.custom_blocked.join('\n'))
    setAllowed(payload.allowed.join('\n'))
  }

  useEffect(() => {
    let active = true
    api.dnsFilter()
      .then((payload) => active && load(payload))
      .catch((reason) => active && setError(reason.message))
    return () => { active = false }
  }, [])

  const lines = (value: string) => value.split(/[\s,;]+/).map((item) => item.trim()).filter(Boolean)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const payload = await api.setDnsFilter({
        profiles,
        custom_blocked: lines(blocked),
        allowed: lines(allowed),
      })
      load(payload)
      notify(payload.enabled ? `${payload.domain_count} domaines filtrés` : 'Filtrage DNS désactivé')
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Enregistrement impossible'
      setError(message)
      notify(message, true)
    } finally {
      setBusy(false)
    }
  }

  const refresh = async () => {
    setBusy(true)
    setError('')
    try {
      const payload = await api.refreshDnsFilter()
      load(payload)
      notify(`Listes à jour: ${payload.domain_count} domaines`)
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Actualisation impossible'
      setError(message)
      notify(message, true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel
      title="Filtrage DNS"
      className="dns-panel"
      action={
        <button className="button button-ghost" disabled={busy || !state} onClick={() => void refresh()}>
          <RefreshCw size={15} /> Actualiser les listes
        </button>
      }
    >
      <p className="prose">
        Les domaines choisis sont résolus vers 0.0.0.0 pour tous les appareils du Wi-Fi, avant même
        d’atteindre Tor. Les listes sont téléchargées <strong>à travers Tor</strong>: personne
        n’apprend que cette maison les récupère.
      </p>
      <form className="settings-form dns-form" onSubmit={submit}>
        <div className="dns-profiles">
          {state?.available_profiles.map((profile) => (
            <label key={profile.id} className={`choice-card ${profiles.includes(profile.id) ? 'choice-card-active' : ''}`}>
              <input
                type="checkbox"
                checked={profiles.includes(profile.id)}
                onChange={(event) =>
                  setProfiles((current) =>
                    event.target.checked
                      ? [...current, profile.id]
                      : current.filter((item) => item !== profile.id),
                  )
                }
              />
              <div><strong>{profile.label}</strong><p>{profile.description}</p></div>
            </label>
          ))}
        </div>
        <div className="two-column dns-lists">
          <label>
            Domaines bloqués en plus
            <textarea rows={5} value={blocked} placeholder="exemple.com" onChange={(event) => setBlocked(event.target.value)} />
          </label>
          <label>
            Domaines toujours autorisés
            <textarea rows={5} value={allowed} placeholder="mabanque.be" onChange={(event) => setAllowed(event.target.value)} />
          </label>
        </div>
        {error && <div className="form-error" role="alert">{error}</div>}
        <div className="dns-footer">
          <button className="button button-primary" disabled={busy}>
            <Filter size={16} /> {busy ? 'Application…' : 'Appliquer le filtrage'}
          </button>
          {state && (
            <span className="muted">
              {state.enabled
                ? `${state.domain_count.toLocaleString('fr-FR')} domaines · mis à jour ${relativeTime(state.updated_at)}`
                : 'Filtrage désactivé'}
            </span>
          )}
        </div>
        {state?.last_error && (
          <div className="callout callout-danger"><ShieldOff size={20} /><p><strong>Dernier téléchargement en échec</strong>{state.last_error}</p></div>
        )}
      </form>
    </Panel>
  )
}
