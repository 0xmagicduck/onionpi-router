import { useCallback, useEffect, useState } from 'react'
import { KeyRound, Lock, RefreshCw, Share2, Unlock } from 'lucide-react'
import { api } from '../api'
import { Badge } from '../components/ui'
import { relativeTime } from '../lib'
import type {
  RackEndorsementRequest,
  RackMesh,
  RackMeshForward,
  RackNode,
  RackNodeRules,
} from '../types'

/** Découpe « 22, 9080 » en ports. La borne et le refus sont côté baie : ici on
 *  se contente de ne pas envoyer de bruit. */
export function parseMeshPorts(value: string): number[] {
  const ports: number[] = []
  for (const piece of value.split(/[\s,;]+/)) {
    const port = Number.parseInt(piece, 10)
    if (Number.isInteger(port) && port >= 1 && port <= 65535 && !ports.includes(port)) {
      ports.push(port)
    }
  }
  return ports
}

/** La moitié maillage d’une fiche de nœud : ce qu’il expose, ce qu’il ouvre
 *  chez lui vers un pair, et l’identité qu’il a lui-même engendrée. */
export function MeshRulesSection({
  node,
  peers,
  rules,
  setRules,
  busy,
}: {
  node: RackNode
  peers: RackNode[]
  rules: RackNodeRules
  setRules: (rules: RackNodeRules) => void
  busy: boolean
}) {
  const mesh = rules.mesh
  const [portsInput, setPortsInput] = useState(mesh.ports.join(', '))
  const announced = node.state.mesh

  const setMesh = (next: Partial<RackNodeRules['mesh']>) =>
    setRules({ ...rules, mesh: { ...mesh, ...next } })

  const setForward = (index: number, patch: Partial<RackMeshForward>) =>
    setMesh({
      forwards: mesh.forwards.map((forward, position) =>
        position === index ? { ...forward, ...patch } : forward,
      ),
    })

  return (
    <section className="rack-section">
      <h3>Maillage OnionMesh</h3>
      <div className="settings-form">
        <label className="choice-card">
          <input
            type="checkbox"
            checked={mesh.enabled}
            disabled={busy}
            onChange={(event) => setMesh({ enabled: event.target.checked })}
          />
          <div>
            <strong>Faire participer ce nœud au maillage</strong>
            <p>
              Il pourra joindre ses pairs et être joint par eux, par le lien radio quand il
              existe et par un flux onion sinon. Il ne route l’Internet d’aucun autre.
            </p>
          </div>
        </label>
        {mesh.enabled && (
          <>
            <label>
              Ports présentés aux pairs
              <input
                value={portsInput}
                placeholder="22, 9080"
                inputMode="numeric"
                disabled={busy}
                onChange={(event) => setPortsInput(event.target.value)}
                onBlur={() => {
                  const ports = parseMeshPorts(portsInput)
                  setPortsInput(ports.join(', '))
                  setMesh({ ports })
                }}
              />
            </label>
            <div className="rack-forwards">
              <p className="prose">
                Redirections locales : un port de ce nœud qui présente un port d’un pair,
                comme <code>ssh -L</code>. Les deux extrémités appliquent la carte — le pair
                visé doit avoir ouvert ce port de son côté, sinon la session est refusée.
              </p>
              {mesh.forwards.map((forward, index) => (
                <div className="rack-forward" key={`${forward.listen}-${index}`}>
                  <input
                    aria-label="Port local"
                    value={forward.listen || ''}
                    inputMode="numeric"
                    disabled={busy}
                    onChange={(event) =>
                      setForward(index, { listen: Number.parseInt(event.target.value, 10) || 0 })
                    }
                  />
                  <select
                    aria-label="Pair"
                    value={forward.node}
                    disabled={busy}
                    onChange={(event) => setForward(index, { node: event.target.value })}
                  >
                    <option value="">Choisir un pair…</option>
                    {peers.map((peer) => (
                      <option key={peer.id} value={peer.id}>{peer.name}</option>
                    ))}
                  </select>
                  <input
                    aria-label="Port distant"
                    value={forward.port || ''}
                    inputMode="numeric"
                    disabled={busy}
                    onChange={(event) =>
                      setForward(index, { port: Number.parseInt(event.target.value, 10) || 0 })
                    }
                  />
                  <button
                    className="button button-small"
                    disabled={busy}
                    onClick={() =>
                      setMesh({ forwards: mesh.forwards.filter((_, position) => position !== index) })
                    }
                  >
                    Retirer
                  </button>
                </div>
              ))}
              <button
                className="button button-small"
                disabled={busy || mesh.forwards.length >= 8 || peers.length === 0}
                onClick={() =>
                  setMesh({ forwards: [...mesh.forwards, { listen: 0, node: '', port: 0 }] })
                }
              >
                Ajouter une redirection
              </button>
            </div>
          </>
        )}
        {announced?.address ? (
          <dl className="rack-readout">
            <div><dt>Adresse</dt><dd className="mono">{announced.address}</dd></div>
            <div><dt>Identité</dt><dd className="mono">{announced.identity?.slice(0, 24)}…</dd></div>
            <div><dt>Chemin direct</dt><dd className="mono">{announced.direct || 'aucun'}</dd></div>
            <div><dt>Carte</dt><dd className="tabular">série {node.netmap_serial || 0}</dd></div>
            <div><dt>Sessions</dt><dd className="tabular">{announced.sessions ?? 0}</dd></div>
          </dl>
        ) : (
          <p className="prose">
            Ce nœud n’a pas encore annoncé de clé de maillage. Elle est engendrée sur la
            machine à la première exécution d’un agent 0.6 ou plus récent, et seule sa moitié
            publique remonte ici : la baie autorise un nœud, elle ne peut pas l’être.
          </p>
        )}
      </div>
    </section>
  )
}

