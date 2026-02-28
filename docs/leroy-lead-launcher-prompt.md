# Leroy Engineering Lead Launcher -- Code Prompt

## Objective

Create a launcher script for the Engineering Lead (Leroy) agent session in the leroy repo. This is the counterpart to pm.sh. While PM has restricted tools (brain + A2A only), the Engineering Lead has FULL tool access (bash, file edit, SSH, brain, A2A, everything). The persona and behavioral rules come from personas/engineering_lead.md and personas/shared_context.md.

## Context

The leroy repo is at ~/Projects/leroy/. pm.sh already exists and works. It uses --disallowedTools to hard-block code execution for PM sessions. The Engineering Lead needs the opposite: full tool access with a different persona loaded.

The problem: CLAUDE.md at the project root defines the PM persona and auto-loads when you run `claude` in this directory. The Engineering Lead is NOT the PM. We need a way to override the persona for Leroy sessions.

## Implementation

### Create leroy.sh

A launcher script at ~/Projects/leroy/leroy.sh that starts a Claude Code session with:

1. **Full tool access** -- no --disallowedTools restrictions. Leroy can bash, edit, write, SSH, everything.
2. **Engineering Lead persona** -- NOT the PM persona from CLAUDE.md. Load personas/engineering_lead.md as the system context.
3. **Shared context** -- Also load personas/shared_context.md for ecosystem knowledge.
4. **SDLC reference** -- Load sdlc/micro_sprint.md so Leroy knows the 15-step process.
5. **MCP servers** -- Same forge-brain and a2a as PM (already registered project-scoped).

### The Persona Override Problem

CLAUDE.md auto-loads and defines PM. We need Leroy sessions to load a DIFFERENT persona. Research how to accomplish this in Claude Code CLI. Options to investigate:

1. **--system-prompt flag** -- Can we pass a custom system prompt that overrides or supplements CLAUDE.md?
2. **--append-system-prompt flag** -- Can we append the Engineering Lead persona after CLAUDE.md loads?
3. **Environment variable** -- Is there a CLAUDE_SYSTEM_PROMPT or similar env var?
4. **Subdirectory with its own CLAUDE.md** -- Could we run `claude` from a subdirectory (e.g., ~/Projects/leroy/lead/) that has its own CLAUDE.md defining the Engineering Lead persona?
5. **--prompt flag with persona injection** -- Start the session with an initial prompt that establishes the Engineering Lead role.
6. **Pipe initial context** -- Echo the persona file content into the session start.

The subdirectory approach (option 4) is probably the cleanest if it works. Create ~/Projects/leroy/lead/ with its own CLAUDE.md that sources the engineering_lead.md content. When you `cd lead && claude`, it loads Leroy's persona instead of PM's.

Whatever approach you choose, the requirement is: when Brad runs ./leroy.sh, the session identifies as Engineering Lead (Leroy), NOT as PM.

### leroy.sh Content

```bash
#!/bin/bash
# Leroy -- Engineering Lead Session
# Full tool access, engineering persona

cd ~/Projects/leroy
# [whatever mechanism loads the Engineering Lead persona]
# [start claude with full tool access and correct persona]
```

Make it executable: `chmod +x leroy.sh`

### Validation

Start a Leroy session and test:

1. "Who are you?" -- Should identify as Engineering Lead (Leroy), NOT PM
2. "Run echo hello" -- Should execute successfully (full bash access)
3. "Query forge-brain memory_status" -- Should work (brain access)
4. "What's the micro-sprint SDLC?" -- Should know the 15-step process
5. "What's your decision authority?" -- Should describe what it can decide vs what needs PM approval

Also start a pm.sh session and verify it still identifies as PM, not Engineering Lead. Both must coexist without interference.

### Git Commit

```bash
git add leroy.sh [any other new files]
git commit -m "Add Engineering Lead (Leroy) launcher with full tool access"
```

## Success Criteria

1. ./leroy.sh starts a Claude Code session that identifies as Engineering Lead (Leroy)
2. Leroy has full tool access (bash, edit, write, SSH)
3. Leroy knows the 15-step micro-sprint SDLC
4. Leroy knows its decision authority boundaries
5. Leroy has access to forge-brain and a2a MCP servers
6. ./pm.sh still works correctly as PM (no interference)
7. Both can run simultaneously in separate terminals

## Constraints

- Do NOT modify pm.sh or its behavior
- Do NOT modify personas/*.md or sdlc/*.md content files
- Do NOT modify the root CLAUDE.md (that's PM's persona)
- If the subdirectory approach works, create ~/Projects/leroy/lead/CLAUDE.md but compose it FROM the existing persona files (reference or include them, don't duplicate content)
- The MCP servers are already registered project-scoped. They should work from subdirectories too. If not, register them for the subdirectory as well.

## Debug Until Working

If persona override doesn't work:
- Try multiple approaches from the list above
- Document what each approach does
- The minimum viable version: a session that loads engineering_lead.md content as its initial context, even if CLAUDE.md also loads. The Engineering Lead persona should be dominant.
- If nothing cleanly overrides CLAUDE.md, the subdirectory approach with its own CLAUDE.md is the fallback. That definitely works because CLAUDE.md loading is directory-based.

If MCP servers don't work from a subdirectory:
- Check if project-scoped MCP registration applies to subdirectories
- If not, register at the subdirectory level too
- Or run from the project root with a flag that changes persona

Get this working. Two terminals, two roles, same repo, same brain.
