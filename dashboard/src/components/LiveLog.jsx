import { useState, useEffect, useRef, useCallback } from 'react'

// ---- Helpers ----------------------------------------------------------------

function getToolColor(toolName) {
  if (!toolName) return 'text-slate-400'
  const name = toolName.toLowerCase()
  if (name === 'bash') return 'text-amber-400'
  if (name === 'read' || name === 'grep' || name === 'glob') return 'text-blue-400'
  if (name === 'edit' || name === 'write') return 'text-emerald-400'
  if (name === 'agent') return 'text-purple-400'
  if (name === 'ssh') return 'text-red-400'
  return 'text-slate-400'
}

function summarizeInput(toolName, toolInput) {
  if (toolInput == null) return ''
  try {
    const input = typeof toolInput === 'string' ? JSON.parse(toolInput) : toolInput
    const name = (toolName || '').toLowerCase()
    let summary = ''
    if (name === 'bash') {
      summary = input.command || ''
    } else if (name === 'read') {
      summary = input.file_path || ''
    } else if (name === 'grep') {
      summary = `${input.pattern || ''} in ${input.path || ''}`
    } else if (name === 'glob') {
      summary = input.pattern || ''
    } else if (name === 'edit') {
      summary = input.file_path || ''
    } else if (name === 'write') {
      summary = input.file_path || ''
    } else if (name === 'agent') {
      summary = `[${input.subagent_type || ''}] ${input.description || ''}`
    } else if (name === 'ssh') {
      summary = input.command || ''
    } else {
      const entries = Object.entries(input)
      if (entries.length > 0) {
        summary = `${entries[0][0]}=${JSON.stringify(entries[0][1])}`
      }
    }
    return summary.slice(0, 120)
  } catch {
    return String(toolInput).slice(0, 120)
  }
}

function formatEventTime(timestamp) {
  if (!timestamp) return '        '
  try {
    const d = new Date(timestamp)
    const hh = String(d.getHours()).padStart(2, '0')
    const mm = String(d.getMinutes()).padStart(2, '0')
    const ss = String(d.getSeconds()).padStart(2, '0')
    return `${hh}:${mm}:${ss}`
  } catch {
    return '        '
  }
}

// ---- EventRow ---------------------------------------------------------------

function EventRow({ event }) {
  const toolName = event.tool_name || 'unknown'
  const color = getToolColor(toolName)
  const summary = summarizeInput(toolName, event.tool_input)
  const time = formatEventTime(event.timestamp)
  const label = `[${toolName}]`

  return (
    <div className="flex gap-2 hover:bg-white/5 px-3 py-0.5">
      <span className="text-slate-500 flex-shrink-0 select-none w-[8ch]">{time}</span>
      <span className={`${color} flex-shrink-0 w-[16ch] truncate`}>{label}</span>
      <span className="text-slate-300 truncate">{summary}</span>
    </div>
  )
}

// ---- LiveLog ----------------------------------------------------------------

/**
 * Props:
 *   taskId       (string)  -- task to stream / replay
 *   taskStatus   (string)  -- current status of the task
 *   defaultExpanded (bool) -- initial collapsed state
 */
