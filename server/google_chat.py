"""Google Chat webhook notifier.

Posts messages to a Google Chat space via incoming webhook.
Webhook URL is read from GOOGLE_CHAT_WEBHOOK_URL env var.
"""
import logging
import os

import httpx

logger = logging.getLogger("leroy-google-chat")


async def send_google_chat_message(text: str, webhook_url: str | None = None) -> dict:
    """Post a message to Google Chat via incoming webhook.

    Args:
        text: The message text to send.
        webhook_url: Optional webhook URL. Falls back to GOOGLE_CHAT_WEBHOOK_URL env var.

    Returns:
        dict with 'status' ('sent' or 'error') and optional 'error' key.
    """
    url = webhook_url or os.environ.get("GOOGLE_CHAT_WEBHOOK_URL", "")

    if not url:
        logger.warning(
            "GOOGLE_CHAT_WEBHOOK_URL not configured -- Google Chat notification skipped"
        )
        return {"status": "skipped", "reason": "GOOGLE_CHAT_WEBHOOK_URL not configured"}

    payload = {"text": text}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                headers={"Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            return {"status": "sent", "http_status": resp.status_code}
    except httpx.HTTPStatusError as e:
        logger.error("Google Chat webhook returned error: %s", e)
        return {"status": "error", "error": f"HTTP {e.response.status_code}: {e.response.text}"}
    except httpx.RequestError as e:
        logger.error("Google Chat webhook request failed: %s", e)
        return {"status": "error", "error": str(e)}
