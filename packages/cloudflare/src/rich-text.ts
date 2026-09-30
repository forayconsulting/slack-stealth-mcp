/**
 * Convert Slack mrkdwn to Block Kit rich_text.
 *
 * Scheduled messages go through Slack's internal drafts API, which stores the
 * body as rich_text blocks rather than a mrkdwn string. Slack Desktop also
 * discards drafts without a non-empty rich_text block, so every message is
 * emitted as a single rich_text block.
 *
 * Handles the subset of mrkdwn the rest of this server encourages: *bold*,
 * _italic_, ~strike~, `code`, ```code blocks```, > quotes, - / • / 1. lists,
 * <url|text> links, <@U...> / <#C...> / <!here> mentions, and :emoji:.
 *
 * Port of packages/python/src/slack_stealth_mcp/rich_text.py - keep in sync.
 */

import { normalizeMrkdwn } from "./mrkdwn";

type Style = Partial<Record<"bold" | "italic" | "strike" | "code", boolean>>;

export interface RichElement {
  type: string;
  [key: string]: unknown;
}

const FENCE_RE = /```\n?([\s\S]*?)\n?```/g;
const QUOTE_RE = /^(?:>|&gt;) ?(.*)$/;
const BULLET_RE = /^( *)(?:[-•]|\*(?= )) +(.*)$/;
const ORDERED_RE = /^( *)\d+[.)] +(.*)$/;

// Inline tokens, in priority order. Style markers must sit on a word
// boundary, matching how Slack itself parses mrkdwn.
const INLINE_RE = new RegExp(
  [
    "(?<angle><(?<angle_body>[^<>\\n]+)>)",
    "(?<code>`(?<code_body>[^`\\n]+)`)",
    "(?<bold>(?<![\\w*])\\*(?! )(?<bold_body>[^*\\n]+?)(?<! )\\*(?![\\w*]))",
    "(?<italic>(?<![\\w_])_(?! )(?<italic_body>[^_\\n]+?)(?<! )_(?![\\w_]))",
    "(?<strike>(?<![\\w~])~(?! )(?<strike_body>[^~\\n]+?)(?<! )~(?![\\w~]))",
    "(?<emoji>(?<![\\w:]):(?<emoji_name>[a-z0-9_+'-]+):(?::skin-tone-(?<skin>[2-6]):)?(?![\\w:]))",
  ].join("|"),
  "g"
);

const BROADCASTS = new Set(["here", "channel", "everyone"]);

function unescapeHtml(value: string): string {
  return value
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&");
}

function textElement(value: string, style: Style): RichElement {
  const element: RichElement = { type: "text", text: unescapeHtml(value) };
  if (Object.keys(style).length) element.style = { ...style };
  return element;
}

/** Parse the inside of a <...> token, or null if it isn't an entity. */
function angleElement(body: string, style: Style): RichElement | null {
  const pipe = body.indexOf("|");
  const target = pipe === -1 ? body : body.slice(0, pipe);
  const label = pipe === -1 ? "" : body.slice(pipe + 1);

  if (target.startsWith("@") && target.length > 1) {
    return { type: "user", user_id: target.slice(1) };
  }
  if (target.startsWith("#") && target.length > 1) {
    return { type: "channel", channel_id: target.slice(1) };
  }
  if (target.startsWith("!subteam^")) {
    return { type: "usergroup", usergroup_id: target.slice("!subteam^".length) };
  }
  if (target.startsWith("!") && BROADCASTS.has(target.slice(1))) {
    return { type: "broadcast", range: target.slice(1) };
  }
  if (/^(https?|mailto):/.test(target)) {
    const element: RichElement = { type: "link", url: unescapeHtml(target) };
    if (label) element.text = unescapeHtml(label);
    if (Object.keys(style).length) element.style = { ...style };
    return element;
  }
  return null;
}

/** Join adjacent text elements that share a style. */
function mergeText(elements: RichElement[]): RichElement[] {
  const merged: RichElement[] = [];
  for (const element of elements) {
    const prev = merged[merged.length - 1];
    if (
      prev &&
      element.type === "text" &&
      prev.type === "text" &&
      JSON.stringify(prev.style) === JSON.stringify(element.style)
    ) {
      prev.text = (prev.text as string) + (element.text as string);
    } else {
      merged.push(element);
    }
  }
  return merged;
}

/** Parse one run of inline mrkdwn into rich_text elements. */
function parseInline(text: string, style: Style = {}): RichElement[] {
  const elements: RichElement[] = [];
  let pos = 0;

  for (const match of text.matchAll(INLINE_RE)) {
    const groups = match.groups ?? {};
    const start = match.index ?? 0;
    let entity: RichElement | null = null;

    if (groups.angle !== undefined) {
      entity = angleElement(groups.angle_body, style);
      if (!entity) continue; // leave unknown <...> as literal text
    }
    if (start > pos) elements.push(textElement(text.slice(pos, start), style));

    if (entity) {
      elements.push(entity);
    } else if (groups.code !== undefined) {
      elements.push(textElement(groups.code_body, { ...style, code: true }));
    } else if (groups.bold !== undefined) {
      elements.push(...parseInline(groups.bold_body, { ...style, bold: true }));
    } else if (groups.italic !== undefined) {
      elements.push(...parseInline(groups.italic_body, { ...style, italic: true }));
    } else if (groups.strike !== undefined) {
      elements.push(...parseInline(groups.strike_body, { ...style, strike: true }));
    } else if (groups.emoji !== undefined) {
      const emoji: RichElement = { type: "emoji", name: groups.emoji_name };
      if (groups.skin) emoji.skin_tone = parseInt(groups.skin, 10);
      elements.push(emoji);
    }
    pos = start + match[0].length;
  }

  if (pos < text.length) elements.push(textElement(text.slice(pos), style));
  return mergeText(elements);
}

