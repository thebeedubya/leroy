---
spec_id: ideas-column-on-leroy-dashboard-kanban-board
task_id: 535910fc-7d9d-4cae-aff4-1e7dbee91e9c
date: 2026-03-02
status: sent
pass_rate: (pending)
retrospective: (pending)
tags: []
---

# Ideas Column on Leroy Dashboard Kanban Board

## Objective

Add an "Ideas" column to the Leroy dashboard kanban board as the first stage of the task pipeline. Ideas are lightweight placeholders -- a title and a short description. They sit in the backlog until promoted to Pending with a full spec. The pipeline becomes: Ideas → Pending → Working → Completed.

## Why

Brad tracks ideas, TODOs, and future work alongside engineering specs. Right now the board only shows Pending/Working/Completed, which means half-formed thoughts have nowhere to live. They get lost in brain persists or conversations. An Ideas column gives them a visible home on the one board Brad already watches.

## Scope

### Server Changes (`server/server.py` + `server/task_db.py`)

1. **New task status: `idea`**. Add "idea" as a valid task status alongside pending, working, completed, failed, cancelled. Ideas are created with status "idea" and promoted to "pending" when ready.

2. **New endpoint: `POST /ideas`**. Creates an idea task. Body:
   ```json
   {
     "title": "Short idea title",
     "description": "Optional one-liner description"
   }
   ```
   Returns the created task with status "idea" and a task ID. The spec field stores the title. The description goes in a new `description` field or in the spec body as a single line.

3. **New endpoint: `POST /ideas/{task_id}/promote`**. Changes an idea's status from "idea" to "pending". Optionally accepts a full spec body to replace the placeholder:
   ```json
   {
     "spec": "# Full spec markdown..."
   }
   ```
   If no spec body provided, just flips the status.

4. **Existing endpoints handle ideas naturally:**
   - `GET /tasks` already returns all tasks. Ideas show up with status "idea".
   - `DELETE /tasks/{id}` or cancel works on ideas (discard an idea).
   - The task list filter `?status=idea` returns only ideas.

5. **Ideas do NOT trigger auto-execution.** Only "pending" tasks get picked up by the executor. Ideas sit inert.

### Dashboard Changes (`dashboard/`)

1. **New kanban column: "Ideas"**. Leftmost column before Pending. Same card style but simplified:
   - Title only (no full spec preview)
   - Description as subtitle if present
   - Created date
   - No task ID badge needed (keep it clean)

2. **Column ordering: Ideas | Pending | Working | Completed** (left to right).

3. **Add Idea button.** A "+" button at the top of the Ideas column (or in the header area). Clicking opens an inline form:
   - Title input (required)
   - Description input (optional, single line)
   - "Add" button to submit
   - Escape or click away to cancel

4. **Promote action.** Each idea card gets a small arrow-right icon or "Promote" button. Clicking it calls `POST /ideas/{id}/promote` and moves the card to the Pending column.

5. **Discard action.** Each idea card gets a small X or trash icon. Clicking it cancels/deletes the idea.

6. **Idea count in tab.** If the tab bar shows badges, the Tasks tab badge should NOT count ideas. Ideas are not active work.

### API for PM and Headless PM

PM (me) can create ideas via the Leroy message bus or directly via the server API. When Brad says "I have an idea" in a PM session, I can call the ideas endpoint to park it on the board. Headless PM should NOT create ideas (tier 3, interactive only).

## Success Criteria

1. `POST /ideas` with title creates a task with status "idea" and returns a task ID.
2. `POST /ideas` with title and description stores both.
3. `GET /tasks` returns idea tasks with status "idea" alongside other tasks.
4. `GET /tasks?status=idea` returns only ideas.
5. `POST /ideas/{id}/promote` changes status from "idea" to "pending".
6. `POST /ideas/{id}/promote` with spec body replaces the task's spec content.
7. Ideas do NOT trigger auto-execution (no claude -p spawned for ideas).
8. Dashboard shows Ideas as the leftmost kanban column.
9. Column order is: Ideas, Pending, Working, Completed (left to right).
10. Ideas column cards show title, optional description, and created date.
11. "+" button in Ideas column opens inline form to add a new idea.
12. Promote button on idea cards moves them to Pending column.
13. Discard button on idea cards removes them from the board.
14. Existing Pending/Working/Completed columns are unchanged.
15. Dark theme consistent (forge-bg, forge-card, forge-border).

## Constraints

- Server changes in `server/server.py` and `server/task_db.py` only
- Dashboard changes in existing components (no new tab, just new column)
- SQLite schema update must be backwards-compatible (ideas are just tasks with a new status value, no new tables needed)
- No new npm dependencies
- No new Python dependencies
- Ideas must survive server restarts (same SQLite persistence as other tasks)
- All timestamps UTC ISO8601

## Do Not Do

- Do not add drag-and-drop between columns (nice to have, not v1)
- Do not add tags, labels, or categories to ideas (keep it simple)
- Do not add idea prioritization or sorting beyond creation order
- Do not modify the Failed task handling
- Do not add idea templates or forms beyond title + description
- Do not add idea assignment (ideas have no owner until promoted)
- Do not change how existing Pending/Working/Completed tasks behave

## Machine Details

- Server: ~/Projects/leroy/server/server.py, port 9800
- Dashboard: ~/Projects/leroy/dashboard/, port 5173
- Database: ~/Projects/leroy/data/tasks.db

## Budget

Simple. One new status value, two new endpoints, one new kanban column with inline form. No architectural changes.

## Execution

Use agent teams. Backend (new status + endpoints) and frontend (new column + UI) can run in parallel.