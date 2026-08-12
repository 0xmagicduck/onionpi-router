import { FormEvent, useEffect, useState } from 'react'
import { Copy, Gauge, Globe2, KeyRound, Link2, RefreshCw, Route, Timer, Trash2 } from 'lucide-react'
import { api } from '../api'
import { Modal } from '../components/Modal'
import { Panel } from '../components/Panel'
import { EmptyState } from '../components/ui'
import { relativeTime } from '../lib'
import type { OnionState, SpeedTestResult, TorAdvancedPayload, TorPolicyState } from '../types'

type Notify = (message: string, error?: boolean) => void

function rotationLabel(seconds: number): string {
  if (!seconds) return 'Désactivée'
  if (seconds < 3600) return `Toutes les ${Math.round(seconds / 60)} min`
  if (seconds < 86_400) return `Toutes les ${Math.round(seconds / 3600)} h`
  return 'Une fois par jour'
}

export function TorAdvanced({ notify }: { notify: Notify }) {
  const [payload, setPayload] = useState<TorAdvancedPayload>()
  const [error, setError] = useState('')

  const reload = () =>
    api.torAdvanced().then(setPayload).catch((reason) => setError(reason.message))

  useEffect(() => {
    let active = true
    api.torAdvanced()
      .then((value) => active && setPayload(value))
      .catch((reason) => active && setError(reason.message))
    return () => { active = false }
  }, [])

  if (error) return <Panel title="Réglages avancés"><p className="form-error">{error}</p></Panel>
  if (!payload) return <Panel title="Réglages avancés"><p className="muted">Lecture de l’état Tor…</p></Panel>

  return (
    <>
      <div className="two-column">
        <PolicyPanel
          policy={payload.policy}
          notify={notify}
          onSaved={(policy) => setPayload({ ...payload, policy })}
        />
        <SpeedTestPanel notify={notify} />
      </div>
      <div className="two-column">
        <OnionPanel
          onion={payload.onion}
          notify={notify}
          onChanged={(onion) => setPayload({ ...payload, onion })}
        />
        <CircuitsPanel circuits={payload.circuits} onRefresh={() => void reload()} />
      </div>
    </>
  )
}

function PolicyPanel({
  policy,
  notify,
  onSaved,
}: {
  policy: TorPolicyState
  notify: Notify
  onSaved: (policy: TorPolicyState) => void
}) {
  const [country, setCountry] = useState(policy.exit_country)
  const [rotation, setRotation] = useState(policy.rotation_seconds)
  const [busy, setBusy] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    try {
      const updated = await api.setTorPolicy({ exit_country: country, rotation_seconds: rotation })
      onSaved(updated)
      notify('Politique Tor enregistrée')
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Modification refusée', true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel title="Pays de sortie et rotation">
      <form className="settings-form" onSubmit={submit}>
        <label>
          <span className="label-with-icon"><Globe2 size={15} /> Pays du relais de sortie</span>
          <select value={country} onChange={(event) => setCountry(event.target.value)}>
            <option value="">Laisser Tor choisir (recommandé)</option>
            {policy.countries.map((item) => (
              <option key={item.code} value={item.code}>{item.name}</option>
            ))}
          </select>
        </label>
        <label>
          <span className="label-with-icon"><Timer size={15} /> Nouvelle identité automatique</span>
          <select value={rotation} onChange={(event) => setRotation(Number(event.target.value))}>
            {policy.rotation_choices.map((value) => (
              <option key={value} value={value}>{rotationLabel(value)}</option>
            ))}
          </select>
        </label>
        <p className="prose">
          Forcer un pays réduit la taille du groupe dans lequel vous vous fondez et peut ralentir la
          navigation. C’est utile quand un service refuse les connexions étrangères, pas au quotidien.
        </p>
        {policy.next_rotation > 0 && (
          <p className="muted">Dernière rotation {relativeTime(policy.last_rotation)}.</p>
        )}
        <button className="button button-primary" disabled={busy}>
          {busy ? 'Application…' : 'Enregistrer'}
        </button>
      </form>
    </Panel>
  )
}

