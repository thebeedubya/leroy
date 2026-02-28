# Shared Context -- FORGE Ecosystem

## What is FORGE?

FORGE (Framework for Orchestrated Recursive Generative Engineering) is a compound intelligence platform built by Brad Wood. It is Brad's personal IP, separate from his day job at Addium.

FORGE's thesis: product lifecycle management that learns from customer usage patterns to proactively suggest new applications, creating a feedback loop where every customer's usage makes the system smarter for all customers.

The business model: SaaS platform subscriptions and per-app build fees. Solving business problems, not technical ones.

## Architecture Overview

```
Brad (Operator)
  |
  v
PM (Claude Desktop / CLI)
  - Strategy, specs, reviews, memory ownership
  |
  v
Leroy (Engineering Lead -- Claude Code, Architect pattern)
  - Task decomposition, SDLC enforcement, workforce management
  |
  v
Workforce (Subagents)
  - Builders, QA, Evaluators, Specialists
```

### Core Services

| Service | Role | Location |
|---------|------|----------|
| Aianna (forge-brain) | Memory, knowledge, learning fabric | Kush (192.168.1.100:8000) |
| Leroy | Agent orchestration, SDLC enforcement | Haze (this repo) |
| Sentinel | Infrastructure monitoring, health checks | Kush |
| A2A Gateway | Agent-to-agent communication | CloudRaider (155.138.199.82:8443) |
| Qdrant | Vector database for brain storage | Kush (192.168.1.100:6333) |

### Communication Protocols

- **MCP (Model Context Protocol)**: Agent-to-tools. How agents access brain, sentinel, and other services.
- **A2A (Agent-to-Agent, Google standard)**: Agent-to-agent. How PM talks to Leroy, how FORGE talks to APEX.
- **HTTP+SSE / Streamable HTTP**: Transport layer for MCP servers. Dual transport active on forge-brain.
- **mTLS**: Certificate-based authentication for A2A gateway connections.

### Source Tagging

Every piece of data in the brain has provenance:
- `claude-desktop`: PM conversations
- `claude-code` / `codex/haze`: Engineering execution
- `chatgpt-import`: Historical imports
- `claude-legacy`: Pre-tagging conversations

Source tags enable targeted queries and data lineage. Always tag. Always attribute.

## Key People

### Brad Wood (Operator)
VP of Sales and Revenue Operations at Addium (PE-backed cannabis cultivation technology). Builds FORGE as personal IP. Thinks in business outcomes. Zero tolerance for incomplete work or AI slop. Final authority on all decisions.

### Carric (External Partner)
Senior infrastructure architect. Manages multi-tenant SOC operations on APEX. 14 months Qdrant production experience. Built 43-agent architecture on CLI Claude. Connected via A2A gateway. Trusted technical advisor.

### Codex (Engineering Agent)
Claude Code instance on Haze. Handles engineering execution. Source tag: codex/haze. Wrote formal arrival note to brain with provenance and working norms.

## Principles

1. **Battle-tested over theoretical.** Working implementations over elegant designs. If it doesn't run, it doesn't count.

2. **Memory is non-negotiable.** Persist after every significant exchange. Minimum 1500 characters. Include specifics: names, numbers, dates, reasoning. The brain is institutional memory. Treat it like the company database.

3. **Source tag everything.** Every persist call, every data write, every import has provenance. No orphan data.

4. **Three attempts then escalate.** No agent, no level, no approach loops forever. Three strikes and you go up the chain. The cost of escalating is always less than the cost of grinding.

5. **Complete or don't ship.** Never send Brad back with partial fixes. Never send Leroy out with partial specs. Everything is finished or it's not delivered.

6. **PM owns WHAT, Engineering owns HOW.** Clean separation. PM writes requirements. Engineering chooses implementation. Neither crosses the line.

7. **QA is not optional.** Every sprint has a QA agent. pytest for unit/integration. Playwright for E2E. Tests written from the spec before seeing build output.

8. **Zero chatbot slop.** No filler. No preamble. No "Great question!" No unnecessary hedging. Say what you mean, do what you say.

## Current State

Refer to FORGE-STATE in the brain (`get_forge_state`) for the live snapshot of active projects, infrastructure status, pending decisions, and next actions. This document provides stable context. FORGE-STATE provides current state.
