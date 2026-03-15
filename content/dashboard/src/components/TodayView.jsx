import { useTodayContent } from '../hooks/useContent.js'
import PipelineStatus from './PipelineStatus.jsx'
import AngleCard from './AngleCard.jsx'

function formatDate(dateStr) {
  try {
    const d = new Date(dateStr + 'T12:00:00')
    return d.toLocaleDateString([], { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })
  } catch {
    return dateStr
  }
}

export default function TodayView() {
  const { data, loading, error, refresh } = useTodayContent()

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-slate-500 text-sm">
        Loading today's content...
      </div>
    )
  }

  if (error) {
    return (
      <div className="py-12 text-center">
        <p className="text-red-400 text-sm mb-3">Failed to load: {error}</p>
        <button
          onClick={refresh}
          className="px-4 py-2 rounded bg-forge-muted hover:bg-slate-600 text-slate-300 text-sm transition-colors"
        >
          Retry
        </button>
      </div>
    )
  }

  const today = data?.date || new Date().toISOString().slice(0, 10)
  const angles = data?.angles || []
  const hasContent = angles.length > 0

  return (
    <div>
      {/* Date + pipeline status header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">{formatDate(today)}</h1>
          {data?.summary && (
            <p className="text-xs text-slate-500 mt-1 max-w-xl leading-relaxed line-clamp-2">
              {data.summary}
            </p>
          )}
        </div>
        <div className="flex items-center gap-3">
          <PipelineStatus run={data?.pipeline_run} date={today} />
          <button
            onClick={refresh}
            className="text-xs text-slate-500 hover:text-slate-300 px-2 py-1 rounded hover:bg-forge-muted transition-colors"
            title="Refresh"
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Angles */}
      {!hasContent ? (
        <div className="py-16 text-center">
          <p className="text-slate-500 text-sm mb-2">No content for today yet.</p>
          <p className="text-slate-600 text-xs">The content agent runs daily at noon. Check back later.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {/* Summary stats */}
          <div className="flex items-center gap-4 text-xs text-slate-500 mb-2">
            <span>{angles.length} angle{angles.length !== 1 ? 's' : ''}</span>
            <span>{angles.filter((a) => a.status === 'approved').length} approved</span>
            <span>{angles.filter((a) => a.status === 'rejected').length} rejected</span>
            <span>{angles.filter((a) => a.status === 'posted').length} posted</span>
          </div>
          {angles.map((angle) => (
            <AngleCard
              key={angle.index}
              angle={angle}
              date={today}
              onUpdate={refresh}
            />
          ))}
        </div>
      )}
    </div>
  )
}
