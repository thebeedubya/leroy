---
spec_id: flush-2-stale-test-messages-from-pm-queue-retroove
task_id: e6f0e787-6b19-466b-a9e3-747f282981b4
date: 2026-03-01
status: sent
pass_rate: (pending)
retrospective: (pending)
tags: []
---

# Flush Stale Test Messages from Leroy Message Queue

## Objective
Two test messages from notification pipeline testing are sitting in the pending queue, cluttering PM's inbox. Remove them.

## Scope
- Delete or mark as resolved message 512c817cee944220a6d3ae2bb81bd0d1 (decision gate: "Deploy to staging or production first?" from task monitor-qa-2)
- Delete or mark as resolved message 511ac2a5a51b47f6a6d09f0fdf7e017d (question: "Monitor test: should I use Redis?" from task monitor-test-1)
- These are test artifacts, not real work. They should not appear in leroy_read_messages(pending_only=True) after this task.

## Success Criteria
1. leroy_read_messages(pending_only=True) returns zero pending messages after cleanup
2. No real messages or tasks are affected

## Constraints
- Do NOT modify any message handling code. This is a data cleanup, not a code change.
- Only touch these two specific message IDs.
- Check the SQLite database at ~/Projects/leroy/data/tasks.db for where messages are stored.

## Machine Context
- Task DB: ~/Projects/leroy/data/tasks.db
- Leroy server: localhost:9800

## Budget
Simple. 5 minutes max.