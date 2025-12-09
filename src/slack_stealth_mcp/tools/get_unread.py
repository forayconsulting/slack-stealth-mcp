"""Tool to get all unread messages and mentions."""

from __future__ import annotations

from typing import Any, List, Set

from ..workspace import WorkspaceManager


def _get_cached_user_name(client: Any, user_id: str) -> str:
    """Get user name from cache without making API call."""
    cached = client._users_cache.get(user_id)
    if cached:
        return cached.display
    return user_id[:8] + "..."  # Truncated ID as fallback


async def get_unread(
    manager: WorkspaceManager,
    include_channels: bool = True,
    include_dms: bool = True,
    include_mentions: bool = True,
    max_messages_per_conversation: int = 5,
    max_conversations_to_check: int = 20,  # Limit to avoid API overload
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
        max_conversations_to_check: Max conversations to check for unreads (limits API calls)
        workspace: Workspace name (uses default if not specified)

    Returns:
        Dictionary with categorized unread messages and summary
    """
    client = manager.get_client(workspace)

    unread_dms: list[dict[str, Any]] = []
    unread_channels: list[dict[str, Any]] = []
    mentions: list[dict[str, Any]] = []

    # Collect all user IDs we need to resolve
    user_ids_to_prefetch: Set[str] = set()

    checked_count = 0

    # Process DMs - fetch them SEPARATELY to ensure we get them
    # NOTE: conversations.list doesn't return unread counts!
    # We need to call conversations.info for each DM to get unread status
    if include_dms:
        # Dedicated call for DMs only
        dm_conversations = await client.list_conversations(
            types="im,mpim",  # Only DMs and group DMs
            limit=50,  # Limit since we'll check each one
            max_pages=1,
        )

        # For each DM, get detailed info with unread count
        # (conversations.list doesn't include unread_count, but conversations.info does)
        # NOTE: This is slow (1 API call per DM), so we limit to 15 most recent
        dm_convs_with_unread = []
        for conv in dm_conversations[:15]:  # Limit API calls for performance
            try:
                detailed = await client.get_conversation_info(conv.id)
                if detailed.unread_count_display and detailed.unread_count_display > 0:
                    dm_convs_with_unread.append(detailed)
                    if detailed.user:
                        user_ids_to_prefetch.add(detailed.user)
            except Exception:
                pass  # Skip if we can't get info

        # Sort by unread count to prioritize most active
        dm_convs_with_unread.sort(key=lambda c: c.unread_count_display or 0, reverse=True)

        # Prefetch DM participant names (single batch call)
        if user_ids_to_prefetch:
            await client.prefetch_users(list(user_ids_to_prefetch))

        for conv in dm_convs_with_unread[:max_conversations_to_check]:
            checked_count += 1
            # Fetch unread messages
            try:
                messages = await client.get_conversation_history(
                    channel=conv.id,
                    oldest=conv.last_read if conv.last_read else None,
                    limit=max_messages_per_conversation,
                )
            except Exception:
                # Skip if we can't fetch history (e.g., invalid timestamp)
                messages = []

            if messages:
                # Collect message author IDs for later prefetch
                for msg in messages:
                    if msg.user:
                        user_ids_to_prefetch.add(msg.user)

                # Get DM name from cache (should be populated now)
                dm_name = conv.display_name
                if conv.is_im and conv.user:
                    dm_name = f"@{_get_cached_user_name(client, conv.user)}"

                formatted_messages = []
                for msg in reversed(messages):
                    user_name = _get_cached_user_name(client, msg.user) if msg.user else "Unknown"
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

    # Process channels - fetch separately from DMs
    remaining_budget = max_conversations_to_check - checked_count
    if include_channels and remaining_budget > 0:
        # Dedicated call for channels only
        channel_conversations = await client.list_conversations(
            types="public_channel,private_channel",
            limit=200,
            max_pages=2,
        )

        channel_convs = [c for c in channel_conversations if c.last_read]

        for conv in channel_convs[:remaining_budget]:
            # Get recent messages to check if there are unreads
            messages = await client.get_conversation_history(
                channel=conv.id,
                oldest=conv.last_read,
                limit=max_messages_per_conversation,
            )

            if messages:
                # Collect message author IDs
                for msg in messages:
                    if msg.user:
                        user_ids_to_prefetch.add(msg.user)

                formatted_messages = []
                for msg in reversed(messages):
                    user_name = _get_cached_user_name(client, msg.user) if msg.user else "Unknown"
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

    # Prefetch any remaining message authors we collected
    # (do this before mentions to maximize cache hits)
    if user_ids_to_prefetch:
        await client.prefetch_users(list(user_ids_to_prefetch))

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

                # Collect mention author IDs and prefetch
                mention_authors = [msg.user for msg in results.messages if msg.user]
                if mention_authors:
                    await client.prefetch_users(mention_authors)

                for msg in results.messages:
                    user_name = _get_cached_user_name(client, msg.user) if msg.user else "Unknown"
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
