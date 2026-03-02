import { useState, useEffect, useCallback, useRef } from 'react'

/**
 * Polls /api/pm/proposals for pending and recently-decided proposals.
 * Pending: every 10s. Recent (all): every 30s.
 */
export function useProposals() {
  const [pending, setPending] = useState([])
  const [recent, setRecent] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchPending = useCallback(async () => {
    try {
      const res = await fetch('/api/pm/proposals?status=pending')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setPending(data.proposals || [])
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchRecent = useCallback(async () => {
    try {
      const res = await fetch('/api/pm/proposals?status=all&limit=5')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      // Recent = non-pending proposals only
      const resolved = (data.proposals || []).filter((p) => p.status !== 'pending')
      setRecent(resolved.slice(0, 5))
    } catch {
      // silently fail for recent — non-critical
    }
  }, [])

  const approve = useCallback(async (proposalId) => {
    const res = await fetch(`/api/pm/proposals/${proposalId}/approve`, { method: 'POST' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    await Promise.all([fetchPending(), fetchRecent()])
    return res.json()
  }, [fetchPending, fetchRecent])

  const reject = useCallback(async (proposalId, feedback) => {
    const res = await fetch(`/api/pm/proposals/${proposalId}/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ feedback }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    await Promise.all([fetchPending(), fetchRecent()])
    return res.json()
  }, [fetchPending, fetchRecent])

  const recentTimerRef = useRef(null)

  useEffect(() => {
    fetchPending()
    fetchRecent()
    const pendingInterval = setInterval(fetchPending, 10000)
    recentTimerRef.current = setInterval(fetchRecent, 30000)
    return () => {
      clearInterval(pendingInterval)
      clearInterval(recentTimerRef.current)
    }
  }, [fetchPending, fetchRecent])

  return { pending, recent, loading, error, approve, reject, refresh: fetchPending }
}
