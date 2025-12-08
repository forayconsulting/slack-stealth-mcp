# Product Requirements Document
## Slack MCP Server with Session-Based Authentication

**Version:** 1.0  
**Date:** December 8, 2025  
**Status:** Draft  
**Owner:** Engineering  

---

## 1. Executive Summary

This document outlines the requirements for a **Slack MCP (Model Context Protocol) Server** that enables AI assistants like Claude to read from and write to Slack workspaces on behalf of authenticated users. 

The key innovation is a **reverse proxy authentication mechanism** that extracts session tokens (xoxc/xoxd) without requiring users to create Slack apps, request admin approval, or perform complex OAuth flows.

The solution provides a frictionless authentication experience that works across all devices—including mobile—by having users log into Slack through a controlled proxy that captures their session credentials.

---

## 2. Problem Statement

### 2.1 Current State

Users who want AI assistants to interact with their Slack workspaces face significant friction:

| Barrier | Description |
|---------|-------------|
| **Admin-gated app installation** | Most Slack workspaces require administrator approval to install custom apps, which can take days or be denied outright. |
| **Complex OAuth flows** | Creating a Slack app requires navigating api.slack.com, configuring scopes, managing client secrets, and implementing callback handlers. |
| **Bot limitations** | Bot tokens (xoxb-) must be explicitly invited to each channel and cannot access private channels or DMs the user has access to. |
| **Mobile exclusion** | Desktop-based solutions (extracting tokens from Slack Desktop app) don't work for mobile-first users. |

### 2.2 Desired State

A user should be able to connect their Slack account to an MCP server by simply logging in—the same way they'd authenticate with any OAuth-based service—without needing admin permissions, app creation, or technical knowledge.

---

## 3. Background & Discovery

### 3.1 Slack Token Types

Through technical discovery, we identified the following Slack token types and their characteristics:

| Token Prefix | Name | Description |
|--------------|------|-------------|
| `xoxp-` | User OAuth Token | Official user token obtained via OAuth. Requires Slack app creation and potentially admin approval. |
| `xoxb-` | Bot Token | Bot token requiring app installation. Must be explicitly invited to each channel. Limited access. |
| `xoxc-` | Client Session Token | Web client session token. Acts as the user with full access to all user-accessible data. |
| `xoxd-` | Session Cookie | The "d" cookie that authenticates browser sessions. Paired with xoxc for API access. |
| `xoxe-` | Expiring Token | Newer rotating token format used in some OAuth flows. |
| `xapp-` | App-Level Token | Cross-organization app management token. Not relevant for user-level access. |

### 3.2 Key Discovery: xoxc/xoxd Token Pair

The `xoxc` and `xoxd` tokens together provide **full user-level API access** without requiring any Slack app creation or admin approval. These tokens:

- Are generated automatically when a user logs into Slack via web browser
- Provide identical permissions to the logged-in user (read/write all accessible channels, DMs, files, etc.)
- Work with all public Slack API endpoints
- Have long lifetimes (months to over a year, based on December 2025 observations)
- Are officially unsupported by Slack but not actively blocked

**Slack's official position** (from their support documentation): *"While we might not explicitly prevent it, using xoxc tokens for the API is not supported or recommended."*

This represents a pragmatic middle ground—Slack acknowledges the pattern exists but doesn't endorse or guarantee it.

### 3.3 The HttpOnly Challenge

A critical technical constraint emerged during discovery:

| Token | Storage Location | JavaScript Accessible? |
|-------|------------------|------------------------|
| `xoxc-` | localStorage | ✅ Yes |
| `xoxd-` | Cookie (HttpOnly flag) | ❌ No |

The `xoxd` cookie's **HttpOnly flag** prevents client-side JavaScript from accessing it. This is a security measure to prevent XSS attacks from stealing session cookies. This means:

- A browser extension can access it (via `chrome.cookies` API)
- A reverse proxy can intercept it (via Set-Cookie headers)
- A remote browser (Puppeteer/CDP)