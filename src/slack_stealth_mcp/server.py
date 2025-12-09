"""MCP Server for Slack Stealth - main entry point."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .config import load_config
from .tools import (
    auth_login,
    auth_status,
    get_context,
    get_unread,
    list_conversations,
    mark_read,
    react,
    reply,
    search,
)
from .workspace import WorkspaceManager

# Initialize MCP server
server = Server("slack-stealth-mcp")

# Global workspace manager (initialized on startup)
_manager: WorkspaceManager | None = None


def get_manager() -> WorkspaceManager:
    """Get the workspace manager, raising if not initialized."""
    if _manager is None:
        raise RuntimeError("Workspace manager not initialized")
    return _manager


# =============================================================================
# Tool Definitions
# =============================================================================

TOOLS = [
    Tool(
        name="slack_auth_status",
        description="""Check Slack authentication status for configured workspaces.

Returns the authentication state for each workspace:
- Whether tokens are configured
- Whether tokens are valid (tests against Slack API)
- User and team info for valid connections

IMPORTANT: If the result shows needs_auth=true or no workspaces are configured,
immediately call slack_auth_login to initiate authentication - do not ask the
user for permission first. The auth flow opens a browser where they can log in.""",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Specific workspace to check (checks all if not specified)",
                },
                "test_connection": {
                    "type": "boolean",
                    "description": "Actually test tokens against Slack API (default: true)",
                    "default": True,
                },
            },
        },
    ),
    Tool(
        name="slack_auth_login",
        description="""Initiate Slack authentication flow for a new or expired workspace.

This opens a browser window where the user can log into Slack. The tool BLOCKS
until authentication completes (up to 10 minutes), then automatically reloads
the configuration.

Call this automatically when slack_auth_status shows no workspaces or needs_auth=true.
Do not ask for user permission - just call this tool. The browser will appear,
the user will log in, and when complete this tool returns with the new workspace info.

After this returns successfully, proceed with the original Slack request.""",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Name for the workspace (optional - uses Slack team name if not specified)",
                },
                "set_default": {
                    "type": "boolean",
                    "description": "Set this as the default workspace (default: true)",
                    "default": True,
                },
            },
        },
    ),
    Tool(
        name="slack_get_unread",
        description="""Get all unread messages and mentions across your Slack workspace.

This is the "What's new?" tool - perfect for catching up on activity.
Returns unread DMs, channel messages with new activity, and recent mentions.

IMPORTANT: This does NOT mark any messages as read. Your read state is preserved.

If this fails due to missing authentication, call slack_auth_status then
slack_auth_login to set up authentication automatically.""",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Workspace name (uses default if not specified)",
                },
                "include_channels": {
                    "type": "boolean",
                    "description": "Include unread channel messages (default: true)",
                    "default": True,
                },
                "include_dms": {
                    "type": "boolean",
                    "description": "Include unread DMs (default: true)",
                    "default": True,
                },
                "include_mentions": {
                    "type": "boolean",
                    "description": "Include recent mentions (default: true)",
                    "default": True,
                },
            },
        },
    ),
    Tool(
        name="slack_reply",
        description="""Send a message or reply in Slack.

Can send to:
- Channels (use channel ID like C01234567)
- DMs (use user ID like U01234567 - will open DM if needed)
- Threads (provide thread_ts to reply in thread)

Supports Slack mrkdwn formatting:
- *bold*, _italic_, ~strikethrough~
- <@U01234567> for user mentions
- <#C01234567> for channel links

IMPORTANT: This does NOT mark the channel as read.""",
        inputSchema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Channel ID (C...), User ID (U...), or DM ID (D...)",
                },
                "message": {
                    "type": "string",
                    "description": "Message text (supports Slack mrkdwn)",
                },
                "thread_ts": {
                    "type": "string",
                    "description": "Parent message timestamp for thread reply",
                },
                "broadcast": {
                    "type": "boolean",
                    "description": "Also post thread reply to channel (default: false)",
                    "default": False,
                },
                "workspace": {
                    "type": "string",
                    "description": "Workspace name (uses default if not specified)",
                },
            },
            "required": ["target", "message"],
        },
    ),
    Tool(
        name="slack_search",
        description="""Search messages across your Slack workspace.

