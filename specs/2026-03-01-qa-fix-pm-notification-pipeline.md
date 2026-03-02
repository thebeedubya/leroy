---
spec_id: qa-fix-pm-notification-pipeline
task_id: eeda17f3-e4b8-4ccf-bb15-f4929cb9a878
date: 2026-03-01
status: completed
pass_rate: 4/6 (2 partial: PID mismatch in session capture, sidecar messages not written to SQLite)
retrospective: What worked: QA spec was well-structured with 6 independent test criteria. Leroy found two real issues: PID mismatch (launcher PID captured instead of webhook PID) and dual storage gap (sidecar messages not persisted to SQLite). These are genuine bugs that would have bitten us later.  What caused friction: The spec referenced a /webhook endpoint that doesn't exist (correct path is /messages). I wrote the QA spec without checking the actual API routes. Leroy worked around it, but sloppy spec writing. Also, I sent this QA spec and the build respec simultaneously rather than sequentially (build then QA). QA should only run after the build is confirmed complete.  Spec improvement for next time: Always verify endpoint paths against the actual server routes before writing QA specs. And enforce the sequence: build spec completes, THEN QA spec goes out. Never send both at once. The QA needs to test what was actually built, not what I think was built.
tags: []
---

# QA: Fix PM Notification Pipeline - Task Completion Alerts

## Objective
Verify that the PM notification pipeline is fully functional end-to-end. The build task (eeb79b73) reported all components already running from a prior implementation. This QA validates those claims independently.

## What to Test

### Test 1: PM Webhook Health
- Confirm pm_webhook.py is running on port 9802
- Hit GET http://localhost:9802/health and verify 200 response
- Verify PID matches what's in ~/.forge/pm_session.json

### Test 2: Hook Configuration
- Read .claude/settings.json and confirm PreToolUse hook points to server/pm_message_hook.sh
- Confirm pm_message_hook.sh is executable
- Confirm pm_message_hook.sh reads from ~/.forge/pm_messages.json

### Test 3: Terminal Window ID Capture
- Read ~/.forge/pm_session.json
- Confirm it contains window_id, pid fields
- Confirm the PID is alive (kill -0 $PID)

### Test 4: Stale Registry Detection
- Read server/message_broker.py
- Confirm _get_pm_webhook_url() validates PID aliveness via os.kill(pid, 0)
- Confirm HTTP health check with timeout
- Confirm 15-second cache

### Test 5: End-to-End Message Delivery
- Send a test message to the PM webhook: POST http://localhost:9802/webhook with a mock deliverable_ready payload
- Verify the message appears in ~/.forge/pm_messages.json
- Verify the message includes task_id, status, and summary fields

### Test 6: PM Session Restart Survival
- Check that pm_messages.json persists on disk (not just in-memory)
- Check that the SQLite broker database exists and has message records
- Verify that if pm_webhook.py were restarted, it would recover pending messages from disk

## Success Criteria
1. PASS/FAIL: PM webhook responds healthy on port 9802
2. PASS/FAIL: PreToolUse hook is correctly wired in settings.json
3. PASS/FAIL: Terminal window ID captured and PID alive
4. PASS/FAIL: Stale registry detection code present and correct in message_broker.py
5. PASS/FAIL: Test message delivered end-to-end to pm_messages.json
6. PASS/FAIL: Messages persist to disk and survive process restart

## Constraints
- Do NOT modify any code. This is read-only verification.
- Do NOT restart any services.
- Do NOT touch PM's session or tools.
- Server code: ~/Projects/leroy/server/
- PM config: ~/.forge/pm_session.json, ~/.forge/pm_messages.json
- Claude settings: ~/Projects/leroy/.claude/settings.json

## Machine Details
- Haze (local): ~/Projects/leroy/
- PM webhook: localhost:9802
- Leroy server: localhost:9800

## Execution
Use agent teams. Decompose this into sub-tasks and delegate to specialist agents. Do not execute sequentially as a single agent.
---
## Outcome
**Task ID:** eeda17f3-e4b8-4ccf-bb15-f4929cb9a878
**QA pass rate:** 4/6 (2 partial: PID mismatch in session capture, sidecar messages not written to SQLite)

## Retrospective
QA validated the notification pipeline is functional end-to-end. 4 clean passes on webhook health, hook config, stale detection logic, and E2E delivery. Two partial passes: (1) pm_session.json captures launcher shell PID not webhook PID, creating false positive risk on stale detection. (2) Sidecar webhook messages write to pm_messages.json but not SQLite, so leroy_read_messages MCP tool cannot see them after restart. Spec improvement: should have specified which PID to capture and required single storage path for all message sources.

---
## Outcome
**Task ID:** eeda17f3-e4b8-4ccf-bb15-f4929cb9a878
**QA pass rate:** 4/6 (2 partial: PID mismatch in session capture, sidecar messages not written to SQLite)

## Retrospective
What worked: QA spec was well-structured with 6 independent test criteria. Leroy found two real issues: PID mismatch (launcher PID captured instead of webhook PID) and dual storage gap (sidecar messages not persisted to SQLite). These are genuine bugs that would have bitten us later.

What caused friction: The spec referenced a /webhook endpoint that doesn't exist (correct path is /messages). I wrote the QA spec without checking the actual API routes. Leroy worked around it, but sloppy spec writing. Also, I sent this QA spec and the build respec simultaneously rather than sequentially (build then QA). QA should only run after the build is confirmed complete.

Spec improvement for next time: Always verify endpoint paths against the actual server routes before writing QA specs. And enforce the sequence: build spec completes, THEN QA spec goes out. Never send both at once. The QA needs to test what was actually built, not what I think was built.
