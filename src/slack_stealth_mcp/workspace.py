"""Workspace manager for multi-workspace support."""

from __future__ import annotations

from typing import Any

from .config import Config
from .slack_client import SlackClient


class WorkspaceManager:
    """Manages multiple Slack workspace connections."""

    def __init__(self, config: Config):
        """Initialize workspace manager.

        Args:
            config: Application configuration with workspace credentials
        """
        self._config = config
        self._clients: dict[str, SlackClient] = {}

        # Initialize clients for all configured workspaces
        for name, ws_config in config.workspaces.items():
            self._clients[name] = SlackClient(ws_config)

    @property
    def default_workspace(self) -> str | None:
        """Get the default workspace name."""
        return self._config.default_workspace

    @property
    def workspace_names(self) -> list[str]:
        """Get list of all configured workspace names."""
        return list(self._clients.keys())

    def get_client(self, workspace: str | None = None) -> SlackClient:
        """Get Slack client for a workspace.

        Args:
            workspace: Workspace name (uses default if None)

        Returns:
            SlackClient for the workspace

        Raises:
            ValueError: If workspace not found or no default set
        """
        name = workspace or self._config.default_workspace

        if not name:
            raise ValueError(
                "No workspace specified and no default workspace configured. "
                f"Available workspaces: {', '.join(self.workspace_names)}"
            )

        if name not in self._clients:
            raise ValueError(
                f"Workspace '{name}' not found. "
                f"Available workspaces: {', '.join(self.workspace_names)}"
            )

        return self._clients[name]

    async def test_all_connections(self) -> dict[str, dict[str, Any]]:
        """Test authentication for all workspaces.

        Returns:
            Dict mapping workspace name to auth test results or error
        """
        results = {}
        for name, client in self._clients.items():
            try:
                auth = await client.test_auth()
                results[name] = {
                    "ok": True,
                    "user": auth.get("user"),
                    "team": auth.get("team"),
                    "url": auth.get("url"),
                }
            except Exception as e:
                results[name] = {
                    "ok": False,
                    "error": str(e),
                }
        return results

    async def close_all(self) -> None:
        """Close all workspace connections."""
        for client in self._clients.values():
            await client.close()

    async def __aenter__(self) -> "WorkspaceManager":
        """Async context manager entry."""
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.close_all()
