import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import {
  Ban,
  CircleSlash,
  Download,
  KeyRound,
  Laptop,
  Plus,
  RefreshCw,
  Server,
  ServerCog,
  ShieldCheck,
  Terminal,
  Trash2,
} from 'lucide-react'
import { api } from '../api'
import { ConfirmDialog, Modal } from '../components/Modal'
import { Panel } from '../components/Panel'
import { Badge, EmptyState, LoadingPanel, StatCard } from '../components/ui'
import { formatUptime, relativeTime } from '../lib'
import type {
  RackEgress,
  RackEnrollment,
  RackFrame,
  RackNode,
  RackNodeRules,
  RackNodeStatus,
  RackPayload,
} from '../types'

const STATUS: Record<RackNodeStatus, { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' }> = {
  online: { label: 'En ligne', tone: 'success' },
  offline: { label: 'Injoignable', tone: 'danger' },
  isolated: { label: 'Isolé', tone: 'warning' },
  pending: { label: 'En attente', tone: 'neutral' },
  unknown: { label: 'Jamais vu', tone: 'neutral' },
}

const EGRESS_LABELS: Record<RackEgress, string> = {
  'tor-only': 'Tor uniquement',
  direct: 'Sortie directe',
}

const DEFAULT_RULES: RackNodeRules = {
  access: 'allowed',
  egress: 'tor-only',
  exit_country: '',
  keep_open_ports: [22],
  schedule: null,
}

type Props = {
  notify: (message: string, error?: boolean) => void
}

type Draft = {
  kind: 'local' | 'remote'
  name: string
  role: string
  mac: string
  onion: string
  agent_port: number
  notes: string
}

const EMPTY_DRAFT: Draft = {
  kind: 'remote',
  name: '',
  role: '',
  mac: '',
  onion: '',
  agent_port: 9080,
  notes: '',
}

