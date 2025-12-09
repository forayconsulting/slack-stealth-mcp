/**
 * Get Unread Tool - Fetch all unread messages and mentions
 *
 * This is the "What's new?" tool - the most complex implementation
 * with parallel batching for checking unread status.
 *
 * Key quirk: conversations.list doesn't return unread_count for DMs,
 * so we must call conversations.info for each DM to check status.
 */

import { SlackClient } from "../slack-client";
import { tsToDate, type SlackConversation, type SlackMessage } from "../types/slack";

interface GetUnreadOptions {
  includeChannels?: boolean;
  includeDms?: boolean;
  includeMentions?: boolean;
  maxMessagesPerConversation?: number;
  maxConversationsToCheck?: number;
  maxDmsToScan?: number;
}

interface FormattedMessage {
  user: string;
  text: string;
  time: string;
  ts: string;
  thread_ts?: string;
}

interface UnreadConversation {
  channel_id: string;
  name: string;
  unread_count: number;
  messages: FormattedMessage[];
}

interface Mention {
  user: string;
  text: string;
  time: string;
  ts: string;
  channel_id: string;
}

interface GetUnreadResult {
  summary: string;
  unread_dms?: UnreadConversation[];
  unread_channels?: UnreadConversation[];
  mentions?: Mention[];
  totals: {
    unread_dm_messages: number;
    channels_with_unread: number;
    mentions: number;
  };
}

/**
 * Get cached user display name without API call
 */
function getCachedUserName(client: SlackClient, userId: string): string {
  const cached = client.getCachedUser(userId);
  if (cached) {
    return cached.display_name || cached.real_name || cached.name;
  }
  return userId.substring(0, 8) + "...";
}

/**
 * Check if a conversation has unread messages
 * Returns [detailedInfo, unreadCount] if unread, null otherwise
 */
async function checkConversationUnread(
  client: SlackClient,
  conv: SlackConversation
): Promise<[SlackConversation, number] | null> {
  try {
    const detailed = await client.getConversationInfo(conv.id);

    // Check unread_count fields first
    if (detailed.unread_count_display && detailed.unread_count_display > 0) {
      return [detailed, detailed.unread_count_display];
    }
    if (detailed.unread_count && detailed.unread_count > 0) {
      return [detailed, detailed.unread_count];
    }

    // For MPIMs, unread_count is often undefined - check via last_read vs latest message
    if (detailed.is_mpim && detailed.last_read) {
      const msgs = await client.getConversationHistory(conv.id, { limit: 1 });
      if (msgs.length > 0 && msgs[0].ts > detailed.last_read) {
        // Count unread by fetching messages after last_read
        const unreadMsgs = await client.getConversationHistory(conv.id, {
          oldest: detailed.last_read,
          limit: 10,
        });
        return [detailed, unreadMsgs.length];
      }
    }

    // For channels, also check via last_read comparison
    if (!detailed.is_im && detailed.last_read) {
      const msgs = await client.getConversationHistory(conv.id, { limit: 1 });
      if (msgs.length > 0 && msgs[0].ts > detailed.last_read) {
        const unreadMsgs = await client.getConversationHistory(conv.id, {
          oldest: detailed.last_read,
          limit: 10,
        });
        if (unreadMsgs.length > 0) {
          return [detailed, unreadMsgs.length];
        }
      }
    }
  } catch {
    // Silently ignore - will skip this conversation
  }
  return null;
}

/**
 * Get all unread messages and mentions.
 * This does NOT mark any messages as read.
 */
