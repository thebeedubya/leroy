#!/bin/bash
# Content Agent -- Autonomous Daily Media Pipeline
# Runs headless via launchd at 6 AM CST for daily content generation
# --system-prompt bypasses root CLAUDE.md (PM persona)
# --settings loads Content Agent-specific permissions (forge-brain, write access)
# -p flag: non-interactive print mode, reads from stdin, processes once, exits

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Compose system prompt from persona files
PERSONA=$(cat "$SCRIPT_DIR/personas/content_agent.md")
SHARED=$(cat "$SCRIPT_DIR/personas/shared_context.md")

SYSTEM_PROMPT="$PERSONA

---

$SHARED"

# Resolve claude binary (installed to ~/.local/bin by default)
CLAUDE_BIN="${CLAUDE_BIN:-$(command -v claude 2>/dev/null || echo "$HOME/.local/bin/claude")}"

exec "$CLAUDE_BIN" \
  --system-prompt "$SYSTEM_PROMPT" \
  --settings .claude/content-settings.json \
  -p "Run the daily content pipeline. Execute all steps: query Aianna, filter to yesterday, score angles, generate drafts, open PR if post-worthy content exists."
