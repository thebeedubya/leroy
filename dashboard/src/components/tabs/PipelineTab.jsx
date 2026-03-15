import React, { useState } from 'react'
import { usePipeline, formatDuration, timeTier } from '../../hooks/usePipeline'
import { relativeTime } from '../../utils'

// ---- Error Boundary ----------------------------------------------------------

class PipelineErrorBoundary extends React.Component {
  state = { hasError: false, error: null }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, info) {
    console.error('[PipelineTab] render error:', error, info)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex-1 overflow-y-auto p-6">
          <div className="bg-forge-card border border-red-400/25 rounded-lg p-6 max-w-lg">
            <h3 className="font-mono text-sm font-semibold text-red-400 mb-2">Pipeline tab render error</h3>
            <p className="font-mono text-xs text-slate-400 mb-4 break-all">
              {this.state.error?.message || 'Unknown error'}
            </p>
            <button
              onClick={() => this.setState({ hasError: false, error: null })}
              className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded font-mono text-xs text-slate-300 transition-colors"
            >
              Retry
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

// ---- Stage Config ------------------------------------------------------------

const STAGES = [
  { id: 'draft',    label: 'Draft',    bg: '#1e293b', color: '#94a3b8', border: '#475569' },
  { id: 'gate',     label: 'Gate',     bg: '#312e81', color: '#a5b4fc', border: '#4338ca' },
  { id: 'sent',     label: 'Sent',     bg: '#1e3a5f', color: '#7dd3fc', border: '#0369a1' },
  { id: 'building', label: 'Building', bg: '#164e63', color: '#67e8f9', border: '#0e7490' },
  { id: 'qa',       label: 'QA',       bg: '#134e4a', color: '#5eead4', border: '#0f766e' },
  { id: 'retro',    label: 'Retro',    bg: '#365314', color: '#bef264', border: '#4d7c0f' },
  { id: 'persist',  label: 'Persist',  bg: '#4c1d95', color: '#c4b5fd', border: '#6d28d9' },
  { id: 'done',     label: 'Done',     bg: '#14532d', color: '#4ade80', border: '#16a34a' },
]

const STAGE_IDX = Object.fromEntries(STAGES.map((s, i) => [s.id, i]))

// Card background styles per stage
const CARD_STYLES = {
  draft:    { bg: '#1e293b', color: '#94a3b8', accent: '#475569' },
  gate:     { bg: '#312e81', color: '#eef2ff', accent: '#818cf8' },
  sent:     { bg: '#1e3a5f', color: '#e0f2fe', accent: '#38bdf8' },
  building: { bg: '#164e63', color: '#ecfeff', accent: '#06b6d4' },
  qa:       { bg: '#134e4a', color: '#ecfdf5', accent: '#14b8a6' },
  retro:    { bg: '#365314', color: '#f7fee7', accent: '#84cc16' },
  persist:  { bg: '#4c1d95', color: '#f5f3ff', accent: '#a78bfa' },
  done:     { bg: '#14532d', color: '#ecfdf5', accent: '#22c55e' },
}

const FAILED_STYLE  = { bg: '#7f1d1d', color: '#fef2f2', accent: '#ef4444' }
const BLOCKED_STYLE = { bg: '#78350f', color: '#fffbeb', accent: '#f59e0b' }
const ZOMBIE_STYLE  = { bg: '#1f2937', color: '#6b7280', accent: '#374151' }

// ---- MetricsBar --------------------------------------------------------------

function MetricCard({ label, value, valueClass = '' }) {
  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      <span className={`font-mono text-2xl font-bold leading-none ${valueClass || 'text-slate-100'}`}>
        {value ?? '—'}
      </span>
      <span className="font-mono text-[10px] text-slate-500 uppercase tracking-wider whitespace-nowrap">{label}</span>
    </div>
  )
}

