"""Tests for slack_find_conversation name matching and resolution."""

from slack_stealth_mcp.tools.find_conversation import find_conversation, score_name


class TestScoreName:
    def test_exact_match(self):
        assert score_name("general", "general") == 100
        assert score_name("General", "general") == 100
        assert score_name("design team", "design-team") == 100

    def test_prefix_match(self):
        assert score_name("design", "design-team") == 80

    def test_all_tokens_match(self):
        assert score_name("bob jones", "Bob Jones") >= 60

    def test_whole_word_match(self):
        # "team" appears as a whole word in "design-team"
        assert score_name("team", "design-team") == 60

    def test_substring_match(self):
        # "sign" is a substring but not a whole word
        assert score_name("sign", "design-team") == 40

    def test_no_match(self):
        assert score_name("engineering", "design-team") == 0

    def test_empty(self):
        assert score_name("", "general") == 0
        assert score_name("general", "") == 0


async def test_finds_channel_by_name(fake_manager):
    result = await find_conversation(fake_manager, query="general")
    assert result["count"] >= 1
    top = result["matches"][0]
    assert top["id"] == "C100"
    assert top["type"] == "channel"
    assert top["score"] == 100


async def test_finds_private_channel_by_prefix(fake_manager):
    result = await find_conversation(fake_manager, query="design")
    ids = [m["id"] for m in result["matches"]]
    assert "C200" in ids
    match = next(m for m in result["matches"] if m["id"] == "C200")
    assert match["type"] == "private_channel"


async def test_finds_dm_by_person_name(fake_manager):
    result = await find_conversation(fake_manager, query="alice")
    top = result["matches"][0]
    assert top["id"] == "D01"
    assert top["type"] == "dm"
    assert top["user_id"] == "U01"
    assert top["name"] == "@alice"


async def test_dm_outranks_group_dm_for_single_person(fake_manager):
    result = await find_conversation(fake_manager, query="alice")
    ids = [m["id"] for m in result["matches"]]
    assert ids.index("D01") < ids.index("G01")


async def test_finds_dm_by_real_name(fake_manager):
    result = await find_conversation(fake_manager, query="bob jones")
    ids = [m["id"] for m in result["matches"]]
    assert "D02" in ids


async def test_group_dm_participants_resolved(fake_manager):
    result = await find_conversation(fake_manager, query="alice")
    group = next(m for m in result["matches"] if m["id"] == "G01")
    assert group["type"] == "group_dm"
    # mpdm-alice--bob.jones--carol-1 resolved to display names
    assert group["name"] == "alice, bobby, Carol White"


async def test_user_without_dm_matched_with_hint(fake_manager):
    result = await find_conversation(fake_manager, query="carol")
    users = [m for m in result["matches"] if m["type"] == "user"]
    assert len(users) == 1
    assert users[0]["id"] == "U03"
    assert "slack_reply" in users[0]["hint"]


async def test_bots_excluded_from_user_matches(fake_manager):
    result = await find_conversation(fake_manager, query="bot")
    assert all(m["id"] != "UBOT" for m in result["matches"])


async def test_include_users_false(fake_manager):
    result = await find_conversation(fake_manager, query="carol", include_users=False)
    assert all(m["type"] != "user" for m in result["matches"])


async def test_limit_respected(fake_manager):
    result = await find_conversation(fake_manager, query="a", limit=2)
    assert len(result["matches"]) <= 2


async def test_no_matches_gives_hint(fake_manager):
    result = await find_conversation(fake_manager, query="zzzznonexistent")
    assert result["count"] == 0
    assert "hint" in result


async def test_directory_failure_warns_but_still_matches_channels(fake_client, fake_manager):
    fake_client.responses["users.list"] = _raise_api_error
    result = await find_conversation(fake_manager, query="general")
    assert result["matches"][0]["id"] == "C100"
    assert "warning" in result


def _raise_api_error(method, endpoint, kwargs):
    from slack_stealth_mcp.slack_client import SlackAPIError

    raise SlackAPIError("access_denied")
