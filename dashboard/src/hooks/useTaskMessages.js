import { useState, useEffect, useCallback, useRef } from 'react'

const POLL_MS = 4000

export function useTaskMessages(taskId, taskStatus) {
  const [messages, setMessages] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const intervalRef = useRef(null)

  const fetchMessages = useCallback(async () => {
    if (!taskId) return
    setLoading(true)
    try {
      const res = await fetch(`/api/tasks/${taskId}/messages`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setMessages(data.messages || [])
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [taskId])

  useEffect(() => {
    if (!taskId) {
      setMessages([])
      return
    }
    fetchMessages()
    // Poll for active tasks
    const isActive = taskStatus === 'working' || taskStatus === 'pending' || taskStatus === 'waiting_for_pm'
    if (isActive) {
      intervalRef.current = setInterval(fetchMessages, POLL_MS)
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [taskId, taskStatus, fetchMessages])

  return { messages, loading, error }
}