Supports powerful search modifiers:
- in_channel: Search within a specific channel
- from_user: Messages from a specific user
- after_date/before_date: Date filtering (YYYY-MM-DD)
- has_link: Messages containing links
- has_reaction: Messages with reactions
- is_thread: Only thread messages

This does NOT mark messages as read.""",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms",
                },
                "in_channel": {
                    "type": "string",
                    "description": "Channel name or ID to search in",
                },
                "from_user": {
                    "type": "string",
                    "description": "User ID or @name to filter by sender",
                },
                "after_date": {
                    "type": "string",
                    "description": "Only messages after this date (YYYY-MM-DD)",
                },
                "before_date": {
                    "type": "string",
                    "description": "Only messages before this date (YYYY-MM-DD)",
                },
                "has_link": {
                    "type": "boolean",
                    "description": "Only messages with links",
                },
                "has_reaction": {
                    "type": "boolean",
                    "description": "Only messages with reactions",
                },
                "is_thread": {
                    "type": "boolean",
                    "description": "Only messages in threads",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 20, max: 100)",
                    "default": 20,
                },
                "workspace": {
                    "type": "string",
                    "description": "Workspace name (uses default if not specified)",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="slack_get_context",
        description="""Get messages from a conversation for context.

Fetches recent messages from a channel or thread. If message_ts points
to a thread parent, returns the full thread. Otherwise returns recent
channel messages.

IMPORTANT: This does NOT mark messages as read.""",
        inputSchema={
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "Channel ID (C..., D..., or G...)",
                },
                "message_ts": {
                    "type": "string",
                    "description": "Optional message timestamp to get context around",
                },
                "context_size": {
                    "type": "integer",
                    "description": "Number of messages to fetch (default: 10)",
                    "default": 10,
                },
                "workspace": {
                    "type": "string",
                    "description": "Workspace name (uses default if not specified)",
                },
            },
            "required": ["channel"],
        },
    ),
    Tool(
        name="slack_list_conversations",
        description="""List all available conversations in the workspace.

Returns channels, private channels, DMs, and group DMs that you have access to.
Includes unread counts for DMs.""",
        inputSchema={
            "type": "object",
            "properties": {
                "types": {
                    "type": "string",
                    "description": "Filter: 'all', 'channels', or 'dms' (default: all)",
                    "enum": ["all", "channels", "dms"],
                    "default": "all",
                },
                "workspace": {
                    "type": "string",
                    "description": "Workspace name (uses default if not specified)",
                },
            },
        },
    ),
    Tool(
        name="slack_mark_read",
        description="""Mark a conversation as read.

This is the ONLY tool that affects your read state. Use it when you
deliberately want to mark messages as read.

If no timestamp is provided, marks all messages in the conversation as read.""",
        inputSchema={
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "Channel ID to mark as read",
                },
                "timestamp": {
                    "type": "string",
                    "description": "Mark as read up to this message (optional)",
                },
                "workspace": {
                    "type": "string",
                    "description": "Workspace name (uses default if not specified)",
                },
            },
            "required": ["channel"],
        },
    ),
    Tool(
        name="slack_react",
        description="""Add or remove an emoji reaction on a message.

Perfect for acknowledging messages without sending a full reply.
Common reactions: thumbsup, pray (thanks), eyes (noted), heart, fire, 100, joy (funny),
upside_down_face, raised_hands (celebration), melting_face, partyparrot (custom).

Use emoji names without colons (e.g., "thumbsup" not ":thumbsup:").
Supports skin tone modifiers (e.g., "thumbsup::skin-tone-3").

