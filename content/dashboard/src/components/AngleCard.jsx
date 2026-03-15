import { useState } from 'react'
import ScoreBadge from './ScoreBadge.jsx'
import StatusBadge from './StatusBadge.jsx'
import PlatformTabs from './PlatformTabs.jsx'
import { approveAngle, rejectAngle, markPosted } from '../hooks/useContent.js'

const PLATFORMS = ['blog', 'linkedin', 'x', 'instagram']

export default function AngleCard({ angle, date, onUpdate }) {
  const [expanded, setExpanded] = useState(false)
  const [rejectMode, setRejectMode] = useState(false)
  const [rejectReason, setRejectReason] = useState('')
  const [postMode, setPostMode] = useState(false)
  const [postPlatform, setPostPlatform] = useState('')
  const [postUrl, setPostUrl] = useState('')
  const [actionError, setActionError] = useState(null)
  const [loading, setLoading] = useState(false)

  async function handleApprove() {
    setLoading(true)
    setActionError(null)
    try {
      await approveAngle(date, angle.index)
      onUpdate()
    } catch (e) {
      setActionError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleReject() {
    if (!rejectMode) {
      setRejectMode(true)
      return
    }
    setLoading(true)
    setActionError(null)
    try {
      await rejectAngle(date, angle.index, rejectReason)
      setRejectMode(false)
      setRejectReason('')
      onUpdate()
    } catch (e) {
      setActionError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleMarkPosted() {
    if (!postMode) {
      setPostMode(true)
      setPostPlatform(PLATFORMS[0])
      return
    }
    if (!postPlatform) {
      setActionError('Select a platform')
      return
    }
    setLoading(true)
    setActionError(null)
    try {
      await markPosted(date, angle.index, postPlatform, postUrl)
      setPostMode(false)
      setPostPlatform('')
      setPostUrl('')
      onUpdate()
    } catch (e) {
      setActionError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="bg-forge-card border border-forge-border rounded-lg overflow-hidden">
      {/* Card header */}
      <div
        className="flex items-start gap-3 p-4 cursor-pointer hover:bg-forge-muted/20 transition-colors"
        onClick={() => setExpanded((v) => !v)}
      >
        <ScoreBadge score={angle.score} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-sm font-semibold text-slate-100 leading-tight">{angle.title}</h3>
            <StatusBadge status={angle.status} />
          </div>
          {angle.target_angle && (
            <p className="text-xs text-slate-400 mt-1 leading-relaxed">{angle.target_angle}</p>
          )}
          <div className="flex items-center gap-3 mt-2 text-xs text-slate-500">
            {angle.confidence && (
              <span>
                Confidence:{' '}
                <span
                  className={
                    angle.confidence === 'high'
                      ? 'text-green-400'
                      : angle.confidence === 'medium'
                      ? 'text-yellow-400'
                      : 'text-slate-400'
                  }
                >
                  {angle.confidence}
                </span>
              </span>
            )}
            {angle.source_sessions && (
              <span className="truncate max-w-xs" title={angle.source_sessions}>
                Sessions: {angle.source_sessions}
              </span>
            )}
          </div>
          {angle.rejected_reason && (
            <p className="mt-1 text-xs text-red-400">Rejected: {angle.rejected_reason}</p>
          )}
        </div>
        <button
          className="text-slate-500 hover:text-slate-300 text-xs ml-2 flex-shrink-0"
          onClick={(e) => {
            e.stopPropagation()
            setExpanded((v) => !v)
          }}
        >
          {expanded ? '▲ Collapse' : '▼ Expand'}
        </button>
      </div>

      {/* Expanded content */}
      {expanded && (
        <div className="border-t border-forge-border">
          {/* Platform drafts */}
          <div className="p-4">
            <PlatformTabs platforms={angle.platforms} />
          </div>

          {/* Action bar */}
          <div className="border-t border-forge-border bg-forge-surface px-4 py-3">
            {actionError && (
              <p className="text-xs text-red-400 mb-2">{actionError}</p>
            )}

            {/* Reject inline input */}
            {rejectMode && (
              <div className="mb-3 flex items-center gap-2">
                <input
                  type="text"
                  placeholder="Reason for rejection..."
                  value={rejectReason}
                  onChange={(e) => setRejectReason(e.target.value)}
                  className="flex-1 bg-forge-bg border border-forge-border rounded px-3 py-1.5 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-red-500"
                  onKeyDown={(e) => e.key === 'Enter' && handleReject()}
                  autoFocus
                />
                <button
                  onClick={() => { setRejectMode(false); setRejectReason('') }}
                  className="text-xs text-slate-500 hover:text-slate-300 px-2 py-1.5"
                >
                  Cancel
                </button>
              </div>
            )}

            {/* Mark Posted inline inputs */}
            {postMode && (
              <div className="mb-3 flex items-center gap-2 flex-wrap">
                <select
                  value={postPlatform}
                  onChange={(e) => setPostPlatform(e.target.value)}
                  className="bg-forge-bg border border-forge-border rounded px-2 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-blue-500"
                >
                  {PLATFORMS.map((p) => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>
                <input
                  type="text"
                  placeholder="Post URL (optional)"
                  value={postUrl}
                  onChange={(e) => setPostUrl(e.target.value)}
                  className="flex-1 bg-forge-bg border border-forge-border rounded px-3 py-1.5 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-blue-500"
                  onKeyDown={(e) => e.key === 'Enter' && handleMarkPosted()}
                />
                <button
                  onClick={() => { setPostMode(false); setPostPlatform(''); setPostUrl('') }}
                  className="text-xs text-slate-500 hover:text-slate-300 px-2 py-1.5"
                >
                  Cancel
                </button>
              </div>
            )}

            <div className="flex items-center gap-2">
              {angle.status !== 'approved' && angle.status !== 'posted' && (
                <button
                  onClick={handleApprove}
                  disabled={loading}
                  className="px-3 py-1.5 rounded text-xs font-medium bg-green-700 hover:bg-green-600 text-white disabled:opacity-50 transition-colors"
                >
                  Approve
                </button>
              )}
              {angle.status !== 'rejected' && (
                <button
                  onClick={handleReject}
                  disabled={loading}
                  className={`px-3 py-1.5 rounded text-xs font-medium transition-colors disabled:opacity-50 ${
                    rejectMode
                      ? 'bg-red-700 hover:bg-red-600 text-white'
                      : 'bg-forge-muted hover:bg-slate-600 text-red-400 border border-red-800/50'
                  }`}
                >
                  {rejectMode ? 'Confirm Reject' : 'Reject'}
                </button>
              )}
              <button
                onClick={handleMarkPosted}
                disabled={loading}
                className={`px-3 py-1.5 rounded text-xs font-medium transition-colors disabled:opacity-50 ${
                  postMode
                    ? 'bg-blue-700 hover:bg-blue-600 text-white'
                    : 'bg-forge-muted hover:bg-slate-600 text-blue-400 border border-blue-800/50'
                }`}
              >
                {postMode ? 'Confirm Posted' : 'Mark Posted'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
