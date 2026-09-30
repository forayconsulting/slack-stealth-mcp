"""Tests for mrkdwn -> rich_text conversion used by scheduled messages."""

from slack_stealth_mcp.rich_text import mrkdwn_to_rich_text, rich_text_to_plain


def _elements(text):
    blocks = mrkdwn_to_rich_text(text)
    assert len(blocks) == 1 and blocks[0]["type"] == "rich_text"
    return blocks[0]["elements"]


def _inline(text):
    elements = _elements(text)
    assert len(elements) == 1 and elements[0]["type"] == "rich_text_section"
    return elements[0]["elements"]


def test_plain_text_is_one_section():
    assert _inline("hello world") == [{"type": "text", "text": "hello world"}]


def test_styles():
    assert _inline("*b* _i_ ~s~ `c`") == [
        {"type": "text", "text": "b", "style": {"bold": True}},
        {"type": "text", "text": " "},
        {"type": "text", "text": "i", "style": {"italic": True}},
        {"type": "text", "text": " "},
        {"type": "text", "text": "s", "style": {"strike": True}},
        {"type": "text", "text": " "},
        {"type": "text", "text": "c", "style": {"code": True}},
    ]


def test_nested_styles():
    assert _inline("*_both_*") == [
        {"type": "text", "text": "both", "style": {"bold": True, "italic": True}}
    ]


def test_markers_inside_words_are_literal():
    assert _inline("snake_case_name and 2*3*4") == [
        {"type": "text", "text": "snake_case_name and 2*3*4"}
    ]


def test_standard_markdown_is_normalized_first():
    assert _inline("**bold** [docs](https://x.com)") == [
        {"type": "text", "text": "bold", "style": {"bold": True}},
        {"type": "text", "text": " "},
        {"type": "link", "url": "https://x.com", "text": "docs"},
    ]


def test_entities():
    assert _inline("<!here> <@U01> <#C100|general> <!subteam^S1> <https://a.io>") == [
        {"type": "broadcast", "range": "here"},
        {"type": "text", "text": " "},
        {"type": "user", "user_id": "U01"},
        {"type": "text", "text": " "},
        {"type": "channel", "channel_id": "C100"},
        {"type": "text", "text": " "},
        {"type": "usergroup", "usergroup_id": "S1"},
        {"type": "text", "text": " "},
        {"type": "link", "url": "https://a.io"},
    ]


def test_html_entities_are_unescaped():
    assert _inline("a &amp; b &lt;3 <https://x.com/?a=1&amp;b=2|q>") == [
        {"type": "text", "text": "a & b <3 "},
        {"type": "link", "url": "https://x.com/?a=1&b=2", "text": "q"},
    ]


def test_emoji_and_times():
    assert _inline("at 10:30:45 :thumbsup::skin-tone-3:") == [
        {"type": "text", "text": "at 10:30:45 "},
        {"type": "emoji", "name": "thumbsup", "skin_tone": 3},
    ]


def test_lists_quotes_and_code():
    elements = _elements(
        "Intro\n- one\n- *two*\n  - nested\n1. first\n> quoted\n```\nx = *1*\n```\nend"
    )
    assert [e["type"] for e in elements] == [
        "rich_text_section",
        "rich_text_list",
        "rich_text_list",
        "rich_text_list",
        "rich_text_quote",
        "rich_text_preformatted",
        "rich_text_section",
    ]
    bullets, nested, ordered = elements[1], elements[2], elements[3]
    assert (bullets["style"], bullets["indent"], len(bullets["elements"])) == ("bullet", 0, 2)
    assert (nested["style"], nested["indent"]) == ("bullet", 1)
    assert ordered["style"] == "ordered"
    # code is verbatim, not styled
    assert elements[5]["elements"] == [{"type": "text", "text": "x = *1*"}]


def test_multiline_paragraph_keeps_newlines():
    assert _inline("line one\n\nline two") == [
        {"type": "text", "text": "line one\n\nline two"}
    ]


def test_bold_line_is_not_a_bullet():
    assert _elements("*Status*\nall good")[0]["type"] == "rich_text_section"


def test_plain_preview_round_trip():
    text = "Hi <@U01>\n- a\n  - b\n> q"
    assert rich_text_to_plain(mrkdwn_to_rich_text(text)) == text
