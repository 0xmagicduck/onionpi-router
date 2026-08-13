import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Apple,
  Check,
  Copy,
  Download,
  KeyRound,
  Laptop,
  Monitor,
  Power,
  RefreshCw,
  Server,
  Terminal,
  Trash2,
} from 'lucide-react'
import { api } from '../api'
import { ConfirmDialog, Modal } from '../components/Modal'
import { Badge } from '../components/ui'
import { formatBytes, formatUptime, relativeTime } from '../lib'
import type {
  RackEgress,
  RackEnrollment,
  RackFrame,
  RackHistory,
  RackNode,
  RackNodeRules,
  RackProfile,
  RackSample,
} from '../types'
import { MeshKeyActions, MeshRulesSection } from './RackMesh'
import { DEFAULT_RULES, STATUS } from './rackShared'

/** Services dont le journal a un sens à distance. Rien d’autre n’est proposé :
 *  l’agent revalide le nom de son côté, mais une liste courte évite d’envoyer
 *  une requête qui sera refusée. */
const JOURNAL_UNITS = [
  { id: 'tor', label: 'Tor' },
  { id: 'onionpi-node-agent', label: 'Agent OnionPi' },
  { id: 'ssh', label: 'SSH' },
]

type InstallPlatform = keyof RackEnrollment['commands']

const INSTALL_PLATFORMS: Array<{
  id: InstallPlatform
  label: string
  detail: string
  icon: typeof Terminal
}> = [
  { id: 'linux', label: 'Linux', detail: 'Debian, Ubuntu et Raspberry Pi OS', icon: Terminal },
  { id: 'macos', label: 'macOS', detail: 'TCP et DNS transparents via Tor', icon: Apple },
  { id: 'windows', label: 'Windows', detail: 'Agent distant, sortie directe', icon: Monitor },
]

