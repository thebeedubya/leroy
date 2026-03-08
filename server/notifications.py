"""Notification dispatch for Leroy v2.

Sends push notifications to Brad via Google Chat webhook.
Only fires when human action is required (escalations, ops intervention needed).
"""

import asyncio
import logging

from google_chat import send_google_chat_message

logger = logging.getLogger("leroy-notifications")


def send_webhook_notification(message: str, task_id: str | None = None,
                               severity: str = "alert") -> None:
    """Send push notification to Brad via Google Chat webhook.

    Fires ONLY when human action is required. Called from sync context
    (state machine handlers), schedules the async call on the running loop.

    Args:
        message: Notification text.
        task_id: Optional task ID for context.
        severity: 'alert' or 'critical'.
    """
    prefix = "FORGE CRITICAL" if severity == "critical" else "FORGE Alert"
    text = f"{prefix}: {message}"
    if task_id:
        text += f"\nTask: {task_id}"

    # Schedule async webhook call from sync context
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_send_async(text))
    except RuntimeError:
        # No running loop — run synchronously in a new loop
        try:
            asyncio.run(_send_async(text))
        except Exception as e:
            logger.error("Webhook notification failed (no loop): %s", e)


async def _send_async(text: str) -> None:
    """Async wrapper for the webhook call."""
    try:
        result = await send_google_chat_message(text)
        if result["status"] == "sent":
            logger.info("Webhook notification sent successfully")
        elif result["status"] == "skipped":
            logger.debug("Webhook skipped: %s", result.get("reason"))
        else:
            logger.warning("Webhook error: %s", result.get("error"))
    except Exception as e:
        logger.error("Webhook notification failed: %s", e)
