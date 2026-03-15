import { usePlans } from '../../hooks/usePlans'
import { relativeTime } from '../../utils'

function StatCard({ label, value, sub, color = 'text-slate-200' }) {
  return (
    <div className="bg-forge-card border border-forge-border rounded-lg p-4">
      <div className="font-mono text-[10px] text-slate-600 uppercase tracking-wider">{label}</div>
      <div className={`font-mono text-2xl font-bold mt-1 ${color}`}>{value}</div>
      {sub && <div className="font-mono text-[11px] text-slate-500 mt-1">{sub}</div>}
    </div>
  )
}

function ReportSection({ report }) {
  if (!report) return null

  const v2 = report.v2 || {}
  const v1 = report.v1_import || {}
  const combined = report.combined || {}

  const v2PassRate = v2.total > 0 ? ((v2.completed || 0) / v2.total * 100).toFixed(0) : '—'
  const v1PassRate = v1.total > 0 ? ((v1.completed || 0) / v1.total * 100).toFixed(0) : '—'

  return (
    <div>
      <h3 className="font-mono text-xs text-slate-500 uppercase tracking-wider mb-3">Plan Report</h3>
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
        <StatCard label="v2 Plans" value={v2.total || 0} sub={`${v2.completed || 0} completed`} color="text-blue-400" />
        <StatCard label="v2 Pass Rate" value={`${v2PassRate}%`} color={Number(v2PassRate) >= 70 ? 'text-emerald-400' : 'text-yellow-400'} />
        <StatCard label="v2 Cost" value={`$${(v2.total_cost_usd || 0).toFixed(2)}`} sub={`avg $${(v2.avg_cost_usd || 0).toFixed(4)}`} color="text-cyan-400" />
        <StatCard label="v2 Respecs" value={v2.respec_count || 0} color={v2.respec_count > 0 ? 'text-yellow-400' : 'text-slate-400'} />
        <StatCard label="v1 Baseline" value={v1.total || 0} sub={`${v1PassRate}% pass rate`} color="text-slate-400" />
        <StatCard label="Total Plans" value={combined.total || 0} sub={`${combined.failed || 0} failed, ${combined.timeout_count || 0} timeouts`} />
      </div>
    </div>
  )
}

