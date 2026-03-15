const STATUS_STYLES = {
  draft: 'bg-slate-700 text-slate-300 border-slate-600',
  approved: 'bg-green-900/60 text-green-400 border-green-700/50',
  rejected: 'bg-red-900/60 text-red-400 border-red-700/50',
  posted: 'bg-blue-900/60 text-blue-400 border-blue-700/50',
}

export default function StatusBadge({ status }) {
  const s = status || 'draft'
  const style = STATUS_STYLES[s] || STATUS_STYLES.draft
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium border uppercase tracking-wide ${style}`}>
      {s}
    </span>
  )
}
