"""Regression: SlackMessage must carry the channel from search results.

Before the fix, SlackMessage had no channel field, so mention filtering in
get_unread raised AttributeError (silently swallowed) and mentions were
always empty.
"""

from slack_stealth_mcp.types import SlackMessage


def test_parse_message_with_channel_object(fake_client):
    msg = fake_client._parse_message({
        "ts": "1700000150.000100",
        "text": "<@U0ME> ping",
        "user": "U01",
        "channel": {"id": "C100", "name": "general"},
    })
    assert msg.channel_id == "C100"
    assert msg.channel["name"] == "general"


def test_parse_message_with_bare_channel_string(fake_client):
    msg = fake_client._parse_message({
        "ts": "1700000150.000100",
        "text": "hi",
        "channel": "C100",
    })
    assert msg.channel_id == "C100"


def test_parse_message_without_channel(fake_client):
    msg = fake_client._parse_message({"ts": "1700000150.000100", "text": "hi"})
    assert msg.channel_id is None


def test_channel_id_property_default():
    msg = SlackMessage(ts="1700000150.000100", text="hi")
    assert msg.channel_id is None
