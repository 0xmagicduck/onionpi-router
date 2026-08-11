import { useCallback, useEffect, useState } from 'react'

export function usePolling<T>(loader: () => Promise<T>, interval: number) {
  const [data, setData] = useState<T>()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      const value = await loader()
      setData(value)
      setError('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Données indisponibles')
    } finally {
      setLoading(false)
    }
  }, [loader])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(refresh, interval)
    return () => window.clearInterval(timer)
  }, [interval, refresh])

  return { data, error, loading, refresh }
}
