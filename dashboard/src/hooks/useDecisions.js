import { useState, useEffect, useCallback } from 'react'

/**
 * Poll /api/pm/messages/pending every 10 seconds.
 */
export function useDecisions() {
  const [pending, setPending] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetch_ = useCallback(async () => {
    try {
      const res = await fetch('/api/pm/messages/pending')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setPending(data.messages || [])
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const respond = useCallback(async (messageId, response) => {
    const res = await fetch(`/api/pm/messages/${messageId}/respond`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ response }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    // Refresh after responding
    await fetch_()
    return res.json()
  }, [fetch_])

  useEffect(() => {
    fetch_()
    const interval = setInterval(fetch_, 10000)
    return () => clearInterval(interval)
  }, [fetch_])

  return { pending, loading, error, refresh: fetch_, respond }
}
