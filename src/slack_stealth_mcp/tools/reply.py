"""Tool for sending messages and replies."""

from __future__ import annotations

from typing import Any

from ..slack_client import SlackAPIError
from ..workspace import WorkspaceManager


async def reply(
    manager: WorkspaceManager,
    target: str,
    message: str,
    thread_ts: str | None = None,
    broadcast: bool = False,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Send a message or reply to a conversation.

    This does NOT mark the channel as read.

    Args:
        manager: Workspace manager
        target: Where to send - can be:
            - Channel ID (C...)
            - DM channel ID (D...)
            - User ID (U...) - will open/get DM
            - Group DM ID (G...)
        message: Message text (supports Slack mrkdwn formatting)
        thread_ts: Parent message timestamp for thread reply
        broadcast: For thread replies, also post to channel
        workspace: Workspace name (uses default if not specified)

    Returns:
        Dictionary with sent message details
    """
    client = manager.get_client(workspace)

    # Determine channel ID
    channel_id = target

    # If target is a user ID, open/get DM channel
    if target.startswith("U"):
        try:
            channel_id = await client.open_conversation([target])
        except SlackAPIError as e:
            return {
                "ok": False,
                "error": f"Failed to open DM with user {target}: {e.error}",
            }

    # Send the message
    try:
        sent_message = await client.post_message(
            channel=channel_id,
            text=message,
            thread_ts=thread_ts,
            reply_broadcast=broadcast,
        )

        result: dict[str, Any] = {
            "ok": True,
            "channel_id": channel_id,
            "message_ts": sent_message.ts,
            "text": sent_message.text,
        }

        if thread_ts:
            result["thread_ts"] = thread_ts
            result["is_thread_reply"] = True
            if broadcast:
                result["broadcasted"] = True

        # Try to get channel name for context
        try:
            conv = await client.get_conversation_info(channel_id)
            if conv.is_im and conv.user:
                user = await client.get_user_info(conv.user)
                result["sent_to"] = f"DM with @{user.display}"
            else:
                result["sent_to"] = conv.display_name
        except Exception:
            result["sent_to"] = channel_id

        return result

    except SlackAPIError as e:
        return {
            "ok": False,
            "error": f"Failed to send message: {e.error}",
            "channel_id": channel_id,
        }
