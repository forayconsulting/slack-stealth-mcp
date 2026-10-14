"""Async Slack API client using xoxc/xoxd session tokens."""

import asyncio
import json
import re
import time
import uuid
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


def normalize_name(name: str) -> str:
    """Normalize a name for matching: lowercase, alphanumerics only."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


class RateLimiter:
    """Token bucket rate limiter with exponential backoff."""

    def __init__(self, requests_per_second: float = 2.0):
        """Initialize rate limiter.

        Args:
            requests_per_second: Maximum requests per second (default 2/sec, backs off on 429)
        """
        self._min_interval = 1.0 / requests_per_second
        self._last_request = 0.0
        self._lock = asyncio.Lock()
        self._backoff = 1.0

    async def acquire(self) -> None:
        """Wait until a request can be made."""
        async with self._lock:
            now = time.monotonic()
            wait_time = self._last_request + (self._min_interval * self._backoff) - now
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
        self._user_name_index: Dict[str, SlackUser] = {}  # normalized name -> user
        self._users_list_time: float = 0
        self._conversations_cache: Dict[str, SlackConversation] = {}
        self._all_convs_cache: Optional[List[SlackConversation]] = None
        self._all_convs_time: float = 0
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
        max_pages: int = 3,
        page_pause: float = 0.0,
    ) -> List[SlackConversation]:
        """List conversations the user has access to.

        Args:
            types: Comma-separated conversation types
            exclude_archived: Whether to exclude archived channels
            limit: Max results per page
            max_pages: Maximum number of pages to fetch (default 3, set to 0 for unlimited)
            page_pause: Seconds to wait between pages. conversations.list is a
                Tier-2 method (~20 req/min); pacing full pagination avoids 429
                backoffs that are far slower than the pause itself.

        Returns:
            List of conversations
        """
        conversations: List[SlackConversation] = []
        cursor: Optional[str] = None
        pages_fetched = 0

        while True:
            params: Dict[str, Any] = {
                "types": types,
                "exclude_archived": str(exclude_archived).lower(),
                "limit": limit,
            }
            if cursor:
                params["cursor"] = cursor

            data = await self._request("GET", "conversations.list", params=params)
            pages_fetched += 1

            for ch in data.get("channels", []):
                conv = self._parse_conversation(ch)
                conversations.append(conv)
                self._conversations_cache[conv.id] = conv
                if conv.last_read:
                    self._last_read_cache[conv.id] = conv.last_read

            cursor = data.get("response_metadata", {}).get("next_cursor")
            # Stop if no more pages OR we've hit max_pages limit
            if not cursor or (max_pages > 0 and pages_fetched >= max_pages):
                break

            if page_pause:
                await asyncio.sleep(page_pause)

        self._cache_time = time.monotonic()
        return conversations

    async def list_member_conversations(
        self,
        types: str = "public_channel,private_channel,mpim,im",
        limit: int = 200,
        max_pages: int = 0,
    ) -> List[SlackConversation]:
        """List only conversations the user is a member of (users.conversations).

        Far smaller and faster than conversations.list on large workspaces -
        this is the user's own membership set, not every channel in the org.

        Args:
            types: Comma-separated conversation types
            limit: Max results per page
            max_pages: Maximum pages (0 = unlimited)

        Returns:
            The user's member conversations
        """
        conversations: List[SlackConversation] = []
        cursor: Optional[str] = None
        pages_fetched = 0

        while True:
            params: Dict[str, Any] = {
                "types": types,
                "exclude_archived": "true",
                "limit": limit,
            }
            if cursor:
                params["cursor"] = cursor

            data = await self._request("GET", "users.conversations", params=params)
            pages_fetched += 1

            for ch in data.get("channels", []):
                conv = self._parse_conversation(ch)
                conversations.append(conv)
                self._conversations_cache[conv.id] = conv
                if conv.last_read:
                    self._last_read_cache[conv.id] = conv.last_read

            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor or (max_pages > 0 and pages_fetched >= max_pages):
                break

        return conversations

    async def search_channels(
        self, query: str, count: int = 25
    ) -> List[SlackConversation]:
        """Search channels by name via Slack's internal quick-switcher endpoint.

        This is what Slack's own Cmd+K search uses: the server matches the query
        and returns a handful of results (including channels you have not joined)
        without enumerating the whole workspace.

        Read-only. Raises SlackAPIError if the endpoint is unavailable so callers
        can fall back to a full listing.

        Args:
            query: Channel name (or fragment) to match
            count: Max results to request

        Returns:
            Matching channels (server-ranked)
        """
        data = await self._request(
            "POST",
            "search.modules.channels",
            data={
                "token": self._config.xoxc_token,
                "module": "channels",
                "query": query,
                "count": count,
            },
        )

        results: List[SlackConversation] = []
        for item in data.get("items", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            conv = self._parse_conversation(item)
            results.append(conv)
            self._conversations_cache[conv.id] = conv
        return results

    async def ensure_all_conversations(
        self, ttl: float = 1800.0
    ) -> List[SlackConversation]:
        """Return every accessible conversation, fetched once and cached.

        conversations.list is heavily throttled, so name-based tools
        (find_conversation, list_conversations) share this cached full listing
        instead of re-paginating on every call. Only the first call per TTL
        window pays the cost; the rest are instant.

        Args:
            ttl: Seconds before the cached listing is considered stale

        Returns:
            All conversations across every type (paced pagination)
        """
        if (
            self._all_convs_cache is not None
            and time.monotonic() - self._all_convs_time < ttl
        ):
            return self._all_convs_cache

        convs = await self.list_conversations(
            types="public_channel,private_channel,mpim,im",
            max_pages=0,
            page_pause=1.0,
        )
        self._all_convs_cache = convs
        self._all_convs_time = time.monotonic()
        return convs

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

        conv = self._parse_conversation(data["channel"])
        self._conversations_cache[conv.id] = conv
        if conv.last_read:
            self._last_read_cache[conv.id] = conv.last_read
        return conv

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
            # Auto-link plain @handle/#channel (matches Slack handles, not
            # display names); pre-formatted <@U...> tokens work regardless
            "link_names": "true",
        }
        if thread_ts:
            payload["thread_ts"] = thread_ts
            if reply_broadcast:
                payload["reply_broadcast"] = "true"

        data = await self._request("POST", "chat.postMessage", data=payload)
        return self._parse_message(data["message"])

    # =========================================================================
    # Drafts API (internal; backs Slack's "Schedule for later")
    # =========================================================================

    async def create_draft(
        self,
        channel: str,
        blocks: List[Dict[str, Any]],
        thread_ts: Optional[str] = None,
        date_scheduled: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a draft, optionally scheduled for sending.

        A draft with date_scheduled set is sent by Slack's servers at that
        time and shows in the user's Scheduled list. The public
        chat.scheduleMessage rejects session tokens, so this is the only
        scheduling path available to us.

        Args:
            channel: Channel ID (C/D/G - not a user ID)
            blocks: rich_text blocks (see rich_text.mrkdwn_to_rich_text)
            thread_ts: Parent message ts for a thread reply
            date_scheduled: Unix seconds to send at

        Returns:
            The created draft object
        """
        destination: Dict[str, Any] = {"channel_id": channel}
        if thread_ts:
            destination["thread_ts"] = thread_ts
        payload: Dict[str, Any] = {
            "blocks": json.dumps(blocks),
            "destinations": json.dumps([destination]),
            "client_msg_id": str(uuid.uuid4()),
            "file_ids": "[]",
            "is_from_composer": "true",
        }
        if date_scheduled:
            payload["date_scheduled"] = str(int(date_scheduled))

        data = await self._request("POST", "drafts.create", data=payload)
        return data["draft"]

    async def list_drafts(self, limit: int = 100) -> List[Dict[str, Any]]:
        """List the user's active drafts, including scheduled ones.

        drafts.list has no cursor; active drafts rarely exceed one page.
        """
        data = await self._request(
            "POST", "drafts.list", data={"is_active": "true", "limit": str(limit)}
        )
        return data.get("drafts", [])

    async def delete_draft(self, draft_id: str, last_updated_ts: str) -> None:
        """Delete a draft (cancels it if scheduled).

        Slack rejects the stored 6-decimal last_updated_ts as a conflict; it
        wants it padded to 7 decimals. If that still conflicts, retry with
        the current time, which some workspaces require instead.
        """
        integer, _, frac = last_updated_ts.partition(".")
        padded = f"{integer}.{frac:<07}"
        try:
            await self._request(
                "POST",
                "drafts.delete",
                data={"draft_id": draft_id, "client_last_updated_ts": padded},
            )
        except SlackAPIError as e:
            if e.error != "draft_has_conflict":
                raise
            await self._request(
                "POST",
                "drafts.delete",
                data={"draft_id": draft_id, "client_last_updated_ts": f"{time.time():.7f}"},
            )

    # =========================================================================
    # Reactions API
    # =========================================================================

    async def add_reaction(self, channel: str, timestamp: str, name: str) -> None:
        """Add an emoji reaction to a message.

        Args:
            channel: Channel ID containing the message
            timestamp: Message timestamp to react to
            name: Emoji name without colons (e.g., "thumbsup", not ":thumbsup:")
        """
        await self._request(
            "POST",
            "reactions.add",
            data={"channel": channel, "timestamp": timestamp, "name": name},
        )

    async def remove_reaction(self, channel: str, timestamp: str, name: str) -> None:
        """Remove an emoji reaction from a message.

        Args:
            channel: Channel ID containing the message
            timestamp: Message timestamp to remove reaction from
            name: Emoji name without colons (e.g., "thumbsup", not ":thumbsup:")
        """
        await self._request(
            "POST",
            "reactions.remove",
            data={"channel": channel, "timestamp": timestamp, "name": name},
        )

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
        user = self._parse_user(user_data)

        self._cache_user(user)
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

    async def prefetch_users(self, user_ids: List[str], max_fetch: int = 20) -> None:
        """Prefetch and cache info for multiple users.

        This is useful before displaying messages to ensure user names are available.
        Silently ignores errors - cache misses will fall back to user IDs.

        Args:
            user_ids: List of user IDs to prefetch
            max_fetch: Maximum number of users to fetch (to respect rate limits)
        """
        # Filter out already-cached users and None values
        uncached = [uid for uid in user_ids if uid and uid not in self._users_cache]
        if not uncached:
            return

        # Limit to avoid rate limit issues
        for user_id in uncached[:max_fetch]:
            try:
                await self.get_user_info(user_id)
            except Exception:
                pass  # Silently ignore - will show truncated ID as fallback

    async def list_users(
        self, limit: int = 200, max_pages: int = 0, page_pause: float = 1.0
    ) -> List[SlackUser]:
        """Fetch the workspace user directory via users.list.

        Populates the ID cache and the name index used for name -> user lookup.

        Args:
            limit: Max results per page
            max_pages: Maximum pages to fetch (0 = unlimited)
            page_pause: Seconds between pages. users.list is Tier-2 (~20 req/min);
                pacing avoids 429 backoffs that cost far more than the pause.

        Returns:
            List of active (non-deleted) users
        """
        users: List[SlackUser] = []
        cursor: Optional[str] = None
        pages_fetched = 0

        while True:
            params: Dict[str, Any] = {"limit": limit}
            if cursor:
                params["cursor"] = cursor

            data = await self._request("GET", "users.list", params=params)
            pages_fetched += 1

            for member in data.get("members", []):
                if member.get("deleted"):
                    continue
                user = self._parse_user(member)
                users.append(user)
                self._cache_user(user)

            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor or (max_pages > 0 and pages_fetched >= max_pages):
                break

            if page_pause:
                await asyncio.sleep(page_pause)

        self._users_list_time = time.monotonic()
        return users

    async def ensure_user_directory(self, ttl: float = 3600.0) -> bool:
        """Make sure the user directory has been fetched recently.

        Args:
            ttl: Seconds before the directory is considered stale

        Returns:
            True if the directory is available, False if the fetch failed
        """
        if self._users_list_time and time.monotonic() - self._users_list_time < ttl:
            return True
        try:
            await self.list_users()
            return True
        except Exception:
            # Directory is best-effort; ID-based lookups still work
            return False

    def get_user_by_name(self, name: str) -> Optional[SlackUser]:
        """Look up a user by handle, real name, or display name (normalized)."""
        return self._user_name_index.get(normalize_name(name))

    def directory_users(self) -> List[SlackUser]:
        """All users currently known to the cache."""
        return list(self._users_cache.values())

    # =========================================================================
    # Client API (undocumented, used by Slack's own web client)
    # =========================================================================

    async def get_counts(self) -> Dict[str, Any]:
        """Fetch authoritative unread badge state via client.counts.

        This is the endpoint Slack's own client uses to render badges. It
        returns has_unreads/mention_count/last_read/latest for every channel,
        DM, and group DM, plus thread badge state, in one request. Works with
        the same xoxc/xoxd session tokens as every other call here.

        NOTE: Read-only - this does NOT mark anything as read.

        Returns:
            Raw client.counts response (treat all fields as optional -
            the endpoint is undocumented and may drift)
        """
        return await self._request(
            "POST",
            "client.counts",
            data={
                # Some internal endpoints prefer the token in the body even
                # though the Authorization header is set
                "token": self._config.xoxc_token,
                "thread_counts_by_channel": "true",
                "org_wide_aware": "true",
                "include_file_channels": "true",
            },
        )

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

    def _parse_user(self, data: Dict[str, Any]) -> SlackUser:
        """Parse a user from API response."""
        return SlackUser(
            id=data["id"],
            name=data.get("name", ""),
            real_name=data.get("real_name"),
            display_name=data.get("profile", {}).get("display_name"),
            is_bot=data.get("is_bot", False),
        )

    def _cache_user(self, user: SlackUser) -> None:
        """Cache a user by ID and index by all known names."""
        self._users_cache[user.id] = user
        for name in (user.name, user.real_name, user.display_name):
            if name:
                key = normalize_name(name)
                if key:
                    self._user_name_index[key] = user

    def _parse_conversation(self, ch: Dict[str, Any]) -> SlackConversation:
        """Parse a conversation from any Slack response (list/info/search)."""
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

    def _parse_message(self, data: Dict[str, Any]) -> SlackMessage:
        """Parse a message from API response."""
        # search.messages returns channel as an object; history omits it;
        # some endpoints return a bare channel ID string
        channel = data.get("channel")
        if isinstance(channel, str):
            channel = {"id": channel}

        return SlackMessage(
            ts=data["ts"],
            text=data.get("text", ""),
            user=data.get("user"),
            thread_ts=data.get("thread_ts"),
            reply_count=data.get("reply_count"),
            channel=channel,
            permalink=data.get("permalink"),
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
