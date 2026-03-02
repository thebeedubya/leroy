import { useState, useEffect, useCallback } from 'react'

/**
 * Poll /api/specs every 15 seconds.
 */
export function useSpecs() {
  const [specs, setSpecs] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetch_ = useCallback(async () => {
    try {
      const res = await fetch('/api/specs')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setSpecs(data.specs || [])
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetch_()
    const interval = setInterval(fetch_, 15000)
    return () => clearInterval(interval)
  }, [fetch_])

  return { specs, loading, error, refresh: fetch_ }
}
