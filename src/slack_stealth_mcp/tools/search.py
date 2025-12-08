"""Tool for comprehensive Slack search."""

from __future__ import annotations

from typing import Any

from ..workspace import WorkspaceManager


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
        # Handle both channel name and ID
        if in_channel.startswith("C") or in_channel.startswith("G"):
            query_parts.append(f"in:{in_channel}")
        elif in_channel.startswith("@"):
            query_parts.append(f"in:{in_channel}")
        else:
            query_parts.append(f"in:#{in_channel}")

    if from_user:
        if from_user.startswith("U"):
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

    # Format results
    formatted_messages = []
    for msg in results.messages:
        user_name = "Unknown"
        if msg.user:
            user_name = await client.resolve_user_name(msg.user)

        formatted = {
            "ts": msg.ts,
            "user": user_name,
            "text": msg.text[:500] + ("..." if len(msg.text) > 500 else ""),
            "time": msg.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        }

        if msg.thread_ts:
            formatted["thread_ts"] = msg.thread_ts
            formatted["is_thread_reply"] = msg.thread_ts != msg.ts

        formatted_messages.append(formatted)

    return {
        "query": full_query,
        "results": formatted_messages,
        "total_matches": results.total,
        "page": results.page,
        "total_pages": results.pages,
        "showing": len(formatted_messages),
    }
