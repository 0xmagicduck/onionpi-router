import { ChangeEvent, FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  Cable,
  CheckCircle2,
  Database,
  Download,
  ExternalLink,
  Power,
  RefreshCw,
  RotateCw,
  ShieldCheck,
  Upload,
  Wifi,
} from 'lucide-react'
import { api } from '../api'
import { relativeTime } from '../lib'
import { ChatPanel } from '../components/ChatPanel'
import { DeviceTable } from '../components/DeviceTable'
import { Panel } from '../components/Panel'
import { TorStatus } from '../components/TorStatus'
import { TorAdvanced } from './TorAdvanced'
import type {
  ChatMessage,
  Device,
  DiagnosticsPayload,
  StatusPayload,
  SystemActionsPayload,
  UpdateState,
} from '../types'

function serviceActive(status: StatusPayload | undefined, id: string): boolean | undefined {
  return status?.system.services.find((service) => service.id === id)?.active
}

function DiagnosticsPanel() {
  const [report, setReport] = useState<DiagnosticsPayload>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const mounted = useRef(true)

  const load = useCallback(async () => {
    setBusy(true)
    setError('')
    try {
      const next = await api.diagnostics()
      if (mounted.current) setReport(next)
    } catch (reason) {
      if (mounted.current) {
        setError(reason instanceof Error ? reason.message : 'Diagnostic indisponible')
      }
    } finally {
      if (mounted.current) setBusy(false)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    void load()
    return () => { mounted.current = false }
  }, [load])

  const label = report?.status === 'ok'
    ? 'Tous les contrôles sont bons'
    : report?.status === 'warning'
      ? 'Une attention est recommandée'
      : 'Une intervention est requise'

  return (
    <Panel title="Diagnostic de santé" className="diagnostics-panel">
      <div className="feature-status">
        <span className="feature-icon"><Database /></span>
        <div>
          <strong>Services, stockage et données</strong>
          <p>Contrôle local, sans transmettre d’information hors de l’OnionPi.</p>
        </div>
        {report && (
          <span className={`diagnostic-summary diagnostic-summary-${report.status}`}>
            <i /> {label}
          </span>
        )}
      </div>
      {error && <p className="form-error diagnostic-error" role="alert">{error}</p>}
      {!report && !error && <p className="muted diagnostic-loading">Contrôle en cours…</p>}
      {report && (
        <div className="diagnostic-list">
          {report.checks.map((check) => (
            <div className={`diagnostic-row diagnostic-${check.status}`} key={check.id}>
              {check.status === 'ok' ? <CheckCircle2 /> : <AlertTriangle />}
              <div>
                <strong>{check.label}</strong>
                <p>{check.detail}</p>
                {check.remedy && <small>{check.remedy}</small>}
              </div>
            </div>
          ))}
        </div>
      )}
      {report && (
        <p className="diagnostic-meta">
          Base : {report.database.messages} messages · {report.database.activity} événements ·{' '}
          {(report.database.bytes / 1024).toLocaleString('fr-FR', { maximumFractionDigits: 0 })} Kio
        </p>
      )}
      <div className="action-grid">
        <button className="button button-secondary" disabled={busy} onClick={() => void load()}>
          <RefreshCw size={15} /> {busy ? 'Contrôle…' : 'Relancer le diagnostic'}
        </button>
        {report && (
          <button
            className="button button-secondary"
            onClick={() => {
              const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }))
              const link = document.createElement('a')
              link.href = url
              link.download = `onionpi-diagnostic-${report.generated_at}.json`
              link.click()
              URL.revokeObjectURL(url)
            }}
          >
            <Download size={15} /> Exporter le rapport
          </button>
        )}
      </div>
    </Panel>
  )
}

function ServiceBadge({ active, upLabel, downLabel }: { active?: boolean; upLabel: string; downLabel: string }) {
  if (active === undefined) return <span className="muted">—</span>
  return active
    ? <span className="healthy"><i /> {upLabel}</span>
    : <span className="unhealthy"><i /> {downLabel}</span>
}

