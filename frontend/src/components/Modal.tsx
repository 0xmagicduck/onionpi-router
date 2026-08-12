import { ReactNode, useEffect, useId, useRef } from 'react'
import { X } from 'lucide-react'

const FOCUSABLE =
  'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'

type Props = {
  title: string
  description?: ReactNode
  icon?: ReactNode
  tone?: 'default' | 'danger'
  onClose: () => void
  children?: ReactNode
  actions?: ReactNode
  /** Rendered as a <form>, so the primary action can be a submit button. */
  onSubmit?: (event: React.FormEvent) => void
}

export function Modal({ title, description, icon, tone = 'default', onClose, children, actions, onSubmit }: Props) {
  const dialogRef = useRef<HTMLFormElement>(null)
  const titleId = useId()
  const descriptionId = useId()

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const first = dialogRef.current?.querySelector<HTMLElement>(FOCUSABLE)
    first?.focus()
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
        return
      }
      if (event.key !== 'Tab') return
      // Keeping the focus inside is what makes the dialog usable without a
      // mouse, and what stops a screen reader from wandering into the page.
      const nodes = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [])
      if (!nodes.length) return
      const edge = event.shiftKey ? nodes[0] : nodes[nodes.length - 1]
      if (document.activeElement === edge) {
        event.preventDefault()
        ;(event.shiftKey ? nodes[nodes.length - 1] : nodes[0]).focus()
      }
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('keydown', onKeyDown, true)
      document.body.style.overflow = overflow
      previous?.focus?.()
    }
  }, [onClose])

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <form
        ref={dialogRef}
        className={`modal ${tone === 'danger' ? 'modal-danger' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        onMouseDown={(event) => event.stopPropagation()}
        onSubmit={onSubmit ?? ((event) => event.preventDefault())}
      >
        <button type="button" className="icon-button modal-close" onClick={onClose} aria-label="Fermer">
          <X size={18} />
        </button>
        {icon && <span className="modal-icon">{icon}</span>}
        <h2 id={titleId}>{title}</h2>
        {description && <p id={descriptionId}>{description}</p>}
        {children}
        {actions && <div className="modal-actions">{actions}</div>}
      </form>
    </div>
  )
}

type ConfirmProps = {
  title: string
  description: ReactNode
  confirmLabel: string
  icon?: ReactNode
  busy?: boolean
  onConfirm: () => void
  onClose: () => void
}

export function ConfirmDialog({ title, description, confirmLabel, icon, busy, onConfirm, onClose }: ConfirmProps) {
  return (
    <Modal
      title={title}
      description={description}
      icon={icon}
      tone="danger"
      onClose={onClose}
      onSubmit={(event) => {
        event.preventDefault()
        onConfirm()
      }}
      actions={
        <>
          <button type="button" className="button button-secondary" onClick={onClose}>Annuler</button>
          <button className="button button-danger" disabled={busy}>{busy ? 'Suppression…' : confirmLabel}</button>
        </>
      }
    />
  )
}
