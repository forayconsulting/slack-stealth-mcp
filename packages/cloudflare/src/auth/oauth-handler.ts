/**
 * OAuth Handler for MCP Server
 *
 * Integrates our browser-based Slack authentication with OAuth 2.1 flow.
 * This allows Claude to authenticate users via the standard OAuth flow.
 */

import type { Env } from "../types/env";
import type { AuthRequest } from "@cloudflare/workers-oauth-provider";

/**
 * Slack tokens extracted from browser auth
 */
export interface SlackTokens {
  xoxc_token: string;
  xoxd_cookie: string;
  team_id: string;
  team_name: string;
}

/**
 * Props passed to MCP server via OAuth token
 */
export interface OAuthProps {
  slackTokens: SlackTokens;
  authenticatedAt: string;
}

/**
 * OAuth session stored during auth flow
 */
interface OAuthSession {
  authRequest: AuthRequest;
  createdAt: string;
}

/**
 * Browser auth result stored temporarily
 */
interface BrowserAuthResult {
  success: boolean;
  tokens?: SlackTokens;
  error?: string;
}

/**
 * Handle non-API requests (OAuth flow, health check, etc.)
 */
export async function handleOAuthFlow(
  request: Request,
  env: Env,
  ctx: ExecutionContext
): Promise<Response> {
  const url = new URL(request.url);

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

  // Health check
  if (url.pathname === "/" || url.pathname === "/health") {
    return healthResponse(env);
  }

  // OAuth authorize endpoint - show Slack login UI
  if (url.pathname === "/authorize") {
    return handleAuthorize(request, env);
  }

  // OAuth completion - after Slack login
  if (url.pathname === "/authorize/complete") {
    return handleAuthorizeComplete(request, env);
  }

  // Browser auth session start
  if (url.pathname === "/auth/start" && request.method === "POST") {
    return handleAuthStart(request, env, corsHeaders);
  }

  // Browser auth session WebSocket or status/reconnect
  if (url.pathname.startsWith("/auth/session/")) {
    const pathParts = url.pathname.replace("/auth/session/", "").split("/");
    const sessionId = pathParts[0];

    // Handle status check
    if (pathParts[1] === "status") {
      return handleSessionStatus(request, env, sessionId);
    }

    // Handle reconnect
    if (pathParts[1] === "reconnect" && request.method === "POST") {
      return handleSessionReconnect(request, env, sessionId);
    }

    // Default: WebSocket upgrade
    return handleSessionWebSocket(request, env, sessionId);
  }

  // Get browser auth result (called by authorize/complete)
  if (url.pathname.startsWith("/auth/result/") && request.method === "GET") {
    const sessionId = url.pathname.replace("/auth/result/", "");
    return handleAuthResult(request, env, sessionId, corsHeaders);
  }

  return new Response("Not found", { status: 404, headers: corsHeaders });
}

/**
 * Health check response
 */
function healthResponse(env: Env): Response {
  return new Response(
    JSON.stringify(
      {
        service: "slack-stealth-mcp",
        status: "ok",
        environment: env.ENVIRONMENT,
        oauth: true,
        endpoints: {
          "/mcp": "MCP server (requires OAuth)",
          "/authorize": "OAuth authorization endpoint",
          "/token": "OAuth token endpoint (handled by OAuthProvider)",
          "/register": "OAuth client registration (handled by OAuthProvider)",
        },
      },
      null,
      2
    ),
    { headers: { "Content-Type": "application/json" } }
  );
}

/**
 * Handle OAuth /authorize request
 * Parse the OAuth request and show browser auth UI
 */
async function handleAuthorize(request: Request, env: Env): Promise<Response> {
  try {
    // Parse the OAuth authorization request
    const authRequest = await env.OAUTH_PROVIDER.parseAuthRequest(request);

    if (!authRequest.clientId) {
      return new Response("Invalid OAuth request: missing client_id", { status: 400 });
    }

    // Generate a session ID to track this OAuth flow
    const oauthSessionId = crypto.randomUUID();

    // Store the OAuth request for later completion
    const session: OAuthSession = {
      authRequest,
      createdAt: new Date().toISOString(),
    };
    await env.SESSIONS.put(`oauth:${oauthSessionId}`, JSON.stringify(session), {
      expirationTtl: 600, // 10 minutes
    });

    // Return HTML with embedded browser auth UI
    return new Response(renderAuthorizePage(oauthSessionId, authRequest), {
      headers: { "Content-Type": "text/html" },
    });
  } catch (error) {
    console.error("Error handling authorize:", error);
    return new Response(`OAuth error: ${error}`, { status: 400 });
  }
}

/**
 * Handle OAuth completion after browser auth
 */
