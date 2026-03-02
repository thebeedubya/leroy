"""Bearer token authentication for Leroy A2A server.

Follows the forge-brain pattern: tokens.json maps token -> client metadata.
"""
import json
import logging
from pathlib import Path

import config

logger = logging.getLogger("leroy-a2a")

_TOKEN_MAP: dict[str, dict] = {}


def load_tokens() -> None:
    """Load bearer tokens from tokens.json."""
    global _TOKEN_MAP
    tokens_path = Path(config.TOKENS_FILE)
    if not tokens_path.exists():
        logger.warning("tokens.json not found at %s -- auth disabled", tokens_path)
        return
    try:
        _TOKEN_MAP = json.loads(tokens_path.read_text())
        logger.info("Loaded %d client tokens from %s", len(_TOKEN_MAP), tokens_path)
    except Exception:
        logger.exception("Failed to load tokens.json")


def validate_token(token: str) -> dict | None:
    """Validate a bearer token. Returns client metadata or None."""
    return _TOKEN_MAP.get(token)


def is_auth_enabled() -> bool:
    """Check if any tokens are loaded."""
    return bool(_TOKEN_MAP)
