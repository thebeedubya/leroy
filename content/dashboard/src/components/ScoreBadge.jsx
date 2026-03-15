export default function ScoreBadge({ score }) {
  if (score === null || score === undefined) {
    return (
      <span className="w-9 h-9 rounded-full flex items-center justify-center text-xs font-bold bg-slate-700 text-slate-400 border border-forge-border flex-shrink-0">
        --
      </span>
    )
  }

  const colorClass =
    score >= 7
      ? 'bg-green-900/60 text-green-400 border-green-700/50'
      : score >= 5
      ? 'bg-yellow-900/60 text-yellow-400 border-yellow-700/50'
      : 'bg-red-900/60 text-red-400 border-red-700/50'

  return (
    <span
      className={`w-9 h-9 rounded-full flex items-center justify-center text-xs font-bold border flex-shrink-0 ${colorClass}`}
      title={`Post-Worthiness Score: ${score}/10`}
    >
      {score}
    </span>
  )
}