export function NetworkPage({ status, devices }: { status?: StatusPayload; devices: Device[] }) {
  const accessPoint = serviceActive(status, 'onionpi-ap')
  const firewall = serviceActive(status, 'onionpi-firewall')
  return (
    <div className="page operational-page">
      <div className="page-title"><h1>Réseau</h1><p>Le point d’accès local et sa connexion amont.</p></div>
      <div className="two-column">
        <Panel title="Point d’accès Wi-Fi">
          <div className="feature-status"><span className="feature-icon"><Wifi /></span><div><strong>{status?.network.ssid ?? 'OnionPi Wi-Fi'}</strong><p>Interface {status?.network.wifi_interface ?? 'wlan0'} · canal {status?.network.channel ?? 'auto'}</p></div><ServiceBadge active={accessPoint} upLabel="Actif" downLabel="Arrêté" /></div>
          <dl className="details-list"><div><dt>Passerelle locale</dt><dd className="mono">{status?.network.gateway_ip ?? '10.42.0.1'}</dd></div><div><dt>Isolation des clients</dt><dd>Activée</dd></div><div><dt>DNS</dt><dd>Résolution via Tor</dd></div></dl>
        </Panel>
        <Panel title="Connexion amont">
          <div className="feature-status"><span className="feature-icon"><Cable /></span><div><strong>{status?.network.upstream_interface ?? 'eth0'}</strong><p>Accès Internet de la Raspberry Pi</p></div><ServiceBadge active={serviceActive(status, 'tor')} upLabel="Tor connecté" downLabel="Tor arrêté" /></div>
          <div className="privacy-note"><ShieldCheck size={20} /><p><strong>{firewall === false ? 'Coupe-circuit arrêté' : 'Coupe-circuit actif'}</strong>{firewall === false ? 'Le service onionpi-firewall ne tourne pas: vérifiez-le avant de laisser des appareils se connecter.' : 'Le trafic client non redirigé vers Tor est bloqué par nftables.'}</p></div>
        </Panel>
      </div>
      <DeviceTable devices={devices} />
    </div>
  )
}

export function TorPage({ status, busy, onNewIdentity, onOpenBridges, notify }: { status?: StatusPayload; busy: boolean; onNewIdentity: () => Promise<void>; onOpenBridges: () => void; notify: (message: string, error?: boolean) => void }) {
  const bridges = status?.tor.bridges
  const blocked = status !== undefined && !status.tor.connected
  return (
    <div className="page operational-page">
      <div className="page-title"><h1>Tor</h1><p>Circuits, identité de sortie et état de bootstrap.</p></div>
      {status && <TorStatus status={status} busy={busy} onNewIdentity={onNewIdentity} />}
      {blocked && (
        <Panel title="Tor n’a pas fini de démarrer">
          <p className="prose">Un démarrage qui reste bloqué en dessous de 100 % est le symptôme habituel d’un réseau qui filtre Tor. Les ponts et les transports enfichables contournent ce filtrage.</p>
          <button className="button button-primary" style={{ margin: '12px 19px 18px' }} onClick={onOpenBridges}>Configurer un pont</button>
        </Panel>
      )}
      <div className="two-column">
        <Panel title={status?.protection.label ?? 'Protection'}>
          <ul className="check-list">
            {status?.protection.checks.map((check) => (
              <li className={check.ok ? '' : 'protection-check-failed'} key={check.id}>
                <CheckCircle2 />{check.label}<small>{check.detail}</small>
              </li>
            ))}
            {bridges?.use_bridges && <li><CheckCircle2 />Ponts {bridges.transport ?? ''} actifs ({bridges.bridge_count})</li>}
          </ul>
        </Panel>
        <Panel title="À savoir"><p className="prose">Tor protège le chemin réseau, mais ne remplace pas HTTPS. Les applications peuvent encore révéler des informations personnelles si vous vous y connectez avec vos comptes habituels.</p><a className="text-link" href="https://support.torproject.org/" target="_blank" rel="noreferrer">Guide de sécurité Tor <ExternalLink size={14} /></a></Panel>
      </div>
      <TorAdvanced notify={notify} />
    </div>
  )
}

export function ChatPage({ messages, online, connected, send }: { messages: ChatMessage[]; online: number; connected: boolean; send: (body: string) => boolean }) {
  return <div className="page chat-page"><div className="page-title"><h1>Chat du réseau</h1><p>Échangez entre les personnes connectées au Wi-Fi OnionPi.</p></div><ChatPanel messages={messages} online={online} connected={connected} send={send} /></div>
}

