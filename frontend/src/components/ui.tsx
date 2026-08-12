import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'

type Tone = 'success' | 'warning' | 'danger' | 'neutral'

export function Badge({
  tone = 'neutral',
  dot = false,
  live = false,
  children,
}: {
  tone?: Tone
  dot?: boolean
  live?: boolean
  children: ReactNode
}) {
  return (
    <span className={`badge badge-${tone} ${live ? 'badge-live' : ''}`}>
      {(dot || live) && <i />}
      {children}
    </span>
  )
}

export function StatCard({
  icon: Icon,
  label,
  value,
  unit,
  foot,
  tone,
}: {
  icon: LucideIcon
  label: string
  value: ReactNode
  unit?: string
  foot?: ReactNode
  tone?: 'good' | 'warn' | 'bad'
}) {
  return (
    <article className={`stat-card ${tone ? `stat-${tone}` : ''}`}>
      <header><Icon /><span>{label}</span></header>
      <p className="stat-value">{value}{unit && <small>{unit}</small>}</p>
      {foot && <p className="stat-foot">{foot}</p>}
    </article>
  )
}

export function EmptyState({
  icon: Icon,
  title,
  children,
  action,
}: {
  icon: LucideIcon
  title: string
  children?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="empty-state">
      <Icon strokeWidth={1.5} />
      <strong>{title}</strong>
      {children && <p>{children}</p>}
      {action}
    </div>
  )
}

export function Skeleton({ className = '', width, height }: { className?: string; width?: number | string; height?: number | string }) {
  return <span className={`skeleton ${className}`} style={{ width, height }} aria-hidden="true" />
}

/** A panel-sized placeholder. Announced as busy so assistive technology does
 *  not read the shimmer as content. */
export function LoadingPanel({ height = 200, label }: { height?: number; label: string }) {
  return (
    <div className="panel skeleton-panel" style={{ height }} role="status" aria-busy="true" aria-label={label} />
  )
}
