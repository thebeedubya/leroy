---
spec_id: dashboard-qa-review-panel-with-markdown-rendering
task_id: e8e647fd-4bf8-45ac-b16c-6ae4116aa5d8
date: 2026-03-01
status: completed
pass_rate: pending QA (build complete, QA spec 858be7fb sent)
retrospective: What worked: Spec was well-scoped with clear in/out scope boundaries. 6 requirements, each with specific implementation details. Ops completed the build with react-markdown, 6-column kanban, approve/reject flow, and server endpoint. All components delivered.  What caused friction: Leroy interactive session stalled after subtask completion. Ops had to finish it. This is the same "stall in WORKING" pattern seen on 7ec6182b and da5658de. The task execution engine has a recurring issue with status reporting after subtask completion. This is a systemic server bug, not a spec issue.  Spec improvement for next time: The spec was solid. The issue is in the task execution engine, not the spec. Need a separate spec to fix the WORKING stall bug -- tasks completing their work but never reporting completion. This has happened 3 times now.
tags: []
---

# Dashboard QA Review Panel with Markdown Rendering and Approve/Reject Flow

## Objective

Give Brad a way to review QA results in the dashboard and approve or reject them. Right now QA results come back as raw text crammed into a tiny `max-h-48` box. They contain markdown tables, headers, pass/fail breakdowns that are unreadable as raw monospace. Brad needs to see them rendered properly and act on them.

## Scope

### In Scope
1. **QA notification badge** -- visible indicator on the dashboard when completed tasks have QA results awaiting review
2. **Markdown rendering** in the task detail RESULT panel (tables, headers, bold, code blocks, lists)
3. **Approve / Reject CTA buttons** on completed tasks with QA results
4. **New task status: `qa_review`** -- tasks with QA results land here instead of going straight to `completed`
5. **Server endpoint** for approve/reject actions (`POST /tasks/{task_id}/review`)
6. **Notification bar or badge** at the top of the dashboard showing count of tasks awaiting QA review

### Out of Scope
- No email/push notifications (dashboard only)
- No multi-step approval workflows
- No comments or threaded discussion on reviews
- No changes to how Leroy generates QA results

## Requirements

### 1. QA Review Notification
- Dashboard header or top bar shows a badge: "N tasks awaiting review" when any task has status `qa_review`
- Badge is visually prominent (not subtle) -- use the purple/amber attention colors from the existing palette
- Clicking the badge filters or scrolls to the QA review column/tasks

### 2. Markdown Rendering in Result Panel
- Install `react-markdown` (or similar lightweight markdown renderer)
- Render the RESULT field as markdown instead of raw `<pre>` text
- Support: tables, headers (h1-h4), bold, italic, code blocks, inline code, ordered/unordered lists, horizontal rules
- The result panel should expand to fill available vertical space -- remove the `max-h-48` constraint and use the full detail panel height with scroll
- Keep the raw text fallback if markdown parsing fails

### 3. Approve / Reject Buttons
- Two buttons at the bottom of the task detail panel for tasks in `qa_review` status:
  - **APPROVE** (green/emerald) -- moves task to `completed` status with `review_decision: "approved"` and `reviewed_at` timestamp in the task data
  - **REJECT** (red) -- moves task to `failed` status with `review_decision: "rejected"` and `reviewed_at` timestamp. Optional: prompt for a rejection reason (text input)
- Buttons are only visible for tasks in `qa_review` status
- After clicking, the task moves to its new column and the detail panel updates

### 4. Server Changes
- **New endpoint: `POST /tasks/{task_id}/review`**
  - Body: `{ "decision": "approved" | "rejected", "reason": "optional rejection reason" }`
  - Auth: same bearer token auth as existing endpoints
  - Validates task exists and is in `qa_review` status
  - Updates task data with `review_decision`, `review_reason` (if rejected), `reviewed_at`, and moves status to `completed` or `failed`
  - Broadcasts update via SSE so dashboard updates live

