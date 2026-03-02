"""Leroy MCP Client configuration."""
import os

LEROY_A2A_URL = os.getenv("LEROY_A2A_URL", "http://127.0.0.1:9800")
LEROY_A2A_TOKEN = os.getenv("LEROY_A2A_TOKEN", "")
LEROY_HEALTH_URL = os.getenv("LEROY_HEALTH_URL", "http://127.0.0.1:9801")
