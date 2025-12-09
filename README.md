# Slack Stealth MCP

An MCP (Model Context Protocol) server that enables Claude to interact with Slack on your behalf—**no admin approval required**.

## Why "Stealth"?

Traditional Slack integrations require creating a Slack App and getting workspace admin approval. This creates friction for personal productivity tools—you shouldn't need IT approval just to let Claude help you manage your own messages.

**Slack Stealth MCP takes a different approach:** it uses your existing browser session tokens to access Slack as *you*, with your existing permissions. No app installation, no OAuth flows, no admin approval. Just log in to Slack in a browser window and the tokens are captured automatically.

This means:
- **Works on any workspace** you can log into—even heavily restricted enterprise workspaces
- **No permissions to request**—you already have access to everything you can see in Slack
- **No app review process**—start using it immediately
- **Your credentials stay local**—tokens are stored on your machine, never sent to third parties

## Features

- **Zero-Friction Auth**: Just ask Claude about Slack—if you're not logged in, a browser opens automatically
- **No Admin Approval**: Uses your existing session, not a Slack App
- **Stealth Reading**: Fetch messages without marking them as read
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

### 2. Add to Claude Desktop

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

### 3. Use with Claude

Ask Claude things like:
- "What's new in Slack?"
- "Search for messages about the Q4 roadmap"
- "Reply to John's last message saying I'll review it tomorrow"
- "Show me unread DMs"

**First time?** Claude will detect no workspace is configured and automatically open a browser for you to log in. Just complete the Slack login (supports SSO, 2FA, etc.) and Claude will pick up where you left off.

## Available Tools

| Tool | Description |
|------|-------------|
| `slack_auth_status` | Check authentication status for workspaces |
| `slack_auth_login` | Authenticate a new workspace (opens browser) |
| `slack_get_unread` | Get unread messages across ALL workspaces—perfect for "What's new?" |
| `slack_reply` | Send messages to channels, DMs, or threads |
| `slack_react` | Add/remove emoji reactions to acknowledge messages without replying |
| `slack_search` | Search with full Slack syntax (in:, from:, has:, dates) |
| `slack_get_context` | Get messages from a conversation for context |
| `slack_list_conversations` | List all available channels and DMs |
| `slack_mark_read` | Explicitly mark conversations as read |

## Read Receipt Behavior

As a bonus, the Slack APIs used by this tool don't trigger read receipts or typing indicators:

| Operation | Marks as Read? | Shows Typing? |
|-----------|---------------|---------------|
| Read messages | No | No |
| Search | No | No |
| Get context | No | No |
| Send message | No | No |
| Add reaction | No | No |
| Mark as read | Yes (explicit) | No |

## Manual Authentication

You can also authenticate from the command line:

```bash
# Authenticate (opens browser)
slack-stealth-auth

# Add additional workspaces
slack-stealth-auth --workspace work
slack-stealth-auth --workspace personal
```

Config is saved to `~/.config/slack-stealth-mcp/config.json`.

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

## How Authentication Works

When you log into Slack in your browser, Slack stores two tokens:
- **xoxc token**: A session token stored in localStorage
- **xoxd cookie**: A session cookie that authenticates the token

These are the same credentials your browser uses when you access Slack. The `slack-stealth-auth` command opens a browser, lets you log in normally, then extracts these tokens and saves them locally.

This approach:
- Supports any login method (SSO, 2FA, SAML, etc.)
- Requires no Slack App creation or approval
- Gives you exactly the same access you have in the browser
- Keeps credentials local to your machine

## Important Notes

- **Session Tokens**: This uses Slack session tokens (xoxc/xoxd) rather than official Bot or User OAuth tokens. Slack doesn't officially support this, but it's the same mechanism the web client uses.
- **Token Lifetime**: Tokens typically last several months to a year. Re-run `slack-stealth-auth` if you get authentication errors.
- **Rate Limits**: Slack has rate limits. The server handles these automatically with exponential backoff.
- **Personal Use**: This is intended for personal productivity with your own Slack account—you're accessing Slack as yourself, with your own permissions.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest
```

## License

MIT
