#!/usr/bin/env python3
"""Kush Worker -- Picks up target=kush tasks from Haze A2A and executes locally.

Polls the Leroy A2A server on Haze for pending tasks with target=kush.
Accepts them, runs claude --print locally on Kush, posts results back.

No SSH. No pipes across machines. Full localhost access to brain, Qdrant,
Neo4j, Postgres, classifier, sentinel.

Usage:
    python3 kush_worker.py              # foreground
    python3 kush_worker.py --once       # single poll cycle then exit (testing)

Environment:
    LEROY_A2A_URL       Haze A2A URL (default: http://ADM-MSHA-80085.local:9800)
    LEROY_A2A_TOKEN     Auth token for the bus
    POLL_INTERVAL       Seconds between polls (default: 30)
    OPS_AGENT_DIR       Path to ops-agent dir (default: ~/Projects/ops-agent)
    CLAUDE_BIN          Path to claude binary (default: /opt/homebrew/bin/claude)
    MAX_TURNS           Max agent turns per task (default: 50)
"""

import json
import logging
import os
import selectors
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
A2A_URL = os.getenv("LEROY_A2A_URL", "http://ADM-MSHA-80085.local:9800").rstrip("/")
A2A_TOKEN = os.getenv("LEROY_A2A_TOKEN", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))
OPS_AGENT_DIR = Path(os.getenv("OPS_AGENT_DIR", Path.home() / "Projects" / "ops-agent"))
CLAUDE_BIN = os.getenv("CLAUDE_BIN", "/opt/homebrew/bin/claude")
MAX_TURNS = int(os.getenv("MAX_TURNS", "50"))
INACTIVITY_TIMEOUT = int(os.getenv("INACTIVITY_TIMEOUT", "600"))  # 10 min no output = kill
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "9815"))
TARGET = "kush"

# ---------------------------------------------------------------------------
# Worker state (updated by poll loop; read by health endpoint)
# ---------------------------------------------------------------------------
_worker_state: dict = {
    "status": "starting",
    "current_task_id": None,
    "start_time": None,       # set in main()
    "last_heartbeat": None,   # ISO string, set by _heartbeat()
}

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("kush-worker")