async function handleAuthorizeComplete(
  request: Request,
  env: Env
): Promise<Response> {
  const url = new URL(request.url);
  const oauthSessionId = url.searchParams.get("oauth");
  const browserSessionId = url.searchParams.get("browser");

  if (!oauthSessionId || !browserSessionId) {
    return new Response("Missing oauth or browser session ID", { status: 400 });
  }

  try {
    // Get the OAuth session
    const sessionJson = await env.SESSIONS.get(`oauth:${oauthSessionId}`);
    if (!sessionJson) {
      return new Response("OAuth session expired or not found", { status: 400 });
    }
    const session: OAuthSession = JSON.parse(sessionJson);

    // Get the browser auth result
    const resultJson = await env.SESSIONS.get(`browser-result:${browserSessionId}`);
    if (!resultJson) {
      return new Response("Browser auth session not found", { status: 400 });
    }
    const result: BrowserAuthResult = JSON.parse(resultJson);

    if (!result.success || !result.tokens) {
      return new Response(`Authentication failed: ${result.error || "Unknown error"}`, {
        status: 400,
      });
    }

    // Get client info for display (optional)
    let clientName = "Claude";
    try {
      const clientInfo = await env.OAUTH_PROVIDER.lookupClient(session.authRequest.clientId);
      clientName = clientInfo?.clientName || "Claude";
    } catch {
      // Ignore errors looking up client
    }

    // Complete the OAuth authorization
    const { redirectTo } = await env.OAUTH_PROVIDER.completeAuthorization({
      request: session.authRequest,
      userId: `slack:${result.tokens.team_id}`, // Use team_id as user identifier
      metadata: {
        teamName: result.tokens.team_name,
        teamId: result.tokens.team_id,
        authenticatedAt: new Date().toISOString(),
      },
      scope: session.authRequest.scope,
      props: {
        slackTokens: result.tokens,
        authenticatedAt: new Date().toISOString(),
      } satisfies OAuthProps,
    });

    // Clean up sessions
    await Promise.all([
      env.SESSIONS.delete(`oauth:${oauthSessionId}`),
      env.SESSIONS.delete(`browser-result:${browserSessionId}`),
    ]);

    // Redirect back to Claude with the authorization code
    return Response.redirect(redirectTo, 302);
  } catch (error) {
    console.error("Error completing authorization:", error);
    return new Response(`Error completing authorization: ${error}`, { status: 500 });
  }
}

/**
 * Start browser auth session
 */
