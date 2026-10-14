"""Tests for the search tool: IDs and permalinks carried through from search.messages."""

from slack_stealth_mcp.tools.search import search


async def test_search_results_carry_ids_and_permalink(fake_client, fake_manager):
    await fake_client.ensure_user_directory()
    fake_client.responses["search.messages"] = {
        "ok": True,
        "messages": {
            "matches": [
                {
                    "ts": "1700000150.000100",
                    "text": "deploy is done",
                    "user": "U01",
                    "channel": {"id": "C100", "name": "general"},
                    "permalink": "https://team.slack.com/archives/C100/p1700000150000100",
                },
            ],
            "paging": {"total": 1, "page": 1, "pages": 1},
        },
    }

    result = await search(fake_manager, query="deploy")

    hit = result["results"][0]
    assert hit["channel_id"] == "C100"
    assert hit["channel_name"] == "general"
    assert hit["user_id"] == "U01"
    assert hit["permalink"] == "https://team.slack.com/archives/C100/p1700000150000100"
    # Existing fields keep their formats
    assert hit["ts"] == "1700000150.000100"
    assert hit["user"] == "alice"
    assert hit["text"] == "deploy is done"
    assert "thread_ts" not in hit


async def test_search_missing_ids_are_none(fake_client, fake_manager):
    fake_client.responses["search.messages"] = {
        "ok": True,
        "messages": {
            "matches": [{"ts": "1700000150.000100", "text": "bot post"}],
            "paging": {"total": 1, "page": 1, "pages": 1},
        },
    }

    result = await search(fake_manager, query="bot")

    hit = result["results"][0]
    assert hit["channel_id"] is None
    assert hit["channel_name"] is None
    assert hit["user_id"] is None
    assert hit["permalink"] is None
    assert hit["user"] == "Unknown"


async def test_search_thread_reply_keeps_thread_fields(fake_client, fake_manager):
    fake_client.responses["search.messages"] = {
        "ok": True,
        "messages": {
            "matches": [
                {
                    "ts": "1700000200.000200",
                    "thread_ts": "1700000100.000100",
                    "text": "reply",
                    "user": "U02",
                    "channel": "D02",
                },
            ],
            "paging": {"total": 1, "page": 1, "pages": 1},
        },
    }

    result = await search(fake_manager, query="reply")

    hit = result["results"][0]
    assert hit["channel_id"] == "D02"  # bare channel ID string is accepted
    assert hit["channel_name"] is None
    assert hit["thread_ts"] == "1700000100.000100"
    assert hit["is_thread_reply"] is True


async def test_search_calls_only_search_messages(fake_client, fake_manager):
    await search(fake_manager, query="ping")

    assert fake_client.endpoints_called == ["search.messages"]


# --- in_channel / from_user ID detection ------------------------------------


async def _query_for(fake_manager, **kwargs) -> str:
    result = await search(fake_manager, query="x", **kwargs)
    return result["query"]


async def test_in_channel_ids_use_channel_mention_form(fake_client, fake_manager):
    # Live-verified: a bare in:C0123ABCD returns 0 hits; in:<#C0123ABCD> works
    assert await _query_for(fake_manager, in_channel="C0123ABCD") == "x in:<#C0123ABCD>"
    assert await _query_for(fake_manager, in_channel="G0123ABCD") == "x in:<#G0123ABCD>"
    # DM IDs are IDs too, not channel names
    assert await _query_for(fake_manager, in_channel="D0123ABCD") == "x in:<#D0123ABCD>"


async def test_in_channel_names_starting_with_id_letters_stay_names(fake_client, fake_manager):
    assert await _query_for(fake_manager, in_channel="design") == "x in:#design"
    assert await _query_for(fake_manager, in_channel="general") == "x in:#general"
    # Uppercase-initial names used to misfire as IDs
    assert await _query_for(fake_manager, in_channel="General") == "x in:#General"
    assert await _query_for(fake_manager, in_channel="Customers") == "x in:#Customers"
    assert await _query_for(fake_manager, in_channel="DEVOPS") == "x in:#DEVOPS"
    assert await _query_for(fake_manager, in_channel="@alice") == "x in:@alice"


async def test_from_user_ids_use_mention_form(fake_client, fake_manager):
    assert await _query_for(fake_manager, from_user="U0123ABCD") == "x from:<@U0123ABCD>"
    # Enterprise Grid user IDs
    assert await _query_for(fake_manager, from_user="W0123ABCD") == "x from:<@W0123ABCD>"


async def test_from_user_names_starting_with_id_letters_stay_names(fake_client, fake_manager):
    assert await _query_for(fake_manager, from_user="will") == "x from:@will"
    assert await _query_for(fake_manager, from_user="Uma") == "x from:@Uma"
    assert await _query_for(fake_manager, from_user="WALTER") == "x from:@WALTER"
    assert await _query_for(fake_manager, from_user="@bob") == "x from:@bob"


# --- thread_ts recovered from permalink -------------------------------------


def _one_match(fake_client, match):
    fake_client.responses["search.messages"] = {
        "ok": True,
        "messages": {"matches": [match], "paging": {"total": 1, "page": 1, "pages": 1}},
    }


async def test_thread_ts_falls_back_to_permalink(fake_client, fake_manager):
    _one_match(fake_client, {
        "ts": "1700000200.000200",
        "text": "reply",
        "channel": {"id": "C100"},
        "permalink": "https://team.slack.com/archives/C100/p1700000200000200"
                     "?thread_ts=1700000100.000100&cid=C100",
    })

    hit = (await search(fake_manager, query="reply"))["results"][0]

    assert hit["thread_ts"] == "1700000100.000100"
    assert hit["is_thread_reply"] is True


async def test_permalink_thread_ts_equal_to_ts_is_root(fake_client, fake_manager):
    _one_match(fake_client, {
        "ts": "1700000100.000100",
        "text": "root",
        "permalink": "https://team.slack.com/archives/C100/p1700000100000100"
                     "?thread_ts=1700000100.000100",
    })

    hit = (await search(fake_manager, query="root"))["results"][0]

    assert hit["thread_ts"] == "1700000100.000100"
    assert hit["is_thread_reply"] is False


async def test_explicit_thread_ts_wins_over_permalink(fake_client, fake_manager):
    _one_match(fake_client, {
        "ts": "1700000200.000200",
        "thread_ts": "1700000100.000100",
        "text": "reply",
        "permalink": "https://team.slack.com/archives/C100/p1700000200000200"
                     "?thread_ts=1699999999.000000",
    })

    hit = (await search(fake_manager, query="reply"))["results"][0]

    assert hit["thread_ts"] == "1700000100.000100"


async def test_permalink_without_thread_ts_adds_no_thread_fields(fake_client, fake_manager):
    _one_match(fake_client, {
        "ts": "1700000200.000200",
        "text": "plain",
        "permalink": "https://team.slack.com/archives/C100/p1700000200000200?cid=C100",
    })

    hit = (await search(fake_manager, query="plain"))["results"][0]

    assert "thread_ts" not in hit
    assert "is_thread_reply" not in hit
