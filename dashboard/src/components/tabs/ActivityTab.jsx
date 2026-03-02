import { useState, useRef, useEffect } from 'react'
import { useActivity } from '../../hooks/useActivity'

const AGENT_COLORS = {
  leroy: 'text-blue-400 bg-blue-400/10 border-blue-400/30',
  pm: 'text-purple-400 bg-purple-400/10 border-purple-400/30',
  'content-agent': 'text-emerald-400 bg-emerald-400/10 border-emerald-400/30',
  ops: 'text-orange-400 bg-orange-400/10 border-orange-400/30',
}

const EVENT_ICONS = {
  task_start: '▶',
  task_complete: '✓',
  qa_review: '?',
  error: '✗',
  decision_requested: '!',
  status_update: '·',
  query: '◎',
  heartbeat: '♥',
  brain_persist: '▲',
  pr_opened: '⊕',
}

const SEVERITY_COLORS = {
  info: 'text-slate-400',
  warn: 'text-yellow-400',
  error: 'text-red-400',
}

function AgentBadge({ agent }) {
  const cls = AGENT_COLORS[agent.toLowerCase()] || 'text-slate-400 bg-slate-400/10 border-slate-400/30'
  return (
    <span className={`px-1.5 py-0.5 rounded border font-mono text-[10px] uppercase flex-shrink-0 ${cls}`}>
      {agent}
    </span>
  )
}

function EventRow({ event }) {
  const d = new Date(event.timestamp)
  const timeStr = d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  const icon = EVENT_ICONS[event.type] || '·'
  const severityColor = SEVERITY_COLORS[event.severity] || 'text-slate-400'

  return (
    <div className="flex items-start gap-2 py-1 border-b border-forge-border/30 hover:bg-forge-surface/50 px-2 -mx-2 rounded transition-colors">
      <span className="font-mono text-[11px] text-slate-600 flex-shrink-0 w-20 pt-0.5">{timeStr}</span>
      <AgentBadge agent={event.agent} />
      <span className={`font-mono text-[11px] flex-shrink-0 pt-0.5 ${severityColor}`}>{icon}</span>
      <span className={`font-mono text-[12px] leading-relaxed ${severityColor}`}>
        {event.summary}
      </span>
    </div>
  )
}

const ALL_AGENTS = ['All', 'PM', 'Leroy', 'Content', 'Ops']

export function ActivityTab() {
  const { events, loading, error, connected } = useActivity()
  const [agentFilter, setAgentFilter] = useState('All')
  const [paused, setPaused] = useState(false)
  const bottomRef = useRef(null)

  const filtered = agentFilter === 'All'
    ? events
    : events.filter((e) => e.agent.toLowerCase().includes(agentFilter.toLowerCase()))

  // Auto-scroll to top when new events arrive (events are newest-first)
  // No scroll needed since newest is at top

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-6">
      <div className="flex items-center justify-between mb-3 flex-shrink-0">
        <div className="flex items-center gap-3">
          <h2 className="font-mono text-sm font-semibold text-slate-300">Activity Feed</h2>
          <div className="flex items-center gap-1">
            <div className={[
              'w-1.5 h-1.5 rounded-full',
              connected ? 'bg-green-400' : 'bg-slate-600 animate-pulse',
            ].join(' ')} />
            <span className="font-mono text-[10px] text-slate-600">
              {connected ? 'live' : 'connecting'}
            </span>
          </div>
        </div>
        <button
          onClick={() => setPaused((v) => !v)}
          className="font-mono text-xs text-slate-500 hover:text-slate-300 transition-colors"
        >
          {paused ? 'resume' : 'pause'}
        </button>
      </div>

      {/* Filter chips */}
      <div className="flex items-center gap-2 mb-4 flex-shrink-0 flex-wrap">
        {ALL_AGENTS.map((a) => (
          <button
            key={a}
            onClick={() => setAgentFilter(a)}
            className={[
              'font-mono text-[11px] px-2.5 py-1 rounded border transition-colors',
              agentFilter === a
                ? 'bg-blue-500/20 text-blue-300 border-blue-400/50'
                : 'text-slate-500 border-forge-border hover:text-slate-300 hover:border-forge-muted',
            ].join(' ')}
          >
            {a}
          </button>
        ))}
        <span className="font-mono text-[10px] text-slate-700 ml-auto">
          {filtered.length} events
        </span>
      </div>

      {error && (
        <div className="mb-3 font-mono text-xs text-red-400 bg-red-400/10 border border-red-400/25 rounded px-3 py-2 flex-shrink-0">
          {error}
        </div>
      )}

      {loading && events.length === 0 ? (
        <div className="font-mono text-sm text-slate-600">Loading activity...</div>
      ) : (
        <div className="flex-1 overflow-y-auto font-mono">
          {filtered.length === 0 ? (
            <div className="font-mono text-sm text-slate-600">No activity yet.</div>
          ) : (
            filtered.map((evt) => <EventRow key={evt.id} event={evt} />)
          )}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  )
}
