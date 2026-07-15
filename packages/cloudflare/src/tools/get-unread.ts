/**
 * Get Unread Tool - Fetch all unread messages and mentions
 *
 * Primary path uses client.counts - the endpoint Slack's own client uses for
 * badge state - which is authoritative, includes thread activity, and covers
 * every conversation in one request. If client.counts fails (undocumented
 * endpoint, may drift), we fall back to the original conversations.info scan.
 */

import { SlackClient } from "../slack-client";
import { tsToDate, type SlackConversation, type SlackMessage } from "../types/slack";
import { mpimParticipants } from "./find-conversation";

interface GetUnreadOptions {
  includeChannels?: boolean;
  includeDms?: boolean;
  includeMentions?: boolean;
  includeThreads?: boolean;
  maxMessagesPerConversation?: number;
  maxConversationsToCheck?: number;
  maxDmsToScan?: number;
  maxChannelsToScan?: number;
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
  more_unread?: boolean;
  mention_count?: number;
}

interface Mention {
  user: string;
  text: string;
  time: string;
  ts: string;
  channel_id: string;
}

interface UnreadError {
  channel_id?: string;
  stage?: string;
  error: string;
}

interface GetUnreadResult {
  summary: string;
  unread_dms?: UnreadConversation[];
  unread_channels?: UnreadConversation[];
  mentions?: Mention[];
  threads?: {
    has_unreads: boolean;
    mention_count: number;
    mention_count_by_channel?: Record<string, number>;
    note: string;
  };
  truncated?: {
    dms_not_shown: number;
    channels_not_shown: number;
    note: string;
  };
  errors?: UnreadError[];
  source?: "counts" | "scan_fallback";
  fallback_reason?: string;
  totals: {
    unread_dm_messages: number;
    channels_with_unread: number;
    mentions: number;
  };
}

