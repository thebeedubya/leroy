import { useState } from 'react'
import { useAgents } from '../../hooks/useAgents'
import { relativeTime } from '../../utils'

function statusDot(status) {
  switch (status) {
    case 'running':
      return <span className="w-2.5 h-2.5 rounded-full bg-green-400 inline-block" title="Running" />
    case 'idle':
      return <span className="w-2.5 h-2.5 rounded-full bg-slate-500 inline-block" title="Idle" />
    case 'error':
      return <span className="w-2.5 h-2.5 rounded-full bg-red-400 inline-block" title="Error" />
    case 'unreachable':
      return (
        <span
          className="w-2.5 h-2.5 rounded-full bg-yellow-400 inline-block animate-pulse"
          title="Unreachable"
        />
      )
    default:
      return <span className="w-2.5 h-2.5 rounded-full bg-slate-600 inline-block" title={status} />
  }
}

function typeBadge(type) {
  const colors = {
    scheduled: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
    daemon: 'bg-blue-500/20 text-blue-300 border-blue-500/30',
    interactive: 'bg-green-500/20 text-green-300 border-green-500/30',
    'on-demand': 'bg-slate-500/20 text-slate-300 border-slate-500/30',
  }
  const cls = colors[type] || 'bg-slate-500/20 text-slate-300 border-slate-500/30'
  return (
    <span className={`px-1.5 py-0.5 rounded border font-mono text-[10px] uppercase ${cls}`}>
      {type}
    </span>
  )
}

function AgentCard({ agent }) {
  const [expanded, setExpanded] = useState(false)

  const lhb = agent.last_heartbeat
  const heartbeatDisplay = lhb ? relativeTime(lhb) : 'never'
  const lastActivity = agent.last_activity ? relativeTime(agent.last_activity) : 'never'

  return (
    <div
      className="bg-forge-card border border-forge-border rounded-lg p-4 cursor-pointer hover:border-forge-muted transition-colors"
      onClick={() => setExpanded((v) => !v)}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          {statusDot(agent.status)}
          <span className="font-mono text-sm font-semibold text-slate-100 truncate">
            {agent.display_name || agent.name}
          </span>
          {typeBadge(agent.type)}
        </div>
        <span className="font-mono text-[10px] text-slate-600 flex-shrink-0">
          {expanded ? '▲' : '▼'}
        </span>
      </div>

      {agent.current_task && (
        <div className="mt-2 font-mono text-xs text-blue-400 truncate">
          ↳ {agent.current_task}
        </div>
      )}

      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1">
        <div className="font-mono text-[11px] text-slate-600">heartbeat</div>
        <div className="font-mono text-[11px] text-slate-400">{heartbeatDisplay}</div>
        <div className="font-mono text-[11px] text-slate-600">last activity</div>
        <div className="font-mono text-[11px] text-slate-400">{lastActivity}</div>
        <div className="font-mono text-[11px] text-slate-600">launcher</div>
        <div className="font-mono text-[11px] text-slate-400 truncate">{agent.launcher || '—'}</div>
      </div>

      {expanded && (
        <div className="mt-4 border-t border-forge-border pt-3">
          {agent.metadata?.description && (
            <p className="font-mono text-[11px] text-slate-400 mb-2">
              {agent.metadata.description}
            </p>
          )}
          {agent.metadata?.schedule && (
            <div className="font-mono text-[11px] text-slate-500">
              schedule: {agent.metadata.schedule}
            </div>
          )}
          <div className="font-mono text-[11px] text-slate-600 mt-1">
            name: {agent.name}
          </div>
          {agent.metadata?.launch_method && (
            <div className="font-mono text-[11px] text-slate-600">
              launch_method: {agent.metadata.launch_method}
            </div>
          )}
          {agent.seeded_at && (
            <div className="font-mono text-[11px] text-slate-700 mt-1">
              seeded: {relativeTime(agent.seeded_at)}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function AgentsTab() {
  const { agents, loading, error, refresh } = useAgents()

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="font-mono text-sm font-semibold text-slate-300">Agent Roster</h2>
        <button
          onClick={refresh}
          className="font-mono text-xs text-slate-500 hover:text-slate-300 transition-colors"
        >
          refresh
        </button>
      </div>

      {error && (
        <div className="mb-4 font-mono text-xs text-red-400 bg-red-400/10 border border-red-400/25 rounded px-3 py-2">
          {error}
        </div>
      )}

      {loading && agents.length === 0 ? (
        <div className="font-mono text-sm text-slate-600">Loading agents...</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {agents.map((agent) => (
            <AgentCard key={agent.name} agent={agent} />
          ))}
          {agents.length === 0 && (
            <div className="col-span-3 font-mono text-sm text-slate-600">
              No agents registered.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
