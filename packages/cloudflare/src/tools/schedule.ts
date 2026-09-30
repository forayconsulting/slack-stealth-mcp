/**
 * Schedule Tools - Send messages later via Slack's drafts API
 *
 * Uses the same mechanism behind the Slack app's "Schedule for later": a
 * draft with date_scheduled is sent by Slack's servers at that time and
 * appears in the user's Scheduled list, where it can be edited or cancelled.
 *
 * Port of packages/python/src/slack_stealth_mcp/tools/schedule.py.
 */

import { SlackClient, SlackAPIError } from "../slack-client";
import type { SlackDraft } from "../types/slack";
import { resolveChannelRef } from "../refs";
import { mrkdwnToRichText, richTextToPlain, type RichElement } from "../rich-text";
import { conversationLabel } from "./reply";

// Slack's own limit for scheduled messages
const MAX_SCHEDULE_DAYS = 120;
// Too close to now and the send can race the create
const MIN_LEAD_SECONDS = 10;

const ERROR_HINTS: Record<string, string> = {
  attached_draft_exists:
    "that conversation already has an unsent draft in the composer; " +
    "send or clear it in Slack, or schedule into a thread instead",
  invalid_channel: "target must resolve to a channel or DM ID",
};

/**
 * Parse a send time into Unix seconds.
 *
 * Accepts Unix seconds or ISO-8601 with a timezone offset. Workers run in
 * UTC, so an ISO time without an offset is rejected rather than guessed.
 */
export function parseSendAt(sendAt: string | number): number {
  if (typeof sendAt === "number") return Math.floor(sendAt);
  const value = sendAt.trim();
  if (/^\d+(\.\d+)?$/.test(value)) return Math.floor(parseFloat(value));

  const hasOffset = /(Z|[+-]\d{2}:?\d{2})$/i.test(value);
  const ms = Date.parse(value);
  if (!hasOffset || Number.isNaN(ms)) {
    throw new Error(
      `Could not parse send_at "${sendAt}". Use ISO-8601 with a timezone ` +
        "offset like 2026-09-30T09:00:00-05:00, or Unix seconds."
    );
  }
  return Math.floor(ms / 1000);
}

function iso(ts: number): string {
  return new Date(ts * 1000).toISOString();
}

function errorText(prefix: string, error: unknown): string {
  const code = (error as SlackAPIError).error || (error as Error).message;
  const hint = ERROR_HINTS[code];
  return `${prefix}: ${code}` + (hint ? ` (${hint})` : "");
}

function isScheduled(draft: SlackDraft): boolean {
  return Boolean(draft.date_scheduled) && !draft.is_sent && !draft.is_deleted;
}

function draftText(draft: SlackDraft): string {
  return richTextToPlain((draft.blocks as RichElement[]) ?? []);
}

interface ScheduleOptions {
  target: string;
  message: string;
  sendAt: string | number;
  threadTs?: string;
}

/**
 * Schedule a message to be sent later.
 */
