---
spec_id: dashboard-decisions-tab-pm-proposals-approvereject
task_id: 5b22edb8-612a-422a-be06-337b32796db7
date: 2026-03-02
status: sent
pass_rate: (pending)
retrospective: (pending)
tags: []
---

# Dashboard Decisions Tab -- PM Proposals Section

## Objective
Add a "PM Proposals" section to the existing Decisions tab so Brad can approve or reject draft specs submitted by headless PM. The backend endpoints are already live. This is frontend only.

## Why
Headless PM is now live. When it encounters tier 2 work (build specs, respec decisions), it submits proposals via POST /pm/proposals. Brad needs a way to see and act on these without opening a terminal. The Decisions tab is the right surface.

## What Already Exists
- Decisions tab in the workforce hub dashboard (built in task 907ba178)
- Backend endpoints: GET /pm/proposals, POST /pm/proposals/{id}/approve, POST /pm/proposals/{id}/reject
- Proposal schema: {id, proposal_type, title, content, reasoning, trigger_event, trigger_task_id, status, created_at, reviewed_at, feedback}

## What to Build

### PM Proposals Section
Place this ABOVE the existing "Needs Your Response" section in the Decisions tab. It is the highest priority content on the tab.

**Section header:** "PM Proposals" with a count badge (same pattern as the existing pending messages badge). Badge shows number of pending proposals. Hide the entire section if there are zero pending proposals.

**Proposal cards:** One card per pending proposal. Each card shows:
- **Title** (bold, top line)
- **Proposal type** badge: "QA Spec" (blue), "Build Spec" (amber), "Respec" (red)
- **Trigger event** (one line, muted text): "Triggered by: task_completed on {task_id}"
- **Reasoning** (one line, muted text): why headless PM thinks this spec should be sent
- **Spec preview** (collapsible): first 20 lines of the draft spec content, monospace, expandable to full text on click
- **Time waiting** (relative): "2m ago", "1h ago"
- **Two action buttons:**
  - **Approve** (green): Sends POST /pm/proposals/{id}/approve. On success, card animates out and count decrements.
  - **Reject** (red): Opens an inline text input for feedback (required). Submit sends POST /pm/proposals/{id}/reject with {feedback: "..."}. Card animates out.

**Recently decided** (collapsed by default): Show last 5 approved/rejected proposals below the pending ones. Each shows: title, outcome (approved/rejected), feedback if rejected, timestamp.

### Data Source
- Poll GET /pm/proposals?status=pending every 10 seconds for pending proposals
- GET /pm/proposals?status=all&limit=5 for recently decided (poll every 30 seconds)

### Interactions
- Click proposal card to expand spec preview
- Approve button: confirm action, POST approve, remove card
- Reject button: expand feedback input, require text, POST reject, remove card
- No bulk actions. One at a time. These are decisions.

## Success Criteria
1. PM Proposals section appears above "Needs Your Response" in the Decisions tab
2. Section is hidden when there are zero pending proposals
3. Pending proposals display title, type badge, trigger, reasoning, time waiting
4. Spec preview is collapsible and shows full content when expanded
5. Approve button sends POST /pm/proposals/{id}/approve and removes the card
6. Reject button requires feedback text before submitting
7. Reject sends POST /pm/proposals/{id}/reject with feedback and removes the card
8. Recently decided section shows last 5 resolved proposals (collapsed by default)
9. Badge count on section header matches pending proposal count
10. Polling refreshes pending proposals every 10 seconds
11. Dark theme matches existing dashboard (forge-bg, forge-card, forge-border, forge-surface)
12. No new npm dependencies

## Constraints
- React 18 + Vite + Tailwind (existing stack)
- No new npm dependencies
- Same dark theme, same font stack (JetBrains Mono for data, DM Sans for UI)
- Same component patterns as existing Decisions tab content
- Monospace font for spec preview content
- Do not modify the existing "Needs Your Response" messages section
- Do not modify any backend code. All endpoints are live and tested.
- Do not add authentication to proposal endpoints (localhost dashboard)

## Do Not Do
- Do not modify backend endpoints or server.py
- Do not change existing Decisions tab content (messages section)
- Do not add WebSocket support (polling is fine for this frequency)
- Do not add batch approve/reject
- Do not add editing of proposals (approve or reject, no middle ground)

## Machine Details
- Dashboard: ~/Projects/leroy/dashboard/ (React 18, Vite, Tailwind, port 5173)
- Server: localhost:9800 (proposals endpoints already live)
- Existing Decisions tab component: find it in the dashboard source, add to it

## Budget
Simple to medium. One new component section in an existing tab. No backend work.

## Execution
Use agent teams. One agent to build the ProposalsSection component, one to wire it into the existing Decisions tab and test the polling + approve/reject flow.