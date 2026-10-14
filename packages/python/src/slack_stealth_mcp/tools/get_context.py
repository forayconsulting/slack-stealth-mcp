"""Tool to get conversation context around a message."""

from __future__ import annotations

from typing import Any

from ..refs import resolve_channel_ref
from ..workspace import WorkspaceManager

# Slack's maximum page size for conversations.replies
THREAD_FETCH_LIMIT = 1000


async def get_context(
    manager: WorkspaceManager,
    channel: str,
    message_ts: str | None = None,
    context_size: int = 10,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Get messages from a conversation, optionally around a specific message.

    This does NOT mark messages as read.

    Args:
        manager: Workspace manager
        channel: Channel ID (C..., D..., G...) or Slack URL
        message_ts: Optional message timestamp to get context around
        context_size: Number of messages to fetch (default 10)
        workspace: Workspace name (uses default if not specified)

    Returns:
        Dictionary with messages and conversation info
    """
    client = manager.get_client(workspace)

    # Resolve URLs/raw IDs; a message URL supplies message_ts when not given
    try:
        ref = resolve_channel_ref(channel)
    except ValueError as e:
        return {"error": str(e)}
    channel = ref.channel_id
    if ref.message_ts and not message_ts:
        message_ts = ref.message_ts

    # Check if this is a thread request (message_ts points to a thread parent)
    is_thread = False
    messages = []
    total_thread_messages = 0
    omitted_replies = 0

    if message_ts:
        # Try to get thread replies first. Fetch a full page (Slack's max for
        # conversations.replies) so the newest replies aren't cut off.
        try:
            thread_messages = await client.get_thread_replies(
                channel=channel,
                thread_ts=message_ts,
                limit=THREAD_FETCH_LIMIT,
            )
            if len(thread_messages) > 1:
                # It's a thread with replies (root first, then oldest to newest)
                is_thread = True
                total_thread_messages = len(thread_messages)
                if total_thread_messages > context_size:
                    # Keep the root plus the newest (context_size - 1) replies
                    keep = max(context_size - 1, 0)
                    replies = thread_messages[1:]
                    kept = replies[len(replies) - keep:] if keep else []
                    omitted_replies = len(replies) - len(kept)
                    messages = [thread_messages[0]] + kept
                else:
                    messages = thread_messages
        except Exception:
            pass

    if not messages:
        # Get channel history
        if message_ts:
            # Get messages around the specified timestamp
            # First get messages before (including target)
            messages = await client.get_conversation_history(
                channel=channel,
                latest=message_ts,
                limit=context_size // 2 + 1,
                inclusive=True,
            )
            # Then get messages after
            after_messages = await client.get_conversation_history(
                channel=channel,
                oldest=message_ts,
                limit=context_size // 2,
                inclusive=False,
            )
            # Combine (after are newest first, messages are also newest first)
            messages = after_messages + messages
        else:
            # Just get recent messages
            messages = await client.get_conversation_history(
                channel=channel,
                limit=context_size,
            )

    # Format messages - use cached user names to avoid N API calls
    # History comes back newest first, so reverse it; threads are already
    # oldest first with the root first.
    ordered = messages if is_thread else list(reversed(messages))
    formatted_messages = []
    for msg in ordered:
        user_name = "Unknown"
        if msg.user:
            cached = client._users_cache.get(msg.user)
            user_name = cached.display if cached else msg.user[:8] + "..."

        formatted = {
            "ts": msg.ts,
            "user": user_name,
            "text": msg.text,
            "time": msg.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": msg.user,
        }

        # Every message in a thread carries the root ts, the root included
        thread_ts = msg.thread_ts or (message_ts if is_thread else None)
        if thread_ts:
            formatted["thread_ts"] = thread_ts
            if thread_ts != msg.ts:
                formatted["is_reply"] = True

        if msg.reply_count:
            formatted["reply_count"] = msg.reply_count

        if msg.reactions:
            formatted["reactions"] = [
                f":{r['name']}:{r['count'] if r.get('count', 1) > 1 else ''}"
                for r in msg.reactions
            ]

        formatted_messages.append(formatted)

    # Get conversation info for context
    try:
        conv = await client.get_conversation_info(channel)
        channel_name = conv.display_name
        channel_type = conv.conversation_type
    except Exception:
        channel_name = channel
        channel_type = "unknown"

    result: dict[str, Any] = {
        "channel": channel_name,
        "channel_id": channel,
        "channel_type": channel_type,
        "messages": formatted_messages,
        "message_count": len(formatted_messages),
    }

    if is_thread:
        result["is_thread"] = True
        result["thread_ts"] = message_ts
        result["total_thread_messages"] = total_thread_messages
        if omitted_replies > 0:
            result["omitted_replies"] = omitted_replies

    return result
