import { Cable, Link2, Trash2 } from 'lucide-react'
import { RefObject, useLayoutEffect, useState } from 'react'
import type { RackCable, RackNode } from '../types'

type CableLine = RackCable & { path: string }

export function RackCableLayer({
  canvasRef,
  cables,
  selected,
  onSelect,
}: {
  canvasRef: RefObject<HTMLDivElement | null>
  cables: RackCable[]
  selected: string
  onSelect: (id: string) => void
}) {
  const [lines, setLines] = useState<CableLine[]>([])
  const [size, setSize] = useState({ width: 1, height: 1 })

  useLayoutEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const measure = () => {
      const frame = canvas.getBoundingClientRect()
      const width = Math.max(1, canvas.scrollWidth)
      const height = Math.max(1, canvas.scrollHeight)
      setSize({ width, height })
      setLines(
        cables.flatMap((cable) => {
          const source = canvas.querySelector<HTMLElement>(
            `[data-rack-port="${cable.source_node_id}:${cable.source_port}"]`,
          )
          const target = canvas.querySelector<HTMLElement>(
            `[data-rack-port="${cable.target_node_id}:${cable.target_port}"]`,
          )
          if (!source || !target) return []
          const a = source.getBoundingClientRect()
          const b = target.getBoundingClientRect()
          // The elevation scrolls independently from the page. Adding the
          // current offsets turns viewport coordinates back into stable rack
          // coordinates, so patch leads do not move away from their ports.
          const x1 = a.left - frame.left + canvas.scrollLeft + a.width / 2
          const y1 = a.top - frame.top + canvas.scrollTop + a.height / 2
          const x2 = b.left - frame.left + canvas.scrollLeft + b.width / 2
          const y2 = b.top - frame.top + canvas.scrollTop + b.height / 2
          const gutter = Math.min(width - 18, Math.max(x1, x2) + 74)
          return [{
            ...cable,
            path: `M ${x1} ${y1} C ${gutter} ${y1}, ${gutter} ${y2}, ${x2} ${y2}`,
          }]
        }),
      )
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(canvas)
    window.addEventListener('resize', measure)
    const frame = window.requestAnimationFrame(measure)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', measure)
      window.cancelAnimationFrame(frame)
    }
  }, [canvasRef, cables])

  return (
    <svg
      className="rack-cable-layer"
      style={{ width: size.width, height: size.height }}
      viewBox={`0 0 ${size.width} ${size.height}`}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      {lines.map((cable) => (
        <g key={cable.id} className={selected === cable.id ? 'rack-cable-selected' : ''}>
          <path className="rack-cable-shadow" d={cable.path} />
          <path
            className={`rack-cable rack-cable-${cable.color} rack-cable-${cable.status}`}
            d={cable.path}
            onClick={() => onSelect(cable.id)}
          />
        </g>
      ))}
    </svg>
  )
}

export function CableInspector({
  cables,
  nodes,
  selected,
  cabling,
  start,
  busy,
  onSelect,
  onRemove,
}: {
  cables: RackCable[]
  nodes: RackNode[]
  selected: string
  cabling: boolean
  start?: { nodeId: string; port: number }
  busy: boolean
  onSelect: (id: string) => void
  onRemove: (id: string) => void
}) {
  const startNode = nodes.find((node) => node.id === start?.nodeId)
  return (
    <aside className="rack-connections" aria-label="Connexions réseau">
      <header>
        <span className="rack-connections-icon"><Cable size={18} /></span>
        <span><strong>Connexions</strong><small>{cables.length} câble{cables.length > 1 ? 's' : ''} actif{cables.length > 1 ? 's' : ''}</small></span>
      </header>

      {cabling && (
        <div className="rack-cabling-hint" role="status">
          <Link2 size={16} />
          {start
            ? <span><strong>{startNode?.name ?? 'Appareil'} · P{start.port}</strong>Choisissez maintenant un port libre sur un autre appareil.</span>
            : <span><strong>Mode câblage actif</strong>Cliquez sur le premier port à relier.</span>}
        </div>
      )}

      <ol className="rack-connection-list">
        {cables.map((cable) => (
          <li key={cable.id}>
            <button
              className={`rack-connection ${selected === cable.id ? 'rack-connection-selected' : ''}`}
              aria-pressed={selected === cable.id}
              onClick={() => onSelect(cable.id)}
            >
              <span className={`rack-cable-dot rack-cable-bg-${cable.color}`} />
              <span className="rack-connection-copy">
                <strong>{cable.label}</strong>
                <small>{cable.source_name} · P{cable.source_port} → {cable.target_name} · P{cable.target_port}</small>
                <span><em>{speedLabel(cable.speed)}</em><em className={`rack-link-${cable.status}`}>{statusLabel(cable.status)}</em></span>
              </span>
            </button>
            <button
              className="icon-button rack-connection-remove"
              aria-label={`Retirer le câble ${cable.label}`}
              disabled={busy}
              onClick={() => onRemove(cable.id)}
            >
              <Trash2 size={15} />
            </button>
          </li>
        ))}
      </ol>

      {!cables.length && !cabling && (
        <div className="rack-connections-empty">
          <Cable size={24} />
          <strong>Aucun câble dessiné</strong>
          <p>Activez « Câbler », puis reliez deux ports libres.</p>
        </div>
      )}

      <footer className="rack-port-legend">
        <strong>Ports</strong>
        <span><i className="rack-port-swatch" /> Libre</span>
        <span><i className="rack-port-swatch rack-port-swatch-connected" /> Connecté</span>
        <span><i className="rack-port-swatch rack-port-swatch-offline" /> Liaison à vérifier</span>
      </footer>
    </aside>
  )
}

function speedLabel(speed: RackCable['speed']): string {
  return { '100-mbps': '100 Mbps', '1-gbps': '1 Gbps', '10-gbps': '10 Gbps' }[speed]
}

function statusLabel(status: RackCable['status']): string {
  return { online: 'En ligne', warning: 'À surveiller', offline: 'Injoignable' }[status]
}
