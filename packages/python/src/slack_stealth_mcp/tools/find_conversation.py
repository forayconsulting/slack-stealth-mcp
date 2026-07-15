"""Tool to find conversations and people by name."""

from __future__ import annotations

import re
from typing import Any

from ..slack_client import SlackClient, normalize_name
from ..types import SlackConversation, SlackUser
from ..workspace import WorkspaceManager


def _tokens(text: str) -> list[str]:
    """Split a name/query into normalized word tokens."""
    return [t for t in re.split(r"[^a-zA-Z0-9]+", text.lower()) if t]


def score_name(query: str, candidate: str) -> int:
    """Score how well a candidate name matches the query (0-100).

    Tiers: exact 100, prefix 80, all query tokens present as words 60,
    substring 40. Multi-token queries get a +10 bonus when every token
    appears somewhere in the candidate.
    """
    query_norm = normalize_name(query)
    cand_norm = normalize_name(candidate)
    if not query_norm or not cand_norm:
        return 0

    query_tokens = _tokens(query)
    cand_tokens = _tokens(candidate)

    if cand_norm == query_norm:
        return 100

    score = 0
    if cand_norm.startswith(query_norm):
        score = 80
    elif query_tokens and all(
        any(ct == qt or ct.startswith(qt) for ct in cand_tokens)
        for qt in query_tokens
    ):
        score = 60
    elif query_norm in cand_norm:
        score = 40

    if score and len(query_tokens) > 1:
        score = min(score + 10, 100)

    return score


def _mpim_participants(conv: SlackConversation, client: SlackClient) -> list[str]:
    """Resolve an mpdm-alice--bob--carol-1 name to participant display names."""
    if not conv.name or not conv.name.startswith("mpdm-"):
        return []
    handles = conv.name.removeprefix("mpdm-").rsplit("-", 1)[0].split("--")
    names = []
    for handle in handles:
        user = client.get_user_by_name(handle)
        names.append(user.display if user else handle)
    return names


def _best_user_score(query: str, user: SlackUser) -> int:
    """Best match score across a user's handle, real name, and display name."""
    candidates = [n for n in (user.name, user.real_name, user.display_name) if n]
    return max((score_name(query, c) for c in candidates), default=0)


async def find_conversation(
    manager: WorkspaceManager,
    query: str,
    include_users: bool = True,
    limit: int = 15,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Find channels, DMs, group DMs, and people by name.

    This does NOT mark anything as read.

    Args:
        manager: Workspace manager
        query: Name to search for (channel name, person's name, or handle)
        include_users: Also match workspace users without an existing DM
        limit: Max matches to return
        workspace: Workspace name (uses default if not specified)

    Returns:
        Dictionary with ranked matches
    """
    client = manager.get_client(workspace)

    directory_ok = await client.ensure_user_directory()
    conversations = await client.list_conversations(max_pages=0)

    matches: list[dict[str, Any]] = []
    dm_user_ids: set[str] = set()

    for conv in conversations:
        if conv.is_im:
            if not conv.user:
                continue
            dm_user_ids.add(conv.user)
            user = client._users_cache.get(conv.user)
            if not user:
                continue
            score = _best_user_score(query, user)
            if score:
                matches.append({
                    "id": conv.id,
                    "name": f"@{user.display}",
                    "type": "dm",
                    "user_id": conv.user,
                    "score": score,
                })
        elif conv.is_mpim:
            participants = _mpim_participants(conv, client)
            display = ", ".join(participants) if participants else (conv.name or conv.id)
            # Single-participant matches are discounted so the direct DM
            # with that person outranks group DMs they happen to be in
            score = max(
                score_name(query, display),
                int(0.9 * max((score_name(query, p) for p in participants), default=0)),
            )
            if score:
                matches.append({
                    "id": conv.id,
                    "name": display,
                    "type": "group_dm",
                    "score": score,
                })
        else:
            if not conv.name:
                continue
            score = score_name(query, conv.name)
            if score:
                matches.append({
                    "id": conv.id,
                    "name": conv.name,
                    "type": conv.conversation_type,
                    "score": score,
                })

    # People in the directory with no existing DM are still reachable:
    # slack_reply with target=U... opens one
    if include_users and directory_ok:
        for user in client.directory_users():
            if user.id in dm_user_ids or user.is_bot:
                continue
            score = _best_user_score(query, user)
            if score:
                matches.append({
                    "id": user.id,
                    "name": f"@{user.display}",
                    "type": "user",
                    "user_id": user.id,
                    "score": score,
                    "hint": "No DM yet - use slack_reply with this user ID as target to start one",
                })

    matches.sort(key=lambda m: m["score"], reverse=True)
    matches = matches[:limit]

    result: dict[str, Any] = {
        "query": query,
        "workspace": workspace or manager.default_workspace,
        "count": len(matches),
        "matches": matches,
    }
    if not directory_ok:
        result["warning"] = (
            "User directory unavailable - DM and people matching may be incomplete"
        )
    if not matches:
        result["hint"] = (
            "No matches. Try a shorter query, a person's Slack handle, or "
            "slack_list_conversations to browse everything."
        )
    return result
