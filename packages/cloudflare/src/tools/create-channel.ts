/**
 * Create Channel Tool - Create a channel, optionally set description/topic and invite people
 *
 * Port of packages/python/src/slack_stealth_mcp/tools/create_channel.py.
 */

import { SlackClient, SlackAPIError } from "../slack-client";

// Slack channel names: lowercase letters, numbers, hyphens, underscores; max 80
const MAX_CHANNEL_NAME = 80;
const USER_ID_RE = /^[UW][A-Z0-9]+$/;

const ERROR_HINTS: Record<string, string> = {
  name_taken: "a channel with that name already exists (it may be archived)",
  restricted_action: "this workspace does not let you create channels",
  invalid_name_specials: "the name has characters Slack does not allow",
};

/**
 * Turn a human name into Slack's channel-name form.
 * "Q4 Planning!" becomes "q4-planning". A leading "#" is dropped.
 */
export function normalizeChannelName(name: string): string {
  let slug = name.trim().replace(/^#+/, "").toLowerCase();
  slug = slug.replace(/\s+/g, "-");
  slug = slug.replace(/[^a-z0-9_-]/g, "");
  slug = slug.replace(/-{2,}/g, "-").replace(/^-+|-+$/g, "");
  return slug.slice(0, MAX_CHANNEL_NAME);
}

/** Accept a list or comma-separated string of user IDs (or <@U...> mentions). */
function userIds(invite?: string[] | string): string[] {
  if (!invite) return [];
  const items = typeof invite === "string" ? invite.split(",") : invite;
  return items
    .map((item) => item.trim().replace(/^[<>@]+|[<>@]+$/g, "").split("|")[0])
    .filter((item) => item.length > 0);
}

interface CreateChannelOptions {
  name: string;
  isPrivate?: boolean;
  description?: string;
  topic?: string;
  invite?: string[] | string;
}

/**
 * Create a channel. Later steps that fail are reported in "warnings"
 * instead of failing the call, because the channel already exists.
 */
export async function createChannel(
  client: SlackClient,
  options: CreateChannelOptions
): Promise<Record<string, unknown>> {
  const { name, isPrivate = false, description, topic } = options;

  const slug = normalizeChannelName(name);
  if (!slug) {
    return {
      ok: false,
      error:
        `'${name}' has no usable characters for a channel name ` +
        "(use lowercase letters, numbers, hyphens, underscores)",
    };
  }

  const ids = userIds(options.invite);
  for (const uid of ids) {
    if (!USER_ID_RE.test(uid)) {
      return {
        ok: false,
        error:
          `invite expects user IDs (U...), got '${uid}'. ` +
          "Use slack_find_conversation to look up a person's ID.",
      };
    }
  }

  let conv;
  try {
    conv = await client.createConversation(slug, isPrivate);
  } catch (error) {
    const code = (error as SlackAPIError).error || (error as Error).message;
    const hint = ERROR_HINTS[code];
    return {
      ok: false,
      error: `Failed to create channel '${slug}': ${code}` + (hint ? ` (${hint})` : ""),
    };
  }

  const result: Record<string, unknown> = {
    ok: true,
    channel_id: conv.id,
    name: conv.name || slug,
    is_private: isPrivate,
  };
  if (slug !== name) result.requested_name = name;
  const warnings: string[] = [];
  const code = (error: unknown) => (error as SlackAPIError).error || (error as Error).message;

  if (description) {
    try {
      await client.setConversationPurpose(conv.id, description);
      result.description = description;
    } catch (error) {
      warnings.push(`description not set: ${code(error)}`);
    }
  }

  if (topic) {
    try {
      await client.setConversationTopic(conv.id, topic);
      result.topic = topic;
    } catch (error) {
      warnings.push(`topic not set: ${code(error)}`);
    }
  }

  if (ids.length > 0) {
    try {
      const errors = await client.inviteToConversation(conv.id, ids);
      const failed = new Map<string, string>();
      for (const e of errors) {
        if (e.user) failed.set(e.user, e.error || "unknown_error");
      }
      result.invited = ids.filter((u) => !failed.has(u));
      for (const [user, err] of failed) {
        warnings.push(`could not invite ${user}: ${err}`);
      }
      if (errors.length > 0 && failed.size === 0) {
        for (const e of errors) warnings.push(`invite error: ${e.error}`);
      }
    } catch (error) {
      warnings.push(`invites failed: ${code(error)}`);
    }
  }

  if (warnings.length > 0) result.warnings = warnings;
  return result;
}
