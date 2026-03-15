"""Admin, health, persistence, and hook route handlers.

Extracted from server.py -- handles health check, circuit breaker, persistence
gateway, and Claude Code hook receivers.
"""
import asyncio
import json
import time
from datetime import datetime, timezone
from uuid import uuid4

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

import auth
import agent_bus
import config
import server_state as state
from execution import _active_pids, LOGS_DIR


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

async def health(request: Request) -> JSONResponse:
    """Health check endpoint."""
    uptime = time.time() - state._START_TIME
    return JSONResponse({
        "status": "ok",
        "service": "leroy-a2a",
        "version": config.AGENT_VERSION,
        "uptime_seconds": round(uptime, 1),
        "tasks": {
            "total": len(state._task_meta),
            "pending": sum(1 for t in state._task_meta.values() if t["status"] == "pending"),
            "working": sum(1 for t in state._task_meta.values() if t["status"] == "working"),
            "waiting_for_pm": sum(1 for t in state._task_meta.values() if t["status"] == "waiting_for_pm"),
            "completed": sum(1 for t in state._task_meta.values() if t["status"] == "completed"),
            "failed": sum(1 for t in state._task_meta.values() if t["status"] == "failed"),
            "cancelled": sum(1 for t in state._task_meta.values() if t["status"] == "cancelled"),
        },
        "messages": {
            "total_pending": agent_bus.pending_count(),
            "agents": {a["name"]: {"unread": a["unread_count"], "pending": a["pending_response_count"]}
                       for a in agent_bus.agent_summary()},
        },
        "persistence": {
            "queue_depth": state._persist_manager.queue_depth(),
            "dead_letter_depth": state._persist_manager.dead_letter_depth(),
            "circuit_breaker": state._persist_manager.circuit_state,
            "forge_brain_url": config.FORGE_BRAIN_URL,
            "recent_log": state._persist_manager.recent_log(5),
        },
        "auth_enabled": auth.is_auth_enabled(),
        "observability": {
            "active_pids": {tid: pid for tid, pid in _active_pids.items()},
            "stuck_tasks": [
                {"task_id": tid, "detected_at": meta.get("_stuck_detected_at"), "reason": meta.get("_stuck_reason")}
                for tid, meta in state._task_meta.items()
                if meta.get("_stuck_detected_at") and meta.get("status") == "working"
            ],
            "logs_dir": str(LOGS_DIR),
        },
    })

async def admin_circuit_reset(request: Request) -> JSONResponse:
    """POST /admin/circuit-reset -- Force-reset the persistence circuit breaker."""
    result = state._persist_manager.reset_circuit()
    return JSONResponse(result)


async def http_persist(request: Request) -> JSONResponse:
    """POST /persist -- HTTP gateway for shell hooks to persist content to forge-brain.

    Accepts JSON body with: content (required, min 100 chars), session_title (opt),
    session_tags (opt list), source (opt, default "hook/http").
    Returns: {"status": "queued"|"error", "queue_depth": int, "circuit_state": dict}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "error": "invalid or missing JSON body"}, status_code=400)

    content = body.get("content", "")
    if not content or not isinstance(content, str):
        return JSONResponse({"status": "error", "error": "missing required field: content"}, status_code=400)
    if len(content) < 100:
        return JSONResponse(
            {"status": "error", "error": f"content too short: {len(content)} chars (minimum 100)"},
            status_code=400,
        )

    payload = {
        "id": uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "attempt_count": 0,
        "last_attempt": None,
        "task_id": None,
        "content": content,
        "session_title": body.get("session_title") or "Hook Persist",
        "session_tags": body.get("session_tags") or ["hook", "http"],
        "source": body.get("source") or "hook/http",
    }

    state._persist_manager._enqueue(payload)
    # Layer 1+3: record this as a persist event for the given source
    state._persist_manager.record_persist(
        payload.get("source", "hook/http"),
        chars=len(content),
        brain_ack=False,  # queued, not yet confirmed by brain
    )
    return JSONResponse({
        "status": "queued",
        "queue_depth": state._persist_manager.queue_depth(),
        "circuit_state": state._persist_manager.circuit_state,
    })


async def http_persist_append(request: Request) -> JSONResponse:
    """POST /persist/append -- Append content to an existing forge-brain session.

    Accepts JSON body with: session_id (required), content (required, min 100 chars).
    Calls forge-brain persist_append MCP tool via thread executor.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "error": "invalid or missing JSON body"}, status_code=400)

    session_id = body.get("session_id", "")
    content = body.get("content", "")
    if not session_id or not isinstance(session_id, str):
        return JSONResponse({"status": "error", "error": "missing required field: session_id"}, status_code=400)
    if not content or not isinstance(content, str) or len(content) < 100:
        return JSONResponse(
            {"status": "error", "error": f"content too short or missing (minimum 100 chars)"},
            status_code=400,
        )

    async def _call_append() -> dict:
        from mcp.client.streamable_http import streamablehttp_client
        from mcp import ClientSession
        headers = {"Authorization": f"Bearer {config.FORGE_BRAIN_TOKEN}"}
        async with streamablehttp_client(config.FORGE_BRAIN_URL, headers=headers, timeout=30.0) as (read, write, _):
            async with ClientSession(read, write) as sess:
                await sess.initialize()
                result = await sess.call_tool("persist_append", {"session_id": session_id, "content": content})
                return {"raw": str(result)[:200]}

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: asyncio.run(_call_append()))
        return JSONResponse({"status": "appended", "circuit_state": state._persist_manager.circuit_state})
    except Exception as e:
        state.logger.warning("http_persist_append failed: %s", e)
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)


