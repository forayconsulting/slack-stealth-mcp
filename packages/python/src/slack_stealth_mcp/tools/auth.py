"""Tools for Slack authentication management."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

from ..config import DEFAULT_CONFIG_PATH
from ..slack_client import SlackAPIError
from ..workspace import WorkspaceManager


def _has_display() -> bool:
    """Check if a graphical display is available."""
    # macOS always has display if running in user session
    if sys.platform == "darwin":
        return True
    # Windows always has display if running in user session
    if sys.platform == "win32":
        return True
    # Linux/BSD: check for X11 or Wayland
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


async def auth_status(
    manager: WorkspaceManager,
    workspace: str | None = None,
    test_connection: bool = True,
) -> dict[str, Any]:
    """Check authentication status for workspaces.

    Args:
        manager: Workspace manager instance
        workspace: Specific workspace to check (checks all if not specified)
        test_connection: Whether to actually test tokens against Slack API

    Returns:
        Dict with workspace auth status and summary
    """
    result: dict[str, Any] = {
        "workspaces": {},
        "default_workspace": manager.default_workspace,
        "config_path": str(DEFAULT_CONFIG_PATH),
    }

    # Determine which workspaces to check
    if workspace:
        workspaces_to_check = [workspace]
    else:
        workspaces_to_check = manager.workspace_names

    # Handle case where no workspaces are configured
    if not workspaces_to_check:
        result["summary"] = "No workspaces configured"
        result["needs_auth"] = True
        return result

    for ws_name in workspaces_to_check:
        ws_status: dict[str, Any] = {}

        if ws_name not in manager.workspace_names:
            ws_status = {
                "configured": False,
                "error": f"Workspace '{ws_name}' not found in config",
            }
        elif test_connection:
            try:
                client = manager.get_client(ws_name)
                auth = await client.test_auth()
                ws_status = {
                    "configured": True,
                    "valid": True,
                    "user": auth.get("user"),
                    "team": auth.get("team"),
                    "url": auth.get("url"),
                }
            except SlackAPIError as e:
                ws_status = {
                    "configured": True,
                    "valid": False,
                    "error": e.error,
                }
            except Exception as e:
                ws_status = {
                    "configured": True,
                    "valid": False,
                    "error": str(e),
                }
        else:
            ws_status = {
                "configured": True,
                "valid": "not_tested",
            }

        result["workspaces"][ws_name] = ws_status

    # Add summary
    configured = sum(1 for ws in result["workspaces"].values() if ws.get("configured"))
    valid = sum(1 for ws in result["workspaces"].values() if ws.get("valid") is True)
    result["summary"] = f"{valid}/{configured} workspace(s) authenticated"
    result["needs_auth"] = valid == 0

    return result


async def auth_login(
    manager: WorkspaceManager,
    workspace: str | None = None,
    set_default: bool = True,
) -> dict[str, Any]:
    """Initiate browser-based authentication flow and wait for completion.

    This is a BLOCKING call that waits for the user to complete login
    in the browser, then automatically reloads the configuration.

    Args:
        manager: Workspace manager instance
        workspace: Name for the workspace (optional)
        set_default: Whether to set as default workspace

    Returns:
        Dict with authentication result and new workspace info
    """
    # Check for display
    if not _has_display():
        return {
            "ok": False,
            "error": "no_display",
            "message": "Cannot open browser - no display detected",
            "instructions": [
                "Authentication requires a graphical display.",
                "Options:",
                "1. Run 'slack-stealth-auth' from a terminal with display access",
                f"2. Manually add tokens to {DEFAULT_CONFIG_PATH}",
            ],
        }

    # Build command
    cmd = [sys.executable, "-m", "slack_stealth_mcp.auth"]
    if workspace:
        cmd.extend(["--workspace", workspace])
    if not set_default:
        cmd.append("--no-default")

    try:
        # Launch subprocess
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # BLOCKING: Wait for auth to complete (up to 10 minutes)
        try:
            return_code = process.wait(timeout=600)
        except subprocess.TimeoutExpired:
            process.kill()
            return {
                "ok": False,
                "error": "timeout",
                "message": "Authentication timed out after 10 minutes",
            }

        if return_code != 0:
            stderr = process.stderr.read().decode() if process.stderr else ""
            return {
                "ok": False,
                "error": "auth_failed",
                "message": f"Authentication process failed (exit code {return_code})",
                "details": stderr[:500] if stderr else None,
            }

        # Auto-reload config after successful auth
        reload_result = await manager.reload_config()

        if not reload_result.get("ok"):
            return {
                "ok": False,
                "error": "reload_failed",
                "message": "Authentication succeeded but failed to reload config",
                "details": reload_result.get("error"),
            }

        return {
            "ok": True,
            "status": "authenticated",
            "message": "Successfully authenticated with Slack",
            "changes": reload_result.get("changes", {}),
            "workspaces": reload_result.get("workspaces", []),
            "default_workspace": reload_result.get("default_workspace"),
        }

    except Exception as e:
        return {
            "ok": False,
            "error": "launch_failed",
            "message": f"Failed to launch authentication: {e}",
        }


