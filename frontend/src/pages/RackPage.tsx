import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArrowDownUp,
  Cable,
  CheckCircle2,
  CircleSlash,
  Gauge,
  Laptop,
  Plus,
  RefreshCw,
  Route,
  Search,
  Server,
  ServerCog,
  ShieldCheck,
  Sparkles,
  Trash2,
  Wifi,
} from 'lucide-react'
import { api } from '../api'
import { ConfirmDialog, Modal } from '../components/Modal'
import { Panel } from '../components/Panel'
import { Badge, EmptyState, LoadingPanel } from '../components/ui'
import type {
  RackBulkAnswer,
  RackCable,
  RackCableColor,
  RackEgress,
  RackFrame,
  RackNode,
  RackNodeRules,
  RackNodeStatus,
  RackPayload,
  RackProfile,
} from '../types'
import { CableInspector, RackCableLayer } from './RackCabling'
import { RackNodeSheet } from './RackNodeSheet'
import {
  DatacenterOperations,
  FabricPanel,
  summarizeFabric,
} from './RackOperations'
import { DEFAULT_RULES, EGRESS_LABELS, STATUS } from './rackShared'

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

/** Les filtres proposés au-dessus de la baie, dans l’ordre où ils inquiètent. */
const FILTERS: Array<{ id: 'all' | 'alert' | RackNodeStatus; label: string }> = [
  { id: 'all', label: 'Tous' },
  { id: 'alert', label: 'À surveiller' },
  { id: 'online', label: 'En ligne' },
  { id: 'offline', label: 'Injoignables' },
  { id: 'isolated', label: 'Isolés' },
]

const CABLE_COLORS: RackCableColor[] = ['cyan', 'green', 'violet', 'amber']

