#!/bin/bash
# Leroy -- Engineering Lead Session
# Full tool access, engineering persona
# --system-prompt bypasses root CLAUDE.md (PM persona)
# --settings loads Leroy-specific permissions (full tool access)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Compose system prompt from persona files
PERSONA=$(cat "$SCRIPT_DIR/personas/engineering_lead.md")
SHARED=$(cat "$SCRIPT_DIR/personas/shared_context.md")
SDLC=$(cat "$SCRIPT_DIR/sdlc/micro_sprint.md")

SYSTEM_PROMPT="$PERSONA

---

$SHARED

---

$SDLC"

exec claude \
  --system-prompt "$SYSTEM_PROMPT" \
  --settings .claude/leroy-settings.json