export function RackPage({ notify }: Props) {
  const [payload, setPayload] = useState<RackPayload>()
  const [error, setError] = useState('')
  const [selectedRack, setSelectedRack] = useState('')
  const [openNode, setOpenNode] = useState('')
  const [editingRack, setEditingRack] = useState<RackFrame | 'new'>()
  const [drafting, setDrafting] = useState(false)
  const [dragged, setDragged] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setPayload(await api.rack())
      setError('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Baie indisponible')
    }
  }, [])

  useEffect(() => {
    void load()
    // La baie bouge lentement: un nœud distant n’est sondé qu’une fois par
    // minute côté serveur, inutile de le demander plus souvent qu’ici.
    const timer = window.setInterval(() => void load(), 20_000)
    return () => window.clearInterval(timer)
  }, [load])

  const racks = payload?.racks ?? []
  const nodes = payload?.nodes ?? []
  const current = racks.find((rack) => rack.id === selectedRack) ?? racks[0]
  const unracked = nodes.filter((node) => !node.rack_id || !node.position)
  const detail = nodes.find((node) => node.id === openNode)

  const counts = useMemo(
    () => ({
      online: nodes.filter((node) => node.status === 'online').length,
      isolated: nodes.filter((node) => node.status === 'isolated').length,
      unreachable: nodes.filter((node) => node.status === 'offline').length,
    }),
    [nodes],
  )

  const run = useCallback(
    async (action: () => Promise<unknown>, message: string) => {
      setBusy(true)
      try {
        await action()
        notify(message)
        await load()
        return true
      } catch (reason) {
        notify(reason instanceof Error ? reason.message : 'Action refusée', true)
        return false
      } finally {
        setBusy(false)
      }
    },
    [load, notify],
  )

  const place = (nodeId: string, position: number) => {
    if (!current) return
    void run(
      () => api.moveRackNode({ id: nodeId, rack_id: current.id, position }),
      `Nœud placé en U${position}`,
    )
  }

  if (!payload && !error) return <LoadingPanel height={360} label="Chargement de la baie" />

  return (
    <div className="page rack-page">
      <div className="page-title">
        <h1>Baie virtuelle</h1>
        <p>
          Une salle machine dessinée à votre échelle. Les clients du Wi-Fi et les machines
          distantes — un VPS, un serveur — occupent des emplacements, portent des règles, et
          sortent par Tor. Rien de ce qui est affiché ici n’est décoratif : une règle posée sur un
          emplacement est appliquée par le pare-feu, ici ou là-bas.
        </p>
      </div>

      {error && <p className="global-warning" role="status">{error}</p>}

      <div className="stat-grid">
        <StatCard icon={ShieldCheck} label="Nœuds en ligne" value={counts.online} tone="good" />
        <StatCard icon={CircleSlash} label="Nœuds isolés" value={counts.isolated} tone={counts.isolated ? 'warn' : undefined} />
        <StatCard icon={Ban} label="Injoignables" value={counts.unreachable} tone={counts.unreachable ? 'bad' : undefined} />
        <StatCard icon={Server} label="Baies" value={racks.length} foot={`${nodes.length} nœud${nodes.length > 1 ? 's' : ''} au total`} />
      </div>

      <Panel
        title="Baies"
        subtitle={current ? `${current.occupied} / ${current.units} U occupés` : undefined}
        action={
          <div className="rack-toolbar">
            <button className="button button-small button-secondary" onClick={() => setEditingRack('new')}>
              <Plus size={14} /> Nouvelle baie
            </button>
            <button className="button button-small button-primary" onClick={() => setDrafting(true)}>
              <Plus size={14} /> Ajouter un nœud
            </button>
          </div>
        }
      >
        {racks.length > 0 && (
          <div className="rack-tabs" role="tablist" aria-label="Baies">
            {racks.map((rack) => (
              <button
                key={rack.id}
                role="tab"
                aria-selected={rack.id === current?.id}
                className={`rack-tab ${rack.id === current?.id ? 'rack-tab-active' : ''}`}
                onClick={() => setSelectedRack(rack.id)}
              >
                <strong>{rack.name}</strong>
                <span>{rack.location || `${rack.units} U`}</span>
              </button>
            ))}
          </div>
        )}

        {!current && (
          <EmptyState
            icon={Server}
            title="Aucune baie"
            action={
              <button className="button button-primary" onClick={() => setEditingRack('new')}>
                <Plus size={16} /> Créer une baie
              </button>
            }
          >
            Une baie n’est qu’un cadre : créez-la, puis glissez-y les machines à surveiller.
          </EmptyState>
        )}

        {current && (
          <>
            <div className="rack-frame-head">
              <span className="muted">{current.location || 'Emplacement non précisé'}</span>
              <div>
                <button className="button button-small button-ghost" onClick={() => setEditingRack(current)}>
                  Modifier la baie
                </button>
              </div>
            </div>
            <ol className="rack-elevation" aria-label={`Emplacements de ${current.name}`}>
              {Array.from({ length: current.units }, (_, index) => index + 1).map((unit) => {
                const node = nodes.find((item) => item.rack_id === current.id && item.position === unit)
                return (
                  <li
                    key={unit}
                    className={`rack-slot ${node ? 'rack-slot-filled' : ''}`}
                    onDragOver={(event) => {
                      if (dragged) event.preventDefault()
                    }}
                    onDrop={(event) => {
                      event.preventDefault()
                      if (dragged) place(dragged, unit)
                      setDragged('')
                    }}
                  >
                    <span className="rack-unit">U{unit}</span>
                    {node ? (
                      <NodeCard
                        node={node}
                        busy={busy}
                        onOpen={() => setOpenNode(node.id)}
                        onDragStart={() => setDragged(node.id)}
                        onDragEnd={() => setDragged('')}
                        onMove={(delta) => {
                          const target = unit + delta
                          if (target >= 1 && target <= current.units) place(node.id, target)
                        }}
                        onEject={() =>
                          void run(
                            () => api.moveRackNode({ id: node.id, rack_id: '', position: 0 }),
                            `${node.name} retiré de la baie`,
                          )
                        }
                      />
                    ) : (
                      <button
                        className="rack-empty"
                        disabled={!unracked.length || busy}
                        onClick={() => unracked[0] && place(unracked[0].id, unit)}
                      >
                        {unracked.length ? `Placer ${unracked[0].name}` : 'Emplacement libre'}
                      </button>
                    )}
                  </li>
                )
              })}
            </ol>
          </>
        )}
      </Panel>

      <Panel
        title={`Nœuds hors baie (${unracked.length})`}
        subtitle="Ils gardent leurs règles : un nœud sans emplacement reste surveillé."
      >
        {unracked.length ? (
          <div className="rack-pool">
            {unracked.map((node) => (
              <NodeCard
                key={node.id}
                node={node}
                busy={busy}
                onOpen={() => setOpenNode(node.id)}
                onDragStart={() => setDragged(node.id)}
                onDragEnd={() => setDragged('')}
              />
            ))}
          </div>
        ) : (
          <EmptyState icon={ServerCog} title="Tout est rangé">
            Chaque nœud déclaré occupe un emplacement.
          </EmptyState>
        )}
      </Panel>

      {editingRack && (
        <RackEditor
          rack={editingRack === 'new' ? undefined : editingRack}
          maxUnits={payload?.limits.max_units ?? 42}
          defaultUnits={payload?.limits.default_units ?? 12}
          onClose={() => setEditingRack(undefined)}
          onSaved={async () => {
            setEditingRack(undefined)
            await load()
          }}
          onRemoved={async () => {
            setEditingRack(undefined)
            setSelectedRack('')
            await load()
          }}
          notify={notify}
        />
      )}

      {drafting && (
        <NodeCreator
          onClose={() => setDrafting(false)}
          onCreated={async (node) => {
            setDrafting(false)
            await load()
            setOpenNode(node.id)
          }}
          notify={notify}
        />
      )}

      {detail && (
        <NodeDetail
          node={detail}
          racks={racks}
          onClose={() => setOpenNode('')}
          onChanged={load}
          notify={notify}
        />
      )}
    </div>
  )
}

