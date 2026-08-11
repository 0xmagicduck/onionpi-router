import { useCallback, useEffect, useRef, useState } from 'react'

export function usePolling<T>(loader: () => Promise<T>, interval: number) {
  const [data, setData] = useState<T>()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const loaderRef = useRef(loader)
  const mountedRef = useRef(false)
  const inFlightRef = useRef<Promise<void> | null>(null)

  useEffect(() => {
    loaderRef.current = loader
  }, [loader])

  const refresh = useCallback((): Promise<void> => {
    if (inFlightRef.current) return inFlightRef.current
    const request = loaderRef.current()
      .then((value) => {
        if (!mountedRef.current) return
        setData(value)
        setError('')
      })
      .catch((reason: unknown) => {
        if (!mountedRef.current) return
        setError(reason instanceof Error ? reason.message : 'Données indisponibles')
      })
      .finally(() => {
        if (mountedRef.current) setLoading(false)
        if (inFlightRef.current === request) inFlightRef.current = null
      })
    inFlightRef.current = request
    return request
  }, [])

  useEffect(() => {
    mountedRef.current = true
    let cancelled = false
    let timer = 0
    const poll = async () => {
      if (!document.hidden) await refresh()
      if (!cancelled) timer = window.setTimeout(() => void poll(), interval)
    }
    const onVisibilityChange = () => {
      if (!document.hidden) void refresh()
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    void poll()
    return () => {
      cancelled = true
      mountedRef.current = false
      window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [interval, refresh])

  return { data, error, loading, refresh }
}
