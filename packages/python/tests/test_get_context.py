"""Tests for get_context: user IDs, thread order/truncation, reactions."""

from slack_stealth_mcp.tools.get_context import get_context

ROOT = "1700000100.000100"

THREAD = [
    # conversations.replies order: root first, then replies
    {"ts": ROOT, "thread_ts": ROOT, "reply_count": 2, "user": "U01", "text": "root"},
    {"ts": "1700000110.000000", "thread_ts": ROOT, "user": "U02", "text": "r1"},
    {"ts": "1700000120.000000", "thread_ts": ROOT, "user": "U01", "text": "r2"},
]


async def test_thread_messages_all_carry_root_ts(fake_client, fake_manager):
    fake_client.responses["conversations.replies"] = {"ok": True, "messages": THREAD}

    result = await get_context(fake_manager, channel="C100", message_ts=ROOT)

    assert result["is_thread"] is True
    by_ts = {m["ts"]: m for m in result["messages"]}
    assert len(by_ts) == 3
    for m in result["messages"]:
        assert m["thread_ts"] == ROOT
    assert "is_reply" not in by_ts[ROOT]
    assert by_ts["1700000110.000000"]["is_reply"] is True
    assert by_ts["1700000120.000000"]["is_reply"] is True
    assert by_ts[ROOT]["user_id"] == "U01"
    assert by_ts["1700000110.000000"]["user_id"] == "U02"


async def test_thread_root_ts_filled_when_message_lacks_it(fake_client, fake_manager):
    # Defensive: if Slack omits thread_ts on a thread message, use the requested root
    messages = [dict(m) for m in THREAD]
    messages[0].pop("thread_ts")
    fake_client.responses["conversations.replies"] = {"ok": True, "messages": messages}

    result = await get_context(fake_manager, channel="C100", message_ts=ROOT)

    root = next(m for m in result["messages"] if m["ts"] == ROOT)
    assert root["thread_ts"] == ROOT
    assert "is_reply" not in root


async def test_history_parent_carries_thread_ts_plain_message_does_not(
    fake_client, fake_manager
):
    fake_client.responses["conversations.history"] = {
        "ok": True,
        "messages": [
            {"ts": "1700000300.000000", "text": "plain", "user": "U02"},
            {"ts": ROOT, "thread_ts": ROOT, "reply_count": 2, "text": "root", "user": "U01"},
            {"ts": "1700000050.000000", "text": "bot post"},
        ],
    }

    result = await get_context(fake_manager, channel="C100")

    by_ts = {m["ts"]: m for m in result["messages"]}
    assert by_ts[ROOT]["thread_ts"] == ROOT
    assert "is_reply" not in by_ts[ROOT]
    assert by_ts[ROOT]["reply_count"] == 2
    assert "thread_ts" not in by_ts["1700000300.000000"]
    assert by_ts["1700000300.000000"]["user_id"] == "U02"
    assert by_ts["1700000050.000000"]["user_id"] is None
    assert by_ts["1700000050.000000"]["user"] == "Unknown"
    assert "is_thread" not in result


async def test_thread_is_oldest_first_with_root_first(fake_client, fake_manager):
    fake_client.responses["conversations.replies"] = {"ok": True, "messages": THREAD}

    result = await get_context(fake_manager, channel="C100", message_ts=ROOT)

    assert [m["ts"] for m in result["messages"]] == [m["ts"] for m in THREAD]
    assert result["total_thread_messages"] == 3
    assert "omitted_replies" not in result


async def test_history_is_still_reversed_to_oldest_first(fake_client, fake_manager):
    # conversations.history returns newest first; output must be oldest first
    fake_client.responses["conversations.history"] = {
        "ok": True,
        "messages": [
            {"ts": "1700000300.000000", "text": "c", "user": "U01"},
            {"ts": "1700000200.000000", "text": "b", "user": "U01"},
            {"ts": "1700000100.000000", "text": "a", "user": "U01"},
        ],
    }

    result = await get_context(fake_manager, channel="C100")

    assert [m["text"] for m in result["messages"]] == ["a", "b", "c"]
    assert "total_thread_messages" not in result


def _long_thread(replies: int) -> list[dict]:
    msgs = [{"ts": ROOT, "thread_ts": ROOT, "reply_count": replies, "user": "U01", "text": "root"}]
    for i in range(replies):
        msgs.append(
            {"ts": f"17000002{i:02d}.000000", "thread_ts": ROOT, "user": "U02", "text": f"r{i}"}
        )
    return msgs


async def test_long_thread_keeps_root_and_newest_replies(fake_client, fake_manager):
    fake_client.responses["conversations.replies"] = {
        "ok": True,
        "messages": _long_thread(20),
    }

    result = await get_context(
        fake_manager, channel="C100", message_ts=ROOT, context_size=5
    )

    texts = [m["text"] for m in result["messages"]]
    assert texts == ["root", "r16", "r17", "r18", "r19"]
    assert result["message_count"] == 5
    assert result["total_thread_messages"] == 21
    assert result["omitted_replies"] == 16
    assert result["is_thread"] is True
    assert result["thread_ts"] == ROOT

    # The thread is fetched with a full page, not context_size
    params = next(kw for _, ep, kw in fake_client.calls if ep == "conversations.replies")
    assert params["params"]["limit"] == 1000


async def test_reactions_omit_count_of_one(fake_client, fake_manager):
    fake_client.responses["conversations.history"] = {
        "ok": True,
        "messages": [
            {
                "ts": "1700000300.000000",
                "text": "hi",
                "user": "U01",
                "reactions": [
                    {"name": "x", "count": 1, "users": ["U02"]},
                    {"name": "tada", "count": 3, "users": ["U01", "U02", "U03"]},
                ],
            },
        ],
    }

    result = await get_context(fake_manager, channel="C100")

    assert result["messages"][0]["reactions"] == [":x:", ":tada:3"]
