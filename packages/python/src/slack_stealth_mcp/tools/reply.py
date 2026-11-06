"""Tool for sending messages and replies."""

from __future__ import annotations

from typing import Any

from ..mrkdwn import normalize_mrkdwn
from ..refs import resolve_channel_ref
from ..slack_client import SlackAPIError, SlackClient
from ..workspace import WorkspaceManager


async def conversation_label(client: SlackClient, channel_id: str) -> str:
    """Human-readable name for a conversation, falling back to its ID."""
    try:
        conv = await client.get_conversation_info(channel_id)
        if conv.is_im and conv.user:
            user = await client.get_user_info(conv.user)
            return f"DM with @{user.display}"
        return conv.display_name
    except Exception:
        return channel_id


async def resolve_send_target(
    client: SlackClient, target: str, thread_ts: str | None = None
) -> tuple[str, str | None]:
    """Turn a send target into (channel_id, thread_ts).

    Accepts the same targets as slack_reply: C/D/G IDs, U/W user IDs (opens
    the DM), or Slack URLs. A message URL implies a thread reply unless the
    caller passed thread_ts.

    Raises:
        ValueError: with a message suitable for the tool's error field
    """
    ref = resolve_channel_ref(target)
    channel_id = ref.channel_id
    if ref.message_ts and not thread_ts:
        thread_ts = ref.message_ts

    if channel_id.startswith(("U", "W")):
        try:
            channel_id = await client.open_conversation([channel_id])
        except SlackAPIError as e:
            raise ValueError(f"Failed to open DM with user {channel_id}: {e.error}") from e

    return channel_id, thread_ts


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
            - Slack URL (copied via "Copy link")
        message: Message text (supports Slack mrkdwn formatting)
        thread_ts: Parent message timestamp for thread reply
        broadcast: For thread replies, also post to channel
        workspace: Workspace name (uses default if not specified)

    Returns:
        Dictionary with sent message details
    """
    client = manager.get_client(workspace)

    # Resolve URLs/raw IDs/user IDs to a channel ID (and thread from a URL)
    try:
        channel_id, thread_ts = await resolve_send_target(client, target, thread_ts)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    # Convert any stray Markdown to Slack mrkdwn
    message = normalize_mrkdwn(message)

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

        result["sent_to"] = await conversation_label(client, channel_id)
        return result

    except SlackAPIError as e:
        return {
            "ok": False,
            "error": f"Failed to send message: {e.error}",
            "channel_id": channel_id,
        }
