import { useEffect, useState } from 'react'
import { useTaskDetail } from '../hooks/useTaskDetail'
import { useTaskSubtasks } from '../hooks/useTaskSubtasks'
import { useTaskMessages } from '../hooks/useTaskMessages'
import {
  getTaskTitle,
  getDuration,
  getSubtaskDuration,
  formatTimestamp,
  parseSuccessCriteria,
  getStatusConfig,
} from '../utils'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export function TaskDetail({ taskId, onClose, tasks }) {
  const { task, loading, error, fetchTask } = useTaskDetail()
  const { subtasks } = useTaskSubtasks(taskId, task?.status)
  const { messages } = useTaskMessages(taskId, task?.status)
  const [specExpanded, setSpecExpanded] = useState(false)
  const [expandedSubtasks, setExpandedSubtasks] = useState({})
  const [reviewLoading, setReviewLoading] = useState(false)
  const [reviewError, setReviewError] = useState(null)
  const [rejectReason, setRejectReason] = useState('')
  const [showRejectInput, setShowRejectInput] = useState(false)

  useEffect(() => {
    fetchTask(taskId)
  }, [taskId, fetchTask])

  // Auto-refresh detail for active tasks (working/pending/waiting_for_pm)
  useEffect(() => {
    if (!task) return
    if (task.status !== 'working' && task.status !== 'pending' && task.status !== 'waiting_for_pm') return
    const interval = setInterval(() => fetchTask(taskId), 3000)
    return () => clearInterval(interval)
  }, [task, taskId, fetchTask])

  // Reset expanded state when task changes
  useEffect(() => {
    setSpecExpanded(false)
    setExpandedSubtasks({})
  }, [taskId])

  const toggleSubtask = (id) => setExpandedSubtasks((prev) => ({ ...prev, [id]: !prev[id] }))

  const handleReview = async (decision) => {
    if (decision === 'rejected' && !showRejectInput) {
      setShowRejectInput(true)
      return
    }
    setReviewLoading(true)
    setReviewError(null)
    try {
      const res = await fetch(`http://127.0.0.1:9800/tasks/${taskId}/review`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: 'Bearer LEROY_A2A_TOKEN_REDACTED',
        },
        body: JSON.stringify({
          decision,
          reason: decision === 'rejected' ? rejectReason : undefined,
        }),
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.error || `HTTP ${res.status}`)
      }
      setShowRejectInput(false)
      setRejectReason('')
      fetchTask(taskId)
    } catch (e) {
      setReviewError(e.message)
    } finally {
      setReviewLoading(false)
    }
  }

  if (!taskId) return null

  const cfg = task ? getStatusConfig(task.status) : null
  const title = task ? getTaskTitle(task) : '—'
  const duration = task ? getDuration(task) : null
  const criteria = (task?.status === 'completed' || task?.status === 'qa_review') ? parseSuccessCriteria(task.result) : null

  return (
    <div className="bg-forge-surface h-full">
      {/* Detail header */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-forge-border">
        <div className="flex items-center gap-3 min-w-0">
          {cfg && (
            <div className="flex items-center gap-1.5 flex-shrink-0">
              <div className={`w-1.5 h-1.5 rounded-full ${cfg.dotColor} ${cfg.pulse ? 'animate-pulse' : ''}`} />
              <span className={`font-mono text-xs font-bold tracking-wider ${cfg.textColor}`}>
                {cfg.label}
              </span>
            </div>
          )}
          <span className="font-mono text-xs text-slate-600 flex-shrink-0">
            {taskId?.slice(0, 16)}
          </span>
          {task && (
            <span className="text-sm text-slate-300 font-medium truncate">{title}</span>
          )}
        </div>
        <button
          onClick={onClose}
          className="font-mono text-xs text-slate-600 hover:text-slate-300 transition-colors px-2 py-1 rounded hover:bg-forge-card flex-shrink-0 ml-4"
        >
          [close]
        </button>
      </div>

      {/* Detail body */}
      <div className="px-6 py-4">
        {loading && !task && (
          <div className="flex items-center gap-2 text-slate-500 font-mono text-sm">
            <div className="w-1.5 h-1.5 rounded-full bg-slate-500 animate-pulse" />
            loading...
          </div>
        )}

        {error && (
          <div className="font-mono text-sm text-red-400 bg-red-400/10 border border-red-400/20 rounded px-3 py-2">
            Error: {error}
          </div>
        )}

        {task && (
          <>
            <div className="grid grid-cols-2 gap-4">
              {/* Left: Spec (collapsible) */}
              <div>
                <button
                  onClick={() => setSpecExpanded((v) => !v)}
                  className="flex items-center justify-between mb-2 w-full text-left hover:opacity-80 transition-opacity"
                >
                  <span className="font-mono text-xs text-slate-500 tracking-wider">
                    SPEC {specExpanded ? '▼' : '▶'}
                  </span>
                  <span className="font-mono text-xs text-slate-700">
                    {task.spec?.length?.toLocaleString()} chars
                  </span>
                </button>
                <div className={`bg-forge-card border border-forge-border rounded p-3 overflow-auto transition-all ${specExpanded ? 'max-h-96' : 'max-h-24'}`}>
                  <pre className="font-mono text-xs text-slate-300 whitespace-pre-wrap leading-relaxed">
                    {task.spec || '(empty)'}
                  </pre>
                </div>
              </div>

              {/* Right: Result + metadata */}
              <div className="flex flex-col gap-3">
                {/* Metadata row */}
                <div className="grid grid-cols-3 gap-3">
                  <MetaBox label="CREATED" value={formatTimestamp(task.created_at)} />
                  <MetaBox
                    label={task.completed_at ? 'COMPLETED' : 'ELAPSED'}
                    value={task.completed_at ? formatTimestamp(task.completed_at) : (duration || '—')}
                  />
                  <MetaBox
                    label="DURATION"
                    value={duration || (task.status === 'working' ? 'running...' : '—')}
                    valueClass={task.status === 'working' ? 'text-blue-400 animate-pulse' : undefined}
                  />
                </div>

                {/* Success criteria */}
                {criteria && (
                  <div
                    className={`font-mono text-xs px-3 py-2 rounded border flex items-center justify-between ${
                      criteria.fails === 0
                        ? 'bg-emerald-400/10 border-emerald-400/25 text-emerald-400'
                        : 'bg-red-400/10 border-red-400/25 text-red-400'
                    }`}
                  >
                    <span>SUCCESS CRITERIA</span>
                    <span className="font-bold">
                      {criteria.passes}/{criteria.total} PASS
                      {criteria.fails > 0 && ` · ${criteria.fails} FAIL`}
                    </span>
                  </div>
                )}

                {/* Result */}
                <div className="flex-1">
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-mono text-xs text-slate-500 tracking-wider">RESULT</span>
                    {task.result && (
                      <span className="font-mono text-xs text-slate-700">
                        {task.result.length.toLocaleString()} chars
                      </span>
                    )}
                  </div>
                  <div className="bg-forge-card border border-forge-border rounded p-3 overflow-auto max-h-[32rem]">
                    {task.result ? (
                      <div className="prose prose-invert prose-sm max-w-none text-slate-300 [&_table]:w-full [&_table]:border-collapse [&_th]:border [&_th]:border-forge-border [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_th]:font-mono [&_th]:text-xs [&_th]:text-slate-400 [&_td]:border [&_td]:border-forge-border [&_td]:px-2 [&_td]:py-1 [&_td]:font-mono [&_td]:text-xs [&_td]:text-slate-300 [&_code]:bg-forge-surface [&_code]:px-1 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-xs [&_pre]:bg-forge-surface [&_pre]:p-2 [&_pre]:rounded [&_pre]:overflow-auto [&_h1]:text-base [&_h2]:text-sm [&_h3]:text-sm [&_h4]:text-xs [&_h1]:font-bold [&_h2]:font-bold [&_h3]:font-semibold [&_h4]:font-semibold [&_h1]:text-slate-100 [&_h2]:text-slate-200 [&_h3]:text-slate-200 [&_h4]:text-slate-300 [&_ul]:list-disc [&_ul]:pl-4 [&_ol]:list-decimal [&_ol]:pl-4 [&_li]:text-xs [&_li]:text-slate-300 [&_hr]:border-forge-border [&_strong]:text-slate-200 [&_em]:text-slate-300">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {task.result}
                        </ReactMarkdown>
                      </div>
                    ) : task.status === 'working' ? (
                      <div className="flex items-center gap-2 text-blue-400 font-mono text-xs">
                        <div className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
                        Executing...
                      </div>
                    ) : task.status === 'waiting_for_pm' ? (
                      <div className="flex items-center gap-2 text-purple-400 font-mono text-xs">
                        <div className="w-1.5 h-1.5 rounded-full bg-purple-400 animate-pulse" />
                        Waiting for PM response...
                      </div>
                    ) : task.status === 'pending' ? (
                      <span className="font-mono text-xs text-slate-600">Waiting to execute...</span>
                    ) : (
                      <span className="font-mono text-xs text-slate-600">(no result)</span>
                    )}
                  </div>
                </div>

                {/* QA Review: Approve / Reject buttons */}
                {task.status === 'qa_review' && (
                  <div className="flex flex-col gap-2">
                    {reviewError && (
                      <div className="font-mono text-xs text-red-400 bg-red-400/10 border border-red-400/25 rounded px-3 py-2">
                        {reviewError}
                      </div>
                    )}
                    {showRejectInput && (
                      <div className="flex flex-col gap-1.5">
                        <label className="font-mono text-xs text-slate-500 tracking-wider">REJECTION REASON (optional)</label>
                        <input
                          type="text"
                          value={rejectReason}
                          onChange={(e) => setRejectReason(e.target.value)}
                          placeholder="Why is this being rejected?"
                          className="bg-forge-card border border-red-400/40 rounded px-3 py-1.5 font-mono text-xs text-slate-300 placeholder-slate-600 focus:outline-none focus:border-red-400/70"
                        />
                      </div>
                    )}
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleReview('approved')}
                        disabled={reviewLoading}
                        className="flex-1 font-mono text-xs font-bold tracking-wider px-4 py-2 rounded border bg-emerald-400/15 border-emerald-400/40 text-emerald-400 hover:bg-emerald-400/25 hover:border-emerald-400/70 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {reviewLoading ? 'PROCESSING...' : 'APPROVE'}
                      </button>
                      <button
                        onClick={() => handleReview('rejected')}
                        disabled={reviewLoading}
                        className="flex-1 font-mono text-xs font-bold tracking-wider px-4 py-2 rounded border bg-red-400/15 border-red-400/40 text-red-400 hover:bg-red-400/25 hover:border-red-400/70 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {showRejectInput ? (reviewLoading ? 'PROCESSING...' : 'CONFIRM REJECT') : 'REJECT'}
                      </button>
                      {showRejectInput && (
                        <button
                          onClick={() => { setShowRejectInput(false); setRejectReason('') }}
                          className="font-mono text-xs text-slate-600 hover:text-slate-400 transition-colors px-3 py-2 rounded border border-forge-border hover:bg-forge-card"
                        >
                          cancel
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Sub-tasks section */}
            {subtasks.length > 0 && (
              <div className="mt-4 pt-4 border-t border-forge-border">
                <div className="flex items-center justify-between mb-3">
                  <span className="font-mono text-xs text-slate-500 tracking-wider">SUB-TASKS</span>
                  <span className="font-mono text-xs text-slate-700">{subtasks.length} task{subtasks.length !== 1 ? 's' : ''}</span>
                </div>
                <div className="flex flex-col gap-2">
                  {subtasks.map((st) => {
                    const stCfg = getStatusConfig(st.status === 'running' ? 'working' : st.status)
                    const stDuration = getSubtaskDuration(st)
                    const isOutputExpanded = expandedSubtasks[st.subtask_id]
                    return (
                      <div key={st.subtask_id} className={`rounded border p-2.5 ${stCfg.bgColor} ${stCfg.borderColor}`}>
                        <div className="flex items-center gap-2 mb-1">
                          <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${stCfg.dotColor} ${stCfg.pulse ? 'animate-pulse' : ''}`} />
                          <span className={`font-mono text-xs font-semibold ${stCfg.textColor}`}>{st.status.toUpperCase()}</span>
                          {st.agent && (
                            <span className="font-mono text-xs text-slate-500 bg-forge-card px-1.5 py-0.5 rounded border border-forge-border">
                              {st.agent}
                            </span>
                          )}
                          {stDuration && (
                            <span className={`font-mono text-xs ${st.status === 'running' ? 'text-blue-400 animate-pulse' : 'text-slate-600'}`}>
                              {stDuration}
                            </span>
                          )}
                          <span className="font-mono text-xs text-slate-600 ml-auto">
                            {st.started_at ? formatTimestamp(st.started_at) : '—'}
                          </span>
                        </div>
                        <p className="text-xs text-slate-300 font-medium pl-3.5">{st.name}</p>
                        {st.output && (
                          <div className="mt-1.5 pl-3.5">
                            <button
                              onClick={() => toggleSubtask(st.subtask_id)}
                              className="font-mono text-xs text-slate-600 hover:text-slate-400 transition-colors mb-1"
                            >
                              {isOutputExpanded ? '▼ hide output' : '▶ show output'}
                            </button>
                            {isOutputExpanded && (
                              <pre className="font-mono text-xs text-slate-500 whitespace-pre-wrap leading-relaxed bg-forge-card border border-forge-border rounded p-2 max-h-48 overflow-auto">
                                {st.output}
                              </pre>
                            )}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {/* PM Messages section */}
            {messages.length > 0 && (
              <div className="mt-4 pt-4 border-t border-forge-border">
                <div className="flex items-center justify-between mb-3">
                  <span className="font-mono text-xs text-slate-500 tracking-wider">PM MESSAGES</span>
                  <span className="font-mono text-xs text-slate-700">{messages.length} message{messages.length !== 1 ? 's' : ''}</span>
                </div>
                <div className="flex flex-col gap-2">
                  {messages.map((msg) => {
                    const isBlocking = msg.requires_response
                    const isAnswered = msg.responded
                    const dotColor = isBlocking && !isAnswered ? 'bg-purple-400 animate-pulse' : isAnswered ? 'bg-emerald-400' : 'bg-slate-500'
                    const labelColor = isBlocking && !isAnswered ? 'text-purple-400' : isAnswered ? 'text-emerald-400' : 'text-slate-500'
                    return (
                      <div key={msg.message_id} className="rounded border border-forge-border bg-forge-card p-2.5">
                        <div className="flex items-center gap-2 mb-1.5">
                          <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotColor}`} />
                          <span className={`font-mono text-xs font-semibold ${labelColor}`}>
                            {msg.type.replace(/_/g, ' ').toUpperCase()}
                          </span>
                          {isAnswered && (
                            <span className="font-mono text-xs text-emerald-400 bg-emerald-400/10 border border-emerald-400/25 px-1.5 py-0.5 rounded">
                              ANSWERED
                            </span>
                          )}
                          <span className="font-mono text-xs text-slate-600 ml-auto">
                            {formatTimestamp(msg.received_at)}
                          </span>
                        </div>
                        <p className="text-xs text-slate-300 pl-3.5 leading-relaxed">{msg.content}</p>
                        {msg.options && msg.options.length > 0 && (
                          <div className="flex flex-wrap gap-1.5 mt-1.5 pl-3.5">
                            {msg.options.map((opt, i) => (
                              <span key={i} className="font-mono text-xs text-slate-400 bg-forge-surface border border-forge-border px-2 py-0.5 rounded">
                                {opt}
                              </span>
                            ))}
                          </div>
                        )}
                        {msg.pm_response && (
                          <div className="mt-1.5 pl-3.5 pt-1.5 border-t border-forge-border/50">
                            <span className="font-mono text-xs text-slate-600">PM: </span>
                            <span className="font-mono text-xs text-emerald-400">{msg.pm_response}</span>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function MetaBox({ label, value, valueClass }) {
  return (
    <div className="bg-forge-card border border-forge-border rounded p-2">
      <div className="font-mono text-xs text-slate-600 mb-1 tracking-wider">{label}</div>
      <div className={`font-mono text-xs ${valueClass || 'text-slate-300'} break-all`}>{value}</div>
    </div>
  )
}
