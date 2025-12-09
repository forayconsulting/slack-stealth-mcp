/**
 * Authentication Routes
 *
 * Handles auth API endpoints and routes to BrowserAuthSession Durable Object
 */

import type { Env } from "../types/env";
import { deriveKey, decryptWorkspace, type StoredWorkspace, type UserProfile } from "../crypto";

/**
 * Handle auth-related requests
 */
export async function handleAuthRequest(
  request: Request,
  env: Env
): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname.replace("/auth", "");

  // CORS headers for browser clients
  const corsHeaders = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
  };

  // Handle preflight
  if (request.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders });
  }

  try {
    // Start new auth session
    if (path === "/start" && request.method === "POST") {
      return handleStartAuth(request, env, corsHeaders);
    }

    // WebSocket connection to active session
    if (path.startsWith("/session/")) {
      const sessionId = path.replace("/session/", "");
      return handleSessionWebSocket(request, env, sessionId);
    }

    // Check auth status
    if (path === "/status" && request.method === "GET") {
      return handleAuthStatus(request, env, corsHeaders);
    }

    // List workspaces
    if (path === "/workspaces" && request.method === "GET") {
      return handleListWorkspaces(request, env, corsHeaders);
    }

    // Delete workspace
    if (path.startsWith("/workspace/") && request.method === "DELETE") {
      const workspaceName = path.replace("/workspace/", "");
      return handleDeleteWorkspace(request, env, workspaceName, corsHeaders);
    }

    return new Response("Not found", { status: 404, headers: corsHeaders });
  } catch (error) {
    return new Response(
      JSON.stringify({ error: String(error) }),
      { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }
}

/**
 * Start a new browser auth session
 */
async function handleStartAuth(
  request: Request,
  env: Env,
  corsHeaders: Record<string, string>
): Promise<Response> {
  const body = await request.json() as { userId: string };

  if (!body.userId) {
    return new Response(
      JSON.stringify({ error: "userId is required" }),
      { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }

  // Create a new Durable Object instance for this auth session
  const id = env.BROWSER_SESSION.newUniqueId();
  const stub = env.BROWSER_SESSION.get(id);

  // Forward the request to the Durable Object
  const doRequest = new Request(new URL("/start", request.url), {
    method: "POST",
    body: JSON.stringify({ userId: body.userId }),
    headers: { "Content-Type": "application/json" },
  });

  const response = await stub.fetch(doRequest);
  const result = await response.json() as { success: boolean; sessionId?: string; message?: string };

  if (result.success) {
    return new Response(
      JSON.stringify({
        success: true,
        sessionId: id.toString(),
        wsUrl: `/auth/session/${id.toString()}`,
        message: result.message,
      }),
      { headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }

  return new Response(
    JSON.stringify(result),
    { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
  );
}

/**
 * Handle WebSocket connection to auth session
 */
async function handleSessionWebSocket(
  request: Request,
  env: Env,
  sessionId: string
): Promise<Response> {
  // Reconstruct the Durable Object ID
  const id = env.BROWSER_SESSION.idFromString(sessionId);
  const stub = env.BROWSER_SESSION.get(id);

  // Forward WebSocket upgrade to Durable Object
  return stub.fetch(request);
}

/**
 * Check auth status for a user
 */
async function handleAuthStatus(
  request: Request,
  env: Env,
  corsHeaders: Record<string, string>
): Promise<Response> {
  const url = new URL(request.url);
  const userId = url.searchParams.get("userId");

  if (!userId) {
    return new Response(
      JSON.stringify({ error: "userId query parameter required" }),
      { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }

  // Get user profile
  const profileKey = `user:${userId}:profile`;
  const profile = await env.TOKENS.get(profileKey, "json") as UserProfile | null;

  if (!profile) {
    return new Response(
      JSON.stringify({
        authenticated: false,
        workspaces: [],
      }),
      { headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }

  // Check each workspace
  const workspaceStatuses = await Promise.all(
    profile.workspaces.map(async (name) => {
      const wsKey = `user:${userId}:workspace:${name}`;
      const ws = await env.TOKENS.get(wsKey, "json") as StoredWorkspace | null;
      return {
        name,
        configured: !!ws,
        lastVerified: ws?.last_verified,
      };
    })
  );

  return new Response(
    JSON.stringify({
      authenticated: workspaceStatuses.some((ws) => ws.configured),
      workspaces: workspaceStatuses,
      defaultWorkspace: profile.default_workspace,
    }),
    { headers: { ...corsHeaders, "Content-Type": "application/json" } }
  );
}

/**
 * List all workspaces for a user
 */
async function handleListWorkspaces(
  request: Request,
  env: Env,
  corsHeaders: Record<string, string>
): Promise<Response> {
  const url = new URL(request.url);
  const userId = url.searchParams.get("userId");

  if (!userId) {
    return new Response(
      JSON.stringify({ error: "userId query parameter required" }),
      { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }

  const profileKey = `user:${userId}:profile`;
  const profile = await env.TOKENS.get(profileKey, "json") as UserProfile | null;

  if (!profile) {
    return new Response(
      JSON.stringify({ workspaces: [] }),
      { headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }

  // Get workspace details (without decrypting tokens)
  const workspaces = await Promise.all(
    profile.workspaces.map(async (name) => {
      const wsKey = `user:${userId}:workspace:${name}`;
      const ws = await env.TOKENS.get(wsKey, "json") as StoredWorkspace | null;
      if (!ws) return null;
      return {
        name,
        team_id: ws.team_id,
        team_name: ws.team_name,
        created_at: ws.created_at,
        last_verified: ws.last_verified,
      };
    })
  );

  return new Response(
    JSON.stringify({
      workspaces: workspaces.filter(Boolean),
      defaultWorkspace: profile.default_workspace,
    }),
    { headers: { ...corsHeaders, "Content-Type": "application/json" } }
  );
}

/**
 * Delete a workspace for a user
 */
async function handleDeleteWorkspace(
  request: Request,
  env: Env,
  workspaceName: string,
  corsHeaders: Record<string, string>
): Promise<Response> {
  const url = new URL(request.url);
  const userId = url.searchParams.get("userId");

  if (!userId) {
    return new Response(
      JSON.stringify({ error: "userId query parameter required" }),
      { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }

  // Delete workspace tokens
  const wsKey = `user:${userId}:workspace:${workspaceName}`;
  await env.TOKENS.delete(wsKey);

  // Update profile
  const profileKey = `user:${userId}:profile`;
  const profile = await env.TOKENS.get(profileKey, "json") as UserProfile | null;

  if (profile) {
    profile.workspaces = profile.workspaces.filter((w) => w !== workspaceName);
    if (profile.default_workspace === workspaceName) {
      profile.default_workspace = profile.workspaces[0] || "";
    }
    await env.TOKENS.put(profileKey, JSON.stringify(profile));
  }

  return new Response(
    JSON.stringify({ success: true, deleted: workspaceName }),
    { headers: { ...corsHeaders, "Content-Type": "application/json" } }
  );
}

/**
 * Get decrypted tokens for a workspace (internal use)
 */
export async function getWorkspaceTokens(
  env: Env,
  userId: string,
  workspaceName: string
): Promise<{ xoxc_token: string; xoxd_cookie: string; team_id: string; team_name: string } | null> {
  const wsKey = `user:${userId}:workspace:${workspaceName}`;
  const stored = await env.TOKENS.get(wsKey, "json") as StoredWorkspace | null;

  if (!stored) return null;

  const key = await deriveKey(env.ENCRYPTION_KEY);
  return decryptWorkspace(stored, key);
}

/**
 * List all workspace names for a user (internal use)
 */
export async function listUserWorkspaces(
  env: Env,
  userId: string
): Promise<string[]> {
  const profileKey = `user:${userId}:profile`;
  const profile = await env.TOKENS.get(profileKey, "json") as UserProfile | null;

  if (!profile) return [];

  return profile.workspaces;
}
