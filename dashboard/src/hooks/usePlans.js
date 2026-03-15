import { useState, useEffect, useCallback } from 'react'

/**
 * Fetch plan report, cost report, subsystem health, and plan list.
 * Polls every 30 seconds.
 */
export function usePlans() {
  const [report, setReport] = useState(null)
  const [cost, setCost] = useState(null)
  const [health, setHealth] = useState(null)
  const [plans, setPlans] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchAll = useCallback(async () => {
    try {
      const [reportRes, costRes, healthRes, plansRes] = await Promise.allSettled([
        fetch('/api/plans/report').then(r => r.json()),
        fetch('/api/plans/cost').then(r => r.json()),
        fetch('/api/plans/subsystem-health').then(r => r.json()),
        fetch('/api/plans?limit=100').then(r => r.json()),
      ])

      if (reportRes.status === 'fulfilled') setReport(reportRes.value)
      if (costRes.status === 'fulfilled') setCost(costRes.value)
      if (healthRes.status === 'fulfilled') setHealth(healthRes.value)
      if (plansRes.status === 'fulfilled') setPlans(plansRes.value.plans || [])
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAll()
    const interval = setInterval(fetchAll, 30000)
    return () => clearInterval(interval)
  }, [fetchAll])

  return { report, cost, health, plans, loading, error, refresh: fetchAll }
}
