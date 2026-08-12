import { FormEvent, useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Globe, RefreshCw, Snowflake, Zap } from 'lucide-react'
import { api } from '../api'
import { Panel } from '../components/Panel'
import { Badge } from '../components/ui'
import { formatBytes, relativeTime } from '../lib'
import type { CircumventionPayload, ConnectionMode, StatusPayload } from '../types'

const MODE_LABELS: Record<ConnectionMode, { title: string; help: string }> = {
  direct: {
    title: 'Connexion directe',
    help: 'Tor se connecte sans pont. C’est le plus rapide là où Tor n’est pas filtré.',
  },
  auto: {
    title: 'Automatique',
    help: 'OnionPi surveille le démarrage de Tor et bascule seul sur un pont si la connexion reste bloquée.',
  },
  manual: {
    title: 'Pont choisi',
    help: 'Vous imposez un transport et, si besoin, vos propres lignes de pont.',
  },
}

const COUNTRY_NAMES: Record<string, string> = {
  BY: 'Biélorussie',
  CN: 'Chine',
  EG: 'Égypte',
  HK: 'Hong Kong',
  IR: 'Iran',
  MM: 'Birmanie',
  RU: 'Russie',
  TM: 'Turkménistan',
}

function countryLabel(code: string): string {
  return COUNTRY_NAMES[code.toUpperCase()] ?? code.toUpperCase()
}

