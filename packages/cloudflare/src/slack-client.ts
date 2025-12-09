/**
 * Slack API Client
 *
 * Async client for Slack API using xoxc/xoxd session tokens.
 * Ported from Python to TypeScript for Cloudflare Workers.
 *
 * Key features:
 * - Rate limiting with exponential backoff
 * - In-memory caching for users and conversations
 * - All read operations do NOT mark messages as read (stealth)
 */

import { RateLimiter } from "./rate-limiter";
import type {
  SlackConversation,
  SlackMessage,
  SlackSearchResult,
  SlackUser,
  WorkspaceConfig,
  AuthTestResponse,
} from "./types/slack";

const SLACK_API_BASE = "https://slack.com/api";

/**
 * Slack API Error
 */
export class SlackAPIError extends Error {
  public readonly error: string;
  public readonly response: Record<string, unknown>;

  constructor(error: string, response?: Record<string, unknown>) {
    super(`Slack API error: ${error}`);
    this.name = "SlackAPIError";
    this.error = error;
    this.response = response || {};
  }
}

/**
 * Helper to sleep for a given duration
 */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Async Slack API Client
 */
export class SlackClient {
  private xoxcToken: string;
  private xoxdCookie: string;
  private rateLimiter: RateLimiter;

  // In-memory caches
  private usersCache: Map<string, SlackUser> = new Map();
  private conversationsCache: Map<string, SlackConversation> = new Map();
  private lastReadCache: Map<string, string> = new Map();
  private cacheTime: number = 0;

  /**
   * Create a Slack client
   * @param config Workspace configuration with tokens
   */
  constructor(config: WorkspaceConfig) {
    this.xoxcToken = config.xoxc_token;
    this.xoxdCookie = config.xoxd_cookie;
    this.rateLimiter = new RateLimiter(2); // 2 requests/sec
  }

