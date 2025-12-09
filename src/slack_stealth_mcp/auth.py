"""OAuth-like authentication flow using Playwright to capture Slack session tokens."""

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

from .config import DEFAULT_CONFIG_PATH, load_config, save_config
from .types import Config, WorkspaceConfig


async def extract_tokens_from_browser(
    workspace_name: str = "default",
    headless: bool = False,
    timeout: int = 300,
) -> Tuple[str, str, str]:
    """Open browser for Slack login and extract xoxc/xoxd tokens.

    This provides an OAuth-like experience where the user simply logs in
    and tokens are automatically captured.

    Args:
        workspace_name: Name to save this workspace as
        headless: Run browser in headless mode (not recommended for auth)
        timeout: Max seconds to wait for authentication

    Returns:
        Tuple of (xoxc_token, xoxd_cookie, team_name)

    Raises:
        TimeoutError: If authentication times out
        ValueError: If tokens cannot be extracted
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright not installed. Installing...")
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "playwright"], check=True)
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        from playwright.async_api import async_playwright

    print(f"\n🔐 Starting Slack authentication for workspace '{workspace_name}'")
    print("   A browser window will open. Please log in to Slack.")
    print("   This window will close automatically after successful login.\n")

    async with async_playwright() as p:
        # Launch browser
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()

        # Navigate to Slack
        await page.goto("https://slack.com/signin")

        # Wait for successful authentication
        # We detect this by waiting for the app to load (URL contains /client/)
        # or by detecting the presence of specific localStorage keys
        xoxc_token = None
        xoxd_cookie = None
        team_name = None

        start_time = asyncio.get_event_loop().time()

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                await browser.close()
                raise TimeoutError(
                    f"Authentication timed out after {timeout} seconds. "
                    "Please try again."
                )

            # Check ALL pages in context - Slack often opens workspace in new tab
            all_pages = context.pages
            target_page = None
            current_url = None

            for p in all_pages:
                url = p.url
                if "/client/" in url or ("app.slack.com" in url and "/client/" in url):
                    target_page = p
                    current_url = url
                    break

            # If no workspace page found yet, keep the original page reference
            if target_page is None:
                current_url = page.url
            else:
                page = target_page  # Switch to the workspace page

            # Look for patterns that indicate we're logged in
            # - /client/ in URL means we're in the Slack app
            # - app.slack.com means we're in the web app
            if "/client/" in current_url or "app.slack.com" in current_url:
                print(f"✓ Detected successful login at: {current_url}")
                print("   Extracting tokens...")

                # Wait a moment for everything to load
                await asyncio.sleep(3)

                # Extract xoxc token from localStorage
                # Slack stores it in various localStorage keys
                local_storage = await page.evaluate("""
                    () => {
                        const result = {};
                        for (let i = 0; i < localStorage.length; i++) {
                            const key = localStorage.key(i);
                            result[key] = localStorage.getItem(key);
                        }
                        return result;
                    }
                """)

                print(f"   Found {len(local_storage)} localStorage keys")

                # Find xoxc token - it's usually in localConfig_v2 or similar
                for key, value in local_storage.items():
                    if value and "xoxc-" in value:
                        # Try to extract the token - include underscores in pattern
                        match = re.search(r'(xoxc-[a-zA-Z0-9_-]+)', value)
                        if match:
                            xoxc_token = match.group(1)
                            print(f"   ✓ Found xoxc token in key: {key[:30]}...")
                            break
                    # Also check for JSON-encoded values
                    if value and value.startswith('{'):
                        try:
                            data = json.loads(value)
                            if isinstance(data, dict):
                                # Recursively search for xoxc token
                                token = _find_token_in_dict(data, "xoxc-")
                                if token:
                                    xoxc_token = token
                                    print(f"   ✓ Found xoxc token in JSON key: {key[:30]}...")
                                    break
                        except json.JSONDecodeError:
                            pass

                if not xoxc_token:
                    print("   ⚠ Could not find xoxc token in localStorage")
                    # Debug: show keys that might contain token
                    for key in local_storage.keys():
                        if "config" in key.lower() or "token" in key.lower():
                            print(f"      Potential key: {key}")

                # Extract xoxd cookie (the 'd' cookie)
                cookies = await context.cookies()
                print(f"   Found {len(cookies)} cookies")
                for cookie in cookies:
                    if cookie["name"] == "d" and "slack.com" in cookie["domain"]:
                        xoxd_cookie = cookie["value"]
                        print(f"   ✓ Found 'd' cookie for {cookie['domain']}")
                        break

                if not xoxd_cookie:
                    print("   ⚠ Could not find 'd' cookie")
                    # Debug: show slack cookies
                    for cookie in cookies:
                        if "slack" in cookie.get("domain", ""):
                            print(f"      Cookie: {cookie['name']} @ {cookie['domain']}")

                # Try to get team name from URL or page content
                if "app.slack.com/client/" in current_url:
                    # URL format: app.slack.com/client/TXXXXXX/...
                    match = re.search(r'/client/([A-Z0-9]+)', current_url)
                    if match:
                        team_id = match.group(1)
                        team_name = team_id  # We'll use team ID as name for now

                # Also try to get team name from page title
                # Title format is usually: "channel - TeamName" or "channel (Channel) - TeamName - X new items"
                title = await page.title()
                if " - " in title:
                    # Extract team name (usually second part, before any "new items" suffix)
                    parts = title.split(" - ")
                    if len(parts) >= 2:
                        # Team name is typically the second part
                        candidate = parts[1].strip()
                        # Remove notification counts like "4 new items"
                        if not candidate.endswith("new items") and not candidate.endswith("new item"):
                            team_name = candidate
                        elif len(parts) >= 3:
                            # Try third part if second was notification count
                            team_name = parts[1].strip()
                elif " | " in title:
                    team_name = title.split(" | ")[-1].strip()
                elif "Slack" in title:
                    team_name = title.replace("Slack", "").strip(" -|")

                if xoxc_token and xoxd_cookie:
                    print(f"\n✓ Successfully extracted tokens for '{team_name or workspace_name}'")
                    await browser.close()
                    return xoxc_token, xoxd_cookie, team_name or workspace_name
                else:
                    # If we're on the right page but can't find tokens, wait and retry
                    print("   Waiting for tokens to appear...")
                    await asyncio.sleep(2)

            # Brief pause before checking again
            await asyncio.sleep(1)

        await browser.close()
        raise ValueError("Could not extract tokens. Please try again.")


def _find_token_in_dict(data: dict, prefix: str) -> Optional[str]:
    """Recursively search for a token with given prefix in a dict."""
    for key, value in data.items():
        if isinstance(value, str) and value.startswith(prefix):
            return value
        if isinstance(value, dict):
            result = _find_token_in_dict(value, prefix)
            if result:
                return result
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    result = _find_token_in_dict(item, prefix)
                    if result:
                        return result
                elif isinstance(item, str) and item.startswith(prefix):
                    return item
    return None


async def authenticate(
    workspace_name: str = "default",
    set_default: bool = True,
) -> None:
    """Run the authentication flow and save tokens to config.

    Args:
        workspace_name: Name for this workspace in config
        set_default: Whether to set this as the default workspace
    """
    # Extract tokens via browser
    xoxc_token, xoxd_cookie, team_name = await extract_tokens_from_browser(
        workspace_name=workspace_name
    )

    # Use team name if we got one, otherwise use provided workspace_name
    final_name = workspace_name if workspace_name != "default" else team_name

    # Load existing config or create new
    try:
        config = load_config()
    except ValueError:
        config = Config(workspaces={}, default_workspace=None)

    # Add/update workspace
    config.workspaces[final_name] = WorkspaceConfig(
        xoxc_token=xoxc_token,
        xoxd_cookie=xoxd_cookie,
        name=final_name,
    )

    # Set as default if requested or if it's the first workspace
    if set_default or config.default_workspace is None:
        config.default_workspace = final_name

    # Save config
    save_config(config)

    print(f"\n✅ Authentication successful!")
    print(f"   Workspace '{final_name}' saved to config")
    print(f"   Config location: {DEFAULT_CONFIG_PATH}")

    if set_default:
        print(f"   Set as default workspace")

    print("\n   You can now use the MCP server with Claude Desktop.")
    print("   Add this to your Claude Desktop config:\n")
    print('   "slack": {')
    print('     "command": "slack-stealth-mcp"')
    print('   }')


def main() -> None:
    """CLI entry point for authentication."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Authenticate with Slack and save tokens for the MCP server"
    )
    parser.add_argument(
        "--workspace", "-w",
        default="default",
        help="Name for this workspace (default: uses Slack team name)"
    )
    parser.add_argument(
        "--no-default",
        action="store_true",
        help="Don't set this workspace as the default"
    )

    args = parser.parse_args()

    # Check if playwright browsers are installed
    try:
        asyncio.run(authenticate(
            workspace_name=args.workspace,
            set_default=not args.no_default,
        ))
    except KeyboardInterrupt:
        print("\n\nAuthentication cancelled.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Authentication failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
