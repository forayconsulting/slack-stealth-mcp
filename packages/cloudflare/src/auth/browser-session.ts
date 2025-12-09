/**
 * BrowserAuthSession - Durable Object for Interactive Browser Authentication
 *
 * Uses Cloudflare Browser Rendering with CDP to:
 * 1. Stream browser frames to the user via WebSocket
 * 2. Forward user input (mouse/keyboard) to the browser
 * 3. Detect login completion and extract tokens
 */

import puppeteer, { Browser, Page, CDPSession } from "@cloudflare/puppeteer";
import type { Env } from "../types/env";

/**
 * Browser auth result stored temporarily for OAuth flow
 */
interface BrowserAuthResult {
  success: boolean;
  tokens?: {
    xoxc_token: string;
    xoxd_cookie: string;
    team_id: string;
    team_name: string;
  };
  error?: string;
}

// Message types for WebSocket communication
interface ScreencastFrameMessage {
  type: "frame";
  data: string; // base64 JPEG
  sessionId: number;
}

interface MouseEventMessage {
  type: "mouse";
  eventType: "mousePressed" | "mouseReleased" | "mouseMoved";
  x: number;
  y: number;
  button: "left" | "right" | "middle" | "none";
  clickCount?: number;
}

interface KeyEventMessage {
  type: "key";
  eventType: "keyDown" | "keyUp" | "char";
  key: string;
  code?: string;
  text?: string;
  modifiers?: number; // bitmask: Alt=1, Ctrl=2, Meta=4, Shift=8
}

interface ScrollEventMessage {
  type: "scroll";
  deltaX: number;
  deltaY: number;
}

interface AuthCompleteMessage {
  type: "auth_complete";
  success: boolean;
  workspace?: string;
  error?: string;
}

interface StatusMessage {
  type: "status";
  status: "connecting" | "ready" | "authenticating" | "complete" | "error";
  message?: string;
  url?: string;
}

interface TwoFactorCodeMessage {
  type: "2fa_code";
  code: string;
  message: string;
}

type IncomingMessage = MouseEventMessage | KeyEventMessage | ScrollEventMessage | { type: "ping" };
type OutgoingMessage =
  | ScreencastFrameMessage
  | AuthCompleteMessage
  | StatusMessage
  | TwoFactorCodeMessage;

export class BrowserAuthSession {
  private state: DurableObjectState;
  private env: Env;
  private browser: Browser | null = null;
  private page: Page | null = null;
  private cdpSession: CDPSession | null = null;
  private webSocket: WebSocket | null = null;
  private isComplete = false;

  // Session persistence for 2FA handling
  private disconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private readonly DISCONNECT_GRACE_PERIOD = 180000; // 3 minutes to handle 2FA
  private lastDetected2FACode: string | null = null;

  constructor(state: DurableObjectState, env: Env) {
    this.state = state;
    this.env = env;
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);

    // WebSocket upgrade for streaming
    if (request.headers.get("Upgrade") === "websocket") {
      return this.handleWebSocket(request);
    }

    // Start auth session
    if (url.pathname === "/start" && request.method === "POST") {
      return this.handleStart(request);
    }

    // Get session status
    if (url.pathname === "/status") {
      return new Response(
        JSON.stringify({
          active: this.browser !== null,
          complete: this.isComplete,
          canReconnect: this.browser !== null && !this.isComplete,
          detected2FACode: this.lastDetected2FACode,
        }),
        { headers: { "Content-Type": "application/json" } }
      );
    }

    // Reconnect to existing session
    if (url.pathname === "/reconnect" && request.method === "POST") {
      return this.handleReconnect();
    }

