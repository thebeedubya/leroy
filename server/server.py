"""Leroy A2A Server.

Google A2A protocol server for PM-to-Leroy task lifecycle.
When a spec arrives via A2A, spawns `claude -p` to execute it automatically.
Custom endpoints for task status and management.
Separate health server on HEALTH_PORT.
"""
import asyncio
import hashlib
import json
import logging
import os
import selectors
import signal
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    AgentCard,
    AgentSkill,
    AgentCapabilities,
    HTTPAuthSecurityScheme,
    SecurityScheme,
)
from a2a.utils import new_agent_text_message

import config
import auth
import persist_manager as pm
import message_broker as broker  # legacy, kept for backward compat during migration
import agent_bus
import task_db
from state_machine import TaskStateMachine, TaskState, IllegalTransitionError
from failure_taxonomy import classify_failure, FailureCategory, is_infra_failure, INFRA_CATEGORIES
from retry_budget import RetryBudget

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log_level = os.environ.get("LEROY_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    stream=sys.stderr,
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("leroy-a2a")

# ---------------------------------------------------------------------------
# Task storage (A2A SDK store + persistent custom metadata)
# ---------------------------------------------------------------------------
_task_store = InMemoryTaskStore()
_START_TIME = time.time()

# Custom task metadata: task_id -> {spec, status, result, created_at, ...}
# Backed by SQLite via task_db -- survives server restarts.
# Initialized in main() before server starts.
_task_meta: task_db.PersistentTaskDict | None = None  # set in main()

# Sub-task tracking: task_id -> list of subtask dicts
# Also backed by SQLite via task_db.
_subtask_store: task_db.PersistentSubtaskStore | None = None  # set in main()

# Agent registry and activity event store -- set in main()
_agent_store: task_db.AgentStore | None = None
_activity_store: task_db.ActivityStore | None = None

# v2 State machine + retry budget -- set in main()
_state_machine: TaskStateMachine | None = None
_retry_budget: RetryBudget | None = None

# SSE subscribers: set of asyncio.Queue instances for broadcasting task updates
_sse_subscribers: set = set()
_sse_lock = asyncio.Lock()

# SSE subscribers for activity stream: set of asyncio.Queue instances
_activity_sse_subscribers: set = set()

# ---------------------------------------------------------------------------
# Hook event storage (Claude Code hook receivers)
# ---------------------------------------------------------------------------
_HOOK_EVENTS_MAX = 5000
_hook_events: list[dict] = []  # global ring buffer, capped at _HOOK_EVENTS_MAX
_task_hook_events: dict[str, list[dict]] = {}  # task_id -> list of hook events
_session_to_task: dict[str, str] = {}  # session_id -> task_id mapping
_hook_sse_subscribers: list[asyncio.Queue] = []  # SSE subscribers for hook event stream

async def _broadcast_task_update(task_id: str) -> None:
    """Broadcast a task update to all SSE subscribers."""
    if not _sse_subscribers:
        return
    task = _task_meta.get(task_id)
    if not task:
        return
    event_data = json.dumps({"type": "task_update", "task": dict(task)})
    dead = set()
    for queue in list(_sse_subscribers):
        try:
            queue.put_nowait(event_data)
        except asyncio.QueueFull:
            dead.add(queue)
    for q in dead:
        _sse_subscribers.discard(q)


def _broadcast_task_update_sync(task_id: str) -> None:
    """Thread-safe broadcast from sync context."""
    if not _sse_subscribers:
        return
    task = _task_meta.get(task_id)
    if not task:
        return
    event_data = json.dumps({"type": "task_update", "task": dict(task)})
    dead = set()
    for queue in list(_sse_subscribers):
        try:
            queue.put_nowait(event_data)
        except Exception:
            dead.add(queue)
    for q in dead:
        _sse_subscribers.discard(q)

def _emit_activity(agent: str, event_type: str, summary: str,
                   detail: str | None = None, task_id: str | None = None,
                   severity: str = "info") -> None:
    """Emit an activity event and broadcast to SSE subscribers."""
    if _activity_store is None:
        return
    evt = _activity_store.append(agent, event_type, summary, detail, task_id, severity)
    evt_data = json.dumps({"type": "activity_event", "event": evt})
    dead = set()
    for queue in list(_activity_sse_subscribers):
        try:
            queue.put_nowait(evt_data)
        except Exception:
            dead.add(queue)
    for q in dead:
        _activity_sse_subscribers.discard(q)


# Persistence manager -- persists task completions to Aianna (forge-brain)
_persist_manager = pm.PersistenceManager()


# ---------------------------------------------------------------------------
# Claude CLI execution engine
# ---------------------------------------------------------------------------
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", shutil.which("claude") or "claude")
WORK_DIR = os.environ.get("LEROY_WORK_DIR", str(Path(__file__).parent.parent))
MAX_TASK_TIMEOUT = int(os.environ.get("LEROY_TASK_TIMEOUT", "3600"))  # 1 hour default
LOGS_DIR = Path(WORK_DIR) / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Stuck task detection settings
_STUCK_CHECK_INTERVAL = 60  # seconds between checks
_STUCK_THRESHOLD = 120  # seconds after all subtasks done before flagging as stuck
_active_pids: dict[str, int] = {}  # task_id -> subprocess PID for liveness checks

# v2 Phase 1: Graduated timeout thresholds (replace flat inactivity timeout)
_GRADUATED_GRACE_MINUTES = 5     # 0-5 min: no warnings
_GRADUATED_WARN_MINUTES = 15     # 5-15 min: warn in metadata + SSE
_GRADUATED_KILL_MINUTES = 30     # 15-30 min: kill with partial capture
_PARTIAL_SNAPSHOT_INTERVAL = 60  # seconds between partial output snapshots

# System prompt injected into every claude -p invocation
LEROY_SYSTEM_PROMPT = """You are Leroy, the Engineering Lead for the FORGE ecosystem.
You receive specs from PM and execute them. You have full tool access.
Execute the spec completely. Return a structured result with:
- What was done
- Files created/modified
- Success criteria pass/fail
- Any issues encountered
Be thorough but concise. No filler.

BUILDER OUTPUT DISCIPLINE:
1. Before producing any deliverable, output three sections:
   [WHAT] Restate the task criteria in your own words.
   [REASONING] Explain your approach, assumptions, and tradeoffs.
   [OUTPUT] Then produce the actual deliverable.

2. Every 60 seconds of work, output: [PROGRESS] Working on: {current step}
   This resets the inactivity timer. Silence kills your session.

3. If blocked on something outside your control (service down, missing credentials,
   missing file), output: [BLOCKED] {description of what you need}
   This notifies the PM immediately.

4. Do not claim completion unless ALL criteria in the spec are addressed.
   If you cannot complete a criterion, explicitly state which ones and why.

When you need to communicate with PM during task execution, use the agent message bus:

  POST http://127.0.0.1:9800/messages
  Content-Type: application/json
  Body: {
    "from": "leroy",
    "to": "pm",
    "type": "question|status_update|decision_gate|blocker|deliverable_ready",
    "task_id": "<your LEROY_TASK_ID env var>",
    "content": "your message text",
    "context": "relevant background for PM",
    "requires_response": true|false
  }
  Returns: {"message_id": "...", "status": "queued"}

You can also message Ops for infrastructure requests:
  {"from": "leroy", "to": "ops", "type": "request", "content": "restart dashboard"}

Message types:
- status_update: non-blocking progress report, continues immediately
- deliverable_ready: non-blocking notification that work is ready for review
- question: BLOCKING -- wait for PM response before continuing
- decision_gate: BLOCKING -- PM picks from options before you continue
- blocker: BLOCKING -- you cannot proceed without PM input

For BLOCKING messages, after POSTing, poll for the response:
  GET http://127.0.0.1:9800/messages/{message_id}/response
  Poll every 5 seconds. Max wait 10 minutes.
  Returns: {"status": "pending"} or {"status": "answered", "response": "..."}

Your task_id is in the LEROY_TASK_ID environment variable.
Use it in every message so PM can route responses correctly.

Sub-task reporting: When you decompose work into sub-tasks and delegate to specialist agents, report each sub-task to the server so the dashboard can show execution progress:

  POST http://127.0.0.1:9800/tasks/{task_id}/subtasks
  Content-Type: application/json
  Body: {
    "subtask_id": "unique-id-for-this-subtask",
    "name": "What this subtask does (concise description)",
    "agent": "agent type (e.g. general-purpose, Explore, Plan)",
    "status": "running",
    "started_at": "<ISO timestamp>"
  }
  No auth required. Use the value of your LEROY_TASK_ID env var as {task_id}.
  POST when subtask starts, POST again when done:
  Body update: {"subtask_id": "same-id", "name": "same", "status": "completed", "output": "result summary", "completed_at": "<ISO timestamp>"}
  Use status "failed" if the subtask fails."""

# v2 Phase 3: Builder prompt version hash (computed once at module load)
_BUILDER_PROMPT_VERSION = hashlib.sha256(LEROY_SYSTEM_PROMPT.encode()).hexdigest()[:16]


def _setup_worktree(task_id: str) -> tuple[str | None, str | None]:
    """Create a git worktree for builder isolation. Returns (worktree_path, branch_name) or (None, None)."""
    branch_name = f"task/{task_id[:8]}"
    worktree_path = os.path.join(WORK_DIR, ".claude", "worktrees", task_id)
    try:
        os.makedirs(os.path.dirname(worktree_path), exist_ok=True)
        subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, worktree_path],
            cwd=WORK_DIR, check=True, capture_output=True, text=True, timeout=30,
        )
        logger.info("Task %s: worktree created at %s (branch %s)", task_id, worktree_path, branch_name)
        return worktree_path, branch_name
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning("Task %s: worktree creation failed (%s), falling back to project root", task_id, e)
        return None, None


def _cleanup_worktree(task_id: str, worktree_path: str | None, success: bool) -> None:
    """Clean up worktree after task. On success: preserve for review. On failure: remove."""
    if not worktree_path:
        return
    if success:
        logger.info("Task %s: preserving worktree at %s for review", task_id, worktree_path)
        return
    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", worktree_path],
            cwd=WORK_DIR, capture_output=True, text=True, timeout=30,
        )
        logger.info("Task %s: worktree cleaned up", task_id)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning("Task %s: worktree cleanup failed: %s", task_id, e)


def _parse_token_usage(output: str) -> dict | None:
    """Parse token usage from claude -p output. Returns {input, output, estimated_cost_usd} or None."""
    import re
    input_match = re.search(r"Input tokens:\s*([\d,]+)", output)
    output_match = re.search(r"Output tokens:\s*([\d,]+)", output)
    if input_match and output_match:
        input_tokens = int(input_match.group(1).replace(",", ""))
        output_tokens = int(output_match.group(1).replace(",", ""))
        # Sonnet pricing: $3/MTok input, $15/MTok output
        cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000
        return {"input": input_tokens, "output": output_tokens, "estimated_cost_usd": round(cost, 4)}
    return None


def _get_graduated_timeout(task_id: str) -> int:
    """Get the kill timeout in seconds. Spec override > graduated default."""
    meta = _task_meta.get(task_id) or {}
    override = meta.get("inactivity_timeout")
    if override is not None:
        try:
            return int(override) * 60  # override is in minutes
        except (ValueError, TypeError):
            pass
    return _GRADUATED_KILL_MINUTES * 60


