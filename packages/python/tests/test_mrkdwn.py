"""Tests for Markdown -> Slack mrkdwn normalization."""

from slack_stealth_mcp.mrkdwn import normalize_mrkdwn


def test_double_asterisk_bold():
    assert normalize_mrkdwn("this is **bold** text") == "this is *bold* text"


def test_double_underscore_bold():
    assert normalize_mrkdwn("this is __bold__ text") == "this is *bold* text"


def test_markdown_link():
    assert (
        normalize_mrkdwn("see [the docs](https://example.com/docs) here")
        == "see <https://example.com/docs|the docs> here"
    )


def test_headers_become_bold_lines():
    assert normalize_mrkdwn("# Title\nbody") == "*Title*\nbody"
    assert normalize_mrkdwn("### Sub Heading\nbody") == "*Sub Heading*\nbody"


def test_code_fences_preserved():
    text = "before\n```\n**not bold** [x](https://a.com)\n```\nafter **bold**"
    result = normalize_mrkdwn(text)
    assert "```\n**not bold** [x](https://a.com)\n```" in result
    assert result.endswith("after *bold*")


def test_inline_code_preserved():
    text = "use `**kwargs` and **bold**"
    assert normalize_mrkdwn(text) == "use `**kwargs` and *bold*"


def test_existing_mrkdwn_untouched():
    text = "*bold* _italic_ <@U0123456> <!here> <#C0123456> <https://a.com|link>"
    assert normalize_mrkdwn(text) == text


def test_idempotent():
    text = "## Head\n**bold** [x](https://a.com) `code` and *done*"
    once = normalize_mrkdwn(text)
    assert normalize_mrkdwn(once) == once


def test_non_http_brackets_untouched():
    # [0](1) style text that is not a link must not be rewritten
    assert normalize_mrkdwn("array[0](see note)") == "array[0](see note)"
