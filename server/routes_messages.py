"""Message bus route handlers.

Extracted from server.py -- handles PM messaging and generic agent bus endpoints.
"""
from datetime import datetime, timezone

from starlette.requests import Request
from starlette.responses import JSONResponse

import agent_bus
import server_state as state
from state_machine import TaskState


# ---------------------------------------------------------------------------
# PM <-> Leroy bidirectional messaging endpoints
# ---------------------------------------------------------------------------

async def pm_messages_receive(request: Request) -> JSONResponse:
    """POST /pm/messages -- Leroy subprocess sends a message to PM.

    Body: full message schema (see agent_bus.py docstring).
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
    state.logger.info(
        "PM message received: type=%s task=%s message_id=%s requires_response=%s",
        body["type"], body.get("task_id"), message_id, requires_response,
    )

    # Emit activity event for PM message
    severity = "warn" if requires_response else "info"
    state._emit_activity(
        "leroy", "decision_requested" if requires_response else "status_update",
        f"PM message ({body['type']}): {body.get('content', '')[:80]}",
        task_id=body.get("task_id"),
        severity=severity,
    )

    # Update task status to "waiting_for_pm" if blocking
    task_id = body.get("task_id")
    if requires_response and task_id and task_id in state._task_meta:
        try:
            if state._state_machine:
                state._state_machine.transition(task_id, TaskState.BLOCKED, reason="waiting_for_pm_response")
        except Exception as _sm_err:
            state.logger.warning("State machine transition failed for %s: %s", task_id, _sm_err)
        state._task_meta[task_id]["status"] = "waiting_for_pm"  # legacy compat (BLOCKED fires handlers; override string for UI)
        state._task_meta[task_id]["waiting_on_message"] = message_id
        state._broadcast_task_update_sync(task_id)

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
    client = state._check_auth(request)
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
    if task_id and task_id in state._task_meta:
        if state._task_meta[task_id].get("status") == "waiting_for_pm":
            try:
                if state._state_machine:
                    state._state_machine.transition(task_id, TaskState.RUNNING, reason="pm_response_received")
            except Exception as _sm_err:
                state.logger.warning("State machine transition failed for %s: %s", task_id, _sm_err)
            state._task_meta[task_id]["status"] = "working"  # fallback / legacy compat
            state._task_meta[task_id].pop("waiting_on_message", None)
            state._broadcast_task_update_sync(task_id)

    state.logger.info("PM responded to message %s (task %s)", message_id, task_id)
    state._emit_activity("pm", "decision_requested",
                   f"PM responded to {msg.get('type', 'message')} (task {(task_id or '')[:8]})",
                   task_id=task_id, severity="info")
    return JSONResponse({"status": "ok", "message_id": message_id, "task_id": task_id})


async def pm_messages_pending(request: Request) -> JSONResponse:
    """GET /pm/messages/pending -- PM reads unread messages awaiting response."""
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    pending = agent_bus.list_messages(to="pm", pending=True)
    return JSONResponse({"messages": pending, "count": len(pending)})


async def pm_messages_all(request: Request) -> JSONResponse:
    """GET /pm/messages -- PM reads all recent messages (responded or not)."""
    client = state._check_auth(request)
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
    state._emit_activity(
        msg["from"],
        "message_sent",
        f"{msg['from']} -> {msg['to']}: {msg['content'][:80]}",
        task_id=msg.get("task_id"),
        severity=severity,
    )

    # If blocking message linked to a task, update task status
    if msg["requires_response"] and msg.get("task_id") and msg["task_id"] in state._task_meta:
        _bus_task_id = msg["task_id"]
        try:
            if state._state_machine:
                state._state_machine.transition(_bus_task_id, TaskState.BLOCKED, reason="waiting_for_pm_response")
        except Exception as _sm_err:
            state.logger.warning("State machine transition failed for %s: %s", _bus_task_id, _sm_err)
        state._task_meta[_bus_task_id]["status"] = "waiting_for_pm"  # legacy compat (BLOCKED fires handlers; override string for UI)
        state._task_meta[_bus_task_id]["waiting_on_message"] = msg["message_id"]
        state._broadcast_task_update_sync(_bus_task_id)

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
    if task_id and task_id in state._task_meta:
        if state._task_meta[task_id].get("status") == "waiting_for_pm":
            try:
                if state._state_machine:
                    state._state_machine.transition(task_id, TaskState.RUNNING, reason="message_response_received")
            except Exception as _sm_err:
                state.logger.warning("State machine transition failed for %s: %s", task_id, _sm_err)
            state._task_meta[task_id]["status"] = "working"  # fallback / legacy compat
            state._task_meta[task_id].pop("waiting_on_message", None)
            state._broadcast_task_update_sync(task_id)

    state._emit_activity(
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
