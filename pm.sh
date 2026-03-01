#!/bin/bash
# Launch PM session with hard tool restrictions
# --disallowedTools removes tools from the model's context entirely (hard deny)
# Combined with .claude/settings.json deny rules (soft deny) for defense in depth
#
# Also starts the PM webhook sidecar (server/pm_webhook.py) on port 9802.
# The sidecar registers ~/.forge/pm_webhook.json so the Leroy A2A server
# can forward messages and trigger macOS desktop notifications.
cd "$(dirname "$0")"

# Capture the Terminal window ID so pm_webhook.py can target it for keystroke
# injection when Leroy sends blocking messages.
# Window IDs are stable for the lifetime of the window, unlike titles which
# Claude Code overwrites via ANSI escapes.
PM_WINDOW_ID=$(osascript -e 'tell application "Terminal" to get id of front window' 2>/dev/null || echo "")
if [ -n "$PM_WINDOW_ID" ]; then
    mkdir -p ~/.forge
    printf '{"window_id": %s, "pid": %s, "started_at": "%s"}\n' \
        "$PM_WINDOW_ID" "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        > ~/.forge/pm_session.json
fi

# Start PM webhook sidecar in background
server/.venv/bin/python3 server/pm_webhook.py &
PM_WEBHOOK_PID=$!

# Give sidecar a moment to register before PM session starts
sleep 1

# Launch Claude (not exec -- we need the cleanup trap to fire on exit)
claude \
  --disallowedTools "Bash" "Edit" "MultiEdit" "NotebookEdit" "WebFetch" "WebSearch"
EXIT_CODE=$?

# Clean up webhook sidecar when Claude exits
if [ -n "$PM_WEBHOOK_PID" ]; then
    kill "$PM_WEBHOOK_PID" 2>/dev/null
    wait "$PM_WEBHOOK_PID" 2>/dev/null
fi

exit $EXIT_CODE
