"""Tool to list available Slack conversations."""

from __future__ import annotations

from typing import Any

from ..workspace import WorkspaceManager
from .find_conversation import _mpim_participants


async def list_conversations(
    manager: WorkspaceManager,
    workspace: str | None = None,
    types: str = "all",
) -> dict[str, Any]:
    """List all available conversations in the workspace.

    Args:
        manager: Workspace manager
        workspace: Workspace name (uses default if not specified)
        types: Filter type - "all", "channels", "dms"

    Returns:
        Dictionary with categorized conversation lists
    """
    client = manager.get_client(workspace)

    # Map type filter to Slack API types
    if types == "channels":
        slack_types = "public_channel,private_channel"
    elif types == "dms":
        slack_types = "im,mpim"
    else:
        slack_types = "public_channel,private_channel,mpim,im"

    # Warm the user directory (one cached users.list call) so DMs and group
    # DMs show people's names instead of raw IDs / mpdm- strings
    await client.ensure_user_directory()

    conversations = await client.list_conversations(types=slack_types, max_pages=0)

    # Categorize conversations
    channels = []
    private_channels = []
    dms = []
    group_dms = []

    for conv in conversations:
        display_name = conv.display_name
        if conv.is_im and conv.user:
            cached_user = client._users_cache.get(conv.user)
            if cached_user:
                display_name = f"@{cached_user.display}"
            else:
                display_name = f"DM ({conv.user[:8]}...)"
        elif conv.is_mpim:
            participants = _mpim_participants(conv, client)
            if participants:
                display_name = ", ".join(participants)

        entry = {
            "id": conv.id,
            "name": display_name,
            "type": conv.conversation_type,
        }

        # Add unread info if available (DMs only have this)
        if conv.unread_count_display is not None:
            entry["unread_count"] = conv.unread_count_display

        if conv.last_read:
            entry["has_unread"] = True  # Will be refined when we fetch messages

        if conv.is_im:
            dms.append(entry)
        elif conv.is_mpim:
            group_dms.append(entry)
        elif conv.is_private:
            private_channels.append(entry)
        else:
            channels.append(entry)

    result: dict[str, Any] = {
        "workspace": workspace or manager.default_workspace,
    }

    if channels:
        result["channels"] = channels
    if private_channels:
        result["private_channels"] = private_channels
    if dms:
        result["direct_messages"] = dms
    if group_dms:
        result["group_dms"] = group_dms

    total = len(channels) + len(private_channels) + len(dms) + len(group_dms)
    result["total_conversations"] = total

    return result
