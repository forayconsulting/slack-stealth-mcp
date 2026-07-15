"""Tests for the scan fallback when client.counts is unavailable."""

from slack_stealth_mcp.slack_client import SlackAPIError
from slack_stealth_mcp.tools.get_unread import get_unread
from tests.conftest import RAW_CONVS


def _counts_unavailable(method, endpoint, kwargs):
    raise SlackAPIError("unknown_method")


async def test_falls_back_to_scan(fake_client, fake_manager):
    fake_client.responses["client.counts"] = _counts_unavailable

    # Scan path determines unreads via conversations.info fields
    def info_with_unreads(method, endpoint, kwargs):
        channel = kwargs.get("params", {}).get("channel")
        if channel not in RAW_CONVS:
            raise SlackAPIError("channel_not_found")
        data = dict(RAW_CONVS[channel])
        if channel == "D01":
            data["unread_count_display"] = 2
            data["last_read"] = "1700000100.000100"
        elif channel == "C100":
            data["unread_count_display"] = 1
            data["last_read"] = "1700000100.000100"
        else:
            data["unread_count_display"] = 0
            data["last_read"] = "1700009999.000000"
        return {"ok": True, "channel": data}

    fake_client.responses["conversations.info"] = info_with_unreads

    result = await get_unread(fake_manager, workspace="testws")

    assert result["source"] == "scan_fallback"
    assert "unknown_method" in result["fallback_reason"]

    dm_ids = [dm["channel_id"] for dm in result["unread_dms"]]
    assert dm_ids == ["D01"]
    assert [ch["channel_id"] for ch in result["unread_channels"]] == ["C100"]


async def test_scan_detects_dm_without_unread_count_fields(fake_client, fake_manager):
    """Regression: 1:1 DMs missing unread_count* must fall back to last_read."""
    fake_client.responses["client.counts"] = _counts_unavailable

    def info_no_counts(method, endpoint, kwargs):
        channel = kwargs.get("params", {}).get("channel")
        if channel not in RAW_CONVS:
            raise SlackAPIError("channel_not_found")
        data = dict(RAW_CONVS[channel])
        # No unread_count fields at all; D01 has messages newer than last_read
        if channel == "D01":
            data["last_read"] = "1700000100.000100"
        else:
            data["last_read"] = "1700009999.000000"
        return {"ok": True, "channel": data}

    fake_client.responses["conversations.info"] = info_no_counts

    result = await get_unread(fake_manager, workspace="testws")

    assert result["source"] == "scan_fallback"
    dm_ids = [dm["channel_id"] for dm in result.get("unread_dms", [])]
    assert "D01" in dm_ids
