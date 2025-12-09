/**
 * React Tool - Add/remove emoji reactions on messages
 */

import { SlackClient, SlackAPIError } from "../slack-client";

interface ReactOptions {
  channel: string;
  messageTs: string;
  emoji: string;
  remove?: boolean;
}

interface ReactResult {
  ok: boolean;
  action?: string;
  emoji?: string;
  channel?: string;
  message_ts?: string;
  channel_name?: string;
  error?: string;
}

/**
 * Add or remove an emoji reaction on a message.
 * This does NOT mark messages as read.
 */
export async function react(
  client: SlackClient,
  options: ReactOptions
): Promise<ReactResult> {
  const { channel, messageTs, emoji, remove = false } = options;

  // Normalize emoji name - strip colons if provided
  const emojiName = emoji.replace(/^:|:$/g, "");

  try {
    if (remove) {
      await client.removeReaction(channel, messageTs, emojiName);
    } else {
      await client.addReaction(channel, messageTs, emojiName);
    }

    const result: ReactResult = {
      ok: true,
      action: remove ? "removed" : "added",
      emoji: emojiName,
      channel,
      message_ts: messageTs,
    };

    // Try to get channel name for context
    try {
      const conv = await client.getConversationInfo(channel);
      if (conv.is_im && conv.user) {
        const user = await client.getUserInfo(conv.user);
        const displayName = user.display_name || user.real_name || user.name;
        result.channel_name = `DM with @${displayName}`;
      } else {
        result.channel_name = conv.name || channel;
      }
    } catch {
      result.channel_name = channel;
    }

    return result;
  } catch (error) {
    const apiError = error as SlackAPIError;
    return {
      ok: false,
      error: `Failed to ${remove ? "remove" : "add"} reaction: ${apiError.error || apiError.message}`,
      emoji: emojiName,
      channel,
      message_ts: messageTs,
    };
  }
}
