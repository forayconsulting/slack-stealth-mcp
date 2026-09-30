"""Tools for scheduling messages to send later.

Uses Slack's internal drafts API, the same mechanism behind the app's
"Schedule for later": a draft with date_scheduled is sent by Slack's
servers at that time and appears in the user's Scheduled list, where it
can also be edited or cancelled by hand.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from ..refs import resolve_channel_ref
from ..rich_text import mrkdwn_to_rich_text, rich_text_to_plain
from ..slack_client import SlackAPIError
from ..workspace import WorkspaceManager
from .reply import conversation_label

# Slack's own limit for scheduled messages
MAX_SCHEDULE_DAYS = 120
# Too close to now and the send can race the create
MIN_LEAD_SECONDS = 10

_ERROR_HINTS = {
    "attached_draft_exists": (
        "that conversation already has an unsent draft in the composer; "
        "send or clear it in Slack, or schedule into a thread instead"
    ),
    "invalid_channel": "target must resolve to a channel or DM ID",
}


def parse_send_at(send_at: str | int | float) -> int:
    """Parse a send time into Unix seconds.

    Accepts Unix seconds (number or numeric string) or ISO-8601. An ISO time
    without an offset is taken as this machine's local time.

    Raises:
        ValueError: If the value can't be parsed
    """
    if isinstance(send_at, (int, float)):
        return int(send_at)
    value = str(send_at).strip()
    try:
        return int(float(value))
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(
            f"Could not parse send_at {send_at!r}. Use ISO-8601 like "
            "2026-09-30T09:00:00-05:00, or Unix seconds."
        ) from None
    if dt.tzinfo is None:
        dt = dt.astimezone()  # assume local time
    return int(dt.timestamp())


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat()


def _error(prefix: str, e: SlackAPIError) -> str:
    hint = _ERROR_HINTS.get(e.error)
    return f"{prefix}: {e.error}" + (f" ({hint})" if hint else "")


def _is_scheduled(draft: dict[str, Any]) -> bool:
    return (
        bool(draft.get("date_scheduled"))
        and not draft.get("is_sent")
        and not draft.get("is_deleted")
    )


async def schedule_message(
    manager: WorkspaceManager,
    target: str,
    message: str,
    send_at: str | int | float,
    thread_ts: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Schedule a message to be sent later.

    Args:
        manager: Workspace manager
        target: Channel ID, DM ID, user ID, or Slack URL (same as reply)
        message: Message text in Slack mrkdwn
        send_at: When to send - ISO-8601 or Unix seconds
        thread_ts: Parent message timestamp for a thread reply
        workspace: Workspace name (uses default if not specified)

    Returns:
        Dictionary with the scheduled draft's details
    """
    client = manager.get_client(workspace)

    if not message.strip():
        return {"ok": False, "error": "message is empty"}

    try:
        post_at = parse_send_at(send_at)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    now = time.time()
    if post_at < now + MIN_LEAD_SECONDS:
        return {
            "ok": False,
            "error": f"send_at {_iso(post_at)} is not in the future (now is {_iso(int(now))})",
        }
    if post_at > now + MAX_SCHEDULE_DAYS * 86400:
        return {
            "ok": False,
            "error": f"send_at is more than {MAX_SCHEDULE_DAYS} days out (Slack's limit)",
        }

    try:
        ref = resolve_channel_ref(target)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    channel_id = ref.channel_id
    if ref.message_ts and not thread_ts:
        thread_ts = ref.message_ts

    if channel_id.startswith(("U", "W")):
        try:
            channel_id = await client.open_conversation([channel_id])
        except SlackAPIError as e:
            return {
                "ok": False,
                "error": f"Failed to open DM with user {channel_id}: {e.error}",
            }

    blocks = mrkdwn_to_rich_text(message)

    try:
        draft = await client.create_draft(
            channel=channel_id,
            blocks=blocks,
            thread_ts=thread_ts,
            date_scheduled=post_at,
        )
    except SlackAPIError as e:
        return {
            "ok": False,
            "error": _error("Failed to schedule message", e),
            "channel_id": channel_id,
        }

    result: dict[str, Any] = {
        "ok": True,
        "scheduled_id": draft.get("id"),
        "channel_id": channel_id,
        "send_at": _iso(post_at),
        "send_at_unix": post_at,
        "text": rich_text_to_plain(blocks),
        "sent_to": await conversation_label(client, channel_id),
    }
    if thread_ts:
        result["thread_ts"] = thread_ts
    return result


async def list_scheduled(
    manager: WorkspaceManager,
    workspace: str | None = None,
) -> dict[str, Any]:
    """List messages scheduled to send later, soonest first.

    Includes messages scheduled in the Slack app, not just by this tool.
    """
    client = manager.get_client(workspace)

    try:
        drafts = await client.list_drafts()
    except SlackAPIError as e:
        return {"ok": False, "error": _error("Failed to list scheduled messages", e)}

    scheduled = sorted(
        (d for d in drafts if _is_scheduled(d)), key=lambda d: d["date_scheduled"]
    )

    labels: dict[str, str] = {}
    items: list[dict[str, Any]] = []
    for draft in scheduled:
        destination = (draft.get("destinations") or [{}])[0]
        channel_id = destination.get("channel_id", "")
        if channel_id and channel_id not in labels:
            labels[channel_id] = await conversation_label(client, channel_id)
        item: dict[str, Any] = {
            "scheduled_id": draft.get("id"),
            "send_at": _iso(int(draft["date_scheduled"])),
            "send_at_unix": int(draft["date_scheduled"]),
            "channel_id": channel_id,
            "sent_to": labels.get(channel_id, channel_id),
            "text": rich_text_to_plain(draft.get("blocks") or []),
        }
        if destination.get("thread_ts"):
            item["thread_ts"] = destination["thread_ts"]
        items.append(item)

    return {"ok": True, "count": len(items), "scheduled": items}


async def cancel_scheduled(
    manager: WorkspaceManager,
    scheduled_id: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Cancel a scheduled message so it is never sent.

    Only scheduled drafts can be cancelled; ordinary unsent drafts the user
    typed are left alone.
    """
    client = manager.get_client(workspace)

    try:
        drafts = await client.list_drafts()
    except SlackAPIError as e:
        return {"ok": False, "error": _error("Failed to look up scheduled message", e)}

    draft = next((d for d in drafts if d.get("id") == scheduled_id), None)
    if draft is None or not _is_scheduled(draft):
        return {
            "ok": False,
            "error": (
                f"No pending scheduled message with id {scheduled_id} "
                "(it may have already been sent or cancelled). "
                "Use slack_list_scheduled to see current ids."
            ),
        }

    try:
        await client.delete_draft(scheduled_id, draft.get("last_updated_ts", ""))
    except SlackAPIError as e:
        return {"ok": False, "error": _error("Failed to cancel scheduled message", e)}

    destination = (draft.get("destinations") or [{}])[0]
    channel_id = destination.get("channel_id", "")
    return {
        "ok": True,
        "cancelled": scheduled_id,
        "was_scheduled_for": _iso(int(draft["date_scheduled"])),
        "channel_id": channel_id,
        "sent_to": await conversation_label(client, channel_id) if channel_id else "",
        "text": rich_text_to_plain(draft.get("blocks") or []),
    }
