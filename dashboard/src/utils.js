/**
 * Extract a readable title from a task's spec text.
 * Looks for the first non-empty line; strips markdown heading markers.
 */
export function getTaskTitle(task) {
  if (!task.spec) return task.task_id.slice(0, 12)
  const lines = task.spec.trim().split('\n')
  for (const line of lines) {
    const trimmed = line.trim()
    if (trimmed) {
      if (trimmed.startsWith('#')) return trimmed.replace(/^#+\s*/, '').trim().slice(0, 100)
      return trimmed.slice(0, 100)
    }
  }
  return task.task_id.slice(0, 12)
}

/**
 * Calculate human-readable duration for a task.
 * If not completed, calculates elapsed time from created_at to now.
 */
export function getDuration(task) {
  if (!task.created_at) return null
  const start = new Date(task.created_at)
  const end = task.completed_at ? new Date(task.completed_at) : new Date()
  const ms = end - start
  if (ms < 0) return null
  if (ms < 1000) return '<1s'
  if (ms < 60000) return `${Math.round(ms / 1000)}s`
  const mins = Math.floor(ms / 60000)
  const secs = Math.round((ms % 60000) / 1000)
  if (mins < 60) return `${mins}m ${secs}s`
  const hrs = Math.floor(mins / 60)
  return `${hrs}h ${mins % 60}m`
}

/**
 * Format an ISO timestamp to a readable local time string.
 */
export function formatTimestamp(iso) {
  if (!iso) return '--'
  const d = new Date(iso)
  return d.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

/**
 * Format uptime in seconds to a readable string.
 */
export function formatUptime(seconds) {
  if (!seconds && seconds !== 0) return null
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return `${h}h ${m}m`
}

/**
 * Extract first N lines of result for card preview.
 */
export function getResultPreview(result, maxLines = 2) {
  if (!result) return null
  const lines = result.split('\n').filter((l) => l.trim()).slice(0, maxLines)
  return lines.join(' ').slice(0, 140) || null
}

/**
 * Scan result text for PASS/FAIL markers to build success criteria summary.
 * Returns { passes, fails, total } or null if no markers found.
 */
export function parseSuccessCriteria(result) {
  if (!result) return null
  const passCount = (result.match(/\bPASS\b/gi) || []).length
  const failCount = (result.match(/\bFAIL\b(?!URE)/gi) || []).length
  if (passCount + failCount === 0) return null
  return { passes: passCount, fails: failCount, total: passCount + failCount }
}

/**
 * Status metadata for display.
 */
export const STATUS_CONFIG = {
  pending: {
    label: 'PENDING',
    columnLabel: 'PENDING',
    textColor: 'text-amber-400',
    bgColor: 'bg-amber-400/10',
    borderColor: 'border-amber-400/25',
    dotColor: 'bg-amber-400',
    headerColor: 'text-amber-400',
    pulse: true,
  },
  waiting_for_pm: {
    label: 'WAITING PM',
    columnLabel: 'WAITING FOR PM',
    textColor: 'text-purple-400',
    bgColor: 'bg-purple-400/10',
    borderColor: 'border-purple-400/25',
    dotColor: 'bg-purple-400',
    headerColor: 'text-purple-400',
    pulse: true,
  },
  qa_review: {
    label: 'QA REVIEW',
    columnLabel: 'QA REVIEW',
    textColor: 'text-cyan-400',
    bgColor: 'bg-cyan-400/10',
    borderColor: 'border-cyan-400/25',
    dotColor: 'bg-cyan-400',
    headerColor: 'text-cyan-400',
    pulse: false,
  },
  working: {
    label: 'RUNNING',
    columnLabel: 'EXECUTING',
    textColor: 'text-blue-400',
    bgColor: 'bg-blue-400/10',
    borderColor: 'border-blue-400/25',
    dotColor: 'bg-blue-400',
    headerColor: 'text-blue-400',
    pulse: true,
  },
  completed: {
    label: 'DONE',
    columnLabel: 'COMPLETED',
    textColor: 'text-emerald-400',
    bgColor: 'bg-emerald-400/10',
    borderColor: 'border-emerald-400/25',
    dotColor: 'bg-emerald-400',
    headerColor: 'text-emerald-400',
    pulse: false,
  },
  failed: {
    label: 'FAILED',
    columnLabel: 'FAILED',
    textColor: 'text-red-400',
    bgColor: 'bg-red-400/10',
    borderColor: 'border-red-400/25',
    dotColor: 'bg-red-400',
    headerColor: 'text-red-400',
    pulse: false,
  },
  cancelled: {
    label: 'CANCELLED',
    columnLabel: 'CANCELLED',
    textColor: 'text-slate-500',
    bgColor: 'bg-slate-500/10',
    borderColor: 'border-slate-500/25',
    dotColor: 'bg-slate-500',
    headerColor: 'text-slate-400',
    pulse: false,
  },
}

export function getStatusConfig(status) {
  return STATUS_CONFIG[status] || STATUS_CONFIG.cancelled
}

/**
 * Calculate duration for a subtask.
 * Running subtasks show elapsed from started_at to now.
 * Completed subtasks show started_at to completed_at.
 */
export function getSubtaskDuration(subtask) {
  if (!subtask.started_at) return null
  const start = new Date(subtask.started_at)
  const end = subtask.completed_at ? new Date(subtask.completed_at) : new Date()
  const ms = end - start
  if (ms < 0) return null
  if (ms < 1000) return '<1s'
  if (ms < 60000) return `${Math.round(ms / 1000)}s`
  const mins = Math.floor(ms / 60000)
  const secs = Math.round((ms % 60000) / 1000)
  if (mins < 60) return `${mins}m ${secs}s`
  const hrs = Math.floor(mins / 60)
  return `${hrs}h ${mins % 60}m`
}

/**
 * Return a human-readable relative time string from an ISO timestamp.
 * E.g. "2m ago", "3h ago", "just now"
 */
export function relativeTime(iso) {
  if (!iso) return '—'
  const ms = Date.now() - new Date(iso).getTime()
  if (ms < 0) return 'just now'
  if (ms < 5000) return 'just now'
  if (ms < 60000) return `${Math.floor(ms / 1000)}s ago`
  if (ms < 3600000) return `${Math.floor(ms / 60000)}m ago`
  if (ms < 86400000) return `${Math.floor(ms / 3600000)}h ago`
  return `${Math.floor(ms / 86400000)}d ago`
}

/**
 * Sort tasks: working first, pending second, then by created_at descending.
 */
export function sortTasks(tasks) {
  const order = { working: 0, waiting_for_pm: 1, pending: 2, qa_review: 3, completed: 4, failed: 5, cancelled: 6 }
  return [...tasks].sort((a, b) => {
    const oa = order[a.status] ?? 5
    const ob = order[b.status] ?? 5
    if (oa !== ob) return oa - ob
    return new Date(b.created_at) - new Date(a.created_at)
  })
}