export function LogsPage() {
  const [service, setService] = useState('tor')
  const [lines, setLines] = useState<string[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    api.logs(service).then((payload) => active && setLines(payload.lines)).catch((reason) => active && setError(reason.message))
    return () => { active = false }
  }, [service])
  return (
    <div className="page logs-page"><div className="page-title title-with-action"><div><h1>Journaux</h1><p>Événements récents des services OnionPi.</p></div><label className="select-control">Service<select value={service} onChange={(event) => setService(event.target.value)}><option value="tor">Tor</option><option value="onionpi">OnionPi</option><option value="NetworkManager">Wi-Fi</option><option value="dnsmasq">DHCP</option><option value="onionpi-firewall">Pare-feu OnionPi</option><option value="nftables">Moteur nftables</option><option value="snowflake-proxy">Proxy Snowflake</option></select></label></div>
      <section className="panel terminal" aria-live="polite">{error ? <p className="form-error">{error}</p> : lines.length ? lines.map((line, index) => <code key={`${line}-${index}`}>{line}</code>) : <p className="muted">Aucune entrée disponible. Le compte OnionPi doit appartenir au groupe systemd-journal.</p>}</section>
    </div>
  )
}

function PasswordPanel({ onChanged }: { onChanged: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (next !== confirm) {
      setError('Les deux nouveaux mots de passe diffèrent.')
      return
    }
    setBusy(true)
    setError('')
    try {
      await api.changePassword(current, next)
      onChanged()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Modification impossible')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel title="Mot de passe administrateur">
      <form className="settings-form" onSubmit={submit}>
        <label>Mot de passe actuel<input type="password" autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} required /></label>
        <label>Nouveau mot de passe<input type="password" autoComplete="new-password" minLength={12} value={next} onChange={(event) => setNext(event.target.value)} required /></label>
        <label>Confirmation<input type="password" autoComplete="new-password" minLength={12} value={confirm} onChange={(event) => setConfirm(event.target.value)} required /></label>
        <p className="prose">12 caractères minimum. Toutes les sessions ouvertes seront fermées, y compris celle-ci.</p>
        {error && <div className="form-error" role="alert">{error}</div>}
        <button className="button button-primary" disabled={busy}>{busy ? 'Modification…' : 'Modifier le mot de passe'}</button>
      </form>
    </Panel>
  )
}

const RESTART_ACTIONS = [
  { id: 'restart-tor', label: 'Redémarrer Tor' },
  { id: 'restart-dnsmasq', label: 'Redémarrer le DNS' },
  { id: 'restart-network', label: 'Redémarrer le Wi-Fi' },
  { id: 'restart-firewall', label: 'Recharger le pare-feu' },
]

function MaintenancePanel({ notify }: { notify: (message: string, error?: boolean) => void }) {
  const [actions, setActions] = useState<SystemActionsPayload>()
  const [busy, setBusy] = useState('')
  const [confirming, setConfirming] = useState('')

  useEffect(() => {
    let active = true
    api.systemActions().then((payload) => active && setActions(payload)).catch(() => undefined)
    return () => { active = false }
  }, [])

  const run = async (action: string) => {
    setBusy(action)
    setConfirming('')
    try {
      const result = await api.runSystemAction(action)
      notify(result.message)
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Action refusée', true)
    } finally {
      setBusy('')
    }
  }

  return (
    <Panel title="Maintenance">
      {actions && !actions.available && (
        <div className="privacy-note"><ShieldCheck size={20} /><p><strong>Service privilégié absent</strong>onionpi-agent.path n’écoute pas: relancez l’installateur pour rétablir les redémarrages depuis l’interface.</p></div>
      )}
      <div className="action-grid">
        {RESTART_ACTIONS.map((action) => (
          <button key={action.id} className="button button-secondary" disabled={busy !== ''} onClick={() => void run(action.id)}>
            <RotateCw size={15} /> {busy === action.id ? 'En cours…' : action.label}
          </button>
        ))}
      </div>
      <div className="action-grid">
        {['reboot', 'poweroff'].map((action) => (
          <button
            key={action}
            className={confirming === action ? 'button button-danger' : 'button button-outline'}
            disabled={busy !== ''}
            onClick={() => (confirming === action ? void run(action) : setConfirming(action))}
          >
            <Power size={15} />
            {confirming === action
              ? 'Confirmer'
              : action === 'reboot' ? 'Redémarrer la Pi' : 'Éteindre la Pi'}
          </button>
        ))}
      </div>
      <p className="prose">
        Un redémarrage coupe le Wi-Fi OnionPi pendant une minute environ. Après une extinction, seule
        une coupure d’alimentation permet de rallumer la Pi.
      </p>
    </Panel>
  )
}

const UPDATE_HOURS = Array.from({ length: 24 }, (_, hour) => `${String(hour).padStart(2, '0')}:00`)

