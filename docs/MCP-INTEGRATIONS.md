# MCP integrations

LLMaestro gives agents the ability to take real actions through the
[Model Context Protocol (MCP)](https://modelcontextprotocol.io): a standard
that exposes external tools and data sources to an LLM through a uniform
interface. This document records the MCP servers wired into the orchestrator and
the integration decisions behind them.

---

## How MCP fits in

MCP servers are **side-loaded capabilities**: the orchestrator declares the
servers it trusts, and their tools become callable by the agent at runtime.
Two practical constraints shape how LLMaestro uses them:

1. **Token overhead.** Every connected MCP server injects its tool definitions
   into the model context on each turn. Connect only what a task needs, and
   prefer a thin custom connector when a single function is all that is required
   (see the Reddit case study below).
2. **Runtime binding.** Some MCP servers are bound to a specific host runtime
   and only resolve there. The orchestrator treats those as host-specific tools
   rather than portable connectors.

---

## Active integrations

### Browser automation: Playwright MCP

- **Server:** `@playwright/mcp`
- **Capability:** drive a real browser: `navigate`, `snapshot`, read rendered
  content, interact with JavaScript-heavy pages.
- **Why:** verifying live web state and reading SPA content that plain HTTP
  fetches cannot see. Anything that needs the *rendered* DOM goes through here
  rather than a raw `fetch`.
- **Wiring:** declared per-project via an `.mcp.json` manifest so the server is
  discovered deterministically; the server is trusted on first launch.

---

## Case study: when *not* to use an MCP

### Reddit search: from MCP to a custom connector

The Reddit integration is a good illustration of the "thin connector over heavy
MCP" trade-off.

1. **Attempt, MCP server.** A Reddit MCP server (`reddit-mcp-buddy`) was added.
   It connected, but two problems surfaced: the official Reddit API path it
   relied on had become unreliable (self-service keys withdrawn), and the server
   injected **five tool definitions on every turn** for what was, in practice, a
   single "search Reddit" need.
2. **Decision, replace with a direct connector.** The MCP server was removed
   and replaced by a **~85-line Node connector** that queries the
   [Pullpush.io](https://pullpush.io) archive (a Pushshift successor) directly:
   no key, no auth, full-text search over posts *and* comments.
3. **Result.** Zero per-turn token overhead, no dependency on a flaky upstream
   API, and a clean structured output the orchestrator can parse.

→ See [`connectors/reddit-search.mjs`](../connectors/reddit-search.mjs) and the
[connectors guide](../connectors/README.md).

**Takeaway:** MCP is the right tool for rich, multi-function capabilities
(browser automation). For a single well-defined call, a small purpose-built
connector is cheaper, more robust, and easier to reason about.

---

## Planned

| Capability | Approach | Status |
|---|---|---|
| External data sources (job boards, knowledge bases) | MCP where multi-function; thin connectors otherwise | planned |
| Vision / computer-use | MCP-exposed screen capture + a vision model | planned |
