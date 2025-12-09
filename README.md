<img src="https://img.shields.io/badge/MCP-Compatible-blue" alt="MCP Compatible" /> <img src="https://img.shields.io/badge/Claude-Desktop%20%7C%20Mobile-purple" alt="Claude Desktop | Mobile" /> <img src="https://img.shields.io/badge/License-MIT-green" alt="MIT License" />

# Slack Stealth MCP

> **Access Slack from Claude without admin approval, OAuth apps, or read receipts.**

An MCP (Model Context Protocol) server that lets Claude read and respond to your Slack messages using your existing browser session—no Slack App installation required.

<p align="center">
  <img src="https://github.com/user-attachments/assets/placeholder-demo.gif" alt="Demo" width="600" />
</p>

---

## Why "Stealth"?

| Traditional Slack Apps | Slack Stealth MCP |
|------------------------|-------------------|
| Requires workspace admin approval | Works immediately on any workspace |
| Must create and configure OAuth app | Uses your existing browser session |
| Limited to granted OAuth scopes | Access everything you can see in Slack |
| Triggers read receipts & typing indicators | **Silent reading**—no traces left |
| Tokens managed by Slack | Tokens stored locally (or encrypted in cloud) |

**The "stealth" advantage:** Reading messages, searching, and getting context **never marks messages as read** or shows typing indicators. Only explicit actions like `slack_mark_read` affect your read state.

---

## Choose Your Setup

Slack Stealth MCP offers two deployment options depending on your needs:

| | Local MCP (Python) | Remote MCP (Cloudflare) |
|---|---|---|
| **Best for** | Claude Desktop on Mac/PC | Claude Mobile (iOS/Android) |
| **Setup complexity** | Medium (Python + Playwright) | Easy (just connect) |
| **Where tokens live** | Your machine | Encrypted in Cloudflare KV |
| **Authentication** | Local browser popup | Remote browser streaming |
| **Requires** | Python 3.10+, Playwright | Just a browser |

---

<details>
<summary><h2>📱 Remote MCP Setup (Claude Mobile)</h2></summary>

### Perfect for accessing Slack from Claude on your phone or tablet.

The Remote MCP server runs on Cloudflare Workers and handles authentication through a secure browser streaming interface.

### Quick Start

1. **Open Claude Mobile** and go to Settings → Connectors
2. **Add a new MCP server** with this URL:
   ```
   https://slack-stealth-mcp.foray-consulting.workers.dev/mcp
   ```
3. **Authenticate** when prompted—you'll see a Slack login page streamed to your device
4. **Start chatting** with Claude about your Slack messages!

### Authentication Flow

When you first connect, you'll be guided through a secure authentication process:

```
┌─────────────────────────────────────────────────────────────┐
│  Your Phone              Cloudflare Worker       Browser    │
│  ┌──────────┐           ┌──────────────┐      ┌──────────┐ │
│  │  Claude  │◀─────────▶│  MCP Server  │◀────▶│ Chromium │ │
│  │  Mobile  │  OAuth    │  (DO + KV)   │ CDP  │ (Remote) │ │
│  └──────────┘           └──────────────┘      └──────────┘ │
│       │                        │                    │       │
│  1. Connect to MCP        2. Need auth?        3. Stream   │
│  4. See Slack login ◀──── 5. Video frames ◀─── browser    │
│  6. Tap/type to login     7. Forward input ───▶            │
│  8. Complete 2FA          9. Extract tokens                │
│  10. Tokens encrypted ───▶ stored in KV                    │
│  11. Ready to use Slack!                                    │
└─────────────────────────────────────────────────────────────┘
```

### Mobile-Optimized Features

The remote auth UI is designed for mobile with:

- **Touch-optimized controls** - Tap to click, swipe to scroll
- **Floating keyboard button** - Reliable text input on mobile
- **2FA code detection** - Shows Google 2FA codes in a banner so you can switch apps
- **Session persistence** - 3-minute grace period for handling 2FA
- **Reconnect capability** - Resume your session after switching apps
- **QR code handoff** - Stuck on CAPTCHA? Scan to continue on desktop

### Handling 2FA on Mobile

If your Slack login requires Google 2FA:

1. The 2FA number will appear in a **prominent blue banner** at the top
2. Switch to your authenticator app and tap the matching number
3. Come back to Claude—tap **"Reconnect to Browser"**
4. Your session continues right where you left off

Alternatively, tap **"Switch to desktop"** to get a QR code and finish authentication on your computer.

### Security

- Tokens are encrypted with AES-256-GCM before storage
- Each user's tokens are isolated in Cloudflare KV
- OAuth 2.1 with PKCE protects the authentication flow
- Browser sessions are ephemeral and auto-cleanup after use

