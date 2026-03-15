import { useState } from 'react'
import { useHistory, useBriefByDate } from '../hooks/useContent.js'
import PipelineStatus from './PipelineStatus.jsx'
import AngleCard from './AngleCard.jsx'

function formatDate(dateStr) {
  try {
    const d = new Date(dateStr + 'T12:00:00')
    return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
  } catch {
    return dateStr
  }
}

function HistoryRow({ entry, isSelected, onSelect }) {
  const { date, angles_count, approved, rejected, posted, pipeline_run } = entry

  return (
    <div
      className={`p-3 rounded-lg border cursor-pointer transition-colors ${
        isSelected
          ? 'bg-forge-muted border-blue-700/50'
          : 'bg-forge-card border-forge-border hover:bg-forge-muted/30'
      }`}
      onClick={onSelect}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-sm font-medium text-slate-200">{formatDate(date)}</span>
          <span className="text-xs text-slate-500">{date}</span>
        </div>
        <PipelineStatus run={pipeline_run} date={date} />
      </div>
      <div className="flex items-center gap-4 mt-2 text-xs text-slate-500">
        <span>{angles_count} angle{angles_count !== 1 ? 's' : ''}</span>
        {approved > 0 && <span className="text-green-500">{approved} approved</span>}
        {rejected > 0 && <span className="text-red-400">{rejected} rejected</span>}
        {posted > 0 && <span className="text-blue-400">{posted} posted</span>}
        {approved === 0 && rejected === 0 && posted === 0 && angles_count > 0 && (
          <span className="text-slate-600">no actions taken</span>
        )}
      </div>
    </div>
  )
}

function ExpandedBrief({ date, onCollapse }) {
  const { data, loading, error } = useBriefByDate(date)

  if (loading) {
    return (
      <div className="py-8 text-center text-slate-500 text-sm">Loading {date}...</div>
    )
  }
  if (error) {
    return (
      <div className="py-8 text-center text-red-400 text-sm">Error: {error}</div>
    )
  }
  if (!data) return null

  const angles = data.angles || []

  return (
    <div className="mt-4 border-t border-forge-border pt-4">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-200">{date}</h2>
          {data.summary && (
            <p className="text-xs text-slate-500 mt-1 leading-relaxed max-w-xl">{data.summary}</p>
          )}
        </div>
        <button
          onClick={onCollapse}
          className="text-xs text-slate-500 hover:text-slate-300 px-2 py-1 rounded hover:bg-forge-muted transition-colors"
        >
          Collapse
        </button>
      </div>
      {angles.length === 0 ? (
        <p className="text-sm text-slate-500 italic">No angles found for this date.</p>
      ) : (
        <div className="space-y-4">
          {angles.map((angle) => (
            <AngleCard key={angle.index} angle={angle} date={date} onUpdate={() => {}} />
          ))}
        </div>
      )}
    </div>
  )
}

export default function HistoryView() {
  const { data, loading, error, refresh } = useHistory()
  const [selectedDate, setSelectedDate] = useState(null)

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-slate-500 text-sm">
        Loading history...
      </div>
    )
  }

  if (error) {
    return (
      <div className="py-12 text-center">
        <p className="text-red-400 text-sm mb-3">Failed to load: {error}</p>
        <button onClick={refresh} className="px-4 py-2 rounded bg-forge-muted hover:bg-slate-600 text-slate-300 text-sm">
          Retry
        </button>
      </div>
    )
  }

  const history = data?.history || []

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-lg font-semibold text-slate-100">History</h1>
        <button
          onClick={refresh}
          className="text-xs text-slate-500 hover:text-slate-300 px-2 py-1 rounded hover:bg-forge-muted transition-colors"
        >
          Refresh
        </button>
      </div>

      {history.length === 0 ? (
        <div className="py-16 text-center text-slate-500 text-sm">
          No history yet. Content will appear here after the daily pipeline runs.
        </div>
      ) : (
        <div className="space-y-2">
          {history.map((entry) => {
            const isSelected = selectedDate === entry.date
            return (
              <div key={entry.date}>
                <HistoryRow
                  entry={entry}
                  isSelected={isSelected}
                  onSelect={() => setSelectedDate(isSelected ? null : entry.date)}
                />
                {isSelected && (
                  <div className="ml-4 pl-4 border-l border-forge-border">
                    <ExpandedBrief date={entry.date} onCollapse={() => setSelectedDate(null)} />
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
