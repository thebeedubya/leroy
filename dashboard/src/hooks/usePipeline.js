import { useState, useEffect, useCallback } from 'react'
import { getTaskTitle } from '../utils'

// Stage order for progress dots
const STAGE_ORDER = ['draft', 'gate', 'sent', 'building', 'qa', 'retro', 'persist', 'done']

/**
 * Fallback: map a task's status to a pipeline stage (used when API doesn't return pipeline_stage).
 */
function mapStatusToStage(status) {
  if (status === 'pending') return 'sent'
  if (status === 'working' || status === 'waiting_for_pm') return 'building'
  if (status === 'blocked') return 'building'
  if (status === 'qa_review' || status === 'completed_unverified') return 'qa'
  if (status === 'failed' || status === 'cancelled') return 'done'
  if (status === 'completed') return 'done'
  return 'sent'
}

/**
 * Map a task to pipeline stage metadata.
 * Prefers server-computed pipeline_stage; falls back to client-side status mapping.
 * Returns { stage, isFailed, isBlocked, isZombie } or null to exclude the task.
 */
function mapTaskToStage(task) {
  const s = task.status
  // Exclude ideas and cancelled tasks from pipeline view (unless API says otherwise)
  if (!task.pipeline_stage && (s === 'cancelled' || s === 'idea')) return null

  const isFailed = s === 'failed'
  const isBlocked = s === 'blocked'
  const isZombie = task.pipeline_is_zombie || false

  // Use server-computed stage; map 'zombie' to 'building' with isZombie flag
  let stage = task.pipeline_stage || mapStatusToStage(s)
  if (stage === 'zombie') stage = 'building'

  // Exclude ideas and cancelled from pipeline view (API may return 'draft' for idea)
  if (stage === 'draft' && s === 'idea') return null
  if (s === 'cancelled' && !task.pipeline_stage) return null

  return { stage, isFailed, isBlocked, isZombie }
}

/**
 * Calculate time a task has been in its current stage (ms).
 * Uses updated_at if available, otherwise created_at.
 */
function timeInStage(task) {
  const ref = task.updated_at || task.created_at
  if (!ref) return 0
  return Date.now() - Date.parse(ref)
}

/**
 * Format milliseconds to a short human-readable string.
 */
export function formatDuration(ms) {
  if (ms < 0 || ms == null) return '—'
  if (ms < 60000) return `${Math.round(ms / 1000)}s`
  if (ms < 3600000) return `${Math.floor(ms / 60000)}m`
  return `${Math.floor(ms / 3600000)}h ${Math.floor((ms % 3600000) / 60000)}m`
}

/**
 * Time badge tier: ok (<15m), warn (<60m), critical (>=60m)
 */
export function timeTier(ms) {
  if (ms < 15 * 60 * 1000) return 'ok'
  if (ms < 60 * 60 * 1000) return 'warn'
  return 'critical'
}

/**
 * Median of a numeric array.
 */
function median(arr) {
  if (!arr.length) return 0
  const sorted = [...arr].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid]
}

/**
 * Transform raw tasks array into pipeline data structure.
 */
