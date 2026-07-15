"""Normalize standard Markdown to Slack mrkdwn.

LLMs habitually emit GitHub-style Markdown (**bold**, [text](url), ## headers)
which Slack renders literally. This module conservatively converts the common
mistakes to mrkdwn while leaving code regions and already-correct mrkdwn
(<@U...>, <!here>, <url|text>) untouched. The conversion is idempotent.
"""

from __future__ import annotations

import re

# Fenced code blocks (```...```) and inline code (`...`) must not be rewritten
_CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_BOLD_UNDERSCORE_RE = re.compile(r"__(.+?)__", re.DOTALL)
# Only convert real links; <url|text> output won't re-match
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_HEADER_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)


def _convert_segment(text: str) -> str:
    """Convert one non-code segment of text to mrkdwn."""
    text = _BOLD_RE.sub(r"*\1*", text)
    text = _BOLD_UNDERSCORE_RE.sub(r"*\1*", text)
    text = _LINK_RE.sub(r"<\2|\1>", text)
    text = _HEADER_RE.sub(r"*\1*", text)
    return text


def normalize_mrkdwn(text: str) -> str:
    """Convert common Markdown patterns to Slack mrkdwn.

    Code blocks and inline code spans are preserved verbatim.
    """
    result: list[str] = []
    last_end = 0

    for match in _CODE_RE.finditer(text):
        result.append(_convert_segment(text[last_end:match.start()]))
        result.append(match.group(0))
        last_end = match.end()

    result.append(_convert_segment(text[last_end:]))
    return "".join(result)
