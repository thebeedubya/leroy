#!/bin/bash
# Ops -- Infrastructure & Agent Maintenance Session
# Full tool access, ops persona
# --system-prompt bypasses root CLAUDE.md (PM persona)
# --settings loads Ops-specific permissions (full tool access)
# Surgeons don't operate on themselves. Ops configures PM and Leroy.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Compose system prompt from persona files
PERSONA=$(cat "$SCRIPT_DIR/personas/ops.md")
SHARED=$(cat "$SCRIPT_DIR/personas/shared_context.md")

SYSTEM_PROMPT="$PERSONA

---

$SHARED"

exec claude \
  --system-prompt "$SYSTEM_PROMPT" \
  --settings .claude/ops-settings.json
