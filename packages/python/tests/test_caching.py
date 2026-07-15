"""Tests for the shared, TTL-cached conversation/user directory listings."""

from slack_stealth_mcp.tools.find_conversation import find_conversation
from slack_stealth_mcp.tools.list_conversations import list_conversations


async def test_ensure_all_conversations_fetches_once(fake_client):
    a = await fake_client.ensure_all_conversations()
    b = await fake_client.ensure_all_conversations()
    assert a == b
    assert fake_client.endpoints_called.count("conversations.list") == 1


async def test_find_conversation_reuses_cache_across_calls(fake_client, fake_manager):
    await find_conversation(fake_manager, query="alice")
    await find_conversation(fake_manager, query="bob")

    # Full listing + user directory each fetched only once for the session
    assert fake_client.endpoints_called.count("conversations.list") == 1
    assert fake_client.endpoints_called.count("users.list") == 1


async def test_find_and_list_share_conversation_cache(fake_client, fake_manager):
    await find_conversation(fake_manager, query="alice")
    await list_conversations(fake_manager, workspace="testws")

    assert fake_client.endpoints_called.count("conversations.list") == 1


async def test_ttl_expiry_refetches(fake_client):
    await fake_client.ensure_all_conversations()
    # Force the cache stale
    fake_client._all_convs_time = 0
    await fake_client.ensure_all_conversations()
    assert fake_client.endpoints_called.count("conversations.list") == 2


async def test_list_conversations_filters_by_type(fake_manager):
    dms_only = await list_conversations(fake_manager, workspace="testws", types="dms")
    assert "channels" not in dms_only
    assert "private_channels" not in dms_only
    assert "direct_messages" in dms_only or "group_dms" in dms_only

    channels_only = await list_conversations(
        fake_manager, workspace="testws", types="channels"
    )
    assert "direct_messages" not in channels_only
    assert "group_dms" not in channels_only
    assert "channels" in channels_only or "private_channels" in channels_only
