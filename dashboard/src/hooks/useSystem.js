import { useState, useEffect, useCallback } from 'react'

/**
 * Fetch with AbortController timeout and response.ok check.
 */
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

/**
 * Poll /api/brain/health and /api/infra/status every 30 seconds.
 */
export function useSystem() {
  const [brainHealth, setBrainHealth] = useState(null)
  const [infraStatus, setInfraStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [brainError, setBrainError] = useState(null)
  const [infraError, setInfraError] = useState(null)

  const fetchAll = useCallback(async () => {
    const [brainRes, infraRes] = await Promise.allSettled([
      fetchWithTimeout('/api/brain/health', 5000),
      fetchWithTimeout('/api/infra/status', 5000),
    ])

    if (brainRes.status === 'fulfilled') {
      setBrainHealth(brainRes.value)
      setBrainError(null)
    } else {
      setBrainError(brainRes.reason?.message || 'Failed to reach brain health endpoint')
    }

    if (infraRes.status === 'fulfilled') {
      setInfraStatus(infraRes.value)
      setInfraError(null)
    } else {
      setInfraError(infraRes.reason?.message || 'Failed to reach infra status endpoint')
    }

    setLoading(false)
  }, [])

  useEffect(() => {
    fetchAll()
    const interval = setInterval(fetchAll, 30000)
    return () => clearInterval(interval)
  }, [fetchAll])

  return { brainHealth, infraStatus, loading, brainError, infraError, refresh: fetchAll }
}
