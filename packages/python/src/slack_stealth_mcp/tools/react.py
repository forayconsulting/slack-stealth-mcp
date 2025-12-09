"""Tool for adding/removing emoji reactions to messages."""

from __future__ import annotations

from typing import Any

from ..slack_client import SlackAPIError
from ..workspace import WorkspaceManager


async def react(
    manager: WorkspaceManager,
    channel: str,
    message_ts: str,
    emoji: str,
    remove: bool = False,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Add or remove an emoji reaction on a message.

    Args:
        manager: Workspace manager
        channel: Channel ID containing the message
        message_ts: Timestamp of the message to react to
        emoji: Emoji name without colons (e.g., "thumbsup", not ":thumbsup:")
        remove: If True, remove the reaction instead of adding
        workspace: Workspace name (uses default if not specified)

    Returns:
        Dictionary with reaction details
    """
    client = manager.get_client(workspace)

    # Normalize emoji name - strip colons if provided
    emoji_name = emoji.strip(":")

    try:
        if remove:
            await client.remove_reaction(channel, message_ts, emoji_name)
            action = "removed"
        else:
            await client.add_reaction(channel, message_ts, emoji_name)
            action = "added"

        result: dict[str, Any] = {
            "ok": True,
            "action": action,
            "emoji": emoji_name,
            "channel": channel,
            "message_ts": message_ts,
        }

        # Try to get channel name for context
        try:
            conv = await client.get_conversation_info(channel)
            if conv.is_im and conv.user:
                user = await client.get_user_info(conv.user)
                result["channel_name"] = f"DM with @{user.display}"
            else:
                result["channel_name"] = conv.display_name
        except Exception:
            result["channel_name"] = channel

        return result

    except SlackAPIError as e:
        return {
            "ok": False,
            "error": f"Failed to {('remove' if remove else 'add')} reaction: {e.error}",
            "emoji": emoji_name,
            "channel": channel,
            "message_ts": message_ts,
        }