export async function scheduleMessage(
  client: SlackClient,
  options: ScheduleOptions
): Promise<Record<string, unknown>> {
  const { target, message } = options;
  let { threadTs } = options;

  if (!message.trim()) return { ok: false, error: "message is empty" };

  let postAt: number;
  try {
    postAt = parseSendAt(options.sendAt);
  } catch (error) {
    return { ok: false, error: (error as Error).message };
  }

  const now = Date.now() / 1000;
  if (postAt < now + MIN_LEAD_SECONDS) {
    return {
      ok: false,
      error: `send_at ${iso(postAt)} is not in the future (now is ${iso(Math.floor(now))})`,
    };
  }
  if (postAt > now + MAX_SCHEDULE_DAYS * 86400) {
    return {
      ok: false,
      error: `send_at is more than ${MAX_SCHEDULE_DAYS} days out (Slack's limit)`,
    };
  }

  let channelId: string;
  try {
    const ref = resolveChannelRef(target);
    channelId = ref.channelId;
    if (ref.messageTs && !threadTs) threadTs = ref.messageTs;
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }

  if (channelId.startsWith("U") || channelId.startsWith("W")) {
    try {
      channelId = await client.openConversation([channelId]);
    } catch (error) {
      const apiError = error as SlackAPIError;
      return {
        ok: false,
        error: `Failed to open DM with user ${channelId}: ${apiError.error || apiError.message}`,
      };
    }
  }

  const blocks = mrkdwnToRichText(message);

  let draft: SlackDraft;
  try {
    draft = await client.createDraft(channelId, blocks, { threadTs, dateScheduled: postAt });
  } catch (error) {
    return {
      ok: false,
      error: errorText("Failed to schedule message", error),
      channel_id: channelId,
    };
  }

  const result: Record<string, unknown> = {
    ok: true,
    scheduled_id: draft.id,
    channel_id: channelId,
    send_at: iso(postAt),
    send_at_unix: postAt,
    text: richTextToPlain(blocks),
    sent_to: await conversationLabel(client, channelId),
  };
  if (threadTs) result.thread_ts = threadTs;
  return result;
}

/**
 * List messages scheduled to send later, soonest first.
 * Includes messages scheduled in the Slack app, not just by this tool.
 */
export async function listScheduled(
  client: SlackClient
): Promise<Record<string, unknown>> {
  let drafts: SlackDraft[];
  try {
    drafts = await client.listDrafts();
  } catch (error) {
    return { ok: false, error: errorText("Failed to list scheduled messages", error) };
  }

  const scheduled = drafts
    .filter(isScheduled)
    .sort((a, b) => (a.date_scheduled ?? 0) - (b.date_scheduled ?? 0));

  const labels = new Map<string, string>();
  const items: Record<string, unknown>[] = [];
  for (const draft of scheduled) {
    const destination = draft.destinations?.[0] ?? {};
    const channelId = destination.channel_id ?? "";
    if (channelId && !labels.has(channelId)) {
      labels.set(channelId, await conversationLabel(client, channelId));
    }
    const item: Record<string, unknown> = {
      scheduled_id: draft.id,
      send_at: iso(draft.date_scheduled ?? 0),
      send_at_unix: draft.date_scheduled,
      channel_id: channelId,
      sent_to: labels.get(channelId) ?? channelId,
      text: draftText(draft),
    };
    if (destination.thread_ts) item.thread_ts = destination.thread_ts;
    items.push(item);
  }

  return { ok: true, count: items.length, scheduled: items };
}

/**
 * Cancel a scheduled message so it is never sent.
 * Only scheduled drafts can be cancelled; ordinary drafts are left alone.
 */
export async function cancelScheduled(
  client: SlackClient,
  scheduledId: string
): Promise<Record<string, unknown>> {
  let drafts: SlackDraft[];
  try {
    drafts = await client.listDrafts();
  } catch (error) {
    return { ok: false, error: errorText("Failed to look up scheduled message", error) };
  }

  const draft = drafts.find((d) => d.id === scheduledId);
  if (!draft || !isScheduled(draft)) {
    return {
      ok: false,
      error:
        `No pending scheduled message with id ${scheduledId} ` +
        "(it may have already been sent or cancelled). " +
        "Use slack_list_scheduled to see current ids.",
    };
  }

  try {
    await client.deleteDraft(scheduledId, draft.last_updated_ts ?? "");
  } catch (error) {
    return { ok: false, error: errorText("Failed to cancel scheduled message", error) };
  }

  const channelId = draft.destinations?.[0]?.channel_id ?? "";
  return {
    ok: true,
    cancelled: scheduledId,
    was_scheduled_for: iso(draft.date_scheduled ?? 0),
    channel_id: channelId,
    sent_to: channelId ? await conversationLabel(client, channelId) : "",
    text: draftText(draft),
  };
}