- **New status: `qa_review`**
  - Add to the task lifecycle. Tasks can transition: `working` -> `qa_review` -> `completed` (approved) or `failed` (rejected)
  - The A2A `tasks/complete` endpoint should accept an optional `qa_review: true` flag. When present, task goes to `qa_review` instead of `completed`
  - Add `qa_review` to the status config in the dashboard utils (use a distinct color -- orange or cyan to differentiate from pending/waiting_for_pm)

### 5. Dashboard Column
- Add a new kanban column: **QA REVIEW** between EXECUTING and COMPLETED
- Use a distinct color scheme (suggest cyan: `text-cyan-400`, `bg-cyan-400/10`, `border-cyan-400/25`)
- Column header shows count of tasks in review

## Success Criteria

1. Completed QA tasks show up in a QA REVIEW column (not straight to completed)
2. A visible notification/badge shows the count of tasks awaiting review
3. Clicking a QA review task shows the result rendered as markdown with proper tables and formatting
4. APPROVE button moves the task to completed with review metadata
5. REJECT button moves the task to failed with review metadata and optional reason
6. The review endpoint is authenticated and validates status transitions
7. SSE broadcast fires on review so the board updates live without refresh

## Constraints

- Do not break existing task lifecycle for non-QA tasks. Tasks without `qa_review: true` still go straight to `completed`
- Do not modify the SQLite schema (task data is a JSON blob in the `data` column -- add review fields to the JSON)
- Keep the existing `parseSuccessCriteria` logic working on the task cards (it reads PASS/FAIL from raw text)
- Dashboard must continue to work for all existing completed tasks (backward compatible)
- Use the existing Tailwind classes and forge color palette

## Technical Context

- **Dashboard**: React, Vite, Tailwind. Source at `dashboard/src/`
- **Server**: Python Starlette. Source at `server/server.py`
- **Task DB**: SQLite WAL at `~/Projects/leroy/data/tasks.db`. Schema in `server/task_db.py`. Task data stored as JSON blob.
- **SSE**: Already implemented at `/tasks/stream` for live updates
- **Auth**: Bearer token `LEROY_A2A_TOKEN_REDACTED`
- **Existing status config**: In `dashboard/src/utils.js` -- add `qa_review` entry
- **Existing components**: `TaskDetail.jsx` (detail panel), `TaskCard.jsx` (kanban card), `TaskColumn.jsx`, `TaskBoard.jsx`, `App.jsx`

## What NOT To Do

- Do not add a new database table for reviews. Use the existing task data JSON blob.
- Do not change how Leroy reports results. The `qa_review` flag is set when completing a task, not by modifying the result format.
- Do not add WebSocket support. Use the existing SSE infrastructure.
- Do not add user accounts or role-based access. This is single-user (Brad).

## Execution

Use agent teams. Decompose this into sub-tasks and delegate to specialist agents. Do not execute sequentially as a single agent.
---
## Outcome
**Task ID:** e8e647fd-4bf8-45ac-b16c-6ae4116aa5d8
**QA pass rate:** pending QA (build complete, QA spec 858be7fb sent)

## Retrospective
What worked: Spec was well-scoped with clear in/out scope boundaries. 6 requirements, each with specific implementation details. Ops completed the build with react-markdown, 6-column kanban, approve/reject flow, and server endpoint. All components delivered.

What caused friction: Leroy interactive session stalled after subtask completion. Ops had to finish it. This is the same "stall in WORKING" pattern seen on 7ec6182b and da5658de. The task execution engine has a recurring issue with status reporting after subtask completion. This is a systemic server bug, not a spec issue.

Spec improvement for next time: The spec was solid. The issue is in the task execution engine, not the spec. Need a separate spec to fix the WORKING stall bug -- tasks completing their work but never reporting completion. This has happened 3 times now.
