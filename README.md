# Leroy

Autonomous engineering lead for the FORGE ecosystem. Receives product specs, decomposes them into executable tasks, dispatches work to AI agents via Claude CLI, enforces a full SDLC with mandatory QA, and reports results back to the PM with full traceability.

Named after Neil H. McElroy (father of product management at P&G, 1931) — with Leroy Jenkins energy.

## Architecture

```
┌─────────────┐     spec      ┌──────────────────┐    Claude CLI    ┌───────────┐
│  PM (Brad)  │──────────────▶│  Leroy A2A :9800 │────────────────▶│ Worktree  │
│  via MCP    │◀──────────────│  Health    :9801  │◀────────────────│ Subprocess│
└─────────────┘    results    └──────────────────┘    stdout/stderr └───────────┘
                                      │
                                      │ persist
                                      ▼
                               ┌──────────────┐
                               │ forge-brain  │
                               │ (Aianna)     │
                               │ kush:8300    │
                               └──────────────┘
```

**Three layers:**

1. **A2A Server** (port 9800) — Google's [Agent-to-Agent protocol](https://github.com/a2aproject/A2A) (Linux Foundation, Apache 2.0). Receives specs via JSON-RPC 2.0, manages task lifecycle, broadcasts state transitions.
2. **Claude CLI Executor** — Spawns `claude -p` subprocesses in isolated git worktrees. Graduated timeouts (5m grace → 15m warn → 30m kill). Captures structured results.
3. **MCP Client** — STDIO server that gives the PM 18 tools for spec submission, task management, messaging, analytics, and cost reporting.

## Task Lifecycle

```
NEW → ANALYZED → PLANNED → RUNNING → COMPLETED_UNVERIFIED → COMPLETED_VERIFIED → PERSISTED → ARCHIVED
                              │
                      FAILED_RETRYABLE → (retry or escalate)
                              │
                          ESCALATED
```

State transitions enforced by a state machine. Each transition fires event handlers that trigger QA proposals, failure routing, persistence, and PM notifications.

## Key Features

- **Spec slicing** — Large specs auto-decomposed into parallel vehicles with dependency constraints
- **Failure taxonomy** — Classifies failures (timeout, infra, scope, code error) and routes accordingly. Infrastructure failures bypass retry budget.
- **Three-tier PM autonomy** — HIGH confidence: auto-execute. MEDIUM: propose + 30-min auto-approve. LOW: escalate to human. Tiers expand/contract based on outcomes.
- **Knowledge governance** — Gate before persisting to brain: evaluates novelty, specificity, non-contradiction
- **Circuit breaker** — Persistence to forge-brain uses circuit breaker with local queue on outage
- **Concurrency control** — Priority queue with per-machine limits (haze: 3, kush: 1)
- **Real-time observability** — SSE streaming for state transitions and live task logs

## Quick Start

```bash
# Install dependencies
cd server && pip install -r requirements.txt
cd ../mcp && pip install -r requirements.txt

# Set required env vars
export FORGE_BRAIN_TOKEN="your-token"
export FORGE_BRAIN_URL="http://kush.local:8300/mcp"

# Run
python server/start_server.py

# Health check
curl http://127.0.0.1:9801/health
```

Or via launchd (macOS):
```bash
cp server/com.forge.leroy-a2a.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.forge.leroy-a2a.plist
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `LEROY_HOST` | 127.0.0.1 | Server bind address |
| `LEROY_PORT` | 9800 | A2A server port |
| `LEROY_HEALTH_PORT` | 9801 | Health check port |
| `LEROY_TASK_DB_PATH` | data/tasks.db | SQLite database |
| `FORGE_BRAIN_URL` | http://kush.local:8300/mcp | Aianna endpoint |
| `FORGE_BRAIN_TOKEN` | (empty) | Auth token for forge-brain |
| `LEROY_TASK_TIMEOUT` | 3600 | Max task runtime (seconds) |
| `LEROY_MAX_CONCURRENT_HAZE` | 3 | Concurrent task limit |

## MCP Tools (PM Interface)

| Tool | Purpose |
|------|---------|
| `leroy_send_spec` | Submit a spec for execution |
| `leroy_check_task` | Get full task status and result |
| `leroy_list_tasks` | Query tasks by status |
| `leroy_cancel_task` | Cancel a pending/running task |
| `leroy_read_messages` | Poll for pending PM messages |
| `leroy_reply_to_message` | Respond to decision gates |
| `leroy_tail_task` | Stream live task logs |
| `leroy_cost_report` | Token usage breakdown |
| `leroy_subsystem_health` | Per-subsystem pass rates |
| `leroy_plan_report` | Plan completion statistics |

## Project Structure

```
leroy/
├── server/
│   ├── server.py              # Entry point, route table, initialization
│   ├── execution.py           # Claude CLI subprocess executor
│   ├── server_state.py        # Shared state, broadcast helpers, auth
│   ├── config.py              # Environment-based configuration
│   ├── state_machine.py       # Task state machine (10 states)
│   ├── task_db.py             # SQLite persistence (WAL mode)
│   ├── retry_budget.py        # Retry limits with infra bypass
│   ├── failure_taxonomy.py    # Failure classification
│   ├── dispatcher.py          # Spec slicing into vehicles
│   ├── container_store.py     # Vehicle container persistence
│   ├── task_queue.py          # Priority queue with concurrency
│   ├── pm_autonomy.py         # Three-tier decision confidence
│   ├── task_events.py         # Event handlers on transitions
│   ├── persist_manager.py     # Async brain persistence + circuit breaker
│   ├── agent_bus.py           # Agent-to-agent messaging
│   ├── task_analytics.py      # Scoring and validation
│   ├── knowledge_governance.py# Pre-persist quality gate
│   ├── routes_tasks.py        # Task CRUD endpoints
│   ├── routes_messages.py     # PM messaging endpoints
│   ├── routes_ops.py          # Ops, agents, proposals, plans
│   └── routes_admin.py        # Health, circuit breaker, hooks
├── mcp/
│   ├── leroy_client.py        # MCP STDIO server (PM tools)
│   └── requirements.txt
├── data/
│   └── tasks.db               # SQLite task database
└── content/
    └── logs/                  # Task logs, persist queue
```

## Dependencies

**Server:** `a2a-sdk[http-server]`, `uvicorn`, `starlette`, `httpx`, `mcp[cli]`

**MCP Client:** `fastmcp`, `httpx`

## Part of FORGE

Leroy is one component of the [FORGE ecosystem](https://github.com/thebeedubya/forge-ecosystem) — an AI-native development platform where agents communicate via Google's A2A protocol and persist knowledge to a shared brain (Qdrant + Neo4j + PostgreSQL).

## License

MIT
