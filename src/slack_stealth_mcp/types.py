"""Pydantic models for Slack API responses and internal data structures."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SlackUser(BaseModel):
    """Represents a Slack user."""

    id: str
    name: str
    real_name: Optional[str] = None
    display_name: Optional[str] = None
    is_bot: bool = False

    @property
    def display(self) -> str:
        """Best display name for the user."""
        return self.display_name or self.real_name or self.name


class SlackMessage(BaseModel):
    """Represents a Slack message."""

    ts: str
    text: str
    user: Optional[str] = None
    user_name: Optional[str] = None  # Resolved user name
    thread_ts: Optional[str] = None
    reply_count: Optional[int] = None
    reactions: List[Dict[str, Any]] = Field(default_factory=list)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    blocks: List[Dict[str, Any]] = Field(default_factory=list)

    @property
    def is_thread_parent(self) -> bool:
        """Check if this message is a thread parent."""
        return self.thread_ts == self.ts and self.reply_count is not None

    @property
    def is_thread_reply(self) -> bool:
        """Check if this message is a thread reply."""
        return self.thread_ts is not None and self.thread_ts != self.ts

    @property
    def timestamp(self) -> datetime:
        """Convert Slack ts to datetime."""
        return datetime.fromtimestamp(float(self.ts.split(".")[0]))


class SlackConversation(BaseModel):
    """Represents a Slack conversation (channel, DM, or group)."""

    id: str
    name: Optional[str] = None
    is_channel: bool = False
    is_group: bool = False
    is_im: bool = False
    is_mpim: bool = False
    is_private: bool = False
    is_archived: bool = False
    is_member: bool = True
    user: Optional[str] = None  # For DMs, the other user's ID
    last_read: Optional[str] = None
    unread_count: Optional[int] = None
    unread_count_display: Optional[int] = None

    @property
    def display_name(self) -> str:
        """Best display name for the conversation."""
        if self.name:
            return self.name
        if self.is_im and self.user:
            return f"DM:{self.user}"
        return self.id

    @property
    def conversation_type(self) -> str:
        """Human-readable conversation type."""
        if self.is_im:
            return "dm"
        if self.is_mpim:
            return "group_dm"
        if self.is_private:
            return "private_channel"
        return "channel"


class SlackSearchResult(BaseModel):
    """Represents a search result."""

    messages: List[SlackMessage] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    pages: int = 1


class UnreadSummary(BaseModel):
    """Summary of unread messages across workspace."""

    unread_dms: List[Dict[str, Any]] = Field(default_factory=list)
    unread_channels: List[Dict[str, Any]] = Field(default_factory=list)
    mentions: List[Dict[str, Any]] = Field(default_factory=list)
    total_unread_dms: int = 0
    total_unread_channels: int = 0
    total_mentions: int = 0

    @property
    def summary(self) -> str:
        """Human-readable summary."""
        parts = []
        if self.total_unread_dms:
            parts.append(f"{self.total_unread_dms} unread DM(s)")
        if self.total_unread_channels:
            parts.append(f"{self.total_unread_channels} channel(s) with unread messages")
        if self.total_mentions:
            parts.append(f"{self.total_mentions} mention(s)")
        return ", ".join(parts) if parts else "No unread messages"


class WorkspaceConfig(BaseModel):
    """Configuration for a single workspace."""

    xoxc_token: str
    xoxd_cookie: str
    name: Optional[str] = None


class Config(BaseModel):
    """Full application configuration."""

    workspaces: Dict[str, WorkspaceConfig] = Field(default_factory=dict)
    default_workspace: Optional[str] = None
