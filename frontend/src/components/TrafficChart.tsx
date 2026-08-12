import { useMemo, useRef, useState } from 'react'
import { Activity } from 'lucide-react'
import type { TrafficSample } from '../types'
import { Panel } from './Panel'
import { Badge, EmptyState } from './ui'

/* The plot is drawn in a fixed 800x180 user space stretched to the panel width
 * (preserveAspectRatio="none"). Anything that must not be stretched — labels,
 * cursor, hover dots — is HTML positioned over the same coordinates. */
const WIDTH = 800
const HEIGHT = 180
const TOP = 10
const BOTTOM = 14

type Series = 'download_mbps' | 'upload_mbps'

const scaleY = (value: number, max: number) => TOP + (HEIGHT - TOP - BOTTOM) * (1 - value / max)
const scaleX = (index: number, count: number) => (count <= 1 ? WIDTH / 2 : (index / (count - 1)) * WIDTH)

function linePath(samples: TrafficSample[], key: Series, max: number): string {
  return samples
    .map((sample, index) => `${index === 0 ? 'M' : 'L'} ${scaleX(index, samples.length).toFixed(1)} ${scaleY(sample[key], max).toFixed(1)}`)
    .join(' ')
}

function areaPath(samples: TrafficSample[], key: Series, max: number): string {
  if (!samples.length) return ''
  const base = HEIGHT - BOTTOM
  return `${linePath(samples, key, max)} L ${WIDTH} ${base} L 0 ${base} Z`
}

/** Rounds the axis top to a readable value instead of the raw peak. */
function niceMax(value: number): number {
  if (value <= 1) return 1
  const magnitude = 10 ** Math.floor(Math.log10(value))
  return Math.ceil(value / magnitude) * magnitude
}

const timeFormat = new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

export function TrafficChart({ samples }: { samples: TrafficSample[] }) {
  const plotRef = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<number>()

  const recent = useMemo(() => samples.slice(-60), [samples])
  const max = useMemo(
    () => niceMax(Math.max(1, ...recent.flatMap((sample) => [sample.download_mbps, sample.upload_mbps]))),
    [recent],
  )
  const latest = recent.at(-1)
  const active = hover !== undefined ? recent[hover] : undefined
  const shown = active ?? latest

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const box = plotRef.current?.getBoundingClientRect()
    if (!box || recent.length < 2) return
    const ratio = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width))
    setHover(Math.round(ratio * (recent.length - 1)))
  }

  const percent = (index: number) => `${(scaleX(index, recent.length) / WIDTH) * 100}%`

  return (
    <Panel
      title="Trafic réseau"
      subtitle="Cinq dernières minutes"
      className="traffic-panel"
      action={<Badge tone="success" live>en direct</Badge>}
    >
      {recent.length === 0 ? (
        <EmptyState icon={Activity} title="Pas encore de mesure">
          Le premier échantillon de débit apparaît quelques secondes après le démarrage du service.
        </EmptyState>
      ) : (
        <>
          <div className="chart-wrap">
            <div
              className="chart-plot"
              ref={plotRef}
              onPointerMove={onPointerMove}
              onPointerLeave={() => setHover(undefined)}
            >
              <svg
                viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
                preserveAspectRatio="none"
                role="img"
                aria-label={`Débit réseau des cinq dernières minutes : ${latest?.download_mbps.toFixed(2) ?? 0} Mbps en réception, ${latest?.upload_mbps.toFixed(2) ?? 0} Mbps en émission.`}
              >
                <defs>
                  <linearGradient id="traffic-download" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--success)" stopOpacity=".3" />
                    <stop offset="100%" stopColor="var(--success)" stopOpacity="0" />
                  </linearGradient>
                  <linearGradient id="traffic-upload" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--info)" stopOpacity=".24" />
                    <stop offset="100%" stopColor="var(--info)" stopOpacity="0" />
                  </linearGradient>
                </defs>
                {[0, 0.5, 1].map((ratio) => (
                  <line
                    key={ratio}
                    className="chart-grid"
                    x1="0"
                    y1={scaleY(max * ratio, max)}
                    x2={WIDTH}
                    y2={scaleY(max * ratio, max)}
                  />
                ))}
                <path className="chart-area-download" d={areaPath(recent, 'download_mbps', max)} />
                <path className="chart-area-upload" d={areaPath(recent, 'upload_mbps', max)} />
                <path className="chart-line chart-line-download" d={linePath(recent, 'download_mbps', max)} />
                <path className="chart-line chart-line-upload" d={linePath(recent, 'upload_mbps', max)} />
              </svg>
              {[1, 0.5].map((ratio) => (
                <span className="chart-axis-label" key={ratio} style={{ top: `${scaleY(max * ratio, max)}px` }}>
                  {max * ratio >= 10 ? Math.round(max * ratio) : (max * ratio).toFixed(1)} Mbps
                </span>
              ))}
              {active && hover !== undefined && (
                <>
                  <span className="chart-cursor" style={{ left: percent(hover) }} />
                  <span
                    className="chart-dot dot-download"
                    style={{ left: percent(hover), top: `${scaleY(active.download_mbps, max)}px` }}
                  />
                  <span
                    className="chart-dot dot-upload"
                    style={{ left: percent(hover), top: `${scaleY(active.upload_mbps, max)}px` }}
                  />
                  <div
                    className="chart-tooltip"
                    /* Flips against the plot edges so the card never leaves the panel. */
                    style={{
                      left: percent(hover),
                      transform: `translateX(${hover / Math.max(1, recent.length - 1) < 0.2 ? '0%' : hover / Math.max(1, recent.length - 1) > 0.8 ? '-100%' : '-50%'})`,
                    }}
                  >
                    <time>{timeFormat.format(active.timestamp * 1000)}</time>
                    <b>Réception <span>{active.download_mbps.toFixed(2)} Mbps</span></b>
                    <b>Émission <span>{active.upload_mbps.toFixed(2)} Mbps</span></b>
                  </div>
                </>
              )}
            </div>
          </div>
          <div className="chart-legend">
            <span><i className="dot-download" />Téléchargement <strong>{shown?.download_mbps.toFixed(2) ?? '0.00'} Mbps</strong></span>
            <span><i className="dot-upload" />Téléversement <strong>{shown?.upload_mbps.toFixed(2) ?? '0.00'} Mbps</strong></span>
            <span className="muted">Échelle {max} Mbps</span>
          </div>
        </>
      )}
    </Panel>
  )
}
