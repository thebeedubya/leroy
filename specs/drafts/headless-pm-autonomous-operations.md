# Headless PM -- Autonomous Operations with Tiered Authority

## Objective

Enable PM to operate autonomously between Brad's interactive sessions. When a task completes, a message arrives, or a QA cycle finishes, headless PM wakes up, does the operational work that doesn't require human judgment, and queues anything that does for Brad's approval on the dashboard.

## Why

Right now PM only acts when Brad is in a session. Tasks complete at 2 AM and nothing happens until Brad opens a terminal. QA specs wait. Retros don't get written. Leroy's questions sit unanswered. The monitor daemon watches and logs, but can't act. Headless PM closes that gap.

## Architecture

### Three Components

1. **Monitor daemon** (exists: monitor/pm_monitor.py) -- detects trigger events, spawns headless PM sessions
2. **Headless PM** (new: spawned via `claude -p` with restricted persona) -- acts on tier 1 events autonomously, queues tier 2 for Brad
3. **Approval queue** (new: dashboard Decisions tab + server endpoint) -- Brad sees queued proposals, approves or rejects

### Trigger Events (monitor detects, spawns headless PM)

| Event | Monitor Detection | Headless PM Action |
|-------|------------------|-------------------|
| Task completed | Task status transition to COMPLETED | Pull results, compare to spec criteria, write retro via leroy_update_spec, send QA spec if build task |
| Task failed | Task status transition to FAILED | Pull results, write retro with failure analysis, alert Brad via bus message |
| Leroy question (routine) | New message type=QUESTION | Check if answer is in the spec. If yes, respond. If ambiguous, escalate to dashboard queue. |
| Leroy blocker | New message type=BLOCKER | Always escalate to dashboard queue. Never auto-respond to blockers. |
| Leroy decision gate | New message type=DECISION_GATE | Always escalate to dashboard queue. |
| QA results in | QA task completed | Pull pass/fail table, write retro, report summary to dashboard activity feed |
| Brad approves queued spec | Approval message on bus | Send the approved spec to Leroy via leroy_send_spec |

### Tiered Authority Model

**Tier 1 -- Headless PM autonomous (no human gate):**
- Read inbox, mark messages as read
- Respond to routine Leroy questions (answer is clearly in the spec)
- Write and send QA specs for completed build tasks
- Write retrospectives via leroy_update_spec
- Persist decisions and outcomes to forge-brain
- Archive completed tasks
- Post status updates to dashboard activity feed

**Tier 2 -- Headless PM queues for Brad (human gate required):**
- New build specs
- Respec decisions (task failed, need to rewrite and resend)
- Responding to blockers or decision gates
- Anything that changes what gets built

**Tier 3 -- Interactive PM only (Brad present):**
- Discovery conversations
- Design artifacts and mockups
- Strategy decisions
- New workstreams

### Headless PM Session Lifecycle

1. Monitor detects trigger event
2. Monitor spawns: `claude -p "$(cat personas/pm_headless.md)" --allowedTools leroy_*,mcp__aianna__*,Read,Glob,Grep --max-turns 20`
3. Headless PM receives context: trigger event type, task ID, relevant spec content
4. Headless PM executes tier 1 actions
5. Headless PM queues tier 2 items to bus: `POST /messages {from: "pm-headless", to: "pm", type: "approval_request", content: {spec_draft, reason, trigger}}`
6. Session ends. No persistent state beyond brain and bus.

### Headless PM Persona (personas/pm_headless.md)

Must include:
- Same PM identity and constraints from CLAUDE.md
- Explicit tier 1/2/3 boundaries with examples
- "When in doubt, queue for Brad" default
- No interactive conversation. Execute and exit.
- No design artifacts. No discovery.
- Max 20 turns per session (safety valve)
- Source tag all brain persists as "pm-headless/haze"

### Dashboard Approval Queue

Extends the existing Decisions tab (from workforce hub build):

- New section: "PM Proposals" above "Needs Your Response"
- Each proposal shows: trigger event, draft spec preview, PM's reasoning for the spec
- Two buttons: Approve (sends to Leroy), Reject (with optional feedback)
- Approved proposals: monitor detects the approval, spawns headless PM to send the spec
- Rejected proposals: feedback posted to bus, headless PM can revise on next trigger

Server endpoint:
- `POST /pm/proposals` -- headless PM submits a draft spec for approval
- `GET /pm/proposals?status=pending` -- dashboard polls for pending proposals
- `POST /pm/proposals/{id}/approve` -- Brad approves
- `POST /pm/proposals/{id}/reject` -- Brad rejects with feedback

