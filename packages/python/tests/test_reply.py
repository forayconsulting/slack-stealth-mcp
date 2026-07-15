"""Tests for the reply tool: mrkdwn normalization, URL/user resolution."""

from slack_stealth_mcp.tools.reply import reply
from tests.conftest import RAW_CONVS


def _install_post(fake_client):
    """Capture chat.postMessage payloads and echo the sent text back."""
    sent = {}

    def post_message(method, endpoint, kwargs):
        sent.update(kwargs.get("data", {}))
        return {
            "ok": True,
            "message": {"ts": "1700000999.000000", "text": sent.get("text", "")},
        }

    fake_client.responses["chat.postMessage"] = post_message
    return sent


async def test_reply_normalizes_markdown(fake_client, fake_manager):
    sent = _install_post(fake_client)

    result = await reply(
        fake_manager,
        target="C100",
        message="**bold** and [docs](https://x.com)",
    )

    assert result["ok"] is True
    assert sent["text"] == "*bold* and <https://x.com|docs>"
    assert sent["link_names"] == "true"


async def test_reply_resolves_archive_url(fake_client, fake_manager):
    sent = _install_post(fake_client)

    result = await reply(
        fake_manager,
        target="https://team.slack.com/archives/C100/p1700000000123456",
        message="hi",
    )

    assert result["ok"] is True
    assert sent["channel"] == "C100"
    # message URL implies a thread reply
    assert sent["thread_ts"] == "1700000000.123456"


async def test_reply_opens_dm_for_user_id(fake_client, fake_manager):
    sent = _install_post(fake_client)
    fake_client.responses["conversations.open"] = {"ok": True, "channel": {"id": "D01"}}

    result = await reply(fake_manager, target="U01", message="hey")

    assert result["ok"] is True
    assert sent["channel"] == "D01"


async def test_reply_rejects_unresolvable_target(fake_client, fake_manager):
    _install_post(fake_client)
    result = await reply(fake_manager, target="the general channel", message="hi")
    assert result["ok"] is False
    assert "slack_find_conversation" in result["error"]
