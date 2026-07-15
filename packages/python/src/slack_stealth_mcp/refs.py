"""Resolve channel references (IDs or Slack URLs) to channel IDs.

Users often paste links copied from Slack ("Copy link" on a channel or
message) instead of raw channel IDs. This module turns any supported
reference into a channel ID plus an optional message timestamp, with no
network calls.
"""

from __future__ import annotations

import re
from typing import NamedTuple, Optional


class ChannelRef(NamedTuple):
    """A resolved channel reference."""

    channel_id: str
    message_ts: Optional[str] = None


# https://workspace.slack.com/archives/C0123456789
# https://workspace.slack.com/archives/C0123456789/p1700000000123456
# also matches workspace.enterprise.slack.com
_ARCHIVES_RE = re.compile(
    r"https?://[^/\s]*slack\.com/archives/([A-Z][A-Z0-9]+)(?:/p(\d{16,}))?",
    re.IGNORECASE,
)

# Enterprise Grid / app client URLs:
# https://app.slack.com/client/T0123456789/C0123456789
# optionally followed by /thread/C0123456789-1700000000.123456 or a message ts
_CLIENT_RE = re.compile(
    r"https?://app\.slack\.com/client/[TE][A-Z0-9]+/([A-Z][A-Z0-9]+)(?:/thread/[A-Z][A-Z0-9]+-(\d+\.\d+))?",
    re.IGNORECASE,
)

# Raw conversation or user IDs: C/D/G channels, U/W users.
# The trailing length is deliberately permissive - this branch is a
# passthrough, and its job is only to distinguish an ID-shaped token from a
# natural-language name (which has spaces/lowercase). A malformed token just
# yields a clean not_found from Slack, so there is no need to police length.
_RAW_ID_RE = re.compile(r"^[CDGUW][A-Z0-9]{2,}$")


def _p_to_ts(p_digits: str) -> str:
    """Convert an archives URL p-timestamp (p1700000000123456) to a Slack ts.

    The decimal point goes 6 digits from the end: 1700000000.123456
    """
    return f"{p_digits[:-6]}.{p_digits[-6:]}"


def resolve_channel_ref(ref: str) -> ChannelRef:
    """Resolve a channel reference (raw ID or Slack URL) to a channel ID.

    Args:
        ref: Raw ID (C.../D.../G.../U.../W...), archives URL, or app client URL

    Returns:
        ChannelRef with the channel ID and message ts if the URL carried one

    Raises:
        ValueError: If the reference cannot be resolved
    """
    ref = ref.strip().strip("<>")

    if _RAW_ID_RE.match(ref):
        return ChannelRef(channel_id=ref)

    match = _ARCHIVES_RE.search(ref)
    if match:
        channel_id = match.group(1).upper()
        p_digits = match.group(2)
        return ChannelRef(
            channel_id=channel_id,
            message_ts=_p_to_ts(p_digits) if p_digits else None,
        )

    match = _CLIENT_RE.search(ref)
    if match:
        return ChannelRef(
            channel_id=match.group(1).upper(),
            message_ts=match.group(2),
        )

    raise ValueError(
        f"Could not resolve '{ref}' to a channel. Provide a channel ID "
        "(C.../D.../G...), user ID (U...), or a Slack link copied via "
        "'Copy link'. Use slack_find_conversation to look up channels by name."
    )