function NodeCard({
  node,
  busy,
  onOpen,
  onDragStart,
  onDragEnd,
  onMove,
  onEject,
}: {
  node: RackNode
  busy: boolean
  onOpen: () => void
  onDragStart: () => void
  onDragEnd: () => void
  onMove?: (delta: number) => void
  onEject?: () => void
}) {
  const status = STATUS[node.status]
  const drifted = Boolean(node.state.policy && node.state.policy.digest !== node.policy_digest)
  return (
    <article
      className={`rack-node rack-node-${node.status}`}
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
    >
      <button
        className="rack-node-open"
        aria-label={`Ouvrir la fiche de ${node.name}`}
        onClick={onOpen}
      >
        <span className="rack-node-icon">{node.kind === 'remote' ? <Server size={18} /> : <Laptop size={18} />}</span>
        <span className="rack-node-identity">
          <strong>{node.name}</strong>
          <em>{node.role || (node.kind === 'remote' ? 'Machine distante' : 'Client du Wi-Fi')}</em>
        </span>
        <span className="rack-node-meta">
          <Badge tone={status.tone} dot>{status.label}</Badge>
          <small className="muted">{EGRESS_LABELS[node.rules.egress]}</small>
        </span>
      </button>
      {drifted && (
        <p className="rack-node-drift" role="status">Règles en attente d’application sur le nœud.</p>
      )}
      {(onMove || onEject) && (
        <div className="rack-node-controls">
          {onMove && (
            <>
              <button className="icon-button" aria-label="Monter d’un U" disabled={busy} onClick={() => onMove(-1)}>↑</button>
              <button className="icon-button" aria-label="Descendre d’un U" disabled={busy} onClick={() => onMove(1)}>↓</button>
            </>
          )}
          {onEject && (
            <button className="button button-small button-ghost" disabled={busy} onClick={onEject}>
              Retirer
            </button>
          )}
        </div>
      )}
    </article>
  )
}

