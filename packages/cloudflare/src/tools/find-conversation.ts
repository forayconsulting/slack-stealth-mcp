/**
 * Find Conversation Tool - Find channels, DMs, group DMs, and people by name
 */

import { SlackClient, normalizeName } from "../slack-client";
import type { SlackConversation, SlackUser } from "../types/slack";
import { getConversationType, getUserDisplayName } from "../types/slack";

interface FindConversationOptions {
  query: string;
  includeUsers?: boolean;
  limit?: number;
}

interface ConversationMatch {
  id: string;
  name: string;
  type: string;
  user_id?: string;
  score: number;
  hint?: string;
}

interface FindConversationResult {
  query: string;
  count: number;
  matches: ConversationMatch[];
  warning?: string;
  note?: string;
  hint?: string;
}

/**
 * Split a name/query into normalized word tokens
 */
function tokens(text: string): string[] {
  return text
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean);
}

/**
 * Score how well a candidate name matches the query (0-100).
 *
 * Tiers: exact 100, prefix 80, all query tokens present as words 60,
 * substring 40. Multi-token queries get a +10 bonus when every token
 * appears somewhere in the candidate.
 */
export function scoreName(query: string, candidate: string): number {
  const queryNorm = normalizeName(query);
  const candNorm = normalizeName(candidate);
  if (!queryNorm || !candNorm) return 0;

  const queryTokens = tokens(query);
  const candTokens = tokens(candidate);

  if (candNorm === queryNorm) return 100;

  let score = 0;
  if (candNorm.startsWith(queryNorm)) {
    score = 80;
  } else if (
    queryTokens.length > 0 &&
    queryTokens.every((qt) =>
      candTokens.some((ct) => ct === qt || ct.startsWith(qt))
    )
  ) {
    score = 60;
  } else if (candNorm.includes(queryNorm)) {
    score = 40;
  }

  if (score && queryTokens.length > 1) {
    score = Math.min(score + 10, 100);
  }

  return score;
}

/**
 * Resolve an mpdm-alice--bob--carol-1 name to participant display names
 */
export function mpimParticipants(
  conv: SlackConversation,
  client: SlackClient
): string[] {
  if (!conv.name || !conv.name.startsWith("mpdm-")) return [];
  const inner = conv.name.slice("mpdm-".length).replace(/-\d+$/, "");
  return inner.split("--").map((handle) => {
    const user = client.getUserByName(handle);
    return user ? getUserDisplayName(user) : handle;
  });
}

function bestUserScore(query: string, user: SlackUser): number {
  const candidates = [user.name, user.real_name, user.display_name].filter(
    (n): n is string => Boolean(n)
  );
  return Math.max(0, ...candidates.map((c) => scoreName(query, c)));
}

/**
 * Find channels, DMs, group DMs, and people by name.
 * This does NOT mark anything as read.
 */
export async function findConversation(
  client: SlackClient,
  options: FindConversationOptions
): Promise<FindConversationResult> {
  const { query, includeUsers = true, limit = 15 } = options;

  const directoryOk = await client.ensureUserDirectory();

  // Channels: ask Slack's server-side search (instant, and finds channels you
  // haven't joined). Fall back to the full cached listing only if the search
  // endpoint is unavailable.
  let searchOk = true;
  let channelConvs: SlackConversation[];
  try {
    channelConvs = await client.searchChannels(query);
  } catch {
    searchOk = false;
    channelConvs = (await client.ensureAllConversations()).filter(
      (c) => !(c.is_im || c.is_mpim)
    );
  }

  // DMs and group DMs: your membership set, not the whole workspace
  const memberConvs = await client.listMemberConversations({ types: "im,mpim" });

  const matches: ConversationMatch[] = [];
  const dmUserIds = new Set<string>();

  for (const conv of channelConvs) {
    if (!conv.name) continue;
    const base = scoreName(query, conv.name);
    // Server-side hits are already relevant; floor the score so they surface
    // even when Slack matched on purpose/topic, while exact/prefix name
    // matches still rank above the floor.
    const score = searchOk ? Math.max(base, 50) : base;
    if (score) {
      matches.push({
        id: conv.id,
        name: conv.name,
        type: getConversationType(conv),
        score,
      });
    }
  }

  for (const conv of memberConvs) {
    if (conv.is_im) {
      if (!conv.user) continue;
      dmUserIds.add(conv.user);
      const user = client.getCachedUser(conv.user);
      if (!user) continue;
      const score = bestUserScore(query, user);
      if (score) {
        matches.push({
          id: conv.id,
          name: `@${getUserDisplayName(user)}`,
          type: "dm",
          user_id: conv.user,
          score,
        });
      }
    } else if (conv.is_mpim) {
      const participants = mpimParticipants(conv, client);
      const display =
        participants.length > 0 ? participants.join(", ") : conv.name || conv.id;
      // Single-participant matches are discounted so the direct DM with
      // that person outranks group DMs they happen to be in
      const score = Math.max(
        scoreName(query, display),
        Math.floor(
          0.9 * Math.max(...participants.map((p) => scoreName(query, p)), 0)
        )
      );
      if (score) {
        matches.push({
          id: conv.id,
          name: display,
          type: "group_dm",
          score,
        });
      }
    }
  }

  // People in the directory with no existing DM are still reachable:
  // slack_reply with target=U... opens one
  if (includeUsers && directoryOk) {
    for (const user of client.directoryUsers()) {
      if (dmUserIds.has(user.id) || user.is_bot) continue;
      const score = bestUserScore(query, user);
      if (score) {
        matches.push({
          id: user.id,
          name: `@${getUserDisplayName(user)}`,
          type: "user",
          user_id: user.id,
          score,
          hint: "No DM yet - use slack_reply with this user ID as target to start one",
        });
      }
    }
  }

  matches.sort((a, b) => b.score - a.score);
  const topMatches = matches.slice(0, limit);

  const result: FindConversationResult = {
    query,
    count: topMatches.length,
    matches: topMatches,
  };
  if (!directoryOk) {
    result.warning =
      "User directory unavailable - DM and people matching may be incomplete";
  }
  if (!searchOk) {
    result.note =
      "Channel search endpoint unavailable - used the full cached listing instead";
  }
  if (topMatches.length === 0) {
    result.hint =
      "No matches. Try a shorter query, a person's Slack handle, or " +
      "slack_list_conversations to browse everything.";
  }
  return result;
}
