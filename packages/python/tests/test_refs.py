"""Tests for channel reference resolution (IDs and Slack URLs)."""

import pytest

from slack_stealth_mcp.refs import resolve_channel_ref


def test_raw_channel_id_passthrough():
    assert resolve_channel_ref("C0123456789").channel_id == "C0123456789"
    assert resolve_channel_ref("D0123456789").channel_id == "D0123456789"
    assert resolve_channel_ref("G0123456789").channel_id == "G0123456789"
    assert resolve_channel_ref("U0123456789").channel_id == "U0123456789"
    assert resolve_channel_ref("W0123456789").channel_id == "W0123456789"


def test_archives_url_channel_only():
    ref = resolve_channel_ref("https://myteam.slack.com/archives/C0123456789")
    assert ref.channel_id == "C0123456789"
    assert ref.message_ts is None


def test_archives_url_with_message_ts():
    ref = resolve_channel_ref(
        "https://myteam.slack.com/archives/C0123456789/p1700000000123456"
    )
    assert ref.channel_id == "C0123456789"
    assert ref.message_ts == "1700000000.123456"


def test_enterprise_grid_archives_url():
    ref = resolve_channel_ref(
        "https://myteam.enterprise.slack.com/archives/C0123456789"
    )
    assert ref.channel_id == "C0123456789"


def test_app_client_url():
    ref = resolve_channel_ref(
        "https://app.slack.com/client/T0123456789/C0123456789"
    )
    assert ref.channel_id == "C0123456789"
    assert ref.message_ts is None


def test_app_client_thread_url():
    ref = resolve_channel_ref(
        "https://app.slack.com/client/T0123456789/C0123456789/thread/C0123456789-1700000000.123456"
    )
    assert ref.channel_id == "C0123456789"
    assert ref.message_ts == "1700000000.123456"


def test_whitespace_and_angle_brackets_stripped():
    ref = resolve_channel_ref("  <https://myteam.slack.com/archives/C0123456789>  ")
    assert ref.channel_id == "C0123456789"


def test_unresolvable_reference_raises():
    with pytest.raises(ValueError, match="slack_find_conversation"):
        resolve_channel_ref("the design channel")
