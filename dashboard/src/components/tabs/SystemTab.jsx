import { useSystem } from '../../hooks/useSystem'
import { relativeTime } from '../../utils'

function StatusDot({ status }) {
  const colors = {
    up: 'bg-green-400',
    down: 'bg-red-400',
    degraded: 'bg-yellow-400',
    unreachable: 'bg-yellow-400 animate-pulse',
  }
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full flex-shrink-0 ${colors[status] || 'bg-slate-600'}`}
      title={status}
    />
  )
}

function ServiceRow({ svc }) {
  const isUp = svc.status === 'up'
  return (
    <div className="flex items-center gap-2 py-1">
      <StatusDot status={svc.status} />
      <span className="font-mono text-[11px] text-slate-400 flex-1">{svc.name}</span>
      <span className="font-mono text-[10px] text-slate-600">:{svc.port}</span>
      {svc.latency_ms != null && isUp && (
        <span className="font-mono text-[10px] text-slate-600">{svc.latency_ms}ms</span>
      )}
      {!isUp && svc.error && (
        <span className="font-mono text-[10px] text-red-400 truncate max-w-[120px]" title={svc.error}>
          {svc.error}
        </span>
      )}
    </div>
  )
}

function MachineCard({ machine }) {
  return (
    <div className="bg-forge-card border border-forge-border rounded-lg p-4">
      <div className="flex items-center gap-2 mb-3">
        <StatusDot status={machine.status} />
        <span className="font-mono text-sm font-semibold text-slate-100">{machine.name}</span>
        <span className="font-mono text-[10px] text-slate-600 ml-auto">{machine.ip}</span>
      </div>
      <div className="font-mono text-[11px] text-slate-500 mb-2">{machine.role}</div>
      <div className="divide-y divide-forge-border/40">
        {machine.services.map((svc) => (
          <ServiceRow key={svc.name} svc={svc} />
        ))}
      </div>
      {machine.checked_at && (
        <div className="mt-2 font-mono text-[10px] text-slate-700">
          checked {relativeTime(machine.checked_at)}
        </div>
      )}
    </div>
  )
}

function BrainPanel({ health, error }) {
  if (error) {
    return (
      <div className="bg-forge-card border border-forge-border rounded-lg p-4">
        <h3 className="font-mono text-sm font-semibold text-slate-300 mb-3">Brain Health</h3>
        <div className="font-mono text-xs text-red-400">{error}</div>
      </div>
    )
  }

  if (!health) {
    return (
      <div className="bg-forge-card border border-forge-border rounded-lg p-4">
        <h3 className="font-mono text-sm font-semibold text-slate-300 mb-3">Brain Health</h3>
        <div className="font-mono text-xs text-slate-600">Loading...</div>
      </div>
    )
  }

  const isOk = health.status === 'ok' || health._proxy_ok
  const cbState = health.circuit_breaker || 'unknown'
  const cbColor = cbState === 'closed' ? 'text-emerald-400'
    : cbState === 'open' ? 'text-red-400'
    : 'text-yellow-400'

  return (
    <div className="bg-forge-card border border-forge-border rounded-lg p-4">
      <div className="flex items-center gap-2 mb-3">
        <span className={`inline-block w-2 h-2 rounded-full ${isOk ? 'bg-green-400' : 'bg-red-400'}`} />
        <h3 className="font-mono text-sm font-semibold text-slate-300">Brain Health</h3>
      </div>

      <div className="space-y-1.5">
        <div className="flex justify-between">
          <span className="font-mono text-[11px] text-slate-600">status</span>
          <span className={`font-mono text-[11px] ${isOk ? 'text-emerald-400' : 'text-red-400'}`}>
            {health.status || 'unknown'}
          </span>
        </div>

        {health.version && (
          <div className="flex justify-between">
            <span className="font-mono text-[11px] text-slate-600">version</span>
            <span className="font-mono text-[11px] text-slate-400">{health.version}</span>
          </div>
        )}

        <div className="flex justify-between">
          <span className="font-mono text-[11px] text-slate-600">circuit breaker</span>
          <span className={`font-mono text-[11px] ${cbColor}`}>{cbState}</span>
        </div>

        {health.persist_queue_depth != null && (
          <div className="flex justify-between">
            <span className="font-mono text-[11px] text-slate-600">persist queue</span>
            <span className="font-mono text-[11px] text-slate-400">{health.persist_queue_depth}</span>
          </div>
        )}

        {health.dead_letter_depth != null && health.dead_letter_depth > 0 && (
          <div className="flex justify-between">
            <span className="font-mono text-[11px] text-slate-600">dead letter</span>
            <span className="font-mono text-[11px] text-red-400">{health.dead_letter_depth}</span>
          </div>
        )}

        {health._proxy_source && (
          <div className="mt-2 pt-2 border-t border-forge-border">
            <span className="font-mono text-[10px] text-slate-700 break-all">{health._proxy_source}</span>
          </div>
        )}

        {health.error && (
          <div className="mt-1 font-mono text-[11px] text-red-400">{health.error}</div>
        )}
      </div>
    </div>
  )
}

export function SystemTab() {
  const { brainHealth, infraStatus, loading, brainError, infraError, refresh } = useSystem()

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="font-mono text-sm font-semibold text-slate-300">System</h2>
        <button
          onClick={refresh}
          className="font-mono text-xs text-slate-500 hover:text-slate-300 transition-colors"
        >
          refresh
        </button>
      </div>

      {loading && !brainHealth && !infraStatus ? (
        <div className="flex items-center gap-2 font-mono text-sm text-slate-400">
          <span className="inline-block w-2 h-2 rounded-full bg-slate-400 animate-pulse flex-shrink-0" />
          Checking system status...
        </div>
      ) : (
        <div className="flex flex-col lg:flex-row gap-6">
          {/* Left: Brain Health */}
          <div className="lg:w-80 flex-shrink-0">
            <div className="font-mono text-[11px] text-slate-500 uppercase tracking-wider mb-3">
              Brain (Aianna)
            </div>
            <BrainPanel health={brainHealth} error={brainError} />
          </div>

          {/* Right: Infrastructure */}
          <div className="flex-1">
            <div className="font-mono text-[11px] text-slate-500 uppercase tracking-wider mb-3">
              Infrastructure
            </div>
            {infraError && (
              <div className="mb-3 font-mono text-xs text-red-400 bg-red-400/10 border border-red-400/25 rounded px-3 py-2">
                {infraError}
              </div>
            )}
            <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
              {infraStatus?.machines?.map((machine) => (
                <MachineCard key={machine.name} machine={machine} />
              ))}
              {!infraStatus && !infraError && (
                <div className="font-mono text-sm text-slate-600">Loading infrastructure...</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
