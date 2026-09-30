"""Convert Slack mrkdwn to Block Kit rich_text.

Scheduled messages go through Slack's internal drafts API, which stores the
body as rich_text blocks rather than a mrkdwn string. Slack Desktop also
discards drafts without a non-empty rich_text block, so every message is
emitted as a single rich_text block.

Handles the subset of mrkdwn the rest of this server encourages: *bold*,
_italic_, ~strike~, `code`, ```code blocks```, > quotes, - / • / 1. lists,
<url|text> links, <@U...> / <#C...> / <!here> mentions, and :emoji:.
"""

from __future__ import annotations

import html
import re
from typing import Any

from .mrkdwn import normalize_mrkdwn

Element = dict[str, Any]

_FENCE_RE = re.compile(r"```\n?(.*?)\n?```", re.DOTALL)
_QUOTE_RE = re.compile(r"^(?:>|&gt;) ?(.*)$")
_BULLET_RE = re.compile(r"^( *)(?:[-•]|\*(?= )) +(.*)$")
_ORDERED_RE = re.compile(r"^( *)\d+[.)] +(.*)$")

# Inline tokens, in priority order. Style markers must sit on a word
# boundary, matching how Slack itself parses mrkdwn.
_INLINE_RE = re.compile(
    r"(?P<angle><(?P<angle_body>[^<>\n]+)>)"
    r"|(?P<code>`(?P<code_body>[^`\n]+)`)"
    r"|(?P<bold>(?<![\w*])\*(?! )(?P<bold_body>[^*\n]+?)(?<! )\*(?![\w*]))"
    r"|(?P<italic>(?<![\w_])_(?! )(?P<italic_body>[^_\n]+?)(?<! )_(?![\w_]))"
    r"|(?P<strike>(?<![\w~])~(?! )(?P<strike_body>[^~\n]+?)(?<! )~(?![\w~]))"
    r"|(?P<emoji>(?<![\w:]):(?P<emoji_name>[a-z0-9_+'-]+):"
    r"(?::skin-tone-(?P<skin>[2-6]):)?(?![\w:]))"
)

_BROADCASTS = {"here", "channel", "everyone"}


def _text(value: str, style: dict[str, bool]) -> Element:
    element: Element = {"type": "text", "text": html.unescape(value)}
    if style:
        element["style"] = dict(style)
    return element


def _angle_element(body: str, style: dict[str, bool]) -> Element | None:
    """Parse the inside of a <...> token, or None if it isn't an entity."""
    target, _, label = body.partition("|")
    if target.startswith("@") and len(target) > 1:
        return {"type": "user", "user_id": target[1:]}
    if target.startswith("#") and len(target) > 1:
        return {"type": "channel", "channel_id": target[1:]}
    if target.startswith("!subteam^"):
        return {"type": "usergroup", "usergroup_id": target[len("!subteam^"):]}
    if target.startswith("!") and target[1:] in _BROADCASTS:
        return {"type": "broadcast", "range": target[1:]}
    if re.match(r"^(https?|mailto):", target):
        element: Element = {"type": "link", "url": html.unescape(target)}
        if label:
            element["text"] = html.unescape(label)
        if style:
            element["style"] = dict(style)
        return element
    return None


def _parse_inline(text: str, style: dict[str, bool] | None = None) -> list[Element]:
    """Parse one run of inline mrkdwn into rich_text elements."""
    style = style or {}
    elements: list[Element] = []
    pos = 0

    for match in _INLINE_RE.finditer(text):
        kind = match.lastgroup
        if kind == "angle":
            entity = _angle_element(match.group("angle_body"), style)
            if entity is None:
                continue  # leave unknown <...> as literal text
        if match.start() > pos:
            elements.append(_text(text[pos:match.start()], style))

        if kind == "angle":
            elements.append(entity)
        elif kind == "code":
            elements.append(_text(match.group("code_body"), {**style, "code": True}))
        elif kind in ("bold", "italic", "strike"):
            inner_style = {**style, kind: True}
            elements.extend(_parse_inline(match.group(f"{kind}_body"), inner_style))
        elif kind == "emoji":
            emoji: Element = {"type": "emoji", "name": match.group("emoji_name")}
            if match.group("skin"):
                emoji["skin_tone"] = int(match.group("skin"))
            elements.append(emoji)
        pos = match.end()

    if pos < len(text):
        elements.append(_text(text[pos:], style))
    return _merge_text(elements)


