import type { HTMLAttributes, ReactNode } from 'react'

type Props = HTMLAttributes<HTMLElement> & {
  title?: string
  action?: ReactNode
}

export function Panel({ title, action, className = '', children, ...props }: Props) {
  return (
    <section className={`panel ${className}`} {...props}>
      {(title || action) && (
        <div className="panel-heading">
          {title && <h2>{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  )
}

export function MetricBar({ label, value, suffix = '%' }: { label: string; value: number; suffix?: string }) {
  const normalized = Math.min(100, Math.max(0, value))
  return (
    <div className="metric">
      <div className="metric-label"><span>{label}</span><strong>{Math.round(value)}{suffix}</strong></div>
      <div className="metric-track"><span style={{ width: `${normalized}%` }} /></div>
    </div>
  )
}
