import { useState, useEffect } from 'react'
import TodayView from './components/TodayView.jsx'
import HistoryView from './components/HistoryView.jsx'

function getHashTab() {
  const hash = window.location.hash.replace('#', '')
  return hash === 'history' ? 'history' : 'today'
}

export default function App() {
  const [activeTab, setActiveTab] = useState(getHashTab)

  useEffect(() => {
    const onHash = () => setActiveTab(getHashTab())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const handleTabChange = (tab) => {
    window.location.hash = tab
    setActiveTab(tab)
  }

  return (
    <div className="min-h-screen bg-forge-bg text-slate-200 font-mono">
      {/* Top bar */}
      <header className="sticky top-0 z-10 bg-forge-surface border-b border-forge-border px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-slate-400 text-xs tracking-widest uppercase">dbradwood.com</span>
          <span className="text-forge-border">|</span>
          <span className="text-slate-200 font-semibold tracking-wide">Content Dashboard</span>
        </div>
        <nav className="flex gap-1">
          {['today', 'history'].map((tab) => (
            <button
              key={tab}
              onClick={() => handleTabChange(tab)}
              className={`px-4 py-1.5 rounded text-sm font-medium transition-colors ${
                activeTab === tab
                  ? 'bg-blue-600 text-white'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-forge-muted'
              }`}
            >
              {tab === 'today' ? 'Today' : 'History'}
            </button>
          ))}
        </nav>
      </header>

      <main className="max-w-4xl mx-auto px-4 py-6">
        {activeTab === 'today' && <TodayView />}
        {activeTab === 'history' && <HistoryView />}
      </main>
    </div>
  )
}
