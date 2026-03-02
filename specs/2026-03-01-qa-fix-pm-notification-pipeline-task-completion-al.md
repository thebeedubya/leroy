---
spec_id: qa-fix-pm-notification-pipeline-task-completion-al
task_id: 138ae9ea-648d-4ba5-a73c-cdaffdfd3b33
date: 2026-03-01
status: failed
pass_rate: 5/6 (1 partial fail: pm_webhook.py doesn't reload pm_messages.json into memory on restart)
retrospective: What worked: This was a duplicate QA spec (I sent two QA specs for the same build). Despite that, it found a new issue the first QA missed: pm_webhook.py doesn't reload messages from disk on startup, so the HTTP API shows empty post-restart even though the file has 30 messages. Good test isolation.  What caused friction: This spec should not have existed. I sent two QA specs for the same notification pipeline (eeda17f3 and 138ae9ea). That's wasted capacity. The second one found a slightly different issue but the overlap was 90%.  Spec improvement for next time: One QA spec per build. Period. If the first QA finds issues, fix them, then re-QA. Don't shotgun multiple QA specs at the same build hoping to catch more. It's wasteful and confusing.
tags: []
---

# QA: Fix PM Notification Pipeline - Task Completion Alerts (Respec v2)

## Objective
Independently verify that the PM notification pipeline is fully functional end-to-end. The build task (eeb79b73) reported all 6 acceptance criteria passing. This QA task must confirm each one independently, not trust the build task's self-reported results.

## Scope
- Verify all pipeline components are running and healthy
- Execute independent end-to-end tests for each acceptance criterion
- Report pass/fail for each criterion with evidence

## Success Criteria (test each independently)

1. **Task completion notification delivery**: Submit a test task to the A2A server (POST to localhost:9800), wait for it to complete, and confirm a `deliverable_ready` message arrives at the PM webhook (localhost:9802) within 60 seconds. Evidence: HTTP response from webhook showing the message was received.

2. **Notification content completeness**: Verify that delivered notifications contain: task_id, status, and result summary. Evidence: dump the message payload and confirm all three fields are present and non-empty.

3. **Blocking message surfacing**: Send a mock `question` type message to the A2A server's message broker and confirm it is surfaced to PM without manual polling. Evidence: check `~/.forge/pm_messages.json` for the message, confirm `requires_response=True`.

4. **Session restart survival**: Restart the PM webhook (kill PID, relaunch), then confirm previously stored messages are still accessible from SQLite/disk. Evidence: message count before and after restart matches.

5. **No claude -p spawning**: Grep all pipeline code (`server/pm_webhook.py`, `server/pm_message_hook.sh`, `server/message_broker.py`) for any `claude -p` invocations. Evidence: zero matches.

6. **pm.sh window ID capture**: Confirm `~/.forge/pm_session.json` exists, contains a valid `window_id` field, and that the window is reachable via osascript. Evidence: file contents and osascript return value.

## Constraints
- Do NOT modify any production code or configuration
- Do NOT restart the A2A server (localhost:9800/9801) -- it has active tasks
- The PM webhook runs on port 9802
- All tests must be non-destructive
- Machine: Haze (local dev machine, macOS)
- Working directory: ~/Projects/leroy/

## Do Not Do
- Do not modify server code
- Do not restart the A2A server
- Do not interfere with the active WORKING task (cdcd6165)
- Do not create permanent test fixtures

## Execution
Use agent teams. Decompose this into sub-tasks and delegate to specialist agents. Do not execute sequentially as a single agent.
---
## Outcome
**Task ID:** 138ae9ea-648d-4ba5-a73c-cdaffdfd3b33
**QA pass rate:** 5/6 (1 partial fail: pm_webhook.py doesn't reload pm_messages.json into memory on restart)

## Retrospective
What worked: This was a duplicate QA spec (I sent two QA specs for the same build). Despite that, it found a new issue the first QA missed: pm_webhook.py doesn't reload messages from disk on startup, so the HTTP API shows empty post-restart even though the file has 30 messages. Good test isolation.

What caused friction: This spec should not have existed. I sent two QA specs for the same notification pipeline (eeda17f3 and 138ae9ea). That's wasted capacity. The second one found a slightly different issue but the overlap was 90%.

Spec improvement for next time: One QA spec per build. Period. If the first QA finds issues, fix them, then re-QA. Don't shotgun multiple QA specs at the same build hoping to catch more. It's wasteful and confusing.
