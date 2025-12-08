"""Tool to get all unread messages and mentions."""

from __future__ import annotations

from typing import Any

from ..workspace import WorkspaceManager


async def get_unread(
    manager: WorkspaceManager,
    include_channels: bool = True,
    include_dms: bool = True,
    include_mentions: bool = True,
    max_messages_per_conversation: int = 5,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Get all unread messages and mentions across the workspace.

    This is the "What's new?" tool - it fetches unread DMs, channel messages,
    and recent mentions without marking anything as read.

    Args:
        manager: Workspace manager
        include_channels: Include unread channel messages
        include_dms: Include unread DMs
        include_mentions: Search for recent mentions
        max_messages_per_conversation: Max unread messages to fetch per conversation
        workspace: Workspace name (uses default if not specified)

    Returns:
        Dictionary with categorized unread messages and summary
    """
    client = manager.get_client(workspace)

    # Get all conversations to find those with unread messages
    conversations = await client.list_conversations()

    unread_dms: list[dict[str, Any]] = []
    unread_channels: list[dict[str, Any]] = []
    mentions: list[dict[str, Any]] = []

    # Process DMs - they have unread_count
    if include_dms:
        dm_convs = [c for c in conversations if (c.is_im or c.is_mpim) and c.unread_count_display]
        for conv in dm_convs:
            if conv.unread_count_display and conv.unread_count_display > 0:
                # Fetch unread messages
                messages = await client.get_conversation_history(
                    channel=conv.id,
                    oldest=conv.last_read,
                    limit=max_messages_per_conversation,
                )

                if messages:
                    # Resolve user name for DM
                    dm_name = conv.display_name
                    if conv.is_im and conv.user:
                        dm_name = await client.resolve_user_name(conv.user)

                    formatted_messages = []
                    for msg in reversed(messages):
                        user_name = "Unknown"
                        if msg.user:
                            user_name = await client.resolve_user_name(msg.user)
                        formatted_messages.append({
                            "user": user_name,
                            "text": msg.text[:200] + ("..." if len(msg.text) > 200 else ""),
                            "time": msg.timestamp.strftime("%H:%M"),
                            "ts": msg.ts,
                        })

                    unread_dms.append({
                        "channel_id": conv.id,
                        "name": dm_name,
                        "unread_count": conv.unread_count_display,
                        "messages": formatted_messages,
                    })

    # Process channels - compare last_read vs most recent message
    if include_channels:
        channel_convs = [c for c in conversations if c.is_channel or c.is_group]
        for conv in channel_convs:
            if not conv.last_read:
                continue

            # Get recent messages to check if there are unreads
            messages = await client.get_conversation_history(
                channel=conv.id,
                oldest=conv.last_read,
                limit=max_messages_per_conversation,
            )

            if messages:
                formatted_messages = []
                for msg in reversed(messages):
                    user_name = "Unknown"
                    if msg.user:
                        user_name = await client.resolve_user_name(msg.user)
                    formatted_messages.append({
                        "user": user_name,
                        "text": msg.text[:200] + ("..." if len(msg.text) > 200 else ""),
                        "time": msg.timestamp.strftime("%H:%M"),
                        "ts": msg.ts,
                        "thread_ts": msg.thread_ts if msg.is_thread_reply else None,
                    })

                unread_channels.append({
                    "channel_id": conv.id,
                    "name": conv.display_name,
                    "unread_count": len(messages),
                    "messages": formatted_messages,
                })

    # Search for recent mentions
    if include_mentions:
        try:
            # Get current user info
            auth = await client.test_auth()
            user_id = auth.get("user_id")

            if user_id:
                # Search for messages mentioning the user
                results = await client.search_messages(
                    query=f"<@{user_id}>",
                    count=10,
                    sort="timestamp",
                    sort_dir="desc",
                )

                for msg in results.messages:
                    user_name = "Unknown"
                    if msg.user:
                        user_name = await client.resolve_user_name(msg.user)

                    mentions.append({
                        "user": user_name,
                        "text": msg.text[:200] + ("..." if len(msg.text) > 200 else ""),
                        "time": msg.timestamp.strftime("%Y-%m-%d %H:%M"),
                        "ts": msg.ts,
                    })
        except Exception:
            pass  # Mentions are supplementary, don't fail if search fails

    # Build summary
    total_unread_dms = sum(dm.get("unread_count", 0) for dm in unread_dms)
    total_unread_channels = len(unread_channels)
    total_mentions = len(mentions)

    summary_parts = []
    if total_unread_dms:
        dm_count = len(unread_dms)
        summary_parts.append(f"{total_unread_dms} unread message(s) in {dm_count} DM(s)")
    if total_unread_channels:
        summary_parts.append(f"{total_unread_channels} channel(s) with new messages")
    if total_mentions:
        summary_parts.append(f"{total_mentions} recent mention(s)")

    summary = "; ".join(summary_parts) if summary_parts else "No unread messages"

    result: dict[str, Any] = {
        "summary": summary,
        "workspace": workspace or manager.default_workspace,
    }

    if unread_dms:
        result["unread_dms"] = unread_dms
    if unread_channels:
        result["unread_channels"] = unread_channels
    if mentions:
        result["mentions"] = mentions

    result["totals"] = {
        "unread_dm_messages": total_unread_dms,
        "channels_with_unread": total_unread_channels,
        "mentions": total_mentions,
    }

    return result
