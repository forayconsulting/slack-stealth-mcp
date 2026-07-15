"""Tests for the client.counts-based unread path."""

from slack_stealth_mcp.slack_client import SlackAPIError
from slack_stealth_mcp.tools.get_unread import get_unread


async def test_counts_path_finds_all_unreads(fake_manager):
    result = await get_unread(fake_manager, workspace="testws")

    assert result["source"] == "counts"

    # DMs: D01 (2 unread) and G01 (1 unread); D02 has no unreads
    dm_ids = [dm["channel_id"] for dm in result["unread_dms"]]
    assert dm_ids == ["D01", "G01"]  # recency order: D01 latest > G01 latest

    d01 = result["unread_dms"][0]
    assert d01["name"] == "@alice"
    assert d01["unread_count"] == 2
    assert [m["text"] for m in d01["messages"]] == ["hey", "are you around?"]

    g01 = result["unread_dms"][1]
    assert g01["name"] == "alice, bobby, Carol White"

    # Channels: C100 unread with a mention; C200 fully read
    channel_ids = [ch["channel_id"] for ch in result["unread_channels"]]
    assert channel_ids == ["C100"]
    assert result["unread_channels"][0]["name"] == "general"
    assert result["unread_channels"][0]["mention_count"] == 1

    assert result["totals"]["unread_dm_messages"] == 3
    assert result["totals"]["channels_with_unread"] == 1


async def test_counts_path_unread_mentions_only(fake_manager):
    result = await get_unread(fake_manager, workspace="testws")

    # Two mentions in search results; only the one newer than last_read counts
    assert result["totals"]["mentions"] == 1
    assert result["mentions"][0]["ts"] == "1700000150.000100"
    assert result["mentions"][0]["channel_id"] == "C100"


async def test_thread_summary_surfaced(fake_manager):
    result = await get_unread(fake_manager, workspace="testws")

    assert result["threads"]["has_unreads"] is True
    assert result["threads"]["mention_count"] == 2
    assert "thread" in result["summary"]


async def test_include_threads_false(fake_manager):
    result = await get_unread(fake_manager, workspace="testws", include_threads=False)
    assert "threads" not in result


async def test_caps_produce_truncation_notice(fake_manager):
    result = await get_unread(
        fake_manager, workspace="testws", max_conversations_to_check=1
    )

    # Only the most recent DM is previewed; the rest are reported, not hidden
    assert [dm["channel_id"] for dm in result["unread_dms"]] == ["D01"]
    assert result["truncated"]["dms_not_shown"] == 1  # G01
    assert result["truncated"]["channels_not_shown"] == 1  # C100


async def test_errors_surfaced_not_swallowed(fake_client, fake_manager):
    """A rate-limited conversation must show up in errors, not read as 'all read'."""
    original = fake_client.responses["conversations.history"]

    def flaky_history(method, endpoint, kwargs):
        if kwargs.get("params", {}).get("channel") == "C100":
            raise SlackAPIError("ratelimited")
        return original(method, endpoint, kwargs)

    fake_client.responses["conversations.history"] = flaky_history

    result = await get_unread(fake_manager, workspace="testws")

    assert result["source"] == "counts"
    assert any(
        e.get("channel_id") == "C100" and "ratelimited" in e["error"]
        for e in result["errors"]
    )
    # DMs still delivered despite the channel failure
    assert [dm["channel_id"] for dm in result["unread_dms"]] == ["D01", "G01"]


async def test_include_flags(fake_manager):
    result = await get_unread(
        fake_manager,
        workspace="testws",
        include_dms=False,
        include_mentions=False,
    )
    assert "unread_dms" not in result
    assert "mentions" not in result
    assert [ch["channel_id"] for ch in result["unread_channels"]] == ["C100"]


async def test_more_unread_flag(fake_manager):
    result = await get_unread(
        fake_manager, workspace="testws", max_messages_per_conversation=2
    )
    d01 = next(dm for dm in result["unread_dms"] if dm["channel_id"] == "D01")
    assert d01["more_unread"] is True