export function LiveLog({ taskId, taskStatus, defaultExpanded }) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const [events, setEvents] = useState([])
  const [streamStatus, setStreamStatus] = useState('idle') // idle | connecting | connected | disconnected
  const [totalCount, setTotalCount] = useState(null)

  const scrollRef = useRef(null)
  const autoScrollRef = useRef(true)
  const esRef = useRef(null)
  const reconnectTimer = useRef(null)

  const isWorking = taskStatus === 'working'
  const isCompleted = taskStatus === 'completed' || taskStatus === 'failed' || taskStatus === 'qa_review'
  const isPending = taskStatus === 'pending'

  // ---- Auto-scroll logic ----------------------------------------------------

  const scrollToBottom = useCallback(() => {
    if (scrollRef.current && autoScrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [])

  const handleScroll = useCallback(() => {
    if (!scrollRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current
    autoScrollRef.current = scrollHeight - scrollTop - clientHeight < 20
  }, [])

  useEffect(() => {
    if (isWorking && expanded) {
      scrollToBottom()
    }
  }, [events, isWorking, expanded, scrollToBottom])

  // ---- Historical fetch for completed / failed ------------------------------

  useEffect(() => {
    if (!taskId || !isCompleted || !expanded) return
    let cancelled = false

    fetch(`/api/hooks/events?task_id=${taskId}&limit=500`)
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return
        const evts = Array.isArray(data) ? data : (data.events || [])
        setEvents(evts)
        setTotalCount(evts.length)
      })
      .catch(() => {
        if (cancelled) return
        setEvents([])
        setTotalCount(0)
      })

    return () => { cancelled = true }
  }, [taskId, isCompleted, expanded])

  // ---- SSE connection for working tasks -------------------------------------

  const connectSSE = useCallback(() => {
    if (!taskId) return
    if (esRef.current) {
      esRef.current.close()
      esRef.current = null
    }
    setStreamStatus('connecting')

    const es = new EventSource(`/api/hooks/events/stream?task_id=${taskId}`)
    esRef.current = es

    es.onopen = () => {
      setStreamStatus('connected')
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = null
      }
    }

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'heartbeat') return
        // SSE broadcasts wrap the event as {"type": "hook_event", "event": {...}}
        const evt = data.event || data
        setEvents((prev) => [...prev, evt])
      } catch {
        // ignore parse errors
      }
    }

    es.onerror = () => {
      setStreamStatus('disconnected')
      es.close()
      esRef.current = null
      reconnectTimer.current = setTimeout(connectSSE, 3000)
    }
  }, [taskId])

  // Connect SSE when panel is expanded and task is working
  useEffect(() => {
    if (!expanded || !isWorking) return

    connectSSE()

    return () => {
      if (esRef.current) {
        esRef.current.close()
        esRef.current = null
      }
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = null
      }
    }
  }, [expanded, isWorking, connectSSE])

  // Full cleanup on unmount or taskId change
  useEffect(() => {
    return () => {
      if (esRef.current) {
        esRef.current.close()
        esRef.current = null
      }
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = null
      }
    }
  }, [taskId])

  // Reset state when taskId changes
  useEffect(() => {
    setEvents([])
    setTotalCount(null)
    setStreamStatus('idle')
    autoScrollRef.current = true
    setExpanded(defaultExpanded)
  }, [taskId, defaultExpanded])

  // ---- Derived header label -------------------------------------------------

  const headerLabel =
    isCompleted && totalCount !== null
      ? `LIVE LOG (${totalCount} events)`
      : 'LIVE LOG'

  // ---- Streaming indicator dot ----------------------------------------------

  const dotColor =
    streamStatus === 'connected'
      ? 'bg-emerald-400 animate-pulse'
      : streamStatus === 'disconnected'
        ? 'bg-red-400'
        : 'bg-slate-500 animate-pulse'

  // ---- Empty state message --------------------------------------------------

  const emptyMsg = isPending
    ? 'No events yet.'
    : isWorking
      ? 'Waiting for events...'
      : 'No events recorded.'

  // ---- Render ---------------------------------------------------------------

  return (
    <div className="mt-4 pt-4 border-t border-forge-border">
      {/* Header toggle */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center justify-between mb-2 w-full text-left hover:opacity-80 transition-opacity"
      >
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs text-slate-500 tracking-wider">
            {headerLabel} {expanded ? '▼' : '▶'}
          </span>
          {isWorking && expanded && (
            <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotColor}`} />
          )}
          {isWorking && expanded && streamStatus === 'disconnected' && (
            <span className="font-mono text-xs text-red-400">Stream disconnected</span>
          )}
        </div>
        {events.length > 0 && (
          <span className="font-mono text-xs text-slate-700">
            {events.length} event{events.length !== 1 ? 's' : ''}
          </span>
        )}
      </button>

      {/* Log body */}
      {expanded && (
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="bg-[#0a0e17] border border-[#1e2535] rounded-lg overflow-auto max-h-96"
          style={{ scrollbarWidth: 'thin', scrollbarColor: '#1e2535 transparent' }}
        >
          {events.length === 0 ? (
            <div className="px-3 py-4 font-mono text-xs text-slate-600">
              {emptyMsg}
            </div>
          ) : (
            <div className="py-1 font-mono text-xs leading-5">
              {events.map((evt, i) => (
                <EventRow key={evt.id || `${evt.timestamp}-${i}`} event={evt} />
              ))}
              {isWorking && streamStatus === 'disconnected' && (
                <div className="px-3 py-1 font-mono text-xs text-red-400">
                  Stream disconnected. Reconnecting in 3s...
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
