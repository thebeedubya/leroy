import { useState } from 'react'
import { relativeTime } from '../utils'

// Proposal type badge config
const PROPOSAL_TYPE_CONFIG = {
  qa_spec:    { label: 'QA Spec',    color: 'text-blue-400 bg-blue-400/10 border-blue-400/30' },
  build_spec: { label: 'Build Spec', color: 'text-amber-400 bg-amber-400/10 border-amber-400/30' },
  respec:     { label: 'Respec',     color: 'text-red-400 bg-red-400/10 border-red-400/30' },
}

function proposalTypeBadge(type) {
  const cfg = PROPOSAL_TYPE_CONFIG[type] || {
    label: type || 'Proposal',
    color: 'text-slate-400 bg-slate-400/10 border-slate-400/30',
  }
  return (
    <span className={`px-1.5 py-0.5 rounded border font-mono text-[10px] uppercase tracking-wide ${cfg.color}`}>
      {cfg.label}
    </span>
  )
}

function SpecPreview({ content }) {
  const [expanded, setExpanded] = useState(false)
  if (!content) return null

  const lines = content.split('\n')
  const preview = lines.slice(0, 20).join('\n')
  const hasMore = lines.length > 20

  return (
    <div className="mt-3">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="font-mono text-[10px] text-slate-500 hover:text-slate-300 transition-colors mb-1.5 flex items-center gap-1"
      >
        <span>{expanded ? '▼' : '▶'}</span>
        <span>spec preview {hasMore && !expanded ? `(${lines.length} lines, showing 20)` : ''}</span>
      </button>
      {expanded ? (
        <pre className="bg-forge-surface border border-forge-border rounded p-3 font-mono text-[11px] text-slate-300 overflow-x-auto whitespace-pre-wrap leading-relaxed">
          {content}
        </pre>
      ) : (
        <pre className="bg-forge-surface border border-forge-border rounded p-3 font-mono text-[11px] text-slate-300 overflow-x-auto whitespace-pre-wrap leading-relaxed">
          {preview}
          {hasMore && (
            <span className="text-slate-600">
              {'\n'}... {lines.length - 20} more lines
            </span>
          )}
        </pre>
      )}
    </div>
  )
}

