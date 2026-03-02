import { useState, useEffect, useRef, useCallback } from 'react'

/**
 * Load activity feed from /api/activity, then stream updates via /api/activity/stream SSE.
 */
export function useActivity() {
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [connected, setConnected] = useState(false)
  const esRef = useRef(null)
  const reconnectTimer = useRef(null)

  const loadInitial = useCallback(async () => {
    try {
      const res = await fetch('/api/activity?limit=100')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setEvents(data.events || [])
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const connectSSE = useCallback(() => {
    if (esRef.current) {
      esRef.current.close()
    }

    const es = new EventSource('/api/activity/stream')
    esRef.current = es

    es.onopen = () => {
      setConnected(true)
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = null
      }
    }

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'activity_snapshot') {
          setEvents(data.events || [])
          setLoading(false)
        } else if (data.type === 'activity_event' && data.event) {
          setEvents((prev) => [data.event, ...prev].slice(0, 500))
        }
        // heartbeat: ignore
      } catch (err) {
        // parse error, ignore
      }
    }

    es.onerror = () => {
      setConnected(false)
      es.close()
      esRef.current = null
      reconnectTimer.current = setTimeout(connectSSE, 5000)
    }
  }, [])

  useEffect(() => {
    loadInitial()
    connectSSE()
    return () => {
      if (esRef.current) esRef.current.close()
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
    }
  }, [loadInitial, connectSSE])

  return { events, loading, error, connected }
}