function RackEditor({
  rack,
  maxUnits,
  defaultUnits,
  onClose,
  onSaved,
  onRemoved,
  notify,
}: {
  rack?: RackFrame
  maxUnits: number
  defaultUnits: number
  onClose: () => void
  onSaved: () => Promise<void>
  onRemoved: () => Promise<void>
  notify: (message: string, error?: boolean) => void
}) {
  const [name, setName] = useState(rack?.name ?? '')
  const [location, setLocation] = useState(rack?.location ?? '')
  const [units, setUnits] = useState(rack?.units ?? defaultUnits)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState('')
  const [confirming, setConfirming] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setFailure('')
    try {
      if (rack) await api.updateRack({ id: rack.id, name, location, units })
      else await api.createRack({ name, location, units })
      notify(rack ? 'Baie modifiée' : 'Baie créée')
      await onSaved()
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : 'Enregistrement impossible')
    } finally {
      setBusy(false)
    }
  }

  if (confirming && rack) {
    return (
      <ConfirmDialog
        title={`Supprimer ${rack.name} ?`}
        description="Les machines qu’elle contient sont conservées : elles retournent hors baie, avec leurs règles."
        confirmLabel="Supprimer la baie"
        icon={<Trash2 size={22} />}
        busy={busy}
        onClose={() => setConfirming(false)}
        onConfirm={async () => {
          setBusy(true)
          try {
            await api.removeRack(rack.id)
            notify('Baie supprimée')
            await onRemoved()
          } catch (reason) {
            notify(reason instanceof Error ? reason.message : 'Suppression refusée', true)
          } finally {
            setBusy(false)
          }
        }}
      />
    )
  }

  return (
    <Modal
      title={rack ? `Baie ${rack.name}` : 'Nouvelle baie'}
      description="Le nombre d’unités fixe la hauteur du cadre. Il se réduit tant qu’aucun nœud n’occupe les U supprimés."
      icon={<Server size={22} />}
      onClose={onClose}
      onSubmit={submit}
      actions={
        <>
          {rack && (
            <button type="button" className="button button-danger" onClick={() => setConfirming(true)}>
              Supprimer
            </button>
          )}
          <button type="button" className="button button-secondary" onClick={onClose}>Annuler</button>
          <button className="button button-primary" disabled={busy}>{busy ? 'Enregistrement…' : 'Enregistrer'}</button>
        </>
      }
    >
      <div className="settings-form">
        <label>
          Nom
          <input value={name} maxLength={48} required onChange={(event) => setName(event.target.value)} />
        </label>
        <label>
          Emplacement
          <input value={location} maxLength={48} placeholder="Salon, bureau, hébergeur…" onChange={(event) => setLocation(event.target.value)} />
        </label>
        <label>
          Hauteur (U)
          <input
            type="number"
            min={1}
            max={maxUnits}
            value={units}
            onChange={(event) => setUnits(Number(event.target.value))}
          />
        </label>
        {failure && <div className="form-error" role="alert">{failure}</div>}
      </div>
    </Modal>
  )
}

