---
spec_id: fix-pm-notification-pipeline-task-completion-alert
task_id: 7ec6182b-34c4-469a-9c8d-cfa69c42f41d
date: 2026-03-01
status: completed
pass_rate: cancelled (stalled in WORKING, code was complete but status never reported)
retrospective: What worked: The code Leroy wrote was correct. All notification pipeline components were implemented and functional. The task completed the actual engineering work.  What caused friction: Task stalled in WORKING status. The execution was done but the completion callback never fired. I cancelled it and respecced (eeb79b73) instead of investigating why it stalled. The respec just verified the original work was already done. Wasted a task slot.  Spec improvement for next time: Stalled tasks are not failed tasks. Check the work product before cancelling and respeccing. If the code is done but the status is stuck, that's a server-side status reporting issue, not a spec issue.
tags: []
---

# Fix PM Notification Pipeline - Task Completion Alerts

## Objective
PM (Product Manager agent) currently has no way to receive automatic notifications when Leroy completes tasks. The PM webhook (pm_webhook.py) is registered with the Leroy A2A server but notifications are not being delivered into the PM's Claude Code session. PM is forced to manually poll `leroy_check_task()` which is unreliable and unsustainable.

The previous "coordinator" approach failed because it tried to spawn `claude -p` inside an existing Claude Code session, which is blocked by the nested session guard.

Fix PM's notification pipeline so that task completions, failures, questions, and decision gates from Leroy are surfaced to PM automatically.

## Background

### Current State
- Leroy A2A server runs at localhost:9800, webhook is registered
- `server/pm_webhook.py` exists -- it was built to catch Leroy events and notify PM
- `server/pm_message_hook.sh` exists as a shell hook
- PM runs via `./pm.sh` which launches Claude Code with `--disallowedTools` for Bash/Edit/etc
- The coordinator concept (a subprocess that polls Leroy and reports to PM) crashed because `claude -p` cannot run inside another Claude Code session (CLAUDECODE env var blocks it)
- PM currently has `leroy_read_messages()` and `leroy_check_task()` but must call them manually

### What Needs to Work
When Leroy completes a task, fails a task, asks a question, or hits a decision gate, PM should be notified without manual intervention. The notification should include: task ID, status, and enough context that PM can act on it.

## Scope

### In Scope
- Diagnose why pm_webhook.py notifications are not reaching PM's session
- Fix or rebuild the notification delivery mechanism
- Consider alternatives to the nested `claude -p` approach:
  - Desktop notifications (macOS `osascript` alert)
  - A file-based signal that PM's session can detect
  - A hook registered in PM's Claude Code settings that fires on events
  - Fixing the webhook to write to a location PM can read
  - Any other approach that actually works
- Test end to end: send a test spec to Leroy, verify PM gets notified on completion

### Out of Scope
- Changes to Leroy's task execution logic
- Changes to PM's CLAUDE.md or persona
- Dashboard changes
- Content agent work

## Success Criteria
1. When Leroy completes a task, PM receives a notification within 60 seconds without manual polling
2. The notification includes: task ID, task subject, completion status, and result summary
3. When Leroy sends a message (question, blocker, decision gate), PM receives it without manually calling `leroy_read_messages()`
4. The solution survives PM session restarts (not dependent on ephemeral state)
5. The solution does NOT require spawning `claude -p` inside an existing Claude Code session

## Constraints
- PM runs on Haze via `./pm.sh`
- PM cannot run Bash, Edit, or write code. Do not modify PM's tool access.
- The fix must be implemented by Ops (./ops.sh) or directly in the server/webhook code
- Follow the "surgeons don't operate on themselves" principle -- PM does not configure PM
- Read existing code (pm_webhook.py, pm_message_hook.sh, server config) before building anything new
- Leroy server code: ~/Projects/leroy/server/
- PM launcher: ~/Projects/leroy/pm.sh
- Ops launcher: ~/Projects/leroy/ops.sh

## Machine Details
- Haze (local): ~/Projects/leroy/
- Leroy server: localhost:9800 (health on 9801)
- forge-brain: Kush 192.168.1.100:8300

## Execution
Use agent teams. Decompose this into sub-tasks and delegate to specialist agents. Do not execute sequentially as a single agent.
---
## Outcome
**Task ID:** 7ec6182b-34c4-469a-9c8d-cfa69c42f41d
**QA pass rate:** cancelled (stalled in WORKING, code was complete but status never reported)

## Retrospective
What worked: The code Leroy wrote was correct. All notification pipeline components were implemented and functional. The task completed the actual engineering work.

What caused friction: Task stalled in WORKING status. The execution was done but the completion callback never fired. I cancelled it and respecced (eeb79b73) instead of investigating why it stalled. The respec just verified the original work was already done. Wasted a task slot.

Spec improvement for next time: Stalled tasks are not failed tasks. Check the work product before cancelling and respeccing. If the code is done but the status is stuck, that's a server-side status reporting issue, not a spec issue.
