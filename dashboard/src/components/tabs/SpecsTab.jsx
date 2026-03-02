import { useState } from 'react'
import { useSpecs } from '../../hooks/useSpecs'
import { relativeTime } from '../../utils'

const STAGES = [
  { id: 'draft', label: 'Draft', color: 'text-slate-400 border-slate-500/30' },
  { id: 'sent', label: 'Sent', color: 'text-amber-400 border-amber-500/30' },
  { id: 'building', label: 'Building', color: 'text-blue-400 border-blue-500/30' },
  { id: 'qa', label: 'QA', color: 'text-cyan-400 border-cyan-500/30' },
  { id: 'done', label: 'Done', color: 'text-emerald-400 border-emerald-500/30' },
  { id: 'failed', label: 'Failed', color: 'text-red-400 border-red-500/30' },
]

function SpecCard({ spec }) {
  const [expanded, setExpanded] = useState(false)
  const timeDisplay = spec.created_at ? relativeTime(spec.created_at) : '—'

  return (
    <div
      className="bg-forge-card border border-forge-border rounded p-3 cursor-pointer hover:border-forge-muted transition-colors"
      onClick={() => setExpanded((v) => !v)}
    >
      <div className="font-mono text-xs text-slate-200 leading-snug line-clamp-2">
        {spec.title}
      </div>
      <div className="mt-1.5 flex items-center gap-2">
        {spec.task_id && (
          <span className="font-mono text-[10px] text-slate-600">
            {spec.task_id.slice(0, 8)}
          </span>
        )}
        {spec.draft_file && (
          <span className="font-mono text-[10px] text-slate-600">{spec.draft_file}</span>
        )}
        <span className="font-mono text-[10px] text-slate-700 ml-auto">{timeDisplay}</span>
      </div>

      {spec.qa_pass_rate && (
        <div className="mt-1 font-mono text-[10px] text-emerald-400">
          QA: {spec.qa_pass_rate}
        </div>
      )}

      {expanded && spec.task_id && (
        <div className="mt-2 pt-2 border-t border-forge-border">
          <div className="font-mono text-[10px] text-slate-600 break-all">
            task_id: {spec.task_id}
          </div>
          {spec.completed_at && (
            <div className="font-mono text-[10px] text-slate-600">
              completed: {relativeTime(spec.completed_at)}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function StageColumn({ stage, specs }) {
  return (
    <div className="flex flex-col min-w-[220px] max-w-[280px] flex-1">
      <div className={`font-mono text-[11px] font-semibold uppercase tracking-wider mb-3 flex items-center gap-2 ${stage.color.split(' ')[0]}`}>
        <span>{stage.label}</span>
        {specs.length > 0 && (
          <span className="font-mono text-[10px] text-slate-600">({specs.length})</span>
        )}
      </div>
      <div className="flex-1 overflow-y-auto space-y-2 pr-1">
        {specs.length === 0 ? (
          <div className="font-mono text-[11px] text-slate-700 italic">empty</div>
        ) : (
          specs.map((s, i) => <SpecCard key={s.task_id || `draft-${i}`} spec={s} />)
        )}
      </div>
    </div>
  )
}

export function SpecsTab() {
  const { specs, loading, error, refresh } = useSpecs()
  const [showArchived, setShowArchived] = useState(false)

  const visibleSpecs = showArchived ? specs : specs.filter((s) => !s.archived)

  const byStage = {}
  for (const stage of STAGES) {
    byStage[stage.id] = visibleSpecs.filter((s) => s.stage === stage.id)
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-6">
      <div className="flex items-center justify-between mb-4 flex-shrink-0">
        <h2 className="font-mono text-sm font-semibold text-slate-300">Spec Pipeline</h2>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
              className="w-3 h-3"
            />
            <span className="font-mono text-[11px] text-slate-500">archived</span>
          </label>
          <button
            onClick={refresh}
            className="font-mono text-xs text-slate-500 hover:text-slate-300 transition-colors"
          >
            refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 font-mono text-xs text-red-400 bg-red-400/10 border border-red-400/25 rounded px-3 py-2 flex-shrink-0">
          {error}
        </div>
      )}

      {loading && specs.length === 0 ? (
        <div className="font-mono text-sm text-slate-600">Loading specs...</div>
      ) : (
        <div className="flex-1 overflow-x-auto overflow-y-hidden">
          <div className="flex gap-4 h-full min-w-max">
            {STAGES.map((stage) => (
              <StageColumn key={stage.id} stage={stage} specs={byStage[stage.id] || []} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