function NodeCreator({
  onClose,
  onCreated,
  notify,
}: {
  onClose: () => void
  onCreated: (node: RackNode) => Promise<void>
  notify: (message: string, error?: boolean) => void
}) {
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState('')

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setFailure('')
    try {
      const node = await api.createRackNode(draft)
      notify(`${node.name} ajouté à la baie`)
      await onCreated(node)
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : 'Création impossible')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title="Ajouter un nœud"
      description="Un client du Wi-Fi est identifié par son adresse MAC. Une machine distante est jointe par son service onion, jamais par une adresse IP."
      icon={<ServerCog size={22} />}
      onClose={onClose}
      onSubmit={submit}
      actions={
        <>
          <button type="button" className="button button-secondary" onClick={onClose}>Annuler</button>
          <button className="button button-primary" disabled={busy}>{busy ? 'Création…' : 'Ajouter'}</button>
        </>
      }
    >
      <div className="settings-form">
        <fieldset className="rack-kind">
          <legend>Type de nœud</legend>
          {(['remote', 'local'] as const).map((kind) => (
            <label key={kind} className={draft.kind === kind ? 'rack-kind-active' : ''}>
              <input
                type="radio"
                name="kind"
                checked={draft.kind === kind}
                onChange={() => setDraft({ ...draft, kind })}
              />
              {kind === 'remote' ? 'Machine distante (agent)' : 'Client du Wi-Fi'}
            </label>
          ))}
        </fieldset>
        <label>
          Nom
          <input value={draft.name} maxLength={48} required onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
        </label>
        <label>
          Rôle
          <input
            value={draft.role}
            maxLength={32}
            placeholder="Relais, sauvegardes, poste de travail…"
            onChange={(event) => setDraft({ ...draft, role: event.target.value })}
          />
        </label>
        {draft.kind === 'local' ? (
          <label>
            Adresse MAC
            <input
              value={draft.mac}
              maxLength={17}
              required
              placeholder="aa:bb:cc:dd:ee:ff"
              onChange={(event) => setDraft({ ...draft, mac: event.target.value })}
            />
          </label>
        ) : (
          <>
            <label>
              Adresse onion du nœud
              <input
                value={draft.onion}
                maxLength={80}
                placeholder="Affichée par l’installateur, après coup"
                onChange={(event) => setDraft({ ...draft, onion: event.target.value })}
              />
            </label>
            <label>
              Port de l’agent
              <input
                type="number"
                min={1}
                max={65535}
                value={draft.agent_port}
                onChange={(event) => setDraft({ ...draft, agent_port: Number(event.target.value) })}
              />
            </label>
            <p className="prose">
              Créez le nœud d’abord : la fiche vous donnera la commande d’installation, et
              l’installateur vous rendra l’adresse onion à recopier ici.
            </p>
          </>
        )}
        {failure && <div className="form-error" role="alert">{failure}</div>}
      </div>
    </Modal>
  )
}

