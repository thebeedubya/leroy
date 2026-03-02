import { useState } from 'react'
import { useDecisions } from '../../hooks/useDecisions'
import { relativeTime } from '../../utils'

const TYPE_CONFIG = {
  question: { label: 'QUESTION', color: 'text-blue-400 bg-blue-400/10 border-blue-400/30' },
  blocker: { label: 'BLOCKER', color: 'text-red-400 bg-red-400/10 border-red-400/30' },
  decision_gate: { label: 'DECISION', color: 'text-yellow-400 bg-yellow-400/10 border-yellow-400/30' },
  status_update: { label: 'STATUS', color: 'text-slate-400 bg-slate-400/10 border-slate-400/30' },
  deliverable_ready: { label: 'READY', color: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/30' },
}

function typeBadge(type) {
  const cfg = TYPE_CONFIG[type] || TYPE_CONFIG.status_update
  return (
    <span className={`px-1.5 py-0.5 rounded border font-mono text-[10px] uppercase ${cfg.color}`}>
      {cfg.label}
    </span>
  )
}

function urgencyClass(receivedAt) {
  if (!receivedAt) return ''
  const ms = Date.now() - new Date(receivedAt).getTime()
  if (ms > 30 * 60000) return 'border-l-2 border-red-500'
  if (ms > 10 * 60000) return 'border-l-2 border-yellow-500'
  return ''
}

function DecisionItem({ msg, onRespond }) {
  const [expanded, setExpanded] = useState(false)
  const [responseText, setResponseText] = useState('')
  const [sending, setSending] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState(null)

  const waitTime = msg.received_at ? relativeTime(msg.received_at) : '—'

  const handleSend = async () => {
    if (!responseText.trim()) return
    setSending(true)
    setError(null)
    try {
      await onRespond(msg.message_id, responseText.trim())
      setSent(true)
      setResponseText('')
      setExpanded(false)
    } catch (e) {
      setError(e.message)
    } finally {
      setSending(false)
    }
  }

  return (
    <div
      className={[
        'bg-forge-card border border-forge-border rounded-lg p-4 transition-colors',
        urgencyClass(msg.received_at),
      ].join(' ')}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
          {typeBadge(msg.type)}
          <span className="font-mono text-[11px] text-slate-500">task: {(msg.task_id || 'unknown').slice(0, 8)}</span>
          <span className="font-mono text-[11px] text-slate-600">waiting {waitTime}</span>
        </div>
        <button
          onClick={() => setExpanded((v) => !v)}
          className="font-mono text-xs text-slate-500 hover:text-slate-300 transition-colors flex-shrink-0"
        >
          {expanded ? 'collapse' : 'respond'}
        </button>
      </div>

      <p className="mt-2 font-mono text-sm text-slate-200 leading-relaxed">
        {msg.content}
      </p>

      {msg.context && (
        <p className="mt-1 font-mono text-xs text-slate-500 truncate">
          Context: {msg.context}
        </p>
      )}

      {msg.options && msg.options.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {msg.options.map((opt) => (
            <button
              key={opt}
              onClick={() => { setExpanded(true); setResponseText(opt) }}
              className="font-mono text-xs px-3 py-1.5 bg-forge-surface border border-forge-border rounded hover:border-blue-400/50 hover:text-blue-300 transition-colors"
            >
              {opt}
            </button>
          ))}
        </div>
      )}

      {expanded && (
        <div className="mt-3">
          <textarea
            className="w-full bg-forge-surface border border-forge-border rounded px-3 py-2 font-mono text-sm text-slate-200 focus:outline-none focus:border-blue-400/50 resize-none"
            rows={3}
            placeholder="Type your response..."
            value={responseText}
            onChange={(e) => setResponseText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                handleSend()
              }
            }}
          />
          {error && (
            <p className="mt-1 font-mono text-xs text-red-400">{error}</p>
          )}
          <div className="mt-2 flex items-center gap-3">
            <button
              onClick={handleSend}
              disabled={sending || !responseText.trim()}
              className="font-mono text-xs px-4 py-1.5 bg-blue-500 text-white rounded hover:bg-blue-400 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {sending ? 'Sending...' : 'Send (⌘↵)'}
            </button>
            <button
              onClick={() => setExpanded(false)}
              className="font-mono text-xs text-slate-500 hover:text-slate-300 transition-colors"
            >
              cancel
            </button>
          </div>
        </div>
      )}

      {sent && (
        <p className="mt-2 font-mono text-xs text-emerald-400">Response sent.</p>
      )}
    </div>
  )
}

export function DecisionsTab() {
  const { pending, loading, error, refresh, respond } = useDecisions()

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="font-mono text-sm font-semibold text-slate-300">
          Decisions
          {pending.length > 0 && (
            <span className="ml-2 inline-flex items-center justify-center min-w-[18px] h-4 px-1 rounded-full bg-red-500 text-white text-[10px] font-bold">
              {pending.length}
            </span>
          )}
        </h2>
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

      {loading && pending.length === 0 ? (
        <div className="font-mono text-sm text-slate-600">Loading decisions...</div>
      ) : pending.length === 0 ? (
        <div className="font-mono text-sm text-slate-600">
          No pending decisions. Leroy is unblocked.
        </div>
      ) : (
        <div className="space-y-3">
          <div className="font-mono text-xs text-slate-500 uppercase tracking-wider mb-2">
            Needs Your Response ({pending.length})
          </div>
          {pending.map((msg) => (
            <DecisionItem key={msg.message_id} msg={msg} onRespond={respond} />
          ))}
        </div>
      )}
    </div>
  )
}
