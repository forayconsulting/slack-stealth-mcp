/**
 * Mark Read Tool - Explicitly mark conversations as read
 *
 * This is the ONLY tool that affects read state.
 */

import { SlackClient, SlackAPIError } from "../slack-client";

interface MarkReadOptions {
  channel: string;
  timestamp?: string;
}

interface MarkReadResult {
  ok: boolean;
  channel_id: string;
  channel_name?: string;
  marked_read_until?: string;
  message?: string;
  error?: string;
}

/**
 * Mark a conversation as read.
 * This is the ONLY operation that affects read state.
 */
export async function markRead(
  client: SlackClient,
  options: MarkReadOptions
): Promise<MarkReadResult> {
  const { channel } = options;
  let { timestamp } = options;

  // If no timestamp provided, get the latest message
  if (!timestamp) {
    try {
      const messages = await client.getConversationHistory(channel, {
        limit: 1,
      });
      if (messages.length > 0) {
        timestamp = messages[0].ts;
      } else {
        return {
          ok: true,
          channel_id: channel,
          message: "No messages to mark as read",
        };
      }
    } catch (error) {
      const apiError = error as SlackAPIError;
      return {
        ok: false,
        channel_id: channel,
        error: `Failed to get latest message: ${apiError.error || apiError.message}`,
      };
    }
  }

  // Mark as read
  try {
    await client.markConversation(channel, timestamp);

    // Get channel info for context
    let channelName = channel;
    try {
      const conv = await client.getConversationInfo(channel);
      if (conv.is_im && conv.user) {
        const user = await client.getUserInfo(conv.user);
        const displayName = user.display_name || user.real_name || user.name;
        channelName = `DM with @${displayName}`;
      } else {
        channelName = conv.name || channel;
      }
    } catch {
      // Ignore - use channel ID as fallback
    }

    return {
      ok: true,
      channel_id: channel,
      channel_name: channelName,
      marked_read_until: timestamp,
      message: `Marked ${channelName} as read`,
    };
  } catch (error) {
    const apiError = error as SlackAPIError;
    return {
      ok: false,
      channel_id: channel,
      error: `Failed to mark as read: ${apiError.error || apiError.message}`,
    };
  }
}
