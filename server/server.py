"""Leroy A2A Server.

Google A2A protocol server for PM-to-Leroy task lifecycle.
When a spec arrives via A2A, spawns `claude -p` to execute it automatically.
Custom endpoints for task status and management.
Separate health server on HEALTH_PORT.
"""
import asyncio
import json
import logging
import os
import signal
import shutil
import subprocess
import sys
import time
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
import message_broker as broker
import task_db

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

# SSE subscribers: set of asyncio.Queue instances for broadcasting task updates
_sse_subscribers: set = set()
_sse_lock = asyncio.Lock()

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

# Persistence manager -- persists task completions to Aianna (forge-brain)
_persist_manager = pm.PersistenceManager()


# ---------------------------------------------------------------------------
# Claude CLI execution engine
# ---------------------------------------------------------------------------
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", shutil.which("claude") or "claude")
WORK_DIR = os.environ.get("LEROY_WORK_DIR", str(Path(__file__).parent.parent))
MAX_TASK_TIMEOUT = int(os.environ.get("LEROY_TASK_TIMEOUT", "3600"))  # 1 hour default

# System prompt injected into every claude -p invocation
LEROY_SYSTEM_PROMPT = """You are Leroy, the Engineering Lead for the FORGE ecosystem.
You receive specs from PM and execute them. You have full tool access.
Execute the spec completely. Return a structured result with:
- What was done
- Files created/modified
- Success criteria pass/fail
- Any issues encountered
Be thorough but concise. No filler.

When you need to communicate with PM during task execution, use the PM messaging API:

  POST http://127.0.0.1:9800/pm/messages
  Content-Type: application/json
  Body: {
    "type": "question|status_update|decision_gate|blocker|deliverable_ready",
    "task_id": "<your LEROY_TASK_ID env var>",
    "content": "your message text",
    "options": ["option1", "option2"],  // for decision_gate only
    "context": "relevant background for PM",
    "requires_response": true|false
  }
  Returns: {"message_id": "...", "status": "queued"}

Message types:
- status_update: non-blocking progress report, continues immediately
- deliverable_ready: non-blocking notification that work is ready for review
- question: BLOCKING -- wait for PM response before continuing
- decision_gate: BLOCKING -- PM picks from options before you continue
- blocker: BLOCKING -- you cannot proceed without PM input

For BLOCKING messages, after POSTing, poll for PM's response:
  GET http://127.0.0.1:9800/pm/messages/{message_id}/response
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


def _run_claude_sync(task_id: str, spec: str) -> None:
    """Run claude -p in a subprocess. Called from a background thread."""
    logger.info("Task %s: spawning claude -p (timeout=%ds)", task_id, MAX_TASK_TIMEOUT)
    _task_meta[task_id]["status"] = "working"
    _broadcast_task_update_sync(task_id)

    proc = None
    try:
        # Start in a new process group so we can kill the whole tree on timeout
        proc = subprocess.Popen(
            [
                CLAUDE_BIN,
                "-p", spec,
                "--output-format", "text",
                "--system-prompt", LEROY_SYSTEM_PROMPT,
                "--dangerously-skip-permissions",
                "--no-session-persistence",
                "--model", "sonnet",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=WORK_DIR,
            env={**os.environ, "CLAUDE_CODE_ENTRYPOINT": "leroy-a2a", "LEROY_TASK_ID": task_id},
            start_new_session=True,  # new process group
        )

        stdout, stderr = proc.communicate(timeout=MAX_TASK_TIMEOUT)

        if proc.returncode == 0:
            _task_meta[task_id]["status"] = "completed"
            _task_meta[task_id]["result"] = stdout
            logger.info("Task %s: completed (%d chars output)", task_id, len(stdout))
            _broadcast_task_update_sync(task_id)
        else:
            _task_meta[task_id]["status"] = "failed"
            _task_meta[task_id]["result"] = (
                f"Exit code {proc.returncode}\n"
                f"STDOUT:\n{stdout}\n"
                f"STDERR:\n{stderr}"
            )
            logger.error("Task %s: claude exited with code %d", task_id, proc.returncode)
            _broadcast_task_update_sync(task_id)

    except subprocess.TimeoutExpired:
        # Kill the entire process group (claude + all children)
        if proc:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        _task_meta[task_id]["status"] = "failed"
        _task_meta[task_id]["result"] = f"Task timed out after {MAX_TASK_TIMEOUT}s"
        logger.error("Task %s: timed out after %ds", task_id, MAX_TASK_TIMEOUT)
        _broadcast_task_update_sync(task_id)
    except Exception as e:
        _task_meta[task_id]["status"] = "failed"
        _task_meta[task_id]["result"] = f"Execution error: {e}"
        logger.exception("Task %s: execution error", task_id)
        _broadcast_task_update_sync(task_id)
    finally:
        _task_meta[task_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
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
    """Receives specs from PM, queues them for interactive Leroy pickup."""

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

        # Store task metadata as pending -- interactive Leroy picks it up
        _task_meta[task_id] = {
            "task_id": task_id,
            "spec": spec_text,
            "status": "pending",
            "result": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
        }

        logger.info("Task %s received (spec length: %d chars) -- queued for pickup", task_id, len(spec_text))

        # Trigger persistence queue flush (non-blocking)
        _persist_manager.flush_if_ready()

        _broadcast_task_update_sync(task_id)

        # Respond immediately via A2A protocol -- task is queued, not executing
        await event_queue.enqueue_event(
            new_agent_text_message(
                f"Task {task_id} received and queued. "
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

    _task_meta[task_id]["status"] = "completed"
    _task_meta[task_id]["result"] = result
    _task_meta[task_id]["completed_at"] = datetime.now(timezone.utc).isoformat()

    logger.info("Task %s completed", task_id)
    _broadcast_task_update_sync(task_id)

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
    # Default: return ALL tasks. Only exclude archived if explicitly requested via ?include_archived=false.
    exclude_archived = request.query_params.get("include_archived", "").lower() in ("0", "false", "no")
    tasks = list(_task_meta.values())
    if status_filter:
        # Status filter: return all tasks with that status.
        tasks = [t for t in tasks if t["status"] == status_filter]
    if exclude_archived:
        # Only hide archived tasks when explicitly asked.
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

    if _task_meta[task_id]["status"] not in ("pending", "working"):
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

    message_id = broker.store_message(body)
    requires_response = body["type"] in ("question", "decision_gate", "blocker")
    logger.info(
        "PM message received: type=%s task=%s message_id=%s requires_response=%s",
        body["type"], body.get("task_id"), message_id, requires_response,
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
    msg = broker.get_message(message_id)
    if msg is None:
        return JSONResponse({"error": f"message {message_id} not found"}, status_code=404)

    response = broker.poll_response(message_id)
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

    msg = broker.get_message(message_id)
    if msg is None:
        return JSONResponse({"error": f"message {message_id} not found"}, status_code=404)

    ok = broker.store_response(message_id, response_text)
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
    return JSONResponse({"status": "ok", "message_id": message_id, "task_id": task_id})


async def pm_messages_pending(request: Request) -> JSONResponse:
    """GET /pm/messages/pending -- PM reads unread messages awaiting response."""
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    pending = broker.list_pending()
    return JSONResponse({"messages": pending, "count": len(pending)})


async def pm_messages_all(request: Request) -> JSONResponse:
    """GET /pm/messages -- PM reads all recent messages (responded or not)."""
    client = _check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    limit = int(request.query_params.get("limit", "20"))
    messages = broker.list_all(limit=limit)
    return JSONResponse({"messages": messages, "count": len(messages)})


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

    all_messages = broker.list_all(limit=200)
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
        "pm_messages": {
            "pending_pm_response": broker.pending_count(),
            # pm_webhook_registered now validates PID alive + HTTP reachable,
            # not just "file exists". Eliminates stale-registry false positives.
            "pm_webhook_registered": broker.pm_webhook_registered(),
        },
        "persistence": {
            "queue_depth": _persist_manager.queue_depth(),
            "dead_letter_depth": _persist_manager.dead_letter_depth(),
            "circuit_breaker": _persist_manager.circuit_state,
            "forge_brain_url": config.FORGE_BRAIN_URL,
            "recent_log": _persist_manager.recent_log(5),
        },
        "auth_enabled": auth.is_auth_enabled(),
    })

health_app = Starlette(routes=[Route("/health", health)])


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
        Route("/tasks/{task_id}", task_delete, methods=["DELETE"]),
        Route("/tasks/{task_id}/subtasks", subtask_list, methods=["GET"]),
        Route("/tasks/{task_id}/subtasks", subtask_update, methods=["POST"]),
        Route("/tasks/{task_id}/messages", task_messages, methods=["GET"]),
        Route("/tasks/{task_id}", task_detail, methods=["GET"]),
        Route("/tasks", tasks_list, methods=["GET"]),
        # PM <-> Leroy bidirectional messaging
        Route("/pm/messages/pending", pm_messages_pending, methods=["GET"]),
        Route("/pm/messages/{message_id}/respond", pm_messages_respond, methods=["POST"]),
        Route("/pm/messages/{message_id}/response", pm_messages_response_poll, methods=["GET"]),
        Route("/pm/messages", pm_messages_receive, methods=["POST"]),
        Route("/pm/messages", pm_messages_all, methods=["GET"]),
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
    global _task_meta, _subtask_store

    auth.load_tokens()

    # Initialize SQLite-backed task, subtask, and message stores (loads from DB on startup)
    task_db.init(config.TASK_DB_PATH)
    _task_meta = task_db.task_meta
    _subtask_store = task_db.subtask_store
    broker.init_store(task_db.msg_store)
    logger.info(
        "Task store loaded: %d task(s), %d subtask group(s), %d message(s)",
        len(_task_meta),
        len(task_db.subtask_store._cache),
        len(task_db.msg_store._messages),
    )

    # Start persistence manager (background retry thread + startup queue flush)
    _persist_manager.start()

    # Start message broker flush thread (retries unforwarded messages when PM comes online)
    broker.start_flush_thread()

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

    # Run main A2A server
    uvicorn.run(
        app,
        host=config.HOST,
        port=config.PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
