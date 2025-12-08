"""Async Slack API client using xoxc/xoxd session tokens."""

import asyncio
import time
from typing import Any, Dict, List, Optional

import httpx

from .types import (
    SlackConversation,
    SlackMessage,
    SlackSearchResult,
    SlackUser,
    WorkspaceConfig,
)

SLACK_API_BASE = "https://slack.com/api"


class RateLimiter:
    """Token bucket rate limiter with exponential backoff."""

    def __init__(self, requests_per_minute: float = 50):
        """Initialize rate limiter.

        Args:
            requests_per_minute: Maximum requests per minute
        """
        self._interval = 60.0 / requests_per_minute
        self._last_request = 0.0
        self._lock = asyncio.Lock()
        self._backoff = 1.0

    async def acquire(self) -> None:
        """Wait until a request can be made."""
        async with self._lock:
            now = time.monotonic()
            wait_time = self._last_request + (self._interval * self._backoff) - now
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self._last_request = time.monotonic()

    def backoff(self) -> None:
        """Increase backoff after rate limit hit."""
        self._backoff = min(self._backoff * 2, 60.0)

    def reset_backoff(self) -> None:
        """Reset backoff after successful request."""
        self._backoff = 1.0


class SlackAPIError(Exception):
    """Exception for Slack API errors."""

    def __init__(self, error: str, response: Optional[Dict[str, Any]] = None):
        self.error = error
        self.response = response or {}
        super().__init__(f"Slack API error: {error}")