export function RackPage({ notify }: Props) {
  const [payload, setPayload] = useState<RackPayload>()
  const [error, setError] = useState('')
  const [selectedRack, setSelectedRack] = useState('')
  const [openNode, setOpenNode] = useState('')
  const [editingRack, setEditingRack] = useState<RackFrame | 'new'>()
  const [editingProfile, setEditingProfile] = useState<RackProfile | 'new'>()
  const [drafting, setDrafting] = useState(false)
  const [dragged, setDragged] = useState('')
  const [busy, setBusy] = useState(false)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<'all' | 'alert' | RackNodeStatus>('all')
  const [selection, setSelection] = useState<string[]>([])
  const [bulkProfile, setBulkProfile] = useState('')
  const [cabling, setCabling] = useState(false)
  const [cableStart, setCableStart] = useState<{ nodeId: string; port: number }>()
  const [selectedCable, setSelectedCable] = useState('')
  const rackCanvasRef = useRef<HTMLDivElement>(null)

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
  const profiles = payload?.profiles ?? []
  const discovered = payload?.discovered ?? []
  const current = racks.find((rack) => rack.id === selectedRack) ?? racks[0]
  const detail = nodes.find((node) => node.id === openNode)
  const currentCables = useMemo(
    () => (payload?.cables ?? []).filter((cable) => cable.rack_id === current?.id),
    [current?.id, payload?.cables],
  )

  useEffect(() => {
    setCableStart(undefined)
    setSelectedCable('')
  }, [current?.id])

  const matches = useCallback(
    (node: RackNode) => {
      const needle = query.trim().toLowerCase()
      if (needle) {
        const haystack = `${node.name} ${node.role} ${node.mac} ${node.address} ${node.notes}`
        if (!haystack.toLowerCase().includes(needle)) return false
      }
      if (filter === 'all') return true
      if (filter === 'alert') return node.alerts.length > 0
      return node.status === filter
    },
    [filter, query],
  )

  const visible = useMemo(() => nodes.filter(matches), [nodes, matches])
  const unracked = useMemo(
    () => visible.filter((node) => !node.rack_id || !node.position),
    [visible],
  )
  const filtering = filter !== 'all' || query.trim().length > 0

  // Une sélection qui survit à un nœud supprimé enverrait des actions dans le
  // vide: elle est recoupée avec ce que la baie contient réellement.
  const selected = useMemo(
    () => selection.filter((id) => nodes.some((node) => node.id === id)),
    [nodes, selection],
  )

  const counts = useMemo(
    () => ({
      online: nodes.filter((node) => node.status === 'online').length,
      isolated: nodes.filter((node) => node.status === 'isolated').length,
      unreachable: nodes.filter((node) => node.status === 'offline').length,
    }),
    [nodes],
  )

  const fabric = useMemo(() => summarizeFabric(nodes), [nodes])

  const alertEntries = useMemo(
    () =>
      nodes
        .flatMap((node) => node.alerts.map((alert) => ({ node, alert })))
        .sort((left, right) => {
          const rank = { danger: 0, warning: 1, info: 2 }
          return rank[left.alert.level] - rank[right.alert.level]
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

  /** Une action groupée réussit rarement partout: le compte rendu le dit. */
  const runBulk = useCallback(
    async (action: () => Promise<RackBulkAnswer>, verb: string) => {
      setBusy(true)
      try {
        const answer = await action()
        setPayload(answer.snapshot)
        if (answer.failures.length) {
          notify(
            `${verb} : ${answer.applied} appliqué${answer.applied > 1 ? 's' : ''}, ` +
              `${answer.failures.map((failure) => failure.name).join(', ')} en échec`,
            true,
          )
        } else {
          notify(`${verb} : ${answer.applied} nœud${answer.applied > 1 ? 's' : ''}`)
        }
      } catch (reason) {
        notify(reason instanceof Error ? reason.message : 'Action refusée', true)
      } finally {
        setBusy(false)
      }
    },
    [notify],
  )

  const place = (nodeId: string, position: number) => {
    if (!current) return
    void run(
      () => api.moveRackNode({ id: nodeId, rack_id: current.id, position }),
      `Nœud placé en U${position}`,
    )
  }

  const toggle = (nodeId: string) =>
    setSelection((previous) =>
      previous.includes(nodeId)
        ? previous.filter((id) => id !== nodeId)
        : [...previous, nodeId],
    )

  const connectPort = async (nodeId: string, port: number) => {
    if (!current || busy) return
    const existing = cableAt(currentCables, nodeId, port)
    if (existing) {
      setSelectedCable(existing.id)
      return
    }
    if (!cabling) setCabling(true)
    if (!cableStart) {
      setCableStart({ nodeId, port })
      return
    }
    if (cableStart.nodeId === nodeId) {
      notify('Choisissez un port sur un autre appareil', true)
      return
    }
    setBusy(true)
    try {
      const next = await api.createRackCable({
        rack_id: current.id,
        source_node_id: cableStart.nodeId,
        source_port: cableStart.port,
        target_node_id: nodeId,
        target_port: port,
        color: CABLE_COLORS[currentCables.length % CABLE_COLORS.length],
        speed: '1-gbps',
      })
      const previousIds = new Set(currentCables.map((cable) => cable.id))
      const created = next.cables.find((cable) => !previousIds.has(cable.id))
      setPayload(next)
      setCableStart(undefined)
      setSelectedCable(created?.id ?? '')
      notify('Câble réseau ajouté')
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Câblage impossible', true)
    } finally {
      setBusy(false)
    }
  }

  const removeCable = async (id: string) => {
    setBusy(true)
    try {
      setPayload(await api.removeRackCable(id))
      if (selectedCable === id) setSelectedCable('')
      notify('Câble retiré')
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Suppression impossible', true)
    } finally {
      setBusy(false)
    }
  }

  if (!payload && !error) return <LoadingPanel height={360} label="Chargement de la baie" />

  const testFabric = () => {
    const ids = fabric.remote.filter((node) => Boolean(node.address)).map((node) => node.id)
    if (!ids.length) {
      notify('Aucun agent distant enrôlé à interroger', true)
      return
    }
    void runBulk(() => api.bulkRackNodes('refresh', ids), 'Test du fabric')
  }

  return (
    <div className="page rack-page">
      <header className="rack-hero">
        <div className="page-title">
          <h1>Centre de données virtuel</h1>
          <p>Pilotez vos nœuds privés à travers Tor, sans exposer de port d’administration sur Internet.</p>
        </div>
        <div className="rack-hero-actions">
          <button className="button button-primary" onClick={() => setDrafting(true)}>
            <Plus size={16} /> Ajouter un nœud
          </button>
          <button
            className="button button-secondary"
            disabled={busy || fabric.enrolled === 0}
            onClick={testFabric}
          >
            <Gauge size={16} /> Tester le fabric
          </button>
          <button
            className={`button ${cabling ? 'button-primary' : 'button-secondary'}`}
            aria-pressed={cabling}
            disabled={!current}
            onClick={() => {
              setCabling((value) => !value)
              setCableStart(undefined)
            }}
          >
            <Cable size={16} /> {cabling ? 'Quitter le câblage' : 'Câbler'}
          </button>
        </div>
      </header>

      {error && <p className="global-warning" role="status">{error}</p>}

      <div className="rack-summary" aria-label="État de la baie">
        <RackSummaryItem icon={<ShieldCheck />} label="Nœuds en ligne" value={`${counts.online} / ${nodes.length}`} note={`${counts.isolated} isolé${counts.isolated > 1 ? 's' : ''}`} tone={counts.online === nodes.length && nodes.length ? 'good' : undefined} />
        <RackSummaryItem icon={<Route />} label="Couverture Tor" value={`${fabric.coverage} %`} note={`${fabric.torReady}/${fabric.remote.length} agents amorcés`} tone={fabric.coverage === 100 ? 'good' : 'warn'} />
        <RackSummaryItem icon={<CheckCircle2 />} label="Règles synchronisées" value={`${fabric.synchronized} / ${fabric.remote.length}`} note={`${fabric.authenticated} autorisation${fabric.authenticated > 1 ? 's' : ''} client`} tone={fabric.remote.length > 0 && fabric.syncRate === 100 ? 'good' : 'warn'} />
        <RackSummaryItem icon={<AlertTriangle />} label="Alertes" value={(payload?.health.warnings ?? 0) + (payload?.health.failures ?? 0)} note={`${counts.unreachable} injoignable${counts.unreachable > 1 ? 's' : ''}`} tone={payload?.health.failures ? 'bad' : payload?.health.warnings ? 'warn' : undefined} />
      </div>

      <Panel>
        {(racks.length > 0 || nodes.length > 0) && (
          <div className="rack-controlbar">
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
                    <span>
                      {rack.location || `${rack.units} U`}
                      {rack.alerts > 0 && <em className="rack-tab-alerts"> · {rack.alerts} ⚠</em>}
                    </span>
                  </button>
                ))}
              </div>
            )}
            {nodes.length > 0 && (
              <div className="rack-filters">
                <label className="rack-search">
                  <Search size={14} />
                  <input
                    value={query}
                    placeholder="Chercher un nom, un rôle, une adresse…"
                    aria-label="Chercher un nœud"
                    onChange={(event) => setQuery(event.target.value)}
                  />
                </label>
                <div className="rack-chips" role="group" aria-label="Filtrer les nœuds">
                  {FILTERS.map((item) => (
                    <button
                      key={item.id}
                      className={`rack-chip ${filter === item.id ? 'rack-chip-active' : ''}`}
                      aria-pressed={filter === item.id}
                      onClick={() => setFilter(item.id)}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <button className="button button-small button-secondary rack-new-frame" onClick={() => setEditingRack('new')}>
              <Plus size={14} /> Nouvelle baie
            </button>
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
          <div className="rack-workspace">
            <div className="rack-canvas-column">
              <div className="rack-frame-head">
                <span><strong>{current.name}</strong><small>{current.location || 'Emplacement non précisé'} · {currentCables.length} connexion{currentCables.length > 1 ? 's' : ''}</small></span>
                <div className="rack-actions">
                  <button
                    className="button button-small button-ghost"
                    disabled={busy || !current.occupied}
                    onClick={() =>
                      void run(() => api.arrangeRack(current.id), 'Baie rangée : les U sont contigus')
                    }
                  >
                    <ArrowDownUp size={14} /> Ranger
                  </button>
                  <button className="button button-small button-ghost" onClick={() => setEditingRack(current)}>
                    Modifier
                  </button>
                </div>
              </div>
              <div className={`rack-canvas ${cabling ? 'rack-canvas-cabling' : ''}`} ref={rackCanvasRef}>
                <RackCableLayer
                  canvasRef={rackCanvasRef}
                  cables={currentCables}
                  selected={selectedCable}
                  onSelect={setSelectedCable}
                />
                <ol className="rack-elevation" aria-label={`Emplacements de ${current.name}`}>
                  {Array.from({ length: current.units }, (_, index) => index + 1).map((unit) => {
                    const node = nodes.find((item) => item.rack_id === current.id && item.position === unit)
                    const dimmed = Boolean(node && filtering && !matches(node))
                    return (
                      <li
                        key={unit}
                        className={`rack-slot ${node ? 'rack-slot-filled' : ''} ${dimmed ? 'rack-slot-dimmed' : ''}`}
                        onDragOver={(event) => {
                          if (dragged) event.preventDefault()
                        }}
                        onDrop={(event) => {
                          event.preventDefault()
                          if (dragged) place(dragged, unit)
                          setDragged('')
                        }}
                      >
                        <span className="rack-unit rack-unit-left">U{unit}</span>
                        {node ? (
                          <NodeCard
                            node={node}
                            busy={busy}
                            selected={selected.includes(node.id)}
                            cables={currentCables}
                            cabling={cabling}
                            cableStart={cableStart}
                            onPort={(port) => void connectPort(node.id, port)}
                            onSelect={() => toggle(node.id)}
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
                          <EmptySlot
                            nodes={nodes.filter((item) => !item.rack_id || !item.position)}
                            busy={busy}
                            onPlace={(nodeId) => place(nodeId, unit)}
                          />
                        )}
                        <span className="rack-unit rack-unit-right">U{unit}</span>
                      </li>
                    )
                  })}
                </ol>
              </div>
              <CableLegend />
            </div>
            <div className="rack-vdc-rail">
              <FabricPanel fabric={fabric} onOpenNode={setOpenNode} />
              <CableInspector
                cables={currentCables}
                nodes={nodes}
                selected={selectedCable}
                cabling={cabling}
                start={cableStart}
                busy={busy}
                onSelect={setSelectedCable}
                onRemove={(id) => void removeCable(id)}
              />
            </div>
          </div>
        )}
      </Panel>

      <DatacenterOperations
        nodes={visible}
        alerts={alertEntries}
        busy={busy}
        onOpenNode={setOpenNode}
        onRefreshNode={(node) =>
          void run(() => api.refreshRackNode(node.id), `${node.name} interrogé`)
        }
      />

      {selected.length > 0 && (
        <div className="rack-bulk" role="region" aria-label="Actions groupées">
          <strong>{selected.length} nœud{selected.length > 1 ? 's' : ''} sélectionné{selected.length > 1 ? 's' : ''}</strong>
          <div className="rack-actions">
            <button
              className="button button-small button-secondary"
              disabled={busy}
              onClick={() => void runBulk(() => api.bulkRackNodes('isolate', selected), 'Isolement')}
            >
              <CircleSlash size={14} /> Isoler
            </button>
            <button
              className="button button-small button-secondary"
              disabled={busy}
              onClick={() => void runBulk(() => api.bulkRackNodes('allow', selected), 'Autorisation')}
            >
              <ShieldCheck size={14} /> Autoriser
            </button>
            <button
              className="button button-small button-ghost"
              disabled={busy}
              onClick={() => void runBulk(() => api.bulkRackNodes('refresh', selected), 'Interrogation')}
            >
              <RefreshCw size={14} /> Interroger
            </button>
            <button
              className="button button-small button-ghost"
              disabled={busy}
              onClick={() => void runBulk(() => api.bulkRackNodes('unrack', selected), 'Sortie de baie')}
            >
              Sortir de la baie
            </button>
            {profiles.length > 0 && (
              <>
                <select
                  value={bulkProfile}
                  aria-label="Profil à appliquer"
                  onChange={(event) => setBulkProfile(event.target.value)}
                >
                  <option value="">Profil…</option>
                  {profiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>{profile.name}</option>
                  ))}
                </select>
                <button
                  className="button button-small button-primary"
                  disabled={busy || !bulkProfile}
                  onClick={() =>
                    void runBulk(
                      () => api.bulkRackNodes('profile', selected, bulkProfile),
                      'Profil appliqué',
                    )
                  }
                >
                  Appliquer
                </button>
              </>
            )}
            <button className="button button-small button-ghost" onClick={() => setSelection([])}>
              Tout désélectionner
            </button>
          </div>
        </div>
      )}

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
                selected={selected.includes(node.id)}
                onSelect={() => toggle(node.id)}
                onOpen={() => setOpenNode(node.id)}
                onDragStart={() => setDragged(node.id)}
                onDragEnd={() => setDragged('')}
              />
            ))}
          </div>
        ) : (
          <EmptyState icon={ServerCog} title={filtering ? 'Aucun nœud ne correspond' : 'Tout est rangé'}>
            {filtering
              ? 'Aucun nœud hors baie ne correspond à cette recherche.'
              : 'Chaque nœud déclaré occupe un emplacement.'}
          </EmptyState>
        )}
      </Panel>

      <DiscoveryPanel
        discovered={discovered}
        rackId={current?.id ?? ''}
        busy={busy}
        onImport={(macs, rackId) =>
          void runBulk(() => api.importRackDevices(macs, rackId), 'Import')
        }
      />

      <ProfilesPanel
        profiles={profiles}
        maxProfiles={payload?.limits.max_profiles ?? 12}
        busy={busy}
        onEdit={(profile) => setEditingProfile(profile)}
        onRemove={(profile) =>
          void run(() => api.removeRackProfile(profile.id), `Profil « ${profile.name} » supprimé`)
        }
      />

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

      {editingProfile && (
        <ProfileEditor
          profile={editingProfile === 'new' ? undefined : editingProfile}
          onClose={() => setEditingProfile(undefined)}
          onSaved={async () => {
            setEditingProfile(undefined)
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
        <RackNodeSheet
          node={detail}
          racks={racks}
          profiles={profiles}
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
  selected,
  onSelect,
  onOpen,
  onDragStart,
  onDragEnd,
  onMove,
  onEject,
  cables = [],
  cabling = false,
  cableStart,
  onPort,
}: {
  node: RackNode
  busy: boolean
  selected: boolean
  onSelect: () => void
  onOpen: () => void
  onDragStart: () => void
  onDragEnd: () => void
  onMove?: (delta: number) => void
  onEject?: () => void
  cables?: RackCable[]
  cabling?: boolean
  cableStart?: { nodeId: string; port: number }
  onPort?: (port: number) => void
}) {
  const status = STATUS[node.status]
  const worst = node.alerts.find((alert) => alert.level === 'danger') ?? node.alerts[0]
  const ports = Array.from({ length: node.kind === 'local' ? 1 : 4 }, (_, index) => index + 1)
  return (
    <article
      className={`rack-node rack-node-${node.status} ${selected ? 'rack-node-selected' : ''}`}
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
    >
      <label className="rack-node-select">
        <input
          type="checkbox"
          checked={selected}
          aria-label={`Sélectionner ${node.name}`}
          onChange={onSelect}
        />
      </label>
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
          <small className="muted">
            {node.link?.ip || EGRESS_LABELS[node.rules.egress]}
          </small>
        </span>
      </button>
      {onPort && (
        <div className="rack-node-ports" aria-label={`Ports de ${node.name}`}>
          {ports.map((port) => {
            const cable = cableAt(cables, node.id, port)
            const peer = cable
              ? cable.source_node_id === node.id
                ? `${cable.target_name} port ${cable.target_port}`
                : `${cable.source_name} port ${cable.source_port}`
              : ''
            const starting = cableStart?.nodeId === node.id && cableStart.port === port
            return (
              <button
                key={port}
                type="button"
                data-rack-port={`${node.id}:${port}`}
                className={`rack-port ${cable ? `rack-port-connected rack-port-${cable.color} rack-port-${cable.status}` : ''} ${starting ? 'rack-port-start' : ''}`}
                aria-label={cable ? `Port ${port} de ${node.name}, connecté à ${peer}` : `Port ${port} libre de ${node.name}`}
                aria-pressed={starting || Boolean(cable)}
                disabled={busy}
                onClick={() => onPort(port)}
              >
                <span>{port}</span>
                <i aria-hidden="true" />
                {cabling && !cable && <em>libre</em>}
              </button>
            )
          })}
        </div>
      )}
      {worst && (
        <p className={`rack-node-alert rack-alert-${worst.level}`} role="status">
          {worst.message}
          {node.alerts.length > 1 && ` (+${node.alerts.length - 1})`}
        </p>
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

function cableAt(cables: RackCable[], nodeId: string, port: number): RackCable | undefined {
  return cables.find(
    (cable) =>
      (cable.source_node_id === nodeId && cable.source_port === port) ||
      (cable.target_node_id === nodeId && cable.target_port === port),
  )
}

function RackSummaryItem({
  icon,
  label,
  value,
  note,
  tone,
}: {
  icon: React.ReactNode
  label: string
  value: number | string
  note?: string
  tone?: 'good' | 'warn' | 'bad'
}) {
  return (
    <span className={`rack-summary-item ${tone ? `rack-summary-${tone}` : ''}`}>
      <i>{icon}</i><span><small>{label}</small><strong>{value}</strong>{note && <em>{note}</em>}</span>
    </span>
  )
}

function CableLegend() {
  return (
    <div className="rack-cable-legend" aria-label="Légende des câbles">
      <strong>Légende des câbles</strong>
      <span><i className="rack-legend-line rack-cable-bg-amber" /> Internet / WAN</span>
      <span><i className="rack-legend-line rack-cable-bg-green" /> Réseau local</span>
      <span><i className="rack-legend-line rack-cable-bg-cyan" /> Serveur</span>
      <span><i className="rack-legend-line rack-cable-bg-violet" /> Gestion</span>
    </div>
  )
}

/** Un emplacement libre propose les machines qui n’en ont pas, par leur nom. */
function EmptySlot({
  nodes,
  busy,
  onPlace,
}: {
  nodes: RackNode[]
  busy: boolean
  onPlace: (nodeId: string) => void
}) {
  if (!nodes.length) return <span className="rack-empty rack-empty-quiet">Emplacement libre</span>
  if (nodes.length === 1) {
    return (
      <button className="rack-empty" disabled={busy} onClick={() => onPlace(nodes[0].id)}>
        Placer {nodes[0].name}
      </button>
    )
  }
  return (
    <select
      className="rack-empty"
      value=""
      disabled={busy}
      aria-label="Placer une machine ici"
      onChange={(event) => event.target.value && onPlace(event.target.value)}
    >
      <option value="">Placer une machine…</option>
      {nodes.map((node) => (
        <option key={node.id} value={node.id}>{node.name}</option>
      ))}
    </select>
  )
}

function DiscoveryPanel({
  discovered,
  rackId,
  busy,
  onImport,
}: {
  discovered: RackPayload['discovered']
  rackId: string
  busy: boolean
  onImport: (macs: string[], rackId: string) => void
}) {
  const [chosen, setChosen] = useState<string[]>([])
  const available = discovered.map((device) => device.mac)
  const selected = chosen.filter((mac) => available.includes(mac))

  return (
    <Panel
      title={`Clients du Wi-Fi détectés (${discovered.length})`}
      subtitle="Ils sont déjà routés par OnionPi. Les ajouter à la baie leur donne une fiche, un emplacement et une feuille de règles."
      action={
        selected.length > 0 && (
          <button
            className="button button-small button-primary"
            disabled={busy}
            onClick={() => {
              onImport(selected, rackId)
              setChosen([])
            }}
          >
            Ajouter {selected.length} appareil{selected.length > 1 ? 's' : ''}
          </button>
        )
      }
    >
      {discovered.length ? (
        <ul className="rack-discovery">
          {discovered.map((device) => (
            <li key={device.mac}>
              <label>
                <input
                  type="checkbox"
                  checked={selected.includes(device.mac)}
                  aria-label={`Ajouter ${device.name} à la baie`}
                  onChange={() =>
                    setChosen((previous) =>
                      previous.includes(device.mac)
                        ? previous.filter((mac) => mac !== device.mac)
                        : [...previous, device.mac],
                    )
                  }
                />
                <span className="rack-discovery-identity">
                  <strong>{device.name}</strong>
                  <em className="mono">{device.mac}</em>
                </span>
                <span className="rack-discovery-meta">
                  <span className="mono muted">{device.ip || '—'}</span>
                  <Badge tone={device.online ? 'success' : 'neutral'} dot>
                    {device.online ? 'Présent' : 'Absent'}
                  </Badge>
                </span>
              </label>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState icon={Wifi} title="Rien de nouveau">
          Tous les appareils vus sur le Wi-Fi occupent déjà une fiche dans la baie.
        </EmptyState>
      )}
    </Panel>
  )
}

function ProfilesPanel({
  profiles,
  maxProfiles,
  busy,
  onEdit,
  onRemove,
}: {
  profiles: RackProfile[]
  maxProfiles: number
  busy: boolean
  onEdit: (profile: RackProfile | 'new') => void
  onRemove: (profile: RackProfile) => void
}) {
  return (
    <Panel
      title={`Profils de règles (${profiles.length}/${maxProfiles})`}
      subtitle="Une feuille de règles nommée, applicable à plusieurs machines d’un coup. Un profil n’exprime rien qu’une fiche ne puisse exprimer."
      action={
        <button
          className="button button-small button-secondary"
          disabled={busy || profiles.length >= maxProfiles}
          onClick={() => onEdit('new')}
        >
          <Plus size={14} /> Nouveau profil
        </button>
      }
    >
      {profiles.length ? (
        <ul className="rack-profiles">
          {profiles.map((profile) => (
            <li key={profile.id}>
              <div className="rack-profile-identity">
                <strong>{profile.name}</strong>
                <span className="muted">
                  {profile.rules.access === 'blocked' ? 'Isolé' : 'Autorisé'} ·{' '}
                  {EGRESS_LABELS[profile.rules.egress]}
                  {profile.rules.exit_country && ` · sortie ${profile.rules.exit_country}`}
                  {profile.rules.keep_open_ports.length > 0 &&
                    ` · ports ${profile.rules.keep_open_ports.join(', ')}`}
                </span>
              </div>
              <div className="rack-actions">
                <button className="button button-small button-ghost" onClick={() => onEdit(profile)}>
                  Modifier
                </button>
                <button
                  className="button button-small button-ghost"
                  disabled={busy}
                  onClick={() => onRemove(profile)}
                >
                  Supprimer
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState icon={Sparkles} title="Aucun profil">
          Créez-en un pour poser les mêmes règles sur toute une famille de machines.
        </EmptyState>
      )}
    </Panel>
  )
}

function ProfileEditor({
  profile,
  onClose,
  onSaved,
  notify,
}: {
  profile?: RackProfile
  onClose: () => void
  onSaved: () => Promise<void>
  notify: (message: string, error?: boolean) => void
}) {
  const [name, setName] = useState(profile?.name ?? '')
  const [rules, setRules] = useState<RackNodeRules>({ ...DEFAULT_RULES, ...profile?.rules })
  const [portsInput, setPortsInput] = useState((profile?.rules.keep_open_ports ?? DEFAULT_RULES.keep_open_ports).join(', '))
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState('')

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setFailure('')
    try {
      await api.saveRackProfile({
        id: profile?.id ?? '',
        name,
        rules: { ...rules, keep_open_ports: parseRackPorts(portsInput) },
      })
      notify(profile ? 'Profil modifié' : 'Profil créé')
      await onSaved()
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : 'Enregistrement impossible')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={profile ? `Profil ${profile.name}` : 'Nouveau profil'}
      description="Appliquer un profil écrit exactement les règles d’une fiche : il ne peut rien demander de plus."
      icon={<Sparkles size={22} />}
      onClose={onClose}
      onSubmit={submit}
      actions={
        <>
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
          Accès
          <select
            value={rules.access}
            onChange={(event) => setRules({ ...rules, access: event.target.value as RackNodeRules['access'] })}
          >
            <option value="allowed">Autorisé</option>
            <option value="blocked">Isolé</option>
          </select>
        </label>
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
          Pays de sortie
          <input
            value={rules.exit_country}
            maxLength={2}
            placeholder="SE"
            onChange={(event) =>
              setRules({ ...rules, exit_country: event.target.value.toUpperCase().slice(0, 2) })
            }
          />
        </label>
        <label>
          Ports laissés joignables
          <input
            value={portsInput}
            placeholder="22"
            inputMode="numeric"
            onChange={(event) => setPortsInput(event.target.value)}
          />
        </label>
        <p className="prose">
          Les règles de sortie et de ports ne concernent que les machines distantes : sur un
          client du Wi-Fi, seul l’accès est repris.
        </p>
        {failure && <div className="form-error" role="alert">{failure}</div>}
      </div>
    </Modal>
  )
}

function parseRackPorts(value: string): number[] {
  return value
    .split(/[\s,;]+/)
    .map((item) => Number(item))
    .filter((item) => Number.isInteger(item) && item > 0 && item <= 65535)
    .slice(0, 8)
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
