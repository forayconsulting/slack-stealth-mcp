/**
 * Slack Stealth MCP - Cloudflare Workers
 *
 * Hosted MCP server for stealth Slack interaction.
 * Features:
 * - OAuth 2.1 authentication via workers-oauth-provider
 * - Interactive browser auth (CDP screencast + user input)
 * - MCP server via Durable Objects with McpAgent
 */

import { OAuthProvider } from "@cloudflare/workers-oauth-provider";
import type { Env } from "./types/env";
import { SlackMcpServer } from "./mcp/server";
import { handleOAuthFlow } from "./auth/oauth-handler";

// Re-export Durable Objects for Wrangler
export { BrowserAuthSession } from "./auth/browser-session";
export { SlackMcpServer } from "./mcp/server";

// Re-export SlackClient for use in tools
export { SlackClient } from "./slack-client";
export type * from "./types/slack";

/**
 * Default handler for non-MCP requests
 * Handles OAuth flow, health check, browser auth sessions
 * Note: Cast to ExportedHandler because OAuthProvider uses generic types
 */
const defaultHandler = {
  async fetch(
    request: Request,
    env: Env,
    ctx: ExecutionContext
  ): Promise<Response> {
    return handleOAuthFlow(request, env, ctx);
  },
} as ExportedHandler;

/**
 * Main export - OAuthProvider wraps everything
 *
 * Protected routes (/mcp) require a valid OAuth token
 * Other routes are handled by defaultHandler
 */
export default new OAuthProvider({
  // MCP endpoint - requires OAuth authentication
  apiRoute: "/mcp",

  // MCP server handler (McpAgent router)
  apiHandler: SlackMcpServer.serve("/mcp"),

  // Handler for non-API routes (OAuth flow, health, etc.)
  defaultHandler,

  // OAuth endpoint configuration
  authorizeEndpoint: "/authorize",
  tokenEndpoint: "/token",
  clientRegistrationEndpoint: "/register",

  // Supported scopes
  scopesSupported: ["slack:read", "slack:write"],
});