function transformTasks(rawTasks) {
  const now = Date.now()
  const oneDay = 86400000
  const twoDays = 172800000
  const sevenDays = 7 * oneDay

  // Enrich each task with pipeline metadata
  const enriched = rawTasks
    .map((task) => {
      const mapped = mapTaskToStage(task)
      if (!mapped) return null
      const { stage, isFailed, isBlocked, isZombie } = mapped
      const stageIdx = STAGE_ORDER.indexOf(stage)

      // Prefer server-computed age; fall back to client-side calculation
      const tis = task.pipeline_age_seconds != null
        ? task.pipeline_age_seconds * 1000
        : timeInStage(task)

      // Build stage history for waterfall (simplified proxy from created_at)
      const totalMs = task.completed_at
        ? Date.parse(task.completed_at) - Date.parse(task.created_at)
        : tis

      return {
        ...task,
        _stage: stage,
        _stageIdx: stageIdx,
        _isFailed: isFailed,
        _isBlocked: isBlocked,
        _isZombie: isZombie,
        _timeInStage: tis,
        _timeTier: timeTier(tis),
        _totalMs: totalMs,
        _shortId: task.task_id ? task.task_id.slice(0, 8) : '?',
        _title: getTaskTitle(task),
        // Pipeline lifecycle flags from server
        _hasRetro: task.pipeline_has_retro || false,
        _brainPersisted: task.pipeline_brain_persisted || false,
        _passRate: task.pipeline_pass_rate || null,
      }
    })
    .filter(Boolean)

  // Bucket into stages
  const stages = {
    draft: [],
    gate: [],
    sent: [],
    building: [],
    qa: [],
    retro: [],
    persist: [],
    done: [],
  }
  enriched.forEach((t) => {
    if (stages[t._stage]) stages[t._stage].push(t)
  })

  // --- Metrics ---
  const inFlight = stages.sent.length + stages.building.length + stages.qa.length
  const blocked = enriched.filter((t) => t._isBlocked).length

  // Completed today
  const completedToday = enriched.filter((t) => {
    if (t._stage !== 'done') return false
    if (!t.completed_at) return false
    return now - Date.parse(t.completed_at) < oneDay
  }).length

  // Avg cycle time (median, last 7 days)
  const recentCompleted = enriched.filter((t) => {
    if (t._stage !== 'done') return false
    if (!t.completed_at || !t.created_at) return false
    return now - Date.parse(t.completed_at) < sevenDays
  })
  const cycleTimes = recentCompleted.map((t) => Date.parse(t.completed_at) - Date.parse(t.created_at))
  const avgCycleTime = cycleTimes.length ? median(cycleTimes) : null

  // First pass rate: tasks in done stage that do NOT have status===failed anywhere in visible data
  // Proxy: tasks that are done (completed) vs tasks that appear in failed state
  const totalResolved = enriched.filter((t) => t._stage === 'done' || t._isFailed).length
  const passedClean = enriched.filter((t) => t._stage === 'done').length
  const firstPassRate = totalResolved > 0 ? Math.round((passedClean / totalResolved) * 100) : null

  // Retros pending: tasks in retro or persist stage (server-computed), or fallback to 48h heuristic
  const retrosPending = enriched.filter((t) => {
    // If server provides lifecycle stages, use them directly
    if (t.pipeline_stage) {
      return t.pipeline_stage === 'retro' || t.pipeline_stage === 'persist'
    }
    // Fallback: completed in last 48h without a retro
    if (t._stage !== 'done') return false
    if (!t.completed_at) return false
    return now - Date.parse(t.completed_at) < twoDays
  }).length

  // Dwell time per active stage (for bottleneck detection)
  const activeStageDwells = ['sent', 'building', 'qa'].reduce((acc, s) => {
    const items = stages[s]
    if (!items.length) return acc
    const avg = items.reduce((sum, t) => sum + t._timeInStage, 0) / items.length
    acc[s] = avg
    return acc
  }, {})

  return {
    stages,
    tasks: enriched,
    metrics: {
      inFlight,
      completedToday,
      avgCycleTime,
      firstPassRate,
      blocked,
      retrosPending,
      lastBrainPersist: null, // populated separately
    },
    activeStageDwells,
  }
}

/**
 * Custom hook: fetches /api/tasks and transforms into pipeline data.
 * Polls every 15 seconds.
 * Returns { stages, metrics, tasks, loading, error, refresh }
 */
export function usePipeline() {
  const [stages, setStages] = useState({
    draft: [], gate: [], sent: [], building: [], qa: [], retro: [], persist: [], done: [],
  })
  const [metrics, setMetrics] = useState({
    inFlight: 0, completedToday: 0, avgCycleTime: null,
    firstPassRate: null, blocked: 0, retrosPending: 0, lastBrainPersist: null,
  })
  const [activeStageDwells, setActiveStageDwells] = useState({})
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchPersistLast = useCallback(async () => {
    try {
      const res = await fetch('http://localhost:9801/persist/last?source=pm')
      if (!res.ok) return null
      const data = await res.json()
      return data.timestamp || data.last_persisted || null
    } catch {
      return null
    }
  }, [])

  const fetch_ = useCallback(async () => {
    try {
      const [tasksRes, lastPersist] = await Promise.all([
        fetch('/api/tasks'),
        fetchPersistLast(),
      ])
      if (!tasksRes.ok) throw new Error(`HTTP ${tasksRes.status}`)
      const data = await tasksRes.json()
      const rawTasks = Array.isArray(data) ? data : (data.tasks || [])
      const { stages: s, tasks: t, metrics: m, activeStageDwells: asd } = transformTasks(rawTasks)
      setStages(s)
      setTasks(t)
      setActiveStageDwells(asd)
      setMetrics({ ...m, lastBrainPersist: lastPersist })
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [fetchPersistLast])

  useEffect(() => {
    fetch_()
    const interval = setInterval(fetch_, 15000)
    return () => clearInterval(interval)
  }, [fetch_])

  return { stages, metrics, tasks, activeStageDwells, loading, error, refresh: fetch_ }
}
