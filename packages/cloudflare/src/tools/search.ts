/**
 * Search Tool - Comprehensive Slack message search
 */

import { SlackClient } from "../slack-client";
import { tsToDate } from "../types/slack";

interface SearchOptions {
  query: string;
  inChannel?: string;
  fromUser?: string;
  afterDate?: string;
  beforeDate?: string;
  hasLink?: boolean;
  hasReaction?: boolean;
  isThread?: boolean;
  limit?: number;
}

interface FormattedMessage {
  ts: string;
  user: string;
  text: string;
  time: string;
  thread_ts?: string;
  is_thread_reply?: boolean;
}

interface SearchResult {
  query: string;
  results: FormattedMessage[];
  total_matches: number;
  page: number;
  total_pages: number;
  showing: number;
}

/**
 * Search messages across the workspace.
 * This does NOT mark messages as read.
 */
export async function search(
  client: SlackClient,
  options: SearchOptions
): Promise<SearchResult> {
  const {
    query,
    inChannel,
    fromUser,
    afterDate,
    beforeDate,
    hasLink = false,
    hasReaction = false,
    isThread = false,
    limit = 20,
  } = options;

  // Build the full query with modifiers
  const queryParts: string[] = [query];

  if (inChannel) {
    // Handle both channel name and ID
    if (inChannel.startsWith("C") || inChannel.startsWith("G")) {
      queryParts.push(`in:${inChannel}`);
    } else if (inChannel.startsWith("@")) {
      queryParts.push(`in:${inChannel}`);
    } else {
      queryParts.push(`in:#${inChannel}`);
    }
  }

  if (fromUser) {
    if (fromUser.startsWith("U")) {
      queryParts.push(`from:<@${fromUser}>`);
    } else if (fromUser.startsWith("@")) {
      queryParts.push(`from:${fromUser}`);
    } else {
      queryParts.push(`from:@${fromUser}`);
    }
  }

  if (afterDate) {
    queryParts.push(`after:${afterDate}`);
  }

  if (beforeDate) {
    queryParts.push(`before:${beforeDate}`);
  }

  if (hasLink) {
    queryParts.push("has:link");
  }

  if (hasReaction) {
    queryParts.push("has:reaction");
  }

  if (isThread) {
    queryParts.push("is:thread");
  }

  const fullQuery = queryParts.join(" ");

  // Perform search
  const results = await client.searchMessages(fullQuery, {
    count: Math.min(limit, 100),
    sort: "timestamp",
    sortDir: "desc",
  });

  // Format results - use cached user names
  const formattedMessages: FormattedMessage[] = [];
  for (const msg of results.messages) {
    // Use cache, don't make API call for each message
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
      text:
        msg.text.length > 500 ? msg.text.substring(0, 500) + "..." : msg.text,
      time: tsToDate(msg.ts).toISOString().replace("T", " ").substring(0, 19),
    };

    if (msg.thread_ts) {
      formatted.thread_ts = msg.thread_ts;
      formatted.is_thread_reply = msg.thread_ts !== msg.ts;
    }

    formattedMessages.push(formatted);
  }

  return {
    query: fullQuery,
    results: formattedMessages,
    total_matches: results.total,
    page: results.page,
    total_pages: results.pages,
    showing: formattedMessages.length,
  };
}
