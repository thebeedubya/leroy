---
spec_id: troubleshoot-leroy-working-stall-bug
task_id: 54b7de7d-91c5-4b7e-8f89-4d09888e8850
date: 2026-03-01
status: completed
pass_rate: 4/4 (root cause identified, fix implemented, lifecycle intact, no regressions)
retrospective: What worked: Spec was well-structured with clear investigation steps. Root cause was found: 3 issues (stdin not devnulled causing subprocess hang, proc.poll() not called in event loop, no stuck task detector). All three fixed surgically in server.py. The troubleshooting format with numbered investigation steps worked well for diagnostic tasks.  What caused friction: This task itself stalled in WORKING (ironic). Ops had to complete it. But the fix was applied, so future tasks should not stall. The result is truncated and I don't have the full code diff or line numbers. Should have required "include exact file, function, and line numbers for every change" in the spec.  Spec improvement for next time: For troubleshooting specs, require the result to include: root cause with exact code path, diff or pseudodiff of changes, before/after behavior description. The truncated result leaves me guessing at the specifics of the fix.
tags: []
---

# Troubleshoot Leroy WORKING Stall Bug

## Objective

Diagnose and fix the recurring bug where Leroy tasks complete their work (code changes made, results generated) but never transition out of WORKING status. The task stays stuck in WORKING indefinitely until manually completed or respecced. This has happened 3 times and wastes PM capacity on redundant respecs.

## Known Occurrences

| Task ID | Spec | What Happened |
|---------|------|---------------|
| 7ec6182b | Fix PM Notification Pipeline | Code was fully implemented and running. Task never reported completion. PM cancelled and respecced (eeb79b73), which just verified the work was already done. |
| da5658de | Fix Dashboard Completed Tasks | CSS fix was applied correctly. Task never reported completion. PM respecced (68190e80), which confirmed the fix was already in place. |
| e8e647fd | Dashboard QA Review Panel | Both subtasks completed. Leroy interactive session stalled after subtask completion. Ops had to finish it. |

## Pattern

All three cases share: subtasks or main work completes successfully, but the parent task status never transitions from WORKING to COMPLETED. The `claude -p` subprocess appears to finish its work but the completion callback in the server doesn't fire or gets lost.

## Investigation Steps

1. **Read the task execution flow** in `server/server.py`. Trace the full lifecycle: task created -> status set to WORKING -> claude -p subprocess spawned -> subprocess completes -> status set to COMPLETED. Find where the chain can break.

2. **Check the subprocess completion handler.** When `claude -p` exits, what code runs? Is there a try/except that swallows errors? Is there a race condition between subprocess exit and status update? Does a timeout kill the subprocess before it can report?

3. **Check the subtask completion path.** When subtasks complete (agent teams), does the parent task status depend on subtask status? Is there a condition where all subtasks complete but the parent never transitions?

4. **Check MAX_TASK_TIMEOUT.** Currently 3600s (1 hour). If the subprocess runs close to the timeout, does the timeout handler race with the completion handler?

5. **Read server logs.** Check `~/Projects/leroy/data/` or stderr output for any error messages around the time of the three stalled tasks. Look for exceptions, timeouts, or unexpected subprocess exit codes.

6. **Check the SSE broadcast path.** Does `_broadcast_task_update_sync` fire on completion? Could an error in the broadcast prevent the status update from being written to SQLite?

7. **Propose a fix.** Based on root cause, implement the fix. If the root cause is unclear, add defensive logging at every status transition point so the next occurrence is diagnosable.

## Success Criteria

1. Root cause identified with specific code path (file, function, line number) where the stall occurs
2. Fix implemented that prevents the stall, OR comprehensive logging added at every status transition if root cause is ambiguous
3. Existing task lifecycle (create, working, completed, failed) still functions correctly after fix
4. No regressions in subtask tracking, SSE broadcasting, or SQLite persistence

## Constraints

- This is a troubleshooting task. Read the code carefully before changing anything.
- Do not refactor unrelated code. Surgical fix only.
- If the root cause is a race condition, the fix must be thread-safe.
- All changes in `server/` directory only.

## Do Not Do

- Do not modify the dashboard
- Do not modify mcp/leroy_client.py
- Do not change the A2A protocol or task schema
- Do not add new dependencies
- Do not change how tasks are created or how specs are received

## Machine Details

- Haze: `~/Projects/leroy/server/server.py` is the primary target
- SQLite: `~/Projects/leroy/data/tasks.db`
- Server: Starlette, port 9800 (API) / 9801 (health)
- Claude subprocess: `claude -p` with LEROY_SYSTEM_PROMPT injected

## Budget

Medium. Investigation + surgical fix. May require reading 500+ lines of server.py carefully.

## Execution

Use agent teams. Decompose this into sub-tasks and delegate to specialist agents. Do not execute sequentially as a single agent.

---
## Outcome
**Task ID:** 54b7de7d-91c5-4b7e-8f89-4d09888e8850
**QA pass rate:** 4/4 (root cause identified, fix implemented, lifecycle intact, no regressions)

## Retrospective
What worked: Spec was well-structured with clear investigation steps. Root cause was found: 3 issues (stdin not devnulled causing subprocess hang, proc.poll() not called in event loop, no stuck task detector). All three fixed surgically in server.py. The troubleshooting format with numbered investigation steps worked well for diagnostic tasks.

What caused friction: This task itself stalled in WORKING (ironic). Ops had to complete it. But the fix was applied, so future tasks should not stall. The result is truncated and I don't have the full code diff or line numbers. Should have required "include exact file, function, and line numbers for every change" in the spec.

Spec improvement for next time: For troubleshooting specs, require the result to include: root cause with exact code path, diff or pseudodiff of changes, before/after behavior description. The truncated result leaves me guessing at the specifics of the fix.
