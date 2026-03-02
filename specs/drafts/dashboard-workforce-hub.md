# FORGE Dashboard -- Workforce Hub (Agents, Decisions, Activity, Specs, Brain, Infra)

## Objective

Expand the Leroy dashboard from a task-centric view into a full workforce management hub. Humans managing an agent ecosystem need visibility into WHO is running, WHAT is waiting on them, and WHAT is happening across the system, not just individual task status. This spec adds six new views behind a tabbed navigation, keeping the existing Tasks kanban as the default tab.

## Why

Brad currently has zero visibility into agent state without opening terminals. As the agent count grows (PM, Ops, Leroy, Content Agent, future agents), the human needs a single pane of glass. The decision queue is especially critical: it surfaces the moments where human judgment is the bottleneck.

## Architecture

### Frontend Only (Phase 1)

All six new tabs are frontend components that consume existing or new REST endpoints from the Leroy A2A server (port 9800). No new backend services. The server already has SSE for live updates.

### New Server Endpoints Required

The dashboard needs data. These endpoints must be added to `server/server.py`:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/agents` | GET | Returns registered agent roster with status, last heartbeat, current task |
| `/agents/{name}/heartbeat` | POST | Agents call this periodically to report status. Body: `{status, current_task, metadata}` |
| `/activity` | GET | Returns recent activity events across all agents. Query params: `?limit=50&since=<iso8601>` |
| `/activity/stream` | GET | SSE stream of activity events (same pattern as `/tasks/stream`) |
| `/specs` | GET | Returns specs with pipeline stage (draft/sent/building/qa/done/failed). Can derive from task metadata. |
| `/brain/health` | GET | Proxies to forge-brain health endpoint (Kush:8301/health). Adds chunk count from a lightweight query. |
| `/infra/status` | GET | Returns infrastructure status. Initially hardcoded topology with health-check pings to Kush, APEX. Sentinel integration later. |

### Agent Registration

Agents register themselves via the heartbeat endpoint. The server maintains an in-memory registry (persisted to SQLite for restart survival). Agent record:

```json
{
  "name": "content-agent",
  "display_name": "Content Agent",
  "type": "scheduled",
  "launcher": "./content.sh",
  "status": "idle|running|error|unreachable",
  "current_task": null,
  "last_heartbeat": "2026-03-01T12:00:00Z",
  "last_activity": "2026-03-01T06:15:00Z",
  "metadata": {
    "launch_method": "launchd",
    "schedule": "daily 6AM CST"
  }
}
```

Agents that miss 3 consecutive heartbeat windows (configurable, default 60s interval) are marked "unreachable."

For Phase 1, seed the registry with known agents (PM, Ops, Leroy, Content Agent) via a config file or startup registration. Heartbeat integration with actual agents comes in Phase 2.

### Activity Event Model

Every significant action produces an activity event, stored in SQLite (rolling 7-day window):

```json
{
  "id": "evt-uuid",
  "timestamp": "2026-03-01T06:02:00Z",
  "agent": "content-agent",
  "type": "query|task_start|task_complete|pr_opened|error|heartbeat|decision_requested|brain_persist",
  "summary": "Queried Aianna for daily content angles",
  "detail": null,
  "task_id": null,
  "severity": "info|warn|error"
}
```

For Phase 1: automatically emit activity events from existing task lifecycle hooks (task created, task started, task completed, task failed, PM message received, PM message responded). Future: agents emit their own events via a POST endpoint.

## Tab Structure

The dashboard gets a horizontal tab bar below the header. Tabs:

1. **Tasks** (default, existing kanban -- no changes)
2. **Agents** (new)
3. **Decisions** (new)
4. **Activity** (new)
5. **Specs** (new)
6. **System** (new -- combines Brain Health + Infrastructure)

Use the same dark theme (forge-bg, forge-card, forge-border, forge-surface). Same font stack (JetBrains Mono). Same component patterns (status dots, badges, cards).

## Tab 1: Tasks (Existing)

No changes. The current kanban board with drill-down. Remains the default landing tab.

## Tab 2: Agents

### Layout
Grid of agent cards (2-3 per row depending on viewport). Each card shows:

- Agent name + type badge (scheduled / daemon / interactive / on-demand)
- Status indicator (green dot = running, gray = idle, red = error, yellow pulsing = unreachable)
- Current task (if any) with link to Tasks tab
- Last heartbeat (relative time: "2m ago", "3h ago")
- Last activity summary (one line)
- Launch method (launchd / manual / Leroy server)

### Interactions
- Click card to expand inline detail: full metadata, recent activity for that agent, error log tail
- No edit/control actions in Phase 1 (agents are launched externally)

### Data Source
`GET /agents` polled every 15s, or SSE if we extend the event stream.

## Tab 3: Decisions

### Layout
Priority-sorted list of items awaiting human input. Three sections:

**Needs Your Response** (top, prominent)
- Leroy questions/blockers/decision gates (from `/pm/messages/pending`)
- Each shows: agent name, message type badge (question/blocker/decision_gate), content preview, time waiting, urgency indicator (>10min = yellow, >30min = red)

**PRs Awaiting Review** (middle)
- Content Agent PRs on dbradwood.com
- Any other PRs from agent branches
- Shows: repo, PR title, created time, files changed count

**Recently Decided** (bottom, collapsed by default)
- Last 10 resolved decisions with outcome summary

### Interactions
- Click a pending decision to see full context + response options
- "Respond" button opens inline text input (sends via `/pm/messages/{id}/respond`)
- PR items link out to GitHub

### Data Source
`GET /pm/messages/pending` for Leroy decisions. GitHub API (via server proxy) for PRs, or initially just a link to GitHub.

## Tab 4: Activity

### Layout
Reverse-chronological feed (newest on top). Each entry is a single line:

```
[timestamp] [agent-badge] [event-type-icon] Summary text
```

Example:
```
06:15  CONTENT  PR opened: Daily Content 2026-02-28 (#47)
06:12  CONTENT  Generated 2 post-worthy angles (scores: 5, 4)
06:05  CONTENT  Queried Aianna: 48 chunks, 6 sessions found
06:02  CONTENT  Pipeline started (daily trigger)
05:58  LEROY   Task completed: Dashboard UX Fix (13886722)
05:45  PM      Spec sent: Content Agent Pipeline (31fe79e5)
```

Color-coded by agent. Filterable by agent name and event type. Auto-scrolls when new events arrive (SSE).

### Interactions
- Filter chips at top: All | PM | Leroy | Content | Ops (toggle)
- Event type filter: All | Tasks | Decisions | Errors
- Click any event to jump to related detail (task, decision, etc.)
- Pause auto-scroll button

### Data Source
`GET /activity?limit=100` for initial load. `GET /activity/stream` SSE for live updates.

## Tab 5: Specs

### Layout
Horizontal pipeline view (like a simplified kanban, but for spec lifecycle):

**Columns:** Draft -> Sent -> Building -> QA -> Done | Failed

Each spec card shows:
- Spec title (first H1 or subject)
- Task ID (short)
- Time in current stage
- QA pass rate (if in Done/Failed)

### Interactions
- Click card to see full spec text + result + retrospective
- Filter: date range, status

### Data Source
`GET /specs` derives from existing task data. Specs with "QA" in the title are QA tasks, others are build tasks. Map task status to pipeline stage:
- pending = Sent
- working = Building
- completed + has QA follow-up = QA
- completed + no QA or QA completed = Done
- failed/cancelled = Failed

Also reads from `~/Projects/leroy/specs/` directory for draft specs not yet sent.

## Tab 6: System

### Layout
Two-panel view:

**Left panel: Brain Health**
- Connection status to forge-brain (Kush:8300)
- Qdrant collection stats (chunk count, collection names)
- Last successful persist (timestamp + source agent)
- Persist queue depth (from Leroy's persist_manager)
- Circuit breaker status (open/closed/half-open)

**Right panel: Infrastructure**
- Card per machine: Kush, Haze, APEX
- Each shows: hostname, IP, role, status (up/down/degraded), services running
- Service-level detail: Qdrant (port 6333), forge-brain (8300), Sentinel (8200), Leroy (9800), Dashboard (5173)
- Last health check timestamp

### Data Source
`GET /brain/health` proxies to Kush. `GET /infra/status` pings known endpoints. Both polled every 30s.

## Navigation

Add a tab bar component between the Header and the main content area. Tabs are defined as:

```jsx
const TABS = [
  { id: 'tasks', label: 'Tasks', badge: workingCount },
  { id: 'agents', label: 'Agents', badge: null },
  { id: 'decisions', label: 'Decisions', badge: pendingDecisionCount },
  { id: 'activity', label: 'Activity', badge: null },
  { id: 'specs', label: 'Specs', badge: null },
  { id: 'system', label: 'System', badge: null },
]
```

The Decisions tab gets a red badge count when items are pending (same pattern as the existing QA review badge).

URL hash routing: `#tasks`, `#agents`, `#decisions`, `#activity`, `#specs`, `#system`. No React Router dependency, just hash state.

## Success Criteria

1. Tab navigation works across all 6 tabs. Tasks tab is default. URL hash persists selected tab on refresh.
2. Agents tab displays at least 4 agent cards (PM, Ops, Leroy, Content Agent) with seeded data.
3. Agents tab shows status, last heartbeat, current task (if any), launch method.
4. Decisions tab displays pending PM messages from `/pm/messages/pending`.
5. Decisions tab allows inline response to pending messages.
6. Activity tab shows reverse-chronological feed of task lifecycle events.
7. Activity tab supports filtering by agent name.
8. Activity tab auto-updates via SSE.
9. Specs tab shows pipeline columns (Draft/Sent/Building/QA/Done/Failed).
10. Specs tab populates from existing task data.
11. System tab shows brain health status (connection to forge-brain, circuit breaker state).
12. System tab shows infrastructure cards for Kush, Haze, APEX with up/down status.
13. All new endpoints return valid JSON and handle errors gracefully.
14. `/agents` endpoint returns agent roster with status fields.
15. `/agents/{name}/heartbeat` endpoint accepts POST and updates agent record.
16. `/activity` endpoint returns event list with limit/since params.
17. `/activity/stream` SSE endpoint emits activity events in real-time.
18. `/brain/health` proxies forge-brain health check.
19. `/infra/status` returns machine status with health pings.
20. Dark theme consistent with existing dashboard (forge-bg, forge-card, forge-border).
21. No new npm dependencies beyond what's already in package.json.
22. No changes to existing Tasks tab behavior.

## Constraints

- React 18 + Vite + Tailwind (existing stack). No new frameworks.
- No React Router. Use hash-based tab switching (useState + window.hashchange).
- No new npm dependencies. Use what's in package.json already.
- Server endpoints go in `server/server.py` (or new files imported by it).
- Agent heartbeat data stored in SQLite via `task_db.py` patterns.
- Activity events stored in SQLite with 7-day rolling retention.
- All timestamps in UTC ISO8601.
- SSE for activity stream follows the same pattern as existing `/tasks/stream`.

## Do Not Do

- Do not modify the existing Tasks tab or TaskCard component.
- Do not add authentication (not needed for localhost dashboard).
- Do not build agent start/stop controls (agents are managed externally).
- Do not integrate with GitHub API for PR data in Phase 1 (just show PM messages as decisions).
- Do not add WebSocket support (SSE is the established pattern).
- Do not add dark/light theme toggle.
- Do not add user accounts or login.

## Machine Details

- **Haze** (dev machine): dashboard at `~/Projects/leroy/dashboard/`, server at `~/Projects/leroy/server/`
- **Server**: Python Starlette, port 9800 (API) / 9801 (health). SQLite at `~/Projects/leroy/data/tasks.db`
- **Dashboard**: React 18, Vite, Tailwind 3.4, JetBrains Mono font. Dev server on port 5173.
- **Brain proxy target**: Kush at 192.168.1.100:8300 (forge-brain), health on 8301

## Budget

Complex. Multiple new server endpoints, 5 new frontend views, SQLite schema additions, SSE extension. Expect multi-hour execution with agent teams.

## Execution

Use agent teams. Decompose this into sub-tasks and delegate to specialist agents. Suggested decomposition:
1. Backend: new endpoints + SQLite schema for agents and activity events
2. Frontend: tab navigation + 5 new tab components
3. Integration: wire frontend to backend, test SSE activity stream

Do not execute sequentially as a single agent.