    return new Response("Not found", { status: 404 });
  }

  /**
   * Handle reconnection to an existing browser session
   * Used when user returns after switching apps for 2FA
   */
  private async handleReconnect(): Promise<Response> {
    // Cancel pending cleanup if reconnecting
    if (this.disconnectTimeout) {
      clearTimeout(this.disconnectTimeout);
      this.disconnectTimeout = null;
    }

    if (!this.browser || !this.page) {
      return new Response(
        JSON.stringify({
          success: false,
          error: "No active browser session to reconnect to",
          canReconnect: false,
        }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    if (this.isComplete) {
      return new Response(
        JSON.stringify({
          success: false,
          error: "Session already completed",
          canReconnect: false,
        }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    return new Response(
      JSON.stringify({
        success: true,
        message: "Session still active. Connect via WebSocket to resume.",
        canReconnect: true,
        detected2FACode: this.lastDetected2FACode,
      }),
      { headers: { "Content-Type": "application/json" } }
    );
  }

  private async handleStart(request: Request): Promise<Response> {
    try {
      // Launch browser with retry logic for rate limits
      let lastError: Error | null = null;
      const maxRetries = 3;

      for (let attempt = 0; attempt < maxRetries; attempt++) {
        try {
          this.browser = await puppeteer.launch(this.env.BROWSER);
          break; // Success!
        } catch (err) {
          lastError = err as Error;
          const errorStr = String(err);

          // Check if it's a rate limit error
          if (errorStr.includes("429") || errorStr.includes("Rate limit")) {
            if (attempt < maxRetries - 1) {
              // Wait before retrying (exponential backoff)
              const waitMs = Math.pow(2, attempt) * 2000; // 2s, 4s, 8s
              await new Promise((resolve) => setTimeout(resolve, waitMs));
              continue;
            }
          }

          // Not a rate limit or out of retries
          throw err;
        }
      }

      if (!this.browser) {
        throw lastError || new Error("Failed to launch browser");
      }

      this.page = await this.browser.newPage();

      // Set viewport for consistent experience
      await this.page.setViewport({ width: 1280, height: 800 });

      // Navigate to Slack signin
      await this.page.goto("https://slack.com/signin", {
        waitUntil: "networkidle0",
      });

      return new Response(
        JSON.stringify({
          success: true,
          sessionId: this.state.id.toString(),
          message: "Browser session started. Connect via WebSocket to interact.",
        }),
        { headers: { "Content-Type": "application/json" } }
      );
    } catch (error) {
      const errorStr = String(error);

      // Provide user-friendly error messages
      let userMessage = errorStr;
      let retryAfter: number | null = null;

      if (errorStr.includes("429") || errorStr.includes("Rate limit")) {
        if (errorStr.includes("time limit exceeded") || errorStr.includes("Browser time")) {
          userMessage =
            "Daily browser limit reached (free tier: 10 min/day). Try again tomorrow or use the Local MCP instead.";
        } else {
          userMessage =
            "Too many auth sessions recently. Please wait 1-2 minutes and try again.";
          retryAfter = 60;
        }
      }

      return new Response(
        JSON.stringify({
          success: false,
          error: userMessage,
          isRateLimit: errorStr.includes("429"),
          retryAfter,
        }),
        { status: 429, headers: { "Content-Type": "application/json" } }
      );
    }
  }

  private handleWebSocket(request: Request): Response {
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);

    // Cancel any pending cleanup - user is reconnecting
    if (this.disconnectTimeout) {
      clearTimeout(this.disconnectTimeout);
      this.disconnectTimeout = null;
      console.log("User reconnected - cancelled pending cleanup");
    }

    // Use WebSocket hibernation for efficiency
    this.state.acceptWebSocket(server);
    this.webSocket = server;

    // Start streaming (or resume if already running)
    this.startScreencast();

    return new Response(null, {
      status: 101,
      webSocket: client,
    });
  }

  // WebSocket hibernation handlers
  async webSocketMessage(ws: WebSocket, message: string | ArrayBuffer) {
    if (typeof message !== "string") return;

    try {
      const msg = JSON.parse(message) as IncomingMessage;

      switch (msg.type) {
        case "mouse":
          await this.handleMouseEvent(msg);
          break;
        case "key":
          await this.handleKeyEvent(msg);
          break;
        case "scroll":
          await this.handleScrollEvent(msg);
          break;
        case "ping":
          this.send({ type: "status", status: "ready" });
          break;
      }
    } catch (error) {
      console.error("Error handling message:", error);
    }
  }

  async webSocketClose(ws: WebSocket) {
    // Don't immediately cleanup - user might be handling 2FA
    // Keep browser alive for grace period to allow reconnection
    console.log("WebSocket closed - starting grace period for potential reconnect");

    this.webSocket = null;

    // Only schedule cleanup if not already complete
    if (!this.isComplete && this.browser) {
      this.disconnectTimeout = setTimeout(async () => {
        console.log("Grace period expired - cleaning up browser session");
        await this.cleanup();
      }, this.DISCONNECT_GRACE_PERIOD);
    } else {
      await this.cleanup();
    }
  }

  async webSocketError(ws: WebSocket, error: unknown) {
    console.error("WebSocket error:", error);
    await this.cleanup();
  }

  private async startScreencast() {
    if (!this.page || !this.webSocket) return;

    try {
      // Get CDP session
      this.cdpSession = await this.page.createCDPSession();

      // Subscribe to screencast frames
      this.cdpSession.on("Page.screencastFrame", async (params) => {
        // Send frame to client
        this.send({
          type: "frame",
          data: params.data,
          sessionId: params.sessionId,
        });

        // Acknowledge frame
        await this.cdpSession?.send("Page.screencastFrameAck", {
          sessionId: params.sessionId,
        });

        // Check if login completed
        const currentUrl = this.page?.url() || "";
        if (currentUrl.includes("/client/") && !this.isComplete) {
          await this.handleLoginComplete();
        }

        // Detect 2FA codes on Google pages
        if (currentUrl.includes("google.com") || currentUrl.includes("accounts.google")) {
          await this.detect2FACode();
        }
      });

      // Start screencast with high quality for CAPTCHA visibility
      await this.cdpSession.send("Page.startScreencast", {
        format: "jpeg",
        quality: 95, // Higher quality for text/CAPTCHA clarity
        maxWidth: 1280,
        maxHeight: 800,
        everyNthFrame: 1,
      });

      this.send({ type: "status", status: "ready", url: this.page.url() });
    } catch (error) {
      console.error("Error starting screencast:", error);
      this.send({
        type: "status",
        status: "error",
        message: String(error),
      });
    }
  }

  private async handleMouseEvent(msg: MouseEventMessage) {
    if (!this.cdpSession) return;

    try {
      await this.cdpSession.send("Input.dispatchMouseEvent", {
        type: msg.eventType,
        x: msg.x,
        y: msg.y,
        button: msg.button,
        clickCount: msg.clickCount || 1,
      });
    } catch (error) {
      console.error("Error dispatching mouse event:", error);
    }
  }

  private async handleKeyEvent(msg: KeyEventMessage) {
    if (!this.cdpSession) return;

    try {
      await this.cdpSession.send("Input.dispatchKeyEvent", {
        type: msg.eventType,
        key: msg.key,
        code: msg.code,
        text: msg.text,
        modifiers: msg.modifiers || 0,
      });
    } catch (error) {
      console.error("Error dispatching key event:", error);
    }
  }

  private async handleScrollEvent(msg: ScrollEventMessage) {
    if (!this.cdpSession || !this.page) return;

    try {
      // Use page.evaluate for more reliable scrolling
      await this.page.evaluate((deltaY: number) => {
        window.scrollBy(0, deltaY);
      }, msg.deltaY);
    } catch (error) {
      console.error("Error dispatching scroll event:", error);
    }
  }

  private async handleLoginComplete() {
    if (!this.page || this.isComplete) return;

    this.isComplete = true;
    this.send({ type: "status", status: "authenticating" });

    const sessionId = this.state.id.toString();

    try {
      // Extract xoxc token from localStorage
      // Note: This code runs in the browser context via Puppeteer's evaluate()
      const xoxcToken = await this.page.evaluate(`
        (function() {
          for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            if (!key) continue;
            const value = localStorage.getItem(key);
            if (!value) continue;

            // Direct match
            if (value.startsWith("xoxc-")) {
              return value;
            }

            // Search in JSON values
            try {
              const parsed = JSON.parse(value);
              const str = JSON.stringify(parsed);
              const match = str.match(/xoxc-[a-zA-Z0-9_-]+/);
              if (match) return match[0];
            } catch {
              // Not JSON, check for embedded token
              const match = value.match(/xoxc-[a-zA-Z0-9_-]+/);
              if (match) return match[0];
            }
          }
          return null;
        })()
      `) as string | null;

      if (!xoxcToken) {
        throw new Error("Could not extract xoxc token from localStorage");
      }

      // Extract xoxd cookie
      const cookies = await this.page.cookies();
      const xoxdCookie = cookies.find(
        (c) => c.name === "d" && c.domain.includes("slack.com")
      );

      if (!xoxdCookie) {
        throw new Error("Could not extract xoxd cookie");
      }

      // Extract team info from URL
      const currentUrl = this.page.url();
      const teamMatch = currentUrl.match(/\/client\/([A-Z0-9]+)/);
      const teamId = teamMatch ? teamMatch[1] : "unknown";

      // Get team name from page title
      const pageTitle = await this.page.title();
      const teamName = pageTitle.replace(" | Slack", "").trim() || teamId;

      // Store tokens temporarily in SESSIONS KV for OAuth flow
      // The OAuth handler will retrieve these and put them in the access token props
      const result: BrowserAuthResult = {
        success: true,
        tokens: {
          xoxc_token: xoxcToken,
          xoxd_cookie: xoxdCookie.value,
          team_id: teamId,
          team_name: teamName,
        },
      };
      await this.env.SESSIONS.put(
        `browser-result:${sessionId}`,
        JSON.stringify(result),
        { expirationTtl: 300 } // 5 minutes - enough time to complete OAuth
      );

      // Notify client
      this.send({
        type: "auth_complete",
        success: true,
        workspace: teamName,
      });

      this.send({
        type: "status",
        status: "complete",
        message: `Successfully authenticated workspace: ${teamName}`,
      });
    } catch (error) {
      // Store error result
      const result: BrowserAuthResult = {
        success: false,
        error: String(error),
      };
      await this.env.SESSIONS.put(
        `browser-result:${sessionId}`,
        JSON.stringify(result),
        { expirationTtl: 300 }
      );

      this.send({
        type: "auth_complete",
        success: false,
        error: String(error),
      });

      this.send({
        type: "status",
        status: "error",
        message: String(error),
      });
    } finally {
      // Cleanup after a short delay
      setTimeout(() => this.cleanup(), 5000);
    }
  }

  /**
   * Detect 2FA verification codes on Google sign-in pages
   * Extracts the number and sends it to the client so they can see it
   * even when switching apps to complete 2FA
   */
  private async detect2FACode() {
    if (!this.page) return;

    try {
      // Google shows 2FA codes in various formats - try to extract them
      const code = await this.page.evaluate(() => {
        // Look for the 2-digit number Google shows for device matching
        // Common selectors for Google's 2FA number display
        const selectors = [
          '[data-verification-code]',
          '.verification-code',
          '[aria-label*="number"]',
          'div[jsname] span[jsname]', // Google's dynamic elements
        ];

        for (const selector of selectors) {
          const elements = document.querySelectorAll(selector);
          for (const el of elements) {
            const text = el.textContent?.trim() || '';
            // Google 2FA codes are typically 2-digit numbers
            if (/^\d{2}$/.test(text)) {
              return text;
            }
          }
        }

        // Fallback: Look for any large standalone 2-digit number in the page
        // Google displays these prominently
        const allElements = document.querySelectorAll('*');
        for (const el of allElements) {
          const text = el.textContent?.trim() || '';
          const style = window.getComputedStyle(el);
          const fontSize = parseFloat(style.fontSize);

          // Look for large text that's just a 2-digit number
          if (/^\d{2}$/.test(text) && fontSize >= 24) {
            return text;
          }
        }

        return null;
      });

      if (code && code !== this.lastDetected2FACode) {
        this.lastDetected2FACode = code;
        console.log("Detected 2FA code:", code);

        // Send to client
        this.send({
          type: "2fa_code",
          code: code,
          message: `Tap ${code} on your phone to verify`,
        });
      }
    } catch (error) {
      // Silently ignore - 2FA detection is best-effort
      console.error("Error detecting 2FA code:", error);
    }
  }

  private send(msg: OutgoingMessage) {
    if (this.webSocket?.readyState === WebSocket.OPEN) {
      this.webSocket.send(JSON.stringify(msg));
    }
  }

  private async cleanup() {
    try {
      if (this.cdpSession) {
        await this.cdpSession.send("Page.stopScreencast").catch(() => {});
        await this.cdpSession.detach().catch(() => {});
        this.cdpSession = null;
      }

      if (this.browser) {
        await this.browser.close().catch(() => {});
        this.browser = null;
      }

      this.page = null;

      if (this.webSocket?.readyState === WebSocket.OPEN) {
        this.webSocket.close();
      }
      this.webSocket = null;
    } catch (error) {
      console.error("Error during cleanup:", error);
    }
  }
}
