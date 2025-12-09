"""Tool to get all unread messages and mentions."""

from __future__ import annotations

import asyncio
from typing import Any, Set

from ..workspace import WorkspaceManager


def _get_cached_user_name(client: Any, user_id: str) -> str:
    """Get user name from cache without making API call."""
    cached = client._users_cache.get(user_id)
    if cached:
        return cached.display
    return user_id[:8] + "..."  # Truncated ID as fallback


async def _get_unread_for_workspace(
    manager: WorkspaceManager,
    workspace: str,
    include_channels: bool,
    include_dms: bool,
    include_mentions: bool,
    max_messages_per_conversation: int,
    max_conversations_to_check: int,
    max_dms_to_scan: int,
) -> dict[str, Any]:
    """Get unread messages for a single workspace.

    This is the core implementation - extracted to support multi-workspace iteration.
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
            limit=100,  # Get more DMs to ensure we find all unreads
            max_pages=2,
        )

        # Check DMs for unread status using parallel requests for speed
        # conversations.list doesn't include unread_count, but conversations.info does
        # Limit to max_dms_to_scan for performance (checking each DM = 1 API call)
        async def check_dm_unread(conv: Any) -> tuple[Any, int] | None:
            """Check if a DM has unreads, return (detailed_info, unread_count) if so."""
            try:
                detailed = await client.get_conversation_info(conv.id)

                # Check unread_count fields first (works for regular DMs)
                if detailed.unread_count_display and detailed.unread_count_display > 0:
                    return (detailed, detailed.unread_count_display)
                if detailed.unread_count and detailed.unread_count > 0:
                    return (detailed, detailed.unread_count)

                # For MPIMs, unread_count is often None - check via last_read vs latest message
                if detailed.is_mpim and detailed.last_read:
                    # Get just the latest message to compare timestamps
                    msgs = await client.get_conversation_history(channel=conv.id, limit=1)
                    if msgs and msgs[0].ts > detailed.last_read:
                        # Count unread by fetching messages after last_read
                        unread_msgs = await client.get_conversation_history(
                            channel=conv.id, oldest=detailed.last_read, limit=10
                        )
                        return (detailed, len(unread_msgs))
            except Exception:
                pass
            return None

        # Only scan first N DMs for performance (they're sorted by recent activity)
        dms_to_check = dm_conversations[:max_dms_to_scan]

        # Process DMs in parallel batches
        # Results are tuples of (conv_info, unread_count)
        dm_convs_with_unread: list[tuple[Any, int]] = []
        batch_size = 15  # Parallel batch size
        for i in range(0, len(dms_to_check), batch_size):
            batch = dms_to_check[i:i + batch_size]
            results = await asyncio.gather(*[check_dm_unread(conv) for conv in batch])
            for result in results:
                if result is not None:
                    conv, unread_count = result
                    dm_convs_with_unread.append((conv, unread_count))
                    if conv.user:
                        user_ids_to_prefetch.add(conv.user)

        # Sort by unread count to prioritize most active
        dm_convs_with_unread.sort(key=lambda x: x[1], reverse=True)

        # Prefetch DM participant names (single batch call)
        if user_ids_to_prefetch:
            await client.prefetch_users(list(user_ids_to_prefetch))

        for conv, unread_count in dm_convs_with_unread[:max_conversations_to_check]:
            checked_count += 1
            # Fetch unread messages
            # Note: "Mark as unread" sets last_read to "0000000000.000000" which is invalid
            # In that case, just fetch recent messages without the oldest filter
            oldest = conv.last_read
            if oldest and oldest.startswith("0000000000"):
                oldest = None  # Invalid timestamp, fetch recent messages instead
            try:
                messages = await client.get_conversation_history(
                    channel=conv.id,
                    oldest=oldest,
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

                # Get DM name - handle both regular DMs and MPIMs
                if conv.is_im and conv.user:
                    dm_name = f"@{_get_cached_user_name(client, conv.user)}"
                elif conv.is_mpim and conv.name:
                    # MPIM name is like "mpdm-user1--user2--user3-1", extract usernames
                    parts = conv.name.replace("mpdm-", "").split("--")
                    # Remove trailing number suffix
                    parts = [p.split("-")[0] if p else p for p in parts]
                    dm_name = ", ".join(parts[:3])  # Limit to 3 names
                    if len(parts) > 3:
                        dm_name += f" +{len(parts) - 3}"
                else:
                    dm_name = conv.display_name or conv.name or conv.id

                formatted_messages = []
                for msg in reversed(messages):
                    user_name = _get_cached_user_name(client, msg.user) if msg.user else "Unknown"
                    formatted_messages.append({
                        "user": user_name,
                        "text": msg.text,  # Full message text
                        "time": msg.timestamp.strftime("%H:%M"),
                        "ts": msg.ts,
                    })

                unread_dms.append({
                    "channel_id": conv.id,
                    "name": dm_name,
                    "unread_count": unread_count,
                    "messages": formatted_messages,
                })

    # Process channels - fetch separately from DMs
    # NOTE: conversations.list doesn't return last_read - must call conversations.info
    remaining_budget = max_conversations_to_check - checked_count
    if include_channels and remaining_budget > 0:
        # Dedicated call for channels only
        channel_conversations = await client.list_conversations(
            types="public_channel,private_channel",
            limit=200,
            max_pages=2,
        )

        # Check channels for unread status using parallel requests
        # Like DMs, we need conversations.info to get last_read
        async def check_channel_unread(conv: Any) -> tuple[Any, int] | None:
            """Check if a channel has unreads, return (detailed_info, unread_count) if so."""
            try:
                detailed = await client.get_conversation_info(conv.id)

                # Check unread_count fields
                if detailed.unread_count_display and detailed.unread_count_display > 0:
                    return (detailed, detailed.unread_count_display)
                if detailed.unread_count and detailed.unread_count > 0:
                    return (detailed, detailed.unread_count)

                # Fallback: compare last_read with latest message
                if detailed.last_read:
                    msgs = await client.get_conversation_history(channel=conv.id, limit=1)
                    if msgs and msgs[0].ts > detailed.last_read:
                        # Count unread messages
                        unread_msgs = await client.get_conversation_history(
                            channel=conv.id, oldest=detailed.last_read, limit=10
                        )
                        if unread_msgs:
                            return (detailed, len(unread_msgs))
            except Exception:
                pass
            return None

        # Limit channels to check for performance
        max_channels_to_scan = 50
        channels_to_check = channel_conversations[:max_channels_to_scan]

        # Process channels in parallel batches
        channel_convs_with_unread: list[tuple[Any, int]] = []
        batch_size = 15
        for i in range(0, len(channels_to_check), batch_size):
            batch = channels_to_check[i:i + batch_size]
            results = await asyncio.gather(*[check_channel_unread(conv) for conv in batch])
            for result in results:
                if result is not None:
                    channel_convs_with_unread.append(result)

        # Sort by unread count to prioritize most active
        channel_convs_with_unread.sort(key=lambda x: x[1], reverse=True)

        for conv, unread_count in channel_convs_with_unread[:remaining_budget]:
            # Fetch unread messages
            oldest = conv.last_read
            if oldest and oldest.startswith("0000000000"):
                oldest = None  # Invalid timestamp from "Mark as unread"
            try:
                messages = await client.get_conversation_history(
                    channel=conv.id,
                    oldest=oldest,
                    limit=max_messages_per_conversation,
                )
            except Exception:
                messages = []

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
                        "text": msg.text,  # Full message text
                        "time": msg.timestamp.strftime("%H:%M"),
                        "ts": msg.ts,
                        "thread_ts": msg.thread_ts if msg.is_thread_reply else None,
                    })

                unread_channels.append({
                    "channel_id": conv.id,
                    "name": conv.display_name or conv.name,
                    "unread_count": unread_count,
                    "messages": formatted_messages,
                })

    # Prefetch any remaining message authors we collected
    # (do this before mentions to maximize cache hits)
    if user_ids_to_prefetch:
        await client.prefetch_users(list(user_ids_to_prefetch))

    # Search for recent mentions - only include UNREAD ones
    if include_mentions:
        try:
            # Get current user info
            auth = await client.test_auth()
            user_id = auth.get("user_id")

            if user_id:
                # Search for messages mentioning the user
                results = await client.search_messages(
                    query=f"<@{user_id}>",
                    count=20,  # Fetch more since we'll filter
                    sort="timestamp",
                    sort_dir="desc",
                )

                # Collect mention author IDs and prefetch
                mention_authors = [msg.user for msg in results.messages if msg.user]
                if mention_authors:
                    await client.prefetch_users(mention_authors)

                # Filter to only unread mentions by checking channel read state
                # Cache channel info to avoid duplicate API calls
                channel_read_cache: dict[str, str | None] = {}

                for msg in results.messages:
                    # Get channel's last_read (with caching)
                    if msg.channel not in channel_read_cache:
                        try:
                            channel_info = await client.get_conversation_info(msg.channel)
                            channel_read_cache[msg.channel] = channel_info.last_read
                        except Exception:
                            channel_read_cache[msg.channel] = None

                    last_read = channel_read_cache.get(msg.channel)

                    # Only include if message is unread (ts > last_read)
                    if last_read and msg.ts > last_read:
                        user_name = _get_cached_user_name(client, msg.user) if msg.user else "Unknown"
                        mentions.append({
                            "user": user_name,
                            "text": msg.text,  # Full message text
                            "time": msg.timestamp.strftime("%Y-%m-%d %H:%M"),
                            "ts": msg.ts,
                            "channel_id": msg.channel,
                        })

                        # Limit to 10 unread mentions
                        if len(mentions) >= 10:
                            break
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
        "workspace": workspace,
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


def _combine_workspace_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine results from multiple workspaces into a single response."""
    # Calculate combined totals
    total_dms = sum(r.get("totals", {}).get("unread_dm_messages", 0) for r in results)
    total_channels = sum(r.get("totals", {}).get("channels_with_unread", 0) for r in results)
    total_mentions = sum(r.get("totals", {}).get("mentions", 0) for r in results)

    # Build combined summary
    total_unread = total_dms + total_channels
    workspaces_with_unreads = sum(
        1 for r in results
        if r.get("totals", {}).get("unread_dm_messages", 0) > 0
        or r.get("totals", {}).get("channels_with_unread", 0) > 0
    )

    if total_unread > 0:
        summary = f"{total_unread} unread across {workspaces_with_unreads} workspace(s)"
    else:
        summary = "No unread messages across all workspaces"

    return {
        "summary": summary,
        "workspaces": results,
        "totals": {
            "unread_dm_messages": total_dms,
            "channels_with_unread": total_channels,
            "mentions": total_mentions,
        },
    }


