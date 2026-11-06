/**
 * Upload File Tool - Upload a file to a channel, DM, or thread
 *
 * Port of packages/python/src/slack_stealth_mcp/tools/upload_file.py.
 * The Worker has no filesystem, so there is no file_path option: content
 * arrives as text or base64.
 */

import { SlackClient, SlackAPIError } from "../slack-client";
import { normalizeMrkdwn } from "../mrkdwn";
import { conversationLabel, resolveSendTarget } from "./reply";

// Lower than Python's 100 MB: a Worker isolate has 128 MB of memory, and
// base64 input plus decoded bytes are both held at once.
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

interface UploadFileOptions {
  target: string;
  content?: string;
  contentBase64?: string;
  filename: string;
  title?: string;
  message?: string;
  threadTs?: string;
}

function decodeBase64(value: string): Uint8Array {
  const clean = value.replace(/\s+/g, "");
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(clean) || clean.length % 4 !== 0) {
    throw new Error("content_base64 is not valid base64");
  }
  const binary = atob(clean);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/**
 * Upload a file to a conversation. Does NOT mark anything read.
 */
export async function uploadFile(
  client: SlackClient,
  options: UploadFileOptions
): Promise<Record<string, unknown>> {
  const { target, content, contentBase64, filename, title, message } = options;

  if ((content === undefined) === (contentBase64 === undefined)) {
    return { ok: false, error: "Provide exactly one of content or content_base64" };
  }
  if (!filename) {
    return { ok: false, error: "filename is required" };
  }

  let data: Uint8Array;
  try {
    data =
      contentBase64 !== undefined
        ? decodeBase64(contentBase64)
        : new TextEncoder().encode(content);
  } catch (error) {
    return { ok: false, error: (error as Error).message };
  }
  if (data.byteLength === 0) {
    return { ok: false, error: "File is empty; Slack rejects empty uploads" };
  }
  if (data.byteLength > MAX_UPLOAD_BYTES) {
    return {
      ok: false,
      error: `Content is ${data.byteLength} bytes, over the ${MAX_UPLOAD_BYTES} byte limit`,
    };
  }

  let channelId: string;
  let threadTs: string | undefined;
  try {
    ({ channelId, threadTs } = await resolveSendTarget(client, target, options.threadTs));
  } catch (error) {
    return { ok: false, error: (error as Error).message };
  }

  let file: Record<string, unknown>;
  try {
    file = await client.uploadFile(data, filename, channelId, {
      title,
      initialComment: message ? normalizeMrkdwn(message) : undefined,
      threadTs,
    });
  } catch (error) {
    const apiError = error as SlackAPIError;
    return {
      ok: false,
      error: `Failed to upload file: ${apiError.error || apiError.message}`,
      channel_id: channelId,
    };
  }

  const result: Record<string, unknown> = {
    ok: true,
    file_id: file.id,
    filename,
    title: (file.title as string) || title || filename,
    size: data.byteLength,
    channel_id: channelId,
  };
  if (file.permalink) result.permalink = file.permalink;
  if (threadTs) result.thread_ts = threadTs;
  result.sent_to = await conversationLabel(client, channelId);
  return result;
}
