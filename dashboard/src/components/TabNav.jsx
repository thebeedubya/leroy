/**
 * TabNav -- horizontal tab bar for the Workforce Hub dashboard.
 * Uses hash-based routing: #tasks, #agents, #decisions, #activity, #specs, #system.
 */

export function TabNav({ activeTab, onTabChange, badges = {} }) {
  const TABS = [
    { id: 'tasks', label: 'Tasks' },
    { id: 'agents', label: 'Agents' },
    { id: 'decisions', label: 'Decisions' },
    { id: 'activity', label: 'Activity' },
    { id: 'specs', label: 'Specs' },
    { id: 'system', label: 'System' },
  ]

  return (
    <nav className="border-b border-forge-border px-6 flex items-center gap-1 bg-forge-surface flex-shrink-0">
      {TABS.map((tab) => {
        const isActive = activeTab === tab.id
        const badge = badges[tab.id]
        return (
          <button
            key={tab.id}
            onClick={() => onTabChange(tab.id)}
            className={[
              'relative px-4 py-3 font-mono text-xs font-medium transition-colors',
              'border-b-2 -mb-px focus:outline-none',
              isActive
                ? 'border-blue-400 text-blue-400'
                : 'border-transparent text-slate-500 hover:text-slate-300 hover:border-forge-muted',
            ].join(' ')}
          >
            {tab.label}
            {badge != null && badge > 0 && (
              <span className="ml-1.5 inline-flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full bg-red-500 text-white text-[10px] font-bold">
                {badge > 99 ? '99+' : badge}
              </span>
            )}
          </button>
        )
      })}
    </nav>
  )
}
