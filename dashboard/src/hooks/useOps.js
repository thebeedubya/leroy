import { useState, useEffect, useCallback } from 'react'

function fetchWithTimeout(url, timeoutMs = 5000) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  return fetch(url, { signal: controller.signal })
    .then((r) => {
      clearTimeout(timer)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return r.json()
    })
    .catch((err) => {
      clearTimeout(timer)
      if (err.name === 'AbortError') throw new Error('Request timed out (5s)')
      throw err
    })
}

export function useOps() {
  const [sessions, setSessions] = useState(null)
  const [toolStats, setToolStats] = useState(null)
  const [errors, setErrors] = useState(null)
  const [volume, setVolume] = useState(null)
  const [timeline, setTimeline] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchAll = useCallback(async () => {
    const [sessRes, statsRes, errRes, volRes, timeRes] = await Promise.allSettled([
      fetchWithTimeout('/api/ops/sessions', 5000),
      fetchWithTimeout('/api/ops/tool-stats', 5000),
      fetchWithTimeout('/api/ops/errors', 5000),
      fetchWithTimeout('/api/ops/volume', 5000),
      fetchWithTimeout('/api/ops/timeline', 5000),
    ])

    if (sessRes.status === 'fulfilled') setSessions(sessRes.value)
    if (statsRes.status === 'fulfilled') setToolStats(statsRes.value)
    if (errRes.status === 'fulfilled') setErrors(errRes.value)
    if (volRes.status === 'fulfilled') setVolume(volRes.value)
    if (timeRes.status === 'fulfilled') setTimeline(timeRes.value)

    const anyFailed = [sessRes, statsRes, errRes, volRes, timeRes].some(r => r.status === 'rejected')
    if (anyFailed) {
      setError('Some ops endpoints unavailable')
    } else {
      setError(null)
    }

    setLoading(false)
  }, [])

  useEffect(() => {
    fetchAll()
    const interval = setInterval(fetchAll, 30000)
    return () => clearInterval(interval)
  }, [fetchAll])

  return { sessions, toolStats, errors, volume, timeline, loading, error, refresh: fetchAll }
}
