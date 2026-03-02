import { useState } from 'react'
import { TaskCard } from './TaskCard'

const DEFAULT_CAP = 10

export function TaskColumn({ label, status, tasks, selectedTaskId, onSelectTask, statusConfig, maxVisible }) {
  const cfg = statusConfig
  const count = tasks.length
  const cap = maxVisible ?? null
  const [showAll, setShowAll] = useState(false)

  const visibleTasks = cap && !showAll ? tasks.slice(0, cap) : tasks
  const hiddenCount = cap ? Math.max(0, count - cap) : 0

  return (
    <div className="flex flex-col min-w-0 h-full overflow-hidden">
      {/* Column header */}
      <div className="flex items-center justify-between mb-3 px-1 flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className={`font-mono text-xs font-bold tracking-widest ${cfg.headerColor}`}>
            {label}
          </span>
        </div>
        <span
          className={`font-mono text-xs px-1.5 py-0.5 rounded ${cfg.bgColor} ${cfg.textColor} border ${cfg.borderColor}`}
        >
          {count}
        </span>
      </div>

      {/* Divider */}
      <div className={`h-px mb-3 flex-shrink-0 ${count > 0 ? cfg.bgColor : 'bg-forge-border'}`}
        style={{ opacity: count > 0 ? 1 : 0.4 }}
      />

      {/* Cards */}
      <div className="flex flex-col gap-2 overflow-y-auto flex-1 min-h-0">
        {count === 0 ? (
          <div className="text-center py-8">
            <span className="font-mono text-xs text-slate-700">—</span>
          </div>
        ) : (
          <>
            {visibleTasks.map((task) => (
              <TaskCard
                key={task.task_id}
                task={task}
                isSelected={task.task_id === selectedTaskId}
                onClick={() => onSelectTask(task.task_id === selectedTaskId ? null : task.task_id)}
              />
            ))}
            {cap && hiddenCount > 0 && !showAll && (
              <button
                onClick={() => setShowAll(true)}
                className="font-mono text-xs text-slate-600 hover:text-slate-400 transition-colors py-2 text-center border border-forge-border rounded hover:border-slate-600"
              >
                +{hiddenCount} more
              </button>
            )}
            {cap && showAll && count > cap && (
              <button
                onClick={() => setShowAll(false)}
                className="font-mono text-xs text-slate-600 hover:text-slate-400 transition-colors py-2 text-center border border-forge-border rounded hover:border-slate-600"
              >
                show less
              </button>
            )}
          </>
        )}
      </div>
    </div>
  )
}
