/**
 * Reply Tool - Send messages or replies in Slack
 */

import { SlackClient, SlackAPIError } from "../slack-client";
import { normalizeMrkdwn } from "../mrkdwn";
import { resolveChannelRef } from "../refs";

/**
 * Human-readable name for a conversation, falling back to its ID.
 */
export async function conversationLabel(
  client: SlackClient,
  channelId: string
): Promise<string> {
  try {
    const conv = await client.getConversationInfo(channelId);
    if (conv.is_im && conv.user) {
      const user = await client.getUserInfo(conv.user);
      const displayName = user.display_name || user.real_name || user.name;
      return `DM with @${displayName}`;
    }
    return conv.name || channelId;
  } catch {
    return channelId;
  }
}

/**
 * Turn a send target into { channelId, threadTs }.
 *
 * Accepts the same targets as slack_reply: C/D/G IDs, U/W user IDs (opens
 * the DM), or Slack URLs. A message URL implies a thread reply unless the
 * caller passed threadTs. Throws an Error whose message suits the tool's
 * error field.
 */
export async function resolveSendTarget(
  client: SlackClient,
  target: string,
  threadTs?: string
): Promise<{ channelId: string; threadTs?: string }> {
  const ref = resolveChannelRef(target);
  let channelId = ref.channelId;
  if (ref.messageTs && !threadTs) {
    threadTs = ref.messageTs;
  }

  if (channelId.startsWith("U") || channelId.startsWith("W")) {
    try {
      channelId = await client.openConversation([channelId]);
    } catch (error) {
      const apiError = error as SlackAPIError;
      throw new Error(
        `Failed to open DM with user ${channelId}: ${apiError.error || apiError.message}`
      );
    }
  }

  return { channelId, threadTs };
}

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

  // Resolve URLs/raw IDs/user IDs to a channel ID (and thread from a URL)
  let channelId: string;
  try {
    ({ channelId, threadTs } = await resolveSendTarget(client, target, threadTs));
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    };
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

    result.sent_to = await conversationLabel(client, channelId);
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
