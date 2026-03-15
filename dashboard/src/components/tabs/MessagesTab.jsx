import { useState } from 'react'
import { useMessages } from '../../hooks/useMessages'
import { relativeTime } from '../../utils'

const TYPE_CONFIG = {
  request: { label: 'REQUEST', color: 'text-blue-400 bg-blue-400/10 border-blue-400/30' },
  question: { label: 'QUESTION', color: 'text-blue-400 bg-blue-400/10 border-blue-400/30' },
  blocker: { label: 'BLOCKER', color: 'text-red-400 bg-red-400/10 border-red-400/30' },
  decision_gate: { label: 'DECISION', color: 'text-yellow-400 bg-yellow-400/10 border-yellow-400/30' },
  status_update: { label: 'STATUS', color: 'text-slate-400 bg-slate-400/10 border-slate-400/30' },
  deliverable_ready: { label: 'READY', color: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/30' },
  action_required: { label: 'ACTION', color: 'text-orange-400 bg-orange-400/10 border-orange-400/30' },
  infra_alert: { label: 'INFRA', color: 'text-red-400 bg-red-400/10 border-red-400/30' },
  escalation: { label: 'ESCALATION', color: 'text-red-500 bg-red-500/10 border-red-500/30' },
  alert: { label: 'ALERT', color: 'text-orange-400 bg-orange-400/10 border-orange-400/30' },
  response: { label: 'RESPONSE', color: 'text-green-400 bg-green-400/10 border-green-400/30' },
}

function typeBadge(type) {
  const cfg = TYPE_CONFIG[type] || { label: type?.toUpperCase() || '?', color: 'text-slate-400 bg-slate-400/10 border-slate-400/30' }
  return (
    <span className={`px-1.5 py-0.5 rounded border font-mono text-[10px] uppercase ${cfg.color}`}>
      {cfg.label}
    </span>
  )
}

function statusDot(msg) {
  if (msg.requires_response && !msg.responded) {
    return <span className="w-2 h-2 rounded-full bg-yellow-400 animate-pulse flex-shrink-0" title="Awaiting response" />
  }
  if (msg.responded) {
    return <span className="w-2 h-2 rounded-full bg-green-400 flex-shrink-0" title="Responded" />
  }
  if (!msg.read) {
    return <span className="w-2 h-2 rounded-full bg-blue-400 flex-shrink-0" title="Unread" />
  }
  return <span className="w-2 h-2 rounded-full bg-slate-600 flex-shrink-0" title="Read" />
}

function MessageItem({ msg, onRespond, onMarkRead }) {
  const [expanded, setExpanded] = useState(false)
  const [responseText, setResponseText] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState(null)

  const handleSend = async () => {
    if (!responseText.trim()) return
    setSending(true)
    setError(null)
    try {
      await onRespond(msg.message_id, responseText.trim())
      setResponseText('')
      setExpanded(false)
    } catch (e) {
      setError(e.message)
    } finally {
      setSending(false)
    }
  }

  const needsResponse = msg.requires_response && !msg.responded

  return (
    <div className={[
      'bg-forge-card border border-forge-border rounded-lg p-4 transition-colors',
      needsResponse ? 'border-l-2 border-l-yellow-500' : '',
      msg.type === 'escalation' ? 'border-l-2 border-l-red-500' : '',
    ].join(' ')}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
          {statusDot(msg)}
          {typeBadge(msg.type)}
          <span className="font-mono text-[11px] text-cyan-400">{msg.from}</span>
          <span className="font-mono text-[11px] text-slate-600">&rarr;</span>
          <span className="font-mono text-[11px] text-purple-400">{msg.to}</span>
          {msg.task_id && (
            <span className="font-mono text-[10px] text-slate-600">task: {msg.task_id.slice(0, 8)}</span>
          )}
          <span className="font-mono text-[10px] text-slate-700">
            {msg.created_at ? relativeTime(msg.created_at) : ''}
          </span>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {!msg.read && (
            <button
              onClick={() => onMarkRead(msg.message_id)}
              className="font-mono text-[10px] text-slate-600 hover:text-slate-400 transition-colors"
            >
              mark read
            </button>
          )}
          {needsResponse && (
            <button
              onClick={() => setExpanded((v) => !v)}
              className="font-mono text-xs text-yellow-400 hover:text-yellow-300 transition-colors"
            >
              {expanded ? 'collapse' : 'respond'}
            </button>
          )}
        </div>
      </div>

      <p className="mt-2 font-mono text-sm text-slate-200 leading-relaxed whitespace-pre-wrap">
        {msg.content}
      </p>

      {msg.context && (
        <p className="mt-1 font-mono text-xs text-slate-500 leading-relaxed">
          Context: {msg.context}
        </p>
      )}

      {msg.responded && msg.response && (
        <div className="mt-2 pl-3 border-l-2 border-green-500/30">
          <p className="font-mono text-xs text-green-400">
            {msg.response_from || 'responder'}: {msg.response}
          </p>
          {msg.responded_at && (
            <span className="font-mono text-[10px] text-slate-700">{relativeTime(msg.responded_at)}</span>
          )}
        </div>
      )}

      {expanded && (
        <div className="mt-3 space-y-2">
          <textarea
            value={responseText}
            onChange={(e) => setResponseText(e.target.value)}
            placeholder="Type your response..."
            className="w-full bg-forge-bg border border-forge-border rounded px-3 py-2 font-mono text-sm text-slate-200 focus:outline-none focus:border-yellow-500/50 resize-none"
            rows={3}
            autoFocus
          />
          {error && (
            <p className="font-mono text-xs text-red-400">{error}</p>
          )}
          <div className="flex justify-end">
            <button
              onClick={handleSend}
              disabled={sending || !responseText.trim()}
              className="px-3 py-1.5 bg-yellow-500/20 text-yellow-400 border border-yellow-500/30 rounded font-mono text-xs hover:bg-yellow-500/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {sending ? 'Sending...' : 'Send Response'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function AgentSidebar({ agents, filter, setFilter }) {
  const allUnread = agents.reduce((sum, a) => sum + (a.unread_count || 0), 0)
  const allPending = agents.reduce((sum, a) => sum + (a.pending_response_count || 0), 0)

  return (
    <div className="w-48 border-r border-forge-border p-3 space-y-1 flex-shrink-0 overflow-y-auto">
      <h3 className="font-mono text-[10px] text-slate-600 uppercase tracking-wider mb-2">Mailboxes</h3>

      <button
        onClick={() => setFilter({ to: '', from: '', pending: false, unread: false })}
        className={[
          'w-full text-left px-2 py-1.5 rounded font-mono text-xs transition-colors',
          !filter.to && !filter.pending && !filter.unread
            ? 'bg-blue-500/10 text-blue-400'
            : 'text-slate-400 hover:text-slate-200 hover:bg-forge-card',
        ].join(' ')}
      >
        All ({allUnread} unread)
      </button>

      <button
        onClick={() => setFilter({ to: '', from: '', pending: true, unread: false })}
        className={[
          'w-full text-left px-2 py-1.5 rounded font-mono text-xs transition-colors',
          filter.pending
            ? 'bg-yellow-500/10 text-yellow-400'
            : 'text-slate-400 hover:text-slate-200 hover:bg-forge-card',
        ].join(' ')}
      >
        Needs Response ({allPending})
      </button>

      <div className="border-t border-forge-border my-2" />

      {agents.map((agent) => (
        <button
          key={agent.name}
          onClick={() => setFilter({ to: agent.name, from: '', pending: false, unread: false })}
          className={[
            'w-full text-left px-2 py-1.5 rounded font-mono text-xs transition-colors flex justify-between',
            filter.to === agent.name
              ? 'bg-blue-500/10 text-blue-400'
              : 'text-slate-400 hover:text-slate-200 hover:bg-forge-card',
          ].join(' ')}
        >
          <span>{agent.name}</span>
          <span className="text-slate-600">
            {(agent.unread_count || 0) > 0 && (
              <span className="text-blue-400">{agent.unread_count}</span>
            )}
          </span>
        </button>
      ))}
    </div>
  )
}

export function MessagesTab() {
  const { messages, agents, loading, error, filter, setFilter, refresh, markRead, respond } = useMessages()

  if (loading && messages.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 w-full">
        <div className="flex items-center gap-3 text-slate-500 font-mono text-sm">
          <div className="w-2 h-2 rounded-full bg-slate-500 animate-pulse" />
          Loading messages...
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full w-full overflow-hidden">
      <AgentSidebar agents={agents} filter={filter} setFilter={setFilter} />

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {error && (
          <div className="font-mono text-sm text-red-400 bg-red-400/10 border border-red-400/25 rounded px-4 py-2">
            Error: {error}
          </div>
        )}

        <div className="flex items-center justify-between mb-2">
          <h2 className="font-mono text-sm text-slate-400">
            {filter.to ? `${filter.to} inbox` : filter.pending ? 'Needs Response' : 'All Messages'}
            <span className="text-slate-600 ml-2">({messages.length})</span>
          </h2>
          <button
            onClick={refresh}
            className="font-mono text-[10px] text-slate-600 hover:text-slate-400 transition-colors"
          >
            refresh
          </button>
        </div>

        {messages.length === 0 ? (
          <div className="text-center py-12 font-mono text-sm text-slate-600">
            No messages{filter.to ? ` for ${filter.to}` : ''}.
          </div>
        ) : (
          messages.map((msg) => (
            <MessageItem
              key={msg.message_id}
              msg={msg}
              onRespond={respond}
              onMarkRead={markRead}
            />
          ))
        )}
      </div>
    </div>
  )
}