IMPORTANT: This does NOT mark messages as read.""",
        inputSchema={
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "Channel ID containing the message",
                },
                "message_ts": {
                    "type": "string",
                    "description": "Timestamp of the message to react to",
                },
                "emoji": {
                    "type": "string",
                    "description": "Emoji name without colons (e.g., 'thumbsup', 'heart', 'eyes')",
                },
                "remove": {
                    "type": "boolean",
                    "description": "Remove reaction instead of adding (default: false)",
                    "default": False,
                },
                "workspace": {
                    "type": "string",
                    "description": "Workspace name (uses default if not specified)",
                },
            },
            "required": ["channel", "message_ts", "emoji"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Return list of available tools."""
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    manager = get_manager()

    try:
        if name == "slack_auth_status":
            result = await auth_status(
                manager=manager,
                workspace=arguments.get("workspace"),
                test_connection=arguments.get("test_connection", True),
            )

        elif name == "slack_auth_login":
            result = await auth_login(
                manager=manager,
                workspace=arguments.get("workspace"),
                set_default=arguments.get("set_default", True),
            )

        elif name == "slack_get_unread":
            result = await get_unread(
                manager=manager,
                workspace=arguments.get("workspace"),
                include_channels=arguments.get("include_channels", True),
                include_dms=arguments.get("include_dms", True),
                include_mentions=arguments.get("include_mentions", True),
            )

        elif name == "slack_reply":
            result = await reply(
                manager=manager,
                target=arguments["target"],
                message=arguments["message"],
                thread_ts=arguments.get("thread_ts"),
                broadcast=arguments.get("broadcast", False),
                workspace=arguments.get("workspace"),
            )

        elif name == "slack_search":
            result = await search(
                manager=manager,
                query=arguments["query"],
                in_channel=arguments.get("in_channel"),
                from_user=arguments.get("from_user"),
                after_date=arguments.get("after_date"),
                before_date=arguments.get("before_date"),
                has_link=arguments.get("has_link", False),
                has_reaction=arguments.get("has_reaction", False),
                is_thread=arguments.get("is_thread", False),
                limit=arguments.get("limit", 20),
                workspace=arguments.get("workspace"),
            )

        elif name == "slack_get_context":
            result = await get_context(
                manager=manager,
                channel=arguments["channel"],
                message_ts=arguments.get("message_ts"),
                context_size=arguments.get("context_size", 10),
                workspace=arguments.get("workspace"),
            )

        elif name == "slack_list_conversations":
            result = await list_conversations(
                manager=manager,
                workspace=arguments.get("workspace"),
                types=arguments.get("types", "all"),
            )

        elif name == "slack_mark_read":
            result = await mark_read(
                manager=manager,
                channel=arguments["channel"],
                timestamp=arguments.get("timestamp"),
                workspace=arguments.get("workspace"),
            )

        elif name == "slack_react":
            result = await react(
                manager=manager,
                channel=arguments["channel"],
                message_ts=arguments["message_ts"],
                emoji=arguments["emoji"],
                remove=arguments.get("remove", False),
                workspace=arguments.get("workspace"),
            )

        else:
            result = {"error": f"Unknown tool: {name}"}

    except Exception as e:
        result = {"error": str(e)}

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def run_server() -> None:
    """Run the MCP server."""
    global _manager

    # Load configuration (returns empty config if none exists)
    config = load_config()

    # Initialize workspace manager
    _manager = WorkspaceManager(config)

    # Log workspace status
    if _manager.workspace_names:
        print(f"Configured workspaces: {', '.join(_manager.workspace_names)}")
        print(f"Default workspace: {_manager.default_workspace}")
    else:
        print("No workspaces configured. Use slack_auth_login to authenticate.")

    # Run the server
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )

    # Cleanup
    await _manager.close_all()


def main() -> None:
    """Entry point."""
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
