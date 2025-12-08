"""Tool to get conversation context around a message."""

from __future__ import annotations

from typing import Any

from ..workspace import WorkspaceManager


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
        channel: Channel ID (C..., D..., G...)
        message_ts: Optional message timestamp to get context around
        context_size: Number of messages to fetch (default 10)
        workspace: Workspace name (uses default if not specified)

    Returns:
        Dictionary with messages and conversation info
    """
    client = manager.get_client(workspace)

    # Check if this is a thread request (message_ts points to a thread parent)
    is_thread = False
    messages = []

    if message_ts:
        # Try to get thread replies first
        try:
            thread_messages = await client.get_thread_replies(
                channel=channel,
                thread_ts=message_ts,
                limit=context_size,
            )
            if len(thread_messages) > 1:
                # It's a thread with replies
                is_thread = True
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

    # Resolve user names
    formatted_messages = []
    for msg in reversed(messages):  # Reverse to show oldest first
        user_name = "Unknown"
        if msg.user:
            user_name = await client.resolve_user_name(msg.user)

        formatted = {
            "ts": msg.ts,
            "user": user_name,
            "text": msg.text,
            "time": msg.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        }

        if msg.thread_ts and msg.thread_ts != msg.ts:
            formatted["thread_ts"] = msg.thread_ts
            formatted["is_reply"] = True

        if msg.reply_count:
            formatted["reply_count"] = msg.reply_count

        if msg.reactions:
            formatted["reactions"] = [
                f":{r['name']}:{r.get('count', 1)}" for r in msg.reactions
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

    return result
