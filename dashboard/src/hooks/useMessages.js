import { useState, useEffect, useCallback } from 'react'

/**
 * Poll /api/messages every 10 seconds.
 * Fetches all messages with optional filters.
 */
export function useMessages() {
  const [messages, setMessages] = useState([])
  const [agents, setAgents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState({ to: '', from: '', pending: false, unread: false })

  const fetchMessages = useCallback(async () => {
    try {
      const params = new URLSearchParams()
      if (filter.to) params.set('to', filter.to)
      if (filter.from) params.set('from', filter.from)
      if (filter.pending) params.set('pending', 'true')
      if (filter.unread) params.set('unread', 'true')
      params.set('limit', '100')

      const res = await fetch(`/api/messages?${params}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setMessages(data.messages || [])
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [filter])

  const fetchAgents = useCallback(async () => {
    try {
      const res = await fetch('/api/messages/agents')
      if (!res.ok) return
      const data = await res.json()
      setAgents(data.agents || [])
    } catch {
      // non-fatal
    }
  }, [])

  const markRead = useCallback(async (messageId) => {
    try {
      await fetch(`/api/messages/${messageId}/read`, { method: 'POST' })
      await fetchMessages()
    } catch {
      // non-fatal
    }
  }, [fetchMessages])

  const respond = useCallback(async (messageId, response, responder = 'pm') => {
    const res = await fetch(`/api/messages/${messageId}/respond`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from: responder, content: response }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    await fetchMessages()
    return res.json()
  }, [fetchMessages])

  useEffect(() => {
    fetchMessages()
    fetchAgents()
    const interval = setInterval(() => {
      fetchMessages()
      fetchAgents()
    }, 10000)
    return () => clearInterval(interval)
  }, [fetchMessages, fetchAgents])

  return { messages, agents, loading, error, filter, setFilter, refresh: fetchMessages, markRead, respond }
}
