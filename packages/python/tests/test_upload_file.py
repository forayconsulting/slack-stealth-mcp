"""Tests for slack_upload_file: content sources, target resolution, the 3-step flow."""

import base64
import json

from slack_stealth_mcp.slack_client import SlackAPIError
from slack_stealth_mcp.tools.upload_file import upload_file


def _install_upload(fake_client):
    """Stub the 3-step upload; return a dict capturing what was sent."""
    seen = {}

    def get_url(method, endpoint, kwargs):
        seen["reserve"] = kwargs["data"]
        return {"ok": True, "upload_url": "https://files.slack.com/upload/v1/abc", "file_id": "F123"}

    def complete(method, endpoint, kwargs):
        seen["complete"] = kwargs["data"]
        return {
            "ok": True,
            "files": [
                {
                    "id": "F123",
                    "title": json.loads(kwargs["data"]["files"])[0]["title"],
                    "permalink": "https://team.slack.com/files/U0ME/F123/x",
                }
            ],
        }

    async def send_bytes(upload_url, content, filename):
        seen["bytes"] = (upload_url, content, filename)

    fake_client.responses["files.getUploadURLExternal"] = get_url
    fake_client.responses["files.completeUploadExternal"] = complete
    fake_client._send_upload_bytes = send_bytes
    return seen


async def test_upload_text_content(fake_client, fake_manager):
    seen = _install_upload(fake_client)

    result = await upload_file(
        fake_manager,
        target="C100",
        content="a,b\n1,2\n",
        filename="data.csv",
        message="**here** it is",
    )

    assert result["ok"] is True
    assert result["file_id"] == "F123"
    assert result["permalink"].endswith("/F123/x")
    assert result["size"] == 8
    assert seen["reserve"] == {"filename": "data.csv", "length": "8"}
    assert seen["bytes"] == ("https://files.slack.com/upload/v1/abc", b"a,b\n1,2\n", "data.csv")
    assert json.loads(seen["complete"]["files"]) == [{"id": "F123", "title": "data.csv"}]
    assert seen["complete"]["channel_id"] == "C100"
    # initial comment goes through mrkdwn normalization
    assert seen["complete"]["initial_comment"] == "*here* it is"
    assert "thread_ts" not in seen["complete"]


async def test_upload_local_file_into_thread_from_url(fake_client, fake_manager, tmp_path):
    seen = _install_upload(fake_client)
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.4 fake")

    result = await upload_file(
        fake_manager,
        target="https://team.slack.com/archives/C100/p1700000000123456",
        file_path=str(path),
        title="Q4 report",
    )

    assert result["ok"] is True
    assert result["filename"] == "report.pdf"
    assert result["title"] == "Q4 report"
    assert result["thread_ts"] == "1700000000.123456"
    assert seen["complete"]["thread_ts"] == "1700000000.123456"
    assert seen["bytes"][1] == b"%PDF-1.4 fake"


async def test_upload_base64_to_user_opens_dm(fake_client, fake_manager):
    seen = _install_upload(fake_client)
    fake_client.responses["conversations.open"] = {"ok": True, "channel": {"id": "D01"}}

    result = await upload_file(
        fake_manager,
        target="U01",
        content_base64=base64.b64encode(b"\x89PNG").decode(),
        filename="pic.png",
    )

    assert result["ok"] is True
    assert result["channel_id"] == "D01"
    assert seen["bytes"][1] == b"\x89PNG"


async def test_upload_input_validation(fake_client, fake_manager, tmp_path):
    _install_upload(fake_client)

    none = await upload_file(fake_manager, target="C100")
    two = await upload_file(fake_manager, target="C100", content="x", content_base64="eA==", filename="x")
    no_name = await upload_file(fake_manager, target="C100", content="x")
    bad_b64 = await upload_file(fake_manager, target="C100", content_base64="***", filename="x")
    missing = await upload_file(fake_manager, target="C100", file_path=str(tmp_path / "nope.txt"))
    empty = await upload_file(fake_manager, target="C100", content="", filename="e.txt")

    for r in (none, two, no_name, bad_b64, missing, empty):
        assert r["ok"] is False
    assert "exactly one" in none["error"]
    assert "filename" in no_name["error"]
    assert "base64" in bad_b64["error"]
    assert "not found" in missing["error"]
    assert "empty" in empty["error"]
    assert "files.getUploadURLExternal" not in fake_client.endpoints_called


async def test_upload_api_error(fake_client, fake_manager):
    _install_upload(fake_client)

    def fail(method, endpoint, kwargs):
        raise SlackAPIError("not_in_channel")

    fake_client.responses["files.completeUploadExternal"] = fail

    result = await upload_file(fake_manager, target="C100", content="x", filename="x.txt")

    assert result["ok"] is False
    assert "not_in_channel" in result["error"]