/** Group non-code lines into sections, quotes, and lists. */
function parseLines(lines: string[]): RichElement[] {
  const blocks: RichElement[] = [];
  const section: string[] = [];
  const quote: string[] = [];
  const listItems: string[] = [];
  let listStyle = "";
  let listIndent = 0;

  const flushSection = () => {
    if (section.length) {
      blocks.push({ type: "rich_text_section", elements: parseInline(section.join("\n")) });
      section.length = 0;
    }
  };
  const flushQuote = () => {
    if (quote.length) {
      blocks.push({ type: "rich_text_quote", elements: parseInline(quote.join("\n")) });
      quote.length = 0;
    }
  };
  const flushList = () => {
    if (listItems.length) {
      blocks.push({
        type: "rich_text_list",
        style: listStyle,
        indent: listIndent,
        elements: listItems.map((item) => ({
          type: "rich_text_section",
          elements: parseInline(item),
        })),
      });
      listItems.length = 0;
    }
  };

  for (const line of lines) {
    const quoteMatch = QUOTE_RE.exec(line);
    const bulletMatch = BULLET_RE.exec(line);
    const listMatch = bulletMatch || ORDERED_RE.exec(line);

    if (quoteMatch) {
      flushSection();
      flushList();
      quote.push(quoteMatch[1]);
    } else if (listMatch) {
      flushSection();
      flushQuote();
      const style = bulletMatch ? "bullet" : "ordered";
      const indent = Math.min(Math.floor(listMatch[1].length / 2), 8);
      if (listItems.length && (style !== listStyle || indent !== listIndent)) {
        flushList();
      }
      listStyle = style;
      listIndent = indent;
      listItems.push(listMatch[2]);
    } else {
      flushQuote();
      flushList();
      section.push(line);
    }
  }

  flushSection();
  flushQuote();
  flushList();
  return blocks;
}

function trimBlankLines(lines: string[]): string[] {
  while (lines.length && !lines[0].trim()) lines.shift();
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
  return lines;
}

/**
 * Convert a mrkdwn message to a list holding one rich_text block.
 *
 * Stray standard Markdown is normalized first, so **bold** and [text](url)
 * come out the same as they would through slack_reply.
 */
export function mrkdwnToRichText(input: string): RichElement[] {
  const text = normalizeMrkdwn(input);
  let elements: RichElement[] = [];
  let pos = 0;

  for (const match of text.matchAll(FENCE_RE)) {
    const start = match.index ?? 0;
    elements.push(...parseLines(trimBlankLines(text.slice(pos, start).split("\n"))));
    if (match[1]) {
      elements.push({
        type: "rich_text_preformatted",
        elements: [{ type: "text", text: unescapeHtml(match[1]) }],
      });
    }
    pos = start + match[0].length;
  }

  elements.push(...parseLines(trimBlankLines(text.slice(pos).split("\n"))));
  elements = elements.filter((e) => Array.isArray(e.elements) && e.elements.length);
  return [{ type: "rich_text", elements }];
}

function inlineToPlain(elements: RichElement[]): string {
  return elements
    .map((e) => {
      switch (e.type) {
        case "text":
          return (e.text as string) ?? "";
        case "link":
          return (e.text as string) || (e.url as string) || "";
        case "user":
          return `<@${e.user_id}>`;
        case "channel":
          return `<#${e.channel_id}>`;
        case "broadcast":
          return `<!${e.range}>`;
        case "usergroup":
          return `<!subteam^${e.usergroup_id}>`;
        case "emoji":
          return `:${e.name}:`;
        default:
          return "";
      }
    })
    .join("");
}

/** Flatten rich_text blocks to readable text (for previews). */
export function richTextToPlain(blocks: RichElement[]): string {
  const lines: string[] = [];
  for (const block of blocks) {
    for (const element of (block.elements as RichElement[]) ?? []) {
      const children = (element.elements as RichElement[]) ?? [];
      if (element.type === "rich_text_list") {
        const pad = "  ".repeat((element.indent as number) ?? 0);
        for (const item of children) {
          lines.push(`${pad}- ${inlineToPlain((item.elements as RichElement[]) ?? [])}`);
        }
      } else if (element.type === "rich_text_quote") {
        for (const line of inlineToPlain(children).split("\n")) lines.push(`> ${line}`);
      } else if (element.type === "rich_text_preformatted") {
        lines.push("```" + inlineToPlain(children) + "```");
      } else {
        lines.push(inlineToPlain(children));
      }
    }
  }
  return lines.join("\n").trim();
}