function MetricsBar({ metrics }) {
  const { inFlight, completedToday, avgCycleTime, firstPassRate, blocked, retrosPending, lastBrainPersist } = metrics

  const cycleDisplay = avgCycleTime != null ? formatDuration(avgCycleTime) : '—'
  const passDisplay  = firstPassRate  != null ? `${firstPassRate}%`        : '—'
  const persistDisplay = lastBrainPersist ? relativeTime(lastBrainPersist) : '—'

  return (
    <div
      className="flex gap-6 flex-wrap mb-6 p-4 rounded-lg border"
      style={{ background: '#1e293b', borderColor: '#334155' }}
    >
      <MetricCard label="In Flight"       value={inFlight}       valueClass="text-blue-400" />
      <div className="w-px bg-slate-700 self-stretch" />
      <MetricCard label="Done Today"      value={completedToday} valueClass="text-emerald-400" />
      <div className="w-px bg-slate-700 self-stretch" />
      <MetricCard label="Avg Cycle Time"  value={cycleDisplay}   valueClass="text-slate-100" />
      <div className="w-px bg-slate-700 self-stretch" />
      <MetricCard label="1st Pass Rate"   value={passDisplay}    valueClass={firstPassRate != null && firstPassRate >= 80 ? 'text-emerald-400' : 'text-yellow-400'} />
      <div className="w-px bg-slate-700 self-stretch" />
      <MetricCard label="Blocked"         value={blocked}        valueClass={blocked > 0 ? 'text-red-400' : 'text-slate-500'} />
      <div className="w-px bg-slate-700 self-stretch" />
      <MetricCard label="Retros Pending"  value={retrosPending}  valueClass={retrosPending > 0 ? 'text-yellow-400' : 'text-slate-500'} />
      <div className="w-px bg-slate-700 self-stretch" />
      <MetricCard label="Last Brain Sync" value={persistDisplay} valueClass="text-slate-400" />
    </div>
  )
}

// ---- TaskCard ----------------------------------------------------------------

const TIME_BADGE_STYLE = {
  ok:       { bg: '#166534', color: '#4ade80' },
  warn:     { bg: '#854d0e', color: '#fbbf24' },
  critical: { bg: '#991b1b', color: '#fca5a5' },
}

function TaskCardComponent({ task, isSelected, onClick }) {
  const stage = task._stage
  let style = CARD_STYLES[stage] || CARD_STYLES.sent
  if (task._isFailed)  style = FAILED_STYLE
  if (task._isBlocked) style = BLOCKED_STYLE
  // Note: zombie tasks are NOT rendered as individual cards (collapsed into summary row)

  const tier   = task._timeTier || 'ok'
  const tbStyle = TIME_BADGE_STYLE[tier]
  const currentIdx = STAGE_IDX[stage] ?? 0

  return (
    <div
      onClick={onClick}
      title={task._title}
      style={{
        background: style.bg,
        color: style.color,
        borderLeft: `3px solid ${style.accent}`,
        outline: isSelected ? `2px solid ${style.accent}` : 'none',
      }}
      className="w-full rounded p-1.5 cursor-pointer transition-transform hover:scale-[1.03] relative select-none"
    >
      {/* Time badge */}
      <span
        className="absolute top-1 right-1 font-mono text-[8px] font-semibold px-1 rounded"
        style={{ background: tbStyle.bg, color: tbStyle.color }}
      >
        {formatDuration(task._timeInStage)}
      </span>

      {/* Task name */}
      <div className="font-mono text-[10px] font-semibold pr-8 truncate leading-tight">
        {task._title}
      </div>

      {/* Short ID */}
      <div className="font-mono text-[8px] opacity-40 mt-0.5">{task._shortId}</div>

      {/* Progress dots */}
      <div className="flex gap-0.5 mt-1">
        {STAGES.map((s, i) => {
          let dotClass = 'bg-slate-700'
          if (i < currentIdx) dotClass = 'bg-green-400'
          else if (i === currentIdx) dotClass = 'bg-blue-400 animate-pulse'
          return (
            <span key={s.id} className={`inline-block w-1.5 h-1.5 rounded-full ${dotClass}`} />
          )
        })}
      </div>
    </div>
  )
}

// ---- ZombieSummaryRow --------------------------------------------------------

