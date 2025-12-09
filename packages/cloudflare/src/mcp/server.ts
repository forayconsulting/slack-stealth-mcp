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

/**
 * Slack Stealth MCP Server - Durable Object
 *
 * Authentication is handled by OAuthProvider wrapper.
 * Slack tokens are received via this.props.slackTokens.
 */
export class SlackMcpServer extends McpAgent<Env, McpState, McpProps> {
  server = new McpServer({
    name: "slack-stealth-mcp",
    version: "1.0.0",
  });

  initialState: McpState = {};

  /**
   * Get a SlackClient using tokens from OAuth props
   * This will throw if called without authentication (shouldn't happen - OAuth handles it)
   */
  private getClient(): SlackClient {
    if (!this.props.slackTokens) {
      throw new Error(
        "Not authenticated. Slack tokens not found in OAuth props."
      );
    }
    return new SlackClient(this.props.slackTokens);
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
        "Returns unread DMs, channel messages with new activity, and recent mentions. " +
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
      },
      async (args) => {
        const client = this.getClient();
        const result = await getUnread(client, {
          includeChannels: args.include_channels,
          includeDms: args.include_dms,
          includeMentions: args.include_mentions,
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
        "Can send to channels (C...), DMs (D...), or users (U... - will open DM). " +
        "Supports Slack mrkdwn: *bold*, _italic_, ~strikethrough~, <@U...> mentions. " +
        "IMPORTANT: This does NOT mark the channel as read.",
      {
        target: z
          .string()
          .describe("Channel ID (C...), User ID (U...), or DM ID (D...)"),
        message: z
          .string()
          .describe("Message text (supports Slack mrkdwn)"),
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
          .describe("Channel ID (C..., D..., or G...)"),
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
    // slack_list_conversations - List all conversations
    // =========================================================================
    this.server.tool(
      "slack_list_conversations",
      "List all available conversations in the workspace. " +
        "Returns channels, private channels, DMs, and group DMs that you have access to.",
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
        channel: z.string().describe("Channel ID to mark as read"),
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
          .describe("Channel ID containing the message"),
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
