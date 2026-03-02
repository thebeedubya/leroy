---
spec_id: qa-content-dashboard-v1
task_id: 21189b54-0e94-4678-a012-e21476e6b0bb
date: 2026-03-02
status: completed
pass_rate: 15/17
retrospective: ## Retrospective **What worked in this QA spec:** The 17-test plan mapped 1:1 to original success criteria, making pass/fail assessment clean. Covering backend API, frontend UI, SQLite persistence, theme, and independence checks in a single pass gave comprehensive coverage. The constraint to not modify build output was respected.  **What caused friction:** QA result output was truncated by the Leroy API, losing detail on 15 of 17 test results. Only SC-1 and partial SC-2 were visible in the task result. This means PM cannot fully analyze which specific tests passed or review failure details beyond SC-2.  **Spec improvement for next time:** For QA specs with more than 10 test cases, add a constraint: "Write results to a file at {path}/qa-results.md in addition to returning them." This ensures full results survive API truncation. Alternatively, split into two QA specs (backend + frontend) to keep each result set under the API field limit.
tags: []
---

# QA: Content Dashboard v1 -- Standalone dbradwood.com Marketing Hub

## Objective
Validate the build output of task dc9f10d2-de37-4c7d-8292-544c049c741b against the original spec's success criteria. This is a standalone content dashboard for Brad's content operations, separate from the Leroy dashboard.

## Original Spec Reference
Task dc9f10d2: Content Dashboard v1. 17 success criteria covering backend API, frontend UI, markdown parser, SQLite persistence, dark theme, and standalone operation.

## Test Plan

Test each success criterion. Report exact pass/fail for each.

### Backend Tests

**SC-1: Health endpoint**
- Start the content server on its assigned port.
- `curl http://localhost:{port}/health` must return `{"status": "ok"}` with HTTP 200.
- PASS if response matches. FAIL otherwise.

**SC-2: GET /content/today**
- `curl http://localhost:{port}/content/today`
- Response must be JSON containing parsed media brief with: angles array, each angle having scores, title, and all 4 platform drafts (blog, linkedin, x, instagram).
- PASS if all fields present and populated. FAIL if any missing.

**SC-3: GET /content/{date}**
- `curl http://localhost:{port}/content/2026-03-01`
- Must return the parsed brief for that specific date (the test draft file).
- PASS if returns valid brief with angles. FAIL if 404 or empty.

**SC-4: GET /content/history**
- `curl http://localhost:{port}/content/history`
- Must return a list of dates with run status and approval counts.
- Test `?limit=5&offset=0` pagination.
- PASS if returns array of date entries. FAIL otherwise.

**SC-11: Markdown parser**
- Test parser against `~/Projects/leroy/content/drafts/2026-03-01.md`.
- Verify it extracts: H1 title/date, summary, each angle's title, score, target angle, source sessions, confidence.
- Verify each angle has all 4 platform drafts: blog (with front matter), linkedin, x (thread format), instagram (caption + carousel).
- PASS if all fields correctly extracted. FAIL if any field missing or malformed.

**SC-14: Read-only file access**
- Verify content server reads from `~/Projects/leroy/content/drafts/` and `~/Projects/leroy/content/logs/` without modifying those files.
- Check file mtimes before and after server operation.
- PASS if no files modified. FAIL if any mtime changed.

**SC-15: SQLite database location**
- Verify SQLite database exists at `~/Projects/leroy/content/data/content.db`.
- Verify schema contains tables: briefs, angles, platform_drafts, runs.
- PASS if DB exists with correct schema. FAIL otherwise.

**SC-16: Launchd plist**
- Verify a launchd plist exists for the content server with KeepAlive and RunAtLoad keys.
- PASS if plist exists with both keys. FAIL otherwise.

**SC-17: No Leroy dependency**
- Grep the content server and dashboard source for any imports from or references to Leroy server modules or endpoints.
- PASS if zero Leroy references found. FAIL if any dependency detected.

