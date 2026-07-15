/**
 * Resolve channel references (IDs or Slack URLs) to channel IDs.
 *
 * Users often paste links copied from Slack ("Copy link" on a channel or
 * message) instead of raw channel IDs. This module turns any supported
 * reference into a channel ID plus an optional message timestamp, with no
 * network calls.
 */

export interface ChannelRef {
  channelId: string;
  messageTs?: string;
}

// https://workspace.slack.com/archives/C0123456789
// https://workspace.slack.com/archives/C0123456789/p1700000000123456
// also matches workspace.enterprise.slack.com
const ARCHIVES_RE =
  /https?:\/\/[^/\s]*slack\.com\/archives\/([A-Z][A-Z0-9]+)(?:\/p(\d{16,}))?/i;

// Enterprise Grid / app client URLs:
// https://app.slack.com/client/T0123456789/C0123456789
// optionally followed by /thread/C0123456789-1700000000.123456
const CLIENT_RE =
  /https?:\/\/app\.slack\.com\/client\/[TE][A-Z0-9]+\/([A-Z][A-Z0-9]+)(?:\/thread\/[A-Z][A-Z0-9]+-(\d+\.\d+))?/i;

// Raw conversation or user IDs: C/D/G channels, U/W users.
// The trailing length is deliberately permissive - this branch is a
// passthrough, and its job is only to distinguish an ID-shaped token from a
// natural-language name (which has spaces/lowercase). A malformed token just
// yields a clean not_found from Slack, so there is no need to police length.
const RAW_ID_RE = /^[CDGUW][A-Z0-9]{2,}$/;

/**
 * Convert an archives URL p-timestamp (p1700000000123456) to a Slack ts.
 * The decimal point goes 6 digits from the end: 1700000000.123456
 */
function pToTs(pDigits: string): string {
  return `${pDigits.slice(0, -6)}.${pDigits.slice(-6)}`;
}

/**
 * Resolve a channel reference (raw ID or Slack URL) to a channel ID.
 *
 * @param ref Raw ID (C.../D.../G.../U.../W...), archives URL, or app client URL
 * @throws Error if the reference cannot be resolved
 */
export function resolveChannelRef(ref: string): ChannelRef {
  const trimmed = ref.trim().replace(/^<|>$/g, "");

  if (RAW_ID_RE.test(trimmed)) {
    return { channelId: trimmed };
  }

  const archivesMatch = trimmed.match(ARCHIVES_RE);
  if (archivesMatch) {
    return {
      channelId: archivesMatch[1].toUpperCase(),
      messageTs: archivesMatch[2] ? pToTs(archivesMatch[2]) : undefined,
    };
  }

  const clientMatch = trimmed.match(CLIENT_RE);
  if (clientMatch) {
    return {
      channelId: clientMatch[1].toUpperCase(),
      messageTs: clientMatch[2],
    };
  }

  throw new Error(
    `Could not resolve '${ref}' to a channel. Provide a channel ID ` +
      "(C.../D.../G...), user ID (U...), or a Slack link copied via " +
      "'Copy link'. Use slack_find_conversation to look up channels by name."
  );
}
