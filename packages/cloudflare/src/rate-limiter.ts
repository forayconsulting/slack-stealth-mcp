/**
 * Rate Limiter with Token Bucket and Exponential Backoff
 *
 * Designed for Cloudflare Workers - no async locks needed since
 * each Durable Object instance is single-threaded.
 */

/**
 * Simple delay function
 */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Token bucket rate limiter with exponential backoff
 */
export class RateLimiter {
  private minInterval: number;
  private lastRequest: number = 0;
  private backoffMultiplier: number = 1;

  /**
   * Create a rate limiter
   * @param requestsPerSecond Maximum requests per second (default 2)
   */
  constructor(requestsPerSecond: number = 2) {
    this.minInterval = 1000 / requestsPerSecond;
  }

  /**
   * Wait until a request can be made
   */
  async acquire(): Promise<void> {
    const now = Date.now();
    const effectiveInterval = this.minInterval * this.backoffMultiplier;
    const waitTime = this.lastRequest + effectiveInterval - now;

    if (waitTime > 0) {
      await sleep(waitTime);
    }

    this.lastRequest = Date.now();
  }

  /**
   * Increase backoff after rate limit hit
   */
  backoff(): void {
    this.backoffMultiplier = Math.min(this.backoffMultiplier * 2, 60);
  }

  /**
   * Reset backoff after successful request
   */
  resetBackoff(): void {
    this.backoffMultiplier = 1;
  }

  /**
   * Get current backoff multiplier (for debugging)
   */
  getBackoff(): number {
    return this.backoffMultiplier;
  }
}
