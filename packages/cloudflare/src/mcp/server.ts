/**
 * Slack Stealth MCP Server
 *
 * Remote MCP server for Slack interaction via Cloudflare Workers.
 * Uses OAuth 2.1 for authentication - Slack tokens are passed via OAuth props.
 */

import { McpAgent } from "agents/mcp";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import type { Env } from "../types/env";
import { SlackClient } from "../slack-client";
import type { OAuthProps, SlackTokens } from "../auth/oauth-handler";

// Tool implementations
import { getUnread } from "../tools/get-unread";
import { reply } from "../tools/reply";
import { search } from "../tools/search";
import { getContext } from "../tools/get-context";
import { findConversation } from "../tools/find-conversation";
import { listConversations } from "../tools/list-conversations";
import { markRead } from "../tools/mark-read";
import { react } from "../tools/react";

/**
 * MCP Server State
 */
interface McpState {
  // Currently empty - state is managed via OAuth props
}

/**
 * Props passed via OAuth token
 * Contains the authenticated user's Slack tokens
 */
interface McpProps extends Record<string, unknown> {
  slackTokens?: SlackTokens;
  authenticatedAt?: string;
}

// Standing guidance delivered to MCP clients at initialization.
// Not all clients surface this to the model, so the slack_reply tool
// description repeats the composition rules.
const SERVER_INSTRUCTIONS = `\
Slack Stealth MCP: read Slack without triggering read receipts; only
slack_mark_read changes read state.

When composing any Slack message, always write Slack mrkdwn, NOT standard
Markdown:
- *bold* (single asterisks), _italic_, ~strikethrough~, \`code\`, \`\`\`code blocks\`\`\`
- > for quotes; use - or • for bullet lists
- Never use ## headers - use a *bold* line instead
- Links: <https://example.com|link text>, never [text](url)

Mentions:
- Mention people as <@USERID> instead of writing their plain name whenever
  they are in the conversation. Use slack_find_conversation to look up a
  person's user ID by name.
- When addressing the whole group, open with <!here> (pings active members)
  or <!channel> (pings everyone). Use <!here> by default.
- Reference channels as <#C01234567>.

Channels and DMs can be referenced by ID, by pasted Slack URL, or found by
name/person with slack_find_conversation.`;

/**
 * Slack Stealth MCP Server - Durable Object
 *
 * Authentication is handled by OAuthProvider wrapper.
 * Slack tokens are received via this.props.slackTokens.
 */
export class SlackMcpServer extends McpAgent<Env, McpState, McpProps> {
  server = new McpServer(
    {
      name: "slack-stealth-mcp",
      version: "1.0.0",
    },
    {
      instructions: SERVER_INSTRUCTIONS,
    }
  );

  initialState: McpState = {};

  private _client?: SlackClient;

  /**
   * Get a SlackClient using tokens from OAuth props
   * This will throw if called without authentication (shouldn't happen - OAuth handles it)
   *
   * The client is memoized on the Durable Object so its in-memory caches
   * (users, conversations) survive across tool calls in a session.
   */
  private getClient(): SlackClient {
    if (!this.props.slackTokens) {
      throw new Error(
        "Not authenticated. Slack tokens not found in OAuth props."
      );
    }
    if (!this._client) {
      this._client = new SlackClient(this.props.slackTokens);
    }
    return this._client;
  }

