import { useState, useEffect, useRef, useCallback } from 'react'

/**
 * Subscribe to SSE stream at /api/tasks/stream.
 * Falls back to polling if SSE fails or is unsupported.
 * Returns { connected, connectionType } where connectionType is 'sse' | 'polling' | 'connecting'.
 */
export function useSSE({ onSnapshot, onTaskUpdate }) {
  const [connected, setConnected] = useState(false)
  const [connectionType, setConnectionType] = useState('connecting')
  const esRef = useRef(null)
  const reconnectTimer = useRef(null)

  const connect = useCallback(() => {
    if (esRef.current) {
      esRef.current.close()
    }

    const es = new EventSource('/api/tasks/stream')
    esRef.current = es

    es.onopen = () => {
      setConnected(true)
      setConnectionType('sse')
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = null
      }
    }

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'snapshot' && onSnapshot) {
          onSnapshot(data.tasks)
        } else if (data.type === 'task_update' && onTaskUpdate) {
          onTaskUpdate(data.task)
        }
        // heartbeat: ignore
      } catch (err) {
        // parse error, ignore
      }
    }

    es.onerror = () => {
      setConnected(false)
      setConnectionType('polling')
      es.close()
      esRef.current = null
      // Reconnect after 5 seconds
      reconnectTimer.current = setTimeout(connect, 5000)
    }
  }, [onSnapshot, onTaskUpdate])

  useEffect(() => {
    connect()
    return () => {
      if (esRef.current) {
        esRef.current.close()
      }
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
      }
    }
  }, [connect])

  return { connected, connectionType }
}