</details>

---

<details>
<summary><h2>🖥️ Local MCP Setup (Claude Desktop)</h2></summary>

### Perfect for power users who want everything running on their own machine.

### Prerequisites

- Python 3.10 or higher
- Claude Desktop
- A Slack account (any workspace)

### Installation

```bash
# Clone the repository
git clone https://github.com/forayconsulting/slack-stealth-mcp.git
cd slack-stealth-mcp/packages/python

# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install the package
pip install -e .

# Install browser for authentication
playwright install chromium
```

### Configure Claude Desktop

Add to your Claude Desktop config file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "slack": {
      "command": "/absolute/path/to/slack-stealth-mcp/packages/python/.venv/bin/slack-stealth-mcp"
    }
  }
}
```

> **Example (macOS):** If you cloned to `~/Desktop/slack-stealth-mcp`, use:
> ```json
> "command": "/Users/yourname/Desktop/slack-stealth-mcp/packages/python/.venv/bin/slack-stealth-mcp"
> ```

**After editing**, restart Claude Desktop for changes to take effect.

### First-Time Authentication

Just start chatting with Claude about Slack! On first use:

1. Claude detects no workspace is configured
2. A browser window opens automatically
3. Log into Slack normally (SSO, 2FA, SAML all work)
4. Tokens are captured and saved locally
5. Claude picks up right where you left off

### Manual Authentication (Optional)

```bash
# Authenticate via command line
slack-stealth-auth

# Add multiple workspaces
slack-stealth-auth --workspace work
slack-stealth-auth --workspace personal
```

### Configuration Files

Tokens are stored in `~/.config/slack-stealth-mcp/config.json`:

```json
{
  "workspaces": {
    "acme-corp": {
      "xoxc_token": "xoxc-...",
      "xoxd_cookie": "xoxd-...",
      "team_id": "T12345678",
      "team_name": "Acme Corp"
    }
  },
  "default_workspace": "acme-corp"
}
```

### Environment Variables (Alternative)

```bash
export SLACK_XOXC_TOKEN="xoxc-..."
export SLACK_XOXD_COOKIE="xoxd-..."
```

</details>

---

<details>
<summary><h2>🔧 Available Tools</h2></summary>

### Core Tools

| Tool | Description |
|------|-------------|
| `slack_get_unread` | Get unread messages across ALL workspaces—perfect for "What's new in Slack?" |
| `slack_reply` | Send messages to channels, DMs, or threads |
| `slack_react` | Add/remove emoji reactions to messages |
| `slack_search` | Search with full Slack syntax (`in:`, `from:`, `has:`, date ranges) |
| `slack_get_context` | Get conversation history for context |
| `slack_list_conversations` | List all channels and DMs you have access to |
| `slack_mark_read` | Explicitly mark conversations as read |

### Authentication Tools (varies by deployment)

**Local MCP:**
| Tool | Description |
|------|-------------|
| `slack_auth_status` | Check which workspaces are authenticated |
| `slack_auth_login` | Authenticate a new workspace (opens browser) |

**Remote MCP:** Authentication is handled via OAuth flow when you first connect.

### Example Prompts

```
"What's new in Slack?"
→ Uses slack_get_unread to fetch unread messages across all workspaces

"Search for messages about the Q4 roadmap"
→ Uses slack_search with query "Q4 roadmap"

"Reply to Sarah's last message saying I'll review it tomorrow"
→ Uses slack_get_context + slack_reply to thread

"React to that message with a thumbs up"
→ Uses slack_react to add :+1: reaction

"Show me unread DMs"
→ Uses slack_get_unread with filter for DMs

