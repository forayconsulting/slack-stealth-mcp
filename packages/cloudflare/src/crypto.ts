/**
 * Token Encryption/Decryption using Web Crypto API
 *
 * Uses AES-256-GCM for authenticated encryption.
 * IV is prepended to ciphertext for storage.
 */

const ALGORITHM = "AES-GCM";
const IV_LENGTH = 12; // 96 bits recommended for GCM

/**
 * Derives a CryptoKey from a base64-encoded secret
 */
export async function deriveKey(base64Secret: string): Promise<CryptoKey> {
  const keyData = Uint8Array.from(atob(base64Secret), (c) => c.charCodeAt(0));

  return crypto.subtle.importKey("raw", keyData, { name: ALGORITHM }, false, [
    "encrypt",
    "decrypt",
  ]);
}

/**
 * Encrypts a string and returns base64-encoded ciphertext (IV prepended)
 */
export async function encrypt(
  plaintext: string,
  key: CryptoKey
): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(IV_LENGTH));
  const encoded = new TextEncoder().encode(plaintext);

  const ciphertext = await crypto.subtle.encrypt(
    { name: ALGORITHM, iv },
    key,
    encoded
  );

  // Combine IV + ciphertext
  const combined = new Uint8Array(iv.length + ciphertext.byteLength);
  combined.set(iv, 0);
  combined.set(new Uint8Array(ciphertext), iv.length);

  return btoa(String.fromCharCode(...combined));
}

/**
 * Decrypts a base64-encoded ciphertext (with prepended IV)
 */
export async function decrypt(
  ciphertext: string,
  key: CryptoKey
): Promise<string> {
  const combined = Uint8Array.from(atob(ciphertext), (c) => c.charCodeAt(0));

  const iv = combined.slice(0, IV_LENGTH);
  const data = combined.slice(IV_LENGTH);

  const decrypted = await crypto.subtle.decrypt(
    { name: ALGORITHM, iv },
    key,
    data
  );

  return new TextDecoder().decode(decrypted);
}

/**
 * Token storage types
 */
export interface StoredWorkspace {
  xoxc_token: string; // Encrypted
  xoxd_cookie: string; // Encrypted
  team_id: string;
  team_name: string;
  user_id?: string;
  user_name?: string;
  created_at: string;
  last_verified?: string;
}

export interface UserProfile {
  default_workspace: string;
  workspaces: string[];
  created_at: string;
}

/**
 * Helper to encrypt workspace tokens
 */
export async function encryptWorkspace(
  workspace: {
    xoxc_token: string;
    xoxd_cookie: string;
    team_id: string;
    team_name: string;
    user_id?: string;
    user_name?: string;
  },
  key: CryptoKey
): Promise<StoredWorkspace> {
  return {
    xoxc_token: await encrypt(workspace.xoxc_token, key),
    xoxd_cookie: await encrypt(workspace.xoxd_cookie, key),
    team_id: workspace.team_id,
    team_name: workspace.team_name,
    user_id: workspace.user_id,
    user_name: workspace.user_name,
    created_at: new Date().toISOString(),
  };
}

/**
 * Helper to decrypt workspace tokens
 */
export async function decryptWorkspace(
  stored: StoredWorkspace,
  key: CryptoKey
): Promise<{
  xoxc_token: string;
  xoxd_cookie: string;
  team_id: string;
  team_name: string;
  user_id?: string;
  user_name?: string;
}> {
  return {
    xoxc_token: await decrypt(stored.xoxc_token, key),
    xoxd_cookie: await decrypt(stored.xoxd_cookie, key),
    team_id: stored.team_id,
    team_name: stored.team_name,
    user_id: stored.user_id,
    user_name: stored.user_name,
  };
}
