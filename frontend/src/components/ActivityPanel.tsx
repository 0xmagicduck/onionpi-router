import { FolderUp, History, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
import { relativeTime } from '../lib'
import type { Activity } from '../types'
import { Panel } from './Panel'
import { EmptyState } from './ui'

function icon(kind: string) {
  if (kind === 'upload' || kind === 'folder') return <FolderUp size={15} />
  if (kind === 'identity') return <RefreshCw size={15} />
  if (kind === 'delete') return <Trash2 size={15} />
  return <ShieldCheck size={15} />
}

export function ActivityPanel({ activities }: { activities: Activity[] }) {
  return (
    <Panel title="Activité récente" className="activity-panel">
      {activities.length ? (
        <ol className="activity-list">
          {activities.map((activity) => (
            <li className="activity-item" key={activity.id}>
              <span className={`activity-icon activity-${activity.kind}`} aria-hidden="true">{icon(activity.kind)}</span>
              <p>
                {activity.message}
                <small>{relativeTime(activity.created_at)}</small>
              </p>
            </li>
          ))}
        </ol>
      ) : (
        <EmptyState icon={History} title="Rien à signaler">
          Les changements de configuration et les échanges de fichiers apparaîtront ici.
        </EmptyState>
      )}
    </Panel>
  )
}