export function CircumventionPage({
  status,
  notify,
  onStatusRefresh,
}: {
  status?: StatusPayload
  notify: (message: string, error?: boolean) => void
  onStatusRefresh: () => void
}) {
  const [state, setState] = useState<CircumventionPayload>()
  const [error, setError] = useState('')
  const [mode, setMode] = useState<ConnectionMode>('auto')
  const [transport, setTransport] = useState('snowflake')
  const [country, setCountry] = useState('')
  const [bridges, setBridges] = useState('')
  const [busy, setBusy] = useState(false)
  const [relayBusy, setRelayBusy] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  const adopt = (payload: CircumventionPayload) => {
    setState(payload)
    setMode(payload.mode)
    setTransport(payload.transport)
    setCountry(payload.country)
    setBridges(payload.custom_bridges.join('\n'))
  }

  useEffect(() => {
    let active = true
    api
      .circumvention()
      .then((payload) => active && adopt(payload))
      .catch((reason) => active && setError(reason instanceof Error ? reason.message : 'Lecture impossible'))
    return () => {
      active = false
    }
  }, [])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const payload = await api.setCircumvention({
        mode,
        transport,
        country,
        custom_bridges: bridges.split('\n').map((line) => line.trim()).filter(Boolean),
      })
      adopt(payload)
      notify('Configuration de connexion appliquée. Tor redémarre son circuit.')
      window.setTimeout(onStatusRefresh, 3000)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Modification refusée')
    } finally {
      setBusy(false)
    }
  }

  const refresh = async () => {
    setRefreshing(true)
    try {
      adopt(await api.refreshBridges())
      notify('Liste de ponts mise à jour depuis torproject.org')
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Mise à jour impossible', true)
    } finally {
      setRefreshing(false)
    }
  }

  const toggleRelay = async (enabled: boolean) => {
    setRelayBusy(true)
    try {
      const payload = await api.setSnowflakeRelay(enabled)
      adopt(payload)
      notify(enabled ? 'Proxy Snowflake démarré. Merci.' : 'Proxy Snowflake arrêté.')
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Commande refusée', true)
      api.circumvention().then(adopt).catch(() => undefined)
    } finally {
      setRelayBusy(false)
    }
  }

  const active = status?.tor.bridges
  const relay = state?.relay
  const stats = relay?.statistics
  const dirty =
    !!state &&
    (mode !== state.mode ||
      transport !== state.transport ||
      country !== state.country ||
      bridges.trim() !== state.custom_bridges.join('\n'))

  return (
    <div className="page operational-page">
      <div className="page-title">
        <h1>Contournement</h1>
        <p>Ponts et transports enfichables quand Tor est bloqué, et hébergement d’un proxy Snowflake.</p>
      </div>

      <div className="two-column">
        <Panel title="Connexion Tor actuelle">
          <div className="feature-status">
            <span className="feature-icon">{active?.use_bridges ? <Zap /> : <Globe />}</span>
            <div>
              <strong>{active?.use_bridges ? `Ponts ${active.transport ?? ''}`.trim() : 'Connexion directe'}</strong>
              <p>
                {active?.use_bridges
                  ? `${active.bridge_count} pont${active.bridge_count > 1 ? 's' : ''} configuré${active.bridge_count > 1 ? 's' : ''}`
                  : 'Aucun pont: Tor joint le réseau sans intermédiaire.'}
              </p>
            </div>
            {status?.tor.connected ? (
              <Badge tone="success" dot>Bootstrap 100 %</Badge>
            ) : (
              <Badge tone="warning" dot>{status?.tor.bootstrap ?? 0} %</Badge>
            )}
          </div>
          <dl className="details-list">
            <div><dt>Mode</dt><dd>{MODE_LABELS[state?.mode ?? 'auto'].title}</dd></div>
            <div>
              <dt>Bascule automatique</dt>
              <dd>{state?.auto_transport ? `active sur ${state.auto_transport}` : 'aucune'}</dd>
            </div>
            <div>
              <dt>Liste de ponts intégrée</dt>
              <dd>
                {state?.catalog.source === 'moat' ? 'mise à jour' : 'version fournie'}
                {state?.catalog.snapshot_at ? ` · ${relativeTime(state.catalog.snapshot_at)}` : ''}
              </dd>
            </div>
          </dl>
          <div className="action-grid">
            <button className="button button-secondary" disabled={refreshing} onClick={() => void refresh()}>
              <RefreshCw size={15} className={refreshing ? 'spin' : undefined} />
              {refreshing ? 'Mise à jour…' : 'Actualiser la liste de ponts'}
            </button>
          </div>
        </Panel>

        <Panel title="Pays et censure">
          <div className={`callout ${state?.censored_country ? 'callout-warning' : ''}`}>
            {state?.censored_country ? <AlertTriangle size={20} /> : <CheckCircle2 size={20} />}
            <p>
              <strong>
                {state?.country
                  ? `${countryLabel(state.country)}${state.censored_country ? ' — filtrage connu' : ' — pas de filtrage connu'}`
                  : 'Pays non renseigné'}
              </strong>
              {state?.censored_country
                ? `Transports recommandés ici: ${state.recommended.join(', ')}. En mode automatique, OnionPi démarre directement sur des ponts.`
                : 'En mode automatique, OnionPi tente d’abord une connexion directe et ne passe aux ponts qu’en cas de blocage.'}
            </p>
          </div>
          <p className="prose">
            Le pays sert seulement à choisir le bon transport: il vient du code réglementaire Wi-Fi défini à
            l’installation et n’est envoyé nulle part.
          </p>
        </Panel>
      </div>

      <Panel title="Mode de connexion">
        <form className="settings-form" onSubmit={submit}>
          <fieldset className="mode-choices">
            <legend className="sr-only">Mode de connexion</legend>
            {(Object.keys(MODE_LABELS) as ConnectionMode[]).map((id) => (
              <label key={id} className={`mode-choice ${mode === id ? 'mode-choice-active' : ''}`}>
                <input type="radio" name="mode" value={id} checked={mode === id} onChange={() => setMode(id)} />
                <span>
                  <strong>{MODE_LABELS[id].title}</strong>
                  {MODE_LABELS[id].help}
                </span>
              </label>
            ))}
          </fieldset>

          <label>
            Transport {mode === 'auto' && '(premier essai en cas de blocage)'}
            <select value={transport} onChange={(event) => setTransport(event.target.value)}>
              {(state?.transports ?? []).map((item) => (
                <option key={item.id} value={item.id} disabled={!item.available}>
                  {item.label}
                  {item.available ? '' : ' — non installé'}
                </option>
              ))}
            </select>
          </label>
          <p className="prose">
            {state?.transports.find((item) => item.id === transport)?.description ??
              'Choisissez le transport à utiliser.'}
          </p>

          <label>
            Pays (code ISO à deux lettres)
            <input
              type="text"
              value={country}
              maxLength={2}
              placeholder="BE"
              onChange={(event) => setCountry(event.target.value.replace(/[^A-Za-z]/g, '').toUpperCase())}
            />
          </label>

          <label>
            Ponts personnalisés, un par ligne
            <textarea
              rows={4}
              spellCheck={false}
              value={bridges}
              placeholder={'obfs4 192.0.2.1:443 FINGERPRINT cert=… iat-mode=0'}
              onChange={(event) => setBridges(event.target.value)}
            />
          </label>
          <p className="prose">
            Laissez vide pour utiliser les ponts intégrés. Pour obtenir des ponts privés: {' '}
            <a className="text-link" href="https://bridges.torproject.org" target="_blank" rel="noreferrer">
              bridges.torproject.org
            </a>{' '}
            ou un courriel à bridges@torproject.org depuis Gmail ou Riseup.
          </p>

          {error && <div className="form-error" role="alert">{error}</div>}
          <button className="button button-primary" disabled={busy || !dirty}>
            {busy ? 'Application…' : 'Appliquer'}
          </button>
        </form>
      </Panel>

      <Panel title="Héberger un proxy Snowflake">
        <div className="feature-status">
          <span className="feature-icon"><Snowflake /></span>
          <div>
            <strong>{relay?.active ? 'Proxy actif' : relay?.installed ? 'Proxy arrêté' : 'Non installé'}</strong>
            <p>
              {relay?.installed
                ? 'Votre connexion aide des personnes censurées à rejoindre Tor.'
                : 'Installez le paquet Debian snowflake-proxy puis relancez l’installateur.'}
            </p>
          </div>
          <button
            className={relay?.active ? 'button button-outline' : 'button button-primary'}
            disabled={relayBusy || !relay?.installed}
            onClick={() => void toggleRelay(!relay?.active)}
          >
            {relayBusy ? 'Patientez…' : relay?.active ? 'Arrêter' : 'Démarrer'}
          </button>
        </div>
        {stats && (
          <dl className="details-list">
            <div><dt>Connexions servies (24 h)</dt><dd>{stats.connections}</dd></div>
            <div><dt>Relayé vers les clients</dt><dd>{formatBytes(stats.downloaded)}</dd></div>
            <div><dt>Relayé depuis les clients</dt><dd>{formatBytes(stats.uploaded)}</dd></div>
          </dl>
        )}
        <div className="callout">
          <CheckCircle2 size={20} />
          <p>
            <strong>Ce que fait ce proxy, et ce qu’il ne fait pas</strong>
            Il transporte du trafic chiffré entre un client censuré et un pont Snowflake. Ce n’est pas un nœud de
            sortie: aucun site tiers ne verra votre adresse IP. Le trafic consomme votre bande passante et votre
            fournisseur d’accès voit des connexions WebRTC. Dans un pays où l’usage de Tor est réprimé, mieux vaut
            ne pas l’activer.
          </p>
        </div>
      </Panel>
    </div>
  )
}