function updateStatusLabel(state: UpdateState): { text: string; tone: 'healthy' | 'unhealthy' | 'muted' } {
  if (!state.supported) return { text: 'Client de mise à jour absent', tone: 'unhealthy' }
  if (state.running) return { text: 'Installation en cours', tone: 'muted' }
  if (state.update_pending) return { text: `Version ${state.available} disponible`, tone: 'unhealthy' }
  if (state.last_check_status === 'error') return { text: 'Dernière vérification en échec', tone: 'unhealthy' }
  if (!state.enabled) return { text: 'Vérifications désactivées', tone: 'muted' }
  return { text: 'À jour', tone: 'healthy' }
}

function UpdatePanel({ notify }: { notify: (message: string, error?: boolean) => void }) {
  const [state, setState] = useState<UpdateState>()
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const load = async () => {
    try {
      setState(await api.updateState())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'État des mises à jour indisponible')
    }
  }

  useEffect(() => {
    void load()
    // An installation takes minutes and reboots nothing: polling is the only
    // way for the page to notice that it finished.
    const timer = window.setInterval(() => void load(), 20_000)
    return () => window.clearInterval(timer)
  }, [])

  const save = async (changes: Partial<Pick<UpdateState, 'channel' | 'schedule' | 'enabled' | 'auto_apply'>>) => {
    if (!state) return
    const next = { ...state, ...changes }
    setBusy('settings')
    try {
      const saved = await api.saveUpdateSettings({
        channel: next.channel,
        schedule: next.schedule,
        enabled: next.enabled,
        apply: next.auto_apply,
      })
      setState(saved)
      notify(saved.warning ? `Enregistré, mais: ${saved.warning}` : 'Horaire de mise à jour enregistré', Boolean(saved.warning))
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Enregistrement impossible', true)
    } finally {
      setBusy('')
    }
  }

  const check = async () => {
    setBusy('check')
    try {
      const result = await api.checkForUpdate()
      setState(result)
      notify(result.update_pending ? `Version ${result.available} disponible` : `OnionPi est à jour (${result.installed})`)
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Vérification impossible', true)
    } finally {
      setBusy('')
    }
  }

  const install = async () => {
    setBusy('install')
    try {
      const result = await api.runUpdate()
      setState(result)
      notify(result.message ?? 'Mise à jour lancée')
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Mise à jour refusée', true)
    } finally {
      setBusy('')
    }
  }

  if (error) return <Panel title="Mises à jour"><p className="form-error">{error}</p></Panel>
  if (!state) return <Panel title="Mises à jour"><p className="muted">Chargement…</p></Panel>

  const badge = updateStatusLabel(state)
  const hours = state.schedule.split(',')

  return (
    <Panel title="Mises à jour">
      <div className="feature-status">
        <span className="feature-icon"><RefreshCw /></span>
        <div>
          <strong>Version {state.installed}</strong>
          <p>
            Canal {state.channel === 'edge' ? 'edge (branche principale)' : 'stable (publications)'}
            {state.over_tor ? ' · téléchargement par Tor' : ' · téléchargement direct'}
          </p>
        </div>
        <span className={badge.tone === 'muted' ? 'muted' : badge.tone}>
          {badge.tone !== 'muted' && <i />} {badge.text}
        </span>
      </div>

      <form className="settings-form" onSubmit={(event) => event.preventDefault()}>
        <label>
          Canal
          <select
            value={state.channel}
            disabled={busy !== '' || !state.supported}
            onChange={(event) => void save({ channel: event.target.value as UpdateState['channel'] })}
          >
            <option value="stable">Stable — versions publiées</option>
            <option value="edge">Edge — chaque envoi sur la branche principale</option>
          </select>
        </label>
        <label>
          Heure de vérification
          <select
            value={hours[0] ?? '04:30'}
            disabled={busy !== '' || !state.supported}
            onChange={(event) => void save({ schedule: [event.target.value, ...hours.slice(1)].join(',') })}
          >
            {[...new Set([hours[0] ?? '04:30', ...UPDATE_HOURS])].sort().map((hour) => (
              <option key={hour} value={hour}>{hour}</option>
            ))}
          </select>
        </label>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={state.enabled}
            disabled={busy !== '' || !state.supported}
            onChange={(event) => void save({ enabled: event.target.checked })}
          />
          Vérifier automatiquement à cette heure
        </label>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={state.auto_apply}
            disabled={busy !== '' || !state.enabled || !state.supported}
            onChange={(event) => void save({ auto_apply: event.target.checked })}
          />
          Installer sans demander
        </label>
      </form>

      <dl className="details-list">
        <div><dt>Prochaine vérification</dt><dd>{state.next_run ? new Date(state.next_run * 1000).toLocaleString('fr-FR') : '—'}</dd></div>
        <div><dt>Dernière vérification</dt><dd>{state.last_check ? relativeTime(state.last_check) : 'jamais'}</dd></div>
        <div><dt>Dernière installation</dt><dd>{state.last_apply_message || '—'}</dd></div>
        {state.repository && <div><dt>Dépôt</dt><dd className="mono">{state.repository}</dd></div>}
      </dl>

      <div className="action-grid">
        <button className="button button-secondary" disabled={busy !== '' || !state.supported} onClick={() => void check()}>
          <RefreshCw size={15} /> {busy === 'check' ? 'Vérification…' : 'Vérifier maintenant'}
        </button>
        <button
          className="button button-primary"
          disabled={busy !== '' || !state.supported || state.running || !state.update_pending}
          onClick={() => void install()}
        >
          <Download size={15} /> {state.running ? 'Installation…' : `Installer ${state.available || ''}`.trim()}
        </button>
      </div>

      <p className="prose">
        La vérification et le téléchargement passent par le port SOCKS de Tor : le réseau local ne
        voit pas que cette adresse est une OnionPi. L’archive n’est installée qu’après contrôle de
        son empreinte SHA-256, et /opt/onionpi est copié avant toute écriture — un contrôle
        post-installation en échec restaure automatiquement la version précédente.
      </p>
      {state.history.length > 0 && (
        <ul className="check-list">
          {state.history.slice(0, 3).map((entry) => <li key={entry}><CheckCircle2 />{entry}</li>)}
        </ul>
      )}
    </Panel>
  )
}

