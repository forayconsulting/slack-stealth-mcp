"""Tests for scheduled messages via the drafts API."""

import json
import time

from slack_stealth_mcp.tools.schedule import (
    cancel_scheduled,
    list_scheduled,
    parse_send_at,
    schedule_message,
)
from slack_stealth_mcp.slack_client import SlackAPIError


def _install_create(fake_client):
    sent = {}

    def create(method, endpoint, kwargs):
        sent.update(kwargs.get("data", {}))
        return {"ok": True, "draft": {"id": "Dr01", "date_scheduled": int(sent["date_scheduled"])}}

    fake_client.responses["drafts.create"] = create
    return sent


def _draft(draft_id, when, **extra):
    draft = {
        "id": draft_id,
        "date_scheduled": when,
        "is_sent": False,
        "is_deleted": False,
        "last_updated_ts": "1700000000.123456",
        "destinations": [{"channel_id": "C100"}],
        "blocks": [
            {
                "type": "rich_text",
                "elements": [
                    {"type": "rich_text_section", "elements": [{"type": "text", "text": "hi"}]}
                ],
            }
        ],
    }
    draft.update(extra)
    return draft


def test_parse_send_at():
    assert parse_send_at(1800000000) == 1800000000
    assert parse_send_at("1800000000") == 1800000000
    assert parse_send_at("2027-01-15T09:00:00-05:00") == 1800021600
    assert parse_send_at("2027-01-15T14:00:00Z") == 1800021600


async def test_schedule_payload(fake_client, fake_manager):
    sent = _install_create(fake_client)
    when = int(time.time()) + 3600

    result = await schedule_message(
        fake_manager, target="C100", message="**hi** <@U01>", send_at=when
    )

    assert result["ok"] is True
    assert result["scheduled_id"] == "Dr01"
    assert result["send_at_unix"] == when
    assert result["sent_to"] == "general"
    assert sent["date_scheduled"] == str(when)
    assert sent["is_from_composer"] == "true"
    assert json.loads(sent["destinations"]) == [{"channel_id": "C100"}]
    blocks = json.loads(sent["blocks"])
    assert blocks[0]["type"] == "rich_text"
    assert blocks[0]["elements"][0]["elements"][0] == {
        "type": "text",
        "text": "hi",
        "style": {"bold": True},
    }
    # lowercase UUIDv4
    msg_id = sent["client_msg_id"]
    assert msg_id == msg_id.lower() and msg_id[14] == "4"


async def test_schedule_thread_from_url(fake_client, fake_manager):
    sent = _install_create(fake_client)

    result = await schedule_message(
        fake_manager,
        target="https://team.slack.com/archives/C100/p1700000000123456",
        message="later",
        send_at=int(time.time()) + 600,
    )

    assert result["ok"] is True
    assert json.loads(sent["destinations"]) == [
        {"channel_id": "C100", "thread_ts": "1700000000.123456"}
    ]


async def test_schedule_opens_dm_for_user(fake_client, fake_manager):
    sent = _install_create(fake_client)
    fake_client.responses["conversations.open"] = {"ok": True, "channel": {"id": "D01"}}

    result = await schedule_message(
        fake_manager, target="U01", message="hey", send_at=int(time.time()) + 600
    )

    assert result["ok"] is True
    assert json.loads(sent["destinations"]) == [{"channel_id": "D01"}]


async def test_schedule_rejects_bad_times(fake_client, fake_manager):
    _install_create(fake_client)
    now = int(time.time())

    past = await schedule_message(fake_manager, target="C100", message="x", send_at=now - 60)
    far = await schedule_message(
        fake_manager, target="C100", message="x", send_at=now + 121 * 86400
    )
    junk = await schedule_message(fake_manager, target="C100", message="x", send_at="soon")

    assert not past["ok"] and "future" in past["error"]
    assert not far["ok"] and "120 days" in far["error"]
    assert not junk["ok"] and "ISO-8601" in junk["error"]
    assert "drafts.create" not in fake_client.endpoints_called


async def test_schedule_rejects_empty_message(fake_client, fake_manager):
    result = await schedule_message(
        fake_manager, target="C100", message="  ", send_at=int(time.time()) + 600
    )
    assert result["ok"] is False


async def test_schedule_explains_attached_draft(fake_client, fake_manager):
    def create(method, endpoint, kwargs):
        raise SlackAPIError("attached_draft_exists")

    fake_client.responses["drafts.create"] = create
    result = await schedule_message(
        fake_manager, target="C100", message="x", send_at=int(time.time()) + 600
    )
    assert result["ok"] is False
    assert "thread" in result["error"]


async def test_list_filters_and_sorts(fake_client, fake_manager):
    now = int(time.time())
    fake_client.responses["drafts.list"] = {
        "ok": True,
        "drafts": [
            _draft("Dr2", now + 7200),
            _draft("Dr1", now + 600, destinations=[{"channel_id": "C100", "thread_ts": "1.2"}]),
            _draft("DrTyped", 0),  # ordinary draft, not scheduled
            _draft("DrSent", now - 60, is_sent=True),
        ],
    }

    result = await list_scheduled(fake_manager)

    assert result["ok"] is True
    assert [s["scheduled_id"] for s in result["scheduled"]] == ["Dr1", "Dr2"]
    assert result["scheduled"][0]["thread_ts"] == "1.2"
    assert result["scheduled"][0]["text"] == "hi"
    assert result["scheduled"][0]["sent_to"] == "general"


async def test_cancel_pads_timestamp(fake_client, fake_manager):
    fake_client.responses["drafts.list"] = {
        "ok": True,
        "drafts": [_draft("Dr1", int(time.time()) + 600)],
    }
    deleted = []
    fake_client.responses["drafts.delete"] = lambda m, e, kw: (
        deleted.append(kw["data"]) or {"ok": True}
    )

    result = await cancel_scheduled(fake_manager, scheduled_id="Dr1")

    assert result["ok"] is True
    assert deleted == [{"draft_id": "Dr1", "client_last_updated_ts": "1700000000.1234560"}]


async def test_cancel_retries_conflict_with_current_time(fake_client, fake_manager):
    fake_client.responses["drafts.list"] = {
        "ok": True,
        "drafts": [_draft("Dr1", int(time.time()) + 600)],
    }
    attempts = []

    def delete(method, endpoint, kwargs):
        attempts.append(kwargs["data"]["client_last_updated_ts"])
        if len(attempts) == 1:
            raise SlackAPIError("draft_has_conflict")
        return {"ok": True}

    fake_client.responses["drafts.delete"] = delete

    result = await cancel_scheduled(fake_manager, scheduled_id="Dr1")

    assert result["ok"] is True
    assert len(attempts) == 2 and float(attempts[1]) > 1700000000.2


async def test_cancel_refuses_ordinary_drafts(fake_client, fake_manager):
    fake_client.responses["drafts.list"] = {"ok": True, "drafts": [_draft("DrTyped", 0)]}

    result = await cancel_scheduled(fake_manager, scheduled_id="DrTyped")

    assert result["ok"] is False
    assert "drafts.delete" not in fake_client.endpoints_called