function ProposalCard({ proposal, onApprove, onReject }) {
  const [rejectMode, setRejectMode] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [removing, setRemoving] = useState(false)

  const triggerLabel = proposal.trigger_event
    ? `Triggered by: ${proposal.trigger_event}${proposal.trigger_task_id ? ` on ${proposal.trigger_task_id.slice(0, 8)}` : ''}`
    : null

  const handleApprove = async () => {
    setBusy(true)
    setError(null)
    try {
      setRemoving(true)
      await onApprove(proposal.proposal_id)
    } catch (e) {
      setRemoving(false)
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const handleReject = async () => {
    if (!feedback.trim()) return
    setBusy(true)
    setError(null)
    try {
      setRemoving(true)
      await onReject(proposal.proposal_id, feedback.trim())
    } catch (e) {
      setRemoving(false)
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (removing) {
    return (
      <div className="bg-forge-card border border-forge-border rounded-lg p-4 opacity-0 transition-opacity duration-300 pointer-events-none" />
    )
  }

  return (
    <div className="bg-forge-card border border-forge-border rounded-lg p-4 transition-colors">
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1.5 min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            {proposalTypeBadge(proposal.proposal_type)}
            <span className="font-mono text-[11px] text-slate-500">
              waiting {relativeTime(proposal.created_at)}
            </span>
          </div>
          <p className="font-mono text-sm font-semibold text-slate-100 leading-snug">
            {proposal.title || 'Untitled Proposal'}
          </p>
        </div>
      </div>

      {/* Trigger */}
      {triggerLabel && (
        <p className="mt-1.5 font-mono text-[11px] text-slate-500 truncate">
          {triggerLabel}
        </p>
      )}

      {/* Reasoning */}
      {proposal.reasoning && (
        <p className="mt-1 font-mono text-[11px] text-slate-400 line-clamp-2">
          {proposal.reasoning}
        </p>
      )}

      {/* Spec preview */}
      <SpecPreview content={proposal.content} />

      {/* Actions */}
      {!rejectMode ? (
        <div className="mt-3 flex items-center gap-2">
          <button
            onClick={handleApprove}
            disabled={busy}
            className="font-mono text-xs px-4 py-1.5 bg-emerald-600 text-white rounded hover:bg-emerald-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {busy ? 'Approving...' : 'Approve'}
          </button>
          <button
            onClick={() => setRejectMode(true)}
            disabled={busy}
            className="font-mono text-xs px-4 py-1.5 bg-red-600/80 text-white rounded hover:bg-red-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Reject
          </button>
        </div>
      ) : (
        <div className="mt-3">
          <textarea
            className="w-full bg-forge-surface border border-forge-border rounded px-3 py-2 font-mono text-sm text-slate-200 focus:outline-none focus:border-red-400/50 resize-none"
            rows={2}
            placeholder="Rejection reason (required)..."
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleReject()
              if (e.key === 'Escape') { setRejectMode(false); setFeedback('') }
            }}
          />
          <div className="mt-2 flex items-center gap-3">
            <button
              onClick={handleReject}
              disabled={busy || !feedback.trim()}
              className="font-mono text-xs px-4 py-1.5 bg-red-600 text-white rounded hover:bg-red-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {busy ? 'Rejecting...' : 'Confirm Reject (⌘↵)'}
            </button>
            <button
              onClick={() => { setRejectMode(false); setFeedback('') }}
              className="font-mono text-xs text-slate-500 hover:text-slate-300 transition-colors"
            >
              cancel
            </button>
          </div>
        </div>
      )}

      {error && (
        <p className="mt-2 font-mono text-xs text-red-400">{error}</p>
      )}
    </div>
  )
}

function RecentProposalRow({ proposal }) {
  const approved = proposal.status === 'approved'
  return (
    <div className="flex items-start gap-3 py-2 border-b border-forge-border last:border-0">
      <span className={`font-mono text-[10px] px-1.5 py-0.5 rounded border flex-shrink-0 ${
        approved
          ? 'text-emerald-400 bg-emerald-400/10 border-emerald-400/30'
          : 'text-red-400 bg-red-400/10 border-red-400/30'
      }`}>
        {approved ? 'APPROVED' : 'REJECTED'}
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-mono text-xs text-slate-300 truncate">
          {proposal.title || 'Untitled'}
        </p>
        {!approved && proposal.reviewer_feedback && (
          <p className="font-mono text-[11px] text-slate-500 truncate">
            {proposal.reviewer_feedback}
          </p>
        )}
      </div>
      <span className="font-mono text-[10px] text-slate-600 flex-shrink-0">
        {relativeTime(proposal.reviewed_at || proposal.created_at)}
      </span>
    </div>
  )
}

export function ProposalsSection({ proposals, recent, onApprove, onReject }) {
  const [recentExpanded, setRecentExpanded] = useState(false)

  // Section is hidden when no pending proposals
  if (!proposals || proposals.length === 0) return null

  return (
    <div className="mb-6">
      {/* Section header */}
      <div className="flex items-center gap-2 mb-3">
        <span className="font-mono text-xs text-slate-500 uppercase tracking-wider">
          PM Proposals
        </span>
        <span className="inline-flex items-center justify-center min-w-[18px] h-4 px-1 rounded-full bg-amber-500 text-white text-[10px] font-bold">
          {proposals.length}
        </span>
      </div>

      {/* Pending proposal cards */}
      <div className="space-y-3">
        {proposals.map((proposal) => (
          <ProposalCard
            key={proposal.proposal_id}
            proposal={proposal}
            onApprove={onApprove}
            onReject={onReject}
          />
        ))}
      </div>

      {/* Recently decided (collapsed by default) */}
      {recent && recent.length > 0 && (
        <div className="mt-4">
          <button
            onClick={() => setRecentExpanded((v) => !v)}
            className="font-mono text-[10px] text-slate-500 hover:text-slate-300 transition-colors flex items-center gap-1 uppercase tracking-wider"
          >
            <span>{recentExpanded ? '▼' : '▶'}</span>
            <span>recently decided ({recent.length})</span>
          </button>
          {recentExpanded && (
            <div className="mt-2 bg-forge-card border border-forge-border rounded-lg px-3 py-1">
              {recent.map((p) => (
                <RecentProposalRow key={p.proposal_id} proposal={p} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
