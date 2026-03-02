import { useState, useEffect, useCallback, useRef } from 'react'

const POLL_MS = 3000

export function useTaskSubtasks(taskId, taskStatus) {
  const [subtasks, setSubtasks] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const intervalRef = useRef(null)

  const fetchSubtasks = useCallback(async () => {
    if (!taskId) return
    setLoading(true)
    try {
      const res = await fetch(`/api/tasks/${taskId}/subtasks`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setSubtasks(data.subtasks || [])
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [taskId])

  useEffect(() => {
    if (!taskId) {
      setSubtasks([])
      return
    }
    fetchSubtasks()
    // Poll if task is active
    const isActive = taskStatus === 'working' || taskStatus === 'pending' || taskStatus === 'waiting_for_pm'
    if (isActive) {
      intervalRef.current = setInterval(fetchSubtasks, POLL_MS)
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [taskId, taskStatus, fetchSubtasks])

  return { subtasks, loading, error }
}
