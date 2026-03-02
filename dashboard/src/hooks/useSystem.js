import { useState, useEffect, useCallback } from 'react'

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
      fetch('/api/brain/health').then((r) => r.json()),
      fetch('/api/infra/status').then((r) => r.json()),
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
