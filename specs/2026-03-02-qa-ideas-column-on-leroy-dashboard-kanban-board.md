---
spec_id: qa-ideas-column-on-leroy-dashboard-kanban-board
task_id: 97126e9b-813c-4139-aab3-4c63efaebf31
date: 2026-03-02
status: sent
pass_rate: (pending)
retrospective: (pending)
tags: []
---

# QA: Ideas Column on Leroy Dashboard Kanban Board

## Objective

Validate the build output of task 535910fc-7d9d-4cae-aff4-1e7dbee91e9c against the original spec's success criteria. This is a QA-only task. Do not modify any build output. Test what was built.

## Original Spec Reference

The build added an "Ideas" column to the Leroy dashboard kanban board as the first pipeline stage. Server-side: new "idea" status, POST /ideas endpoint, POST /ideas/{id}/promote endpoint. Dashboard-side: new leftmost kanban column with add/promote/discard actions.

## Test Plan

### Server API Tests

**Test 1: Create idea with title only**
- `POST http://localhost:9800/ideas` with body `{"title": "Test idea one"}`
- Expected: 200/201 response, returned task has status "idea", has a task_id, spec or title field contains "Test idea one"

**Test 2: Create idea with title and description**
- `POST http://localhost:9800/ideas` with body `{"title": "Test idea two", "description": "A short description"}`
- Expected: 200/201 response, returned task has status "idea", description is stored and retrievable

**Test 3: List all tasks includes ideas**
- `GET http://localhost:9800/tasks`
- Expected: Response includes the idea tasks created above with status "idea"

**Test 4: Filter tasks by idea status**
- `GET http://localhost:9800/tasks?status=idea`
- Expected: Returns only tasks with status "idea", no pending/working/completed tasks

**Test 5: Promote idea without spec body**
- `POST http://localhost:9800/ideas/{test_idea_id}/promote`
- Expected: Task status changes from "idea" to "pending", no spec content change

**Test 6: Promote idea with spec body**
- Create a new idea, then `POST http://localhost:9800/ideas/{id}/promote` with body `{"spec": "# Full spec\n\nThis is the promoted spec content."}`
- Expected: Task status changes to "pending", spec content is replaced with the provided markdown

**Test 7: Ideas do not trigger auto-execution**
- After creating an idea, verify no claude -p process spawns for it
- Check that ideas sit inert with no working status transition
- Expected: Idea remains in "idea" status until explicitly promoted

**Test 8: Discard/cancel an idea**
- Create an idea, then DELETE or cancel it via existing task cancellation endpoint
- Expected: Idea is removed or marked cancelled

**Test 9: Ideas survive server restart**
- Create an idea, note its ID
- Restart the server (or just verify SQLite persistence by querying the DB)
- `GET /tasks?status=idea` should still return the idea
- Expected: Ideas persist in SQLite across restarts

### Dashboard UI Tests

**Test 10: Ideas column is leftmost**
- Open dashboard at http://localhost:5173
- Expected: Kanban board shows columns in order: Ideas, Pending, Working, Completed (left to right)

**Test 11: Idea cards show correct content**
- With test ideas created, check the Ideas column
- Expected: Cards show title, optional description as subtitle, and created date. No task ID badge.

**Test 12: Add Idea button and inline form**
- Click the "+" button in the Ideas column header area
- Expected: Inline form appears with title input (required), description input (optional), and Add button
- Fill in title, click Add
- Expected: New idea card appears in the Ideas column
- Press Escape or click away
- Expected: Form cancels without creating an idea

**Test 13: Promote button on idea cards**
- Click the promote (arrow-right) button on an idea card
- Expected: Card moves from Ideas column to Pending column

**Test 14: Discard button on idea cards**
- Click the discard (X or trash) button on an idea card
- Expected: Card is removed from the board

**Test 15: Existing columns unchanged**
- Verify Pending, Working, and Completed columns still display and function as before
- Expected: No regressions in existing kanban functionality

**Test 16: Dark theme consistency**
- Verify the Ideas column and cards use forge-bg, forge-card, forge-border styling
- Expected: Visual consistency with existing columns, no unstyled elements

**Test 17: Tab badge does not count ideas**
- If the Tasks tab shows a badge count, verify ideas are excluded from the count
- Expected: Badge reflects only active work (pending + working), not ideas

## Constraints

- Do not modify any build output
- Test against the spec criteria, not assumptions
- Report exact pass/fail for each criterion (17 tests)
- If the server is not running, start it only to test. Do not modify server code.
- If the dashboard dev server is not running, start it only to test. Do not modify dashboard code.

## Machine Details

- Server: ~/Projects/leroy/server/server.py, port 9800
- Dashboard: ~/Projects/leroy/dashboard/, port 5173
- Database: ~/Projects/leroy/data/tasks.db

## Execution

Use agent teams. API tests and dashboard visual tests can run in parallel once both servers are confirmed running.