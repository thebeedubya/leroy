import { useState, useEffect, useCallback, useRef } from 'react'

const TASKS_POLL_MS = 5000

export function useTasks() {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [refreshCount, setRefreshCount] = useState(0)
  const intervalRef = useRef(null)

  const fetchTasks = useCallback(async () => {
    try {
      const res = await fetch('/api/tasks')
      if (!res.ok) {
        const body = await res.text()
        throw new Error(`HTTP ${res.status}: ${body || res.statusText}`)
      }
      const data = await res.json()
      const taskList = Array.isArray(data) ? data : (data.tasks || [])
      setTasks(taskList)
      setLastUpdated(new Date())
      setError(null)
      setRefreshCount((c) => c + 1)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchTasks()
    intervalRef.current = setInterval(fetchTasks, TASKS_POLL_MS)
    return () => clearInterval(intervalRef.current)
  }, [fetchTasks])

  return { tasks, setTasks, loading, error, lastUpdated, refreshCount, refresh: fetchTasks }
}
