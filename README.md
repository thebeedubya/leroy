# Leroy

Agent orchestration and SDLC enforcement for the FORGE compound intelligence ecosystem.

Named after Neil H. McElroy, father of product management (P&G, 1931). Also Leroy Jenkins energy: charge in and execute.

## What Leroy Does

Leroy sits between PM (Product Manager) and the engineering workforce. PM writes specs. Leroy decomposes them into tasks, assigns workforce agents, enforces a 15-step micro-sprint SDLC, runs mandatory QA, and reports results back to PM with budget tracking and acceptance criteria traceability.

## Architecture

```
Brad (Operator)
  |
  v
PM (CLI Claude -- this repo's CLAUDE.md)
  - Strategy, specs, reviews, memory ownership
  - Tools: forge-brain, A2A, web search
  - Cannot execute code
  |
  v
Leroy (Engineering Lead -- Claude Code, Architect pattern)
  - Task decomposition, SDLC enforcement, workforce management
  - Tools: all (bash, file edit, SSH, plus brain and A2A)
  - Does NOT do deep implementation (delegates to workforce)
  |
  v
Workforce (Subagents)
  - Builders (implementation)
  - QA (pytest + Playwright, mandatory)
  - Evaluators (multi-candidate scoring)
  - Specialists (domain-specific profiles)
```

## Communication Protocol

PM and Leroy communicate via **A2A** (Google Agent-to-Agent protocol). A2A provides:
- Task lifecycle: submitted -> working -> input_required -> completed/failed
- Multi-turn conversations for escalation chains
- Artifacts for deliverables (code, test results, budget reports)
- Push notifications for decision gates

Both PM and Leroy use **Aianna** (forge-brain MCP) for persistent memory.

## Repository Structure

```
leroy/
  CLAUDE.md                 # PM persona (loads automatically in CLI)
  README.md                 # This file
  .claude/
    settings.json           # MCP config (brain + A2A only for PM)
  server/                   # Leroy A2A server (Python)
  mcp/                      # MCP tool wrappers
  personas/
    pm.md                   # PM behavioral rules and knowledge
    engineering_lead.md     # Leroy behavioral rules and SDLC
    shared_context.md       # FORGE ecosystem context
  sdlc/
    micro_sprint.md         # 15-step sprint template
    escalation.md           # Three-tier escalation rules
    qa_requirements.md      # QA standards and test-first principle
  app/                      # Future: native macOS PM app (SwiftUI)
  docs/
    architecture.md         # System architecture documentation
    setup.md                # Setup and configuration guide
```

## Quick Start

### Running as PM (no code execution)
```bash
cd leroy
claude  # CLAUDE.md loads automatically, restricts to PM role
```

### Running as Engineering Lead (full tool access)
```bash
cd leroy
claude --profile engineering-lead  # Different config, full tools
```

## Status

- [x] PM persona and constraints (CLAUDE.md)
- [x] Engineering Lead persona (personas/engineering_lead.md)
- [x] Shared ecosystem context (personas/shared_context.md)
- [x] Micro-sprint SDLC (sdlc/micro_sprint.md)
- [x] Escalation rules (sdlc/escalation.md)
- [x] QA requirements (sdlc/qa_requirements.md)
- [ ] Leroy A2A server (server/)
- [ ] PM-side A2A MCP tools (mcp/)
- [ ] PM proxy guard (code execution blocker)
- [ ] Native macOS PM app (app/)
- [ ] Architecture documentation (docs/)
- [ ] Setup guide (docs/)

## Build Order

1. **A2A server** -- Leroy's brain. Task lifecycle, SDLC state machine, workforce dispatch.
2. **MCP tools** -- Thin A2A client wrappers so PM and Code can talk to the server.
3. **Proxy guard** -- Hard blocker preventing PM sessions from executing code.
4. **Native macOS app** -- SwiftUI wrapper around CLI with Agent SDK. Xcode 26.3.