function SubsystemHealth({ health }) {
  if (!health || Object.keys(health).length === 0) {
    return (
      <div>
        <h3 className="font-mono text-xs text-slate-500 uppercase tracking-wider mb-3">Subsystem Health</h3>
        <p className="font-mono text-sm text-slate-600">No v2 plans recorded yet.</p>
      </div>
    )
  }

  return (
    <div>
      <h3 className="font-mono text-xs text-slate-500 uppercase tracking-wider mb-3">Subsystem Health</h3>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
        {Object.entries(health).sort(([a], [b]) => a.localeCompare(b)).map(([sub, stats]) => {
          const passRate = (stats.pass_rate * 100).toFixed(0)
          const color = stats.pass_rate >= 0.8 ? 'text-emerald-400' : stats.pass_rate >= 0.5 ? 'text-yellow-400' : 'text-red-400'
          return (
            <div key={sub} className="bg-forge-card border border-forge-border rounded-lg p-3">
              <div className="font-mono text-xs text-slate-300 font-medium">{sub}</div>
              <div className="flex items-baseline gap-2 mt-1">
                <span className={`font-mono text-lg font-bold ${color}`}>{passRate}%</span>
                <span className="font-mono text-[10px] text-slate-600">pass rate</span>
              </div>
              <div className="font-mono text-[10px] text-slate-600 mt-1">
                {stats.total} plans · {stats.completed} ok · {stats.failed} fail · {stats.respec_count} respec
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function CostBreakdown({ cost }) {
  if (!cost) return null

  const byDay = cost.by_day || {}
  const bySub = cost.by_subsystem || {}
  const days = Object.entries(byDay).sort(([a], [b]) => b.localeCompare(a)).slice(0, 14)

  return (
    <div>
      <h3 className="font-mono text-xs text-slate-500 uppercase tracking-wider mb-3">
        Cost Breakdown
        <span className="text-cyan-400 ml-2">${(cost.total_cost_usd || 0).toFixed(2)} total</span>
      </h3>

      {Object.keys(bySub).length > 0 && (
        <div className="mb-4">
          <div className="font-mono text-[10px] text-slate-600 mb-2">By Subsystem</div>
          <div className="space-y-1">
            {Object.entries(bySub).sort(([, a], [, b]) => b.cost - a.cost).map(([sub, stats]) => {
              const pct = cost.total_cost_usd > 0 ? (stats.cost / cost.total_cost_usd * 100) : 0
              return (
                <div key={sub} className="flex items-center gap-2">
                  <span className="font-mono text-xs text-slate-300 w-24 flex-shrink-0">{sub}</span>
                  <div className="flex-1 h-4 bg-forge-bg rounded overflow-hidden">
                    <div className="h-full bg-cyan-500/30 rounded" style={{ width: `${Math.max(pct, 2)}%` }} />
                  </div>
                  <span className="font-mono text-[10px] text-slate-500 w-20 text-right">
                    ${stats.cost.toFixed(4)} ({stats.count})
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {days.length > 0 && (
        <div>
          <div className="font-mono text-[10px] text-slate-600 mb-2">By Day (last 14)</div>
          <div className="space-y-1">
            {days.map(([day, stats]) => {
              const maxCost = Math.max(...days.map(([, s]) => s.cost), 0.001)
              const pct = (stats.cost / maxCost) * 100
              return (
                <div key={day} className="flex items-center gap-2">
                  <span className="font-mono text-[10px] text-slate-500 w-20 flex-shrink-0">{day}</span>
                  <div className="flex-1 h-3 bg-forge-bg rounded overflow-hidden">
                    <div className="h-full bg-blue-500/30 rounded" style={{ width: `${Math.max(pct, 2)}%` }} />
                  </div>
                  <span className="font-mono text-[10px] text-slate-600 w-24 text-right">
                    ${stats.cost.toFixed(4)} ({stats.count} plans)
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function RecentPlans({ plans }) {
  if (!plans || plans.length === 0) return null

  const STATUS_COLORS = {
    draft: 'text-slate-400',
    sent: 'text-blue-400',
    completed: 'text-emerald-400',
    completed_unverified: 'text-yellow-400',
    failed: 'text-red-400',
  }

  return (
    <div>
      <h3 className="font-mono text-xs text-slate-500 uppercase tracking-wider mb-3">
        Recent Plans <span className="text-slate-600">({plans.length})</span>
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-forge-border">
              <th className="font-mono text-[10px] text-slate-600 text-left py-2 px-2">Date</th>
              <th className="font-mono text-[10px] text-slate-600 text-left py-2 px-2">Subject</th>
              <th className="font-mono text-[10px] text-slate-600 text-left py-2 px-2">Status</th>
              <th className="font-mono text-[10px] text-slate-600 text-left py-2 px-2">Subsystem</th>
              <th className="font-mono text-[10px] text-slate-600 text-right py-2 px-2">Complexity</th>
              <th className="font-mono text-[10px] text-slate-600 text-right py-2 px-2">Pass Rate</th>
              <th className="font-mono text-[10px] text-slate-600 text-right py-2 px-2">Cost</th>
            </tr>
          </thead>
          <tbody>
            {plans.map((p) => (
              <tr key={p.plan_id} className="border-b border-forge-border/50 hover:bg-forge-card/50 transition-colors">
                <td className="font-mono text-[11px] text-slate-500 py-2 px-2">{(p.created_at || '').slice(0, 10)}</td>
                <td className="font-mono text-xs text-slate-300 py-2 px-2 max-w-[300px] truncate">{p.subject}</td>
                <td className={`font-mono text-[11px] py-2 px-2 ${STATUS_COLORS[p.status] || 'text-slate-400'}`}>{p.status}</td>
                <td className="font-mono text-[11px] text-slate-500 py-2 px-2">{p.subsystem || '—'}</td>
                <td className="font-mono text-[11px] text-slate-500 py-2 px-2 text-right">{p.complexity_score ?? '—'}</td>
                <td className="font-mono text-[11px] text-slate-400 py-2 px-2 text-right">{p.pass_rate || '—'}</td>
                <td className="font-mono text-[11px] text-slate-500 py-2 px-2 text-right">
                  {p.estimated_cost_usd != null ? `$${p.estimated_cost_usd.toFixed(4)}` : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function AnalyticsTab() {
  const { report, cost, health, plans, loading, error, refresh } = usePlans()

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 w-full">
        <div className="flex items-center gap-3 text-slate-500 font-mono text-sm">
          <div className="w-2 h-2 rounded-full bg-slate-500 animate-pulse" />
          Loading analytics...
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-8">
      {error && (
        <div className="font-mono text-sm text-red-400 bg-red-400/10 border border-red-400/25 rounded px-4 py-2">
          Error: {error}
        </div>
      )}

      <div className="flex items-center justify-between">
        <h2 className="font-mono text-sm text-slate-300">v2 Pipeline Analytics</h2>
        <button
          onClick={refresh}
          className="font-mono text-[10px] text-slate-600 hover:text-slate-400 transition-colors"
        >
          refresh
        </button>
      </div>

      <ReportSection report={report} />
      <SubsystemHealth health={health} />
      <CostBreakdown cost={cost} />
      <RecentPlans plans={plans} />
    </div>
  )
}
