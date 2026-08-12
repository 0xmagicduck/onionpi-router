import { AlertCircle, CheckCircle2, X } from 'lucide-react'
import type { Toast } from '../hooks/useToasts'

export function Toaster({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  return (
    <div className="toaster" role="region" aria-label="Notifications">
      {toasts.map((toast) => (
        <div
          className={`toast ${toast.error ? 'toast-error' : ''}`}
          key={toast.id}
          role={toast.error ? 'alert' : 'status'}
          aria-live={toast.error ? 'assertive' : 'polite'}
        >
          {toast.error ? <AlertCircle size={18} /> : <CheckCircle2 size={18} />}
          <span>{toast.message}</span>
          <button onClick={() => onDismiss(toast.id)} aria-label="Fermer la notification"><X size={15} /></button>
        </div>
      ))}
    </div>
  )
}