/** Shape of one conversation entry in the client.counts response */
interface CountsEntry {
  id?: string;
  last_read?: string;
  latest?: string;
  has_unreads?: boolean;
  mention_count?: number;
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
 * Parse a Slack ts to float for sorting; invalid values sort last
 */
function tsFloat(ts: unknown): number {
  const parsed = parseFloat(String(ts));
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * Sanitize a last_read ts for use as a history 'oldest' filter.
 * "Mark as unread" sets last_read to "0000000000.000000" which is invalid
 * as a filter - fetch recent messages instead.
 */
function validOldest(lastRead: string | undefined): string | undefined {
  if (lastRead && !lastRead.startsWith("0000000000")) {
    return lastRead;
  }
  return undefined;
}

function formatMessages(
  client: SlackClient,
  messages: SlackMessage[]
): FormattedMessage[] {
  const formatted: FormattedMessage[] = [];
  for (const msg of messages.slice().reverse()) {
    const userName = msg.user ? getCachedUserName(client, msg.user) : "Unknown";
    const entry: FormattedMessage = {
      user: userName,
      text: msg.text,
      time: tsToDate(msg.ts).toISOString().substring(11, 16),
      ts: msg.ts,
    };
    if (msg.thread_ts && msg.thread_ts !== msg.ts) {
      entry.thread_ts = msg.thread_ts;
    }
    formatted.push(entry);
  }
  return formatted;
}

/**
 * Search for recent mentions of the current user; keep only unread ones.
 * Callers can prefill channelReadCache (e.g. from client.counts) to avoid
 * per-channel conversations.info calls.
 */
async function collectMentions(
  client: SlackClient,
  channelReadCache: Map<string, string | null>,
  limit: number = 10
): Promise<Mention[]> {
  const mentions: Mention[] = [];

  const auth = await client.testAuth();
  const userId = auth.user_id;
  if (!userId) return mentions;

  const searchResults = await client.searchMessages(`<@${userId}>`, {
    count: 20, // Fetch more since we'll filter
    sort: "timestamp",
    sortDir: "desc",
  });

  const mentionAuthors = searchResults.messages
    .filter((msg) => msg.user)
    .map((msg) => msg.user as string);
  if (mentionAuthors.length > 0) {
    await client.prefetchUsers(mentionAuthors);
  }

  for (const msg of searchResults.messages) {
    if (!msg.channel?.id) continue;
    const channelId = msg.channel.id;

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
      if (mentions.length >= limit) break;
    }
  }

  return mentions;
}

/**
 * Assemble the result with summary and totals
 */
function buildResult(
  unreadDms: UnreadConversation[],
  unreadChannels: UnreadConversation[],
  mentions: Mention[]
): GetUnreadResult {
  const totalUnreadDms = unreadDms.reduce((sum, dm) => sum + dm.unread_count, 0);
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

  const result: GetUnreadResult = {
    summary:
      summaryParts.length > 0 ? summaryParts.join("; ") : "No unread messages",
    totals: {
      unread_dm_messages: totalUnreadDms,
      channels_with_unread: totalUnreadChannels,
      mentions: totalMentions,
    },
  };

  if (unreadDms.length > 0) result.unread_dms = unreadDms;
  if (unreadChannels.length > 0) result.unread_channels = unreadChannels;
  if (mentions.length > 0) result.mentions = mentions;

  return result;
}

// =============================================================================
// Primary path: client.counts
// =============================================================================

async function getUnreadByCounts(
  client: SlackClient,
  options: Required<GetUnreadOptions>
): Promise<GetUnreadResult> {
  const {
    includeChannels,
    includeDms,
    includeMentions,
    includeThreads,
    maxMessagesPerConversation,
    maxConversationsToCheck,
    maxDmsToScan,
    maxChannelsToScan,
  } = options;

  const counts = await client.getCounts();
  const errors: UnreadError[] = [];

  const bucket = (key: string): CountsEntry[] =>
    Array.isArray(counts[key]) ? (counts[key] as CountsEntry[]) : [];

  const unreadEntries = (entries: CountsEntry[]): CountsEntry[] =>
    entries
      .filter(
        (e) => e && e.id && (e.has_unreads || (e.mention_count || 0) > 0)
      )
      // Most recent activity first - so caps drop the oldest, not the newest
      .sort((a, b) => tsFloat(b.latest) - tsFloat(a.latest));

  const dmEntries = includeDms
    ? unreadEntries([...bucket("ims"), ...bucket("mpims")]).slice(0, maxDmsToScan)
    : [];
  const channelEntries = includeChannels
    ? unreadEntries(bucket("channels")).slice(0, maxChannelsToScan)
    : [];

  // last_read for every conversation Slack reported - reused for mention
  // filtering so we skip per-channel conversations.info calls
  const channelReadCache = new Map<string, string | null>();
  for (const key of ["ims", "mpims", "channels"]) {
    for (const entry of bucket(key)) {
      if (entry?.id) {
        channelReadCache.set(entry.id, entry.last_read || null);
      }
    }
  }

  // Warm the user directory once so DM names resolve without N+1 calls
  await client.ensureUserDirectory();

  const resolveConv = async (channelId: string): Promise<SlackConversation> => {
    return (
      client.getCachedConversation(channelId) ??
      (await client.getConversationInfo(channelId))
    );
  };

  const fetchConversation = async (
    entry: CountsEntry
  ): Promise<UnreadConversation | null> => {
    const channelId = entry.id as string;
    try {
      const conv = await resolveConv(channelId);

      const messages = await client.getConversationHistory(channelId, {
        oldest: validOldest(entry.last_read),
        limit: maxMessagesPerConversation,
      });
      if (messages.length === 0) return null;

      await client.prefetchUsers(
        messages.filter((m) => m.user).map((m) => m.user as string)
      );

      let name: string;
      if (conv.is_im && conv.user) {
        name = `@${getCachedUserName(client, conv.user)}`;
      } else if (conv.is_mpim) {
        const participants = mpimParticipants(conv, client);
        name =
          participants.length > 0
            ? participants.join(", ")
            : conv.name || channelId;
      } else {
        name = conv.name || channelId;
      }

      const item: UnreadConversation = {
        channel_id: channelId,
        name,
        unread_count: messages.length,
        messages: formatMessages(client, messages),
      };
      if (messages.length >= maxMessagesPerConversation) {
        item.more_unread = true;
      }
      if (entry.mention_count) {
        item.mention_count = entry.mention_count;
      }
      return item;
    } catch (error) {
      errors.push({
        channel_id: channelId,
        error: error instanceof Error ? error.message : String(error),
      });
      return null;
    }
  };

  // DMs get budget priority; channels use what remains
  const dmBudget = Math.min(dmEntries.length, maxConversationsToCheck);
  const channelBudget = Math.max(maxConversationsToCheck - dmBudget, 0);

  const unreadDms: UnreadConversation[] = [];
  const unreadChannels: UnreadConversation[] = [];

  for (const entry of dmEntries.slice(0, dmBudget)) {
    const item = await fetchConversation(entry);
    if (item) unreadDms.push(item);
  }

  for (const entry of channelEntries.slice(0, channelBudget)) {
    const item = await fetchConversation(entry);
    if (item) unreadChannels.push(item);
  }

  let mentions: Mention[] = [];
  if (includeMentions) {
    try {
      mentions = await collectMentions(client, channelReadCache);
    } catch (error) {
      errors.push({
        stage: "mentions",
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  const result = buildResult(unreadDms, unreadChannels, mentions);

  // Thread badge state - invisible to conversations.history, so surface
  // the counts summary and point at slack_get_context for reading
  const threads = (counts.threads || {}) as {
    has_unreads?: boolean;
    mention_count?: number;
    mention_count_by_channel?: Record<string, number>;
  };
  if (includeThreads && (threads.has_unreads || threads.mention_count)) {
    result.threads = {
      has_unreads: Boolean(threads.has_unreads),
      mention_count: threads.mention_count || 0,
      note:
        "You have unread thread replies (not shown in channel history). " +
        "Open a thread with slack_get_context using the parent message_ts " +
        "to read them.",
    };
    if (
      threads.mention_count_by_channel &&
      Object.keys(threads.mention_count_by_channel).length > 0
    ) {
      result.threads.mention_count_by_channel = threads.mention_count_by_channel;
    }
    result.summary += "; unread thread replies";
  }

  // Dropped-by-cap transparency: never let truncation read as "all done"
  const droppedDms = dmEntries.length - dmBudget;
  const droppedChannels =
    channelEntries.length - Math.min(channelEntries.length, channelBudget);
  if (droppedDms > 0 || droppedChannels > 0) {
    result.truncated = {
      dms_not_shown: Math.max(droppedDms, 0),
      channels_not_shown: Math.max(droppedChannels, 0),
      note: "Raise max_conversations to see more",
    };
  }

  if (errors.length > 0) {
    result.errors = errors;
  }

  return result;
}

// =============================================================================
// Fallback path: conversations.info scan (original implementation)
// =============================================================================

/**
 * Check if a conversation has unread messages
 * Returns [detailedInfo, unreadCount] if unread, null otherwise
 */
async function checkConversationUnread(
  client: SlackClient,
  conv: SlackConversation,
  errors: UnreadError[]
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

    // unread_count is often undefined (always for MPIMs, sometimes for DMs)
    // - fall back to comparing last_read vs latest message
    if (detailed.last_read) {
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
  } catch (error) {
    errors.push({
      channel_id: conv.id,
      error: error instanceof Error ? error.message : String(error),
    });
  }
  return null;
}

async function getUnreadByScan(
  client: SlackClient,
  options: Required<GetUnreadOptions>
): Promise<GetUnreadResult> {
  const {
    includeChannels,
    includeDms,
    includeMentions,
    maxMessagesPerConversation,
    maxConversationsToCheck,
    maxDmsToScan,
    maxChannelsToScan,
  } = options;

  const unreadDms: UnreadConversation[] = [];
  const unreadChannels: UnreadConversation[] = [];
  let mentions: Mention[] = [];
  const errors: UnreadError[] = [];

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
        batch.map((conv) => checkConversationUnread(client, conv, errors))
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

      let messages: SlackMessage[] = [];
      try {
        messages = await client.getConversationHistory(conv.id, {
          oldest: validOldest(conv.last_read),
          limit: maxMessagesPerConversation,
        });
      } catch (error) {
        errors.push({
          channel_id: conv.id,
          error: error instanceof Error ? error.message : String(error),
        });
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
        } else if (conv.is_mpim) {
          const participants = mpimParticipants(conv, client);
          dmName =
            participants.length > 0
              ? participants.join(", ")
              : conv.name || conv.id;
        } else {
          dmName = conv.name || conv.id;
        }

        unreadDms.push({
          channel_id: conv.id,
          name: dmName,
          unread_count: unreadCount,
          messages: formatMessages(client, messages),
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
    const channelsToCheck = channelConversations.slice(0, maxChannelsToScan);
    const channelConvsWithUnread: [SlackConversation, number][] = [];

    const batchSize = 15;
    for (let i = 0; i < channelsToCheck.length; i += batchSize) {
      const batch = channelsToCheck.slice(i, i + batchSize);
      const results = await Promise.all(
        batch.map((conv) => checkConversationUnread(client, conv, errors))
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
      let messages: SlackMessage[] = [];
      try {
        messages = await client.getConversationHistory(conv.id, {
          oldest: validOldest(conv.last_read),
          limit: maxMessagesPerConversation,
        });
      } catch (error) {
        errors.push({
          channel_id: conv.id,
          error: error instanceof Error ? error.message : String(error),
        });
        continue;
      }

      if (messages.length > 0) {
        // Collect message author IDs
        for (const msg of messages) {
          if (msg.user) {
            userIdsToPrefetch.add(msg.user);
          }
        }

        unreadChannels.push({
          channel_id: conv.id,
          name: conv.name || conv.id,
          unread_count: unreadCount,
          messages: formatMessages(client, messages),
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
      mentions = await collectMentions(client, new Map());
    } catch (error) {
      errors.push({
        stage: "mentions",
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  const result = buildResult(unreadDms, unreadChannels, mentions);
  if (errors.length > 0) {
    result.errors = errors;
  }
  return result;
}

/**
 * Get all unread messages and mentions: counts first, scan fallback.
 * This does NOT mark any messages as read.
 */
export async function getUnread(
  client: SlackClient,
  options: GetUnreadOptions = {}
): Promise<GetUnreadResult> {
  const resolved: Required<GetUnreadOptions> = {
    includeChannels: options.includeChannels ?? true,
    includeDms: options.includeDms ?? true,
    includeMentions: options.includeMentions ?? true,
    includeThreads: options.includeThreads ?? true,
    maxMessagesPerConversation: options.maxMessagesPerConversation ?? 5,
    maxConversationsToCheck: options.maxConversationsToCheck ?? 20,
    maxDmsToScan: options.maxDmsToScan ?? 30,
    maxChannelsToScan: options.maxChannelsToScan ?? 50,
  };

  let fallbackReason: string;
  try {
    const result = await getUnreadByCounts(client, resolved);
    result.source = "counts";
    return result;
  } catch (error) {
    fallbackReason = error instanceof Error ? error.message : String(error);
  }

  const result = await getUnreadByScan(client, resolved);
  result.source = "scan_fallback";
  result.fallback_reason = fallbackReason;
  return result;
}
