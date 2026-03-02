#!/bin/bash
# Launch PM session with hard tool restrictions
# --disallowedTools removes tools from the model's context entirely (hard deny)
# Combined with .claude/settings.json deny rules (soft deny) for defense in depth
#
# Webhook sidecar removed 2026-03-02: dashboard is the notification layer now.
cd "$(dirname "$0")"

exec claude \
  --disallowedTools "Bash" "Edit" "MultiEdit" "NotebookEdit"
