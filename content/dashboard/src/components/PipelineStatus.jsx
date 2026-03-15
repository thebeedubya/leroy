import { useState, useEffect } from 'react'

function formatTime(isoStr) {
  if (!isoStr) return null
  try {
    const d = new Date(isoStr)
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return null
  }
}

export default function PipelineStatus({ run, date }) {
  if (!run) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs bg-slate-800 text-slate-400 border border-forge-border">
        <span className="w-2 h-2 rounded-full bg-slate-600"></span>
        Pending
      </span>
    )
  }

  const status = run.status
  const ts = formatTime(run.timestamp)

  if (status === 'success') {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs bg-green-900/40 text-green-400 border border-green-800/50">
        <span className="w-2 h-2 rounded-full bg-green-500"></span>
        {ts ? `Ran at ${ts}` : 'Success'}
      </span>
    )
  }

  if (status === 'failed') {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs bg-red-900/40 text-red-400 border border-red-800/50">
        <span className="w-2 h-2 rounded-full bg-red-500"></span>
        Failed{run.reason ? `: ${run.reason}` : ''}
      </span>
    )
  }

  if (status === 'skipped') {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs bg-yellow-900/40 text-yellow-400 border border-yellow-800/50">
        <span className="w-2 h-2 rounded-full bg-yellow-500"></span>
        Skipped{run.reason ? ` (${run.reason})` : ''}
      </span>
    )
  }

  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs bg-slate-800 text-slate-400 border border-forge-border">
      <span className="w-2 h-2 rounded-full bg-slate-500"></span>
      {status}
    </span>
  )
}