async function handleAuthStart(
  request: Request,
  env: Env,
  corsHeaders: Record<string, string>
): Promise<Response> {
  try {
    const body = (await request.json()) as { oauthSessionId?: string };

    // Create a new Durable Object instance for this auth session
    const id = env.BROWSER_SESSION.newUniqueId();
    const stub = env.BROWSER_SESSION.get(id);

    // Forward the request to the Durable Object
    const doRequest = new Request(new URL("/start", request.url), {
      method: "POST",
      body: JSON.stringify({ oauthSessionId: body.oauthSessionId }),
      headers: { "Content-Type": "application/json" },
    });

    const response = await stub.fetch(doRequest);
    const result = (await response.json()) as {
      success: boolean;
      sessionId?: string;
      message?: string;
    };

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

    return new Response(JSON.stringify(result), {
      status: 500,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    });
  } catch (error) {
    return new Response(
      JSON.stringify({ error: String(error) }),
      { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }
}

/**
 * Handle WebSocket connection to browser auth session
 */
async function handleSessionWebSocket(
  request: Request,
  env: Env,
  sessionId: string
): Promise<Response> {
  const id = env.BROWSER_SESSION.idFromString(sessionId);
  const stub = env.BROWSER_SESSION.get(id);
  return stub.fetch(request);
}

/**
 * Check browser session status (for reconnection)
 */
async function handleSessionStatus(
  request: Request,
  env: Env,
  sessionId: string
): Promise<Response> {
  try {
    const id = env.BROWSER_SESSION.idFromString(sessionId);
    const stub = env.BROWSER_SESSION.get(id);
    const response = await stub.fetch(new Request(new URL("/status", request.url)));
    return response;
  } catch (error) {
    return new Response(
      JSON.stringify({ active: false, canReconnect: false, error: String(error) }),
      { headers: { "Content-Type": "application/json" } }
    );
  }
}

/**
 * Attempt to reconnect to browser session
 */
async function handleSessionReconnect(
  request: Request,
  env: Env,
  sessionId: string
): Promise<Response> {
  try {
    const id = env.BROWSER_SESSION.idFromString(sessionId);
    const stub = env.BROWSER_SESSION.get(id);
    const response = await stub.fetch(
      new Request(new URL("/reconnect", request.url), { method: "POST" })
    );
    return response;
  } catch (error) {
    return new Response(
      JSON.stringify({ success: false, canReconnect: false, error: String(error) }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    );
  }
}

/**
 * Get browser auth result (tokens stored by BrowserAuthSession)
 */
async function handleAuthResult(
  request: Request,
  env: Env,
  sessionId: string,
  corsHeaders: Record<string, string>
): Promise<Response> {
  const resultJson = await env.SESSIONS.get(`browser-result:${sessionId}`);
  if (!resultJson) {
    return new Response(
      JSON.stringify({ error: "No result found" }),
      { status: 404, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }

  return new Response(resultJson, {
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

/**
 * Render the OAuth authorization page with embedded browser auth
 */
function renderAuthorizePage(oauthSessionId: string, authRequest: AuthRequest): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Connect to Slack - Slack Stealth</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --slack-aubergine: #4a154b;
      --slack-aubergine-dark: #3c1039;
      --slack-aubergine-light: #611f69;
      --slack-green: #2eb67d;
      --slack-red: #e01e5a;
      --slack-blue: #36c5f0;
      --slack-yellow: #ecb22e;
      --text-primary: #fff;
      --text-secondary: #cfc3cf;
      --surface: #1a141a;
      --surface-raised: #2c1e2c;
      --border: #5c3d5e;
    }

    html, body {
      height: 100%;
      overflow: hidden;
    }

    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
      background: var(--surface);
      color: var(--text-primary);
      display: flex;
      flex-direction: column;
    }

    .header {
      background: var(--slack-aubergine);
      padding: 16px 20px;
      display: flex;
      align-items: center;
      gap: 12px;
      flex-shrink: 0;
    }

    .logo {
      width: 32px;
      height: 32px;
      display: flex;
      flex-wrap: wrap;
      gap: 2px;
    }

    .logo span {
      width: 14px;
      height: 14px;
      border-radius: 4px;
    }

    .logo .blue { background: var(--slack-blue); }
    .logo .green { background: var(--slack-green); }
    .logo .yellow { background: var(--slack-yellow); }
    .logo .red { background: var(--slack-red); }

    .header h1 {
      font-size: 18px;
      font-weight: 700;
    }

    .status-bar {
      background: var(--surface-raised);
      padding: 12px 20px;
      font-size: 14px;
      color: var(--text-secondary);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      gap: 8px;
      flex-shrink: 0;
    }

    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--text-secondary);
    }

    .status-bar.ready .status-dot { background: var(--slack-green); }
    .status-bar.error .status-dot { background: var(--slack-red); }
    .status-bar.complete .status-dot { background: var(--slack-green); }

    .main {
      flex: 1;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }

    .start-screen {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 20px;
      text-align: center;
    }

    .start-screen.hidden { display: none; }

    .start-icon {
      width: 80px;
      height: 80px;
      background: var(--slack-aubergine-light);
      border-radius: 20px;
      display: flex;
      align-items: center;
      justify-content: center;
      margin-bottom: 24px;
    }

    .start-icon svg {
      width: 40px;
      height: 40px;
      fill: var(--text-primary);
    }

    .start-screen h2 {
      font-size: 24px;
      margin-bottom: 8px;
    }

    .start-screen p {
      color: var(--text-secondary);
      margin-bottom: 32px;
      max-width: 300px;
      line-height: 1.5;
    }

    .client-info {
      background: var(--surface-raised);
      border-radius: 8px;
      padding: 12px 20px;
      margin-bottom: 24px;
      font-size: 14px;
    }

    .client-info strong {
      color: var(--slack-blue);
    }

    .start-btn {
      background: var(--slack-green);
      color: #fff;
      border: none;
      padding: 16px 48px;
      font-size: 18px;
      font-weight: 600;
      border-radius: 8px;
      cursor: pointer;
      transition: transform 0.1s, opacity 0.2s;
    }

    .start-btn:hover { opacity: 0.9; }
    .start-btn:active { transform: scale(0.98); }
    .start-btn:disabled {
      background: var(--border);
      cursor: not-allowed;
      transform: none;
    }

    .browser-container {
      flex: 1;
      display: none;
      background: #000;
      position: relative;
      overflow: hidden;
    }

    .browser-container.active { display: flex; }

    #browser-view {
      width: 100%;
      height: 100%;
      object-fit: contain;
      cursor: crosshair;
      touch-action: none;
      image-rendering: -webkit-optimize-contrast;
    }

    /* Floating keyboard button for mobile */
    .keyboard-btn {
      position: absolute;
      bottom: 20px;
      right: 20px;
      width: 56px;
      height: 56px;
      border-radius: 50%;
      background: var(--slack-blue);
      border: none;
      color: white;
      font-size: 24px;
      cursor: pointer;
      box-shadow: 0 4px 12px rgba(0,0,0,0.3);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 100;
    }

    .keyboard-btn.active {
      background: var(--slack-green);
    }

    /* Desktop fallback link */
    .desktop-fallback {
      position: absolute;
      top: 10px;
      right: 10px;
      background: rgba(0,0,0,0.7);
      color: white;
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 12px;
      text-decoration: none;
      z-index: 100;
    }

    .desktop-fallback:hover {
      background: rgba(0,0,0,0.9);
    }

    /* 2FA code banner - shown when Google 2FA is detected */
    .twofa-banner {
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      background: linear-gradient(135deg, #4285f4, #34a853);
      color: white;
      padding: 16px 20px;
      display: none;
      align-items: center;
      justify-content: center;
      gap: 12px;
      font-size: 18px;
      font-weight: 600;
      z-index: 200;
      animation: pulse-border 2s infinite;
      box-shadow: 0 4px 20px rgba(66, 133, 244, 0.4);
    }

    .twofa-banner.active {
      display: flex;
    }

    .twofa-code {
      background: white;
      color: #4285f4;
      padding: 8px 20px;
      border-radius: 8px;
      font-size: 32px;
      font-weight: 700;
      letter-spacing: 4px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    }

    @keyframes pulse-border {
      0%, 100% { box-shadow: 0 4px 20px rgba(66, 133, 244, 0.4); }
      50% { box-shadow: 0 4px 30px rgba(66, 133, 244, 0.8); }
    }

    /* QR Code modal for desktop handoff */
    .qr-modal {
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.9);
      display: none;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      z-index: 300;
      padding: 20px;
    }

    .qr-modal.active {
      display: flex;
    }

    .qr-modal h3 {
      color: white;
      margin-bottom: 16px;
      text-align: center;
    }

    .qr-modal p {
      color: var(--text-secondary);
      text-align: center;
      max-width: 300px;
      margin-bottom: 24px;
      font-size: 14px;
    }

    .qr-container {
      background: white;
      padding: 16px;
      border-radius: 12px;
      margin-bottom: 24px;
    }

    .qr-container canvas {
      display: block;
    }

    .close-qr {
      background: var(--surface-raised);
      color: white;
      border: 1px solid var(--border);
      padding: 12px 32px;
      border-radius: 8px;
      font-size: 16px;
      cursor: pointer;
    }

    /* Reconnect banner */
    .reconnect-banner {
      position: absolute;
      bottom: 80px;
      left: 20px;
      right: 20px;
      background: var(--slack-aubergine);
      border-radius: 12px;
      padding: 16px;
      display: none;
      flex-direction: column;
      gap: 12px;
      z-index: 150;
      box-shadow: 0 4px 20px rgba(0,0,0,0.3);
    }

    .reconnect-banner.active {
      display: flex;
    }

    .reconnect-banner p {
      margin: 0;
      font-size: 14px;
      color: var(--text-secondary);
    }

    .reconnect-btn {
      background: var(--slack-green);
      color: white;
      border: none;
      padding: 12px 24px;
      border-radius: 8px;
      font-size: 16px;
      font-weight: 600;
      cursor: pointer;
    }

    .success-overlay {
      position: absolute;
      inset: 0;
      background: rgba(26, 20, 26, 0.95);
      display: none;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 20px;
      text-align: center;
    }

    .success-overlay.active { display: flex; }

    .success-icon {
      width: 80px;
      height: 80px;
      background: var(--slack-green);
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      margin-bottom: 24px;
    }

    .success-icon svg {
      width: 40px;
      height: 40px;
      fill: #fff;
    }

    .success-overlay h2 {
      font-size: 24px;
      margin-bottom: 8px;
    }

    .success-overlay p {
      color: var(--text-secondary);
      margin-bottom: 8px;
    }

    .workspace-name {
      color: var(--slack-green);
      font-weight: 600;
      font-size: 18px;
    }

    .redirect-text {
      margin-top: 24px;
      color: var(--text-secondary);
      font-size: 14px;
    }
  </style>
</head>
<body>
  <div class="header">
    <div class="logo">
      <span class="blue"></span>
      <span class="green"></span>
      <span class="yellow"></span>
      <span class="red"></span>
    </div>
    <h1>Slack Stealth</h1>
  </div>

  <div id="status" class="status-bar">
    <span class="status-dot"></span>
    <span class="status-text">Ready to connect</span>
  </div>

  <div class="main">
    <div id="start-screen" class="start-screen">
      <div class="start-icon">
        <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>
      </div>
      <h2>Connect Your Workspace</h2>
      <div class="client-info">
        <strong>Claude</strong> wants to access your Slack workspace
      </div>
      <p>Sign in to Slack in the secure browser below. Your session tokens will be encrypted and stored safely.</p>
      <button id="start-btn" class="start-btn">Sign in to Slack</button>
    </div>

    <div id="browser-container" class="browser-container">
      <canvas id="browser-view"></canvas>

      <!-- 2FA Code Banner - shows the number to tap on your phone -->
      <div id="twofa-banner" class="twofa-banner">
        <span>Tap this number on your phone:</span>
        <span id="twofa-code" class="twofa-code">--</span>
      </div>

      <!-- Reconnect banner - shown when returning from 2FA -->
      <div id="reconnect-banner" class="reconnect-banner">
        <p>Session still active! Tap to reconnect after completing 2FA.</p>
        <button id="reconnect-btn" class="reconnect-btn">Reconnect to Browser</button>
      </div>

      <!-- Hidden input for mobile keyboard -->
      <input type="text" id="keyboard-input" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false"
        style="position: absolute; left: -9999px; top: 50%; opacity: 0; width: 1px; height: 1px;" />

      <!-- Floating keyboard button (mobile) -->
      <button id="keyboard-btn" class="keyboard-btn" title="Show Keyboard">⌨️</button>

      <!-- Desktop fallback - now shows QR code -->
      <button id="desktop-fallback" class="desktop-fallback" style="display: none;">
        📱 Switch to desktop
      </button>

      <!-- QR Code modal -->
      <div id="qr-modal" class="qr-modal">
        <h3>Continue on Desktop</h3>
        <p>Scan this QR code with your computer to continue authentication there. CAPTCHAs are easier on desktop!</p>
        <div class="qr-container">
          <canvas id="qr-canvas"></canvas>
        </div>
        <button id="close-qr" class="close-qr">Back to Mobile</button>
      </div>

      <div id="success-overlay" class="success-overlay">
        <div class="success-icon">
          <svg viewBox="0 0 24 24"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>
        </div>
        <h2>Connected!</h2>
        <p>Successfully authenticated to</p>
        <span id="workspace-name" class="workspace-name"></span>
        <p class="redirect-text">Redirecting back to Claude...</p>
      </div>
    </div>
  </div>

  <script>
    const oauthSessionId = "${oauthSessionId}";

    const canvas = document.getElementById('browser-view');
    const ctx = canvas.getContext('2d');
    const statusBar = document.getElementById('status');
    const statusText = statusBar.querySelector('.status-text');
    const startScreen = document.getElementById('start-screen');
    const startBtn = document.getElementById('start-btn');
    const browserContainer = document.getElementById('browser-container');
    const successOverlay = document.getElementById('success-overlay');
    const workspaceNameEl = document.getElementById('workspace-name');
    const keyboardInput = document.getElementById('keyboard-input');
    const keyboardBtn = document.getElementById('keyboard-btn');
    const desktopFallback = document.getElementById('desktop-fallback');
    const twofaBanner = document.getElementById('twofa-banner');
    const twofaCode = document.getElementById('twofa-code');
    const reconnectBanner = document.getElementById('reconnect-banner');
    const reconnectBtn = document.getElementById('reconnect-btn');
    const qrModal = document.getElementById('qr-modal');
    const qrCanvas = document.getElementById('qr-canvas');
    const closeQrBtn = document.getElementById('close-qr');

    let ws = null;
    let browserSessionId = null;
    let lastDetected2FACode = null;
    const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);

    // Store session ID in localStorage for reconnection after app switch
    function saveSessionId(id) {
      browserSessionId = id;
      try {
        localStorage.setItem('slack_auth_session', JSON.stringify({
          id: id,
          oauthSessionId: oauthSessionId,
          timestamp: Date.now()
        }));
      } catch (e) {}
    }

    function getSavedSession() {
      try {
        const saved = localStorage.getItem('slack_auth_session');
        if (!saved) return null;
        const data = JSON.parse(saved);
        // Session valid for 3 minutes
        if (Date.now() - data.timestamp > 180000) {
          localStorage.removeItem('slack_auth_session');
          return null;
        }
        if (data.oauthSessionId !== oauthSessionId) {
          localStorage.removeItem('slack_auth_session');
          return null;
        }
        return data;
      } catch (e) {
        return null;
      }
    }

    function clearSavedSession() {
      try { localStorage.removeItem('slack_auth_session'); } catch (e) {}
    }

    // Touch state for distinguishing tap vs scroll
    let touchStartPos = null;
    let touchStartTime = 0;
    let isTouchScrolling = false;
    const TAP_THRESHOLD = 15; // pixels - movement less than this is a tap
    const TAP_TIME_THRESHOLD = 300; // ms - tap must be shorter than this

    function updateStatus(text, type = '') {
      statusText.textContent = text;
      statusBar.className = 'status-bar ' + type;
    }

    let retryCountdown = null;

    async function startAuth() {
      startBtn.disabled = true;
      updateStatus('Starting secure browser...');

      // Clear any existing countdown
      if (retryCountdown) {
        clearInterval(retryCountdown);
        retryCountdown = null;
      }

      try {
        const response = await fetch('/auth/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ oauthSessionId })
        });

        const data = await response.json();
        if (!data.success) {
          // Handle rate limit with retry countdown
          if (data.isRateLimit && data.retryAfter) {
            startRateLimitCountdown(data.retryAfter, data.error);
            return;
          }
          throw new Error(data.error || 'Failed to start session');
        }

        saveSessionId(data.sessionId);
        updateStatus('Connecting...');

        startScreen.classList.add('hidden');
        browserContainer.classList.add('active');

        resizeCanvas();
        connectWebSocket();

      } catch (error) {
        updateStatus('Error: ' + error.message, 'error');
        startBtn.disabled = false;
      }
    }

    function startRateLimitCountdown(seconds, message) {
      let remaining = seconds;
      updateStatus(message + ' (retry in ' + remaining + 's)', 'error');
      startBtn.textContent = 'Retry in ' + remaining + 's';

      retryCountdown = setInterval(() => {
        remaining--;
        if (remaining <= 0) {
          clearInterval(retryCountdown);
          retryCountdown = null;
          startBtn.disabled = false;
          startBtn.textContent = 'Sign in to Slack';
          updateStatus('Ready to try again', 'ready');
        } else {
          startBtn.textContent = 'Retry in ' + remaining + 's';
          updateStatus(message + ' (retry in ' + remaining + 's)', 'error');
        }
      }, 1000);
    }

    function connectWebSocket() {
      const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      ws = new WebSocket(wsProtocol + '//' + location.host + '/auth/session/' + browserSessionId);

      ws.onopen = () => {
        updateStatus('Sign in to your Slack workspace', 'ready');
        reconnectBanner.classList.remove('active');

        // Show mobile UI elements
        if (isMobile) {
          keyboardBtn.style.display = 'flex';
          desktopFallback.style.display = 'block';
        }
      };

      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        handleMessage(msg);
      };

      ws.onerror = () => {
        updateStatus('Connection error', 'error');
      };

      ws.onclose = () => {
        if (!successOverlay.classList.contains('active')) {
          // Don't immediately give up - show reconnect option
          updateStatus('Disconnected - complete 2FA then reconnect', 'error');

          // Show reconnect banner on mobile
          if (isMobile && browserSessionId) {
            reconnectBanner.classList.add('active');
          }
        }
      };
    }

    // Reconnect to existing session
    async function attemptReconnect() {
      if (!browserSessionId) return;

      updateStatus('Reconnecting...');
      reconnectBanner.classList.remove('active');

      try {
        // Check if session is still alive
        const response = await fetch('/auth/session/' + browserSessionId + '/reconnect', {
          method: 'POST'
        });
        const data = await response.json();

        if (data.success && data.canReconnect) {
          // Session still active - reconnect WebSocket
          connectWebSocket();

          // Show any detected 2FA code
          if (data.detected2FACode) {
            show2FACode(data.detected2FACode);
          }
        } else {
          // Session expired - need to start fresh
          updateStatus('Session expired - please start again', 'error');
          clearSavedSession();
          startBtn.disabled = false;
          startScreen.classList.remove('hidden');
          browserContainer.classList.remove('active');
        }
      } catch (error) {
        updateStatus('Reconnect failed: ' + error.message, 'error');
      }
    }

    reconnectBtn.addEventListener('click', attemptReconnect);

    function show2FACode(code) {
      lastDetected2FACode = code;
      twofaCode.textContent = code;
      twofaBanner.classList.add('active');

      // Also save to localStorage so it persists during app switch
      try {
        localStorage.setItem('slack_auth_2fa', code);
      } catch (e) {}
    }

    function hide2FACode() {
      twofaBanner.classList.remove('active');
      try {
        localStorage.removeItem('slack_auth_2fa');
      } catch (e) {}
    }

    function handleMessage(msg) {
      switch (msg.type) {
        case 'frame':
          const img = new Image();
          img.onload = () => {
            ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
          };
          img.src = 'data:image/jpeg;base64,' + msg.data;
          break;

        case 'status':
          const types = { ready: 'ready', error: 'error', complete: 'complete' };
          updateStatus(msg.message || msg.status, types[msg.status] || '');
          break;

        case '2fa_code':
          show2FACode(msg.code);
          break;

        case 'auth_complete':
          hide2FACode();
          clearSavedSession();

          if (msg.success) {
            updateStatus('Connected to ' + msg.workspace, 'complete');
            workspaceNameEl.textContent = msg.workspace;
            successOverlay.classList.add('active');

            // Redirect to complete OAuth flow
            setTimeout(() => {
              window.location.href = '/authorize/complete?oauth=' + oauthSessionId + '&browser=' + browserSessionId;
            }, 1500);
          } else {
            updateStatus('Authentication failed: ' + msg.error, 'error');
          }
          break;
      }
    }

    function resizeCanvas() {
      const rect = browserContainer.getBoundingClientRect();
      const aspectRatio = 1280 / 800;
      let width = rect.width;
      let height = rect.height;

      if (width / height > aspectRatio) {
        width = height * aspectRatio;
      } else {
        height = width / aspectRatio;
      }

      canvas.width = 1280;
      canvas.height = 800;
      canvas.style.width = width + 'px';
      canvas.style.height = height + 'px';
    }

    window.addEventListener('resize', resizeCanvas);

    function getCoords(e) {
      const rect = canvas.getBoundingClientRect();
      const scaleX = 1280 / rect.width;
      const scaleY = 800 / rect.height;

      let clientX, clientY;
      // For touch events, use changedTouches (works for touchend when touches is empty)
      if (e.changedTouches && e.changedTouches.length > 0) {
        clientX = e.changedTouches[0].clientX;
        clientY = e.changedTouches[0].clientY;
      } else if (e.touches && e.touches.length > 0) {
        clientX = e.touches[0].clientX;
        clientY = e.touches[0].clientY;
      } else {
        clientX = e.clientX;
        clientY = e.clientY;
      }

      return {
        x: Math.round((clientX - rect.left) * scaleX),
        y: Math.round((clientY - rect.top) * scaleY)
      };
    }

    // Send a click at specific coordinates
    function sendClickAt(x, y) {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;

      ws.send(JSON.stringify({
        type: 'mouse',
        eventType: 'mousePressed',
        x, y,
        button: 'left',
        clickCount: 1
      }));

      setTimeout(() => {
        ws.send(JSON.stringify({
          type: 'mouse',
          eventType: 'mouseReleased',
          x, y,
          button: 'left',
          clickCount: 1
        }));
      }, 50);
    }

    // Send scroll event
    function sendScroll(deltaX, deltaY) {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;

      // Use mouse wheel events for scrolling
      ws.send(JSON.stringify({
        type: 'scroll',
        deltaX: deltaX,
        deltaY: deltaY
      }));
    }

    // Mouse click (desktop)
    canvas.addEventListener('click', (e) => {
      const { x, y } = getCoords(e);
      sendClickAt(x, y);
    });

    // ============ IMPROVED TOUCH HANDLING ============

    canvas.addEventListener('touchstart', (e) => {
      e.preventDefault();

      const touch = e.touches[0];
      touchStartPos = { x: touch.clientX, y: touch.clientY };
      touchStartTime = Date.now();
      isTouchScrolling = false;
    }, { passive: false });

    canvas.addEventListener('touchmove', (e) => {
      e.preventDefault();

      if (!touchStartPos) return;

      const touch = e.touches[0];
      const deltaX = touch.clientX - touchStartPos.x;
      const deltaY = touch.clientY - touchStartPos.y;
      const distance = Math.sqrt(deltaX * deltaX + deltaY * deltaY);

      // If moved more than threshold, it's a scroll
      if (distance > TAP_THRESHOLD) {
        isTouchScrolling = true;

        // Send scroll events (invert for natural scrolling)
        sendScroll(-deltaX * 2, -deltaY * 2);

        // Update start position for continuous scrolling
        touchStartPos = { x: touch.clientX, y: touch.clientY };
      }
    }, { passive: false });

    canvas.addEventListener('touchend', (e) => {
      e.preventDefault();

      const touchDuration = Date.now() - touchStartTime;

      // If it wasn't a scroll and was quick, it's a tap
      if (!isTouchScrolling && touchDuration < TAP_TIME_THRESHOLD) {
        const coords = getCoords(e);
        if (coords.x !== undefined && !isNaN(coords.x)) {
          sendClickAt(coords.x, coords.y);
        }
      }

      // Reset touch state
      touchStartPos = null;
      isTouchScrolling = false;
    }, { passive: false });

    // ============ MOBILE KEYBOARD HANDLING ============

    // Floating keyboard button
    keyboardBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      keyboardInput.focus();
      keyboardBtn.classList.add('active');
    });

    keyboardInput.addEventListener('blur', () => {
      keyboardBtn.classList.remove('active');
    });

    // Handle text input from mobile keyboard
    keyboardInput.addEventListener('input', (e) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;

      const text = e.target.value;
      if (text.length > 0) {
        // Send each character
        for (const char of text) {
          ws.send(JSON.stringify({
            type: 'key',
            eventType: 'keyDown',
            key: char,
            code: 'Key' + char.toUpperCase(),
            text: char,
            modifiers: 0
          }));

          ws.send(JSON.stringify({
            type: 'key',
            eventType: 'keyUp',
            key: char,
            code: 'Key' + char.toUpperCase()
          }));
        }

        // Clear input
        keyboardInput.value = '';
      }
    });

    // Handle special keys (backspace, enter, tab)
    keyboardInput.addEventListener('keydown', (e) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;

      const specialKeys = ['Backspace', 'Enter', 'Tab', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'];

      if (specialKeys.includes(e.key)) {
        e.preventDefault();

        ws.send(JSON.stringify({
          type: 'key',
          eventType: 'keyDown',
          key: e.key,
          code: e.code,
          modifiers: 0
        }));

        ws.send(JSON.stringify({
          type: 'key',
          eventType: 'keyUp',
          key: e.key,
          code: e.code
        }));
      }
    });

    canvas.addEventListener('mousemove', (e) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      const { x, y } = getCoords(e);
      ws.send(JSON.stringify({
        type: 'mouse',
        eventType: 'mouseMoved',
        x, y,
        button: 'none'
      }));
    });

    document.addEventListener('keydown', (e) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (!browserContainer.classList.contains('active')) return;

      e.preventDefault();

      let modifiers = 0;
      if (e.altKey) modifiers |= 1;
      if (e.ctrlKey) modifiers |= 2;
      if (e.metaKey) modifiers |= 4;
      if (e.shiftKey) modifiers |= 8;

      ws.send(JSON.stringify({
        type: 'key',
        eventType: 'keyDown',
        key: e.key,
        code: e.code,
        text: e.key.length === 1 ? e.key : undefined,
        modifiers
      }));
    });

    document.addEventListener('keyup', (e) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (!browserContainer.classList.contains('active')) return;

      e.preventDefault();

      ws.send(JSON.stringify({
        type: 'key',
        eventType: 'keyUp',
        key: e.key,
        code: e.code
      }));
    });

    startBtn.addEventListener('click', startAuth);

    // On page load, check for saved session (user returning from 2FA)
    document.addEventListener('DOMContentLoaded', () => {
      const saved = getSavedSession();
      if (saved) {
        browserSessionId = saved.id;

        // Check if session is still alive
        fetch('/auth/session/' + browserSessionId + '/status')
          .then(r => r.json())
          .then(data => {
            if (data.canReconnect) {
              // Session still active - show reconnect UI
              startScreen.classList.add('hidden');
              browserContainer.classList.add('active');
              resizeCanvas();

              updateStatus('Session active - tap to reconnect', 'ready');
              reconnectBanner.classList.add('active');

              // Restore 2FA code if saved
              const savedCode = localStorage.getItem('slack_auth_2fa');
              if (savedCode) {
                show2FACode(savedCode);
              }
            } else {
              // Session expired
              clearSavedSession();
            }
          })
          .catch(() => {
            clearSavedSession();
          });
      }
    });

    // Handle visibility change (user coming back from 2FA app)
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && browserSessionId && !ws) {
        // Page became visible and we have a session but no WebSocket
        // Show reconnect prompt
        if (browserContainer.classList.contains('active')) {
          reconnectBanner.classList.add('active');
        }
      }
    });

    // QR Code generation (minimal implementation)
    // Uses qr-creator library approach - generates QR code on canvas
    function generateQRCode(text, canvas, size = 200) {
      // Simple QR code using an external API as fallback
      // For a production app, you'd embed a proper QR library
      const ctx = canvas.getContext('2d');
      canvas.width = size;
      canvas.height = size;

      // Use QR code API
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => {
        ctx.drawImage(img, 0, 0, size, size);
      };
      img.onerror = () => {
        // Fallback: show URL as text
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, size, size);
        ctx.fillStyle = '#000';
        ctx.font = '12px monospace';
        ctx.textAlign = 'center';
        ctx.fillText('Scan failed', size/2, size/2 - 10);
        ctx.fillText('Copy URL:', size/2, size/2 + 10);
        ctx.font = '10px monospace';
        const shortUrl = text.length > 30 ? text.substring(0, 30) + '...' : text;
        ctx.fillText(shortUrl, size/2, size/2 + 30);
      };
      // Use a public QR code API
      img.src = 'https://api.qrserver.com/v1/create-qr-code/?size=' + size + 'x' + size + '&data=' + encodeURIComponent(text);
    }

    // Desktop fallback button - shows QR modal
    desktopFallback.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();

      // Generate QR code for current URL
      generateQRCode(window.location.href, qrCanvas, 200);
      qrModal.classList.add('active');
    });

    closeQrBtn.addEventListener('click', () => {
      qrModal.classList.remove('active');
    });

    // Close QR modal when clicking outside
    qrModal.addEventListener('click', (e) => {
      if (e.target === qrModal) {
        qrModal.classList.remove('active');
      }
    });
  </script>
</body>
</html>`;
}
