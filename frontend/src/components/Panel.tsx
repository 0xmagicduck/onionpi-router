import type { HTMLAttributes, ReactNode } from 'react'

type Props = Omit<HTMLAttributes<HTMLElement>, 'title'> & {
  title?: ReactNode
  subtitle?: ReactNode
  action?: ReactNode
}

export function Panel({ title, subtitle, action, className = '', children, ...props }: Props) {
  return (
    <section className={`panel ${className}`} {...props}>
      {(title || action) && (
        <div className="panel-heading">
          {title && (
            <h2>
              {title}
              {subtitle && <span className="panel-subtitle">{subtitle}</span>}
            </h2>
          )}
          {action}
        </div>
      )}
      {children}
    </section>
  )
}

type MetricProps = {
  label: string
  value: number
  suffix?: string
  /** Above this share of the track the bar turns amber, then red. */
  thresholds?: [number, number]
}

export function MetricBar({ label, value, suffix = '%', thresholds = [75, 90] }: MetricProps) {
  const normalized = Math.min(100, Math.max(0, value))
  const tone = normalized >= thresholds[1] ? 'meter-danger' : normalized >= thresholds[0] ? 'meter-warn' : ''
  return (
    <div className="metric">
      <div className="metric-label">
        <span>{label}</span>
        <strong>{Math.round(value)}{suffix}</strong>
      </div>
      <div
        className={`meter-track ${tone}`}
        role="meter"
        aria-label={label}
        aria-valuenow={Math.round(value)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuetext={`${Math.round(value)}${suffix}`}
      >
        <span style={{ width: `${normalized}%` }} />
      </div>
    </div>
  )
}
