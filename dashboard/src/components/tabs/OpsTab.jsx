import React from 'react'
import { useOps } from '../../hooks/useOps'
import { relativeTime } from '../../utils'

// ---- Error Boundary ---------------------------------------------------------

class OpsErrorBoundary extends React.Component {
  state = { hasError: false, error: null }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, info) {
    console.error('[OpsTab] render error:', error, info)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex-1 overflow-y-auto p-6">
          <div className="bg-forge-card border border-red-400/25 rounded-lg p-6 max-w-lg">
            <h3 className="font-mono text-sm font-semibold text-red-400 mb-2">Ops tab render error</h3>
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

// ---- Helpers -----------------------------------------------------------------

function normalizeMachine(m) {
  if (!m) return 'unknown'
  return m.toLowerCase().replace('.local', '')
}

function machineColor(m) {
  const n = normalizeMachine(m)
  if (n === 'kush') return 'text-blue-400'
  if (n === 'haze') return 'text-emerald-400'
  return 'text-slate-400'
}

function StatusDot({ status }) {
  const colors = {
    active: 'bg-green-400',
    closed: 'bg-slate-500',
    error: 'bg-red-400 animate-pulse',
  }
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full flex-shrink-0 ${colors[status] || 'bg-slate-600'}`}
      title={status}
    />
  )
}

// ---- Volume Bar Chart --------------------------------------------------------

function VolumeChart({ volume }) {
  if (!volume?.hourly?.length) {
    return <div className="font-mono text-xs text-slate-600">No volume data</div>
  }

  const data = volume.hourly.slice(-48) // last 48 hours of data
  const maxCalls = Math.max(...data.map(h => h.call_count), 1)

  return (
    <div className="flex items-end gap-0.5 h-24 w-full">
      {data.map((bucket, i) => {
        const height = Math.max((bucket.call_count / maxCalls) * 100, 2)
        const isNewDay = i > 0 && new Date(data[i-1].hour).getDate() !== new Date(bucket.hour).getDate()
        return (
          <div
            key={bucket.hour}
            className="flex-1 flex flex-col justify-end group relative"
            title={`${bucket.hour}: ${bucket.call_count} calls, ${bucket.active_sessions} sessions`}
          >
            {isNewDay && (
              <div className="absolute left-0 top-0 bottom-0 w-px bg-slate-700" />
            )}
            <div
              className="bg-blue-500/60 hover:bg-blue-400/80 transition-colors rounded-sm min-h-[2px]"
              style={{ height: `${height}%` }}
            />
          </div>
        )
      })}
    </div>
  )
}

// ---- Summary Cards -----------------------------------------------------------

function SummaryCards({ sessions, toolStats }) {
  const sessionList = sessions?.sessions || []
  const totalSessions = sessions?.count ?? sessionList.length
  const activeSessions = sessionList.filter(s => s.status === 'active').length
  const closedSessions = sessionList.filter(s => s.status === 'closed').length

  const tools = toolStats?.tools || []
  const totalCalls = tools.reduce((sum, t) => sum + (t.call_count || 0), 0)
  const avgSuccess = tools.length
    ? (tools.reduce((sum, t) => sum + parseFloat(t.success_rate || 0), 0) / tools.length).toFixed(1)
    : null

  // Machine split from sessions
  const machineCounts = {}
  sessionList.forEach(s => {
    const m = normalizeMachine(s.machine)
    machineCounts[m] = (machineCounts[m] || 0) + 1
  })

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
      {/* Sessions card */}
      <div className="bg-forge-card border border-forge-border rounded-lg p-4">
        <div className="font-mono text-[11px] text-slate-500 uppercase tracking-wider mb-1">Sessions (96h)</div>
        <div className="font-mono text-2xl font-bold text-slate-100 mb-2">{totalSessions}</div>
        <div className="flex gap-3">
          <span className="font-mono text-[11px] text-green-400">{activeSessions} active</span>
          <span className="font-mono text-[11px] text-slate-500">{closedSessions} closed</span>
        </div>
      </div>

      {/* Tool calls card */}
      <div className="bg-forge-card border border-forge-border rounded-lg p-4">
        <div className="font-mono text-[11px] text-slate-500 uppercase tracking-wider mb-1">Tool Calls (96h)</div>
        <div className="font-mono text-2xl font-bold text-slate-100 mb-2">{totalCalls.toLocaleString()}</div>
        {avgSuccess && (
          <div className="font-mono text-[11px] text-emerald-400">{avgSuccess}% avg success rate</div>
        )}
      </div>

      {/* Machine split card */}
      <div className="bg-forge-card border border-forge-border rounded-lg p-4">
        <div className="font-mono text-[11px] text-slate-500 uppercase tracking-wider mb-2">Machine Split</div>
        <div className="space-y-1">
          {Object.entries(machineCounts).sort((a,b) => b[1]-a[1]).map(([machine, count]) => (
            <div key={machine} className="flex items-center gap-2">
              <span className={`font-mono text-[11px] font-semibold ${machineColor(machine)} w-12`}>{machine}</span>
              <div className="flex-1 h-1.5 bg-slate-800 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full ${machine === 'kush' ? 'bg-blue-500' : 'bg-emerald-500'}`}
                  style={{ width: `${(count / Math.max(...Object.values(machineCounts))) * 100}%` }}
                />
              </div>
              <span className="font-mono text-[10px] text-slate-500">{count}</span>
            </div>
          ))}
          {Object.keys(machineCounts).length === 0 && (
            <div className="font-mono text-[11px] text-slate-600">No data</div>
          )}
        </div>
      </div>
    </div>
  )
}

