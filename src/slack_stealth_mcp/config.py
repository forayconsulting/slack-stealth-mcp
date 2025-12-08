"""Configuration loader for Slack Stealth MCP.

Supports two configuration methods:
1. JSON config file at ~/.config/slack-stealth-mcp/config.json (multi-workspace)
2. Environment variables SLACK_XOXC_TOKEN and SLACK_XOXD_COOKIE (single workspace)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .types import Config, WorkspaceConfig

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "slack-stealth-mcp" / "config.json"


def load_config(config_path: Path | None = None) -> Config:
    """Load configuration from file or environment variables.

    Priority:
    1. Explicit config_path if provided
    2. Default config file location
    3. Environment variables (creates single "default" workspace)

    Args:
        config_path: Optional path to config file

    Returns:
        Config object with workspace configurations

    Raises:
        ValueError: If no valid configuration is found
    """
    # Try config file first
    path = config_path or DEFAULT_CONFIG_PATH
    if path.exists():
        return _load_from_file(path)

    # Fall back to environment variables
    return _load_from_env()


def _load_from_file(path: Path) -> Config:
    """Load configuration from JSON file."""
    with open(path) as f:
        data = json.load(f)

    workspaces = {}
    for name, ws_data in data.get("workspaces", {}).items():
        workspaces[name] = WorkspaceConfig(
            xoxc_token=ws_data["xoxc_token"],
            xoxd_cookie=ws_data["xoxd_cookie"],
            name=name,
        )

    default = data.get("default_workspace")
    if not default and workspaces:
        default = next(iter(workspaces.keys()))

    return Config(workspaces=workspaces, default_workspace=default)


def _load_from_env() -> Config:
    """Load configuration from environment variables."""
    xoxc = os.environ.get("SLACK_XOXC_TOKEN")
    xoxd = os.environ.get("SLACK_XOXD_COOKIE")

    if not xoxc or not xoxd:
        raise ValueError(
            "No configuration found. Either:\n"
            f"  1. Create config file at {DEFAULT_CONFIG_PATH}\n"
            "  2. Set SLACK_XOXC_TOKEN and SLACK_XOXD_COOKIE environment variables"
        )

    workspace = WorkspaceConfig(
        xoxc_token=xoxc,
        xoxd_cookie=xoxd,
        name="default",
    )

    return Config(
        workspaces={"default": workspace},
        default_workspace="default",
    )


def save_config(config: Config, config_path: Path | None = None) -> None:
    """Save configuration to JSON file.

    Args:
        config: Configuration to save
        config_path: Optional path (defaults to DEFAULT_CONFIG_PATH)
    """
    path = config_path or DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "workspaces": {
            name: {
                "xoxc_token": ws.xoxc_token,
                "xoxd_cookie": ws.xoxd_cookie,
            }
            for name, ws in config.workspaces.items()
        },
        "default_workspace": config.default_workspace,
    }

    with open(path, "w") as f:
        json.dump(data, f, indent=2)
