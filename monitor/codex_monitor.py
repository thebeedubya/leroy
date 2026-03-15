"""Headless Codex review monitor.

Polls the FORGE agent bus for messages addressed to `codex`, runs Codex CLI
code reviews for recognized review requests, posts the result back to the bus,
and marks the source message read after successful handling.

Usage:
    python3 monitor/codex_monitor.py
    python3 monitor/codex_monitor.py --once
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


BUS_URL = os.environ.get("CODEX_BUS_URL", "http://127.0.0.1:9800").rstrip("/")
BUS_TOKEN = os.environ.get("CODEX_BUS_TOKEN", "")
POLL_INTERVAL = int(os.environ.get("CODEX_MONITOR_INTERVAL", "30"))
REVIEW_TIMEOUT = int(os.environ.get("CODEX_REVIEW_TIMEOUT", "1200"))
CODEX_BIN = os.environ.get("CODEX_BIN") or shutil.which("codex") or "/opt/homebrew/bin/codex"
DEFAULT_REPO = os.environ.get("CODEX_DEFAULT_REPO", "").strip()

PROJECT_ROOT = Path(os.environ.get("CODEX_MONITOR_PROJECT_ROOT", str(Path.home() / "Projects" / "leroy")))
STATE_FILE = Path(os.environ.get("CODEX_MONITOR_STATE", str(PROJECT_ROOT / "data" / "codex-monitor-state.json")))
LOG_FILE = Path(os.environ.get("CODEX_MONITOR_LOG", str(PROJECT_ROOT / "data" / "codex-monitor.log")))

REVIEW_MESSAGE_TYPES = {"code_review_request", "review_request", "code_review", "review"}
IGNORE_SENDERS = {"codex-monitor"}


LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [codex-monitor] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("codex-monitor")


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {BUS_TOKEN}",
    }


def _load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load state file, starting fresh: %s", exc)
    return {
        "ignored_message_ids": [],
        "handled_message_ids": [],
        "last_poll": None,
    }


def _save_state(state: dict[str, Any]) -> None:
    state["last_poll"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _remember(state: dict[str, Any], key: str, message_id: str, max_items: int = 500) -> None:
    bucket = state.setdefault(key, [])
    if message_id not in bucket:
        bucket.append(message_id)
    if len(bucket) > max_items:
        state[key] = bucket[-max_items:]


def _fetch_unread_messages() -> list[dict[str, Any]]:
    with httpx.Client(timeout=10.0) as client:
        response = client.get(f"{BUS_URL}/messages?to=codex&unread=true", headers=_headers())
        response.raise_for_status()
        data = response.json()
    return data.get("messages", [])


def _mark_read(message_id: str) -> None:
    with httpx.Client(timeout=10.0) as client:
        response = client.post(
            f"{BUS_URL}/messages/{message_id}/read",
            headers=_headers(),
            json={"agent": "codex"},
        )
        response.raise_for_status()


def _respond(message_id: str, content: str) -> None:
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            f"{BUS_URL}/messages/{message_id}/respond",
            headers=_headers(),
            json={"from": "codex", "content": content},
        )
        response.raise_for_status()


def _send(to: str, msg_type: str, content: str, task_id: str | None = None) -> None:
    payload: dict[str, Any] = {
        "from": "codex",
        "to": to,
        "type": msg_type,
        "content": content,
    }
    if task_id:
        payload["task_id"] = task_id
    with httpx.Client(timeout=20.0) as client:
        response = client.post(f"{BUS_URL}/messages", headers=_headers(), json=payload)
        response.raise_for_status()


def _parse_key_value_content(raw: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    freeform: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if ":" in stripped:
            key, value = stripped.split(":", 1)
            key_norm = key.strip().lower().replace(" ", "_")
            if key_norm in {"repo", "repo_path", "path", "base", "commit", "instructions", "title", "mode", "uncommitted"}:
                parsed[key_norm] = value.strip()
                continue
        freeform.append(stripped)
    if freeform and "instructions" not in parsed:
        parsed["instructions"] = "\n".join(freeform)
    return parsed


def _normalize_request(msg: dict[str, Any]) -> dict[str, Any]:
    content = (msg.get("content") or "").strip()
    payload: dict[str, Any] = {}

    if content:
        try:
            decoded = json.loads(content)
            if isinstance(decoded, dict):
                payload = decoded
        except json.JSONDecodeError:
            payload = _parse_key_value_content(content)

    repo = (
        payload.get("repo")
        or payload.get("repo_path")
        or payload.get("path")
        or DEFAULT_REPO
    )
    instructions = (
        payload.get("instructions")
        or payload.get("prompt")
        or payload.get("content")
        or ""
    )
    base = payload.get("base") or ""
    commit = payload.get("commit") or ""
    title = payload.get("title") or ""
    mode = (payload.get("mode") or "").strip().lower()
    uncommitted_raw = str(payload.get("uncommitted", "")).strip().lower()
    uncommitted = uncommitted_raw in {"1", "true", "yes", "y"}

    if not repo:
        raise ValueError("review request missing repo path and CODEX_DEFAULT_REPO is not set")

    if not mode:
        if commit:
            mode = "commit"
        elif base:
            mode = "base"
        else:
            mode = "uncommitted"

    if mode == "uncommitted":
        uncommitted = True

    return {
        "repo": str(repo),
        "instructions": str(instructions).strip(),
        "base": str(base).strip(),
        "commit": str(commit).strip(),
        "title": str(title).strip(),
        "mode": mode,
        "uncommitted": uncommitted,
    }


def _is_review_request(msg: dict[str, Any]) -> bool:
    msg_type = (msg.get("type") or "").strip().lower()
    if msg_type in REVIEW_MESSAGE_TYPES:
        return True
    content = (msg.get("content") or "").strip()
    if not content:
        return False
    try:
        payload = json.loads(content)
        if isinstance(payload, dict) and any(key in payload for key in ("repo", "repo_path", "path", "base", "commit", "uncommitted", "instructions")):
            return True
    except json.JSONDecodeError:
        pass
    return any(line.strip().lower().startswith(("repo:", "repo_path:", "path:", "base:", "commit:", "instructions:")) for line in content.splitlines())


def _build_review_command(request: dict[str, Any]) -> list[str]:
    cmd = [CODEX_BIN, "-C", request["repo"], "review"]
    if request["mode"] == "commit" and request["commit"]:
        cmd.extend(["--commit", request["commit"]])
    elif request["mode"] == "base" and request["base"]:
        cmd.extend(["--base", request["base"]])
    else:
        cmd.append("--uncommitted")
    if request["title"]:
        cmd.extend(["--title", request["title"]])
    return cmd


def _run_review(request: dict[str, Any]) -> dict[str, Any]:
    repo = Path(request["repo"]).expanduser().resolve()
    if not repo.exists() or not repo.is_dir():
        raise RuntimeError(f"repo path does not exist: {repo}")
    if not (repo / ".git").exists():
        raise RuntimeError(f"repo path is not a git repository: {repo}")

    cmd = _build_review_command({**request, "repo": str(repo)})
    logger.info("Running review: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=REVIEW_TIMEOUT,
    )
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "repo": str(repo),
        "command": cmd,
    }


def _format_review_response(message: dict[str, Any], request: dict[str, Any], result: dict[str, Any]) -> str:
    instructions = request["instructions"] or "(none)"
    instruction_note = ""
    if request["instructions"]:
        instruction_note = (
            "\nNote: this Codex CLI build does not accept custom review instructions together with "
            "diff-selection flags, so the review ran with the default review prompt.\n"
        )

    if result["returncode"] != 0:
        return (
            f"Code review failed for repo `{result['repo']}`.\n\n"
            f"Command: {' '.join(result['command'])}\n"
            f"Exit code: {result['returncode']}\n\n"
            f"Requested instructions: {instructions}\n"
            f"{instruction_note}\n"
            f"stderr:\n{result['stderr'] or '(empty)'}\n\n"
            f"stdout:\n{result['stdout'] or '(empty)'}"
        )

    review_body = result["stdout"] or "(Codex review produced no output)"
    return (
        f"Codex code review complete.\n\n"
        f"Repo: `{result['repo']}`\n"
        f"Mode: `{request['mode']}`\n"
        f"Instructions: {instructions}\n\n"
        f"{instruction_note}"
        f"{review_body}"
    )


def _handle_review_message(msg: dict[str, Any], state: dict[str, Any]) -> None:
    message_id = msg["message_id"]
    sender = msg.get("from", "unknown")
    task_id = msg.get("task_id")
    try:
        request = _normalize_request(msg)
        result = _run_review(request)
        response_text = _format_review_response(msg, request, result)
    except Exception as exc:
        response_text = f"Codex could not process the code review request: {exc}"

    try:
        if msg.get("requires_response"):
            _respond(message_id, response_text)
        else:
            _send(sender, "status_update", response_text, task_id=task_id)
        _mark_read(message_id)
        _remember(state, "handled_message_ids", message_id)
        logger.info("Handled review request %s from %s", message_id, sender)
    except Exception as exc:
        logger.error("Failed to reply to review request %s: %s", message_id, exc)


def poll_once(state: dict[str, Any]) -> None:
    messages = _fetch_unread_messages()
    if not messages:
        logger.debug("No unread codex messages")
        return

    handled = set(state.get("handled_message_ids", []))
    ignored = set(state.get("ignored_message_ids", []))

    for msg in messages:
        message_id = msg.get("message_id", "")
        sender = msg.get("from", "")
        if not message_id or sender in IGNORE_SENDERS:
            continue
        if message_id in handled:
            continue
        if _is_review_request(msg):
            _handle_review_message(msg, state)
            continue
        if message_id not in ignored:
            logger.info("Ignoring non-review unread message %s from %s type=%s", message_id, sender, msg.get("type"))
            _remember(state, "ignored_message_ids", message_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Headless Codex bus monitor")
    parser.add_argument("--once", action="store_true", help="Run a single poll cycle and exit")
    return parser.parse_args()


def main() -> None:
    if not shutil.which(Path(CODEX_BIN).name) and not Path(CODEX_BIN).exists():
        raise SystemExit(f"codex binary not found: {CODEX_BIN}")

    args = parse_args()
    state = _load_state()
    logger.info("Codex monitor starting (poll=%ss, bus=%s)", POLL_INTERVAL, BUS_URL)

    if args.once:
        poll_once(state)
        _save_state(state)
        return

    while True:
        try:
            poll_once(state)
            _save_state(state)
        except KeyboardInterrupt:
            logger.info("Codex monitor stopped by user")
            _save_state(state)
            return
        except Exception as exc:
            logger.exception("Poll cycle failed: %s", exc)
            _save_state(state)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