function SpeedTestPanel({ notify }: { notify: Notify }) {
  const [result, setResult] = useState<SpeedTestResult>()
  const [busy, setBusy] = useState(false)

  const run = async () => {
    setBusy(true)
    try {
      setResult(await api.speedTest())
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Mesure impossible', true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel title="Débit réel à travers Tor">
      <div className="speedtest">
        <strong>{result ? `${result.download_mbps.toLocaleString('fr-FR')} Mb/s` : '—'}</strong>
        <span className="muted">
          {result
            ? `${result.latency_ms} ms de latence · ${(result.bytes / 1_000_000).toFixed(1)} Mo en ${result.seconds} s`
            : 'Télécharge 3 Mo par le circuit courant.'}
        </span>
        <button className="button button-primary" disabled={busy} onClick={() => void run()}>
          <Gauge size={16} /> {busy ? 'Mesure en cours…' : 'Lancer la mesure'}
        </button>
      </div>
      <p className="prose">
        Cette bande passante est offerte par des bénévoles: mesurez seulement quand c’est utile.
      </p>
    </Panel>
  )
}

function OnionPanel({
  onion,
  notify,
  onChanged,
}: {
  onion: OnionState
  notify: Notify
  onChanged: (onion: OnionState) => void
}) {
  const [busy, setBusy] = useState(false)

  const call = async (action: () => Promise<OnionState>, message: string) => {
    setBusy(true)
    try {
      onChanged(await action())
      notify(message)
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Action refusée', true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel title="Accès à distance par service onion">
      <div className="feature-status">
        <span className="feature-icon"><Link2 /></span>
        <div>
          <strong>{onion.enabled ? (onion.published ? 'Publié' : 'Publication en cours') : 'Désactivé'}</strong>
          <p>L’interface reste joignable depuis n’importe où, sans ouvrir le moindre port sur la box.</p>
        </div>
        <button
          className={onion.enabled ? 'button button-outline' : 'button button-primary'}
          disabled={busy}
          onClick={() =>
            void call(
              () => api.setOnion(!onion.enabled),
              onion.enabled ? 'Service onion retiré' : 'Service onion publié',
            )
          }
        >
          {busy ? 'Patientez…' : onion.enabled ? 'Désactiver' : 'Activer'}
        </button>
      </div>
      {onion.address && (
        <div className="onion-address">
          <code className="mono">{onion.address}</code>
          <button
            className="button button-ghost"
            onClick={() => {
              void navigator.clipboard?.writeText(onion.address)
              notify('Adresse copiée')
            }}
          >
            <Copy size={15} /> Copier
          </button>
        </div>
      )}
      <p className="prose">
        {onion.client_auth
          ? 'Seuls les appareils dont la clé figure ci-dessous peuvent résoudre cette adresse. Elle ne suffit plus à elle seule.'
          : 'Ouvrez cette adresse dans le navigateur Tor. Toute personne qui la connaît peut atteindre la page de connexion: elle vaut un mot de passe, ne la publiez pas.'}
      </p>
      {onion.enabled && (
        <>
          <OnionClients onion={onion} notify={notify} onChanged={onChanged} />
          <div className="action-grid">
            <button
              className="button button-danger"
              disabled={busy}
              onClick={() => void call(api.rotateOnion, 'Nouvelle adresse onion générée')}
            >
              <RefreshCw size={15} /> Générer une nouvelle adresse
            </button>
          </div>
        </>
      )}
    </Panel>
  )
}

function OnionClients({
  onion,
  notify,
  onChanged,
}: {
  onion: OnionState
  notify: Notify
  onChanged: (onion: OnionState) => void
}) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [issued, setIssued] = useState<{ name: string; key: string }>()

  const add = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    try {
      const created = await api.addOnionClient(name.trim())
      onChanged(created.onion)
      setIssued({ name: created.name, key: created.private_key })
      setName('')
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Accès refusé', true)
    } finally {
      setBusy(false)
    }
  }

  const revoke = async (clientName: string) => {
    setBusy(true)
    try {
      onChanged(await api.removeOnionClient(clientName))
      notify(`Accès « ${clientName} » révoqué`)
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Révocation refusée', true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="onion-clients">
      <h3 className="section-label"><KeyRound size={14} /> Appareils autorisés</h3>
      <p className="prose">
        Chaque accès crée une clé que le navigateur Tor installe une fois. Sans elle, l’adresse
        reste introuvable — même pour qui la connaît par cœur.
      </p>
      <ul className="rule-list">
        {onion.clients.map((client) => (
          <li key={client.name}>
            <div>
              <strong>{client.name}</strong>
              <span className="muted">Autorisé {relativeTime(client.added_at)}</span>
            </div>
            <button className="button button-small button-ghost" disabled={busy} onClick={() => void revoke(client.name)}>
              <Trash2 size={14} /> Révoquer
            </button>
          </li>
        ))}
        {!onion.clients.length && <li className="muted">Aucun accès restreint pour le moment.</li>}
      </ul>
      <form className="inline-form" onSubmit={add}>
        <label className="sr-only" htmlFor="onion-client-name">Nom de l’accès</label>
        <input
          id="onion-client-name"
          value={name}
          maxLength={32}
          placeholder="Portable de Camille"
          onChange={(event) => setName(event.target.value)}
        />
        <button className="button button-primary" disabled={busy || !name.trim() || onion.clients.length >= onion.max_clients}>
          <KeyRound size={15} /> Autoriser
        </button>
      </form>
      {issued && (
        <Modal
          title={`Clé de ${issued.name}`}
          description="Elle n’est affichée qu’une fois: OnionPi n’en garde que la moitié publique."
          icon={<KeyRound size={22} />}
          onClose={() => setIssued(undefined)}
          actions={
            <>
              <button
                type="button"
                className="button button-secondary"
                onClick={() => {
                  void navigator.clipboard?.writeText(issued.key)
                  notify('Clé copiée')
                }}
              >
                <Copy size={15} /> Copier
              </button>
              <button type="button" className="button button-primary" onClick={() => setIssued(undefined)}>
                J’ai enregistré la clé
              </button>
            </>
          }
        >
          <pre className="mono key-block">{issued.key}</pre>
          <p className="prose">
            Enregistrez cette ligne dans le navigateur Tor, fichier
            {' '}<code>&lt;profil&gt;/tor/onion-auth/{issued.name.replace(/[^\w.-]/g, '-')}.auth_private</code>,
            puis redémarrez-le. Perdre la clé ne coûte qu’un nouvel accès à créer.
          </p>
        </Modal>
      )}
    </div>
  )
}

function CircuitsPanel({
  circuits,
  onRefresh,
}: {
  circuits: TorAdvancedPayload['circuits']
  onRefresh: () => void
}) {
  return (
    <Panel
      title={`Circuits ouverts (${circuits.length})`}
      action={<button className="button button-ghost" onClick={onRefresh}><RefreshCw size={15} /> Actualiser</button>}
    >
      <div className="circuit-list">
        {circuits.map((circuit) => (
          <div className="circuit-row" key={circuit.id}>
            <span className="circuit-id mono"><Route size={15} /> {circuit.id}</span>
            <span className="circuit-path">
              {circuit.nodes.map((node) => node.name).join('  →  ')}
            </span>
          </div>
        ))}
        {!circuits.length && (
          <EmptyState icon={Route} title="Aucun circuit établi">
            Tor ouvre un circuit dès qu’un appareil du réseau demande une connexion.
          </EmptyState>
        )}
      </div>
    </Panel>
  )
}