export function RackNodeSheet({
  node,
  racks,
  profiles,
  peers,
  meshLocked,
  onClose,
  onChanged,
  notify,
}: {
  node: RackNode
  racks: RackFrame[]
  profiles: RackProfile[]
  /** Les autres nœuds distants, seules destinations qu’une redirection peut viser. */
  peers: RackNode[]
  meshLocked: boolean
  onClose: () => void
  onChanged: () => Promise<void>
  notify: (message: string, error?: boolean) => void
}) {
  const [rules, setRules] = useState<RackNodeRules>({ ...DEFAULT_RULES, ...node.rules })
  const [portsInput, setPortsInput] = useState(node.rules.keep_open_ports.join(', '))
  const [identity, setIdentity] = useState({
    name: node.name,
    role: node.role,
    onion: node.address,
    agent_port: node.agent_port,
    notes: node.notes,
  })
  const [enrollment, setEnrollment] = useState<RackEnrollment>()
  const [installPlatform, setInstallPlatform] = useState<InstallPlatform>('linux')
  const [commandCopied, setCommandCopied] = useState(false)
  const [tokenCopied, setTokenCopied] = useState(false)
  const [journal, setJournal] = useState<string[]>()
  const [journalUnit, setJournalUnit] = useState('tor')
  const [history, setHistory] = useState<RackHistory>()
  const [busy, setBusy] = useState(false)
  const [confirming, setConfirming] = useState<'remove' | 'reboot'>()

  const rack = racks.find((frame) => frame.id === node.rack_id)

  useEffect(() => {
    let alive = true
    void api
      .rackNodeHistory(node.id)
      .then((answer) => alive && setHistory(answer))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [node.id, node.last_seen])

  const act = useCallback(
    async (action: () => Promise<unknown>, message: string) => {
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
    },
    [notify, onChanged],
  )

  if (confirming === 'remove') {
    return (
      <ConfirmDialog
        title={`Retirer ${node.name} ?`}
        description="La fiche et ses règles sont supprimées. L’agent installé sur la machine, lui, reste en place : désinstallez-le depuis le nœud."
        confirmLabel="Retirer le nœud"
        icon={<Trash2 size={22} />}
        busy={busy}
        onClose={() => setConfirming(undefined)}
        onConfirm={async () => {
          await act(() => api.removeRackNode(node.id), `${node.name} retiré`)
          onClose()
        }}
      />
    )
  }

  if (confirming === 'reboot') {
    return (
      <ConfirmDialog
        title={`Redémarrer ${node.name} ?`}
        description="La machine est jointe par Tor : si elle ne revient pas, la baie ne peut rien pour elle. Vérifiez qu’un accès de secours existe."
        confirmLabel="Redémarrer"
        icon={<Power size={22} />}
        busy={busy}
        onClose={() => setConfirming(undefined)}
        onConfirm={async () => {
          await act(
            () => api.runRackNodeAction(node.id, 'reboot'),
            'Redémarrage demandé au nœud',
          )
          setConfirming(undefined)
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
          <button type="button" className="button button-danger" onClick={() => setConfirming('remove')}>
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

        {node.alerts.length > 0 && (
          <ul className="rack-alerts" aria-label="Points d’attention">
            {node.alerts.map((alert) => (
              <li key={alert.message} className={`rack-alert rack-alert-${alert.level}`}>
                {alert.message}
              </li>
            ))}
          </ul>
        )}

        {node.kind === 'remote' && (
          <Availability history={history} />
        )}

        {node.link && (
          <dl className="rack-readout">
            <div><dt>Adresse</dt><dd className="mono">{node.link.ip || '—'}</dd></div>
            <div><dt>Présence</dt><dd>{node.link.online ? 'Sur le Wi-Fi' : 'Absent'}</dd></div>
            <div><dt>Reçu</dt><dd className="tabular">{formatBytes(node.link.download)}</dd></div>
            <div><dt>Émis</dt><dd className="tabular">{formatBytes(node.link.upload)}</dd></div>
          </dl>
        )}

        {node.state.agent_version && (
          <dl className="rack-readout">
            <div><dt>Agent</dt><dd>{node.state.agent_version}</dd></div>
            {node.state.platform?.system && (
              <div><dt>Système</dt><dd>{node.state.platform.system} · {node.state.platform.machine}</dd></div>
            )}
            {node.state.platform?.policy_mode && (
              <div><dt>Routage</dt><dd>{node.state.platform.policy_mode}</dd></div>
            )}
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
            {profiles.length > 0 && (
              <label>
                Profil
                <select
                  value=""
                  disabled={busy}
                  onChange={(event) => {
                    const profile = profiles.find((item) => item.id === event.target.value)
                    if (profile) {
                      const next = { ...DEFAULT_RULES, ...profile.rules }
                      setRules(next)
                      setPortsInput(next.keep_open_ports.join(', '))
                    }
                  }}
                >
                  <option value="">Reprendre un profil…</option>
                  {profiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>{profile.name}</option>
                  ))}
                </select>
              </label>
            )}
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
                  « Tor uniquement » est complet sous Linux et transparent pour TCP/DNS sous
                  macOS ; UDP/QUIC y reste bloqué. Windows refuse ce mode tant qu’un tunnel TUN
                  vérifié n’est pas installé. Gardez au moins un port joignable : un serveur
                  distant sans porte d’entrée ne se répare pas.
                </p>
              </>
            )}
            <div className="rack-actions">
              <button
                className="button button-primary button-small"
                disabled={busy}
                onClick={() => {
                  const nextRules = { ...rules, keep_open_ports: parsePorts(portsInput) }
                  setRules(nextRules)
                  setPortsInput(nextRules.keep_open_ports.join(', '))
                  void act(() => api.setRackNodeRules(node.id, nextRules), 'Règles enregistrées')
                }}
              >
                Appliquer les règles
              </button>
            </div>
          </div>
        </section>

        {node.kind === 'remote' && (
          <>
            <MeshRulesSection
              node={node}
              peers={peers}
              rules={rules}
              setRules={setRules}
              busy={busy}
            />
            <MeshKeyActions
              node={node}
              locked={meshLocked}
              busy={busy}
              act={act}
              notify={notify}
            />
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
                <label>
                  Notes
                  <input
                    value={identity.notes}
                    maxLength={200}
                    placeholder="Hébergeur, contrat, contact…"
                    onChange={(event) => setIdentity({ ...identity, notes: event.target.value })}
                  />
                </label>
                <div className="rack-actions">
                  <button
                    className="button button-small button-secondary"
                    disabled={busy}
                    onClick={() =>
                      void act(
                        () => api.updateRackNode({ id: node.id, ...identity }),
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
                  className="button button-small button-danger"
                  disabled={busy || !node.onion}
                  onClick={() => setConfirming('reboot')}
                >
                  <Power size={14} /> Redémarrer le nœud
                </button>
              </div>
              <div className="rack-actions">
                <select
                  value={journalUnit}
                  disabled={busy}
                  aria-label="Service à lire"
                  onChange={(event) => setJournalUnit(event.target.value)}
                >
                  {JOURNAL_UNITS.map((unit) => (
                    <option key={unit.id} value={unit.id}>{unit.label}</option>
                  ))}
                </select>
                <button
                  className="button button-small button-ghost"
                  disabled={busy || !node.onion}
                  onClick={async () => {
                    setBusy(true)
                    try {
                      const answer = await api.runRackNodeAction(node.id, 'journal', journalUnit)
                      setJournal((answer.result.lines as string[]) ?? [])
                    } catch (reason) {
                      notify(reason instanceof Error ? reason.message : 'Journal indisponible', true)
                    } finally {
                      setBusy(false)
                    }
                  }}
                >
                  <Terminal size={14} /> Lire le journal
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
                part et peuvent être réaffichés. L’installation récupère l’agent depuis GitHub,
                installe Tor et crée le service adapté à la machine.
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
                  <KeyRound size={14} /> Préparer l’installation
                </button>
                <a className="button button-small button-ghost" href="/api/v1/rack/agent-bundle" download>
                  <Download size={14} /> Archive hors ligne
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
                <div className="rack-installer">
                  <div className="rack-platforms" role="group" aria-label="Système de la machine distante">
                    {INSTALL_PLATFORMS.map((platform) => {
                      const Icon = platform.icon
                      return (
                        <button
                          type="button"
                          key={platform.id}
                          className={`rack-platform ${installPlatform === platform.id ? 'rack-platform-active' : ''}`}
                          aria-pressed={installPlatform === platform.id}
                          onClick={() => {
                            setInstallPlatform(platform.id)
                            setCommandCopied(false)
                          }}
                        >
                          <Icon size={17} />
                          <span><strong>{platform.label}</strong><small>{platform.detail}</small></span>
                        </button>
                      )
                    })}
                  </div>
                  <div className="rack-command-head">
                    <span>
                      <strong>Commande d’installation</strong>
                      <small>
                        {installPlatform === 'windows'
                          ? 'À coller dans PowerShell ouvert en administrateur.'
                          : 'À coller dans le Terminal de la machine distante.'}
                      </small>
                    </span>
                    <button
                      type="button"
                      className="button button-small button-secondary"
                      onClick={async () => {
                        const command = enrollment.commands[installPlatform] ?? enrollment.command
                        try {
                          await navigator.clipboard.writeText(command)
                          setCommandCopied(true)
                          window.setTimeout(() => setCommandCopied(false), 1800)
                        } catch {
                          notify('Copie refusée par le navigateur', true)
                        }
                      }}
                    >
                      {commandCopied ? <Check size={14} /> : <Copy size={14} />}
                      {commandCopied ? 'Copiée' : 'Copier'}
                    </button>
                  </div>
                  <pre className="rack-enrollment" aria-label="Commande d’installation">
                    {enrollment.commands[installPlatform] ?? enrollment.command}
                  </pre>
                  <div className="rack-command-head">
                    <span>
                      <strong>Jeton du nœud</strong>
                      <small>
                        L’installateur le demande sur le terminal : collez-le à l’invite.
                        Il n’est pas dans la commande, donc il n’apparaît ni dans « ps »
                        ni dans l’historique du shell de la machine.
                      </small>
                    </span>
                    <button
                      type="button"
                      className="button button-small button-secondary"
                      onClick={async () => {
                        try {
                          await navigator.clipboard.writeText(enrollment.token)
                          setTokenCopied(true)
                          window.setTimeout(() => setTokenCopied(false), 1800)
                        } catch {
                          notify('Copie refusée par le navigateur', true)
                        }
                      }}
                    >
                      {tokenCopied ? <Check size={14} /> : <Copy size={14} />}
                      {tokenCopied ? 'Copié' : 'Copier'}
                    </button>
                  </div>
                  <pre className="rack-enrollment" aria-label="Jeton du nœud">
                    {enrollment.token}
                  </pre>
                  <p className="rack-installer-note">
                    Ce jeton ouvre le nœud. Ne le collez que sur la machine à enrôler, et
                    renouvelez-le s’il a été vu ailleurs.
                    {enrollment.bundle_digest
                      ? ' La commande épingle l’agent à celui que cette appliance exécute : un téléchargement modifié est refusé avant d’être lancé.'
                      : ' Cette installation n’a pas de copie de référence de l’agent : la commande porte --unverified-bundle et le téléchargement n’est pas épinglé.'}
                  </p>
                </div>
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
              aria-label="Baie"
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
              {racks.map((frame) => (
                <option key={frame.id} value={frame.id}>{frame.name}</option>
              ))}
            </select>
            {rack && (
              <select
                value={node.position || 1}
                disabled={busy}
                aria-label="Emplacement"
                onChange={(event) =>
                  void act(
                    () =>
                      api.moveRackNode({
                        id: node.id,
                        rack_id: rack.id,
                        position: Number(event.target.value),
                      }),
                    'Nœud déplacé',
                  )
                }
              >
                {Array.from({ length: rack.units }, (_, index) => index + 1).map((unit) => (
                  <option key={unit} value={unit}>U{unit}</option>
                ))}
              </select>
            )}
          </div>
        </section>
      </div>
    </Modal>
  )
}

function parsePorts(value: string): number[] {
  return value
    .split(/[\s,;]+/)
    .map((item) => Number(item))
    .filter((item) => Number.isInteger(item) && item > 0 && item <= 65535)
    .slice(0, 8)
}

/* Le graphique est tracé dans un espace fixe étiré à la largeur de la fiche :
 * une lecture manquée y est un creux, pas un trou, pour que l’œil compte les
 * interruptions au lieu de les deviner. */
const SPARK_WIDTH = 320
const SPARK_HEIGHT = 48

function Availability({ history }: { history?: RackHistory }) {
  const path = useMemo(() => sparkline(history?.samples ?? []), [history])
  if (!history || !history.readings) {
    return (
      <p className="muted rack-availability-empty">
        Aucun sondage enregistré : la disponibilité apparaîtra après la première réponse.
      </p>
    )
  }
  const availability = history.availability ?? 0
  return (
    <section className="rack-availability">
      <header>
        <strong className="tabular">{availability.toFixed(1)} %</strong>
        <span className="muted">
          de réponses sur {history.readings} sondage{history.readings > 1 ? 's' : ''} ·
          {' '}{Math.round(history.window / 3600)} h
        </span>
      </header>
      <svg
        viewBox={`0 0 ${SPARK_WIDTH} ${SPARK_HEIGHT}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Disponibilité: ${availability.toFixed(1)} %`}
      >
        <path className="rack-spark-area" d={`${path} L ${SPARK_WIDTH} ${SPARK_HEIGHT} L 0 ${SPARK_HEIGHT} Z`} />
        <path className="rack-spark-line" d={path} />
      </svg>
    </section>
  )
}

function sparkline(samples: RackSample[]): string {
  const shown = samples.slice(-96)
  if (!shown.length) return `M 0 ${SPARK_HEIGHT}`
  const step = shown.length > 1 ? SPARK_WIDTH / (shown.length - 1) : SPARK_WIDTH
  return shown
    .map((sample, index) => {
      // Une lecture obtenue vaut sa charge mémoire, une lecture manquée le sol :
      // la courbe descend à zéro exactement quand le nœud n’a pas répondu.
      const value = sample.reachable ? Math.max(8, Math.min(100, sample.memory_percent)) : 0
      const y = SPARK_HEIGHT - (value / 100) * (SPARK_HEIGHT - 4)
      return `${index === 0 ? 'M' : 'L'} ${(index * step).toFixed(1)} ${y.toFixed(1)}`
    })
    .join(' ')
}
