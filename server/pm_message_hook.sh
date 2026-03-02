#!/bin/bash
# Claude Code PreToolUse hook for PM
# Checks ~/.forge/pm_messages.json for pending Leroy messages.
# Output is injected into Claude's context before the tool runs.
# Runs as shell command, NOT through the Bash tool -- bypasses --disallowedTools.
#
# Surfaces two categories:
#   1. Blocking messages (requires_response=True, not yet responded) -- always shown.
#   2. Recent deliverable_ready messages (last 10 minutes, not hook_shown) -- task
#      completions/failures that PM needs to act on (QA, review) without manual polling.

MESSAGES_FILE="$HOME/.forge/pm_messages.json"

[ -f "$MESSAGES_FILE" ] || exit 0

python3 - << 'PYEOF'
import json, sys, os
from datetime import datetime, timezone

messages_file = os.path.expanduser("~/.forge/pm_messages.json")
try:
    msgs = json.loads(open(messages_file).read())
except Exception:
    sys.exit(0)

TEN_MINUTES = 600

blocking = [
    m for m in msgs
    if m.get("requires_response") and not m.get("responded")
]

def _msg_age_seconds(m):
    ts = m.get("received_at") or m.get("timestamp", "")
    if not ts:
        return 9999
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return 9999

# Recent task completions not yet surfaced by this hook.
deliverables = [
    m for m in msgs
    if m.get("type") == "deliverable_ready"
    and not m.get("hook_shown")
    and _msg_age_seconds(m) < TEN_MINUTES
]

if not blocking and not deliverables:
    sys.exit(0)

if blocking:
    print(f"\n[LEROY MESSAGES PENDING - {len(blocking)} requiring your response]")
    for m in blocking[:5]:
        task = m.get("task_id", "unknown")[:24]
        msg_id = m.get("message_id", "")[:16]
        mtype = m.get("type", "?")
        content = m.get("content", "")[:300]
        print(f"\n  Type: {mtype} | Task: {task} | ID: {msg_id}...")
        print(f"  Message: {content}")
        if m.get("options"):
            print(f"  Options: {m['options']}")
    print("\n  Use leroy_read_messages and leroy_reply_to_message to respond before proceeding.")

if deliverables:
    print(f"\n[LEROY TASK COMPLETIONS - {len(deliverables)} task(s) finished]")
    for m in deliverables[:5]:
        task = m.get("task_id", "unknown")
        content = m.get("content", "")[:400]
        print(f"\n  Task: {task}")
        print(f"  {content}")
    print("\n  Use leroy_check_task('<task_id>') to review the result, then send a QA spec.")

    # Mark deliverables as hook_shown so they don't repeat on every tool call.
    shown_ids = {m["message_id"] for m in deliverables if m.get("message_id")}
    updated = False
    for m in msgs:
        if m.get("message_id") in shown_ids and not m.get("hook_shown"):
            m["hook_shown"] = True
            updated = True
    if updated:
        try:
            import pathlib
            p = pathlib.Path(messages_file)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(msgs, indent=2))
            tmp.replace(p)
        except Exception:
            pass  # non-fatal -- worst case it shows again on next tool call

PYEOF