  /**
   * Make a rate-limited request to Slack API
   */
  private async request<T = Record<string, unknown>>(
    method: "GET" | "POST",
    endpoint: string,
    options: {
      params?: Record<string, string | number | boolean>;
      data?: Record<string, string | number | boolean>;
    } = {}
  ): Promise<T> {
    await this.rateLimiter.acquire();

    // Build URL with query params for GET
    const url = new URL(`${SLACK_API_BASE}/${endpoint}`);
    if (method === "GET" && options.params) {
      for (const [key, value] of Object.entries(options.params)) {
        url.searchParams.set(key, String(value));
      }
    }

    // Build request
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.xoxcToken}`,
      Cookie: `d=${this.xoxdCookie}`,
    };

    let body: string | undefined;
    if (method === "POST" && options.data) {
      headers["Content-Type"] = "application/x-www-form-urlencoded";
      body = new URLSearchParams(
        Object.fromEntries(
          Object.entries(options.data).map(([k, v]) => [k, String(v)])
        )
      ).toString();
    }

    try {
      const response = await fetch(url.toString(), {
        method,
        headers,
        body,
      });

      const data = (await response.json()) as Record<string, unknown>;

      // Check for Slack API errors
      if (!data.ok) {
        const error = (data.error as string) || "unknown_error";

        // Handle rate limiting
        if (error === "ratelimited") {
          this.rateLimiter.backoff();
          const retryAfter = parseInt(
            response.headers.get("Retry-After") || "60",
            10
          );
          await sleep(retryAfter * 1000);
          return this.request(method, endpoint, options);
        }

        throw new SlackAPIError(error, data);
      }

      this.rateLimiter.resetBackoff();
      return data as T;
    } catch (error) {
      if (error instanceof SlackAPIError) {
        throw error;
      }
      throw new SlackAPIError(
        error instanceof Error ? error.message : "Unknown error"
      );
    }
  }

  // =========================================================================
  // Conversations API
  // =========================================================================

  /**
   * List conversations the user has access to
   * @param options Filtering options
   */
  async listConversations(options: {
    types?: string;
    excludeArchived?: boolean;
    limit?: number;
    maxPages?: number;
  } = {}): Promise<SlackConversation[]> {
    const {
      types = "public_channel,private_channel,mpim,im",
      excludeArchived = true,
      limit = 200,
      maxPages = 3,
    } = options;

    const conversations: SlackConversation[] = [];
    let cursor: string | undefined;
    let pagesFetched = 0;

    while (true) {
      const params: Record<string, string | number | boolean> = {
        types,
        exclude_archived: excludeArchived,
        limit,
      };
      if (cursor) {
        params.cursor = cursor;
      }

      const data = await this.request<{
        channels: Array<Record<string, unknown>>;
        response_metadata?: { next_cursor?: string };
      }>("GET", "conversations.list", { params });

      pagesFetched++;

      for (const ch of data.channels || []) {
        const conv = this.parseConversation(ch);
        conversations.push(conv);
        this.conversationsCache.set(conv.id, conv);
        if (conv.last_read) {
          this.lastReadCache.set(conv.id, conv.last_read);
        }
      }

      cursor = data.response_metadata?.next_cursor;
      if (!cursor || (maxPages > 0 && pagesFetched >= maxPages)) {
        break;
      }
    }

    this.cacheTime = Date.now();
    return conversations;
  }

  /**
   * Get detailed info about a conversation
   */
  async getConversationInfo(channel: string): Promise<SlackConversation> {
    const data = await this.request<{ channel: Record<string, unknown> }>(
      "GET",
      "conversations.info",
      {
        params: { channel, include_num_members: true },
      }
    );

    const conv = this.parseConversation(data.channel);
    this.conversationsCache.set(conv.id, conv);
    if (conv.last_read) {
      this.lastReadCache.set(conv.id, conv.last_read);
    }
    return conv;
  }

  /**
   * Get message history for a conversation
   * NOTE: This does NOT mark messages as read
   */
  async getConversationHistory(
    channel: string,
    options: {
      oldest?: string;
      latest?: string;
      limit?: number;
      inclusive?: boolean;
    } = {}
  ): Promise<SlackMessage[]> {
    const { limit = 100, inclusive = false } = options;

    const params: Record<string, string | number | boolean> = {
      channel,
      limit,
      inclusive,
    };
    if (options.oldest) params.oldest = options.oldest;
    if (options.latest) params.latest = options.latest;

    const data = await this.request<{ messages: Array<Record<string, unknown>> }>(
      "GET",
      "conversations.history",
      { params }
    );

    return (data.messages || []).map((msg) => this.parseMessage(msg));
  }

  /**
   * Get all replies in a thread
   * NOTE: This does NOT mark messages as read
   */
  async getThreadReplies(
    channel: string,
    threadTs: string,
    limit: number = 100
  ): Promise<SlackMessage[]> {
    const data = await this.request<{ messages: Array<Record<string, unknown>> }>(
      "GET",
      "conversations.replies",
      {
        params: { channel, ts: threadTs, limit },
      }
    );

    return (data.messages || []).map((msg) => this.parseMessage(msg));
  }

  /**
   * Open or get existing DM/group DM
   */
  async openConversation(users: string[]): Promise<string> {
    const data = await this.request<{
      channel: { id: string };
    }>("POST", "conversations.open", {
      data: { users: users.join(","), return_im: true },
    });

    return data.channel.id;
  }

  /**
   * Mark conversation as read up to a timestamp
   * This is the ONLY method that affects read state
   */
  async markConversation(channel: string, ts: string): Promise<void> {
    await this.request("POST", "conversations.mark", {
      data: { channel, ts },
    });
    this.lastReadCache.set(channel, ts);
  }

  // =========================================================================
  // Messages API
  // =========================================================================

  /**
   * Post a message to a channel or thread
   * NOTE: This does NOT mark the channel as read
   */
  async postMessage(
    channel: string,
    text: string,
    options: {
      threadTs?: string;
      replyBroadcast?: boolean;
    } = {}
  ): Promise<SlackMessage> {
    const data: Record<string, string | number | boolean> = {
      channel,
      text,
    };

    if (options.threadTs) {
      data.thread_ts = options.threadTs;
      if (options.replyBroadcast) {
        data.reply_broadcast = true;
      }
    }

    const response = await this.request<{
      message: Record<string, unknown>;
    }>("POST", "chat.postMessage", { data });

    return this.parseMessage(response.message);
  }

  // =========================================================================
  // Reactions API
  // =========================================================================

  /**
   * Add an emoji reaction to a message
   * @param channel Channel ID containing the message
   * @param timestamp Message timestamp to react to
   * @param name Emoji name without colons (e.g., "thumbsup")
   */
  async addReaction(
    channel: string,
    timestamp: string,
    name: string
  ): Promise<void> {
    await this.request("POST", "reactions.add", {
      data: { channel, timestamp, name },
    });
  }

  /**
   * Remove an emoji reaction from a message
   */
  async removeReaction(
    channel: string,
    timestamp: string,
    name: string
  ): Promise<void> {
    await this.request("POST", "reactions.remove", {
      data: { channel, timestamp, name },
    });
  }

  // =========================================================================
  // Search API
  // =========================================================================

  /**
   * Search messages in the workspace
   */
  async searchMessages(
    query: string,
    options: {
      sort?: "score" | "timestamp";
      sortDir?: "asc" | "desc";
      count?: number;
      page?: number;
    } = {}
  ): Promise<SlackSearchResult> {
    const { sort = "timestamp", sortDir = "desc", count = 20, page = 1 } = options;

    const data = await this.request<{
      messages: {
        matches: Array<Record<string, unknown>>;
        paging: { total: number; page: number; pages: number };
      };
    }>("GET", "search.messages", {
      params: {
        query,
        sort,
        sort_dir: sortDir,
        count: Math.min(count, 100),
        page,
      },
    });

    const messages = (data.messages?.matches || []).map((msg) =>
      this.parseMessage(msg)
    );
    const paging = data.messages?.paging || { total: 0, page: 1, pages: 1 };

    return {
      messages,
      total: paging.total,
      page: paging.page,
      pages: paging.pages,
    };
  }

  // =========================================================================
  // Users API
  // =========================================================================

  /**
   * Get info about a user (cached)
   */
  async getUserInfo(userId: string): Promise<SlackUser> {
    // Check cache first
    const cached = this.usersCache.get(userId);
    if (cached) return cached;

    const data = await this.request<{
      user: {
        id: string;
        name: string;
        real_name?: string;
        is_bot?: boolean;
        profile?: { display_name?: string };
      };
    }>("GET", "users.info", {
      params: { user: userId },
    });

    const user: SlackUser = {
      id: data.user.id,
      name: data.user.name || "",
      real_name: data.user.real_name,
      display_name: data.user.profile?.display_name,
      is_bot: data.user.is_bot || false,
    };

    this.usersCache.set(userId, user);
    return user;
  }

  /**
   * Resolve a user ID to a display name
   */
  async resolveUserName(userId: string): Promise<string> {
    try {
      const user = await this.getUserInfo(userId);
      return user.display_name || user.real_name || user.name;
    } catch {
      return userId;
    }
  }

  /**
   * Prefetch and cache info for multiple users
   * Useful before displaying messages to ensure names are available
   */
  async prefetchUsers(userIds: string[], maxFetch: number = 20): Promise<void> {
    // Filter out already-cached users
    const uncached = userIds.filter(
      (id) => id && !this.usersCache.has(id)
    );

    if (uncached.length === 0) return;

    // Limit to avoid rate limit issues
    for (const userId of uncached.slice(0, maxFetch)) {
      try {
        await this.getUserInfo(userId);
      } catch {
        // Silently ignore - will show ID as fallback
      }
    }
  }

  // =========================================================================
  // Auth API
  // =========================================================================

  /**
   * Test authentication and get workspace info
   */
  async testAuth(): Promise<AuthTestResponse> {
    return this.request<AuthTestResponse>("GET", "auth.test");
  }

  // =========================================================================
  // Cache Helpers
  // =========================================================================

  /**
   * Get cached last_read timestamp for a channel
   */
  getLastRead(channel: string): string | undefined {
    return this.lastReadCache.get(channel);
  }

  /**
   * Update cached last_read timestamp
   */
  setLastRead(channel: string, ts: string): void {
    this.lastReadCache.set(channel, ts);
  }

  /**
   * Get cached user (if available)
   */
  getCachedUser(userId: string): SlackUser | undefined {
    return this.usersCache.get(userId);
  }

  /**
   * Get cached conversation (if available)
   */
  getCachedConversation(channelId: string): SlackConversation | undefined {
    return this.conversationsCache.get(channelId);
  }

  /**
   * Clear all caches
   */
  clearCaches(): void {
    this.usersCache.clear();
    this.conversationsCache.clear();
    this.lastReadCache.clear();
    this.cacheTime = 0;
  }

  // =========================================================================
  // Parsing Helpers
  // =========================================================================

  private parseConversation(data: Record<string, unknown>): SlackConversation {
    return {
      id: data.id as string,
      name: data.name as string | undefined,
      is_channel: (data.is_channel as boolean) || false,
      is_group: (data.is_group as boolean) || false,
      is_im: (data.is_im as boolean) || false,
      is_mpim: (data.is_mpim as boolean) || false,
      is_private: (data.is_private as boolean) || false,
      is_archived: (data.is_archived as boolean) || false,
      is_member: (data.is_member as boolean) ?? true,
      user: data.user as string | undefined,
      last_read: data.last_read as string | undefined,
      unread_count: data.unread_count as number | undefined,
      unread_count_display: data.unread_count_display as number | undefined,
    };
  }

  private parseMessage(data: Record<string, unknown>): SlackMessage {
    const channel = data.channel as { id: string; name?: string } | string | undefined;

    return {
      ts: data.ts as string,
      text: (data.text as string) || "",
      user: data.user as string | undefined,
      user_name: data.username as string | undefined,
      thread_ts: data.thread_ts as string | undefined,
      reply_count: data.reply_count as number | undefined,
      reactions: (data.reactions as SlackMessage["reactions"]) || [],
      attachments: (data.attachments as SlackMessage["attachments"]) || [],
      blocks: (data.blocks as SlackMessage["blocks"]) || [],
      channel: typeof channel === "string"
        ? { id: channel }
        : channel as SlackMessage["channel"],
      permalink: data.permalink as string | undefined,
    };
  }
}