/** Rotation de clé et contre-signatures. Séparé du formulaire de règles parce
 *  que ce sont des actions, pas une intention à enregistrer. */
export function MeshKeyActions({
  node,
  locked,
  busy,
  act,
  notify,
}: {
  node: RackNode
  locked: boolean
  busy: boolean
  act: (action: () => Promise<unknown>, message: string) => Promise<void>
  notify: (message: string, error?: boolean) => void
}) {
  const [request, setRequest] = useState<RackEndorsementRequest>()
  const [pasted, setPasted] = useState('')

  const submit = useCallback(async () => {
    // « clé garant » et « signature » par ligne : ce que `onionpi-admin
    // mesh-endorse` affiche, recopié tel quel.
    const endorsements: Record<string, string> = {}
    for (const line of pasted.split('\n')) {
      const match = line.match(/(ed25519:[0-9a-f]{64})\D+([0-9a-f]{128})/)
      if (match) endorsements[match[1]] = match[2]
    }
    if (Object.keys(endorsements).length === 0) {
      notify('Aucune contre-signature reconnue dans ce texte.', true)
      return
    }
    await act(
      () => api.setRackEndorsements(node.id, endorsements),
      `${Object.keys(endorsements).length} contre-signature(s) enregistrée(s)`,
    )
    setPasted('')
  }, [act, node.id, notify, pasted])

  return (
    <section className="rack-section">
      <h3>Clé de maillage</h3>
      <div className="rack-actions">
        <button
          className="button button-small"
          disabled={busy || !node.onion}
          onClick={() => void act(() => api.publishRackNetmap(node.id), 'Carte publiée')}
        >
          <Share2 size={16} /> Publier la carte
        </button>
        <button
          className="button button-small"
          disabled={busy || !node.onion}
          onClick={() =>
            void act(
              () => api.runRackNodeAction(node.id, 'mesh-rotate'),
              'Clé de maillage renouvelée',
            )
          }
        >
          <RefreshCw size={16} /> Renouveler la clé
        </button>
        {locked && node.mesh_identity && (
          <button
            className="button button-small"
            disabled={busy}
            onClick={() =>
              void api
                .rackEndorsementRequest(node.id)
                .then(setRequest)
                .catch((reason: unknown) =>
                  notify(reason instanceof Error ? reason.message : 'Refusé', true),
                )
            }
          >
            <KeyRound size={16} /> Faire contresigner
          </button>
        )}
      </div>
      {locked && (
        <p className="prose">
          Verrou actif : {node.mesh_endorsements ? Object.keys(node.mesh_endorsements).length : 0}{' '}
          contre-signature(s) enregistrée(s). Sans le compte requis, les pairs refusent cette clé
          — c’est ce qui empêche une baie compromise d’inscrire la machine de son choix.
        </p>
      )}
      {request && (
        <div className="settings-form">
          <p className="prose">
            Chaque garant lance cette commande sur <em>sa</em> machine, puis vous colle les deux
            lignes affichées :
          </p>
          <pre className="rack-enrollment">{request.command}</pre>
          <label>
            Contre-signatures
            <textarea
              rows={4}
              value={pasted}
              placeholder="ed25519:… &#10;signature…"
              onChange={(event) => setPasted(event.target.value)}
            />
          </label>
          <div className="rack-actions">
            <button className="button button-primary button-small" disabled={busy} onClick={() => void submit()}>
              Enregistrer les contre-signatures
            </button>
          </div>
        </div>
      )}
    </section>
  )
}

/** Vue d’ensemble du réseau superposé : la clé du coordinateur, le verrou, et
 *  qui figure réellement dans les cartes publiées. */
