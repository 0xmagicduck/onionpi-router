import type { TrafficSample } from '../types'
import { Panel } from './Panel'

function pathFor(values: number[], width: number, height: number, max: number): string {
  if (!values.length) return ''
  return values
    .map((value, index) => {
      const x = values.length === 1 ? 0 : (index / (values.length - 1)) * width
      const y = height - (value / max) * height
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`
    })
    .join(' ')
}

export function TrafficChart({ samples }: { samples: TrafficSample[] }) {
  const recent = samples.slice(-60)
  const max = Math.max(10, ...recent.flatMap((sample) => [sample.download_mbps, sample.upload_mbps]))
  const download = pathFor(recent.map((sample) => sample.download_mbps), 800, 150, max)
  const upload = pathFor(recent.map((sample) => sample.upload_mbps), 800, 150, max)
  const latest = recent.at(-1)
  return (
    <Panel title="Trafic réseau" className="traffic-panel" action={<span className="live-label"><i /> en direct</span>}>
      <div className="chart-wrap" aria-label="Débit réseau des cinq dernières minutes">
        <svg viewBox="0 0 800 170" role="img">
          {[10, 50, 90, 130].map((y) => <line key={y} x1="0" y1={y} x2="800" y2={y} className="chart-grid" />)}
          <path d={download} className="chart-download" />
          <path d={upload} className="chart-upload" />
        </svg>
      </div>
      <div className="chart-legend">
        <span><i className="dot-download" />Téléchargement <strong>{latest?.download_mbps.toFixed(2) ?? '0.00'} Mbps</strong></span>
        <span><i className="dot-upload" />Téléversement <strong>{latest?.upload_mbps.toFixed(2) ?? '0.00'} Mbps</strong></span>
      </div>
    </Panel>
  )
}
