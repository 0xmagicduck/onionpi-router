import { FolderUp, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
import { relativeTime } from '../lib'
import type { Activity } from '../types'
import { Panel } from './Panel'

export function ActivityPanel({ activities }: { activities: Activity[] }) {
  const icon = (kind: string) => {
    if (kind === 'upload' || kind === 'folder') return <FolderUp size={18} />
    if (kind === 'identity') return <RefreshCw size={18} />
    if (kind === 'delete') return <Trash2 size={18} />
    return <ShieldCheck size={18} />
  }
  return (
    <Panel title="Activité récente" className="activity-panel">
      <div className="activity-list">
        {activities.map((activity) => (
          <div className="activity-item" key={activity.id}>
            <span className={`activity-icon activity-${activity.kind}`}>{icon(activity.kind)}</span>
            <p>{activity.message}<small>{relativeTime(activity.created_at)}</small></p>
          </div>
        ))}
        {!activities.length && <p className="muted">Aucune activité récente.</p>}
      </div>
    </Panel>
  )
}