"Mark #general as read"
→ Uses slack_mark_read to update read state
```

</details>

---

<details>
<summary><h2>🔒 Read Receipt Behavior</h2></summary>

One of the key benefits of Slack Stealth MCP is **silent reading**:

| Operation | Marks as Read? | Shows Typing? |
|-----------|:--------------:|:-------------:|
| `slack_get_unread` | No | No |
| `slack_search` | No | No |
| `slack_get_context` | No | No |
| `slack_reply` | No | No |
| `slack_react` | No | No |
| `slack_list_conversations` | No | No |
| `slack_mark_read` | **Yes** | No |

Only the explicit `slack_mark_read` tool affects your read state. Everything else is completely silent—your colleagues won't know Claude is helping you catch up on messages.

</details>

---

<details>
<summary><h2>🏗️ Architecture</h2></summary>

### Repository Structure

```
slack-stealth-mcp/
├── packages/
│   ├── python/                 # Local MCP server
│   │   ├── src/slack_stealth_mcp/
│   │   │   ├── server.py       # MCP server entry point
│   │   │   ├── slack_client.py # Async Slack API client
│   │   │   ├── auth.py         # Playwright-based auth
│   │   │   └── tools/          # MCP tool implementations
│   │   └── pyproject.toml
│   │
│   └── cloudflare/             # Remote MCP server
│       ├── src/
│       │   ├── index.ts        # Worker entry with OAuth
│       │   ├── mcp/server.ts   # Durable Object MCP server
│       │   ├── slack-client.ts # Slack API client
│       │   ├── auth/           # Browser streaming auth
│       │   └── tools/          # MCP tool implementations
│       └── wrangler.toml
│
├── CLAUDE.md                   # AI assistant instructions
└── README.md
```

### How Authentication Works

Both deployments use the same underlying technique:

1. **Browser Session Capture**: When you log into Slack in a browser, Slack stores:
   - `xoxc-*` token in localStorage (session token)
   - `xoxd-*` cookie (authenticates the session)

2. **Token Extraction**: After you complete login (including any SSO, CAPTCHA, or 2FA), the tokens are extracted from the browser context.

3. **Token Storage**:
   - **Local**: Saved to `~/.config/slack-stealth-mcp/config.json`
   - **Remote**: Encrypted with AES-256-GCM, stored in Cloudflare KV

4. **API Access**: The tokens are used to call Slack's web API endpoints—the same ones the Slack web client uses.

### Why This Works

These are **session tokens**, not OAuth tokens. They:
- Provide the same access as the Slack web client
- Support any authentication method your workspace uses
- Don't require any Slack App configuration
- Typically last several months before expiring

</details>

---

<details>
<summary><h2>🛠️ Development</h2></summary>

### Local Development (Python)

```bash
cd packages/python

# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run the MCP server directly
.venv/bin/slack-stealth-mcp
```

### Cloudflare Development

```bash
cd packages/cloudflare

# Install dependencies
npm install

# Run locally
npm run dev

# Type check
npm run type-check

# Deploy
npm run deploy
```

### Self-Hosting the Remote MCP

Want to run your own instance?

1. Fork this repository
2. Create Cloudflare KV namespaces:
   ```bash
   wrangler kv namespace create TOKENS
   wrangler kv namespace create SESSIONS
   wrangler kv namespace create OAUTH_KV
   ```
3. Update `wrangler.toml` with your namespace IDs
4. Enable Browser Rendering in your Cloudflare dashboard
5. Deploy: `npm run deploy`

</details>

---

<details>
<summary><h2>❓ Troubleshooting</h2></summary>

### "Authentication failed" or "Invalid token"

Tokens expire after several months. Re-authenticate:
- **Local**: Run `slack-stealth-auth`
- **Remote**: Disconnect and reconnect the MCP server in Claude

### "Rate limited"

Slack has API rate limits. The server handles this automatically with exponential backoff. If you're seeing frequent rate limits, slow down your requests.

### Mobile auth: CAPTCHA is too hard

Tap **"Switch to desktop"** to get a QR code. Scan it with your computer's camera app to open the same auth session on desktop where CAPTCHAs are easier.

### Mobile auth: 2FA issues

The browser session stays alive for 3 minutes while you handle 2FA. If you see the 2FA code banner, note the number, switch apps, complete 2FA, then come back and tap "Reconnect".

### Can't find token in browser

Make sure you're fully logged into Slack (not just at the login page). The token only appears after successful authentication.

### Multi-workspace issues

Each workspace needs separate authentication. Use the workspace name when authenticating:
```bash
slack-stealth-auth --workspace work
slack-stealth-auth --workspace personal
```

</details>

---

<details>
<summary><h2>📋 Important Notes</h2></summary>

### Token Security

- **Local MCP**: Tokens are stored in plaintext in your config file. Protect this file appropriately.
- **Remote MCP**: Tokens are encrypted with AES-256-GCM before storage in Cloudflare KV.

### Session Tokens vs OAuth

This tool uses Slack **session tokens** (xoxc/xoxd), not official OAuth tokens. This is the same mechanism the Slack web client uses. While Slack doesn't officially document this API:
- It's been stable for years
- It's what every Slack web session uses
- Tokens typically last 6-12 months

### Rate Limits

Slack enforces rate limits on all API calls. The server implements:
- Token bucket rate limiting (2 requests/second)
- Automatic exponential backoff on 429 errors
- Request queuing to smooth bursts

### Personal Use

This tool is designed for **personal productivity**—accessing your own Slack account with your own credentials. You're accessing Slack as yourself, with your own permissions.

</details>

---

## License

MIT License - see [LICENSE](LICENSE) for details.

---

<p align="center">
  Made with Claude Code
</p>