function ZombieSummaryRow({ zombies, onExpand, isExpanded }) {
  if (!zombies.length) return null
  return (
    <div>
      {/* Collapsed summary */}
      <button
        onClick={onExpand}
        className="w-full rounded p-1.5 cursor-pointer select-none text-left"
        style={{
          background: ZOMBIE_STYLE.bg,
          color: ZOMBIE_STYLE.color,
          borderLeft: `3px solid ${ZOMBIE_STYLE.accent}`,
          opacity: 0.8,
        }}
        title={`${zombies.length} zombie task(s) — stale > 4h with no activity`}
      >
        <div className="flex items-center justify-between">
          <span className="font-mono text-[10px] font-semibold">
            💀 {zombies.length} zombie task{zombies.length !== 1 ? 's' : ''} (stale &gt;4h)
          </span>
          <span className="font-mono text-[9px] text-slate-600">{isExpanded ? '▲' : '▼'}</span>
        </div>
      </button>

      {/* Expanded list */}
      {isExpanded && (
        <div className="mt-1 space-y-1 pl-1 border-l-2 border-slate-700">
          {zombies.map((task) => (
            <div
              key={task.task_id}
              className="rounded p-1 opacity-50"
              style={{
                background: ZOMBIE_STYLE.bg,
                color: ZOMBIE_STYLE.color,
                borderLeft: `2px solid ${ZOMBIE_STYLE.accent}`,
              }}
              title={task._title}
            >
              <div className="font-mono text-[9px] truncate">{task._title}</div>
              <div className="font-mono text-[8px] text-slate-700">{task._shortId}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ---- PipelineGrid ------------------------------------------------------------

function PipelineGrid({ stages, selectedTaskId, onSelectTask }) {
  const [zombiesExpanded, setZombiesExpanded] = React.useState(false)

  return (
    <div className="w-full overflow-x-auto mb-4">
      {/* Stage headers */}
      <div
        className="grid gap-1 mb-1"
        style={{ gridTemplateColumns: 'repeat(8, minmax(100px, 1fr))' }}
      >
        {STAGES.map((s) => {
          const allItems = stages[s.id] || []
          // For building column, show active + zombie total
          const count = allItems.length
          return (
            <div
              key={s.id}
              className="text-center py-2 px-1 font-mono text-[10px] font-semibold uppercase tracking-wider rounded-t"
              style={{ background: s.bg, color: s.color }}
            >
              {s.label}
              {count > 0 && (
                <span
                  className="ml-1 inline-block px-1.5 py-px rounded-full text-[9px]"
                  style={{ background: 'rgba(255,255,255,0.15)' }}
                >
                  {count}
                </span>
              )}
            </div>
          )
        })}
      </div>

      {/* Task cards in columns */}
      <div
        className="grid gap-1"
        style={{ gridTemplateColumns: 'repeat(8, minmax(100px, 1fr))' }}
      >
        {STAGES.map((s) => {
          const allItems = stages[s.id] || []

          // For building column: separate active tasks from zombies
          const activeItems = s.id === 'building'
            ? allItems.filter((t) => !t._isZombie)
            : allItems
          const zombieItems = s.id === 'building'
            ? allItems.filter((t) => t._isZombie)
            : []

          const isEmpty = activeItems.length === 0 && zombieItems.length === 0

          return (
            <div
              key={s.id}
              className="rounded-b p-1 min-h-[54px] space-y-1"
              style={{ background: '#111827', border: `1px solid ${s.border}22` }}
            >
              {isEmpty && (
                <div className="flex items-center justify-center h-10">
                  <span className="font-mono text-[9px] text-slate-700">—</span>
                </div>
              )}
              {activeItems.map((task) => (
                <TaskCardComponent
                  key={task.task_id}
                  task={task}
                  isSelected={selectedTaskId === task.task_id}
                  onClick={() => onSelectTask(task)}
                />
              ))}
              {/* Zombie collapse row at bottom of Building column */}
              {s.id === 'building' && zombieItems.length > 0 && (
                <ZombieSummaryRow
                  zombies={zombieItems}
                  isExpanded={zombiesExpanded}
                  onExpand={() => setZombiesExpanded((v) => !v)}
                />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---- BottleneckCallout -------------------------------------------------------

function BottleneckCallout({ activeStageDwells }) {
  const entries = Object.entries(activeStageDwells)
  if (entries.length < 2) return null

  const avg = entries.reduce((sum, [, v]) => sum + v, 0) / entries.length
  const [worstStage, worstDwell] = entries.reduce((best, cur) => cur[1] > best[1] ? cur : best, ['', 0])

  if (worstDwell < avg * 2) return null

  const stageCfg = STAGES.find((s) => s.id === worstStage)
  const stageName = stageCfg?.label || worstStage
  const otherAvg = entries.filter(([s]) => s !== worstStage).reduce((sum, [, v]) => sum + v, 0) /
    Math.max(1, entries.length - 1)

  return (
    <div
      className="flex items-center gap-3 px-4 py-3 rounded-lg mb-4"
      style={{ background: '#451a03', border: '1px solid #92400e' }}
    >
      <span className="text-xl flex-shrink-0">⚠️</span>
      <p className="font-mono text-[11px]" style={{ color: '#fbbf24' }}>
        <strong style={{ color: '#fde68a' }}>{stageName}</strong> stage is the bottleneck.
        {' '}Average dwell time <strong style={{ color: '#fde68a' }}>{formatDuration(worstDwell)}</strong>
        {' '}vs <strong style={{ color: '#fde68a' }}>{formatDuration(otherAvg)}</strong> for all other active stages.
      </p>
    </div>
  )
}

// ---- WaterfallDetail ---------------------------------------------------------

const WATERFALL_COLORS = {
  gate:     { bg: '#4338ca', color: '#c7d2fe' },
  building: { bg: '#0e7490', color: '#cffafe' },
  qa:       { bg: '#0f766e', color: '#ccfbf1' },
  retro:    { bg: '#4d7c0f', color: '#ecfccb' },
  persist:  { bg: '#6d28d9', color: '#ddd6fe' },
}

function WaterfallDetail({ task }) {
  if (!task) return null

  // Build approximate per-stage bars from total time
  // Since we don't have per-stage timestamps, we show a single bar for total
  const totalMs = task._totalMs || task._timeInStage || 0
  const stageCfg = STAGES.find((s) => s.id === task._stage)
  const barStyle = WATERFALL_COLORS[task._stage] || { bg: '#334155', color: '#94a3b8' }

  // Build bars for stages up to and including current
  const currentIdx = STAGE_IDX[task._stage] ?? 0
  const stagesShown = STAGES.slice(1, currentIdx + 1) // skip draft, show gate through current

  if (!stagesShown.length) {
    stagesShown.push(stageCfg || STAGES[0])
  }

  // Distribute time evenly across prior stages as a visual proxy
  const perStageMs = stagesShown.length > 1 ? totalMs / stagesShown.length : totalMs
  const maxMs = totalMs

  return (
    <div
      className="rounded-lg p-4 mb-4"
      style={{ background: '#1e293b', border: '1px solid #334155' }}
    >
      <h3 className="font-mono text-xs font-semibold text-slate-300 mb-3">
        Stage Timing: {task._title.slice(0, 60)} ({task._shortId})
      </h3>
      <div className="space-y-1.5">
        {stagesShown.map((s, i) => {
          const ms = i < stagesShown.length - 1 ? perStageMs : (totalMs - perStageMs * (stagesShown.length - 1))
          const pct = maxMs > 0 ? Math.max((ms / maxMs) * 100, 4) : 4
          const wfStyle = WATERFALL_COLORS[s.id] || { bg: '#334155', color: '#94a3b8' }
          return (
            <div key={s.id} className="flex items-center gap-2">
              <span className="font-mono text-[10px] text-slate-500 w-16 text-right flex-shrink-0">{s.label}</span>
              <div className="flex-1 h-4 rounded" style={{ background: '#0f172a' }}>
                <div
                  className="h-full rounded flex items-center pl-1.5"
                  style={{ width: `${pct}%`, background: wfStyle.bg }}
                >
                  <span className="font-mono text-[9px] font-semibold" style={{ color: wfStyle.color }}>
                    {formatDuration(ms)}
                  </span>
                </div>
              </div>
              <span className="font-mono text-[10px] text-slate-400 w-14 text-right flex-shrink-0">
                {formatDuration(ms)}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---- ConversionFunnel --------------------------------------------------------

const FUNNEL_STAGES = [
  { id: 'sent',     label: 'Sent',    gradient: 'linear-gradient(90deg, #1e3a5f, #7dd3fc)' },
  { id: 'building', label: 'Built',   gradient: 'linear-gradient(90deg, #164e63, #67e8f9)' },
  { id: 'qa',       label: 'QA Pass', gradient: 'linear-gradient(90deg, #134e4a, #5eead4)' },
  { id: 'done',     label: 'Done',    gradient: 'linear-gradient(90deg, #14532d, #4ade80)' },
]

function ConversionFunnel({ tasks }) {
  // Only tasks from the last 7 days
  const sevenDays = 7 * 86400000
  const now = Date.now()
  const recent = tasks.filter((t) => {
    const ref = t.completed_at || t.updated_at || t.created_at
    return ref && (now - Date.parse(ref)) < sevenDays
  })

  // Count per stage (cumulative: sent includes all tasks that reached sent or beyond)
  const stageOrder = ['sent', 'building', 'qa', 'done']
  const counts = stageOrder.reduce((acc, s) => {
    const idx = STAGE_IDX[s] ?? 0
    acc[s] = recent.filter((t) => (STAGE_IDX[t._stage] ?? 0) >= idx).length
    return acc
  }, {})

  const total = counts['sent'] || 1 // avoid divide by zero

  return (
    <div
      className="rounded-lg p-4"
      style={{ background: '#1e293b', border: '1px solid #334155' }}
    >
      <h3 className="font-mono text-xs font-semibold text-slate-300 mb-3">
        Conversion Funnel (7 days)
      </h3>
      <div className="space-y-1.5">
        {FUNNEL_STAGES.map((f) => {
          const count = counts[f.id] || 0
          const pct   = Math.round((count / total) * 100)
          const width = Math.max(pct, 4)
          return (
            <div key={f.id} className="flex items-center gap-2">
              <span className="font-mono text-[10px] text-slate-500 w-16 text-right flex-shrink-0">{f.label}</span>
              <div className="flex-1 h-5 rounded" style={{ background: '#0f172a' }}>
                <div
                  className="h-full rounded flex items-center justify-end pr-2"
                  style={{ width: `${width}%`, background: f.gradient }}
                >
                  <span className="font-mono text-[10px] font-semibold text-white">{count}</span>
                </div>
              </div>
              <span className="font-mono text-[10px] text-slate-500 w-8 text-right flex-shrink-0">{pct}%</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---- Main Inner Component ----------------------------------------------------

function PipelineTabInner() {
  const { stages, metrics, tasks, activeStageDwells, loading, error, refresh } = usePipeline()
  const [selectedTask, setSelectedTask] = useState(null)

  const handleSelectTask = (task) => {
    setSelectedTask((prev) => prev?.task_id === task.task_id ? null : task)
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="font-mono text-sm font-semibold text-slate-300">Pipeline</h2>
          <p className="font-mono text-[11px] text-slate-600">Task lifecycle · 8-stage SDLC view</p>
        </div>
        <div className="flex items-center gap-3">
          {error && (
            <span className="font-mono text-[11px] text-yellow-400">{error}</span>
          )}
          <button
            onClick={refresh}
            className="font-mono text-xs text-slate-500 hover:text-slate-300 transition-colors"
          >
            refresh
          </button>
        </div>
      </div>

      {loading && tasks.length === 0 ? (
        <div className="flex items-center gap-2 font-mono text-sm text-slate-400">
          <span className="inline-block w-2 h-2 rounded-full bg-slate-400 animate-pulse flex-shrink-0" />
          Loading pipeline data...
        </div>
      ) : (
        <>
          {/* Metrics Bar */}
          <MetricsBar metrics={metrics} />

          {/* Bottleneck Callout */}
          <BottleneckCallout activeStageDwells={activeStageDwells} />

          {/* Pipeline Grid */}
          <PipelineGrid
            stages={stages}
            selectedTaskId={selectedTask?.task_id}
            onSelectTask={handleSelectTask}
          />

          {/* Waterfall Detail (shown when task selected) */}
          {selectedTask && (
            <WaterfallDetail task={selectedTask} />
          )}

          {/* Conversion Funnel */}
          <ConversionFunnel tasks={tasks} />

          {/* Legend */}
          <div className="mt-4 flex flex-wrap gap-4">
            {[
              { label: 'Building', bg: '#164e63', accent: '#06b6d4' },
              { label: 'QA', bg: '#134e4a', accent: '#14b8a6' },
              { label: 'Retro', bg: '#365314', accent: '#84cc16' },
              { label: 'Done', bg: '#14532d', accent: '#22c55e' },
              { label: 'Blocked', bg: '#78350f', accent: '#f59e0b' },
              { label: 'Failed', bg: '#7f1d1d', accent: '#ef4444' },
              { label: 'Zombie (stale)', bg: '#1f2937', accent: '#374151' },
            ].map((l) => (
              <div key={l.label} className="flex items-center gap-1.5">
                <span
                  className="inline-block w-3 h-3 rounded"
                  style={{ background: l.bg, borderLeft: `3px solid ${l.accent}` }}
                />
                <span className="font-mono text-[10px] text-slate-500">{l.label}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// ---- Export ------------------------------------------------------------------

export function PipelineTab() {
  return (
    <PipelineErrorBoundary>
      <PipelineTabInner />
    </PipelineErrorBoundary>
  )
}