def _run_claude_sync(task_id: str, spec: str) -> None:
    """Run claude -p in a subprocess with real-time log streaming.

    v2 Phase 1: partial output snapshots, [PROGRESS]/[BLOCKED] heartbeat parsing,
    graduated inactivity timeout, git worktree isolation, FD cleanup, token capture.
    """
    log_file = LOGS_DIR / f"{task_id}.log"
    kill_timeout = _get_graduated_timeout(task_id)
    logger.info("Task %s: spawning claude -p (kill_timeout=%ds, log=%s)", task_id, kill_timeout, log_file)

    # v2: transition NEW -> RUNNING
    if _state_machine:
        try:
            _state_machine.transition(task_id, TaskState.RUNNING, reason="builder_launched")
        except (IllegalTransitionError, KeyError) as e:
            logger.warning("v2 transition to RUNNING failed for %s: %s", task_id, e)
            _task_meta[task_id]["status"] = "working"
    else:
        _task_meta[task_id]["status"] = "working"
    _task_meta[task_id]["log_file"] = str(log_file)
    _task_meta[task_id]["last_activity"] = datetime.now(timezone.utc).isoformat()
    _broadcast_task_update_sync(task_id)

    # v2 Phase 1: worktree isolation
    worktree_path, branch_name = _setup_worktree(task_id)
    cwd = worktree_path or WORK_DIR
    if worktree_path:
        _task_meta[task_id]["worktree_path"] = worktree_path
        _task_meta[task_id]["worktree_branch"] = branch_name

    proc = None
    task_success = False
    try:
        with open(log_file, "w") as lf:
            lf.write(f"=== Task {task_id} started at {datetime.now(timezone.utc).isoformat()} ===\n")
            lf.write(f"=== Spec length: {len(spec)} chars ===\n")
            if worktree_path:
                lf.write(f"=== Worktree: {worktree_path} (branch: {branch_name}) ===\n")
            lf.write("\n")
            lf.flush()

            proc = subprocess.Popen(
                [
                    CLAUDE_BIN,
                    "-p", spec,
                    "--output-format", "text",
                    "--system-prompt", LEROY_SYSTEM_PROMPT,
                    "--dangerously-skip-permissions",
                    "--no-session-persistence",
                    "--model", "sonnet",
                    "--setting-sources", "user",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=cwd,
                env={k: v for k, v in os.environ.items() if k != "CLAUDECODE"} | {
                    "CLAUDE_CODE_ENTRYPOINT": "leroy-a2a",
                    "LEROY_TASK_ID": task_id,
                    "ENABLE_TOOL_SEARCH": "true",
                },
                start_new_session=True,
            )
            _active_pids[task_id] = proc.pid
            logger.info("Task %s: claude PID %d (cwd=%s)", task_id, proc.pid, cwd)

            stdout_lines = []
            stderr_lines = []
            last_snapshot_time = time.time()
            last_activity_time = time.time()
            task_start_time = time.time()
            warned_inactivity = False
            builder_sections: dict[str, str] = {}  # WHAT, REASONING, OUTPUT

            sel = selectors.DefaultSelector()
            sel.register(proc.stdout, selectors.EVENT_READ, "stdout")
            sel.register(proc.stderr, selectors.EVENT_READ, "stderr")

            deadline = time.time() + MAX_TASK_TIMEOUT
            open_streams = 2

            while open_streams > 0:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(CLAUDE_BIN, MAX_TASK_TIMEOUT)

                events = sel.select(timeout=min(remaining, 5.0))

                now = time.time()

                # v2 Phase 1: partial output snapshot every 60s
                if now - last_snapshot_time >= _PARTIAL_SNAPSHOT_INTERVAL:
                    partial = "".join(stdout_lines)
                    if partial:
                        _task_meta[task_id]["partial_result"] = partial[-10000:]  # last 10k chars
                    last_snapshot_time = now

                # v2 Phase 1: graduated inactivity timeout
                inactivity = now - last_activity_time
                elapsed_minutes = (now - task_start_time) / 60

                if elapsed_minutes > _GRADUATED_GRACE_MINUTES:
                    if inactivity > kill_timeout:
                        # Kill with partial capture
                        logger.warning(
                            "Task %s: inactivity timeout (%ds since last output, %d min elapsed)",
                            task_id, int(inactivity), int(elapsed_minutes),
                        )
                        raise subprocess.TimeoutExpired(CLAUDE_BIN, int(inactivity))

                    if not warned_inactivity and inactivity > _GRADUATED_WARN_MINUTES * 60:
                        warned_inactivity = True
                        _task_meta[task_id]["inactivity_warning"] = datetime.now(timezone.utc).isoformat()
                        _broadcast_task_update_sync(task_id)
                        logger.warning("Task %s: inactivity warning at %d min", task_id, int(inactivity / 60))

                if not events:
                    if proc.poll() is not None:
                        # Process exited -- drain remaining output
                        for _ in range(10):
                            drain = sel.select(timeout=0.5)
                            if not drain:
                                break
                            for dk, _ in drain:
                                line = dk.fileobj.readline()
                                if not line:
                                    sel.unregister(dk.fileobj)
                                    open_streams -= 1
                                    continue
                                last_activity_time = time.time()
                                _task_meta[task_id]["last_activity"] = datetime.now(timezone.utc).isoformat()
                                if dk.data == "stdout":
                                    stdout_lines.append(line)
                                    lf.write(line)
                                    lf.flush()
                                else:
                                    stderr_lines.append(line)
                                    lf.write(f"[STDERR] {line}")
                                    lf.flush()
                        break
                    continue

                for key, _ in events:
                    line = key.fileobj.readline()
                    if not line:
                        sel.unregister(key.fileobj)
                        open_streams -= 1
                        continue

                    last_activity_time = time.time()
                    warned_inactivity = False  # reset warning on activity
                    _task_meta[task_id]["last_activity"] = datetime.now(timezone.utc).isoformat()

                    if key.data == "stdout":
                        stdout_lines.append(line)
                        lf.write(line)
                        lf.flush()

                        # v2 Phase 1: parse heartbeat markers
                        stripped = line.strip()
                        if stripped.startswith("[PROGRESS]"):
                            progress_msg = stripped[len("[PROGRESS]"):].strip()
                            _task_meta[task_id]["last_progress"] = progress_msg
                            _task_meta[task_id]["last_progress_at"] = datetime.now(timezone.utc).isoformat()
                            logger.debug("Task %s: [PROGRESS] %s", task_id, progress_msg)

                        elif stripped.startswith("[BLOCKED]"):
                            block_msg = stripped[len("[BLOCKED]"):].strip()
                            _task_meta[task_id]["blocked_reason"] = block_msg
                            logger.warning("Task %s: [BLOCKED] %s", task_id, block_msg)
                            if _state_machine:
                                try:
                                    _state_machine.transition(task_id, TaskState.BLOCKED,
                                                              reason=f"builder_blocked: {block_msg}")
                                except (IllegalTransitionError, KeyError) as e:
                                    logger.warning("v2 BLOCKED transition failed: %s", e)

                        elif stripped.startswith("[WHAT]"):
                            builder_sections["what"] = stripped[len("[WHAT]"):].strip()
                        elif stripped.startswith("[REASONING]"):
                            builder_sections["reasoning"] = stripped[len("[REASONING]"):].strip()
                        elif stripped.startswith("[OUTPUT]"):
                            builder_sections["output"] = stripped[len("[OUTPUT]"):].strip()
                    else:
                        stderr_lines.append(line)
                        lf.write(f"[STDERR] {line}")
                        lf.flush()

                # Check if main process exited (orphan pipe guard)
                if open_streams > 0 and proc.poll() is not None:
                    for _ in range(10):
                        drain = sel.select(timeout=0.5)
                        if not drain:
                            break
                        for dk, _ in drain:
                            line = dk.fileobj.readline()
                            if not line:
                                sel.unregister(dk.fileobj)
                                open_streams -= 1
                                continue
                            last_activity_time = time.time()
                            _task_meta[task_id]["last_activity"] = datetime.now(timezone.utc).isoformat()
                            if dk.data == "stdout":
                                stdout_lines.append(line)
                                lf.write(line)
                                lf.flush()
                            else:
                                stderr_lines.append(line)
                                lf.write(f"[STDERR] {line}")
                                lf.flush()
                    logger.info(
                        "Task %s: main process exited (rc=%d) with %d pipe stream(s) still open. Stopping reader.",
                        task_id, proc.returncode, open_streams,
                    )
                    lf.write(
                        f"\n=== Main process exited rc={proc.returncode}, "
                        f"{open_streams} pipe stream(s) still open -- stopping reader ===\n"
                    )
                    lf.flush()
                    break

            sel.close()

            # v2 Phase 1: explicit FD cleanup to prevent orphan pipe hangs
            for fd in (proc.stdout, proc.stderr):
                try:
                    fd.close()
                except Exception:
                    pass

            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                logger.warning("Task %s: proc.wait() timed out -- killing", task_id)
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass

            stdout = "".join(stdout_lines)
            stderr = "".join(stderr_lines)

            lf.write(f"\n=== Process exited with code {proc.returncode} at {datetime.now(timezone.utc).isoformat()} ===\n")
            lf.flush()

        _active_pids.pop(task_id, None)

        # v2 Phase 1: store builder discipline sections
        if builder_sections:
            _task_meta[task_id]["builder_sections"] = builder_sections

        # v2 Phase 1: capture token usage
        token_usage = _parse_token_usage(stdout + stderr)
        if token_usage:
            _task_meta[task_id]["token_usage"] = token_usage

        if proc.returncode == 0:
            task_success = True
            if _state_machine:
                try:
                    _state_machine.transition(task_id, TaskState.COMPLETED_UNVERIFIED, reason="builder_exit_0")
                except (IllegalTransitionError, KeyError) as e:
                    logger.warning("v2 transition to COMPLETED_UNVERIFIED failed: %s", e)
                    _task_meta[task_id]["status"] = "completed"
            else:
                _task_meta[task_id]["status"] = "completed"
            _task_meta[task_id]["result"] = stdout
            logger.info("Task %s: completed (%d chars output)", task_id, len(stdout))
            _broadcast_task_update_sync(task_id)
            result_preview = (stdout[:400] + "...") if len(stdout) > 400 else stdout
            spec_preview = _task_meta[task_id].get("spec", "")[:120]
            agent_bus.send({
                "from": "leroy",
                "to": "pm",
                "type": "deliverable_ready",
                "task_id": task_id,
                "content": (
                    f"Task {task_id} COMPLETED successfully.\n\n"
                    f"Result preview:\n{result_preview}"
                ),
                "context": f"Spec preview: {spec_preview}",
                "requires_response": False,
            })
        else:
            result_text = f"Exit code {proc.returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
            if _state_machine and _retry_budget:
                try:
                    categories = classify_failure(result_text, _task_meta.get(task_id, {}))
                    _task_meta[task_id]["failure_categories"] = [c.value for c in categories]
                    _state_machine.transition(task_id, TaskState.FAILED_RETRYABLE,
                                              reason=f"exit_{proc.returncode}: {','.join(c.value for c in categories)}")
                    remaining = _retry_budget.consume_retry(task_id, categories)
                    if remaining <= 0 and not is_infra_failure(categories):
                        _state_machine.transition(task_id, TaskState.ESCALATED,
                                                  reason="retry_budget_exhausted")
                except (IllegalTransitionError, KeyError) as e:
                    logger.warning("v2 failure handling error: %s", e)
                    _task_meta[task_id]["status"] = "failed"
            else:
                _task_meta[task_id]["status"] = "failed"
            _task_meta[task_id]["result"] = result_text
            logger.error("Task %s: claude exited with code %d", task_id, proc.returncode)
            _broadcast_task_update_sync(task_id)
            agent_bus.send({
                "from": "leroy", "to": "pm",
                "type": "deliverable_ready",
                "task_id": task_id,
                "content": (
                    f"Task {task_id} FAILED (exit code {proc.returncode}).\n\n"
                    f"STDOUT:\n{stdout[:300]}\n"
                    f"STDERR:\n{stderr[:300]}"
                ),
                "context": f"Spec preview: {_task_meta[task_id].get('spec', '')[:120]}",
                "requires_response": False,
            })

    except subprocess.TimeoutExpired:
        _active_pids.pop(task_id, None)
        # v2 Phase 1: collect partial output before killing
        partial_output = "".join(stdout_lines) if 'stdout_lines' in dir() else ""
        _task_meta[task_id]["partial_result"] = partial_output[-10000:] if partial_output else ""

        if proc:
            # v2 Phase 1: explicit FD cleanup before kill
            for fd in (proc.stdout, proc.stderr):
                try:
                    fd.close()
                except Exception:
                    pass
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass

        timeout_result = partial_output if partial_output else f"Task timed out after {kill_timeout}s (no output)"
        if _state_machine and _retry_budget:
            try:
                categories = classify_failure(timeout_result, {**(_task_meta.get(task_id) or {}), "timeout": True})
                _task_meta[task_id]["failure_categories"] = [c.value for c in categories]
                _state_machine.transition(task_id, TaskState.FAILED_RETRYABLE,
                                          reason=f"inactivity_timeout_{kill_timeout}s")
                remaining = _retry_budget.consume_retry(task_id, categories)
                if remaining <= 0:
                    _state_machine.transition(task_id, TaskState.ESCALATED,
                                              reason="retry_budget_exhausted_after_timeout")
            except (IllegalTransitionError, KeyError) as e:
                logger.warning("v2 timeout handling error: %s", e)
                _task_meta[task_id]["status"] = "failed"
        else:
            _task_meta[task_id]["status"] = "failed"
        _task_meta[task_id]["result"] = timeout_result
        logger.error("Task %s: timed out after %ds inactivity", task_id, kill_timeout)
        _broadcast_task_update_sync(task_id)
        agent_bus.send({
            "from": "leroy", "to": "pm",
            "type": "deliverable_ready",
            "task_id": task_id,
            "content": f"Task {task_id} TIMED OUT ({kill_timeout}s inactivity). Partial output: {len(partial_output)} chars.",
            "context": f"Spec preview: {_task_meta[task_id].get('spec', '')[:120]}",
            "requires_response": False,
        })
    except Exception as e:
        if _state_machine:
            try:
                categories = classify_failure(str(e), _task_meta.get(task_id) or {})
                _task_meta[task_id]["failure_categories"] = [c.value for c in categories]
                _state_machine.transition(task_id, TaskState.FAILED_RETRYABLE,
                                          reason=f"execution_error: {e}")
            except (IllegalTransitionError, KeyError) as e2:
                logger.warning("v2 exception handling error: %s", e2)
                _task_meta[task_id]["status"] = "failed"
        else:
            _task_meta[task_id]["status"] = "failed"
        _task_meta[task_id]["result"] = f"Execution error: {e}"
        logger.exception("Task %s: execution error", task_id)
        _broadcast_task_update_sync(task_id)
        agent_bus.send({
            "from": "leroy", "to": "pm",
            "type": "deliverable_ready",
            "task_id": task_id,
            "content": f"Task {task_id} FAILED with execution error: {e}",
            "context": f"Spec preview: {_task_meta[task_id].get('spec', '')[:120]}",
            "requires_response": False,
        })
    finally:
        _task_meta[task_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        # v2 Phase 1: worktree cleanup
        _cleanup_worktree(task_id, worktree_path, task_success)
        # Persist task outcome to Aianna -- non-blocking, handles brain unavailability
        try:
            _persist_manager.persist_task(task_id, _task_meta[task_id])
        except Exception as _pe:
            logger.error("Task %s: persist_manager raised unexpectedly: %s", task_id, _pe)


async def _execute_task(task_id: str, spec: str) -> None:
    """Run claude execution in a thread pool so it doesn't block the server."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _run_claude_sync, task_id, spec)


# ---------------------------------------------------------------------------
# Agent Executor
# ---------------------------------------------------------------------------
class LeroyExecutor(AgentExecutor):
    """Receives specs from PM, executes via claude -p with dashboard visibility."""

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        # Extract the spec text from the incoming message
        task_id = context.task_id or uuid4().hex
        spec_text = ""
        if context.message:
            for part in context.message.parts:
                # Part is a RootModel; text lives in part.root.text
                if hasattr(part, "root") and hasattr(part.root, "text"):
                    spec_text += part.root.text

        # Store task metadata
        _task_meta[task_id] = {
            "task_id": task_id,
            "spec": spec_text,
            "status": "pending",
            "result": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
        }

        # v2: Initialize state machine for this task
        if _state_machine:
            try:
                _state_machine.initialize_task(task_id)
            except Exception as e:
                logger.warning("v2 state machine init failed for %s: %s", task_id, e)

        # v2 Phase 3: Store builder prompt version in task metadata
        _task_meta[task_id]["builder_prompt_version"] = _BUILDER_PROMPT_VERSION

        logger.info("Task %s received (spec length: %d chars) -- launching execution", task_id, len(spec_text))

        # Emit activity event for task creation
        spec_preview = spec_text[:100].replace("\n", " ") if spec_text else ""
        _emit_activity("leroy", "task_start", f"Task received: {spec_preview}...",
                       task_id=task_id)

        # Trigger persistence queue flush on task pickup (non-blocking)
        _persist_manager.flush_if_ready()

        # Fire off execution in background (don't await -- let it run)
        asyncio.create_task(_execute_task(task_id, spec_text))

        _broadcast_task_update_sync(task_id)

        # Respond immediately via A2A protocol
        await event_queue.enqueue_event(
            new_agent_text_message(
                f"Task {task_id} received and executing. "
                f"Spec length: {len(spec_text)} chars. "
                f"Poll GET /tasks/{task_id} for status."
            )
        )

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        task_id = context.task_id
        if task_id and task_id in _task_meta:
            _task_meta[task_id]["status"] = "cancelled"
            logger.info("Task %s cancelled", task_id)
            await event_queue.enqueue_event(
                new_agent_text_message(f"Task {task_id} cancelled.")
            )
        else:
            await event_queue.enqueue_event(
                new_agent_text_message(f"Task {task_id} not found.")
            )


# ---------------------------------------------------------------------------
# Agent Card
# ---------------------------------------------------------------------------
spec_skill = AgentSkill(
    id="receive_spec",
    name="Receive Engineering Spec",
    description=(
        "Receives a product spec from PM and queues it for engineering execution "
        "via the micro-sprint SDLC."
    ),
    tags=["spec", "engineering", "sdlc"],
    examples=["Build the A2A server for Leroy", "Fix the auth middleware"],
)

agent_card = AgentCard(
    name=config.AGENT_NAME,
    description=config.AGENT_DESCRIPTION,
    url=config.AGENT_URL,
    version=config.AGENT_VERSION,
    defaultInputModes=["text"],
    defaultOutputModes=["text"],
    capabilities=AgentCapabilities(),
    skills=[spec_skill],
    securitySchemes={
        "bearer": SecurityScheme(
            root=HTTPAuthSecurityScheme(scheme="bearer")
        ),
    },
    security=[{"bearer": []}],
)


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------
def _check_auth(request: Request) -> dict | None:
    """Validate bearer token from request. Returns client meta or None.

    Returns None (auth passes) if auth is disabled (no tokens loaded).
    """
    if not auth.is_auth_enabled():
        return {"client_id": "anonymous", "source": "unknown"}

    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    return auth.validate_token(token)


# ---------------------------------------------------------------------------
# Custom endpoints for Leroy CLI pickup
# ---------------------------------------------------------------------------
async def tasks_pending(request: Request) -> JSONResponse:
    """GET /tasks/pending -- Returns all pending tasks for Leroy pickup."""
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    pending = [
        t for t in _task_meta.values()
        if t["status"] == "pending"
    ]
    return JSONResponse({"tasks": pending, "count": len(pending)})


async def tasks_complete(request: Request) -> JSONResponse:
    """POST /tasks/complete -- Leroy reports task completion.

    Body: {"task_id": "...", "result": "..."}
    """
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    task_id = body.get("task_id")
    result = body.get("result")

    if not task_id:
        return JSONResponse({"error": "task_id required"}, status_code=400)

    if task_id not in _task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    is_qa_review = bool(body.get("qa_review", False))
    new_status = "qa_review" if is_qa_review else "completed"
    _task_meta[task_id]["status"] = new_status
    _task_meta[task_id]["result"] = result
    _task_meta[task_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
    if is_qa_review:
        _task_meta[task_id]["qa_review_requested_at"] = datetime.now(timezone.utc).isoformat()

    logger.info("Task %s %s", task_id, "queued for qa_review" if is_qa_review else "completed")
    _broadcast_task_update_sync(task_id)

    # Emit activity event
    event_label = "qa_review" if is_qa_review else "task_complete"
    _emit_activity("leroy", event_label,
                   f"Task {'queued for QA review' if is_qa_review else 'completed'}: {task_id[:8]}",
                   task_id=task_id)

    # Notify PM via message broker
    result_str = result or ""
    result_preview = (result_str[:400] + "...") if len(result_str) > 400 else result_str
    agent_bus.send({
        "from": "leroy", "to": "pm",
        "type": "deliverable_ready",
        "task_id": task_id,
        "content": (
            f"Task {task_id} {'AWAITING QA REVIEW' if is_qa_review else 'COMPLETED'} successfully.\n\n"
            f"Result preview:\n{result_preview}"
        ),
        "context": f"Spec preview: {_task_meta[task_id].get('spec', '')[:120]}",
        "requires_response": False,
    })

    # Persist task outcome to Aianna -- non-blocking, handles brain unavailability
    try:
        _persist_manager.persist_task(task_id, _task_meta[task_id])
    except Exception as _pe:
        logger.error("Task %s: persist_manager raised unexpectedly: %s", task_id, _pe)

    return JSONResponse({"status": "ok", "task_id": task_id})


async def task_accept(request: Request) -> JSONResponse:
    """POST /tasks/{task_id}/accept -- Leroy claims a pending task for execution.

    Transitions task from pending -> working so it no longer appears
    in /tasks/pending for other callers.
    """
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in _task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    if _task_meta[task_id]["status"] != "pending":
        return JSONResponse(
            {"error": f"task {task_id} cannot be accepted (status: {_task_meta[task_id]['status']})"},
            status_code=409,
        )

    _task_meta[task_id]["status"] = "working"
    _task_meta[task_id]["accepted_at"] = datetime.now(timezone.utc).isoformat()
    logger.info("Task %s accepted for execution", task_id)
    _broadcast_task_update_sync(task_id)
    return JSONResponse({"status": "ok", "task_id": task_id, "spec": _task_meta[task_id]["spec"]})


async def tasks_list(request: Request) -> JSONResponse:
    """GET /tasks -- Returns all tasks with their status."""
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    status_filter = request.query_params.get("status")
    # Default: hide archived tasks. Pass ?include_archived=true to see them.
    include_archived = request.query_params.get("include_archived", "").lower() in ("1", "true", "yes")
    tasks = list(_task_meta.values())
    if status_filter:
        tasks = [t for t in tasks if t["status"] == status_filter]
    if not include_archived:
        tasks = [t for t in tasks if not t.get("archived", False)]

    return JSONResponse({"tasks": tasks, "count": len(tasks)})


async def task_detail(request: Request) -> JSONResponse:
    """GET /tasks/{task_id} -- Returns a single task by ID."""
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in _task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    return JSONResponse(_task_meta[task_id])


async def task_cancel(request: Request) -> JSONResponse:
    """POST /tasks/{task_id}/cancel -- Cancel a pending task."""
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in _task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    if _task_meta[task_id]["status"] not in ("pending", "working", "idea"):
        return JSONResponse(
            {"error": f"task {task_id} cannot be cancelled (status: {_task_meta[task_id]['status']})"},
            status_code=409,
        )

    _task_meta[task_id]["status"] = "cancelled"
    logger.info("Task %s cancelled via REST", task_id)
    return JSONResponse({"status": "ok", "task_id": task_id})


async def task_archive(request: Request) -> JSONResponse:
    """POST /tasks/{task_id}/archive -- Archive a task (hide from default list view).

    Archived tasks are still queryable via ?include_archived=true or status filter.
    This does NOT delete the task.
    """
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in _task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    _task_meta[task_id]["archived"] = True
    _task_meta[task_id]["archived_at"] = datetime.now(timezone.utc).isoformat()
    logger.info("Task %s archived", task_id)
    return JSONResponse({"status": "ok", "task_id": task_id, "archived": True})


async def task_unarchive(request: Request) -> JSONResponse:
    """POST /tasks/{task_id}/unarchive -- Restore an archived task to default views."""
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in _task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    _task_meta[task_id]["archived"] = False
    _task_meta[task_id].pop("archived_at", None)
    logger.info("Task %s unarchived", task_id)
    return JSONResponse({"status": "ok", "task_id": task_id, "archived": False})


async def task_review(request: Request) -> JSONResponse:
    """POST /tasks/{task_id}/review -- PM approves or rejects a QA review task.

    Body: {"decision": "approved" | "rejected", "reason": "optional rejection reason"}
    Auth: Bearer token required.
    Validates task is in qa_review status.
    Transitions to completed (approved) or failed (rejected).
    Broadcasts SSE update.
    """
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in _task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    if _task_meta[task_id]["status"] != "qa_review":
        return JSONResponse(
            {"error": f"task {task_id} is not in qa_review status (current: {_task_meta[task_id]['status']})"},
            status_code=409,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    decision = body.get("decision")
    if decision not in ("approved", "rejected"):
        return JSONResponse({"error": "decision must be 'approved' or 'rejected'"}, status_code=400)

    reason = body.get("reason", "")
    now = datetime.now(timezone.utc).isoformat()

    if decision == "approved":
        _task_meta[task_id]["status"] = "completed"
    else:
        _task_meta[task_id]["status"] = "failed"

    _task_meta[task_id]["review_decision"] = decision
    _task_meta[task_id]["reviewed_at"] = now
    if reason:
        _task_meta[task_id]["review_reason"] = reason

    logger.info("Task %s review: %s by %s", task_id, decision, client.get("client_id"))
    _broadcast_task_update_sync(task_id)

    return JSONResponse({
        "status": "ok",
        "task_id": task_id,
        "decision": decision,
        "new_status": _task_meta[task_id]["status"],
    })


async def task_delete(request: Request) -> JSONResponse:
    """DELETE /tasks/{task_id} -- Hard delete a task (admin only, requires confirmation).

    Body: {"confirm": true, "reason": "why deleting this task"}
    This permanently removes the task and its subtasks from the database.
    Task messages are retained (they may be relevant to other audit purposes).
    NEVER call this on accident -- there is no undo.
    """
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "JSON body with confirm=true required"}, status_code=400)

    if not body.get("confirm"):
        return JSONResponse(
            {
                "error": "Deletion requires confirm=true in request body. "
                         "Tasks are permanent records. Use archive instead for hiding from views.",
                "hint": "POST /tasks/{task_id}/archive to hide without deleting.",
            },
            status_code=400,
        )

    if task_id not in _task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    reason = body.get("reason", "(no reason given)")
    deleted = _task_meta.delete(task_id)
    if deleted:
        logger.warning("Task %s HARD DELETED by %s. Reason: %s", task_id, client.get("client_id"), reason)
        return JSONResponse({"status": "deleted", "task_id": task_id})
    else:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)


# ---------------------------------------------------------------------------
# PM <-> Leroy bidirectional messaging endpoints
# ---------------------------------------------------------------------------

async def pm_messages_receive(request: Request) -> JSONResponse:
    """POST /pm/messages -- Leroy subprocess sends a message to PM.

    Body: full message schema (see message_broker.py docstring).
    Returns: {"message_id": "...", "status": "queued"}
    """
    # No auth check here -- subprocess runs on same machine, no token available.
    # Only localhost requests can reach this endpoint (server binds 127.0.0.1).
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    required_fields = ["type", "task_id"]
    for field in required_fields:
        if not body.get(field):
            return JSONResponse({"error": f"{field} required"}, status_code=400)

    valid_types = ("question", "status_update", "decision_gate", "blocker", "deliverable_ready")
    if body["type"] not in valid_types:
        return JSONResponse(
            {"error": f"invalid type '{body['type']}'. Valid: {valid_types}"},
            status_code=400,
        )

    # Route through generic agent bus (legacy compat: add from/to fields)
    body.setdefault("from", "leroy")
    body.setdefault("to", "pm")
    msg = agent_bus.send(body)
    message_id = msg["message_id"]
    requires_response = body["type"] in ("question", "decision_gate", "blocker")
    logger.info(
        "PM message received: type=%s task=%s message_id=%s requires_response=%s",
        body["type"], body.get("task_id"), message_id, requires_response,
    )

    # Emit activity event for PM message
    severity = "warn" if requires_response else "info"
    _emit_activity(
        "leroy", "decision_requested" if requires_response else "status_update",
        f"PM message ({body['type']}): {body.get('content', '')[:80]}",
        task_id=body.get("task_id"),
        severity=severity,
    )

    # Update task status to "waiting_for_pm" if blocking
    task_id = body.get("task_id")
    if requires_response and task_id and task_id in _task_meta:
        _task_meta[task_id]["status"] = "waiting_for_pm"
        _task_meta[task_id]["waiting_on_message"] = message_id
        _broadcast_task_update_sync(task_id)

    return JSONResponse({
        "message_id": message_id,
        "status": "queued",
        "requires_response": requires_response,
    })


async def pm_messages_response_poll(request: Request) -> JSONResponse:
    """GET /pm/messages/{message_id}/response -- Subprocess polls for PM response.

    Returns immediately with {"status": "pending"} if not yet answered.
    Returns {"status": "answered", "response": "..."} when PM has replied.
    """
    message_id = request.path_params["message_id"]
    msg = agent_bus.get_message(message_id)
    if msg is None:
        return JSONResponse({"error": f"message {message_id} not found"}, status_code=404)

    response = agent_bus.poll_response(message_id)
    if response is None:
        return JSONResponse({"status": "pending", "message_id": message_id})

    return JSONResponse({
        "status": "answered",
        "message_id": message_id,
        "response": response,
        "responded_at": msg.get("responded_at"),
    })


async def pm_messages_respond(request: Request) -> JSONResponse:
    """POST /pm/messages/{message_id}/respond -- PM sends response to Leroy.

    Called by PM's MCP tool (leroy_reply_to_message).
    Body: {"response": "PM's answer text", "task_id": "optional"}
    """
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    message_id = request.path_params["message_id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    response_text = body.get("response")
    if not response_text:
        return JSONResponse({"error": "response field required"}, status_code=400)

    msg = agent_bus.get_message(message_id)
    if msg is None:
        return JSONResponse({"error": f"message {message_id} not found"}, status_code=404)

    ok = agent_bus.respond(message_id, "pm", response_text)
    if not ok:
        return JSONResponse({"error": "failed to store response"}, status_code=500)

    # If task was in waiting_for_pm state, restore it to working
    task_id = msg.get("task_id")
    if task_id and task_id in _task_meta:
        if _task_meta[task_id].get("status") == "waiting_for_pm":
            _task_meta[task_id]["status"] = "working"
            _task_meta[task_id].pop("waiting_on_message", None)
            _broadcast_task_update_sync(task_id)

    logger.info("PM responded to message %s (task %s)", message_id, task_id)
    _emit_activity("pm", "decision_requested",
                   f"PM responded to {msg.get('type', 'message')} (task {(task_id or '')[:8]})",
                   task_id=task_id, severity="info")
    return JSONResponse({"status": "ok", "message_id": message_id, "task_id": task_id})


async def pm_messages_pending(request: Request) -> JSONResponse:
    """GET /pm/messages/pending -- PM reads unread messages awaiting response."""
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    pending = agent_bus.list_messages(to="pm", pending=True)
    return JSONResponse({"messages": pending, "count": len(pending)})


async def pm_messages_all(request: Request) -> JSONResponse:
    """GET /pm/messages -- PM reads all recent messages (responded or not)."""
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    limit = int(request.query_params.get("limit", "20"))
    messages = agent_bus.list_messages(to="pm", limit=limit)
    return JSONResponse({"messages": messages, "count": len(messages)})


# ---------------------------------------------------------------------------
# Generic Agent Message Bus endpoints
# ---------------------------------------------------------------------------

async def bus_send(request: Request) -> JSONResponse:
    """POST /messages -- Send a message from any agent to any agent.

    Body: {from, to, content, type?, task_id?, context?, requires_response?}
    No auth -- localhost only, same as subprocess messaging.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    if not body.get("from"):
        return JSONResponse({"error": "'from' required"}, status_code=400)
    if not body.get("to"):
        return JSONResponse({"error": "'to' required"}, status_code=400)
    if not body.get("content"):
        return JSONResponse({"error": "'content' required"}, status_code=400)

    msg = agent_bus.send(body)

    # Emit activity event
    severity = "warn" if msg["requires_response"] else "info"
    _emit_activity(
        msg["from"],
        "message_sent",
        f"{msg['from']} -> {msg['to']}: {msg['content'][:80]}",
        task_id=msg.get("task_id"),
        severity=severity,
    )

    # If blocking message linked to a task, update task status
    if msg["requires_response"] and msg.get("task_id") and msg["task_id"] in _task_meta:
        _task_meta[msg["task_id"]]["status"] = "waiting_for_pm"
        _task_meta[msg["task_id"]]["waiting_on_message"] = msg["message_id"]
        _broadcast_task_update_sync(msg["task_id"])

    return JSONResponse({
        "message_id": msg["message_id"],
        "status": "queued",
        "requires_response": msg["requires_response"],
    })


async def bus_list(request: Request) -> JSONResponse:
    """GET /messages -- List messages with filters. Never auto-marks as read.

    Query params: to, from, pending (bool), unread (bool), type, limit
    """
    to = request.query_params.get("to")
    from_agent = request.query_params.get("from")
    pending = request.query_params.get("pending", "").lower() in ("true", "1", "yes")
    unread = request.query_params.get("unread", "").lower() in ("true", "1", "yes")
    msg_type = request.query_params.get("type")
    limit = int(request.query_params.get("limit", "50"))

    messages = agent_bus.list_messages(
        to=to, from_agent=from_agent, pending=pending,
        unread=unread, msg_type=msg_type, limit=limit,
    )
    return JSONResponse({"messages": messages, "count": len(messages)})


async def bus_get(request: Request) -> JSONResponse:
    """GET /messages/{message_id} -- Get a single message."""
    message_id = request.path_params["message_id"]
    msg = agent_bus.get_message(message_id)
    if msg is None:
        return JSONResponse({"error": f"message {message_id} not found"}, status_code=404)
    return JSONResponse(msg)


async def bus_respond(request: Request) -> JSONResponse:
    """POST /messages/{message_id}/respond -- Reply to a message.

    Body: {from, content}
    """
    message_id = request.path_params["message_id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    responder = body.get("from", "unknown")
    content = body.get("content", "")
    if not content:
        return JSONResponse({"error": "'content' required"}, status_code=400)

    msg = agent_bus.get_message(message_id)
    if msg is None:
        return JSONResponse({"error": f"message {message_id} not found"}, status_code=404)

    ok = agent_bus.respond(message_id, responder, content)
    if not ok:
        return JSONResponse({"error": "failed to store response"}, status_code=500)

    # If task was in waiting state, restore to working
    task_id = msg.get("task_id")
    if task_id and task_id in _task_meta:
        if _task_meta[task_id].get("status") == "waiting_for_pm":
            _task_meta[task_id]["status"] = "working"
            _task_meta[task_id].pop("waiting_on_message", None)
            _broadcast_task_update_sync(task_id)

    _emit_activity(
        responder, "message_response",
        f"{responder} responded to {msg.get('from', '?')}'s {msg.get('type', 'message')}",
        task_id=task_id, severity="info",
    )
    return JSONResponse({"status": "ok", "message_id": message_id})


async def bus_read(request: Request) -> JSONResponse:
    """POST /messages/{message_id}/read -- Explicitly mark a message as read.

    Body: {agent: "pm"}
    Read is NEVER automatic on GET. Monitor daemons can poll without consuming.
    """
    message_id = request.path_params["message_id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    agent = body.get("agent", "unknown")
    ok = agent_bus.mark_read(message_id, agent)
    if not ok:
        return JSONResponse({"error": f"message {message_id} not found"}, status_code=404)

    return JSONResponse({"status": "ok", "message_id": message_id, "read_by": agent})


async def bus_agents(request: Request) -> JSONResponse:
    """GET /messages/agents -- List known agents with unread/pending counts."""
    agents = agent_bus.agent_summary()
    return JSONResponse({"agents": agents, "count": len(agents)})


async def bus_poll_response(request: Request) -> JSONResponse:
    """GET /messages/{message_id}/response -- Subprocess polls for response.

    Returns immediately. No blocking. Matches the old /pm/messages/{id}/response pattern
    so Leroy subprocesses work without changes.
    """
    message_id = request.path_params["message_id"]
    msg = agent_bus.get_message(message_id)
    if msg is None:
        return JSONResponse({"error": f"message {message_id} not found"}, status_code=404)

    response = agent_bus.poll_response(message_id)
    if response is None:
        return JSONResponse({"status": "pending", "message_id": message_id})

    return JSONResponse({
        "status": "answered",
        "message_id": message_id,
        "response": response,
        "responded_at": msg.get("responded_at"),
    })


# ---------------------------------------------------------------------------
# Sub-task endpoints
# ---------------------------------------------------------------------------

async def subtask_update(request: Request) -> JSONResponse:
    """POST /tasks/{task_id}/subtasks -- Leroy subprocess reports a sub-task update.

    Body: {
        "subtask_id": "string (required)",
        "name": "string (required)",
        "agent": "string (optional)",
        "status": "pending|running|completed|failed",
        "output": "string (optional)",
        "started_at": "ISO string (optional)",
        "completed_at": "ISO string (optional)"
    }
    If subtask_id exists in the task's list, updates it. Otherwise appends.
    """
    task_id = request.path_params["task_id"]
    if task_id not in _task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    subtask_id = body.get("subtask_id")
    if not subtask_id:
        return JSONResponse({"error": "subtask_id required"}, status_code=400)

    name = body.get("name", "")
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)

    now = datetime.now(timezone.utc).isoformat()
    subtask = {
        "subtask_id": subtask_id,
        "task_id": task_id,
        "name": name,
        "agent": body.get("agent", ""),
        "status": body.get("status", "pending"),
        "output": body.get("output", None),
        "started_at": body.get("started_at", None),
        "completed_at": body.get("completed_at", None),
        "updated_at": now,
    }

    action = _subtask_store.upsert_subtask(task_id, subtask)
    logger.info("Task %s: subtask %s %s (status=%s)", task_id, subtask_id, action, subtask["status"])
    _broadcast_task_update_sync(task_id)
    return JSONResponse({"status": action, "subtask_id": subtask_id})


async def subtask_list(request: Request) -> JSONResponse:
    """GET /tasks/{task_id}/subtasks -- Returns subtasks for a task."""
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in _task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    subtasks = _subtask_store.get(task_id, [])
    return JSONResponse({"subtasks": subtasks, "count": len(subtasks)})


async def task_messages(request: Request) -> JSONResponse:
    """GET /tasks/{task_id}/messages -- Returns PM messages for a specific task."""
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in _task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    all_messages = agent_bus.list_messages(limit=200)
    task_msgs = [m for m in all_messages if m.get("task_id") == task_id]
    return JSONResponse({"messages": task_msgs, "count": len(task_msgs)})


async def tasks_stream(request: Request) -> StreamingResponse:
    """GET /tasks/stream -- SSE stream of task updates.

    Sends:
    - Initial snapshot of all tasks on connect
    - Task updates as they happen
    - Heartbeat every 15 seconds
    """
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _sse_subscribers.add(queue)

    async def event_generator():
        try:
            # Send initial snapshot
            snapshot = json.dumps({
                "type": "snapshot",
                "tasks": list(_task_meta.values()),
            })
            yield f"data: {snapshot}\n\n"

            while True:
                try:
                    # Wait for update or timeout for heartbeat
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat
                    heartbeat = json.dumps({
                        "type": "heartbeat",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    yield f"data: {heartbeat}\n\n"
        except Exception:
            pass
        finally:
            _sse_subscribers.discard(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Stuck task detector (background thread)
# ---------------------------------------------------------------------------
def _stuck_task_detector() -> None:
    """Background thread: detect tasks stuck in 'working' after all subtasks complete."""
    logger.info("Stuck task detector running")
    while True:
        time.sleep(_STUCK_CHECK_INTERVAL)
        try:
            for task_id, meta in list(_task_meta.items()):
                if meta.get("status") != "working":
                    continue

                # Check 1: all subtasks completed but parent still working
                subtasks = _subtask_store.get(task_id) if _subtask_store else []
                if subtasks and all(st.get("status") in ("completed", "failed") for st in subtasks):
                    # Skip if we already tried to auto-complete this task
                    if meta.get("_stuck_resolved"):
                        continue
                    last_subtask_time = max(
                        (st.get("completed_at", "") for st in subtasks),
                        default=""
                    )
                    if last_subtask_time:
                        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last_subtask_time)).total_seconds()
                        if elapsed > _STUCK_THRESHOLD:
                            logger.warning(
                                "STUCK TASK DETECTED: %s -- all %d subtasks done, parent still working for %ds. "
                                "PID: %s, last_activity: %s",
                                task_id, len(subtasks), int(elapsed),
                                _active_pids.get(task_id, "none (interactive session)"),
                                meta.get("last_activity", "unknown"),
                            )
                            now_iso = datetime.now(timezone.utc).isoformat()
                            meta["_stuck_detected_at"] = now_iso
                            meta["_stuck_reason"] = f"All {len(subtasks)} subtasks done, parent working for {int(elapsed)}s"

                            # Auto-resolve: kill the stuck process (if any) and
                            # mark the task completed. All subtasks finished
                            # successfully -- the stall is an infrastructure bug
                            # (e.g. orphaned pipe holder), not a work failure.
                            pid = _active_pids.get(task_id)
                            if pid:
                                try:
                                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                                    logger.info("STUCK TASK %s: sent SIGTERM to process group (PID %d)", task_id, pid)
                                except (ProcessLookupError, OSError) as kill_err:
                                    logger.info("STUCK TASK %s: SIGTERM skipped (%s)", task_id, kill_err)
                                _active_pids.pop(task_id, None)

                            # Write back through PersistentTaskDict so it persists to SQLite.
                            # meta from items() is a plain dict copy -- must use _task_meta[task_id].
                            tracked = _task_meta[task_id]
                            tracked["status"] = "completed"
                            tracked["completed_at"] = now_iso
                            if not tracked.get("result"):
                                tracked["result"] = (
                                    f"[Auto-completed by stuck detector after {int(elapsed)}s. "
                                    f"All {len(subtasks)} subtasks finished. "
                                    f"See logs/{task_id}.log for full output.]"
                                )
                            _broadcast_task_update_sync(task_id)
                            agent_bus.send({
                                "from": "leroy", "to": "pm",
                                "type": "deliverable_ready",
                                "task_id": task_id,
                                "content": (
                                    f"Task {task_id} AUTO-COMPLETED by stuck detector. "
                                    f"All {len(subtasks)} subtasks finished {int(elapsed)}s ago. "
                                    f"Parent process was stuck (likely orphaned pipe). "
                                    f"Work is done -- check logs/{task_id}.log for full output."
                                ),
                                "requires_response": False,
                            })
                            _broadcast_task_update_sync(task_id)
                            logger.info("STUCK TASK %s: auto-completed successfully", task_id)

                # Check 2: subprocess PID liveness (only for server-spawned tasks)
                pid = _active_pids.get(task_id)
                if pid:
                    try:
                        os.kill(pid, 0)  # signal 0 = check if alive
                    except ProcessLookupError:
                        logger.error(
                            "DEAD PROCESS: task %s has PID %d but process is gone. Auto-failing.",
                            task_id, pid
                        )
                        _active_pids.pop(task_id, None)
                        meta["status"] = "failed"
                        meta["result"] = f"Process {pid} died unexpectedly. Check logs/{task_id}.log"
                        _broadcast_task_update_sync(task_id)
                        agent_bus.send({
                            "from": "leroy", "to": "pm",
                            "type": "deliverable_ready",
                            "task_id": task_id,
                            "content": f"Task {task_id} FAILED -- subprocess PID {pid} died unexpectedly.",
                            "requires_response": False,
                        })
        except Exception:
            logger.exception("Stuck task detector error")


async def task_logs(request: Request) -> JSONResponse:
    """GET /tasks/{task_id}/logs -- Tail the task log file for Ops troubleshooting."""
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    tail_lines = int(request.query_params.get("tail", "50"))
    log_file = LOGS_DIR / f"{task_id}.log"

    if not log_file.exists():
        return JSONResponse({"error": "no log file for this task", "task_id": task_id}, status_code=404)

    try:
        lines = log_file.read_text().splitlines()
        tail = lines[-tail_lines:] if len(lines) > tail_lines else lines
        pid = _active_pids.get(task_id)
        pid_alive = False
        if pid:
            try:
                os.kill(pid, 0)
                pid_alive = True
            except ProcessLookupError:
                pass

        return JSONResponse({
            "task_id": task_id,
            "log_lines": tail,
            "total_lines": len(lines),
            "showing": len(tail),
            "log_file": str(log_file),
            "process": {"pid": pid, "alive": pid_alive} if pid else None,
            "last_activity": _task_meta.get(task_id, {}).get("last_activity"),
            "stuck_detected": _task_meta.get(task_id, {}).get("_stuck_detected_at"),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Agent registry endpoints
# ---------------------------------------------------------------------------

# Known agents seeded at startup (Phase 1: static roster)
_SEED_AGENTS = [
    {
        "name": "pm",
        "display_name": "PM",
        "type": "interactive",
        "launcher": "pm.sh",
        "status": "idle",
        "current_task": None,
        "last_heartbeat": None,
        "last_activity": None,
        "metadata": {
            "launch_method": "manual",
            "description": "Product Manager -- specs, decisions, delegation",
        },
    },
    {
        "name": "leroy",
        "display_name": "Leroy",
        "type": "daemon",
        "launcher": "leroy.sh",
        "status": "idle",
        "current_task": None,
        "last_heartbeat": None,
        "last_activity": None,
        "metadata": {
            "launch_method": "launchd",
            "description": "Engineering Lead -- executes specs via claude CLI",
        },
    },
    {
        "name": "ops",
        "display_name": "Ops",
        "type": "on-demand",
        "launcher": "ops.sh",
        "status": "idle",
        "current_task": None,
        "last_heartbeat": None,
        "last_activity": None,
        "metadata": {
            "launch_method": "manual",
            "description": "Infrastructure ops and troubleshooting",
        },
    },
    {
        "name": "content-agent",
        "display_name": "Content Agent",
        "type": "scheduled",
        "launcher": "content.sh",
        "status": "idle",
        "current_task": None,
        "last_heartbeat": None,
        "last_activity": None,
        "metadata": {
            "launch_method": "launchd",
            "schedule": "daily 6AM CST",
            "description": "Daily content pipeline -- queries Aianna, generates drafts",
        },
    },
]

_HEARTBEAT_WINDOW_SECONDS = 60  # seconds per heartbeat window
_HEARTBEAT_MISS_THRESHOLD = 3  # consecutive missed windows = unreachable


async def agents_list(request: Request) -> JSONResponse:
    """GET /agents -- Returns registered agent roster with status fields."""
    agents = _agent_store.list_all()
    # Compute unreachable status based on last_heartbeat
    now = datetime.now(timezone.utc)
    for agent in agents:
        lhb = agent.get("last_heartbeat")
        if lhb and agent.get("status") not in ("error",):
            try:
                lhb_dt = datetime.fromisoformat(lhb)
                elapsed = (now - lhb_dt).total_seconds()
                if elapsed > _HEARTBEAT_WINDOW_SECONDS * _HEARTBEAT_MISS_THRESHOLD:
                    agent["status"] = "unreachable"
            except Exception:
                pass
    return JSONResponse({"agents": agents, "count": len(agents)})


async def agent_heartbeat(request: Request) -> JSONResponse:
    """POST /agents/{name}/heartbeat -- Agent reports status and current task.

    Body: {"status": "idle|running|error", "current_task": null|"task_id", "metadata": {}}
    """
    name = request.path_params["name"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    now = datetime.now(timezone.utc).isoformat()
    existing = _agent_store.get(name)
    if existing is None:
        # Auto-register unknown agents
        existing = {
            "name": name,
            "display_name": name.replace("-", " ").title(),
            "type": "on-demand",
            "launcher": "unknown",
            "status": "idle",
            "current_task": None,
            "last_heartbeat": None,
            "last_activity": None,
            "metadata": {},
        }

    existing["last_heartbeat"] = now
    existing["last_activity"] = now
    if "status" in body:
        existing["status"] = body["status"]
    if "current_task" in body:
        existing["current_task"] = body.get("current_task")
    if "metadata" in body and isinstance(body["metadata"], dict):
        if "metadata" not in existing or not isinstance(existing.get("metadata"), dict):
            existing["metadata"] = {}
        existing["metadata"].update(body["metadata"])

    # Allow heartbeat to update display_name, type, launcher if provided
    for field in ("display_name", "type", "launcher"):
        if field in body:
            existing[field] = body[field]

    _agent_store.upsert(existing)
    return JSONResponse({"status": "ok", "name": name, "updated_at": now})


async def agent_delete(request: Request) -> JSONResponse:
    """DELETE /agents/{name} -- Remove an agent from the roster."""
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    name = request.path_params["name"]
    if name not in _agent_store:
        return JSONResponse({"error": f"agent {name} not found"}, status_code=404)

    with _agent_store._lock:
        _agent_store._agents.pop(name, None)
    with _agent_store._db._write_lock:
        _agent_store._db._conn.execute("DELETE FROM agents WHERE name = ?", (name,))
        _agent_store._db._conn.commit()
    logger.info("Agent %s deleted from roster", name)
    return JSONResponse({"status": "ok", "name": name, "deleted": True})


# ---------------------------------------------------------------------------
# Activity feed endpoints
# ---------------------------------------------------------------------------

async def activity_create(request: Request) -> JSONResponse:
    """POST /activity -- Create an activity event from an external agent/monitor.

    Body: {agent, type, summary, severity?, task_id?, detail?}
    No auth -- localhost only (monitors and sidecars).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    agent = body.get("agent")
    event_type = body.get("type")
    summary = body.get("summary")
    if not all([agent, event_type, summary]):
        return JSONResponse({"error": "agent, type, and summary required"}, status_code=400)

    _emit_activity(
        agent, event_type, summary,
        detail=body.get("detail"),
        task_id=body.get("task_id"),
        severity=body.get("severity", "info"),
    )
    return JSONResponse({"status": "ok"})


async def activity_list(request: Request) -> JSONResponse:
    """GET /activity -- Returns recent activity events.

    Query params:
      ?limit=50   (default 100, max 500)
      ?since=<iso8601>
      ?agent=<name>
    """
    limit = min(int(request.query_params.get("limit", "100")), 500)
    since = request.query_params.get("since")
    agent_filter = request.query_params.get("agent")
    events = _activity_store.list_recent(limit=limit, since=since, agent=agent_filter)
    return JSONResponse({"events": events, "count": len(events)})


async def activity_stream(request: Request) -> StreamingResponse:
    """GET /activity/stream -- SSE stream of activity events.

    Sends:
    - Recent events snapshot on connect
    - New events as they are emitted
    - Heartbeat every 15 seconds
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    _activity_sse_subscribers.add(queue)

    # Wire activity store to push into this queue
    def _push(evt):
        try:
            queue.put_nowait(json.dumps({"type": "activity_event", "event": evt}))
        except asyncio.QueueFull:
            _activity_sse_subscribers.discard(queue)

    _activity_store.add_sse_subscriber(_push)

    async def event_generator():
        try:
            # Send recent snapshot
            snapshot_events = _activity_store.list_recent(limit=50)
            snapshot = json.dumps({"type": "activity_snapshot", "events": snapshot_events})
            yield f"data: {snapshot}\n\n"

            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    heartbeat = json.dumps({
                        "type": "heartbeat",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    yield f"data: {heartbeat}\n\n"
        except Exception:
            pass
        finally:
            _activity_sse_subscribers.discard(queue)
            _activity_store.remove_sse_subscriber(_push)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# PM Proposal approval queue endpoints
# ---------------------------------------------------------------------------

_proposal_store: task_db.ProposalStore | None = None


async def proposals_create(request: Request) -> JSONResponse:
    """POST /pm/proposals -- Headless PM submits a draft spec for Brad's approval.

    Body: {proposal_type, title, content, reasoning, trigger_event?, trigger_task_id?}
    No auth -- localhost only.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    title = body.get("title")
    content = body.get("content")
    if not title or not content:
        return JSONResponse({"error": "title and content required"}, status_code=400)

    from uuid import uuid4
    proposal = {
        "proposal_id": uuid4().hex,
        "status": "pending",
        "proposal_type": body.get("proposal_type", "build_spec"),
        "trigger_event": body.get("trigger_event"),
        "trigger_task_id": body.get("trigger_task_id"),
        "title": title,
        "content": content,
        "reasoning": body.get("reasoning", ""),
    }
    stored = _proposal_store.create(proposal)

    _emit_activity("pm-headless", "proposal_created",
                   f"New proposal: {title}",
                   task_id=body.get("trigger_task_id"),
                   severity="warn")

    logger.info("Proposal created: %s -- %s", stored["proposal_id"], title)
    return JSONResponse({"proposal_id": stored["proposal_id"], "status": "pending"})


async def proposals_list(request: Request) -> JSONResponse:
    """GET /pm/proposals -- List proposals, optionally filtered by status.

    Query params: ?status=pending (default), ?limit=50
    """
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    status = request.query_params.get("status", "pending")
    limit = int(request.query_params.get("limit", "50"))

    if status == "all":
        proposals = _proposal_store.list_all(limit=limit)
    else:
        proposals = _proposal_store.list_by_status(status=status, limit=limit)

    return JSONResponse({"proposals": proposals, "count": len(proposals)})


async def proposals_approve(request: Request) -> JSONResponse:
    """POST /pm/proposals/{proposal_id}/approve -- Brad approves a proposal.

    Body: {feedback?: "optional note"}
    """
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    proposal_id = request.path_params["proposal_id"]
    proposal = _proposal_store.get(proposal_id)
    if proposal is None:
        return JSONResponse({"error": f"proposal {proposal_id} not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        body = {}

    now = datetime.now(timezone.utc).isoformat()
    updated = _proposal_store.update(proposal_id, {
        "status": "approved",
        "reviewed_at": now,
        "reviewer_feedback": body.get("feedback"),
    })

    # Notify on bus so monitor can spawn headless PM to execute
    agent_bus.send({
        "from": "brad",
        "to": "pm-headless",
        "type": "approval",
        "content": f"Proposal approved: {proposal.get('title', '')}",
        "task_id": proposal.get("trigger_task_id"),
        "context": json.dumps({"proposal_id": proposal_id}),
    })

    _emit_activity("brad", "proposal_approved",
                   f"Approved: {proposal.get('title', '')}",
                   task_id=proposal.get("trigger_task_id"))

    logger.info("Proposal %s approved", proposal_id)
    return JSONResponse({"status": "approved", "proposal_id": proposal_id})


async def proposals_reject(request: Request) -> JSONResponse:
    """POST /pm/proposals/{proposal_id}/reject -- Brad rejects a proposal.

    Body: {feedback: "why it was rejected"}
    """
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    proposal_id = request.path_params["proposal_id"]
    proposal = _proposal_store.get(proposal_id)
    if proposal is None:
        return JSONResponse({"error": f"proposal {proposal_id} not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        body = {}

    feedback = body.get("feedback", "")
    now = datetime.now(timezone.utc).isoformat()
    _proposal_store.update(proposal_id, {
        "status": "rejected",
        "reviewed_at": now,
        "reviewer_feedback": feedback,
    })

    _emit_activity("brad", "proposal_rejected",
                   f"Rejected: {proposal.get('title', '')}",
                   detail=feedback,
                   task_id=proposal.get("trigger_task_id"))

    logger.info("Proposal %s rejected: %s", proposal_id, feedback)
    return JSONResponse({"status": "rejected", "proposal_id": proposal_id, "feedback": feedback})


# ---------------------------------------------------------------------------
# Ideas endpoints
# ---------------------------------------------------------------------------

async def ideas_create(request: Request) -> JSONResponse:
    """POST /ideas -- Create an idea task (lightweight backlog placeholder).

    Body: {"title": "Short idea title", "description": "Optional one-liner"}
    Returns: created task with status "idea" and task_id.
    Ideas do NOT trigger auto-execution.
    """
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    title = (body.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "title required"}, status_code=400)

    description = (body.get("description") or "").strip()
    task_id = uuid4().hex
    now = datetime.now(timezone.utc).isoformat()

    task = {
        "task_id": task_id,
        "spec": title,
        "description": description,
        "status": "idea",
        "result": None,
        "created_at": now,
        "completed_at": None,
    }
    _task_meta[task_id] = task
    _broadcast_task_update_sync(task_id)

    _emit_activity("pm", "idea_created", f"Idea created: {title[:80]}", task_id=task_id)
    logger.info("Idea created: %s -- %s", task_id, title[:60])

    return JSONResponse(dict(_task_meta[task_id]), status_code=201)


async def ideas_promote(request: Request) -> JSONResponse:
    """POST /ideas/{task_id}/promote -- Promote an idea to pending.

    Changes status from "idea" to "pending".
    Optional body: {"spec": "# Full spec markdown..."} to replace the placeholder spec.
    If no spec body, the idea title becomes the spec.
    Does NOT trigger auto-execution -- task sits in pending until picked up by Leroy CLI.
    """
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in _task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    if _task_meta[task_id]["status"] != "idea":
        return JSONResponse(
            {"error": f"task {task_id} cannot be promoted (status: {_task_meta[task_id]['status']})"},
            status_code=409,
        )

    try:
        body = await request.json()
    except Exception:
        body = {}

    # If a spec is provided, replace the placeholder
    if body.get("spec"):
        _task_meta[task_id]["spec"] = body["spec"]

    _task_meta[task_id]["status"] = "pending"
    _task_meta[task_id]["promoted_at"] = datetime.now(timezone.utc).isoformat()

    logger.info("Idea %s promoted to pending", task_id)
    _broadcast_task_update_sync(task_id)
    _emit_activity("pm", "idea_promoted",
                   f"Idea promoted to pending: {_task_meta[task_id]['spec'][:60]}",
                   task_id=task_id)

    return JSONResponse({
        "status": "ok",
        "task_id": task_id,
        "new_status": "pending",
        "task": dict(_task_meta[task_id]),
    })


# ---------------------------------------------------------------------------
# Specs pipeline endpoint
# ---------------------------------------------------------------------------

async def specs_list(request: Request) -> JSONResponse:
    """GET /specs -- Returns specs with pipeline stage derived from task metadata.

    Pipeline stages:
      draft   -- specs in ~/Projects/leroy/specs/drafts/ not yet sent
      sent    -- task status == pending
      building -- task status == working | waiting_for_pm
      qa      -- task status == qa_review
      done    -- task status == completed
      failed  -- task status == failed | cancelled
    """
    import re

    def _extract_title(spec_text: str) -> str:
        if not spec_text:
            return "Untitled"
        for line in spec_text.splitlines():
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
            if line.startswith("Subject:"):
                return line[8:].strip()
        # Fall back to first non-empty line
        for line in spec_text.splitlines():
            if line.strip():
                return line.strip()[:80]
        return "Untitled"

    def _task_to_stage(status: str) -> str:
        if status in ("pending",):
            return "sent"
        elif status in ("working", "waiting_for_pm"):
            return "building"
        elif status == "qa_review":
            return "qa"
        elif status == "completed":
            return "done"
        elif status in ("failed", "cancelled"):
            return "failed"
        return "sent"

    specs = []
    for task in _task_meta.values():
        stage = _task_to_stage(task.get("status", "pending"))
        title = _extract_title(task.get("spec", ""))
        # Detect QA tasks by title convention
        is_qa_task = bool(re.search(r'\bqa\b|\bquality assurance\b', title, re.IGNORECASE))
        created_at = task.get("created_at", "")
        completed_at = task.get("completed_at", "")

        # Calculate time in stage
        reference_time = completed_at if completed_at else created_at
        time_in_stage_s = None
        if reference_time:
            try:
                ref_dt = datetime.fromisoformat(reference_time)
                time_in_stage_s = int((datetime.now(timezone.utc) - ref_dt).total_seconds())
            except Exception:
                pass

        qa_pass_rate = None
        if task.get("result"):
            # Extract QA pass rate from result string if present
            m = re.search(r'(\d+/\d+)\s*(?:pass|QA)', task["result"], re.IGNORECASE)
            if m:
                qa_pass_rate = m.group(1)

        specs.append({
            "task_id": task["task_id"],
            "title": title,
            "stage": stage,
            "is_qa_task": is_qa_task,
            "created_at": created_at,
            "completed_at": completed_at,
            "time_in_stage_seconds": time_in_stage_s,
            "qa_pass_rate": qa_pass_rate,
            "archived": task.get("archived", False),
        })

    # Sort: newest first
    specs.sort(key=lambda s: s["created_at"] or "", reverse=True)

    # Optionally include draft specs from filesystem
    draft_dir = Path(WORK_DIR) / "specs" / "drafts"
    if draft_dir.exists():
        for f in sorted(draft_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            try:
                content = f.read_text()
                title = _extract_title(content)
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()
                specs.insert(0, {
                    "task_id": None,
                    "title": title,
                    "stage": "draft",
                    "is_qa_task": False,
                    "created_at": mtime,
                    "completed_at": None,
                    "time_in_stage_seconds": None,
                    "qa_pass_rate": None,
                    "archived": False,
                    "draft_file": f.name,
                })
            except Exception:
                pass

    return JSONResponse({"specs": specs, "count": len(specs)})


# ---------------------------------------------------------------------------
# Brain health proxy
# ---------------------------------------------------------------------------

async def brain_health(request: Request) -> JSONResponse:
    """GET /brain/health -- Proxies to forge-brain health endpoint on Kush."""
    brain_url = config.FORGE_BRAIN_URL.rstrip("/").replace("/mcp", "")
    # Try health endpoint at base:8301/health first, fallback to base:8300/health
    health_urls = [
        brain_url.replace(":8300", ":8301") + "/health",
        brain_url + "/health",
    ]

    for url in health_urls:
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {config.FORGE_BRAIN_TOKEN}"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
                data["_proxy_source"] = url
                data["_proxy_ok"] = True
                data["circuit_breaker"] = _persist_manager.circuit_state
                data["persist_queue_depth"] = _persist_manager.queue_depth()
                data["dead_letter_depth"] = _persist_manager.dead_letter_depth()
                return JSONResponse(data)
        except urllib.error.HTTPError as e:
            # Got a response, parse it
            try:
                data = json.loads(e.read().decode())
                data["_proxy_source"] = url
                data["_proxy_ok"] = False
                data["_http_status"] = e.code
                data["circuit_breaker"] = _persist_manager.circuit_state
                return JSONResponse(data, status_code=200)
            except Exception:
                pass
        except Exception as e:
            last_error = str(e)
            continue

    return JSONResponse({
        "status": "unreachable",
        "error": last_error if "last_error" in locals() else "all health URLs failed",
        "circuit_breaker": _persist_manager.circuit_state,
        "persist_queue_depth": _persist_manager.queue_depth(),
        "dead_letter_depth": _persist_manager.dead_letter_depth(),
        "_proxy_ok": False,
    })


# ---------------------------------------------------------------------------
# Infrastructure status
# ---------------------------------------------------------------------------

_INFRA_TOPOLOGY = [
    {
        "name": "Kush",
        "hostname": "kush",
        "ip": "192.168.1.100",
        "role": "Brain Infrastructure",
        "services": [
            {"name": "Qdrant", "port": 6333, "path": "/healthz"},
            {"name": "forge-brain", "port": 8300, "path": "/health"},
            {"name": "forge-brain-health", "port": 8301, "path": "/health"},
        ],
    },
    {
        "name": "Haze",
        "hostname": "haze",
        "ip": "127.0.0.1",
        "role": "Development Machine",
        "services": [
            {"name": "Leroy A2A", "port": 9800, "path": "/health"},
            {"name": "Leroy Health", "port": 9801, "path": "/health"},
            {"name": "Dashboard", "port": 5173, "path": "/"},
        ],
    },
    {
        "name": "APEX",
        "hostname": "apex",
        "ip": "155.138.199.82",
        "role": "Carric Infrastructure (CloudRaider)",
        "services": [
            {"name": "A2A Gateway", "port": 8443, "path": "/health", "protocol": "https"},
        ],
    },
]


def _ping_service(ip: str, port: int, path: str, timeout: float = 2.0, protocol: str = "http") -> dict:
    """Attempt an HTTP/HTTPS GET to ip:port/path. Returns status dict."""
    url = f"{protocol}://{ip}:{port}{path}"
    start = time.time()
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed_ms = int((time.time() - start) * 1000)
            return {"status": "up", "http_status": resp.status, "latency_ms": elapsed_ms}
    except urllib.error.HTTPError as e:
        elapsed_ms = int((time.time() - start) * 1000)
        # Got response, even if error -- service is up
        return {"status": "up", "http_status": e.code, "latency_ms": elapsed_ms}
    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        return {"status": "down", "error": str(e)[:80], "latency_ms": elapsed_ms}


async def infra_status(request: Request) -> JSONResponse:
    """GET /infra/status -- Returns infrastructure status with health pings (parallel)."""
    now = datetime.now(timezone.utc).isoformat()
    loop = asyncio.get_event_loop()

    async def ping_svc(machine, svc):
        protocol = svc.get("protocol", "http")
        svc_status = await loop.run_in_executor(
            None, _ping_service, machine["ip"], svc["port"], svc["path"], 2.0, protocol
        )
        return {"name": svc["name"], "port": svc["port"], **svc_status}

    async def ping_machine(machine):
        service_results = await asyncio.gather(
            *[ping_svc(machine, svc) for svc in machine["services"]]
        )
        machine_up = any(s["status"] == "up" for s in service_results)
        return {
            "name": machine["name"],
            "hostname": machine["hostname"],
            "ip": machine["ip"],
            "role": machine["role"],
            "status": "up" if machine_up else "down",
            "services": list(service_results),
            "checked_at": now,
        }

    result = await asyncio.gather(*[ping_machine(m) for m in _INFRA_TOPOLOGY])
    return JSONResponse({"machines": list(result), "checked_at": now})


# ---------------------------------------------------------------------------
# Health server (separate port)
# ---------------------------------------------------------------------------
async def health(request: Request) -> JSONResponse:
    """Health check endpoint."""
    uptime = time.time() - _START_TIME
    return JSONResponse({
        "status": "ok",
        "service": "leroy-a2a",
        "version": config.AGENT_VERSION,
        "uptime_seconds": round(uptime, 1),
        "tasks": {
            "total": len(_task_meta),
            "pending": sum(1 for t in _task_meta.values() if t["status"] == "pending"),
            "working": sum(1 for t in _task_meta.values() if t["status"] == "working"),
            "waiting_for_pm": sum(1 for t in _task_meta.values() if t["status"] == "waiting_for_pm"),
            "completed": sum(1 for t in _task_meta.values() if t["status"] == "completed"),
            "failed": sum(1 for t in _task_meta.values() if t["status"] == "failed"),
            "cancelled": sum(1 for t in _task_meta.values() if t["status"] == "cancelled"),
        },
        "messages": {
            "total_pending": agent_bus.pending_count(),
            "agents": {a["name"]: {"unread": a["unread_count"], "pending": a["pending_response_count"]}
                       for a in agent_bus.agent_summary()},
        },
        "persistence": {
            "queue_depth": _persist_manager.queue_depth(),
            "dead_letter_depth": _persist_manager.dead_letter_depth(),
            "circuit_breaker": _persist_manager.circuit_state,
            "forge_brain_url": config.FORGE_BRAIN_URL,
            "recent_log": _persist_manager.recent_log(5),
        },
        "auth_enabled": auth.is_auth_enabled(),
        "observability": {
            "active_pids": {tid: pid for tid, pid in _active_pids.items()},
            "stuck_tasks": [
                {"task_id": tid, "detected_at": meta.get("_stuck_detected_at"), "reason": meta.get("_stuck_reason")}
                for tid, meta in _task_meta.items()
                if meta.get("_stuck_detected_at") and meta.get("status") == "working"
            ],
            "logs_dir": str(LOGS_DIR),
        },
    })

async def admin_circuit_reset(request: Request) -> JSONResponse:
    """POST /admin/circuit-reset -- Force-reset the persistence circuit breaker."""
    result = _persist_manager.reset_circuit()
    return JSONResponse(result)


health_app = Starlette(routes=[Route("/health", health)])


# ---------------------------------------------------------------------------
# Claude Code Hook Receiver endpoints
# ---------------------------------------------------------------------------

def _correlate_session_to_task(session_id: str) -> str | None:
    """Try to map a Claude Code session_id to an active task_id.

    First checks the cache. If not found, scans tasks in 'working' status
    that have active PIDs and assigns the first match. Returns None if no
    correlation can be made.
    """
    if session_id in _session_to_task:
        return _session_to_task[session_id]

    # Heuristic: find working tasks with active PIDs
    for task_id, pid in list(_active_pids.items()):
        if task_id not in _session_to_task.values():
            _session_to_task[session_id] = task_id
            logger.info("Hook: correlated session %s -> task %s (PID %d)", session_id[:12], task_id[:8], pid)
            return task_id
    return None


def _store_hook_event(event: dict, task_id: str | None) -> None:
    """Store a hook event in the global buffer and per-task index. Push to SSE subscribers."""
    # Global buffer with cap
    _hook_events.append(event)
    if len(_hook_events) > _HOOK_EVENTS_MAX:
        # Drop oldest events
        excess = len(_hook_events) - _HOOK_EVENTS_MAX
        del _hook_events[:excess]

    # Per-task index
    if task_id:
        if task_id not in _task_hook_events:
            _task_hook_events[task_id] = []
        _task_hook_events[task_id].append(event)

    # Broadcast to SSE subscribers
    event_data = json.dumps({"type": "hook_event", "event": event})
    dead = []
    for i, queue in enumerate(list(_hook_sse_subscribers)):
        try:
            queue.put_nowait(event_data)
        except (asyncio.QueueFull, Exception):
            dead.append(queue)
    for q in dead:
        try:
            _hook_sse_subscribers.remove(q)
        except ValueError:
            pass


async def hooks_tool_use(request: Request) -> JSONResponse:
    """POST /hooks/tool-use -- Receives PreToolUse/PostToolUse events from Claude Code hooks.

    No auth required (localhost only).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    session_id = body.get("session_id", "")
    task_id = _correlate_session_to_task(session_id) if session_id else None

    event = {
        "event_type": "tool_use",
        "session_id": session_id,
        "task_id": task_id,
        "cwd": body.get("cwd", ""),
        "hook_event_name": body.get("hook_event_name", ""),
        "tool_name": body.get("tool_name", ""),
        "tool_input": body.get("tool_input"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    _store_hook_event(event, task_id)
    logger.debug(
        "Hook tool-use: %s %s (session=%s, task=%s)",
        event["hook_event_name"], event["tool_name"],
        session_id[:12] if session_id else "?",
        task_id[:8] if task_id else "none",
    )
    return JSONResponse({"status": "ok"})


async def hooks_subagent(request: Request) -> JSONResponse:
    """POST /hooks/subagent -- Receives SubagentStart/SubagentStop events from Claude Code hooks.

    No auth required (localhost only).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    session_id = body.get("session_id", "")
    task_id = _correlate_session_to_task(session_id) if session_id else None

    event = {
        "event_type": "subagent",
        "session_id": session_id,
        "task_id": task_id,
        "cwd": body.get("cwd", ""),
        "hook_event_name": body.get("hook_event_name", ""),
        "subagent_id": body.get("subagent_id", ""),
        "subagent_type": body.get("subagent_type", ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # For SubagentStart, register the subagent's session so its child tool calls also correlate
    if body.get("hook_event_name") == "SubagentStart" and body.get("subagent_id") and task_id:
        _session_to_task[body["subagent_id"]] = task_id
        logger.debug("Hook: registered subagent %s -> task %s", body["subagent_id"][:12], task_id[:8])

    _store_hook_event(event, task_id)
    logger.debug(
        "Hook subagent: %s %s (session=%s, task=%s)",
        event["hook_event_name"], event.get("subagent_id", "")[:12],
        session_id[:12] if session_id else "?",
        task_id[:8] if task_id else "none",
    )
    return JSONResponse({"status": "ok"})


async def hooks_events_list(request: Request) -> JSONResponse:
    """GET /hooks/events -- Retrieve hook events, optionally filtered by task_id.

    Query params:
      ?task_id=<id>   -- filter events for a specific task
      ?limit=100      -- max events to return (default 100)
      ?since=<iso>    -- only return events after this ISO timestamp
    """
    task_id = request.query_params.get("task_id")
    limit = int(request.query_params.get("limit", "100"))
    since = request.query_params.get("since")

    if task_id:
        events = list(_task_hook_events.get(task_id, []))
    else:
        events = list(_hook_events)

    # Filter by since timestamp
    if since:
        events = [e for e in events if e.get("timestamp", "") > since]

    # Return most recent events up to limit
    events = events[-limit:]

    return JSONResponse({"events": events, "count": len(events)})


async def hooks_events_stream(request: Request) -> StreamingResponse:
    """GET /hooks/events/stream -- SSE endpoint for real-time hook events.

    Query params:
      ?task_id=<id>   -- filter for a specific task

    Streams events as SSE data lines. Heartbeat every 15 seconds.
    """
    task_id_filter = request.query_params.get("task_id")
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    _hook_sse_subscribers.append(queue)

    async def event_generator():
        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    # Apply task_id filter if specified
                    if task_id_filter:
                        try:
                            parsed = json.loads(data)
                            evt = parsed.get("event", {})
                            if evt.get("task_id") != task_id_filter:
                                continue
                        except Exception:
                            pass
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    heartbeat = json.dumps({
                        "type": "heartbeat",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    yield f"data: {heartbeat}\n\n"
        except Exception:
            pass
        finally:
            try:
                _hook_sse_subscribers.remove(queue)
            except ValueError:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Build combined ASGI app
# ---------------------------------------------------------------------------
def build_app():
    """Build the main Starlette app with A2A + custom routes."""
    # A2A protocol handler
    request_handler = DefaultRequestHandler(
        agent_executor=LeroyExecutor(),
        task_store=_task_store,
    )

    a2a_app_builder = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )

    # Get the base A2A starlette app
    a2a_starlette = a2a_app_builder.build()

    # Custom routes for Leroy pickup
    # Order matters: specific paths before parameterized ones
    custom_routes = [
        Route("/health", health, methods=["GET"]),
        Route("/tasks/pending", tasks_pending, methods=["GET"]),
        Route("/tasks/complete", tasks_complete, methods=["POST"]),
        Route("/tasks/stream", tasks_stream, methods=["GET"]),
        Route("/tasks/{task_id}/accept", task_accept, methods=["POST"]),
        Route("/tasks/{task_id}/cancel", task_cancel, methods=["POST"]),
        Route("/tasks/{task_id}/archive", task_archive, methods=["POST"]),
        Route("/tasks/{task_id}/unarchive", task_unarchive, methods=["POST"]),
        Route("/tasks/{task_id}/review", task_review, methods=["POST"]),
        Route("/tasks/{task_id}", task_delete, methods=["DELETE"]),
        Route("/tasks/{task_id}/logs", task_logs, methods=["GET"]),
        Route("/tasks/{task_id}/subtasks", subtask_list, methods=["GET"]),
        Route("/tasks/{task_id}/subtasks", subtask_update, methods=["POST"]),
        Route("/tasks/{task_id}/messages", task_messages, methods=["GET"]),
        Route("/tasks/{task_id}", task_detail, methods=["GET"]),
        Route("/tasks", tasks_list, methods=["GET"]),
        # Generic agent message bus
        Route("/messages/agents", bus_agents, methods=["GET"]),
        Route("/messages/{message_id}/respond", bus_respond, methods=["POST"]),
        Route("/messages/{message_id}/read", bus_read, methods=["POST"]),
        Route("/messages/{message_id}/response", bus_poll_response, methods=["GET"]),
        Route("/messages/{message_id}", bus_get, methods=["GET"]),
        Route("/messages", bus_send, methods=["POST"]),
        Route("/messages", bus_list, methods=["GET"]),
        # Legacy PM endpoints (backward compat -- Leroy subprocesses still use these)
        Route("/pm/messages/pending", pm_messages_pending, methods=["GET"]),
        Route("/pm/messages/{message_id}/respond", pm_messages_respond, methods=["POST"]),
        Route("/pm/messages/{message_id}/response", pm_messages_response_poll, methods=["GET"]),
        Route("/pm/messages", pm_messages_receive, methods=["POST"]),
        Route("/pm/messages", pm_messages_all, methods=["GET"]),
        # Agent registry
        Route("/agents", agents_list, methods=["GET"]),
        Route("/agents/{name}/heartbeat", agent_heartbeat, methods=["POST"]),
        Route("/agents/{name}", agent_delete, methods=["DELETE"]),
        # Activity feed
        Route("/activity/stream", activity_stream, methods=["GET"]),
        Route("/activity", activity_list, methods=["GET"]),
        Route("/activity", activity_create, methods=["POST"]),
        # PM Proposals (headless PM approval queue)
        Route("/pm/proposals/{proposal_id}/approve", proposals_approve, methods=["POST"]),
        Route("/pm/proposals/{proposal_id}/reject", proposals_reject, methods=["POST"]),
        Route("/pm/proposals", proposals_create, methods=["POST"]),
        Route("/pm/proposals", proposals_list, methods=["GET"]),
        # Ideas (backlog placeholders)
        Route("/ideas/{task_id}/promote", ideas_promote, methods=["POST"]),
        Route("/ideas", ideas_create, methods=["POST"]),
        # Specs pipeline
        Route("/specs", specs_list, methods=["GET"]),
        # Brain health proxy
        Route("/brain/health", brain_health, methods=["GET"]),
        # Infrastructure status
        Route("/infra/status", infra_status, methods=["GET"]),
        # Admin
        Route("/admin/circuit-reset", admin_circuit_reset, methods=["POST"]),
        # Claude Code hook receivers (no auth -- localhost only)
        Route("/hooks/tool-use", hooks_tool_use, methods=["POST"]),
        Route("/hooks/subagent", hooks_subagent, methods=["POST"]),
        Route("/hooks/events/stream", hooks_events_stream, methods=["GET"]),
        Route("/hooks/events", hooks_events_list, methods=["GET"]),
    ]

    # Prepend custom routes before A2A routes
    for route in reversed(custom_routes):
        a2a_starlette.router.routes.insert(0, route)

    return a2a_starlette


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    """Start Leroy A2A server + health server."""
    global _task_meta, _subtask_store, _agent_store, _activity_store, _proposal_store, _state_machine, _retry_budget

    auth.load_tokens()

    # Initialize SQLite-backed task, subtask, and message stores (loads from DB on startup)
    task_db.init(config.TASK_DB_PATH)
    _task_meta = task_db.task_meta
    _subtask_store = task_db.subtask_store
    broker.init_store(task_db.msg_store)
    agent_bus.init(task_db.msg_store, task_db.agent_store)
    _agent_store = task_db.agent_store
    _activity_store = task_db.activity_store
    _proposal_store = task_db.proposal_store

    # v2 State machine + retry budget
    _state_machine = TaskStateMachine(_task_meta)
    _retry_budget = RetryBudget(_task_meta)
    logger.info("v2 state machine and retry budget initialized")

    logger.info(
        "Task store loaded: %d task(s), %d subtask group(s), %d message(s)",
        len(_task_meta),
        len(task_db.subtask_store._cache),
        len(task_db.msg_store._messages),
    )

    # Seed known agents (Phase 1 -- no heartbeat integration yet)
    now = datetime.now(timezone.utc).isoformat()
    for seed in _SEED_AGENTS:
        existing = _agent_store.get(seed["name"])
        if existing is None:
            # Only seed if not already registered (preserves heartbeat data on restart)
            agent_record = dict(seed)
            agent_record["seeded_at"] = now
            _agent_store.upsert(agent_record)
            logger.info("Seeded agent: %s", seed["name"])
        else:
            logger.info("Agent %s already registered, skipping seed", seed["name"])

    # Emit startup activity event
    _emit_activity("leroy", "status_update", "Leroy A2A server started",
                   detail=f"Port {config.PORT}, {len(_task_meta)} task(s) loaded")

    # Start persistence manager (background retry thread + startup queue flush)
    _persist_manager.start()

    # Legacy broker flush thread removed -- webhook is dead, agent_bus handles messaging

    app = build_app()

    logger.info(
        "Starting Leroy A2A server on %s:%d (health on %d)",
        config.HOST, config.PORT, config.HEALTH_PORT,
    )

    # Run health server in background thread
    import threading

    def run_health():
        uvicorn.run(
            health_app,
            host=config.HOST,
            port=config.HEALTH_PORT,
            log_level="warning",
        )

    health_thread = threading.Thread(target=run_health, daemon=True)
    health_thread.start()

    # Start stuck task detector
    stuck_thread = threading.Thread(target=_stuck_task_detector, daemon=True)
    stuck_thread.start()
    logger.info("Stuck task detector started (check every %ds, threshold %ds)", _STUCK_CHECK_INTERVAL, _STUCK_THRESHOLD)

    # Run main A2A server
    uvicorn.run(
        app,
        host=config.HOST,
        port=config.PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
