"""Shared test fixtures: a fake Slack client that stubs the network layer.

FakeClient subclasses the real SlackClient and overrides only _request, so
all real parsing, caching, and pagination logic is exercised. Endpoint
handlers are canned responses (dicts) or callables receiving the request.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest

from slack_stealth_mcp.slack_client import SlackAPIError, SlackClient
from slack_stealth_mcp.types import WorkspaceConfig

# -----------------------------------------------------------------------------
# Canned workspace data
# -----------------------------------------------------------------------------

USERS = [
    {
        "id": "U01",
        "name": "alice",
        "real_name": "Alice Smith",
        "profile": {"display_name": "alice"},
    },
    {
        "id": "U02",
        "name": "bob.jones",
        "real_name": "Bob Jones",
        "profile": {"display_name": "bobby"},
    },
    {
        "id": "U03",
        "name": "carol",
        "real_name": "Carol White",
        "profile": {"display_name": ""},
    },
    {
        "id": "U0ME",
        "name": "me",
        "real_name": "Test User",
        "profile": {"display_name": "me"},
    },
    {
        "id": "UBOT",
        "name": "botuser",
        "real_name": "Bot User",
        "is_bot": True,
        "profile": {"display_name": "bot"},
    },
    {
        "id": "UDEL",
        "name": "gone",
        "real_name": "Gone Person",
        "deleted": True,
        "profile": {"display_name": "gone"},
    },
]

RAW_CONVS: dict[str, dict[str, Any]] = {
    "C100": {"id": "C100", "name": "general", "is_channel": True, "is_member": True},
    "C200": {
        "id": "C200",
        "name": "design-team",
        "is_channel": True,
        "is_private": True,
        "is_member": True,
    },
    "D01": {"id": "D01", "is_im": True, "user": "U01"},
    "D02": {"id": "D02", "is_im": True, "user": "U02"},
    "G01": {"id": "G01", "name": "mpdm-alice--bob.jones--carol-1", "is_mpim": True},
}


def _conv_matches_types(conv: dict[str, Any], types: str) -> bool:
    wanted = set(types.split(","))
    if conv.get("is_im"):
        return "im" in wanted
    if conv.get("is_mpim"):
        return "mpim" in wanted
    if conv.get("is_private"):
        return "private_channel" in wanted
    return "public_channel" in wanted


DEFAULT_COUNTS = {
    "ok": True,
    "channels": [
        {
            "id": "C100",
            "last_read": "1700000100.000100",
            "latest": "1700000200.000200",
            "has_unreads": True,
            "mention_count": 1,
        },
        {
            "id": "C200",
            "last_read": "1700000300.000000",
            "latest": "1700000250.000000",
            "has_unreads": False,
            "mention_count": 0,
        },
    ],
    "ims": [
        {
            "id": "D01",
            "last_read": "1700000100.000100",
            "latest": "1700000400.000000",
            "has_unreads": True,
            "mention_count": 0,
        },
        {
            "id": "D02",
            "last_read": "1700000500.000000",
            "latest": "1700000450.000000",
            "has_unreads": False,
        },
    ],
    "mpims": [
        {
            "id": "G01",
            "last_read": "1700000100.000100",
            "latest": "1700000300.000000",
            "has_unreads": True,
        },
    ],
    "threads": {"has_unreads": True, "mention_count": 2},
}

DEFAULT_HISTORY: dict[str, list[dict[str, Any]]] = {
    # Newest first, like the real API
    "D01": [
        {"ts": "1700000400.000000", "user": "U01", "text": "are you around?"},
        {"ts": "1700000150.000100", "user": "U01", "text": "hey"},
    ],
    "G01": [
        {"ts": "1700000300.000000", "user": "U02", "text": "group update"},
    ],
    "C100": [
        {"ts": "1700000200.000200", "user": "U02", "text": "<@U0ME> can you look?"},
    ],
}

DEFAULT_SEARCH = {
    "ok": True,
    "messages": {
        "matches": [
            {
                "ts": "1700000150.000100",
                "text": "<@U0ME> ping",
                "user": "U01",
                "channel": {"id": "C100", "name": "general"},
            },
            {
                # Older than C100's last_read -> already read, filtered out
                "ts": "1700000050.000000",
                "text": "<@U0ME> old ping",
                "user": "U02",
                "channel": {"id": "C100", "name": "general"},
            },
        ],
        "paging": {"total": 2, "page": 1, "pages": 1},
    },
}


# -----------------------------------------------------------------------------
# Fakes
# -----------------------------------------------------------------------------


class FakeClient(SlackClient):
    """SlackClient with _request stubbed to canned endpoint handlers."""

    def __init__(self, responses: dict[str, Any] | None = None):
        super().__init__(WorkspaceConfig(xoxc_token="xoxc-test", xoxd_cookie="d-test"))
        self._rate_limiter._min_interval = 0  # no artificial delays in tests
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.responses: dict[str, Any] = default_responses()
        if responses:
            self.responses.update(responses)

    async def _request(self, method: str, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((method, endpoint, kwargs))
        handler = self.responses.get(endpoint)
        if handler is None:
            raise SlackAPIError(f"no fake handler for {endpoint}")
        if callable(handler):
            return handler(method, endpoint, kwargs)
        return handler

    @property
    def endpoints_called(self) -> list[str]:
        return [endpoint for _, endpoint, _ in self.calls]


def default_responses() -> dict[str, Any]:
    """Endpoint handlers reflecting the canned workspace."""

    def conversations_list(method: str, endpoint: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        types = kwargs.get("params", {}).get(
            "types", "public_channel,private_channel,mpim,im"
        )
        channels = [c for c in RAW_CONVS.values() if _conv_matches_types(c, types)]
        return {"ok": True, "channels": channels, "response_metadata": {}}

    def conversations_info(method: str, endpoint: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        channel = kwargs.get("params", {}).get("channel")
        if channel not in RAW_CONVS:
            raise SlackAPIError("channel_not_found")
        return {"ok": True, "channel": RAW_CONVS[channel]}

    def conversations_history(method: str, endpoint: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        params = kwargs.get("params", {})
        channel = params.get("channel")
        oldest = params.get("oldest")
        limit = params.get("limit", 100)
        messages = DEFAULT_HISTORY.get(channel, [])
        if oldest:
            messages = [m for m in messages if m["ts"] > oldest]
        return {"ok": True, "messages": messages[:limit]}

    return {
        "users.list": {"ok": True, "members": USERS, "response_metadata": {}},
        "users.info": lambda m, e, kw: {
            "ok": True,
            "user": next(
                u for u in USERS if u["id"] == kw.get("params", {}).get("user")
            ),
        },
        "conversations.list": conversations_list,
        "conversations.info": conversations_info,
        "conversations.history": conversations_history,
        "client.counts": DEFAULT_COUNTS,
        "auth.test": {"ok": True, "user_id": "U0ME", "team": "testteam"},
        "search.messages": DEFAULT_SEARCH,
    }


class FakeManager:
    """Minimal WorkspaceManager stand-in."""

    def __init__(self, client: FakeClient, name: str = "testws"):
        self._client = client
        self.workspace_names = [name]
        self.default_workspace = name

    def get_client(self, workspace: str | None = None) -> FakeClient:
        return self._client


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def fake_manager(fake_client: FakeClient) -> FakeManager:
    return FakeManager(fake_client)