class SlackClient:
    """Async client for Slack API using session tokens."""

    def __init__(self, config: WorkspaceConfig):
        """Initialize Slack client.

        Args:
            config: Workspace configuration with tokens
        """
        self._config = config
        self._client: Optional[httpx.AsyncClient] = None
        self._rate_limiter = RateLimiter()

        # In-memory caches
        self._users_cache: Dict[str, SlackUser] = {}
        self._conversations_cache: Dict[str, SlackConversation] = {}
        self._last_read_cache: Dict[str, str] = {}  # channel_id -> last_read ts
        self._cache_time: float = 0

    async def __aenter__(self) -> "SlackClient":
        """Async context manager entry."""
        await self._ensure_client()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure HTTP client is initialized."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=SLACK_API_BASE,
                headers={
                    "Authorization": f"Bearer {self._config.xoxc_token}",
                    "Cookie": f"d={self._config.xoxd_cookie}",
                },
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Make a rate-limited request to Slack API.

        Args:
            method: HTTP method (GET or POST)
            endpoint: API endpoint (e.g., "conversations.list")
            **kwargs: Additional arguments for httpx request

        Returns:
            JSON response data

        Raises:
            SlackAPIError: If the API returns an error
        """
        await self._rate_limiter.acquire()
        client = await self._ensure_client()

        try:
            if method.upper() == "GET":
                response = await client.get(endpoint, params=kwargs.get("params"))
            else:
                response = await client.post(endpoint, data=kwargs.get("data"))

            response.raise_for_status()
            data = response.json()

            if not data.get("ok"):
                error = data.get("error", "unknown_error")
                if error == "ratelimited":
                    self._rate_limiter.backoff()
                    retry_after = int(response.headers.get("Retry-After", 60))
                    await asyncio.sleep(retry_after)
                    return await self._request(method, endpoint, **kwargs)
                raise SlackAPIError(error, data)

            self._rate_limiter.reset_backoff()
            return data

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                self._rate_limiter.backoff()
                retry_after = int(e.response.headers.get("Retry-After", 60))
                await asyncio.sleep(retry_after)
                return await self._request(method, endpoint, **kwargs)
            raise SlackAPIError(f"HTTP {e.response.status_code}")

    # =========================================================================
    # Conversations API
    # =========================================================================

    async def list_conversations(
        self,
        types: str = "public_channel,private_channel,mpim,im",
        exclude_archived: bool = True,
        limit: int = 200,
    ) -> List[SlackConversation]:
        """List all conversations the user has access to.

        Args:
            types: Comma-separated conversation types
            exclude_archived: Whether to exclude archived channels
            limit: Max results per page

        Returns:
            List of conversations
        """
        conversations: List[SlackConversation] = []
        cursor: Optional[str] = None

        while True:
            params: Dict[str, Any] = {
                "types": types,
                "exclude_archived": str(exclude_archived).lower(),
                "limit": limit,
            }
            if cursor:
                params["cursor"] = cursor

            data = await self._request("GET", "conversations.list", params=params)

            for ch in data.get("channels", []):
                conv = SlackConversation(
                    id=ch["id"],
                    name=ch.get("name"),
                    is_channel=ch.get("is_channel", False),
                    is_group=ch.get("is_group", False),
                    is_im=ch.get("is_im", False),
                    is_mpim=ch.get("is_mpim", False),
                    is_private=ch.get("is_private", False),
                    is_archived=ch.get("is_archived", False),
                    is_member=ch.get("is_member", True),
                    user=ch.get("user"),
                    last_read=ch.get("last_read"),
                    unread_count=ch.get("unread_count"),
                    unread_count_display=ch.get("unread_count_display"),
                )
                conversations.append(conv)
                self._conversations_cache[conv.id] = conv
                if conv.last_read:
                    self._last_read_cache[conv.id] = conv.last_read

            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        self._cache_time = time.monotonic()
        return conversations

    async def get_conversation_info(self, channel: str) -> SlackConversation:
        """Get detailed info about a conversation.

        Args:
            channel: Channel ID

        Returns:
            Conversation details
        """
        data = await self._request(
            "GET",
            "conversations.info",
            params={"channel": channel, "include_num_members": "true"},
        )

        ch = data["channel"]
        return SlackConversation(
            id=ch["id"],
            name=ch.get("name"),
            is_channel=ch.get("is_channel", False),
            is_group=ch.get("is_group", False),
            is_im=ch.get("is_im", False),
            is_mpim=ch.get("is_mpim", False),
            is_private=ch.get("is_private", False),
            is_archived=ch.get("is_archived", False),
            is_member=ch.get("is_member", True),
            user=ch.get("user"),
            last_read=ch.get("last_read"),
            unread_count=ch.get("unread_count"),
            unread_count_display=ch.get("unread_count_display"),
        )

    async def get_conversation_history(
        self,
        channel: str,
        oldest: Optional[str] = None,
        latest: Optional[str] = None,
        limit: int = 100,
        inclusive: bool = False,
    ) -> List[SlackMessage]:
        """Get message history for a conversation.

        NOTE: This does NOT mark messages as read.

        Args:
            channel: Channel ID
            oldest: Only messages after this timestamp
            latest: Only messages before this timestamp
            limit: Max messages to return
            inclusive: Include boundary messages

        Returns:
            List of messages (newest first)
        """
        params: Dict[str, Any] = {
            "channel": channel,
            "limit": limit,
            "inclusive": str(inclusive).lower(),
        }
        if oldest:
            params["oldest"] = oldest
        if latest:
            params["latest"] = latest

        data = await self._request("GET", "conversations.history", params=params)

        messages: List[SlackMessage] = []
        for msg in data.get("messages", []):
            messages.append(self._parse_message(msg))

        return messages

    async def get_thread_replies(
        self,
        channel: str,
        thread_ts: str,
        limit: int = 100,
    ) -> List[SlackMessage]:
        """Get all replies in a thread.

        NOTE: This does NOT mark messages as read.

        Args:
            channel: Channel ID
            thread_ts: Parent message timestamp
            limit: Max messages to return

        Returns:
            List of messages including parent (oldest first)
        """
        params: Dict[str, Any] = {
            "channel": channel,
            "ts": thread_ts,
            "limit": limit,
        }

        data = await self._request("GET", "conversations.replies", params=params)

        messages: List[SlackMessage] = []
        for msg in data.get("messages", []):
            messages.append(self._parse_message(msg))

        return messages

    async def open_conversation(self, users: List[str]) -> str:
        """Open or get existing DM/group DM.

        Args:
            users: List of user IDs (1 for DM, 2-8 for group DM)

        Returns:
            Channel ID of the conversation
        """
        data = await self._request(
            "POST",
            "conversations.open",
            data={"users": ",".join(users), "return_im": "true"},
        )
        return data["channel"]["id"]

    async def mark_conversation(self, channel: str, ts: str) -> None:
        """Mark conversation as read up to a timestamp.

        This is the ONLY method that affects read state.

        Args:
            channel: Channel ID
            ts: Timestamp to mark as read
        """
        await self._request(
            "POST",
            "conversations.mark",
            data={"channel": channel, "ts": ts},
        )
        self._last_read_cache[channel] = ts

    # =========================================================================
    # Messages API
    # =========================================================================

    async def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: Optional[str] = None,
        reply_broadcast: bool = False,
    ) -> SlackMessage:
        """Post a message to a channel or thread.

        NOTE: This does NOT mark the channel as read.

        Args:
            channel: Channel ID
            text: Message text (mrkdwn format supported)
            thread_ts: Parent message ts for thread reply
            reply_broadcast: Also post to channel (for thread replies)

        Returns:
            The posted message
        """
        payload: Dict[str, Any] = {
            "channel": channel,
            "text": text,
        }
        if thread_ts:
            payload["thread_ts"] = thread_ts
            if reply_broadcast:
                payload["reply_broadcast"] = "true"

        data = await self._request("POST", "chat.postMessage", data=payload)
        return self._parse_message(data["message"])

    # =========================================================================
    # Search API
    # =========================================================================

    async def search_messages(
        self,
        query: str,
        sort: str = "timestamp",
        sort_dir: str = "desc",
        count: int = 20,
        page: int = 1,
    ) -> SlackSearchResult:
        """Search messages in the workspace.

        Args:
            query: Search query (supports Slack search syntax)
            sort: Sort by "score" or "timestamp"
            sort_dir: "asc" or "desc"
            count: Results per page (max 100)
            page: Page number

        Returns:
            Search results with messages and pagination info
        """
        params = {
            "query": query,
            "sort": sort,
            "sort_dir": sort_dir,
            "count": min(count, 100),
            "page": page,
        }

        data = await self._request("GET", "search.messages", params=params)

        messages_data = data.get("messages", {})
        matches = messages_data.get("matches", [])

        messages: List[SlackMessage] = []
        for match in matches:
            messages.append(self._parse_message(match))

        paging = messages_data.get("paging", {})
        return SlackSearchResult(
            messages=messages,
            total=paging.get("total", len(messages)),
            page=paging.get("page", 1),
            pages=paging.get("pages", 1),
        )

    # =========================================================================
    # Users API
    # =========================================================================

    async def get_user_info(self, user_id: str) -> SlackUser:
        """Get info about a user.

        Results are cached in memory.

        Args:
            user_id: User ID

        Returns:
            User information
        """
        if user_id in self._users_cache:
            return self._users_cache[user_id]

        data = await self._request("GET", "users.info", params={"user": user_id})

        user_data = data["user"]
        user = SlackUser(
            id=user_data["id"],
            name=user_data.get("name", ""),
            real_name=user_data.get("real_name"),
            display_name=user_data.get("profile", {}).get("display_name"),
            is_bot=user_data.get("is_bot", False),
        )

        self._users_cache[user_id] = user
        return user

    async def resolve_user_name(self, user_id: str) -> str:
        """Resolve a user ID to a display name.

        Args:
            user_id: User ID

        Returns:
            Best display name for the user
        """
        try:
            user = await self.get_user_info(user_id)
            return user.display
        except SlackAPIError:
            return user_id

    # =========================================================================
    # Auth API
    # =========================================================================

    async def test_auth(self) -> Dict[str, Any]:
        """Test authentication and get workspace info.

        Returns:
            Auth test response with user/team info
        """
        return await self._request("GET", "auth.test")

    # =========================================================================
    # Helpers
    # =========================================================================

    def _parse_message(self, data: Dict[str, Any]) -> SlackMessage:
        """Parse a message from API response."""
        return SlackMessage(
            ts=data["ts"],
            text=data.get("text", ""),
            user=data.get("user"),
            thread_ts=data.get("thread_ts"),
            reply_count=data.get("reply_count"),
            reactions=data.get("reactions", []),
            attachments=data.get("attachments", []),
            blocks=data.get("blocks", []),
        )

    def get_last_read(self, channel: str) -> Optional[str]:
        """Get cached last_read timestamp for a channel."""
        return self._last_read_cache.get(channel)

    def set_last_read(self, channel: str, ts: str) -> None:
        """Update cached last_read timestamp."""
        self._last_read_cache[channel] = ts
