import { RefObject, useEffect } from 'react'

/** Closes a popover when the pointer lands outside of it or Escape is pressed.
 *  Menus that stay open behind the next click are the classic dashboard bug. */
export function useDismissable(
  ref: RefObject<HTMLElement>,
  open: boolean,
  onClose: () => void,
): void {
  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent | TouchEvent) => {
      if (!ref.current?.contains(event.target as Node)) onClose()
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('touchstart', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('touchstart', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [ref, open, onClose])
}