def _merge_text(elements: list[Element]) -> list[Element]:
    """Join adjacent text elements that share a style."""
    merged: list[Element] = []
    for element in elements:
        prev = merged[-1] if merged else None
        if (
            prev is not None
            and element["type"] == "text"
            and prev["type"] == "text"
            and prev.get("style") == element.get("style")
        ):
            prev["text"] += element["text"]
        else:
            merged.append(element)
    return merged


def _parse_lines(lines: list[str]) -> list[Element]:
    """Group non-code lines into sections, quotes, and lists."""
    blocks: list[Element] = []
    section: list[str] = []
    quote: list[str] = []
    list_items: list[str] = []
    list_style = ""
    list_indent = 0

    def flush_section() -> None:
        if section:
            blocks.append(
                {"type": "rich_text_section", "elements": _parse_inline("\n".join(section))}
            )
            section.clear()

    def flush_quote() -> None:
        if quote:
            blocks.append(
                {"type": "rich_text_quote", "elements": _parse_inline("\n".join(quote))}
            )
            quote.clear()

    def flush_list() -> None:
        if list_items:
            blocks.append(
                {
                    "type": "rich_text_list",
                    "style": list_style,
                    "indent": list_indent,
                    "elements": [
                        {"type": "rich_text_section", "elements": _parse_inline(item)}
                        for item in list_items
                    ],
                }
            )
            list_items.clear()

    for line in lines:
        quote_match = _QUOTE_RE.match(line)
        list_match = _BULLET_RE.match(line) or _ORDERED_RE.match(line)

        if quote_match:
            flush_section()
            flush_list()
            quote.append(quote_match.group(1))
        elif list_match:
            flush_section()
            flush_quote()
            style = "bullet" if _BULLET_RE.match(line) else "ordered"
            indent = min(len(list_match.group(1)) // 2, 8)
            if list_items and (style != list_style or indent != list_indent):
                flush_list()
            list_style, list_indent = style, indent
            list_items.append(list_match.group(2))
        else:
            flush_quote()
            flush_list()
            section.append(line)

    flush_section()
    flush_quote()
    flush_list()
    return blocks


def _trim_blank_lines(lines: list[str]) -> list[str]:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def mrkdwn_to_rich_text(text: str) -> list[Element]:
    """Convert a mrkdwn message to a list holding one rich_text block.

    Stray standard Markdown is normalized first, so **bold** and [text](url)
    come out the same as they would through slack_reply.
    """
    text = normalize_mrkdwn(text)
    elements: list[Element] = []
    pos = 0

    for match in _FENCE_RE.finditer(text):
        elements.extend(_parse_lines(_trim_blank_lines(text[pos:match.start()].split("\n"))))
        code = match.group(1)
        if code:
            elements.append(
                {
                    "type": "rich_text_preformatted",
                    "elements": [{"type": "text", "text": html.unescape(code)}],
                }
            )
        pos = match.end()

    elements.extend(_parse_lines(_trim_blank_lines(text[pos:].split("\n"))))
    elements = [e for e in elements if e.get("elements")]
    return [{"type": "rich_text", "elements": elements}]


def rich_text_to_plain(blocks: list[Element]) -> str:
    """Flatten rich_text blocks to readable text (for previews)."""
    lines: list[str] = []

    def inline(elements: list[Element]) -> str:
        parts: list[str] = []
        for element in elements:
            kind = element.get("type")
            if kind == "text":
                parts.append(element.get("text", ""))
            elif kind == "link":
                parts.append(element.get("text") or element.get("url", ""))
            elif kind == "user":
                parts.append(f"<@{element.get('user_id', '')}>")
            elif kind == "channel":
                parts.append(f"<#{element.get('channel_id', '')}>")
            elif kind == "broadcast":
                parts.append(f"<!{element.get('range', '')}>")
            elif kind == "usergroup":
                parts.append(f"<!subteam^{element.get('usergroup_id', '')}>")
            elif kind == "emoji":
                parts.append(f":{element.get('name', '')}:")
        return "".join(parts)

    for block in blocks:
        for element in block.get("elements", []):
            kind = element.get("type")
            children = element.get("elements", [])
            if kind == "rich_text_list":
                pad = "  " * element.get("indent", 0)
                for item in children:
                    lines.append(f"{pad}- {inline(item.get('elements', []))}")
            elif kind == "rich_text_quote":
                lines.extend(f"> {line}" for line in inline(children).split("\n"))
            elif kind == "rich_text_preformatted":
                lines.append(f"```{inline(children)}```")
            else:
                lines.append(inline(children))
    return "\n".join(lines).strip()
