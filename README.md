# Slack Stealth MCP

An MCP (Model Context Protocol) server that enables Claude to interact with Slack on your behalf using session tokens. Read messages, search, and reply—all without triggering read receipts or typing indicators.

## Features

- **OAuth-like Authentication**: Just log in to Slack in a browser window—tokens are captured automatically
- **Stealth Reading**: Fetch messages without marking them as read
- **No Typing Indicators**: Slack's API doesn't support typing indicators, so you can't accidentally trigger them
- **Multi-Workspace**: Support multiple Slack workspaces
- **Comprehensive Search**: Full Slack search syntax support
- **Thread Support**: Read and reply to threads

## Quick Start

### 1. Install

```bash
# Clone the repository
git clone https://github.com/forayconsulting/slack-stealth-mcp.git
cd slack-stealth-mcp

# Create virtual environment with Python 3.10+
python3.12 -m venv .venv
source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows

# Install
pip install -e .

# Install browser for authentication
playwright install chromium
```

### 2. Authenticate

Run the authentication command—a browser window will open:

```bash
slack-stealth-auth
```

1. Log in to Slack normally (supports SSO, 2FA, etc.)
2. Once logged in, tokens are automatically captured
3. Config is saved to `~/.config/slack-stealth-mcp/config.json`

**Add more workspaces:**
```bash
slack-stealth-auth --workspace work
slack-stealth-auth --workspace personal
```

### 3. Add to Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "slack": {
      "command": "/path/to/slack-stealth-mcp/.venv/bin/slack-stealth-mcp"
    }
  }
}
```

### 4. Use with Claude

Ask Claude things like:
- "What's new in Slack?"
- "Search for messages about the Q4 roadmap"
- "Reply to John's last message saying I'll review it tomorrow"
- "Show me unread DMs"

## Available Tools

| Tool | Description |
|------|-------------|
| `slack_get_unread` | Get all unread messages and mentions—perfect for "What's new?" |
| `slack_reply` | Send messages to channels, DMs, or threads |
| `slack_search` | Search with full Slack syntax (in:, from:, has:, dates) |
| `slack_get_context` | Get messages from a conversation for context |
| `slack_list_conversations` | List all available channels and DMs |
| `slack_mark_read` | Explicitly mark conversations as read |

## Stealth Behavior

| Operation | Marks as Read? | Shows Typing? |
|-----------|---------------|---------------|
| Read messages | No | No |
| Search | No | No |
| Get context | No | No |
| Send message | No | No |
| Mark as read | Yes (explicit) | No |

## Manual Token Configuration

If you prefer to manually configure tokens:

### Option 1: Config File (Multi-Workspace)

Create `~/.config/slack-stealth-mcp/config.json`:

```json
{
  "workspaces": {
    "work": {
      "xoxc_token": "xoxc-...",
      "xoxd_cookie": "xoxd-..."
    }
  },
  "default_workspace": "work"
}
```

### Option 2: Environment Variables

```bash
export SLACK_XOXC_TOKEN="xoxc-..."
export SLACK_XOXD_COOKIE="xoxd-..."
```

### How to Get Tokens Manually

1. Open Slack in your web browser and log in
2. Open Developer Tools (F12 or Cmd+Option+I)
3. Go to **Application** tab → **Local Storage** → find key containing `localConfig`
4. Find the `token` value starting with `xoxc-`
5. Go to **Application** tab → **Cookies** → `slack.com`
6. Find the cookie named `d` — the value starting with `xoxd-`

## Important Notes

- **Unofficial API Usage**: This uses Slack session tokens (xoxc/xoxd) which are not officially supported by Slack. While Slack doesn't actively block this, they don't guarantee stability.
- **Token Lifetime**: Tokens last approximately 1 year. Re-run `slack-stealth-auth` if you get authentication errors.
- **Rate Limits**: Slack has rate limits. The server handles these automatically with exponential backoff.
- **Personal Use**: This is intended for personal productivity with your own Slack account.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest
```

## License

MIT
