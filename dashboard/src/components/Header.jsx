import { useHealth } from '../hooks/useHealth'
import { formatUptime } from '../utils'

export function Header({ lastUpdated, taskCount, refreshCount, sseConnected, connectionType, qaReviewCount, onQaReviewClick }) {
  const { health, error: healthError, checking } = useHealth()

  const isHealthy = !healthError && health?.status === 'ok'
  const isDegraded = !healthError && health && health.status !== 'ok'
  const isOffline = !!healthError || (!health && !checking)

  const statusLabel = isOffline ? 'OFFLINE' : isDegraded ? 'DEGRADED' : isHealthy ? 'HEALTHY' : 'CONNECTING'
  const dotClass = isOffline
    ? 'bg-red-500'
    : isDegraded
    ? 'bg-yellow-500 animate-pulse'
    : isHealthy
    ? 'bg-emerald-500 animate-pulse'
    : 'bg-slate-500 animate-pulse'
  const textClass = isOffline
    ? 'text-red-400'
    : isDegraded
    ? 'text-yellow-400'
    : isHealthy
    ? 'text-emerald-400'
    : 'text-slate-400'

  const uptime = health?.uptime_seconds != null ? formatUptime(health.uptime_seconds) : null

  return (
    <header className="sticky top-0 z-20 border-b border-forge-border bg-forge-bg/90 backdrop-blur-sm">
      <div className="px-6 py-3 flex items-center justify-between">
        {/* Left: Brand */}
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="text-lg font-mono font-bold tracking-[0.25em] text-slate-100">LEROY</span>
            <span className="text-slate-600 font-mono text-xs tracking-widest">FORGE</span>
          </div>
          {taskCount != null && (
            <span className="font-mono text-xs text-slate-600 border border-forge-border rounded px-2 py-0.5">
              {taskCount} task{taskCount !== 1 ? 's' : ''}
            </span>
          )}
        </div>

        {/* Right: status indicators */}
        <div className="flex items-center gap-5 font-mono text-xs">
          {/* QA Review badge */}
          {qaReviewCount > 0 && (
            <button
              onClick={onQaReviewClick}
              className="flex items-center gap-2 px-3 py-1 rounded border bg-cyan-400/15 border-cyan-400/40 text-cyan-400 hover:bg-cyan-400/25 hover:border-cyan-400/70 transition-all animate-pulse"
            >
              <div className="w-1.5 h-1.5 rounded-full bg-cyan-400" />
              <span className="font-mono text-xs font-bold tracking-wider">
                {qaReviewCount} AWAITING REVIEW
              </span>
            </button>
          )}
          {/* Connection type indicator */}
          {connectionType === 'sse' ? (
            <div className="flex items-center gap-1.5">
              <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              <span className="text-emerald-400 font-mono text-xs">LIVE</span>
            </div>
          ) : connectionType === 'polling' ? (
            <div className="flex items-center gap-1.5">
              <div className="w-1.5 h-1.5 rounded-full bg-amber-400" />
              <span className="text-amber-400 font-mono text-xs">POLLING</span>
            </div>
          ) : (
            <div className="flex items-center gap-1.5">
              <div className="w-1.5 h-1.5 rounded-full bg-slate-500 animate-pulse" />
              <span className="text-slate-500 font-mono text-xs">CONNECTING</span>
            </div>
          )}

          {/* Last polled */}
          {lastUpdated && (
            <span className="text-slate-600">
              {lastUpdated.toLocaleTimeString('en-US', {
                hour12: false,
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
              })}
            </span>
          )}

          {/* Uptime */}
          {uptime && (
            <span className="text-slate-600">
              up <span className="text-slate-400">{uptime}</span>
            </span>
          )}

          {/* Health indicator */}
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full flex-shrink-0 ${dotClass}`} />
            <span className={textClass}>{statusLabel}</span>
          </div>
        </div>
      </div>
    </header>
  )
}
