/**
 * List Conversations Tool - List all available Slack conversations
 */

import { SlackClient } from "../slack-client";
import { getConversationType } from "../types/slack";

interface ListConversationsOptions {
  types?: "all" | "channels" | "dms";
}

interface ConversationEntry {
  id: string;
  name: string;
  type: string;
  unread_count?: number;
  has_unread?: boolean;
}

interface ListConversationsResult {
  channels?: ConversationEntry[];
  private_channels?: ConversationEntry[];
  direct_messages?: ConversationEntry[];
  group_dms?: ConversationEntry[];
  total_conversations: number;
}

/**
 * List all available conversations in the workspace.
 */
export async function listConversations(
  client: SlackClient,
  options: ListConversationsOptions
): Promise<ListConversationsResult> {
  const { types = "all" } = options;

  // Map type filter to Slack API types
  let slackTypes: string;
  if (types === "channels") {
    slackTypes = "public_channel,private_channel";
  } else if (types === "dms") {
    slackTypes = "im,mpim";
  } else {
    slackTypes = "public_channel,private_channel,mpim,im";
  }

  const conversations = await client.listConversations({
    types: slackTypes,
  });

  // Categorize conversations
  const channels: ConversationEntry[] = [];
  const privateChannels: ConversationEntry[] = [];
  const dms: ConversationEntry[] = [];
  const groupDms: ConversationEntry[] = [];

  for (const conv of conversations) {
    // Get display name - use cache for DMs
    let displayName = conv.name || conv.id;
    if (conv.is_im && conv.user) {
      const cachedUser = client.getCachedUser(conv.user);
      if (cachedUser) {
        displayName = `@${cachedUser.display_name || cachedUser.real_name || cachedUser.name}`;
      } else {
        displayName = `DM (${conv.user.substring(0, 8)}...)`;
      }
    }

    const entry: ConversationEntry = {
      id: conv.id,
      name: displayName,
      type: getConversationType(conv),
    };

    // Add unread info if available
    if (conv.unread_count_display !== undefined) {
      entry.unread_count = conv.unread_count_display;
    }

    if (conv.last_read) {
      entry.has_unread = true;
    }

    if (conv.is_im) {
      dms.push(entry);
    } else if (conv.is_mpim) {
      groupDms.push(entry);
    } else if (conv.is_private) {
      privateChannels.push(entry);
    } else {
      channels.push(entry);
    }
  }

  const result: ListConversationsResult = {
    total_conversations:
      channels.length + privateChannels.length + dms.length + groupDms.length,
  };

  if (channels.length > 0) {
    result.channels = channels;
  }
  if (privateChannels.length > 0) {
    result.private_channels = privateChannels;
  }
  if (dms.length > 0) {
    result.direct_messages = dms;
  }
  if (groupDms.length > 0) {
    result.group_dms = groupDms;
  }

  return result;
}