function ConfigPanel({ notify }: { notify: (message: string, error?: boolean) => void }) {
  const fileInput = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)

  const download = async () => {
    setBusy(true)
    try {
      const document_ = await api.exportConfig()
      const blob = new Blob([JSON.stringify(document_, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'onionpi-config.json'
      link.click()
      URL.revokeObjectURL(url)
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Export impossible', true)
    } finally {
      setBusy(false)
    }
  }

  const upload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setBusy(true)
    try {
      const parsed = JSON.parse(await file.text())
      const result = await api.importConfig(parsed)
      notify(
        result.failures.length
          ? `Import partiel: ${result.failures.join(' · ')}`
          : `Import terminé: ${result.applied.join(', ') || 'rien à restaurer'}`,
        result.failures.length > 0,
      )
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Fichier illisible', true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel title="Sauvegarde de la configuration">
      <p className="prose">
        Le fichier contient les ponts, la politique de sortie, le filtrage DNS et les appareils
        bloqués. Il ne contient aucun mot de passe, ni la clé du service onion.
      </p>
      <div className="action-grid">
        <button className="button button-secondary" disabled={busy} onClick={() => void download()}>
          <Download size={15} /> Exporter
        </button>
        <button className="button button-secondary" disabled={busy} onClick={() => fileInput.current?.click()}>
          <Upload size={15} /> Importer
        </button>
        <input ref={fileInput} type="file" accept="application/json" hidden onChange={(event) => void upload(event)} />
      </div>
    </Panel>
  )
}

export function SettingsPage({ status, onPasswordChanged, notify }: { status?: StatusPayload; onPasswordChanged: () => void; notify: (message: string, error?: boolean) => void }) {
  return (
    <div className="page operational-page"><div className="page-title"><h1>Paramètres</h1><p>Informations d’installation, maintenance et règles de sécurité.</p></div>
      <div className="two-column"><Panel title="Cette OnionPi"><dl className="details-list"><div><dt>Nom</dt><dd>{status?.device_name ?? 'OnionPi'}</dd></div><div><dt>Hôte</dt><dd className="mono">{status?.system.hostname ?? '—'}</dd></div><div><dt>Mode démonstration</dt><dd>{status?.demo_mode ? 'Oui' : 'Non'}</dd></div><div><dt>Version</dt><dd className="mono">{status?.version ?? '—'}</dd></div></dl></Panel>
      <Panel title="Administration"><div className="privacy-note"><ShieldCheck size={20} /><p><strong>Accès local uniquement</strong>L’interface n’est pas publiée sur Internet et chaque mutation exige une session et un jeton CSRF.</p></div><p className="prose">Pour modifier le SSID ou le mot de passe Wi-Fi, relancez l’installateur depuis un terminal local. Cela évite de couper accidentellement la session web en cours.</p></Panel></div>
      <div className="two-column"><UpdatePanel notify={notify} /><MaintenancePanel notify={notify} /></div>
      <DiagnosticsPanel />
      <div className="two-column"><ConfigPanel notify={notify} /></div>
      <div className="two-column"><PasswordPanel onChanged={onPasswordChanged} /></div>
    </div>
  )
}