### Safety Rails

1. **Max sessions per hour:** Monitor caps headless PM spawns at 10/hour. Prevents runaway loops.
2. **Max turns per session:** 20 turns. If headless PM can't finish in 20 turns, it posts a summary to the bus and exits.
3. **No self-modification:** Headless PM cannot modify its own persona, launcher, or monitor config (surgeons rule).
4. **Duplicate detection:** Before sending a QA spec, headless PM checks if a QA spec already exists for that build task. Prevents double-QA.
5. **Audit trail:** Every headless PM session persists a summary to forge-brain with source tag "pm-headless/haze". Every action logged to monitor log.
6. **Kill switch:** Brad can disable headless PM by stopping the monitor launchd service. One command.

## Scope

### In Scope
- Headless PM persona file (personas/pm_headless.md)
- Monitor daemon enhancement: trigger detection + headless PM spawning
- Server endpoints for approval queue (proposals CRUD)
- Dashboard Decisions tab: PM Proposals section with approve/reject
- QA spec template that headless PM uses (standardized format)
- Retro writing logic (pull results, compare to criteria, generate retro)
- Safety rails (rate limiting, max turns, duplicate detection)

### Out of Scope
- Headless PM writing build specs (that's tier 2, queued for Brad)
- Headless PM responding to blockers or decision gates (tier 2)
- Headless PM doing discovery or design (tier 3)
- Modifying the interactive PM session or CLAUDE.md
- GitHub PR integration (stays as manual/link-out)
- Multi-machine deployment (headless PM runs on Haze only)

## Success Criteria

1. When a build task completes, headless PM automatically sends a QA spec within 5 minutes.
2. When a QA task completes, headless PM automatically writes a retro via leroy_update_spec.
3. When a routine Leroy question arrives, headless PM responds if the answer is in the spec.
4. Blockers and decision gates always land in the dashboard approval queue, never auto-responded.
5. Brad can approve a queued build spec from the dashboard and it gets sent to Leroy without opening a PM session.
6. Headless PM never exceeds 20 turns per session.
7. Headless PM never spawns more than 10 times per hour.
8. All headless PM actions are persisted to forge-brain with "pm-headless/haze" source tag.
9. Brad can kill all headless PM activity by stopping the monitor launchd service.
10. No changes to interactive PM behavior or CLAUDE.md.

## Constraints

- Python for monitor enhancements (match existing pm_monitor.py)
- Headless PM spawned via `claude -p` (Claude Code CLI, not API)
- Same MCP servers as interactive PM (forge-brain, leroy, a2a)
- Same dark theme for dashboard additions
- No new npm dependencies
- SQLite for proposal storage (same task_db.py patterns)
- All timestamps UTC ISO8601

## Do Not Do

- Do not modify CLAUDE.md or the interactive PM persona
- Do not give headless PM Bash or Edit tool access
- Do not auto-respond to decision gates under any circumstances
- Do not send build specs without Brad's approval
- Do not create a separate dashboard for headless PM (use existing Decisions tab)
- Do not add authentication to the approval endpoints (localhost only)
- Do not build a chat interface for headless PM (it's headless, no UI)

## Machine Details

- Haze: monitor at monitor/pm_monitor.py, launchd managed (com.forge.pm-monitor.plist)
- Server: ~/Projects/leroy/server/server.py, port 9800
- Dashboard: ~/Projects/leroy/dashboard/, port 5173
- Brain: Kush 192.168.1.100:8300
- Personas: ~/Projects/leroy/personas/
- PM launcher reference: ./pm.sh

## Dependencies

- Workforce hub dashboard (task 907ba178) should be QA'd first -- the Decisions tab is the UI surface for this feature
- Generic message bus (already live) -- headless PM uses it for proposals and approvals
- Monitor daemon (already running) -- enhanced, not replaced

## Budget

Complex. New persona, monitor rewrite, server endpoints, dashboard UI, safety rails. Multi-agent execution recommended.

## Execution

Use agent teams. Suggested decomposition:
1. Backend: approval queue endpoints + proposal storage in SQLite
2. Persona: pm_headless.md with tiered authority rules
3. Monitor: trigger detection + headless PM spawning logic + safety rails
4. Frontend: Decisions tab enhancement with PM Proposals section
5. Integration: end-to-end test with a simulated task completion
