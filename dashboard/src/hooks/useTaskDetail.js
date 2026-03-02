import { useState, useCallback } from 'react'

export function useTaskDetail() {
  const [task, setTask] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetchTask = useCallback(async (taskId) => {
    if (!taskId) {
      setTask(null)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/tasks/${taskId}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setTask(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const clearTask = useCallback(() => {
    setTask(null)
    setError(null)
  }, [])

  return { task, loading, error, fetchTask, clearTask }
}