function NodeDetail({
  node,
  racks,
  onClose,
  onChanged,
  notify,
}: {
  node: RackNode
  racks: RackFrame[]
  onClose: () => void
  onChanged: () => Promise<void>
  notify: (message: string, error?: boolean) => void
}) {
  const [rules, setRules] = useState<RackNodeRules>({ ...DEFAULT_RULES, ...node.rules })
  const [identity, setIdentity] = useState({
    name: node.name,
    role: node.role,
    onion: node.address,
    agent_port: node.agent_port,
    notes: node.notes,
  })
  const [enrollment, setEnrollment] = useState<RackEnrollment>()
  const [journal, setJournal] = useState<string[]>()
  const [busy, setBusy] = useState(false)
  const [confirming, setConfirming] = useState(false)

  const act = async (action: () => Promise<unknown>, message: string) => {
    setBusy(true)
    try {
      await action()
      notify(message)
      await onChanged()
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Action refusée', true)
    } finally {
      setBusy(false)
    }
  }

  if (confirming) {
    return (
      <ConfirmDialog
        title={`Retirer ${node.name} ?`}
        description="La fiche et ses règles sont supprimées. L’agent installé sur la machine, lui, reste en place : désinstallez-le depuis le nœud."
        confirmLabel="Retirer le nœud"
        icon={<Trash2 size={22} />}
        busy={busy}
        onClose={() => setConfirming(false)}
        onConfirm={async () => {
          await act(() => api.removeRackNode(node.id), `${node.name} retiré`)
          onClose()
        }}
      />
    )
  }

  const status = STATUS[node.status]
  return (
    <Modal
      title={node.name}
      description={node.address || node.mac || 'Adresse non renseignée'}
      icon={node.kind === 'remote' ? <Server size={22} /> : <Laptop size={22} />}
      onClose={onClose}
      actions={
        <>
          <button type="button" className="button button-danger" onClick={() => setConfirming(true)}>
            Retirer
          </button>
          <button type="button" className="button button-secondary" onClick={onClose}>Fermer</button>
        </>
      }
    >
      <div className="rack-detail">
        <div className="rack-detail-head">
          <Badge tone={status.tone} dot>{status.label}</Badge>
          {node.kind === 'remote' && node.client_auth && (
            <Badge tone="success"><KeyRound size={13} /> Autorisation client active</Badge>
          )}
          {node.last_seen > 0 && <span className="muted">Vu {relativeTime(node.last_seen).toLowerCase()}</span>}
        </div>
        {node.last_error && <div className="form-error" role="alert">{node.last_error}</div>}

        {node.state.agent_version && (
          <dl className="rack-readout">
            <div><dt>Agent</dt><dd>{node.state.agent_version}</dd></div>
            <div><dt>Hôte</dt><dd className="mono">{node.state.hostname ?? '—'}</dd></div>
            <div><dt>Uptime</dt><dd>{formatUptime(node.state.uptime_seconds ?? 0)}</dd></div>
            <div><dt>Charge</dt><dd className="tabular">{node.state.load ?? '—'}</dd></div>
            <div><dt>Mémoire</dt><dd className="tabular">{node.state.memory_percent ?? 0} %</dd></div>
            <div><dt>Disque</dt><dd className="tabular">{node.state.storage_percent ?? 0} %</dd></div>
            <div><dt>Tor</dt><dd>{node.state.tor?.connected ? 'Connecté' : `Bootstrap ${node.state.tor?.bootstrap ?? 0} %`}</dd></div>
          </dl>
        )}

        <section className="rack-section">
          <h3>Règles</h3>
          <div className="settings-form">
            <label>
              Accès
              <select
                value={rules.access}
                onChange={(event) => setRules({ ...rules, access: event.target.value as RackNodeRules['access'] })}
              >
                <option value="allowed">Autorisé</option>
                <option value="blocked">Isolé</option>
              </select>
            </label>
            {node.kind === 'remote' && (
              <>
                <label>
                  Sortie réseau
                  <select
                    value={rules.egress}
                    onChange={(event) => setRules({ ...rules, egress: event.target.value as RackEgress })}
                  >
                    <option value="tor-only">Tor uniquement</option>
                    <option value="direct">Directe (dérogation)</option>
                  </select>
                </label>
                <label>
                  Ports laissés joignables
                  <input
                    value={rules.keep_open_ports.join(', ')}
                    placeholder="22"
                    onChange={(event) =>
                      setRules({
                        ...rules,
                        keep_open_ports: event.target.value
                          .split(/[\s,;]+/)
                          .map((item) => Number(item))
                          .filter((item) => Number.isInteger(item) && item > 0 && item <= 65535)
                          .slice(0, 8),
                      })
                    }
                  />
                </label>
                <p className="prose">
                  En sortie « Tor uniquement », tout ce qui ne passe pas par Tor est jeté sur le
                  nœud, <code>apt</code> compris. Gardez au moins un port joignable : un serveur
                  distant sans porte d’entrée ne se répare pas.
                </p>
              </>
            )}
            <div className="rack-actions">
              <button
                className="button button-primary button-small"
                disabled={busy}
                onClick={() => void act(() => api.setRackNodeRules(node.id, rules), 'Règles enregistrées')}
              >
                Appliquer les règles
              </button>
            </div>
          </div>
        </section>

        {node.kind === 'remote' && (
          <>
            <section className="rack-section">
              <h3>Identité</h3>
              <div className="settings-form">
                <label>
                  Nom
                  <input value={identity.name} maxLength={48} onChange={(event) => setIdentity({ ...identity, name: event.target.value })} />
                </label>
                <label>
                  Rôle
                  <input value={identity.role} maxLength={32} onChange={(event) => setIdentity({ ...identity, role: event.target.value })} />
                </label>
                <label>
                  Adresse onion
                  <input
                    value={identity.onion}
                    maxLength={80}
                    placeholder="Rendue par l’installateur"
                    onChange={(event) => setIdentity({ ...identity, onion: event.target.value })}
                  />
                </label>
                <div className="rack-actions">
                  <button
                    className="button button-small button-secondary"
                    disabled={busy}
                    onClick={() =>
                      void act(
                        () => api.updateRackNode({ id: node.id, ...identity, notes: identity.notes }),
                        'Fiche enregistrée',
                      )
                    }
                  >
                    Enregistrer la fiche
                  </button>
                  <button
                    className="button button-small button-ghost"
                    disabled={busy || !node.onion}
                    onClick={() => void act(() => api.refreshRackNode(node.id), 'Nœud interrogé')}
                  >
                    <RefreshCw size={14} /> Interroger
                  </button>
                </div>
              </div>
            </section>

            <section className="rack-section">
              <h3>Actions</h3>
              <div className="rack-actions">
                <button
                  className="button button-small button-secondary"
                  disabled={busy || !node.onion}
                  onClick={() => void act(() => api.runRackNodeAction(node.id, 'new-identity'), 'Nouvelle identité demandée')}
                >
                  Nouvelle identité Tor
                </button>
                <button
                  className="button button-small button-secondary"
                  disabled={busy || !node.onion}
                  onClick={() => void act(() => api.runRackNodeAction(node.id, 'restart-tor'), 'Redémarrage de Tor demandé')}
                >
                  Redémarrer Tor
                </button>
                <button
                  className="button button-small button-ghost"
                  disabled={busy || !node.onion}
                  onClick={async () => {
                    setBusy(true)
                    try {
                      const answer = await api.runRackNodeAction(node.id, 'journal', 'tor')
                      setJournal((answer.result.lines as string[]) ?? [])
                    } catch (reason) {
                      notify(reason instanceof Error ? reason.message : 'Journal indisponible', true)
                    } finally {
                      setBusy(false)
                    }
                  }}
                >
                  <Terminal size={14} /> Journal de Tor
                </button>
              </div>
              {journal && (
                <pre className="rack-journal" aria-label="Journal du nœud">{journal.join('\n') || 'Journal vide'}</pre>
              )}
            </section>

            <section className="rack-section">
              <h3>Enrôlement</h3>
              <p className="prose">
                Le jeton et la clé sont dérivés du secret de la baie : ils ne sont stockés nulle
                part et peuvent être réaffichés. Le renouvellement invalide immédiatement les
                anciens, et l’agent doit alors être réinstallé avec les nouveaux.
              </p>
              <div className="rack-actions">
                <button
                  className="button button-small button-secondary"
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true)
                    try {
                      setEnrollment(await api.rackNodeEnrollment(node.id))
                    } catch (reason) {
                      notify(reason instanceof Error ? reason.message : 'Jeton indisponible', true)
                    } finally {
                      setBusy(false)
                    }
                  }}
                >
                  <KeyRound size={14} /> Afficher la commande
                </button>
                <a className="button button-small button-ghost" href="/api/v1/rack/agent-bundle" download>
                  <Download size={14} /> Télécharger l’agent
                </a>
                <button
                  className="button button-small button-ghost"
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true)
                    try {
                      setEnrollment(await api.rotateRackNodeToken(node.id))
                      notify('Jeton renouvelé : réinstallez l’agent')
                      await onChanged()
                    } catch (reason) {
                      notify(reason instanceof Error ? reason.message : 'Renouvellement refusé', true)
                    } finally {
                      setBusy(false)
                    }
                  }}
                >
                  Renouveler le jeton
                </button>
              </div>
              {enrollment && (
                <pre className="rack-enrollment" aria-label="Commande d’installation">{enrollment.command}</pre>
              )}
            </section>
          </>
        )}

        <section className="rack-section">
          <h3>Emplacement</h3>
          <div className="rack-actions">
            <select
              value={node.rack_id}
              disabled={busy}
              onChange={(event) =>
                void act(
                  () =>
                    api.moveRackNode({
                      id: node.id,
                      rack_id: event.target.value,
                      position: event.target.value ? Math.max(1, node.position) : 0,
                    }),
                  'Nœud déplacé',
                )
              }
            >
              <option value="">Hors baie</option>
              {racks.map((rack) => (
                <option key={rack.id} value={rack.id}>{rack.name}</option>
              ))}
            </select>
            {node.rack_id && <span className="muted">Emplacement U{node.position}</span>}
          </div>
        </section>
      </div>
    </Modal>
  )
}