// ---- Top Tools Table ---------------------------------------------------------

function TopToolsTable({ toolStats }) {
  const tools = (toolStats?.tools || []).slice(0, 15)

  if (!tools.length) {
    return <div className="font-mono text-xs text-slate-600">No tool data</div>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className="border-b border-forge-border">
            <th className="font-mono text-[10px] text-slate-500 text-left py-1.5 pr-4">Tool</th>
            <th className="font-mono text-[10px] text-slate-500 text-right py-1.5 pr-4">Calls</th>
            <th className="font-mono text-[10px] text-slate-500 text-right py-1.5 pr-4">Success %</th>
            <th className="font-mono text-[10px] text-slate-500 text-right py-1.5">Avg ms</th>
          </tr>
        </thead>
        <tbody>
          {tools.map((tool) => {
            const rate = parseFloat(tool.success_rate || 0)
            const rateColor = rate >= 95 ? 'text-emerald-400' : rate >= 80 ? 'text-yellow-400' : 'text-red-400'
            return (
              <tr key={tool.tool_name} className="border-b border-forge-border/40 hover:bg-slate-800/30">
                <td className="font-mono text-[11px] text-slate-300 py-1.5 pr-4">{tool.tool_name}</td>
                <td className="font-mono text-[11px] text-slate-400 text-right py-1.5 pr-4">{tool.call_count.toLocaleString()}</td>
                <td className={`font-mono text-[11px] text-right py-1.5 pr-4 ${rateColor}`}>{rate.toFixed(1)}%</td>
                <td className="font-mono text-[11px] text-slate-500 text-right py-1.5">
                  {tool.avg_duration_ms != null ? `${parseFloat(tool.avg_duration_ms).toFixed(0)}` : '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ---- Sessions Timeline -------------------------------------------------------

function SessionsTimeline({ sessions }) {
  const list = (sessions?.sessions || []).slice(0, 20)

  if (!list.length) {
    return <div className="font-mono text-xs text-slate-600">No session data</div>
  }

  return (
    <div className="space-y-1">
      {list.map((s) => {
        const machine = normalizeMachine(s.machine)
        const isActive = s.status === 'active'
        const duration = s.ended_at && s.started_at
          ? Math.round((new Date(s.ended_at) - new Date(s.started_at)) / 1000) + 's'
          : isActive ? 'ongoing' : '—'

        return (
          <div key={s.id} className="flex items-center gap-2 py-1.5 border-b border-forge-border/30 hover:bg-slate-800/20">
            <StatusDot status={s.status} />
            <span className={`font-mono text-[11px] font-medium w-20 flex-shrink-0 ${machineColor(machine)}`}>{machine}</span>
            <span className="font-mono text-[11px] text-slate-300 flex-1 truncate">{s.agent_type}</span>
            <span className="font-mono text-[10px] text-slate-600 truncate max-w-[180px]">{s.source}</span>
            <span className="font-mono text-[10px] text-slate-500 w-16 text-right flex-shrink-0">{duration}</span>
            <span className="font-mono text-[10px] text-slate-600 w-24 text-right flex-shrink-0">
              {s.started_at ? relativeTime(s.started_at) : '—'}
            </span>
            <span className="font-mono text-[10px] text-slate-500 w-8 text-right flex-shrink-0">{s.total_tool_calls}</span>
          </div>
        )
      })}
    </div>
  )
}

// ---- Errors Table ------------------------------------------------------------

function ErrorsTable({ errors }) {
  const list = (errors?.errors || []).slice(0, 10)

  if (!list.length) {
    return <div className="font-mono text-xs text-emerald-400">No errors recorded</div>
  }

  return (
    <div className="space-y-1">
      {list.map((e, i) => (
        <div key={i} className="flex items-center gap-2 py-1.5 bg-red-400/5 border border-red-400/15 rounded px-2 hover:bg-red-400/10">
          <span className="font-mono text-[11px] text-red-300 font-medium w-32 flex-shrink-0">{e.tool_name}</span>
          <span className="font-mono text-[11px] text-slate-400 flex-1 truncate" title={e.error_message}>
            {e.error_message || 'no error message'}
          </span>
          <span className="font-mono text-[10px] text-red-400 w-16 text-right flex-shrink-0">{e.occurrences}x</span>
          <span className="font-mono text-[10px] text-slate-600 w-24 text-right flex-shrink-0">
            {e.last_seen ? relativeTime(e.last_seen) : '—'}
          </span>
        </div>
      ))}
    </div>
  )
}

// ---- Main Inner Component ----------------------------------------------------

function OpsTabInner() {
  const { sessions, toolStats, errors, volume, timeline, loading, error, refresh } = useOps()

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="font-mono text-sm font-semibold text-slate-300">Ops</h2>
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

      {loading && !sessions && !toolStats ? (
        <div className="flex items-center gap-2 font-mono text-sm text-slate-400">
          <span className="inline-block w-2 h-2 rounded-full bg-slate-400 animate-pulse flex-shrink-0" />
          Loading ops data...
        </div>
      ) : (
        <div className="space-y-6">
          {/* Row 1: Summary Cards */}
          <SummaryCards sessions={sessions} toolStats={toolStats} />

          {/* Row 2: Volume Chart */}
          <div className="bg-forge-card border border-forge-border rounded-lg p-4">
            <h3 className="font-mono text-sm font-semibold text-slate-300 mb-3">
              Tool Call Volume (96h)
            </h3>
            <VolumeChart volume={volume} />
            <div className="flex justify-between mt-1">
              <span className="font-mono text-[10px] text-slate-700">older</span>
              <span className="font-mono text-[10px] text-slate-700">now</span>
            </div>
          </div>

          {/* Row 3: Top Tools Table */}
          <div className="bg-forge-card border border-forge-border rounded-lg p-4">
            <h3 className="font-mono text-sm font-semibold text-slate-300 mb-3">
              Top Tools
            </h3>
            <TopToolsTable toolStats={toolStats} />
          </div>

          {/* Row 4: Sessions Timeline */}
          <div className="bg-forge-card border border-forge-border rounded-lg p-4">
            <h3 className="font-mono text-sm font-semibold text-slate-300 mb-3">
              Recent Sessions
            </h3>
            <div className="flex items-center gap-4 mb-2">
              <span className="font-mono text-[10px] text-slate-600 w-20">machine</span>
              <span className="font-mono text-[10px] text-slate-600 flex-1">type</span>
              <span className="font-mono text-[10px] text-slate-600 max-w-[180px]">source</span>
              <span className="font-mono text-[10px] text-slate-600 w-16 text-right">duration</span>
              <span className="font-mono text-[10px] text-slate-600 w-24 text-right">started</span>
              <span className="font-mono text-[10px] text-slate-600 w-8 text-right">calls</span>
            </div>
            <SessionsTimeline sessions={sessions} />
          </div>

          {/* Row 5: Errors */}
          <div className="bg-forge-card border border-forge-border rounded-lg p-4">
            <h3 className="font-mono text-sm font-semibold text-slate-300 mb-3">
              Recent Errors
            </h3>
            <ErrorsTable errors={errors} />
          </div>
        </div>
      )}
    </div>
  )
}

// ---- Export ------------------------------------------------------------------

export function OpsTab() {
  return (
    <OpsErrorBoundary>
      <OpsTabInner />
    </OpsErrorBoundary>
  )
}
