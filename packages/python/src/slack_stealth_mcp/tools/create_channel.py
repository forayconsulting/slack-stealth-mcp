"""Tool for creating channels."""

from __future__ import annotations

import re
from typing import Any

from ..slack_client import SlackAPIError
from ..workspace import WorkspaceManager

# Slack channel names: lowercase letters, numbers, hyphens, underscores; max 80
MAX_CHANNEL_NAME = 80
USER_ID_RE = re.compile(r"^[UW][A-Z0-9]+$")


def normalize_channel_name(name: str) -> str:
    """Turn a human name into Slack's channel-name form.

    "Q4 Planning!" becomes "q4-planning". A leading "#" is dropped.
    """
    slug = name.strip().lstrip("#").lower()
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"[^a-z0-9_\-]", "", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:MAX_CHANNEL_NAME]


def _user_ids(invite: list[str] | str | None) -> list[str]:
    """Accept a list or comma-separated string of user IDs (or <@U...> mentions)."""
    if not invite:
        return []
    items = invite.split(",") if isinstance(invite, str) else invite
    ids = []
    for item in items:
        item = item.strip().strip("<>@").split("|")[0]
        if item:
            ids.append(item)
    return ids


async def create_channel(
    manager: WorkspaceManager,
    name: str,
    is_private: bool = False,
    description: str | None = None,
    topic: str | None = None,
    invite: list[str] | str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Create a channel, then optionally set its description/topic and invite people.

    The channel is created first. Later steps that fail are reported in
    "warnings" instead of failing the call, because the channel already exists.
    """
    client = manager.get_client(workspace)

    slug = normalize_channel_name(name)
    if not slug:
        return {
            "ok": False,
            "error": f"'{name}' has no usable characters for a channel name "
            "(use lowercase letters, numbers, hyphens, underscores)",
        }

    user_ids = _user_ids(invite)
    for uid in user_ids:
        if not USER_ID_RE.match(uid):
            return {
                "ok": False,
                "error": f"invite expects user IDs (U...), got '{uid}'. "
                "Use slack_find_conversation to look up a person's ID.",
            }

    try:
        conv = await client.create_conversation(slug, is_private=is_private)
    except SlackAPIError as e:
        hint = {
            "name_taken": "a channel with that name already exists (it may be archived)",
            "restricted_action": "this workspace does not let you create channels",
            "invalid_name_specials": "the name has characters Slack does not allow",
        }.get(e.error)
        error = f"Failed to create channel '{slug}': {e.error}"
        if hint:
            error += f" ({hint})"
        return {"ok": False, "error": error}

    result: dict[str, Any] = {
        "ok": True,
        "channel_id": conv.id,
        "name": conv.name or slug,
        "is_private": is_private,
    }
    if slug != name:
        result["requested_name"] = name
    warnings: list[str] = []

    if description:
        try:
            await client.set_conversation_purpose(conv.id, description)
            result["description"] = description
        except SlackAPIError as e:
            warnings.append(f"description not set: {e.error}")

    if topic:
        try:
            await client.set_conversation_topic(conv.id, topic)
            result["topic"] = topic
        except SlackAPIError as e:
            warnings.append(f"topic not set: {e.error}")

    if user_ids:
        try:
            errors = await client.invite_to_conversation(conv.id, user_ids)
            failed = {e.get("user"): e.get("error") for e in errors if e.get("user")}
            result["invited"] = [u for u in user_ids if u not in failed]
            for user, err in failed.items():
                warnings.append(f"could not invite {user}: {err}")
            if errors and not failed:
                warnings.extend(f"invite error: {e.get('error')}" for e in errors)
        except SlackAPIError as e:
            warnings.append(f"invites failed: {e.error}")

    if warnings:
        result["warnings"] = warnings
    return result
