import { useState, useEffect, useCallback } from 'react'

/**
 * Fetch v2 metrics: quality scoring, improvement engine, baseline comparison.
 * Polls every 60 seconds (heavier queries than other hooks).
 */
export function useV2Metrics() {
  const [quality, setQuality] = useState(null)
  const [suggestions, setSuggestions] = useState([])
  const [templates, setTemplates] = useState([])
  const [baseline, setBaseline] = useState(null)
  const [thresholds, setThresholds] = useState(null)
  const [patterns, setPatterns] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchAll = useCallback(async () => {
    try {
      const [qualityRes, suggestionsRes, templatesRes, baselineRes, thresholdsRes, patternsRes] =
        await Promise.allSettled([
          fetch('/api/quality/metrics').then(r => r.json()),
          fetch('/api/improvement/suggestions').then(r => r.json()),
          fetch('/api/improvement/templates').then(r => r.json()),
          fetch('/api/improvement/baseline').then(r => r.json()),
          fetch('/api/improvement/thresholds').then(r => r.json()),
          fetch('/api/improvement/patterns').then(r => r.json()),
        ])

      if (qualityRes.status === 'fulfilled') setQuality(qualityRes.value)
      if (suggestionsRes.status === 'fulfilled') setSuggestions(suggestionsRes.value.suggestions || [])
      if (templatesRes.status === 'fulfilled') setTemplates(templatesRes.value.templates || [])
      if (baselineRes.status === 'fulfilled') setBaseline(baselineRes.value)
      if (thresholdsRes.status === 'fulfilled') setThresholds(thresholdsRes.value)
      if (patternsRes.status === 'fulfilled') setPatterns(patternsRes.value)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAll()
    const interval = setInterval(fetchAll, 60000)
    return () => clearInterval(interval)
  }, [fetchAll])

  return { quality, suggestions, templates, baseline, thresholds, patterns, loading, error, refresh: fetchAll }
}
