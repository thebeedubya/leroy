"""Leroy A2A Server configuration.

All settings are env-based with sensible defaults.
"""
import os
from pathlib import Path

# Server
HOST = os.getenv("LEROY_HOST", "127.0.0.1")
PORT = int(os.getenv("LEROY_PORT", "9800"))
HEALTH_PORT = int(os.getenv("LEROY_HEALTH_PORT", "9801"))

# PM Webhook sidecar (started by pm.sh alongside PM's Claude session)
PM_WEBHOOK_PORT = int(os.getenv("PM_WEBHOOK_PORT", "9802"))
PM_WEBHOOK_HOST = os.getenv("PM_WEBHOOK_HOST", "127.0.0.1")

# Task persistence DB
TASK_DB_PATH = Path(os.getenv(
    "LEROY_TASK_DB_PATH",
    str(Path(__file__).parent.parent / "data" / "tasks.db"),
))

# Auth
TOKENS_FILE = os.getenv(
    "LEROY_TOKENS_FILE",
    str(Path(__file__).parent / "tokens" / "tokens.json"),
)

# Forge-brain (Aianna) persistence
# Token: shared claude-code-haze token (7 tokens issued, leroy uses same instance)
FORGE_BRAIN_URL = os.getenv("FORGE_BRAIN_URL", "http://192.168.1.100:8300/mcp")
FORGE_BRAIN_TOKEN = os.getenv(
    "FORGE_BRAIN_TOKEN",
    "FORGE_BRAIN_TOKEN_REDACTED",
)

# Agent identity
AGENT_NAME = "Leroy"
AGENT_DESCRIPTION = (
    "FORGE Engineering Lead. Receives specs from PM, auto-executes via "
    "claude CLI, reports results back through the A2A pipeline."
)
AGENT_VERSION = "0.2.0"
AGENT_URL = f"http://{HOST}:{PORT}/"
