"""The "time" field is UTC in every tool, matching the Cloudflare port."""

import time

import pytest

from slack_stealth_mcp.tools.search import search
from slack_stealth_mcp.types import SlackMessage

# 1700000000 = 2023-11-14 22:13:20 UTC
TS = "1700000000.000100"


@pytest.fixture(params=["UTC", "America/Los_Angeles", "Asia/Tokyo"])
def process_tz(request, monkeypatch):
    monkeypatch.setenv("TZ", request.param)
    time.tzset()
    yield request.param
    monkeypatch.undo()
    time.tzset()


def test_timestamp_is_utc_regardless_of_process_tz(process_tz):
    stamp = SlackMessage(ts=TS, text="hi").timestamp

    assert stamp.utcoffset().total_seconds() == 0
    assert stamp.strftime("%Y-%m-%d %H:%M:%S") == "2023-11-14 22:13:20"
    assert stamp.strftime("%H:%M") == "22:13"


async def test_search_time_field_is_utc(process_tz, fake_client, fake_manager):
    fake_client.responses["search.messages"] = {
        "ok": True,
        "messages": {
            "matches": [{"ts": TS, "text": "hi"}],
            "paging": {"total": 1, "page": 1, "pages": 1},
        },
    }

    hit = (await search(fake_manager, query="hi"))["results"][0]

    assert hit["time"] == "2023-11-14 22:13:20"
