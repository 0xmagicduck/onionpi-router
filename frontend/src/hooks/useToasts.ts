import { useCallback, useRef, useState } from 'react'

export type Toast = { id: number; message: string; error: boolean }

const LIFETIME_MS = 5000

/** Stack of transient confirmations. Kept in one place so every screen keeps
 *  the same `notify(message, error?)` signature it already used. */
export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(1)
  const timers = useRef(new Map<number, number>())

  const dismiss = useCallback((id: number) => {
    const timer = timers.current.get(id)
    if (timer) window.clearTimeout(timer)
    timers.current.delete(id)
    setToasts((current) => current.filter((toast) => toast.id !== id))
  }, [])

  const notify = useCallback((message: string, error = false) => {
    const id = nextId.current++
    // Three at a time is the point where the stack stops being readable.
    setToasts((current) => [...current.slice(-2), { id, message, error }])
    timers.current.set(id, window.setTimeout(() => dismiss(id), LIFETIME_MS))
  }, [dismiss])

  return { toasts, notify, dismiss }
}
