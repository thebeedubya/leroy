import { useState } from 'react'
import { formatTimestamp } from '../utils'

const cfg = {
  headerColor: 'text-violet-400',
  bgColor: 'bg-violet-400/10',
  textColor: 'text-violet-400',
  borderColor: 'border-violet-400/25',
}

function IdeaCard({ task, onPromote, onDiscard }) {
  const title = task.spec || task.task_id.slice(0, 12)
  const description = task.description || ''

  return (
    <div className="w-full rounded-lg border border-forge-border bg-forge-card p-3 group">
      {/* Title */}
      <p className="text-sm text-slate-200 leading-snug font-medium line-clamp-2 mb-1">
        {title}
      </p>

      {/* Description */}
      {description && (
        <p className="text-xs text-slate-500 leading-relaxed line-clamp-2 mb-2">
          {description}
        </p>
      )}

      {/* Footer: created date + actions */}
      <div className="flex items-center justify-between mt-2 pt-2 border-t border-forge-border/60">
        <span className="font-mono text-xs text-slate-600">
          {formatTimestamp(task.created_at)}
        </span>
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          {/* Promote button */}
          <button
            onClick={() => onPromote(task.task_id)}
            title="Promote to Pending"
            className="font-mono text-xs px-2 py-0.5 rounded border border-violet-400/30 text-violet-400 hover:bg-violet-400/15 hover:border-violet-400/60 transition-all"
          >
            →
          </button>
          {/* Discard button */}
          <button
            onClick={() => onDiscard(task.task_id)}
            title="Discard idea"
            className="font-mono text-xs px-2 py-0.5 rounded border border-slate-700 text-slate-600 hover:bg-red-400/10 hover:border-red-400/40 hover:text-red-400 transition-all"
          >
            ✕
          </button>
        </div>
      </div>
    </div>
  )
}

export function IdeaColumn({ tasks, onAddIdea, onPromoteIdea, onDiscardIdea }) {
  const [showForm, setShowForm] = useState(false)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const count = tasks.length

  const handleSubmit = async (e) => {
    e.preventDefault()
    const trimmedTitle = title.trim()
    if (!trimmedTitle) return
    setSubmitting(true)
    try {
      await onAddIdea(trimmedTitle, description.trim())
      setTitle('')
      setDescription('')
      setShowForm(false)
    } finally {
      setSubmitting(false)
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Escape') {
      setShowForm(false)
      setTitle('')
      setDescription('')
    }
  }

  return (
    <div className="flex flex-col min-w-0 h-full overflow-hidden">
      {/* Column header */}
      <div className="flex items-center justify-between mb-3 px-1 flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className={`font-mono text-xs font-bold tracking-widest ${cfg.headerColor}`}>
            IDEAS
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={`font-mono text-xs px-1.5 py-0.5 rounded ${cfg.bgColor} ${cfg.textColor} border ${cfg.borderColor}`}>
            {count}
          </span>
          <button
            onClick={() => setShowForm((v) => !v)}
            title="Add idea"
            className={`font-mono text-xs w-5 h-5 flex items-center justify-center rounded border transition-all ${
              showForm
                ? `${cfg.bgColor} ${cfg.borderColor} ${cfg.textColor}`
                : 'border-forge-border text-slate-600 hover:border-violet-400/40 hover:text-violet-400'
            }`}
          >
            +
          </button>
        </div>
      </div>

      {/* Divider */}
      <div
        className={`h-px mb-3 flex-shrink-0 ${count > 0 || showForm ? cfg.bgColor : 'bg-forge-border'}`}
        style={{ opacity: count > 0 || showForm ? 1 : 0.4 }}
      />

      {/* Cards + form */}
      <div className="flex flex-col gap-2 overflow-y-auto flex-1 min-h-0">
        {/* Inline add form */}
        {showForm && (
          <form
            onSubmit={handleSubmit}
            onKeyDown={handleKeyDown}
            className="rounded-lg border border-violet-400/30 bg-violet-400/5 p-3 flex flex-col gap-2"
          >
            <input
              autoFocus
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Idea title..."
              maxLength={200}
              className="w-full bg-forge-bg border border-forge-border rounded px-2 py-1.5 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-violet-400/50 font-mono"
            />
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Short description (optional)"
              maxLength={400}
              className="w-full bg-forge-bg border border-forge-border rounded px-2 py-1.5 text-xs text-slate-300 placeholder-slate-600 focus:outline-none focus:border-violet-400/50 font-mono"
            />
            <div className="flex gap-2 justify-end">
              <button
                type="button"
                onClick={() => { setShowForm(false); setTitle(''); setDescription('') }}
                className="font-mono text-xs px-2 py-1 rounded border border-forge-border text-slate-600 hover:text-slate-400 transition-colors"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={!title.trim() || submitting}
                className="font-mono text-xs px-3 py-1 rounded border border-violet-400/40 text-violet-400 bg-violet-400/10 hover:bg-violet-400/20 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
              >
                {submitting ? '...' : 'Add'}
              </button>
            </div>
          </form>
        )}

        {count === 0 && !showForm ? (
          <div className="text-center py-8">
            <span className="font-mono text-xs text-slate-700">—</span>
          </div>
        ) : (
          tasks.map((task) => (
            <IdeaCard
              key={task.task_id}
              task={task}
              onPromote={onPromoteIdea}
              onDiscard={onDiscardIdea}
            />
          ))
        )}
      </div>
    </div>
  )
}