# ---------------------------------------------------------------------------
# Health endpoint (stdlib only -- no new dependencies)
# ---------------------------------------------------------------------------
class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that serves worker status as JSON."""

    def do_GET(self):  # noqa: N802
        if self.path not in ("/health", "/"):
            self.send_response(404)
            self.end_headers()
            return

        now = time.monotonic()
        start = _worker_state["start_time"]
        uptime_s = int(now - start) if start is not None else 0

        payload = {
            "status": _worker_state["status"],
            "current_task_id": _worker_state["current_task_id"],
            "uptime_seconds": uptime_s,
            "last_heartbeat": _worker_state["last_heartbeat"],
            "worker": "kush-worker",
            "target": TARGET,
            "a2a_url": A2A_URL,
        }
        body = json.dumps(payload, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: N802
        """Suppress default access log -- use our logger only for errors."""
        pass


def start_health_server():
    """Start the health HTTP server in a background daemon thread."""
    server = HTTPServer(("127.0.0.1", HEALTH_PORT), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, name="health-server", daemon=True)
    thread.start()
    log.info("Health endpoint listening on http://127.0.0.1:%d/health", HEALTH_PORT)
    return server


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _headers():
    h = {"Content-Type": "application/json"}
    if A2A_TOKEN:
        h["Authorization"] = f"Bearer {A2A_TOKEN}"
    return h


def _get(path, params=None):
    """GET from A2A server. Returns parsed JSON or None on failure."""
    try:
        r = requests.get(f"{A2A_URL}{path}", headers=_headers(), params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error("GET %s failed: %s", path, e)
        return None


def _post(path, body=None):
    """POST to A2A server. Returns parsed JSON or None on failure."""
    try:
        r = requests.post(f"{A2A_URL}{path}", headers=_headers(), json=body or {}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error("POST %s failed: %s", path, e)
        return None


HEARTBEAT_INTERVAL = 10  # every N poll cycles (~5 minutes at 30s poll interval)

def _heartbeat():
    """Report this worker to the agent roster on Haze. Failure-tolerant."""
    result = _post("/agents/kush-worker/heartbeat", {
        "type": "worker",
        "machine": "kush",
        "status": "active",
        "metadata": {"description": "Kush task execution worker daemon"},
    })
    _worker_state["last_heartbeat"] = datetime.now(timezone.utc).isoformat()
    if result is None:
        log.warning("Heartbeat failed -- agent roster not updated")
    else:
        log.debug("Heartbeat sent")


# ---------------------------------------------------------------------------
# Task execution
# ---------------------------------------------------------------------------
def _load_system_prompt():
    """Load ops persona as system prompt."""
    persona_file = OPS_AGENT_DIR / "personas" / "ops.md"
    shared_file = OPS_AGENT_DIR / "personas" / "shared_context.md"

    parts = []
    if persona_file.exists():
        parts.append(persona_file.read_text(encoding="utf-8"))
    if shared_file.exists():
        parts.append(shared_file.read_text(encoding="utf-8"))

    return "\n\n---\n\n".join(parts) if parts else ""


def execute_task(task_id: str, spec: str) -> tuple[str, bool]:
    """Run a task spec via claude --print. Returns (result_text, success)."""
    log.info("Executing task %s (spec: %d chars)", task_id, len(spec))

    system_prompt = _load_system_prompt()
    settings_file = OPS_AGENT_DIR / ".claude" / "ops-settings.json"

    cmd = [
        CLAUDE_BIN,
        "-p", spec,
        "--output-format", "text",
        "--permission-mode", "bypassPermissions",
        "--no-session-persistence",
        "--max-turns", str(MAX_TURNS),
        "--setting-sources", "user",
    ]

    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])
    if settings_file.exists():
        cmd.extend(["--settings", str(settings_file)])

    # Log file for this task
    log_dir = OPS_AGENT_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"{task_id}.log"

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    env["PATH"] = f"/opt/homebrew/bin:{env.get('PATH', '/usr/bin:/bin')}"
    env["LEROY_TASK_ID"] = task_id

    try:
        with open(log_file, "w") as lf:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=lf,
                text=True,
                cwd=str(OPS_AGENT_DIR),
                env=env,
            )

            # Stream stdout with inactivity timeout
            output_lines = []
            sel = selectors.DefaultSelector()
            sel.register(proc.stdout, selectors.EVENT_READ)
            last_activity = time.monotonic()

            while True:
                events = sel.select(timeout=30)  # check every 30s
                if events:
                    line = proc.stdout.readline()
                    if not line:
                        break  # EOF
                    output_lines.append(line)
                    last_activity = time.monotonic()
                    lf.write(f"[stdout] {line}")
                    lf.flush()
                else:
                    # No output -- check inactivity timeout
                    idle = time.monotonic() - last_activity
                    if idle > INACTIVITY_TIMEOUT:
                        log.warning("Task %s killed after %ds inactivity (timeout=%ds)",
                                    task_id, int(idle), INACTIVITY_TIMEOUT)
                        proc.kill()
                        proc.wait()
                        sel.close()
                        result = "".join(output_lines).strip()
                        if not result:
                            result = f"Task killed after {int(idle)}s inactivity. No output produced."
                        return result, False
                    # Also check if process died without EOF
                    if proc.poll() is not None:
                        break

            sel.close()
            proc.wait()

        result = "".join(output_lines).strip()
        success = proc.returncode == 0

        if not success:
            log.warning("Task %s exited with code %d", task_id, proc.returncode)
            if not result:
                result = f"Task failed with exit code {proc.returncode}. Check log: {log_file}"

        log.info("Task %s completed (success=%s, result: %d chars)", task_id, success, len(result))
        return result, success

    except Exception as e:
        log.error("Task %s execution error: %s", task_id, e)
        return f"Execution error: {e}", False


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------
def poll_and_execute():
    """Single poll cycle: check for pending kush tasks, execute one."""
    data = _get("/tasks", params={"status": "pending", "target": TARGET})
    if not data:
        return False

    tasks = data.get("tasks", [])
    if not tasks:
        return False

    # Take the oldest pending task
    task = tasks[0]
    task_id = task["task_id"]
    spec = task.get("spec", "")

    log.info("Picked up task %s (spec preview: %s)", task_id, spec[:80])

    # Accept the task
    accept_result = _post(f"/tasks/{task_id}/accept")
    if not accept_result or accept_result.get("error"):
        log.error("Failed to accept task %s: %s", task_id, accept_result)
        return False

    # Track active task in health state
    _worker_state["current_task_id"] = task_id
    _worker_state["status"] = "busy"

    # Execute
    result, success = execute_task(task_id, spec)

    # Report completion
    completion = _post("/tasks/complete", {
        "task_id": task_id,
        "result": result,
    })

    if not completion or completion.get("error"):
        log.error("Failed to report completion for %s: %s", task_id, completion)
        # Try to mark as failed
        _post("/tasks/complete", {
            "task_id": task_id,
            "result": f"Worker completed but failed to report: {result[:500]}",
        })

    # Send bus message about completion
    status_word = "completed" if success else "failed"
    _post("/messages", {
        "from": "ops-kush",
        "to": "pm",
        "type": "status_update",
        "content": f"Task {task_id} {status_word} on Kush. Result: {result[:200]}",
        "task_id": task_id,
    })

    log.info("Task %s reported as %s", task_id, status_word)

    # Clear active task from health state
    _worker_state["current_task_id"] = None
    _worker_state["status"] = "idle"

    return True


def main():
    single_run = "--once" in sys.argv

    log.info("Kush worker starting (target=%s, poll=%ds, a2a=%s)",
             TARGET, POLL_INTERVAL, A2A_URL)

    # Initialize worker state
    _worker_state["start_time"] = time.monotonic()
    _worker_state["status"] = "idle"

    # Start health endpoint (daemon thread -- stops when main process exits)
    if not single_run:
        start_health_server()

    # Health check on startup
    health = _get("/health")
    if health:
        log.info("A2A server healthy: %s v%s", health.get("status"), health.get("version"))
    else:
        log.error("A2A server unreachable at startup")
        if single_run:
            sys.exit(1)

    # Register with agent roster immediately on startup
    _heartbeat()

    if single_run:
        found = poll_and_execute()
        if not found:
            log.info("No pending kush tasks found")
        return

    # Main poll loop
    poll_cycle = 0
    while True:
        try:
            poll_and_execute()
            poll_cycle += 1
            if poll_cycle % HEARTBEAT_INTERVAL == 0:
                _heartbeat()
        except KeyboardInterrupt:
            log.info("Shutting down")
            break
        except Exception as e:
            log.error("Poll cycle error: %s", e)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