  /**
   * Initialize MCP server and register all tools
   */
  async init() {
    // =========================================================================
    // slack_get_unread - Get all unread messages and mentions
    // =========================================================================
    this.server.tool(
      "slack_get_unread",
      "Get all unread messages and mentions across your Slack workspace. " +
        "This is the 'What's new?' tool - perfect for catching up on activity. " +
        "Returns unread DMs, channel messages with new activity, recent mentions, " +
        "and a summary of unread thread replies. Uses Slack's own badge state, " +
        "so it matches what the Slack app shows as unread. " +
        "If the result contains a 'truncated' section, more unread conversations " +
        "exist - call again with a higher max_conversations. " +
        "IMPORTANT: This does NOT mark any messages as read.",
      {
        include_channels: z
          .boolean()
          .default(true)
          .describe("Include unread channel messages"),
        include_dms: z
          .boolean()
          .default(true)
          .describe("Include unread DMs"),
        include_mentions: z
          .boolean()
          .default(true)
          .describe("Include recent mentions"),
        include_threads: z
          .boolean()
          .default(true)
          .describe("Include unread thread reply summary"),
        max_conversations: z
          .number()
          .default(20)
          .describe("Max conversations to fetch message previews for"),
        max_messages_per_conversation: z
          .number()
          .default(5)
          .describe("Max unread messages to show per conversation"),
        max_dms: z
          .number()
          .default(30)
          .describe("Cap on unread DMs considered"),
        max_channels: z
          .number()
          .default(50)
          .describe("Cap on unread channels considered"),
      },
      async (args) => {
        const client = this.getClient();
        const result = await getUnread(client, {
          includeChannels: args.include_channels,
          includeDms: args.include_dms,
          includeMentions: args.include_mentions,
          includeThreads: args.include_threads,
          maxConversationsToCheck: args.max_conversations,
          maxMessagesPerConversation: args.max_messages_per_conversation,
          maxDmsToScan: args.max_dms,
          maxChannelsToScan: args.max_channels,
        });
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      }
    );

    // =========================================================================
    // slack_reply - Send a message or reply
    // =========================================================================
    this.server.tool(
      "slack_reply",
      "Send a message or reply in Slack. " +
        "Can send to channels (C... or a pasted Slack URL), DMs (D...), or users (U... - will open DM). " +
        "ALWAYS write Slack mrkdwn, NOT standard Markdown: *bold* with SINGLE asterisks, " +
        "_italic_, ~strikethrough~, > quotes, - bullets, links as <https://example.com|text>. " +
        "Do NOT use **double asterisks**, ## headers, or [text](url) links. " +
        "ALWAYS mention people as <@USERID> instead of plain names when they are in the " +
        "conversation (use slack_find_conversation to get user IDs). When addressing the " +
        "whole group, open with <!here> (active members) or <!channel> (everyone). " +
        "IMPORTANT: This does NOT mark the channel as read.",
      {
        target: z
          .string()
          .describe("Channel ID (C...), User ID (U...), DM ID (D...), or Slack URL"),
        message: z
          .string()
          .describe("Message text in Slack mrkdwn (*bold*, <@USERID> mentions, <!here>)"),
        thread_ts: z
          .string()
          .optional()
          .describe("Parent message timestamp for thread reply"),
        broadcast: z
          .boolean()
          .default(false)
          .describe("Also post thread reply to channel"),
      },
      async (args) => {
        const client = this.getClient();
        const result = await reply(client, {
          target: args.target,
          message: args.message,
          threadTs: args.thread_ts,
          broadcast: args.broadcast,
        });
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      }
    );

    // =========================================================================
    // slack_search - Search messages
    // =========================================================================
    this.server.tool(
      "slack_search",
      "Search messages across your Slack workspace. " +
        "Supports modifiers: in_channel, from_user, after_date, before_date, " +
        "has_link, has_reaction, is_thread. " +
        "This does NOT mark messages as read.",
      {
        query: z.string().describe("Search terms"),
        in_channel: z
          .string()
          .optional()
          .describe("Channel name or ID to search in"),
        from_user: z
          .string()
          .optional()
          .describe("User ID or @name to filter by sender"),
        after_date: z
          .string()
          .optional()
          .describe("Only messages after this date (YYYY-MM-DD)"),
        before_date: z
          .string()
          .optional()
          .describe("Only messages before this date (YYYY-MM-DD)"),
        has_link: z
          .boolean()
          .default(false)
          .describe("Only messages with links"),
        has_reaction: z
          .boolean()
          .default(false)
          .describe("Only messages with reactions"),
        is_thread: z
          .boolean()
          .default(false)
          .describe("Only messages in threads"),
        limit: z
          .number()
          .default(20)
          .describe("Max results (default: 20, max: 100)"),
      },
      async (args) => {
        const client = this.getClient();
        const result = await search(client, {
          query: args.query,
          inChannel: args.in_channel,
          fromUser: args.from_user,
          afterDate: args.after_date,
          beforeDate: args.before_date,
          hasLink: args.has_link,
          hasReaction: args.has_reaction,
          isThread: args.is_thread,
          limit: args.limit,
        });
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      }
    );

    // =========================================================================
    // slack_get_context - Get conversation context
    // =========================================================================
    this.server.tool(
      "slack_get_context",
      "Get messages from a conversation for context. " +
        "Fetches recent messages from a channel or thread. " +
        "If message_ts points to a thread parent, returns the full thread. " +
        "IMPORTANT: This does NOT mark messages as read.",
      {
        channel: z
          .string()
          .describe("Channel ID (C..., D..., or G...) or Slack URL"),
        message_ts: z
          .string()
          .optional()
          .describe("Optional message timestamp to get context around"),
        context_size: z
          .number()
          .default(10)
          .describe("Number of messages to fetch"),
      },
      async (args) => {
        const client = this.getClient();
        const result = await getContext(client, {
          channel: args.channel,
          messageTs: args.message_ts,
          contextSize: args.context_size,
        });
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      }
    );

    // =========================================================================
    // slack_find_conversation - Find channels, DMs, and people by name
    // =========================================================================
    this.server.tool(
      "slack_find_conversation",
      "Find Slack channels, DMs, group DMs, and people by name. " +
        "Use this whenever the user refers to a conversation by name instead of ID: " +
        "'the design channel', 'my DM with Alice', 'the group chat with Bob and Carol'. " +
        "Returns ranked matches with IDs that other tools accept directly. " +
        "Also use it to look up a person's user ID for <@USERID> mentions. " +
        "Matches people even with no existing DM - reply with target=<user_id> to start one. " +
        "IMPORTANT: This does NOT mark anything as read.",
      {
        query: z
          .string()
          .describe("Name to find: channel name, person's name, or Slack handle"),
        include_users: z
          .boolean()
          .default(true)
          .describe("Also match workspace users without an existing DM"),
        limit: z
          .number()
          .default(15)
          .describe("Max matches to return"),
      },
      async (args) => {
        const client = this.getClient();
        const result = await findConversation(client, {
          query: args.query,
          includeUsers: args.include_users,
          limit: args.limit,
        });
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      }
    );

    // =========================================================================
    // slack_list_conversations - List all conversations
    // =========================================================================
    this.server.tool(
      "slack_list_conversations",
      "List the conversations you're a member of (your channels, private " +
        "channels, DMs, and group DMs - not every channel in the workspace). " +
        "To find a specific channel or person by name, including channels you " +
        "haven't joined, use slack_find_conversation instead.",
      {
        types: z
          .enum(["all", "channels", "dms"])
          .default("all")
          .describe("Filter: 'all', 'channels', or 'dms'"),
      },
      async (args) => {
        const client = this.getClient();
        const result = await listConversations(client, {
          types: args.types,
        });
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      }
    );

    // =========================================================================
    // slack_mark_read - Mark conversation as read
    // =========================================================================
    this.server.tool(
      "slack_mark_read",
      "Mark a conversation as read. " +
        "This is the ONLY tool that affects your read state. " +
        "Use it when you deliberately want to mark messages as read. " +
        "If no timestamp is provided, marks all messages as read.",
      {
        channel: z.string().describe("Channel ID (or Slack URL) to mark as read"),
        timestamp: z
          .string()
          .optional()
          .describe("Mark as read up to this message"),
      },
      async (args) => {
        const client = this.getClient();
        const result = await markRead(client, {
          channel: args.channel,
          timestamp: args.timestamp,
        });
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      }
    );

    // =========================================================================
    // slack_react - Add/remove emoji reactions
    // =========================================================================
    this.server.tool(
      "slack_react",
      "Add or remove an emoji reaction on a message. " +
        "Perfect for acknowledging messages without sending a full reply. " +
        "Common: thumbsup, pray, eyes, heart, fire, 100, joy. " +
        "Use emoji names without colons (e.g., 'thumbsup' not ':thumbsup:'). " +
        "IMPORTANT: This does NOT mark messages as read.",
      {
        channel: z
          .string()
          .describe("Channel ID (or Slack URL) containing the message"),
        message_ts: z
          .string()
          .describe("Timestamp of the message to react to"),
        emoji: z
          .string()
          .describe("Emoji name without colons (e.g., 'thumbsup')"),
        remove: z
          .boolean()
          .default(false)
          .describe("Remove reaction instead of adding"),
      },
      async (args) => {
        const client = this.getClient();
        const result = await react(client, {
          channel: args.channel,
          messageTs: args.message_ts,
          emoji: args.emoji,
          remove: args.remove,
        });
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      }
    );

    // =========================================================================
    // slack_workspace_info - Get workspace info (helper tool)
    // =========================================================================
    this.server.tool(
      "slack_workspace_info",
      "Get information about the currently connected Slack workspace.",
      {},
      async () => {
        if (!this.props.slackTokens) {
          return {
            content: [
              {
                type: "text",
                text: JSON.stringify({ error: "Not authenticated" }, null, 2),
              },
            ],
          };
        }

        const client = this.getClient();
        try {
          const auth = await client.testAuth();
          return {
            content: [
              {
                type: "text",
                text: JSON.stringify(
                  {
                    team: auth.team,
                    team_id: auth.team_id,
                    user: auth.user,
                    user_id: auth.user_id,
                    authenticated_at: this.props.authenticatedAt,
                  },
                  null,
                  2
                ),
              },
            ],
          };
        } catch (error) {
          return {
            content: [
              {
                type: "text",
                text: JSON.stringify(
                  { error: String(error) },
                  null,
                  2
                ),
              },
            ],
          };
        }
      }
    );
  }
}
