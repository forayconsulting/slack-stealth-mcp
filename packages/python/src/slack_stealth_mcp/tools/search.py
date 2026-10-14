"""Tool for comprehensive Slack search."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..workspace import WorkspaceManager

# Slack ID shapes: an uppercase prefix letter plus 8+ uppercase alphanumerics.
# Plain names like "design", "general", or "will" never match.
_CHANNEL_ID_RE = re.compile(r"^[CGD][A-Z0-9]{8,}$")
_USER_ID_RE = re.compile(r"^[UW][A-Z0-9]{8,}$")


def _thread_ts_from_permalink(permalink: str | None) -> str | None:
    """Read the thread_ts query param from a search result permalink, if any."""
    if not permalink:
        return None
    try:
        values = parse_qs(urlparse(permalink).query).get("thread_ts")
    except ValueError:
        return None
    return values[0] if values else None


async def search(
    manager: WorkspaceManager,
    query: str,
    in_channel: str | None = None,
    from_user: str | None = None,
    after_date: str | None = None,
    before_date: str | None = None,
    has_link: bool = False,
    has_reaction: bool = False,
    is_thread: bool = False,
    limit: int = 20,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Search messages across the workspace.

    Args:
        manager: Workspace manager
        query: Search terms
        in_channel: Optional channel name/ID to search in
        from_user: Optional user ID/name to filter by sender
        after_date: Only messages after this date (YYYY-MM-DD)
        before_date: Only messages before this date (YYYY-MM-DD)
        has_link: Only messages with links
        has_reaction: Only messages with reactions
        is_thread: Only messages in threads
        limit: Maximum results (default 20, max 100)
        workspace: Workspace name (uses default if not specified)

    Returns:
        Dictionary with search results
    """
    client = manager.get_client(workspace)

    # Build the full query with modifiers
    query_parts = [query]

    if in_channel:
        # Channel/DM IDs need the <#ID> form; a bare in:ID matches nothing
        if _CHANNEL_ID_RE.fullmatch(in_channel):
            query_parts.append(f"in:<#{in_channel}>")
        elif in_channel.startswith("@"):
            query_parts.append(f"in:{in_channel}")
        else:
            query_parts.append(f"in:#{in_channel}")

    if from_user:
        if _USER_ID_RE.fullmatch(from_user):
            query_parts.append(f"from:<@{from_user}>")
        elif from_user.startswith("@"):
            query_parts.append(f"from:{from_user}")
        else:
            query_parts.append(f"from:@{from_user}")

    if after_date:
        query_parts.append(f"after:{after_date}")

    if before_date:
        query_parts.append(f"before:{before_date}")

    if has_link:
        query_parts.append("has:link")

    if has_reaction:
        query_parts.append("has:reaction")

    if is_thread:
        query_parts.append("is:thread")

    full_query = " ".join(query_parts)

    # Perform search
    results = await client.search_messages(
        query=full_query,
        count=min(limit, 100),
        sort="timestamp",
        sort_dir="desc",
    )

    # Format results - use cached user names to avoid N API calls
    formatted_messages = []
    for msg in results.messages:
        # Use cache, don't make API call for each message
        user_name = "Unknown"
        if msg.user:
            cached = client._users_cache.get(msg.user)
            user_name = cached.display if cached else msg.user[:8] + "..."

        formatted = {
            "ts": msg.ts,
            "user": user_name,
            "text": msg.text[:500] + ("..." if len(msg.text) > 500 else ""),
            "time": msg.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            # IDs from the search response itself; no extra API calls
            "channel_id": msg.channel_id,
            "channel_name": (msg.channel or {}).get("name"),
            "user_id": msg.user,
            "permalink": msg.permalink,
        }

        # Search matches can omit thread_ts on replies; the permalink keeps it
        thread_ts = msg.thread_ts or _thread_ts_from_permalink(msg.permalink)
        if thread_ts:
            formatted["thread_ts"] = thread_ts
            formatted["is_thread_reply"] = thread_ts != msg.ts

        formatted_messages.append(formatted)

    return {
        "query": full_query,
        "results": formatted_messages,
        "total_matches": results.total,
        "page": results.page,
        "total_pages": results.pages,
        "showing": len(formatted_messages),
    }
