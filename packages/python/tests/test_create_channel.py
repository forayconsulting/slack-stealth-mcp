"""Tests for slack_create_channel: name normalization, follow-up steps, errors."""

from slack_stealth_mcp.slack_client import SlackAPIError
from slack_stealth_mcp.tools.create_channel import create_channel, normalize_channel_name


def _install_create(fake_client):
    sent = {}

    def create(method, endpoint, kwargs):
        sent.update(kwargs["data"])
        return {
            "ok": True,
            "channel": {
                "id": "C999",
                "name": kwargs["data"]["name"],
                "is_channel": True,
                "is_private": kwargs["data"]["is_private"] == "true",
            },
        }

    fake_client.responses["conversations.create"] = create
    return sent


def test_normalize_channel_name():
    assert normalize_channel_name("Q4 Planning!") == "q4-planning"
    assert normalize_channel_name("#proj_alpha") == "proj_alpha"
    assert normalize_channel_name("  a  --  b ") == "a-b"
    assert normalize_channel_name("x" * 100) == "x" * 80
    assert normalize_channel_name("!!!") == ""


async def test_create_public_channel(fake_client, fake_manager):
    sent = _install_create(fake_client)

    result = await create_channel(fake_manager, name="Q4 Planning")

    assert result["ok"] is True
    assert result["channel_id"] == "C999"
    assert result["name"] == "q4-planning"
    assert result["requested_name"] == "Q4 Planning"
    assert sent == {"name": "q4-planning", "is_private": "false"}
    assert "warnings" not in result
    assert fake_client.endpoints_called == ["conversations.create"]


async def test_create_private_with_extras(fake_client, fake_manager):
    sent = _install_create(fake_client)
    fake_client.responses["conversations.setPurpose"] = {"ok": True}
    fake_client.responses["conversations.setTopic"] = {"ok": True}
    invited = {}

    def invite(method, endpoint, kwargs):
        invited.update(kwargs["data"])
        return {"ok": True, "channel": {"id": "C999"}}

    fake_client.responses["conversations.invite"] = invite

    result = await create_channel(
        fake_manager,
        name="secret-proj",
        is_private=True,
        description="Planning",
        topic="Kickoff Monday",
        invite=["U01", "<@U02>"],
    )

    assert result["ok"] is True
    assert sent["is_private"] == "true"
    assert result["description"] == "Planning"
    assert result["topic"] == "Kickoff Monday"
    assert result["invited"] == ["U01", "U02"]
    assert invited == {"channel": "C999", "users": "U01,U02", "force": "true"}
    assert "requested_name" not in result


async def test_partial_invite_failure_is_a_warning(fake_client, fake_manager):
    _install_create(fake_client)
    fake_client.responses["conversations.invite"] = {
        "ok": True,
        "errors": [{"user": "U02", "ok": False, "error": "user_not_found"}],
    }

    result = await create_channel(fake_manager, name="x", invite="U01, U02")

    assert result["ok"] is True
    assert result["invited"] == ["U01"]
    assert result["warnings"] == ["could not invite U02: user_not_found"]


async def test_followup_failure_keeps_channel(fake_client, fake_manager):
    _install_create(fake_client)

    def fail(method, endpoint, kwargs):
        raise SlackAPIError("not_allowed")

    fake_client.responses["conversations.setPurpose"] = fail

    result = await create_channel(fake_manager, name="x", description="d")

    assert result["ok"] is True
    assert result["channel_id"] == "C999"
    assert result["warnings"] == ["description not set: not_allowed"]


async def test_name_taken(fake_client, fake_manager):
    def taken(method, endpoint, kwargs):
        raise SlackAPIError("name_taken")

    fake_client.responses["conversations.create"] = taken

    result = await create_channel(fake_manager, name="general")

    assert result["ok"] is False
    assert "name_taken" in result["error"]
    assert "already exists" in result["error"]


async def test_rejects_bad_input_before_creating(fake_client, fake_manager):
    _install_create(fake_client)

    bad_name = await create_channel(fake_manager, name="!!!")
    bad_invite = await create_channel(fake_manager, name="ok", invite=["alice"])

    assert bad_name["ok"] is False
    assert bad_invite["ok"] is False
    assert "slack_find_conversation" in bad_invite["error"]
    assert "conversations.create" not in fake_client.endpoints_called
