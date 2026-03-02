---
spec_id: fix-task-list-api-full-uuid-display
task_id: ba03d987-c2b2-4ef4-aa0f-a50a2cdc7139
date: 2026-03-01
status: completed
pass_rate: 4/4
retrospective: What worked: Tight spec. One bug, one file, one line change. Leroy found the root cause (UUID truncation in display formatting) and fixed it in under 2 minutes. Success criteria were simple and binary.  What caused friction: Leroy accidentally archived a task during testing (da5658de). Had to fix it directly in SQLite. The spec should have included a "do not modify task data during testing" constraint, or the test should have used a known-safe task ID.  Spec improvement for next time: For specs that touch task management code, include a constraint: "Test with task IDs from completed tasks only. Do not archive, delete, or modify any task as a side effect of testing." Defensive constraints prevent accidental data mutations.
tags: []
---

# Fix Task List API - Full UUID Display

## Objective
The task list endpoint and MCP tool output truncates task IDs, making it impossible to use them with other API calls (check_task, archive_task, etc.). The list shows IDs like `da5658de-28a...` but the API requires the full UUID. PM cannot manage tasks when IDs are truncated.

## Scope

### In Scope
- Fix the `leroy_list_tasks` MCP tool output to return full UUIDs for every task
- If there's a display width concern, that's a frontend problem. The API/tool response must always include the complete task ID.
- Verify that `leroy_check_task`, `leroy_archive_task`, and `leroy_delete_task` all work with the full IDs returned

### Out of Scope
- Dashboard UI changes
- Task creation or execution logic
- Any other API endpoints

## Success Criteria
1. `leroy_list_tasks()` returns full UUIDs for every task (e.g., `3ebe0b93-182b-4fb4-8d35-5390ab95d112`, not `3ebe0b93-182...`)
2. Every ID returned by list_tasks can be directly passed to check_task, archive_task without modification
3. Existing task data is not modified

## Constraints
- The Leroy A2A server code lives at ~/Projects/leroy/server/
- The MCP client code lives at ~/Projects/leroy/mcp/
- This is a formatting fix, not a schema change. Do not change the database or task creation logic.
- Read the current code before changing anything.

## Machine Details
- Haze (local machine): ~/Projects/leroy/
- Server runs on localhost:9800

## Execution
Use agent teams. Decompose this into sub-tasks and delegate to specialist agents. Do not execute sequentially as a single agent.
---
## Outcome
**Task ID:** ba03d987-c2b2-4ef4-aa0f-a50a2cdc7139
**QA pass rate:** 4/4

## Retrospective
What worked: Tight spec. One bug, one file, one line change. Leroy found the root cause (UUID truncation in display formatting) and fixed it in under 2 minutes. Success criteria were simple and binary.

What caused friction: Leroy accidentally archived a task during testing (da5658de). Had to fix it directly in SQLite. The spec should have included a "do not modify task data during testing" constraint, or the test should have used a known-safe task ID.

Spec improvement for next time: For specs that touch task management code, include a constraint: "Test with task IDs from completed tasks only. Do not archive, delete, or modify any task as a side effect of testing." Defensive constraints prevent accidental data mutations.
