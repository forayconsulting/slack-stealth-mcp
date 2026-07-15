/**
 * Reply Tool - Send messages or replies in Slack
 */

import { SlackClient, SlackAPIError } from "../slack-client";
import { normalizeMrkdwn } from "../mrkdwn";
import { resolveChannelRef } from "../refs";

interface ReplyOptions {
  target: string;
  message: string;
  threadTs?: string;
  broadcast?: boolean;
}

interface ReplyResult {
  ok: boolean;
  channel_id?: string;
  message_ts?: string;
  text?: string;
  thread_ts?: string;
  is_thread_reply?: boolean;
  broadcasted?: boolean;
  sent_to?: string;
  error?: string;
}

/**
 * Send a message or reply to a conversation.
 * This does NOT mark the channel as read.
 */
export async function reply(
  client: SlackClient,
  options: ReplyOptions
): Promise<ReplyResult> {
  const { target, message, broadcast = false } = options;
  let { threadTs } = options;

  // Resolve URLs/raw IDs to a channel ID
  let channelId: string;
  try {
    const ref = resolveChannelRef(target);
    channelId = ref.channelId;
    // A message URL implies a thread reply unless the caller said otherwise
    if (ref.messageTs && !threadTs) {
      threadTs = ref.messageTs;
    }
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    };
  }

  // If target is a user ID, open/get DM channel
  if (channelId.startsWith("U") || channelId.startsWith("W")) {
    try {
      channelId = await client.openConversation([channelId]);
    } catch (error) {
      const apiError = error as SlackAPIError;
      return {
        ok: false,
        error: `Failed to open DM with user ${channelId}: ${apiError.error || apiError.message}`,
      };
    }
  }

  // Send the message
  try {
    const sentMessage = await client.postMessage(
      channelId,
      normalizeMrkdwn(message),
      {
        threadTs,
        replyBroadcast: broadcast,
      }
    );

    const result: ReplyResult = {
      ok: true,
      channel_id: channelId,
      message_ts: sentMessage.ts,
      text: sentMessage.text,
    };

    if (threadTs) {
      result.thread_ts = threadTs;
      result.is_thread_reply = true;
      if (broadcast) {
        result.broadcasted = true;
      }
    }

    // Try to get channel name for context
    try {
      const conv = await client.getConversationInfo(channelId);
      if (conv.is_im && conv.user) {
        const user = await client.getUserInfo(conv.user);
        const displayName = user.display_name || user.real_name || user.name;
        result.sent_to = `DM with @${displayName}`;
      } else {
        result.sent_to = conv.name || channelId;
      }
    } catch {
      result.sent_to = channelId;
    }

    return result;
  } catch (error) {
    const apiError = error as SlackAPIError;
    return {
      ok: false,
      error: `Failed to send message: ${apiError.error || apiError.message}`,
      channel_id: channelId,
    };
  }
}
