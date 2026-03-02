---
spec_id: qa-respec-workforce-hub-22-criteria-must-produce-p
task_id: 77c0a04c-dd87-48a8-891c-5f0da4142bbe
date: 2026-03-02
status: sent
pass_rate: (pending)
retrospective: (pending)
tags: []
---

# QA: FORGE Dashboard Workforce Hub (Respec -- Previous QA Returned No Results)

## Objective
Verify the Workforce Hub build (task 907ba178) against all 22 success criteria. The previous QA attempt (task dd6fe7a9) completed without producing any test results. This time, you MUST produce the pass/fail table as your output. No excuses.

## Why This Is a Respec
Task dd6fe7a9 returned: "I have all the data needed. Compiling the QA report now." and then exited. That is not a QA report. That is nothing. The ONLY acceptable output from this task is a completed pass/fail table with evidence for every criterion.

## What You Must Do

Test each criterion below. For EACH one, report Pass or Fail with a one-line evidence statement. Do not summarize. Do not skip. Do not exit early. If you cannot test a criterion (e.g., server is down), report it as BLOCKED with the reason.

## How to Test

**Backend endpoints (criteria 14-19):** Use curl against localhost:9800. Check HTTP status code and response JSON shape. Example: `curl -s http://localhost:9800/agents | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d),'keys:', list(d.keys())[:5])"` 

**Frontend components (criteria 1-13, 20-22):** Read the source files in ~/Projects/leroy/dashboard/src/. Check that components exist, are imported, and render the expected elements. If the dev server is running on localhost:5173, test in browser. If not, start it: `cd ~/Projects/leroy/dashboard && npm run dev &`

**SSE (criterion 17):** `curl -s -N -H "Accept: text/event-stream" http://localhost:9800/activity/stream &` then wait 5 seconds and check if events arrive.

## Success Criteria (test ALL 22)

### Navigation
1. Tab navigation works across all 6 tabs (Tasks, Agents, Decisions, Activity, Specs, System). Tasks tab is default.
2. URL hash persists selected tab on refresh.

### Agents Tab
3. Agents tab displays at least 4 agent cards (PM, Ops, Leroy, Content Agent).
4. Agent cards show status, last heartbeat, current task, launch method.

### Decisions Tab
5. Decisions tab displays pending PM messages.
6. Decisions tab allows inline response to pending messages.

### Activity Tab
7. Activity tab shows reverse-chronological feed of task lifecycle events.
8. Activity tab supports filtering by agent name.
9. Activity tab auto-updates via SSE.

### Specs Tab
10. Specs tab shows pipeline columns (Draft/Sent/Building/QA/Done/Failed).
11. Specs tab populates from existing task data.

### System Tab
12. System tab shows brain health status.
13. System tab shows infrastructure cards for Kush, Haze, APEX.

### Backend Endpoints
14. GET /agents returns agent roster with status fields (valid JSON, 200 OK).
15. POST /agents/{name}/heartbeat accepts POST and updates agent record (200 or 201).
16. GET /activity returns event list with limit/since params (valid JSON, 200 OK).
17. GET /activity/stream returns SSE content type and emits events.
18. GET /brain/health proxies forge-brain health check (valid JSON).
19. GET /infra/status returns machine status (valid JSON, 200 OK).

### General
20. Dark theme consistent with existing dashboard (forge-bg, forge-card colors in source).
21. No new npm dependencies added to package.json.
22. Existing Tasks tab components (TaskCard, kanban) are unmodified from pre-build state.

## Required Output Format

Your final output MUST be this table, fully populated:

```
| #  | Criterion | Result | Evidence |
|----|-----------|--------|----------|
| 1  | Tab navigation across 6 tabs | Pass/Fail/Blocked | [one line] |
| 2  | URL hash persists on refresh | Pass/Fail/Blocked | [one line] |
| 3  | Agents tab shows 4+ cards | Pass/Fail/Blocked | [one line] |
...through 22...
```

Followed by a summary line: `X/22 Pass, Y/22 Fail, Z/22 Blocked`

If this table is not in your output, you have failed this task regardless of what else you did.

## Constraints
- Read-only testing. Do NOT modify any code.
- If the dashboard dev server is not running, start it with `cd ~/Projects/leroy/dashboard && npm run dev &`
- If the Leroy A2A server is not running on port 9800, report ALL backend criteria as BLOCKED.
- Do NOT exit until the table is complete.

## Machine Context
- Dashboard: ~/Projects/leroy/dashboard/ (React 18, Vite, port 5173)
- Server: ~/Projects/leroy/server/server.py (Starlette, port 9800)
- Brain: Kush 192.168.1.100:8300

## Budget
Medium. But finishing is mandatory. Do not time out before producing the table.

## Execution
Use agent teams. One agent for backend (curl every endpoint), one for frontend (read source files and verify components). Both agents report back. You compile the table. Do not exit without it.