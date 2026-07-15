/**
 * Normalize standard Markdown to Slack mrkdwn.
 *
 * LLMs habitually emit GitHub-style Markdown (**bold**, [text](url), ## headers)
 * which Slack renders literally. This module conservatively converts the common
 * mistakes to mrkdwn while leaving code regions and already-correct mrkdwn
 * (<@U...>, <!here>, <url|text>) untouched. The conversion is idempotent.
 */

// Fenced code blocks (```...```) and inline code (`...`) must not be rewritten
const CODE_RE = /```[\s\S]*?```|`[^`\n]*`/g;

const BOLD_RE = /\*\*([\s\S]+?)\*\*/g;
const BOLD_UNDERSCORE_RE = /__([\s\S]+?)__/g;
// Only convert real links; <url|text> output won't re-match
const LINK_RE = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g;
const HEADER_RE = /^#{1,6}\s+(.+?)\s*$/gm;

function convertSegment(text: string): string {
  return text
    .replace(BOLD_RE, "*$1*")
    .replace(BOLD_UNDERSCORE_RE, "*$1*")
    .replace(LINK_RE, "<$2|$1>")
    .replace(HEADER_RE, "*$1*");
}

/**
 * Convert common Markdown patterns to Slack mrkdwn.
 * Code blocks and inline code spans are preserved verbatim.
 */
export function normalizeMrkdwn(text: string): string {
  const result: string[] = [];
  let lastEnd = 0;

  for (const match of text.matchAll(CODE_RE)) {
    const start = match.index ?? 0;
    result.push(convertSegment(text.slice(lastEnd, start)));
    result.push(match[0]);
    lastEnd = start + match[0].length;
  }

  result.push(convertSegment(text.slice(lastEnd)));
  return result.join("");
}
