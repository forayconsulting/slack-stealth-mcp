"""The stealth invariant: read tools must never touch conversations.mark."""

from slack_stealth_mcp.tools.find_conversation import find_conversation
from slack_stealth_mcp.tools.get_context import get_context
from slack_stealth_mcp.tools.get_unread import get_unread
from slack_stealth_mcp.tools.list_conversations import list_conversations
from slack_stealth_mcp.tools.mark_read import mark_read
from slack_stealth_mcp.tools.schedule import list_scheduled


async def test_read_tools_never_mark_read(fake_client, fake_manager):
    await get_unread(fake_manager, workspace="testws")
    await find_conversation(fake_manager, query="alice")
    await list_conversations(fake_manager, workspace="testws")
    await get_context(fake_manager, channel="C100")
    fake_client.responses["drafts.list"] = {"ok": True, "drafts": []}
    await list_scheduled(fake_manager, workspace="testws")

    assert "conversations.mark" not in fake_client.endpoints_called
    # client.counts is read-only and must be a POST like Slack's own client
    assert ("POST", "client.counts") in [
        (m, e) for m, e, _ in fake_client.calls
    ]


async def test_mark_read_is_the_only_writer(fake_client, fake_manager):
    fake_client.responses["conversations.mark"] = {"ok": True}

    await mark_read(fake_manager, channel="C100", timestamp="1700000200.000200")

    assert fake_client.endpoints_called.count("conversations.mark") == 1
