import { TaskColumn } from './TaskColumn'
import { STATUS_CONFIG, sortTasks } from '../utils'

const COLUMNS = [
  { status: 'pending', label: 'PENDING' },
  { status: 'working', label: 'EXECUTING' },
  { status: 'waiting_for_pm', label: 'WAITING FOR PM' },
  { status: 'qa_review', label: 'QA REVIEW' },
  { status: 'completed', label: 'COMPLETED' },
  { status: 'failed', label: 'FAILED' },
]

export function TaskBoard({ tasks, selectedTaskId, onSelectTask }) {
  // Group tasks by status (put cancelled into failed column)
  const grouped = {
    pending: [],
    working: [],
    waiting_for_pm: [],
    qa_review: [],
    completed: [],
    failed: [],
  }

  for (const task of tasks) {
    const bucket = task.status === 'cancelled' ? 'failed' :
                   (grouped[task.status] !== undefined ? task.status : 'failed')
    grouped[bucket].push(task)
  }

  // Sort each column
  for (const key of Object.keys(grouped)) {
    grouped[key] = sortTasks(grouped[key])
  }

  return (
    <div className="grid grid-cols-6 gap-4 h-full">
      {COLUMNS.map(({ status, label }) => (
        <TaskColumn
          key={status}
          label={label}
          status={status}
          tasks={grouped[status] || []}
          selectedTaskId={selectedTaskId}
          onSelectTask={onSelectTask}
          statusConfig={STATUS_CONFIG[status]}
          maxVisible={status === 'completed' ? 10 : undefined}
        />
      ))}
    </div>
  )
}
