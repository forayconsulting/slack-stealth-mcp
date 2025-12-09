/**
 * Cloudflare Worker Environment Types
 */

import type { OAuthHelpers } from "@cloudflare/workers-oauth-provider";

export interface Env {
  // Browser Rendering
  BROWSER: Fetcher;

  // KV Namespaces
  TOKENS: KVNamespace;
  SESSIONS: KVNamespace;
  OAUTH_KV: KVNamespace;

  // OAuth Provider (injected by OAuthProvider wrapper)
  OAUTH_PROVIDER: OAuthHelpers;

  // Durable Objects
  BROWSER_SESSION: DurableObjectNamespace;
  MCP_SESSION: DurableObjectNamespace;

  // Secrets
  ENCRYPTION_KEY: string;

  // Environment variables
  ENVIRONMENT: string;
}
