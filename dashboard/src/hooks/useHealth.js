import { useState, useEffect, useCallback } from 'react'

const HEALTH_POLL_MS = 30000

export function useHealth() {
  const [health, setHealth] = useState(null)
  const [error, setError] = useState(null)
  const [checking, setChecking] = useState(true)

  const fetchHealth = useCallback(async () => {
    setChecking(true)
    try {
      // /api/health proxies to 127.0.0.1:9800/health (no auth required)
      const res = await fetch('/api/health')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setHealth(data)
      setError(null)
    } catch (err) {
      setError(err.message)
      setHealth(null)
    } finally {
      setChecking(false)
    }
  }, [])

  useEffect(() => {
    fetchHealth()
    const interval = setInterval(fetchHealth, HEALTH_POLL_MS)
    return () => clearInterval(interval)
  }, [fetchHealth])

  return { health, error, checking }
}
