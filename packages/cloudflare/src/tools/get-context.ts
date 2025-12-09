/**
 * Get Context Tool - Fetch conversation messages for context
 */

import { SlackClient } from "../slack-client";
import { tsToDate, getConversationType } from "../types/slack";

interface GetContextOptions {
  channel: string;
  messageTs?: string;
  contextSize?: number;
}

interface FormattedMessage {
  ts: string;
  user: string;
  text: string;
  time: string;
  thread_ts?: string;
  is_reply?: boolean;
  reply_count?: number;
  reactions?: string[];
}

interface GetContextResult {
  channel: string;
  channel_id: string;
  channel_type: string;
  messages: FormattedMessage[];
  message_count: number;
  is_thread?: boolean;
  thread_ts?: string;
}

/**
 * Get messages from a conversation, optionally around a specific message.
 * This does NOT mark messages as read.
 */
export async function getContext(
  client: SlackClient,
  options: GetContextOptions
): Promise<GetContextResult> {
  const { channel, messageTs, contextSize = 10 } = options;

  // Check if this is a thread request
  let isThread = false;
  let messages: Awaited<ReturnType<typeof client.getConversationHistory>> = [];

  if (messageTs) {
    // Try to get thread replies first
    try {
      const threadMessages = await client.getThreadReplies(
        channel,
        messageTs,
        contextSize
      );
      if (threadMessages.length > 1) {
        // It's a thread with replies
        isThread = true;
        messages = threadMessages;
      }
    } catch {
      // Not a thread or error - will fetch channel history
    }
  }

  if (messages.length === 0) {
    // Get channel history
    if (messageTs) {
      // Get messages around the specified timestamp
      // First get messages before (including target)
      const beforeMessages = await client.getConversationHistory(channel, {
        latest: messageTs,
        limit: Math.floor(contextSize / 2) + 1,
        inclusive: true,
      });

      // Then get messages after
      const afterMessages = await client.getConversationHistory(channel, {
        oldest: messageTs,
        limit: Math.floor(contextSize / 2),
        inclusive: false,
      });

      // Combine (after are newest first, messages are also newest first)
      messages = [...afterMessages, ...beforeMessages];
    } else {
      // Just get recent messages
      messages = await client.getConversationHistory(channel, {
        limit: contextSize,
      });
    }
  }

  // Format messages - use cached user names
  const formattedMessages: FormattedMessage[] = [];
  for (const msg of messages.slice().reverse()) {
    // Reverse to show oldest first
    let userName = "Unknown";
    if (msg.user) {
      const cached = client.getCachedUser(msg.user);
      userName = cached
        ? cached.display_name || cached.real_name || cached.name
        : msg.user.substring(0, 8) + "...";
    }

    const formatted: FormattedMessage = {
      ts: msg.ts,
      user: userName,
      text: msg.text,
      time: tsToDate(msg.ts).toISOString().replace("T", " ").substring(0, 19),
    };

    if (msg.thread_ts && msg.thread_ts !== msg.ts) {
      formatted.thread_ts = msg.thread_ts;
      formatted.is_reply = true;
    }

    if (msg.reply_count) {
      formatted.reply_count = msg.reply_count;
    }

    if (msg.reactions && msg.reactions.length > 0) {
      formatted.reactions = msg.reactions.map(
        (r) => `:${r.name}:${r.count > 1 ? r.count : ""}`
      );
    }

    formattedMessages.push(formatted);
  }

  // Get conversation info for context
  let channelName = channel;
  let channelType = "unknown";
  try {
    const conv = await client.getConversationInfo(channel);
    channelName = conv.name || channel;
    channelType = getConversationType(conv);
  } catch {
    // Ignore - use defaults
  }

  const result: GetContextResult = {
    channel: channelName,
    channel_id: channel,
    channel_type: channelType,
    messages: formattedMessages,
    message_count: formattedMessages.length,
  };

  if (isThread) {
    result.is_thread = true;
    result.thread_ts = messageTs;
  }

  return result;
}