async def get_unread(
    manager: WorkspaceManager,
    include_channels: bool = True,
    include_dms: bool = True,
    include_mentions: bool = True,
    max_messages_per_conversation: int = 5,
    max_conversations_to_check: int = 20,  # Limit to avoid API overload
    max_dms_to_scan: int = 30,  # Limit DMs to scan for unread status (performance)
    workspace: str | None = None,
) -> dict[str, Any]:
    """Get all unread messages and mentions across workspaces.

    This is the "What's new?" tool - it fetches unread DMs, channel messages,
    and recent mentions without marking anything as read.

    Args:
        manager: Workspace manager
        include_channels: Include unread channel messages
        include_dms: Include unread DMs
        include_mentions: Search for recent mentions
        max_messages_per_conversation: Max unread messages to fetch per conversation
        max_conversations_to_check: Max conversations to check for unreads (limits API calls)
        max_dms_to_scan: Limit DMs to scan for performance
        workspace: Specific workspace to check (checks ALL workspaces if not specified)

    Returns:
        Dictionary with categorized unread messages and summary
    """
    # Determine which workspaces to check
    if workspace:
        workspaces_to_check = [workspace]
    else:
        workspaces_to_check = manager.workspace_names

    # If no workspaces configured, return early
    if not workspaces_to_check:
        return {
            "summary": "No workspaces configured",
            "needs_auth": True,
            "totals": {
                "unread_dm_messages": 0,
                "channels_with_unread": 0,
                "mentions": 0,
            },
        }

    # Collect results from each workspace
    all_results: list[dict[str, Any]] = []

    for ws_name in workspaces_to_check:
        try:
            result = await _get_unread_for_workspace(
                manager=manager,
                workspace=ws_name,
                include_channels=include_channels,
                include_dms=include_dms,
                include_mentions=include_mentions,
                max_messages_per_conversation=max_messages_per_conversation,
                max_conversations_to_check=max_conversations_to_check,
                max_dms_to_scan=max_dms_to_scan,
            )
            all_results.append(result)
        except Exception as e:
            # If one workspace fails, still return results from others
            all_results.append({
                "workspace": ws_name,
                "summary": f"Error: {str(e)}",
                "error": str(e),
                "totals": {
                    "unread_dm_messages": 0,
                    "channels_with_unread": 0,
                    "mentions": 0,
                },
            })

    # If only one workspace, return its result directly
    if len(all_results) == 1:
        return all_results[0]

    # Multiple workspaces - combine results
    return _combine_workspace_results(all_results)