### Frontend Tests

**SC-5: Dashboard renders angles**
- Load the dashboard in a browser.
- Verify today's angles display with scores, titles, and status badges.
- Score badge colors: green >= 7, yellow 5-6, red < 5.
- PASS if angles render with correct badge colors. FAIL otherwise.

**SC-6: Expand angle for platform drafts**
- Click an angle card to expand.
- Verify 4 platform tabs appear: Blog, LinkedIn, X, Instagram.
- Verify each tab shows its respective draft content.
- PASS if all 4 tabs render with content. FAIL if any missing.

**SC-7: Approve button**
- Click "Approve" on an angle.
- Verify status changes to "approved" (green badge).
- Verify persisted to SQLite (query the angles table).
- PASS if status updates in UI and DB. FAIL otherwise.

**SC-8: Reject button**
- Click "Reject" on an angle.
- Verify a text input appears for reason.
- Enter a reason and submit.
- Verify status changes to "rejected" (red badge) and reason stored in SQLite.
- PASS if status updates with reason in UI and DB. FAIL otherwise.

**SC-9: Mark Posted**
- Click "Mark Posted" on an approved angle.
- Select platform and paste a URL.
- Verify posted_url and posted_at recorded in SQLite platform_drafts table.
- Verify status badge updates to "posted" (blue).
- PASS if posted state recorded correctly. FAIL otherwise.

**SC-10: History view**
- Navigate to History view.
- Verify reverse-chronological list of dates with: date, pipeline status, angle count, approval summary.
- Click a date to expand and verify full brief renders.
- PASS if history displays correctly. FAIL otherwise.

**SC-12: Auto-refresh**
- Dashboard must poll for content updates every 60 seconds.
- Verify network tab shows periodic requests (or check source code for polling interval).
- PASS if 60-second poll confirmed. FAIL otherwise.

**SC-13: Dark theme**
- Verify background color is `#0f172a` (forge-bg).
- Verify card background is `#1e293b` (forge-card).
- Verify border color is `#334155` (forge-border).
- Verify text color is `#e2e8f0` (slate-200).
- Verify accent color is `#3b82f6` (blue-500).
- Verify font is JetBrains Mono.
- PASS if all theme values match. FAIL otherwise.

## Constraints

- Do not modify any build output.
- Test against the spec criteria, not your assumptions.
- Report exact pass/fail for each of the 17 success criteria.
- Test against the existing draft file at `content/drafts/2026-03-01.md`.
- If the content server is not already running, start it to test.
- Format results as a numbered list matching SC-1 through SC-17.

## Machine Details

- Haze: ~/Projects/leroy/content/
- Content dashboard frontend: ~/Projects/leroy/content/dashboard/
- Content server backend: ~/Projects/leroy/content/server/
- Content drafts: ~/Projects/leroy/content/drafts/
- Content logs: ~/Projects/leroy/content/logs/
- SQLite DB expected at: ~/Projects/leroy/content/data/content.db

## Execution
Use agent teams.
---
## Outcome
**Task ID:** 21189b54-0e94-4678-a012-e21476e6b0bb
**QA pass rate:** 15/17

## Retrospective
## Retrospective
**What worked in this QA spec:** The 17-test plan mapped 1:1 to original success criteria, making pass/fail assessment clean. Covering backend API, frontend UI, SQLite persistence, theme, and independence checks in a single pass gave comprehensive coverage. The constraint to not modify build output was respected.

**What caused friction:** QA result output was truncated by the Leroy API, losing detail on 15 of 17 test results. Only SC-1 and partial SC-2 were visible in the task result. This means PM cannot fully analyze which specific tests passed or review failure details beyond SC-2.

**Spec improvement for next time:** For QA specs with more than 10 test cases, add a constraint: "Write results to a file at {path}/qa-results.md in addition to returning them." This ensures full results survive API truncation. Alternatively, split into two QA specs (backend + frontend) to keep each result set under the API field limit.
