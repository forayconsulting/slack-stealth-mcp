"""Tool to explicitly mark conversations as read."""

from __future__ import annotations

from typing import Any

from ..slack_client import SlackAPIError
from ..workspace import WorkspaceManager


async def mark_read(
    manager: WorkspaceManager,
    channel: str,
    timestamp: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Mark a conversation as read.

    This is the ONLY operation that affects read state. Use deliberately
    when you want to mark messages as read.

    Args:
        manager: Workspace manager
        channel: Channel ID to mark as read
        timestamp: Mark as read up to this message timestamp.
                   If not provided, marks all messages as read.
        workspace: Workspace name (uses default if not specified)

    Returns:
        Dictionary with result status
    """
    client = manager.get_client(workspace)

    # If no timestamp provided, get the latest message
    if not timestamp:
        try:
            messages = await client.get_conversation_history(
                channel=channel,
                limit=1,
            )
            if messages:
                timestamp = messages[0].ts
            else:
                return {
                    "ok": True,
                    "channel_id": channel,
                    "message": "No messages to mark as read",
                }
        except SlackAPIError as e:
            return {
                "ok": False,
                "error": f"Failed to get latest message: {e.error}",
                "channel_id": channel,
            }

    # Mark as read
    try:
        await client.mark_conversation(channel=channel, ts=timestamp)

        # Get channel info for context
        channel_name = channel
        try:
            conv = await client.get_conversation_info(channel)
            if conv.is_im and conv.user:
                user = await client.get_user_info(conv.user)
                channel_name = f"DM with @{user.display}"
            else:
                channel_name = conv.display_name
        except Exception:
            pass

        return {
            "ok": True,
            "channel_id": channel,
            "channel_name": channel_name,
            "marked_read_until": timestamp,
            "message": f"Marked {channel_name} as read",
        }

    except SlackAPIError as e:
        return {
            "ok": False,
            "error": f"Failed to mark as read: {e.error}",
            "channel_id": channel,
        }