export async function getUnread(
  client: SlackClient,
  options: GetUnreadOptions = {}
): Promise<GetUnreadResult> {
  const {
    includeChannels = true,
    includeDms = true,
    includeMentions = true,
    maxMessagesPerConversation = 5,
    maxConversationsToCheck = 20,
    maxDmsToScan = 30,
  } = options;

  const unreadDms: UnreadConversation[] = [];
  const unreadChannels: UnreadConversation[] = [];
  const mentions: Mention[] = [];

  // Collect user IDs for prefetching
  const userIdsToPrefetch = new Set<string>();

  let checkedCount = 0;

  // =========================================================================
  // Process DMs
  // =========================================================================
  if (includeDms) {
    // Fetch DMs separately
    const dmConversations = await client.listConversations({
      types: "im,mpim",
      limit: 100,
      maxPages: 2,
    });

    // Check DMs for unread status in parallel batches
    const dmsToCheck = dmConversations.slice(0, maxDmsToScan);
    const dmConvsWithUnread: [SlackConversation, number][] = [];

    const batchSize = 15;
    for (let i = 0; i < dmsToCheck.length; i += batchSize) {
      const batch = dmsToCheck.slice(i, i + batchSize);
      const results = await Promise.all(
        batch.map((conv) => checkConversationUnread(client, conv))
      );

      for (const result of results) {
        if (result !== null) {
          const [conv, unreadCount] = result;
          dmConvsWithUnread.push([conv, unreadCount]);
          if (conv.user) {
            userIdsToPrefetch.add(conv.user);
          }
        }
      }
    }

    // Sort by unread count (most active first)
    dmConvsWithUnread.sort((a, b) => b[1] - a[1]);

    // Prefetch DM participant names
    if (userIdsToPrefetch.size > 0) {
      await client.prefetchUsers(Array.from(userIdsToPrefetch));
    }

    // Process each unread DM
    for (const [conv, unreadCount] of dmConvsWithUnread.slice(
      0,
      maxConversationsToCheck
    )) {
      checkedCount++;

      // Handle invalid last_read (from "Mark as unread")
      let oldest = conv.last_read;
      if (oldest && oldest.startsWith("0000000000")) {
        oldest = undefined;
      }

      let messages: SlackMessage[] = [];
      try {
        messages = await client.getConversationHistory(conv.id, {
          oldest,
          limit: maxMessagesPerConversation,
        });
      } catch {
        // Skip if we can't fetch history
        continue;
      }

      if (messages.length > 0) {
        // Collect message author IDs
        for (const msg of messages) {
          if (msg.user) {
            userIdsToPrefetch.add(msg.user);
          }
        }

        // Get DM name
        let dmName: string;
        if (conv.is_im && conv.user) {
          dmName = `@${getCachedUserName(client, conv.user)}`;
        } else if (conv.is_mpim && conv.name) {
          // MPIM name is like "mpdm-user1--user2--user3-1"
          const parts = conv.name
            .replace("mpdm-", "")
            .split("--")
            .map((p) => p.split("-")[0]);
          dmName = parts.slice(0, 3).join(", ");
          if (parts.length > 3) {
            dmName += ` +${parts.length - 3}`;
          }
        } else {
          dmName = conv.name || conv.id;
        }

        const formattedMessages: FormattedMessage[] = [];
        for (const msg of messages.slice().reverse()) {
          const userName = msg.user
            ? getCachedUserName(client, msg.user)
            : "Unknown";
          formattedMessages.push({
            user: userName,
            text: msg.text,
            time: tsToDate(msg.ts).toISOString().substring(11, 16),
            ts: msg.ts,
          });
        }

        unreadDms.push({
          channel_id: conv.id,
          name: dmName,
          unread_count: unreadCount,
          messages: formattedMessages,
        });
      }
    }
  }

  // =========================================================================
  // Process Channels
  // =========================================================================
  const remainingBudget = maxConversationsToCheck - checkedCount;
  if (includeChannels && remainingBudget > 0) {
    // Fetch channels separately
    const channelConversations = await client.listConversations({
      types: "public_channel,private_channel",
      limit: 200,
      maxPages: 2,
    });

    // Check channels for unread status in parallel batches
    const maxChannelsToScan = 50;
    const channelsToCheck = channelConversations.slice(0, maxChannelsToScan);
    const channelConvsWithUnread: [SlackConversation, number][] = [];

    const batchSize = 15;
    for (let i = 0; i < channelsToCheck.length; i += batchSize) {
      const batch = channelsToCheck.slice(i, i + batchSize);
      const results = await Promise.all(
        batch.map((conv) => checkConversationUnread(client, conv))
      );

      for (const result of results) {
        if (result !== null) {
          channelConvsWithUnread.push(result);
        }
      }
    }

    // Sort by unread count
    channelConvsWithUnread.sort((a, b) => b[1] - a[1]);

    // Process each unread channel
    for (const [conv, unreadCount] of channelConvsWithUnread.slice(
      0,
      remainingBudget
    )) {
      // Handle invalid last_read
      let oldest = conv.last_read;
      if (oldest && oldest.startsWith("0000000000")) {
        oldest = undefined;
      }

      let messages: SlackMessage[] = [];
      try {
        messages = await client.getConversationHistory(conv.id, {
          oldest,
          limit: maxMessagesPerConversation,
        });
      } catch {
        continue;
      }

      if (messages.length > 0) {
        // Collect message author IDs
        for (const msg of messages) {
          if (msg.user) {
            userIdsToPrefetch.add(msg.user);
          }
        }

        const formattedMessages: FormattedMessage[] = [];
        for (const msg of messages.slice().reverse()) {
          const userName = msg.user
            ? getCachedUserName(client, msg.user)
            : "Unknown";
          const formatted: FormattedMessage = {
            user: userName,
            text: msg.text,
            time: tsToDate(msg.ts).toISOString().substring(11, 16),
            ts: msg.ts,
          };
          if (msg.thread_ts && msg.thread_ts !== msg.ts) {
            formatted.thread_ts = msg.thread_ts;
          }
          formattedMessages.push(formatted);
        }

        unreadChannels.push({
          channel_id: conv.id,
          name: conv.name || conv.id,
          unread_count: unreadCount,
          messages: formattedMessages,
        });
      }
    }
  }

  // Prefetch any remaining message authors
  if (userIdsToPrefetch.size > 0) {
    await client.prefetchUsers(Array.from(userIdsToPrefetch));
  }

  // =========================================================================
  // Search for Mentions
  // =========================================================================
  if (includeMentions) {
    try {
      // Get current user info
      const auth = await client.testAuth();
      const userId = auth.user_id;

      if (userId) {
        // Search for messages mentioning the user
        const searchResults = await client.searchMessages(`<@${userId}>`, {
          count: 20,
          sort: "timestamp",
          sortDir: "desc",
        });

        // Prefetch mention authors
        const mentionAuthors = searchResults.messages
          .filter((msg) => msg.user)
          .map((msg) => msg.user as string);
        if (mentionAuthors.length > 0) {
          await client.prefetchUsers(mentionAuthors);
        }

        // Filter to only unread mentions by checking channel read state
        const channelReadCache = new Map<string, string | null>();

        for (const msg of searchResults.messages) {
          if (!msg.channel?.id) continue;

          const channelId = msg.channel.id;

          // Get channel's last_read (with caching)
          if (!channelReadCache.has(channelId)) {
            try {
              const channelInfo = await client.getConversationInfo(channelId);
              channelReadCache.set(channelId, channelInfo.last_read || null);
            } catch {
              channelReadCache.set(channelId, null);
            }
          }

          const lastRead = channelReadCache.get(channelId);

          // Only include if message is unread (ts > last_read)
          if (lastRead && msg.ts > lastRead) {
            const userName = msg.user
              ? getCachedUserName(client, msg.user)
              : "Unknown";

            mentions.push({
              user: userName,
              text: msg.text,
              time: tsToDate(msg.ts).toISOString().replace("T", " ").substring(0, 16),
              ts: msg.ts,
              channel_id: channelId,
            });

            // Limit to 10 unread mentions
            if (mentions.length >= 10) break;
          }
        }
      }
    } catch {
      // Mentions are supplementary - don't fail if search fails
    }
  }

  // =========================================================================
  // Build Summary
  // =========================================================================
  const totalUnreadDms = unreadDms.reduce(
    (sum, dm) => sum + dm.unread_count,
    0
  );
  const totalUnreadChannels = unreadChannels.length;
  const totalMentions = mentions.length;

  const summaryParts: string[] = [];
  if (totalUnreadDms > 0) {
    summaryParts.push(
      `${totalUnreadDms} unread message(s) in ${unreadDms.length} DM(s)`
    );
  }
  if (totalUnreadChannels > 0) {
    summaryParts.push(`${totalUnreadChannels} channel(s) with new messages`);
  }
  if (totalMentions > 0) {
    summaryParts.push(`${totalMentions} recent mention(s)`);
  }

  const summary =
    summaryParts.length > 0 ? summaryParts.join("; ") : "No unread messages";

  const result: GetUnreadResult = {
    summary,
    totals: {
      unread_dm_messages: totalUnreadDms,
      channels_with_unread: totalUnreadChannels,
      mentions: totalMentions,
    },
  };

  if (unreadDms.length > 0) {
    result.unread_dms = unreadDms;
  }
  if (unreadChannels.length > 0) {
    result.unread_channels = unreadChannels;
  }
  if (mentions.length > 0) {
    result.mentions = mentions;
  }

  return result;
}
