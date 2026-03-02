import { useState, useCallback, useEffect } from 'react'
import { Header } from './components/Header'
import { TabNav } from './components/TabNav'
import { TaskBoard } from './components/TaskBoard'
import { TaskDetail } from './components/TaskDetail'
import { AgentsTab } from './components/tabs/AgentsTab'
import { DecisionsTab } from './components/tabs/DecisionsTab'
import { ActivityTab } from './components/tabs/ActivityTab'
import { SpecsTab } from './components/tabs/SpecsTab'
import { SystemTab } from './components/tabs/SystemTab'
import { useTasks } from './hooks/useTasks'
import { useSSE } from './hooks/useSSE'
import { useDecisions } from './hooks/useDecisions'

// Hash-based tab routing helpers
function getHashTab() {
  const hash = window.location.hash.replace('#', '')
  const valid = ['tasks', 'agents', 'decisions', 'activity', 'specs', 'system']
  return valid.includes(hash) ? hash : 'tasks'
}

function setHashTab(tab) {
  window.location.hash = tab
}

export default function App() {
  const { tasks, setTasks, loading, error, lastUpdated, refreshCount, refresh } = useTasks()
  const [selectedTaskId, setSelectedTaskId] = useState(null)
  const [activeTab, setActiveTab] = useState(getHashTab)

  // Keep activeTab in sync with hash changes (back/forward navigation)
  useEffect(() => {
    const onHashChange = () => setActiveTab(getHashTab())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const handleTabChange = (tab) => {
    setHashTab(tab)
    setActiveTab(tab)
    // Close task detail when switching tabs
    if (tab !== 'tasks') setSelectedTaskId(null)
  }

  const qaReviewCount = tasks.filter((t) => t.status === 'qa_review').length

  // Decisions hook for badge count
  const { pending: pendingDecisions } = useDecisions()
  const pendingDecisionCount = pendingDecisions.length

  const handleQaReviewClick = () => {
    const firstQaTask = tasks.find((t) => t.status === 'qa_review')
    if (firstQaTask) {
      setSelectedTaskId(firstQaTask.task_id)
      handleTabChange('tasks')
    }
  }

  const handleSnapshot = useCallback((snapshotTasks) => {
    setTasks(snapshotTasks)
  }, [setTasks])

  const handleTaskUpdate = useCallback((updatedTask) => {
    setTasks((prev) => {
      const idx = prev.findIndex((t) => t.task_id === updatedTask.task_id)
      if (idx === -1) return [...prev, updatedTask]
      const next = [...prev]
      next[idx] = updatedTask
      return next
    })
  }, [setTasks])

  const { connected: sseConnected, connectionType } = useSSE({
    onSnapshot: handleSnapshot,
    onTaskUpdate: handleTaskUpdate,
  })

  const handleSelectTask = (taskId) => {
    setSelectedTaskId(taskId)
  }

  const tabBadges = {
    tasks: qaReviewCount > 0 ? qaReviewCount : null,
    decisions: pendingDecisionCount > 0 ? pendingDecisionCount : null,
  }

  return (
    <div className="h-screen bg-forge-bg flex flex-col overflow-hidden">
      <Header
        lastUpdated={lastUpdated}
        taskCount={tasks.length}
        refreshCount={refreshCount}
        sseConnected={sseConnected}
        connectionType={connectionType}
        qaReviewCount={qaReviewCount}
        onQaReviewClick={handleQaReviewClick}
      />

      <TabNav activeTab={activeTab} onTabChange={handleTabChange} badges={tabBadges} />

      <main className="flex-1 flex overflow-hidden">
        {/* Tasks tab */}
        {activeTab === 'tasks' && (
          <>
            <div className="flex-1 flex flex-col overflow-hidden px-6 py-4 min-w-0">
              {error && (
                <div className="mb-4 font-mono text-sm text-red-400 bg-red-400/10 border border-red-400/25 rounded px-4 py-2 flex items-center gap-2 flex-shrink-0">
                  <div className="w-1.5 h-1.5 rounded-full bg-red-400 flex-shrink-0" />
                  Cannot reach Leroy server: {error}
                </div>
              )}

              {loading && tasks.length === 0 ? (
                <div className="flex items-center justify-center h-64">
                  <div className="flex items-center gap-3 text-slate-500 font-mono text-sm">
                    <div className="w-2 h-2 rounded-full bg-slate-500 animate-pulse" />
                    Connecting to Leroy...
                  </div>
                </div>
              ) : (
                <div className="flex-1 overflow-hidden">
                  <TaskBoard
                    tasks={tasks}
                    selectedTaskId={selectedTaskId}
                    onSelectTask={handleSelectTask}
                  />
                </div>
              )}
            </div>

            {selectedTaskId && (
              <div className="w-[480px] flex-shrink-0 border-l border-forge-border overflow-y-auto">
                <TaskDetail
                  taskId={selectedTaskId}
                  onClose={() => setSelectedTaskId(null)}
                  tasks={tasks}
                />
              </div>
            )}
          </>
        )}

        {activeTab === 'agents' && <AgentsTab />}
        {activeTab === 'decisions' && <DecisionsTab />}
        {activeTab === 'activity' && <ActivityTab />}
        {activeTab === 'specs' && <SpecsTab />}
        {activeTab === 'system' && <SystemTab />}
      </main>

      <footer className="border-t border-forge-border px-6 py-2 flex items-center justify-between flex-shrink-0">
        <span className="font-mono text-xs text-slate-700">
          FORGE · Workforce Hub · {connectionType === 'sse' ? 'live' : 'polling every 5s'}
        </span>
        <span className="font-mono text-xs text-slate-700">
          api: 127.0.0.1:9800
        </span>
      </footer>
    </div>
  )
}
