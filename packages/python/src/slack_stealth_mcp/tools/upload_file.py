"""Tool for uploading files to a conversation."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any

from ..mrkdwn import normalize_mrkdwn
from ..slack_client import SlackAPIError
from ..workspace import WorkspaceManager
from .reply import conversation_label, resolve_send_target

# Slack's own per-file limit is 1 GB. Keep MCP uploads well below that,
# since the whole file is held in memory.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


def _load_content(
    file_path: str | None, content: str | None, content_base64: str | None
) -> tuple[bytes, str | None]:
    """Return (bytes, default filename) from exactly one content source."""
    sources = [s for s in (file_path, content, content_base64) if s is not None]
    if len(sources) != 1:
        raise ValueError("Provide exactly one of file_path, content, or content_base64")

    if file_path is not None:
        path = Path(file_path).expanduser()
        if not path.is_file():
            raise ValueError(f"File not found: {path}")
        size = path.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            raise ValueError(f"File is {size} bytes, over the {MAX_UPLOAD_BYTES} byte limit")
        return path.read_bytes(), path.name

    if content_base64 is not None:
        try:
            data = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise ValueError(f"content_base64 is not valid base64: {e}") from e
    else:
        data = content.encode("utf-8")  # type: ignore[union-attr]

    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Content is {len(data)} bytes, over the {MAX_UPLOAD_BYTES} byte limit")
    return data, None


async def upload_file(
    manager: WorkspaceManager,
    target: str,
    file_path: str | None = None,
    content: str | None = None,
    content_base64: str | None = None,
    filename: str | None = None,
    title: str | None = None,
    message: str | None = None,
    thread_ts: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Upload a file to a channel, DM, or thread. Does NOT mark anything read."""
    client = manager.get_client(workspace)

    try:
        data, default_name = _load_content(file_path, content, content_base64)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    name = filename or default_name
    if not name:
        return {"ok": False, "error": "filename is required with content or content_base64"}
    if not data:
        return {"ok": False, "error": "File is empty; Slack rejects empty uploads"}

    try:
        channel_id, thread_ts = await resolve_send_target(client, target, thread_ts)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    try:
        file = await client.upload_file(
            content=data,
            filename=name,
            channel=channel_id,
            title=title,
            initial_comment=normalize_mrkdwn(message) if message else None,
            thread_ts=thread_ts,
        )
    except SlackAPIError as e:
        return {
            "ok": False,
            "error": f"Failed to upload file: {e.error}",
            "channel_id": channel_id,
        }

    result: dict[str, Any] = {
        "ok": True,
        "file_id": file.get("id"),
        "filename": name,
        "title": file.get("title") or title or name,
        "size": len(data),
        "channel_id": channel_id,
    }
    if file.get("permalink"):
        result["permalink"] = file["permalink"]
    if thread_ts:
        result["thread_ts"] = thread_ts
    result["sent_to"] = await conversation_label(client, channel_id)
    return result