export function MeshOverview({
  mesh,
  notify,
  onChanged,
}: {
  mesh: RackMesh
  notify: (message: string, error?: boolean) => void
  onChanged: () => Promise<void>
}) {
  const [lock, setLock] = useState(mesh.lock)
  const [trustees, setTrustees] = useState(mesh.lock.trustees.join('\n'))
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setLock(mesh.lock)
    setTrustees(mesh.lock.trustees.join('\n'))
  }, [mesh.lock])

  const save = async () => {
    setBusy(true)
    try {
      const list = trustees
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
      await api.setRackMeshLock({ enabled: lock.enabled, threshold: lock.threshold, trustees: list })
      notify(lock.enabled ? 'Verrou de maillage activé' : 'Verrou de maillage désactivé')
      await onChanged()
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Refusé', true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rack-mesh">
      <dl className="rack-readout">
        <div><dt>Coordinateur</dt><dd className="mono">{mesh.coordinator.slice(0, 28)}…</dd></div>
        <div><dt>Port du plan de données</dt><dd className="tabular">{mesh.mesh_port}</dd></div>
        <div><dt>Membres</dt><dd className="tabular">{mesh.members.filter((member) => member.in_map).length}</dd></div>
        <div><dt>Clés révoquées</dt><dd className="tabular">{mesh.revoked.length}</dd></div>
      </dl>
      <p className="prose">
        Le maillage relie les nœuds entre eux ; il ne route l’Internet d’aucun par un autre.
        Chaque paire ouvre son propre chemin — direct par <code>bat0</code> quand la radio le
        permet, relayé par un flux onion sinon — et la même session Noise sert dans les deux cas.
        Ni UDP ni ICMP ne traversent : un <code>ping</code> sur une adresse <code>fd7a:</code> ne
        veut rien dire.
      </p>

      <table className="data-table">
        <thead>
          <tr>
            <th>Nœud</th>
            <th>Adresse</th>
            <th>Ports</th>
            <th>Chemin direct</th>
            <th>Carte</th>
          </tr>
        </thead>
        <tbody>
          {mesh.members.length === 0 && (
            <tr><td colSpan={5}>Aucun nœud distant dans la baie.</td></tr>
          )}
          {mesh.members.map((member) => (
            <tr key={member.id}>
              <td>
                {member.name}{' '}
                {member.in_map ? (
                  <Badge tone="success">dans la carte</Badge>
                ) : (
                  <Badge tone="neutral">{member.enabled ? 'clé ou adresse manquante' : 'désactivé'}</Badge>
                )}
              </td>
              <td className="mono">{member.address || '—'}</td>
              <td className="tabular">{member.ports.join(', ') || '—'}</td>
              <td className="mono">{member.direct || 'relayé'}</td>
              <td className="tabular">
                {member.netmap_serial
                  ? `série ${member.netmap_serial} · ${relativeTime(member.netmap_issued_at)}`
                  : 'jamais publiée'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <section className="rack-section">
        <h3>{lock.enabled ? <Lock size={18} /> : <Unlock size={18} />} Verrou de maillage</h3>
        <div className="settings-form">
          <p className="prose">
            Sans verrou, une baie compromise inscrit le pair de son choix dans les cartes. Avec,
            une clé de pair <em>nouvelle</em> n’est acceptée par les nœuds que contresignée par K
            garants sur N. Le coût est réel — ajouter une machine demande K opérateurs — donc
            c’est un choix. Créez une clé de garant avec{' '}
            <code>onionpi-admin mesh-trustee --out garant.key</code>, sur une autre machine que
            celle-ci.
          </p>
          <label className="choice-card">
            <input
              type="checkbox"
              checked={lock.enabled}
              disabled={busy}
              onChange={(event) => setLock({ ...lock, enabled: event.target.checked })}
            />
            <div>
              <strong>Exiger des contre-signatures pour toute nouvelle clé de pair</strong>
              <p>La baie cesse d’être le point unique dont la compromission ouvre le maillage.</p>
            </div>
          </label>
          <label>
            Seuil K
            <input
              value={lock.threshold || ''}
              inputMode="numeric"
              disabled={busy}
              onChange={(event) =>
                setLock({ ...lock, threshold: Number.parseInt(event.target.value, 10) || 0 })
              }
            />
          </label>
          <label>
            Garants, une clé publique par ligne
            <textarea
              rows={4}
              value={trustees}
              disabled={busy}
              placeholder="ed25519:…"
              onChange={(event) => setTrustees(event.target.value)}
            />
          </label>
          <div className="rack-actions">
            <button className="button button-primary button-small" disabled={busy} onClick={() => void save()}>
              Enregistrer le verrou
            </button>
          </div>
          <p className="prose">
            Le verrou est épinglé sur chaque nœud à son installation, dans un fichier que la baie
            n’écrit pas. Le modifier ici ne change rien aux nœuds déjà enrôlés : réinstallez-les
            avec la commande mise à jour pour qu’ils l’appliquent.
          </p>
        </div>
      </section>
    </div>
  )
}
