"""Tool to get all unread messages and mentions.

Primary path uses client.counts - the endpoint Slack's own client uses for
badge state - which is authoritative, includes thread activity, and covers
every conversation in one request. If client.counts fails (undocumented
endpoint, may drift), we fall back to the original conversations.info scan.
"""

from __future__ import annotations

import asyncio
from typing import Any, Set

from ..workspace import WorkspaceManager
from .find_conversation import _mpim_participants


def _get_cached_user_name(client: Any, user_id: str) -> str:
    """Get user name from cache without making API call."""
    cached = client._users_cache.get(user_id)
    if cached:
        return cached.display
    return user_id[:8] + "..."  # Truncated ID as fallback


def _ts_float(ts: Any) -> float:
    """Parse a Slack ts to float for sorting; invalid values sort last."""
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0


def _valid_oldest(last_read: str | None) -> str | None:
    """Sanitize a last_read ts for use as a history 'oldest' filter.

    "Mark as unread" sets last_read to "0000000000.000000" which is invalid
    as a filter - fetch recent messages instead.
    """
    if last_read and not last_read.startswith("0000000000"):
        return last_read
    return None


async def _collect_mentions(
    client: Any,
    channel_read_cache: dict[str, str | None],
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search for recent mentions of the current user; keep only unread ones.

    Args:
        client: Slack client
        channel_read_cache: channel_id -> last_read; misses are filled via
            conversations.info (callers can prefill from client.counts)
        limit: Max mentions to return
    """
    mentions: list[dict[str, Any]] = []

    auth = await client.test_auth()
    user_id = auth.get("user_id")
    if not user_id:
        return mentions

    results = await client.search_messages(
        query=f"<@{user_id}>",
        count=20,  # Fetch more since we'll filter
        sort="timestamp",
        sort_dir="desc",
    )

    mention_authors = [msg.user for msg in results.messages if msg.user]
    if mention_authors:
        await client.prefetch_users(mention_authors)

    for msg in results.messages:
        channel_id = msg.channel_id
        if not channel_id:
            continue

        if channel_id not in channel_read_cache:
            try:
                channel_info = await client.get_conversation_info(channel_id)
                channel_read_cache[channel_id] = channel_info.last_read
            except Exception:
                channel_read_cache[channel_id] = None

        last_read = channel_read_cache.get(channel_id)

        # Only include if message is unread (ts > last_read)
        if last_read and msg.ts > last_read:
            user_name = _get_cached_user_name(client, msg.user) if msg.user else "Unknown"
            mentions.append({
                "user": user_name,
                "text": msg.text,
                "time": msg.timestamp.strftime("%Y-%m-%d %H:%M"),
                "ts": msg.ts,
                "channel_id": channel_id,
            })
            if len(mentions) >= limit:
                break

    return mentions


def _build_result(
    workspace: str,
    unread_dms: list[dict[str, Any]],
    unread_channels: list[dict[str, Any]],
    mentions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the per-workspace result dict with summary and totals."""
    total_unread_dms = sum(dm.get("unread_count", 0) for dm in unread_dms)
    total_unread_channels = len(unread_channels)
    total_mentions = len(mentions)

    summary_parts = []
    if total_unread_dms:
        summary_parts.append(f"{total_unread_dms} unread message(s) in {len(unread_dms)} DM(s)")
    if total_unread_channels:
        summary_parts.append(f"{total_unread_channels} channel(s) with new messages")
    if total_mentions:
        summary_parts.append(f"{total_mentions} recent mention(s)")

    result: dict[str, Any] = {
        "summary": "; ".join(summary_parts) if summary_parts else "No unread messages",
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


# =============================================================================
# Primary path: client.counts
# =============================================================================


async def _get_unread_by_counts(
    manager: WorkspaceManager,
    workspace: str,
    include_channels: bool,
    include_dms: bool,
    include_mentions: bool,
    include_threads: bool,
    max_messages_per_conversation: int,
    max_conversations_to_check: int,
    max_dms_to_scan: int,
    max_channels_to_scan: int,
) -> dict[str, Any]:
    """Get unreads from the authoritative client.counts badge state."""
    client = manager.get_client(workspace)

    counts = await client.get_counts()

    errors: list[dict[str, Any]] = []

    def unread_entries(bucket: Any) -> list[dict[str, Any]]:
        entries = []
        for entry in bucket or []:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            if entry.get("has_unreads") or (entry.get("mention_count") or 0) > 0:
                entries.append(entry)
        # Most recent activity first - so caps drop the oldest, not the newest
        entries.sort(key=lambda e: _ts_float(e.get("latest")), reverse=True)
        return entries

    dm_entries = unread_entries(
        (counts.get("ims") or []) + (counts.get("mpims") or [])
    ) if include_dms else []
    channel_entries = unread_entries(counts.get("channels")) if include_channels else []

    dm_entries = dm_entries[:max_dms_to_scan]
    channel_entries = channel_entries[:max_channels_to_scan]

    # last_read for every conversation Slack reported - reused for mention
    # filtering so we skip per-channel conversations.info calls
    channel_read_cache: dict[str, str | None] = {}
    for bucket in ("ims", "mpims", "channels"):
        for entry in counts.get(bucket) or []:
            if isinstance(entry, dict) and entry.get("id"):
                channel_read_cache[entry["id"]] = entry.get("last_read")

    # Warm the user directory once so DM names resolve without N+1 calls
    await client.ensure_user_directory()

    async def resolve_conv(channel_id: str) -> Any:
        conv = client._conversations_cache.get(channel_id)
        if conv:
            return conv
        return await client.get_conversation_info(channel_id)

    async def fetch_conversation(entry: dict[str, Any]) -> dict[str, Any] | None:
        """Fetch name + unread message previews for one unread conversation."""
        channel_id = entry["id"]
        try:
            conv = await resolve_conv(channel_id)

            oldest = _valid_oldest(entry.get("last_read"))
            messages = await client.get_conversation_history(
                channel=channel_id,
                oldest=oldest,
                limit=max_messages_per_conversation,
            )
            if not messages:
                return None

            await client.prefetch_users(
                [msg.user for msg in messages if msg.user]
            )

            if conv.is_im and conv.user:
                name = f"@{_get_cached_user_name(client, conv.user)}"
            elif conv.is_mpim:
                participants = _mpim_participants(conv, client)
                name = ", ".join(participants) if participants else (conv.name or channel_id)
            else:
                name = conv.display_name or conv.name or channel_id

            formatted_messages = []
            for msg in reversed(messages):
                user_name = _get_cached_user_name(client, msg.user) if msg.user else "Unknown"
                formatted: dict[str, Any] = {
                    "user": user_name,
                    "text": msg.text,
                    "time": msg.timestamp.strftime("%H:%M"),
                    "ts": msg.ts,
                }
                if msg.is_thread_reply:
                    formatted["thread_ts"] = msg.thread_ts
                formatted_messages.append(formatted)

            item: dict[str, Any] = {
                "channel_id": channel_id,
                "name": name,
                "unread_count": len(messages),
                "messages": formatted_messages,
            }
            if len(messages) >= max_messages_per_conversation:
                item["more_unread"] = True
            mention_count = entry.get("mention_count") or 0
            if mention_count:
                item["mention_count"] = mention_count
            return item
        except Exception as e:
            errors.append({"channel_id": channel_id, "error": str(e)})
            return None

    # DMs get budget priority; channels use what remains
    dm_budget = min(len(dm_entries), max_conversations_to_check)
    channel_budget = max(max_conversations_to_check - dm_budget, 0)

    unread_dms: list[dict[str, Any]] = []
    unread_channels: list[dict[str, Any]] = []

    for entry in dm_entries[:dm_budget]:
        item = await fetch_conversation(entry)
        if item:
            unread_dms.append(item)

    for entry in channel_entries[:channel_budget]:
        item = await fetch_conversation(entry)
        if item:
            unread_channels.append(item)

    mentions: list[dict[str, Any]] = []
    if include_mentions:
        try:
            mentions = await _collect_mentions(client, channel_read_cache)
        except Exception as e:
            errors.append({"stage": "mentions", "error": str(e)})

    result = _build_result(workspace, unread_dms, unread_channels, mentions)

    # Thread badge state - invisible to conversations.history, so surface
    # the counts summary and point at slack_get_context for reading
    threads = counts.get("threads") or {}
    if include_threads and (threads.get("has_unreads") or threads.get("mention_count")):
        thread_info: dict[str, Any] = {
            "has_unreads": bool(threads.get("has_unreads")),
            "mention_count": threads.get("mention_count") or 0,
            "note": (
                "You have unread thread replies (not shown in channel "
                "history). Open a thread with slack_get_context using the "
                "parent message_ts to read them."
            ),
        }
        by_channel = threads.get("mention_count_by_channel")
        if isinstance(by_channel, dict) and by_channel:
            thread_info["mention_count_by_channel"] = by_channel
        result["threads"] = thread_info
        result["summary"] += "; unread thread replies"

    # Dropped-by-cap transparency: never let truncation read as "all done"
    dropped_dms = len(dm_entries) - dm_budget
    dropped_channels = len(channel_entries) - min(len(channel_entries), channel_budget)
    if dropped_dms > 0 or dropped_channels > 0:
        result["truncated"] = {
            "dms_not_shown": max(dropped_dms, 0),
            "channels_not_shown": max(dropped_channels, 0),
            "note": "Raise max_conversations to see more",
        }

    if errors:
        result["errors"] = errors

    return result


# =============================================================================
# Fallback path: conversations.info scan (original implementation)
# =============================================================================


async def _get_unread_by_scan(
    manager: WorkspaceManager,
    workspace: str,
    include_channels: bool,
    include_dms: bool,
    include_mentions: bool,
    max_messages_per_conversation: int,
    max_conversations_to_check: int,
    max_dms_to_scan: int,
    max_channels_to_scan: int,
) -> dict[str, Any]:
    """Get unreads by scanning conversations.info per conversation.

    Fallback when client.counts is unavailable. Subject to scan caps, so it
    can miss conversations outside the scanned window.
    """
    client = manager.get_client(workspace)

    unread_dms: list[dict[str, Any]] = []
    unread_channels: list[dict[str, Any]] = []
    mentions: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

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

                # unread_count is often None (always for MPIMs, sometimes for
                # DMs) - fall back to comparing last_read vs latest message
                if detailed.last_read:
                    msgs = await client.get_conversation_history(channel=conv.id, limit=1)
                    if msgs and msgs[0].ts > detailed.last_read:
                        # Count unread by fetching messages after last_read
                        unread_msgs = await client.get_conversation_history(
                            channel=conv.id, oldest=detailed.last_read, limit=10
                        )
                        return (detailed, len(unread_msgs))
            except Exception as e:
                errors.append({"channel_id": conv.id, "error": str(e)})
            return None

        # Only scan first N DMs for performance
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
            oldest = _valid_oldest(conv.last_read)
            try:
                messages = await client.get_conversation_history(
                    channel=conv.id,
                    oldest=oldest,
                    limit=max_messages_per_conversation,
                )
            except Exception as e:
                errors.append({"channel_id": conv.id, "error": str(e)})
                messages = []

            if messages:
                # Collect message author IDs for later prefetch
                for msg in messages:
                    if msg.user:
                        user_ids_to_prefetch.add(msg.user)

                # Get DM name - handle both regular DMs and MPIMs
                if conv.is_im and conv.user:
                    dm_name = f"@{_get_cached_user_name(client, conv.user)}"
                elif conv.is_mpim:
                    participants = _mpim_participants(conv, client)
                    dm_name = ", ".join(participants) if participants else (conv.name or conv.id)
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
            except Exception as e:
                errors.append({"channel_id": conv.id, "error": str(e)})
            return None

        # Limit channels to check for performance
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
            oldest = _valid_oldest(conv.last_read)
            try:
                messages = await client.get_conversation_history(
                    channel=conv.id,
                    oldest=oldest,
                    limit=max_messages_per_conversation,
                )
            except Exception as e:
                errors.append({"channel_id": conv.id, "error": str(e)})
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
            mentions = await _collect_mentions(client, {})
        except Exception as e:
            errors.append({"stage": "mentions", "error": str(e)})

    result = _build_result(workspace, unread_dms, unread_channels, mentions)
    if errors:
        result["errors"] = errors
    return result


async def _get_unread_for_workspace(
    manager: WorkspaceManager,
    workspace: str,
    include_channels: bool,
    include_dms: bool,
    include_mentions: bool,
    include_threads: bool,
    max_messages_per_conversation: int,
    max_conversations_to_check: int,
    max_dms_to_scan: int,
    max_channels_to_scan: int,
) -> dict[str, Any]:
    """Get unread messages for a single workspace: counts first, scan fallback."""
    try:
        result = await _get_unread_by_counts(
            manager=manager,
            workspace=workspace,
            include_channels=include_channels,
            include_dms=include_dms,
            include_mentions=include_mentions,
            include_threads=include_threads,
            max_messages_per_conversation=max_messages_per_conversation,
            max_conversations_to_check=max_conversations_to_check,
            max_dms_to_scan=max_dms_to_scan,
            max_channels_to_scan=max_channels_to_scan,
        )
        result["source"] = "counts"
        return result
    except Exception as e:
        fallback_reason = str(e)

    result = await _get_unread_by_scan(
        manager=manager,
        workspace=workspace,
        include_channels=include_channels,
        include_dms=include_dms,
        include_mentions=include_mentions,
        max_messages_per_conversation=max_messages_per_conversation,
        max_conversations_to_check=max_conversations_to_check,
        max_dms_to_scan=max_dms_to_scan,
        max_channels_to_scan=max_channels_to_scan,
    )
    result["source"] = "scan_fallback"
    result["fallback_reason"] = fallback_reason
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
    include_threads: bool = True,
    max_messages_per_conversation: int = 5,
    max_conversations_to_check: int = 20,  # Limit to avoid API overload
    max_dms_to_scan: int = 30,  # Cap on DMs considered per call
    max_channels_to_scan: int = 50,  # Cap on channels considered per call
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
        include_threads: Include unread thread reply summary
        max_messages_per_conversation: Max unread messages to fetch per conversation
        max_conversations_to_check: Max conversations to fetch messages for
        max_dms_to_scan: Cap on DMs considered
        max_channels_to_scan: Cap on channels considered
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
                include_threads=include_threads,
                max_messages_per_conversation=max_messages_per_conversation,
                max_conversations_to_check=max_conversations_to_check,
                max_dms_to_scan=max_dms_to_scan,
                max_channels_to_scan=max_channels_to_scan,
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
