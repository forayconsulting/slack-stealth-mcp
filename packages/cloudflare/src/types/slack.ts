/**
 * Slack API Types
 *
 * TypeScript interfaces matching the Python Pydantic models
 */

/**
 * Represents a Slack user
 */
export interface SlackUser {
  id: string;
  name: string;
  real_name?: string;
  display_name?: string;
  is_bot: boolean;
}

/**
 * Get the best display name for a user
 */
export function getUserDisplayName(user: SlackUser): string {
  return user.display_name || user.real_name || user.name;
}

/**
 * Represents a Slack message
 */
export interface SlackMessage {
  ts: string;
  text: string;
  user?: string;
  user_name?: string;
  thread_ts?: string;
  reply_count?: number;
  reactions: Array<{
    name: string;
    count: number;
    users: string[];
  }>;
  attachments: Array<Record<string, unknown>>;
  blocks: Array<Record<string, unknown>>;
  channel?: {
    id: string;
    name?: string;
  };
  permalink?: string;
}

/**
 * Check if a message is a thread parent
 */
export function isThreadParent(msg: SlackMessage): boolean {
  return msg.thread_ts === msg.ts && msg.reply_count !== undefined;
}

/**
 * Check if a message is a thread reply
 */
export function isThreadReply(msg: SlackMessage): boolean {
  return msg.thread_ts !== undefined && msg.thread_ts !== msg.ts;
}

/**
 * Convert Slack timestamp to Date
 */
export function tsToDate(ts: string): Date {
  return new Date(parseFloat(ts.split(".")[0]) * 1000);
}

/**
 * Represents a Slack conversation (channel, DM, or group)
 */
export interface SlackConversation {
  id: string;
  name?: string;
  is_channel: boolean;
  is_group: boolean;
  is_im: boolean;
  is_mpim: boolean;
  is_private: boolean;
  is_archived: boolean;
  is_member: boolean;
  user?: string;
  last_read?: string;
  unread_count?: number;
  unread_count_display?: number;
}

/**
 * Get the best display name for a conversation
 */
export function getConversationDisplayName(conv: SlackConversation): string {
  if (conv.name) return conv.name;
  if (conv.is_im && conv.user) return `DM:${conv.user}`;
  return conv.id;
}

/**
 * Get conversation type as string
 */
export function getConversationType(
  conv: SlackConversation
): "dm" | "group_dm" | "private_channel" | "channel" {
  if (conv.is_im) return "dm";
  if (conv.is_mpim) return "group_dm";
  if (conv.is_private) return "private_channel";
  return "channel";
}

/**
 * Search result
 */
export interface SlackSearchResult {
  messages: SlackMessage[];
  total: number;
  page: number;
  pages: number;
}

/**
 * Unread summary
 */
export interface UnreadSummary {
  unread_dms: Array<{
    channel_id: string;
    user_id?: string;
    user_name?: string;
    unread_count: number;
    latest_message?: SlackMessage;
  }>;
  unread_channels: Array<{
    channel_id: string;
    channel_name: string;
    unread_count: number;
    latest_message?: SlackMessage;
  }>;
  mentions: Array<{
    channel_id: string;
    channel_name?: string;
    message: SlackMessage;
  }>;
  total_unread_dms: number;
  total_unread_channels: number;
  total_mentions: number;
}

/**
 * Get human-readable summary
 */
export function getUnreadSummaryText(summary: UnreadSummary): string {
  const parts: string[] = [];
  if (summary.total_unread_dms > 0) {
    parts.push(`${summary.total_unread_dms} unread DM(s)`);
  }
  if (summary.total_unread_channels > 0) {
    parts.push(`${summary.total_unread_channels} channel(s) with unread messages`);
  }
  if (summary.total_mentions > 0) {
    parts.push(`${summary.total_mentions} mention(s)`);
  }
  return parts.length > 0 ? parts.join(", ") : "No unread messages";
}

/**
 * Workspace configuration
 */
export interface WorkspaceConfig {
  xoxc_token: string;
  xoxd_cookie: string;
  name?: string;
}

/**
 * Auth test response
 */
export interface AuthTestResponse {
  ok: boolean;
  url: string;
  team: string;
  user: string;
  team_id: string;
  user_id: string;
  bot_id?: string;
  is_enterprise_install: boolean;
}