async def http_persist_last_get(request: Request) -> JSONResponse:
    """GET /persist/last -- Return last persist timestamp for a source.

    Query params:
      source (optional): filter by source (e.g. "pm"). If omitted, return all sources.

    Response: {"source": "pm", "last_persist": "ISO8601|null", "age_seconds": N|null, "stale": bool}
    """
    source = request.query_params.get("source")
    result = state._persist_manager.get_last_persist(source)
    return JSONResponse(result)


async def http_persist_last_post(request: Request) -> JSONResponse:
    """POST /persist/last -- Record a persist event from an external caller (e.g. hook script).

    Body: {"source": "pm", "timestamp": "ISO8601" (opt), "chars": N (opt)}
    Updates in-memory tracking and appends to local ledger.
    Response: {"status": "ok", "recorded": true}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "error": "invalid JSON body"}, status_code=400)

    source = body.get("source", "unknown")
    timestamp = body.get("timestamp") or datetime.now(timezone.utc).isoformat()
    chars = int(body.get("chars", 0))

    state._persist_manager.record_persist(source, timestamp=timestamp, chars=chars, brain_ack=True)
    state.logger.debug("POST /persist/last: recorded source=%s ts=%s", source, timestamp)
    return JSONResponse({"status": "ok", "recorded": True})


# ---------------------------------------------------------------------------
# Claude Code Hook Receiver endpoints
# ---------------------------------------------------------------------------

def _correlate_session_to_task(session_id: str) -> str | None:
    """Try to map a Claude Code session_id to an active task_id.

    First checks the cache. If not found, scans tasks in 'working' status
    that have active PIDs and assigns the first match. Returns None if no
    correlation can be made.
    """
    if session_id in state._session_to_task:
        return state._session_to_task[session_id]

    # Heuristic: find working tasks with active PIDs
    for task_id, pid in list(_active_pids.items()):
        if task_id not in state._session_to_task.values():
            state._session_to_task[session_id] = task_id
            state.logger.info("Hook: correlated session %s -> task %s (PID %d)", session_id[:12], task_id[:8], pid)
            return task_id
    return None


def _store_hook_event(event: dict, task_id: str | None) -> None:
    """Store a hook event in the global buffer and per-task index. Push to SSE subscribers."""
    # Global buffer with cap
    state._hook_events.append(event)
    if len(state._hook_events) > state._HOOK_EVENTS_MAX:
        # Drop oldest events
        excess = len(state._hook_events) - state._HOOK_EVENTS_MAX
        del state._hook_events[:excess]

    # Per-task index
    if task_id:
        if task_id not in state._task_hook_events:
            state._task_hook_events[task_id] = []
        state._task_hook_events[task_id].append(event)

    # Broadcast to SSE subscribers
    event_data = json.dumps({"type": "hook_event", "event": event})
    dead = []
    for i, queue in enumerate(list(state._hook_sse_subscribers)):
        try:
            queue.put_nowait(event_data)
        except (asyncio.QueueFull, Exception):
            dead.append(queue)
    for q in dead:
        try:
            state._hook_sse_subscribers.remove(q)
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
    state.logger.debug(
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
        state._session_to_task[body["subagent_id"]] = task_id
        state.logger.debug("Hook: registered subagent %s -> task %s", body["subagent_id"][:12], task_id[:8])

    _store_hook_event(event, task_id)
    state.logger.debug(
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
        events = list(state._task_hook_events.get(task_id, []))
    else:
        events = list(state._hook_events)

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
    state._hook_sse_subscribers.append(queue)

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
                state._hook_sse_subscribers.remove(queue)
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
