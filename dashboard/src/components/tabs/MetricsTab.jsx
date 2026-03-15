import { useV2Metrics } from '../../hooks/useV2Metrics'

function StatCard({ label, value, sub, color = 'text-slate-200' }) {
  return (
    <div className="bg-forge-card border border-forge-border rounded-lg p-4">
      <div className="font-mono text-[10px] text-slate-600 uppercase tracking-wider">{label}</div>
      <div className={`font-mono text-2xl font-bold mt-1 ${color}`}>{value}</div>
      {sub && <div className="font-mono text-[11px] text-slate-500 mt-1">{sub}</div>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Quality Metrics Panel
// ---------------------------------------------------------------------------
function QualityPanel({ quality }) {
  if (!quality) return null
  const dist = quality.score_distribution || {}

  return (
    <div>
      <h3 className="font-mono text-xs text-slate-500 uppercase tracking-wider mb-3">Quality Scoring</h3>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <StatCard
          label="Avg Score"
          value={quality.avg_score != null ? quality.avg_score.toFixed(2) : '—'}
          color={quality.avg_score >= 0.6 ? 'text-emerald-400' : quality.avg_score >= 0.4 ? 'text-yellow-400' : 'text-red-400'}
        />
        <StatCard label="Median Score" value={quality.median_score != null ? quality.median_score.toFixed(2) : '—'} color="text-blue-400" />
        <StatCard label="Scored Plans" value={quality.scored_count || 0} sub={`${quality.unscored_count || 0} unscored`} />
        <StatCard
          label="Brain Compliance"
          value={quality.brain_compliance_pct != null ? `${(quality.brain_compliance_pct * 100).toFixed(0)}%` : '—'}
          color={quality.brain_compliance_pct >= 0.9 ? 'text-emerald-400' : 'text-yellow-400'}
        />
      </div>

      {/* Score distribution bar */}
      {Object.keys(dist).length > 0 && (
        <div className="bg-forge-card border border-forge-border rounded-lg p-4">
          <div className="font-mono text-[10px] text-slate-600 mb-3">Score Distribution</div>
          <div className="space-y-2">
            {Object.entries(dist).map(([bucket, count]) => {
              const max = Math.max(...Object.values(dist), 1)
              const pct = (count / max) * 100
              return (
                <div key={bucket} className="flex items-center gap-2">
                  <span className="font-mono text-[10px] text-slate-500 w-16 flex-shrink-0">{bucket}</span>
                  <div className="flex-1 h-4 bg-forge-bg rounded overflow-hidden">
                    <div className="h-full bg-blue-500/40 rounded" style={{ width: `${Math.max(pct, 2)}%` }} />
                  </div>
                  <span className="font-mono text-[10px] text-slate-500 w-8 text-right">{count}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// v1 vs v2 Baseline Panel
// ---------------------------------------------------------------------------
function BaselinePanel({ baseline }) {
  if (!baseline) return null
  const improvements = baseline.improvements || []

  return (
    <div>
      <h3 className="font-mono text-xs text-slate-500 uppercase tracking-wider mb-3">
        v1 vs v2 Baseline
        <span className="text-slate-600 ml-2">({baseline.v1_plan_count || 0} v1, {baseline.v2_plan_count || 0} v2)</span>
      </h3>
      <div className="bg-forge-card border border-forge-border rounded-lg overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-forge-border">
              <th className="font-mono text-[10px] text-slate-600 text-left py-2 px-3">Metric</th>
              <th className="font-mono text-[10px] text-slate-600 text-right py-2 px-3">v1 Rate</th>
              <th className="font-mono text-[10px] text-slate-600 text-right py-2 px-3">v2 Rate</th>
              <th className="font-mono text-[10px] text-slate-600 text-right py-2 px-3">Change</th>
              <th className="font-mono text-[10px] text-slate-600 text-center py-2 px-3">Status</th>
            </tr>
          </thead>
          <tbody>
            {improvements.map((imp) => (
              <tr key={imp.metric} className="border-b border-forge-border/50">
                <td className="font-mono text-xs text-slate-300 py-2 px-3">{imp.metric}</td>
                <td className="font-mono text-[11px] text-slate-500 py-2 px-3 text-right">
                  {typeof imp.v1_rate === 'number' ? imp.v1_rate.toFixed(3) : '—'}
                </td>
                <td className="font-mono text-[11px] text-slate-400 py-2 px-3 text-right">
                  {typeof imp.v2_rate === 'number' ? imp.v2_rate.toFixed(3) : '—'}
                </td>
                <td className={`font-mono text-[11px] py-2 px-3 text-right ${imp.improved ? 'text-emerald-400' : 'text-red-400'}`}>
                  {imp.change > 0 ? '+' : ''}{typeof imp.change === 'number' ? imp.change.toFixed(3) : '—'}
                </td>
                <td className="font-mono text-[10px] py-2 px-3 text-center">
                  {imp.improved ? (
                    <span className="text-emerald-400">improved</span>
                  ) : (
                    <span className="text-red-400">regressed</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Suggestions Panel
// ---------------------------------------------------------------------------
function SuggestionsPanel({ suggestions }) {
  if (!suggestions || suggestions.length === 0) return null

  const SEVERITY_COLORS = {
    alert: 'border-red-400/30 bg-red-400/5',
    warning: 'border-yellow-400/30 bg-yellow-400/5',
    positive: 'border-emerald-400/30 bg-emerald-400/5',
    info: 'border-blue-400/30 bg-blue-400/5',
  }

  const SEVERITY_TEXT = {
    alert: 'text-red-400',
    warning: 'text-yellow-400',
    positive: 'text-emerald-400',
    info: 'text-blue-400',
  }

  return (
    <div>
      <h3 className="font-mono text-xs text-slate-500 uppercase tracking-wider mb-3">
        Improvement Suggestions <span className="text-slate-600">({suggestions.length})</span>
      </h3>
      <div className="space-y-3">
        {suggestions.map((s, i) => (
          <div key={i} className={`border rounded-lg p-4 ${SEVERITY_COLORS[s.severity] || SEVERITY_COLORS.info}`}>
            <div className="flex items-center gap-2 mb-2">
              <span className={`font-mono text-[10px] uppercase font-bold ${SEVERITY_TEXT[s.severity] || 'text-slate-400'}`}>
                {s.severity}
              </span>
              <span className="font-mono text-[10px] text-slate-600">{s.category}</span>
            </div>
            <p className="font-mono text-xs text-slate-300">{s.suggestion}</p>
            {s.action && (
              <p className="font-mono text-[11px] text-slate-500 mt-2 italic">Action: {s.action}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Golden Templates Panel
// ---------------------------------------------------------------------------
function TemplatesPanel({ templates }) {
  if (!templates || templates.length === 0) return null

  return (
    <div>
      <h3 className="font-mono text-xs text-slate-500 uppercase tracking-wider mb-3">
        Golden Spec Templates <span className="text-slate-600">({templates.length} subsystems)</span>
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {templates.map((t) => {
          const p = t.template_patterns || {}
          return (
            <div key={t.subsystem} className="bg-forge-card border border-forge-border rounded-lg p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="font-mono text-xs text-slate-300 font-medium">{t.subsystem}</span>
                <span className="font-mono text-[10px] text-emerald-400">{t.clean_pass_count} clean passes</span>
              </div>
              <div className="space-y-1">
                <div className="font-mono text-[10px] text-slate-500">
                  Avg criteria: {p.avg_criteria_count || '—'} · Complexity: {p.avg_complexity || '—'} · Target: {p.preferred_target || '—'}
                </div>
                <div className="font-mono text-[10px] text-slate-500">
                  Brain queried: {p.brain_queried_pct != null ? `${(p.brain_queried_pct * 100).toFixed(0)}%` : '—'}
                </div>
                {p.common_criteria_patterns && p.common_criteria_patterns.length > 0 && (
                  <div className="font-mono text-[10px] text-slate-600 mt-1">
                    Common terms: {p.common_criteria_patterns.slice(0, 6).join(', ')}
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Pattern Correlations Panel
// ---------------------------------------------------------------------------
function PatternsPanel({ patterns }) {
  if (!patterns || patterns.error) return null

  const sections = [
    { key: 'failure_correlations', label: 'Failure Correlations', rateKey: 'failure_rate', color: 'text-red-400' },
    { key: 'timeout_correlations', label: 'Timeout Correlations', rateKey: 'timeout_rate', color: 'text-yellow-400' },
    { key: 'respec_correlations', label: 'Respec Correlations', rateKey: 'respec_rate', color: 'text-orange-400' },
    { key: 'success_correlations', label: 'Success Correlations', rateKey: 'success_rate', color: 'text-emerald-400' },
  ]

  const hasAny = sections.some(s => (patterns[s.key] || []).length > 0)
  if (!hasAny) return null

  return (
    <div>
      <h3 className="font-mono text-xs text-slate-500 uppercase tracking-wider mb-3">
        Pattern Correlations <span className="text-slate-600">({patterns.plan_count || 0} plans analyzed)</span>
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {sections.map(({ key, label, rateKey, color }) => {
          const items = patterns[key] || []
          if (items.length === 0) return null
          return (
            <div key={key} className="bg-forge-card border border-forge-border rounded-lg p-4">
              <div className={`font-mono text-[10px] uppercase tracking-wider mb-2 ${color}`}>{label}</div>
              <div className="space-y-1">
                {items.slice(0, 5).map((item, i) => (
                  <div key={i} className="flex items-center justify-between">
                    <span className="font-mono text-[11px] text-slate-400">
                      {item.attribute}: {item.value}
                    </span>
                    <span className={`font-mono text-[11px] ${color}`}>
                      {(item[rateKey] * 100).toFixed(0)}% <span className="text-slate-600">({item.sample_size})</span>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Learned Thresholds Panel
// ---------------------------------------------------------------------------
function ThresholdsPanel({ thresholds }) {
  if (!thresholds || thresholds.sample_size === 0) return null

  const retryBudgets = thresholds.retry_budget_by_category || {}
  const qualityAdj = thresholds.quality_weight_adjustments || {}

  return (
    <div>
      <h3 className="font-mono text-xs text-slate-500 uppercase tracking-wider mb-3">
        Learned Thresholds <span className="text-slate-600">({thresholds.sample_size} plans)</span>
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="bg-forge-card border border-forge-border rounded-lg p-4">
          <div className="font-mono text-[10px] text-slate-600 uppercase mb-2">Complexity Warning</div>
          <div className="font-mono text-lg text-yellow-400 font-bold">{thresholds.complexity_warning_level}</div>
          <div className="font-mono text-[10px] text-slate-600">specs above this fail more often</div>
        </div>

        {Object.keys(retryBudgets).length > 0 && (
          <div className="bg-forge-card border border-forge-border rounded-lg p-4">
            <div className="font-mono text-[10px] text-slate-600 uppercase mb-2">Retry Budgets by Category</div>
            <div className="space-y-1">
              {Object.entries(retryBudgets).slice(0, 5).map(([cat, data]) => (
                <div key={cat} className="flex items-center justify-between">
                  <span className="font-mono text-[10px] text-slate-400">{cat}</span>
                  <span className="font-mono text-[10px] text-blue-400">
                    {data.suggested_budget} retries <span className="text-slate-600">({data.sample_size})</span>
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {qualityAdj.quality_predictive != null && (
          <div className="bg-forge-card border border-forge-border rounded-lg p-4">
            <div className="font-mono text-[10px] text-slate-600 uppercase mb-2">Quality Predictiveness</div>
            <div className={`font-mono text-sm font-bold ${qualityAdj.quality_predictive ? 'text-emerald-400' : 'text-yellow-400'}`}>
              {qualityAdj.quality_predictive ? 'Predictive' : 'Needs tuning'}
            </div>
            <div className="font-mono text-[10px] text-slate-600 mt-1">
              High-quality success: {((qualityAdj.high_quality_success_rate || 0) * 100).toFixed(0)}%
            </div>
            <div className="font-mono text-[10px] text-slate-600">
              Low-quality success: {((qualityAdj.low_quality_success_rate || 0) * 100).toFixed(0)}%
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main MetricsTab
// ---------------------------------------------------------------------------
export function MetricsTab() {
  const { quality, suggestions, templates, baseline, thresholds, patterns, loading, error, refresh } = useV2Metrics()

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 w-full">
        <div className="flex items-center gap-3 text-slate-500 font-mono text-sm">
          <div className="w-2 h-2 rounded-full bg-slate-500 animate-pulse" />
          Loading v2 metrics...
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
        <h2 className="font-mono text-sm text-slate-300">v2 Metrics + Improvement Engine</h2>
        <button
          onClick={refresh}
          className="font-mono text-[10px] text-slate-600 hover:text-slate-400 transition-colors"
        >
          refresh
        </button>
      </div>

      <QualityPanel quality={quality} />
      <BaselinePanel baseline={baseline} />
      <SuggestionsPanel suggestions={suggestions} />
      <PatternsPanel patterns={patterns} />
      <ThresholdsPanel thresholds={thresholds} />
      <TemplatesPanel templates={templates} />
    </div>
  )
}
