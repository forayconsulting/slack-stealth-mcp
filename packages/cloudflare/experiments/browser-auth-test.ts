/**
 * Browser Rendering Experiment
 *
 * Tests whether Cloudflare Browser Rendering can access Slack's login page
 * without being blocked by bot detection.
 *
 * Run with: wrangler dev experiments/browser-auth-test.ts
 * Then visit: http://localhost:8787/test
 *
 * Expected outcomes:
 * 1. SUCCESS: Page loads, can interact with login form
 * 2. BLOCKED: Captcha, "browser not supported", or redirect to error page
 * 3. PARTIAL: Page loads but some functionality is broken
 */

import puppeteer from "@cloudflare/puppeteer";

interface Env {
  BROWSER: Fetcher;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/test") {
      return testSlackAccess(env);
    }

    if (url.pathname === "/screenshot") {
      return takeScreenshot(env);
    }

    return new Response(JSON.stringify({
      message: "Cloudflare Browser Rendering - Slack Auth Test",
      endpoints: {
        "/test": "Run the Slack access test (returns JSON)",
        "/screenshot": "Take a screenshot of Slack login page (returns PNG)"
      }
    }, null, 2), {
      headers: { "Content-Type": "application/json" }
    });
  }
};

async function testSlackAccess(env: Env): Promise<Response> {
  const results: TestResults = {
    timestamp: new Date().toISOString(),
    tests: [],
    conclusion: "unknown"
  };

  let browser;

  try {
    // Connect to Cloudflare Browser
    browser = await puppeteer.launch(env.BROWSER);
    const page = await browser.newPage();

    // Set a reasonable viewport
    await page.setViewport({ width: 1280, height: 800 });

    // Test 1: Can we reach slack.com?
    results.tests.push({
      name: "Navigate to slack.com",
      status: "running"
    });

    const response = await page.goto("https://slack.com", {
      waitUntil: "networkidle0",
      timeout: 30000
    });

    results.tests[0].status = response?.ok() ? "pass" : "fail";
    results.tests[0].details = {
      statusCode: response?.status(),
      url: page.url()
    };

    // Test 2: Can we reach the sign-in page?
    results.tests.push({
      name: "Navigate to signin page",
      status: "running"
    });

    const signinResponse = await page.goto("https://slack.com/signin", {
      waitUntil: "networkidle0",
      timeout: 30000
    });

    results.tests[1].status = signinResponse?.ok() ? "pass" : "fail";
    results.tests[1].details = {
      statusCode: signinResponse?.status(),
      url: page.url(),
      title: await page.title()
    };

    // Test 3: Check for CAPTCHA or bot detection
    results.tests.push({
      name: "Check for bot detection",
      status: "running"
    });

    const pageContent = await page.content();
    const botDetectionSignals = {
      hasCaptcha: pageContent.includes("captcha") ||
                  pageContent.includes("CAPTCHA") ||
                  pageContent.includes("recaptcha") ||
                  pageContent.includes("hCaptcha"),
      hasBotMessage: pageContent.includes("bot") && pageContent.includes("detected") ||
                     pageContent.includes("automated") ||
                     pageContent.includes("browser not supported"),
      hasLoginForm: pageContent.includes('type="email"') ||
                    pageContent.includes('type="password"') ||
                    pageContent.includes("Sign in to Slack"),
      pageTitle: await page.title()
    };

    results.tests[2].status = botDetectionSignals.hasCaptcha || botDetectionSignals.hasBotMessage ? "fail" : "pass";
    results.tests[2].details = botDetectionSignals;

    // Test 4: Can we interact with the page?
    results.tests.push({
      name: "Test page interactivity",
      status: "running"
    });

    try {
      // Try to find the email input field
      const emailInput = await page.$('input[type="email"], input[data-qa="login_email"]');
      results.tests[3].status = emailInput ? "pass" : "fail";
      results.tests[3].details = {
        emailInputFound: !!emailInput,
        note: emailInput ? "Found email input - page is interactive" : "Email input not found"
      };
    } catch (e) {
      results.tests[3].status = "fail";
      results.tests[3].details = {
        error: String(e)
      };
    }

    // Determine overall conclusion
    const passedTests = results.tests.filter(t => t.status === "pass").length;
    const totalTests = results.tests.length;

    if (passedTests === totalTests) {
      results.conclusion = "SUCCESS - Slack login page accessible via Browser Rendering!";
    } else if (results.tests[2].status === "fail") {
      results.conclusion = "BLOCKED - Bot detection triggered. Need alternative auth approach.";
    } else {
      results.conclusion = `PARTIAL - ${passedTests}/${totalTests} tests passed. Review details.`;
    }

  } catch (error) {
    results.tests.push({
      name: "Critical error",
      status: "fail",
      details: {
        error: String(error),
        stack: error instanceof Error ? error.stack : undefined
      }
    });
    results.conclusion = "ERROR - Browser Rendering failed. Check error details.";
  } finally {
    if (browser) {
      await browser.close();
    }
  }

  return new Response(JSON.stringify(results, null, 2), {
    headers: { "Content-Type": "application/json" }
  });
}

async function takeScreenshot(env: Env): Promise<Response> {
  let browser;

  try {
    browser = await puppeteer.launch(env.BROWSER);
    const page = await browser.newPage();

    await page.setViewport({ width: 1280, height: 800 });
    await page.goto("https://slack.com/signin", {
      waitUntil: "networkidle0",
      timeout: 30000
    });

    const screenshot = await page.screenshot({
      type: "png",
      fullPage: false
    });

    return new Response(screenshot, {
      headers: {
        "Content-Type": "image/png",
        "Content-Disposition": "inline; filename=slack-signin.png"
      }
    });

  } catch (error) {
    return new Response(JSON.stringify({
      error: String(error),
      message: "Failed to take screenshot"
    }), {
      status: 500,
      headers: { "Content-Type": "application/json" }
    });
  } finally {
    if (browser) {
      await browser.close();
    }
  }
}

interface TestResult {
  name: string;
  status: "pass" | "fail" | "running";
  details?: Record<string, unknown>;
}

interface TestResults {
  timestamp: string;
  tests: TestResult[];
  conclusion: string;
}
