"""Tests for the lookup data sources: server-side search + member listing.

find_conversation asks Slack to match the query (search.modules.channels) and
reads the user's own membership (users.conversations) instead of enumerating
the whole workspace. The full-listing cache remains only as a fallback.
"""

from slack_stealth_mcp.slack_client import SlackAPIError
from slack_stealth_mcp.tools.find_conversation import find_conversation
from slack_stealth_mcp.tools.list_conversations import list_conversations


async def test_find_uses_search_not_full_enumeration(fake_client, fake_manager):
    await find_conversation(fake_manager, query="general")

    # Channels come from server-side search; DMs from the member set.
    # The full-workspace conversations.list is NOT touched.
    assert "search.modules.channels" in fake_client.endpoints_called
    assert "users.conversations" in fake_client.endpoints_called
    assert "conversations.list" not in fake_client.endpoints_called


async def test_directory_fetched_once_across_calls(fake_client, fake_manager):
    await find_conversation(fake_manager, query="alice")
    await find_conversation(fake_manager, query="bob")
    # The user directory is TTL-cached; search runs fresh (and fast) each call
    assert fake_client.endpoints_called.count("users.list") == 1
    assert fake_client.endpoints_called.count("search.modules.channels") == 2


async def test_find_finds_non_member_channel_via_search(fake_client, fake_manager):
    # Server-side search returns channels even if you have not joined them
    RAW = __import__("tests.conftest", fromlist=["RAW_CONVS"]).RAW_CONVS
    RAW["C900"] = {"id": "C900", "name": "not-joined-room", "is_channel": True, "is_member": False}
    try:
        result = await find_conversation(fake_manager, query="not-joined")
        ids = [m["id"] for m in result["matches"]]
        assert "C900" in ids
    finally:
        RAW.pop("C900", None)


async def test_find_falls_back_when_search_unavailable(fake_client, fake_manager):
    def boom(method, endpoint, kwargs):
        raise SlackAPIError("unknown_method")

    fake_client.responses["search.modules.channels"] = boom

    result = await find_conversation(fake_manager, query="general")
    ids = [m["id"] for m in result["matches"]]
    assert "C100" in ids  # channel still found via the cached full listing
    assert "note" in result
    assert "conversations.list" in fake_client.endpoints_called


async def test_ensure_all_conversations_caches(fake_client):
    a = await fake_client.ensure_all_conversations()
    b = await fake_client.ensure_all_conversations()
    assert a == b
    assert fake_client.endpoints_called.count("conversations.list") == 1


async def test_ensure_all_conversations_ttl_expiry(fake_client):
    await fake_client.ensure_all_conversations()
    fake_client._all_convs_time = 0  # force stale
    await fake_client.ensure_all_conversations()
    assert fake_client.endpoints_called.count("conversations.list") == 2


async def test_list_conversations_uses_member_set_and_filters(fake_manager, fake_client):
    dms_only = await list_conversations(fake_manager, workspace="testws", types="dms")
    assert "channels" not in dms_only
    assert "private_channels" not in dms_only
    assert "direct_messages" in dms_only or "group_dms" in dms_only
    # Member listing, not the full workspace enumeration
    assert "users.conversations" in fake_client.endpoints_called
    assert "conversations.list" not in fake_client.endpoints_called
