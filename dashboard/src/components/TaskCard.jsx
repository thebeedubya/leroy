import { getTaskTitle, getDuration, formatTimestamp, getResultPreview, parseSuccessCriteria, getStatusConfig } from '../utils'

export function TaskCard({ task, isSelected, onClick }) {
  const cfg = getStatusConfig(task.status)
  const title = getTaskTitle(task)
  const duration = getDuration(task)
  const preview = getResultPreview(task.result, 2)
  const criteria = task.status === 'completed' ? parseSuccessCriteria(task.result) : null
  const shortId = task.task_id.slice(0, 8)

  return (
    <button
      onClick={onClick}
      className={`
        w-full text-left rounded-lg border p-3 transition-all duration-150 cursor-pointer
        ${isSelected
          ? `${cfg.bgColor} ${cfg.borderColor} ring-1 ring-inset ${cfg.borderColor}`
          : 'bg-forge-card border-forge-border hover:border-forge-muted hover:bg-forge-surface'
        }
      `}
    >
      {/* Status badge + ID */}
      <div className="flex items-center justify-between mb-2 gap-2">
        <div className="flex items-center gap-1.5">
          <div
            className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${cfg.dotColor} ${cfg.pulse ? 'animate-pulse' : ''}`}
          />
          <span className={`font-mono text-xs font-semibold tracking-wider ${cfg.textColor}`}>
            {cfg.label}
          </span>
        </div>
        <span className="font-mono text-xs text-slate-600">{shortId}</span>
      </div>

      {/* Title */}
      <p className="text-sm text-slate-200 leading-snug font-medium mb-2 line-clamp-2">
        {title}
      </p>

      {/* Result preview (completed/failed only) */}
      {preview && (
        <p className="text-xs text-slate-500 leading-relaxed mb-2 line-clamp-2 font-mono">
          {preview}
        </p>
      )}

      {/* Running tasks: show elapsed time */}
      {task.status === 'working' && duration && (
        <div className="flex items-center gap-1.5 mt-1">
          <div className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse flex-shrink-0" />
          <span className="font-mono text-xs text-blue-400">{duration} elapsed</span>
        </div>
      )}

      {/* Footer: timestamp + duration + criteria */}
      <div className="flex items-center justify-between mt-2 pt-2 border-t border-forge-border/60">
        <span className="font-mono text-xs text-slate-600">
          {formatTimestamp(task.created_at)}
        </span>
        <div className="flex items-center gap-2">
          {/* Success criteria badge */}
          {criteria && (
            <span
              className={`font-mono text-xs px-1.5 py-0.5 rounded ${
                criteria.fails === 0
                  ? 'text-emerald-400 bg-emerald-400/10'
                  : 'text-red-400 bg-red-400/10'
              }`}
            >
              {criteria.passes}/{criteria.total} PASS
            </span>
          )}
          {/* Duration for finished tasks */}
          {(task.status === 'completed' || task.status === 'failed') && duration && (
            <span className="font-mono text-xs text-slate-600">{duration}</span>
          )}
        </div>
      </div>
    </button>
  )
}
