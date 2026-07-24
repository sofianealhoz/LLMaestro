# LLMaestro

**Orchestration of multi-LLM AI agents, cloud and local.**

LLMaestro is a software orchestration layer that routes each task to the most
suitable LLM provider (cloud API or on-device model) according to **cost,
latency, and quality**, with **automatic fallback** on failure or rate-limit,
**tool-use** execution, **MCP** connectors, and **local inference**. A single
high-quality orchestrator stays in charge; self-contained sub-tasks are
dispatched to whichever provider can do them cheapest and fastest.

> **Status: work in progress.** This repository documents the architecture and
> ships the building blocks as they are implemented.
>

---

## Why

Running a *full* agent against free/low-cost APIs does not scale: a complete
agent sends **32–42k tokens per request** (base system prompt + tool schemas
are largely incompressible), which immediately hits per-minute token limits or
shared-pool rate-limits on free tiers. The same providers answer a **direct,
single-purpose call** with a short prompt in **~0.15s**, comfortably under those
limits.

LLMaestro is built on that finding:

- Keep a **capable orchestrator** in charge of planning and hard reasoning.
- **Offload leaf tasks** (reformat, translate, classify, summarize) as
  *targeted direct calls* to the cheapest provider that can do the job.
- **Fall back automatically** when a provider errors, rate-limits, or caps the
  context: try the next one in the chain instead of failing.

This keeps quality where it matters while staying within free/low-cost budgets.

---

## Architecture

```
                 task in
                    │
        ┌───────────▼────────────┐
        │      Orchestrator      │   planning · tiered routing
        └───────────┬────────────┘
                    │  route by cost / latency / quality
        ┌───────────▼────────────┐
        │         Router         │   automatic fallback on
        │  (provider selection)  │   error · 429 · context cap
        └──┬──────┬──────┬──────┬─┘
           │      │      │      │
      ┌────▼───┐ ┌▼────┐ ┌▼────┐ ┌▼──────┐
      │Cerebras│ │Groq │ │Open-│ │Ollama │   providers
      │ cloud  │ │cloud│ │Router│ │ local │
      └────────┘ └─────┘ └─────┘ └───────┘

   cross-cutting:  tool-use / connectors · MCP servers · output evaluation
```

The orchestrator decides *what* needs to happen; the router decides *where* it
runs. Cloud providers are reached over their HTTP APIs behind a unified
interface; local models run on-device via Ollama for sovereign, offline
inference. Connectors and MCP servers give agents the ability to take real
actions (search, browse, read external data).

---

## Components

| Layer | What it does | Status |
|---|---|---|
| **Router** | selects a provider by cost/latency/quality, with a fallback chain on failure | in progress, design validated |
| **Cloud providers** | Cerebras, Groq, OpenRouter behind a unified interface | in progress, connectivity verified |
| **Local inference** | Ollama (open-weights, on-device, sovereign) | planned |
| **Tool-use connectors** | self-contained tools an agent can call (e.g. Reddit search) | implemented, first connector: see [`connectors/`](connectors/) |
| **MCP integrations** | Model Context Protocol servers (browser automation, external data) | documented: see [`docs/MCP-INTEGRATIONS.md`](docs/MCP-INTEGRATIONS.md) |
| **Output evaluation** | scoring / QA pass on model outputs | planned |
| **Vision / computer-use** | agent "sees" the screen and acts on it | planned |

---

## Repository layout

```
connectors/   tool-use connectors (self-contained, callable by the orchestrator)
docs/         architecture deep-dives and integration notes
README.md     this file
LICENSE       MIT
```

---

## Providers

| Provider | Type | Role in the chain |
|---|---|---|
| Cerebras | cloud (free tier) | primary for leaf tasks: fast, generous daily quota (context-capped) |
| Groq | cloud (free tier) | high-volume fallback, very fast small models |
| OpenRouter | cloud (free tier) | secondary fallback, multi-model, single key |
| Ollama | local | sovereign / offline inference (planned) |

Credentials are read from the local environment and never committed.

---

## Roadmap

- [ ] Router with cost-tiered provider selection + fallback (Cerebras → Groq → OpenRouter)
- [ ] Local inference backend (Ollama, e.g. Qwen2.5-Coder)
- [ ] Output evaluation pass
- [ ] Vision / computer-use support

---

## Stack

- **Python**: orchestrator, provider router, and all new modules (provider SDKs,
  cloud and local).
- **Node.js**: existing standalone connectors (e.g. Reddit search).
- **MCP** (Model Context Protocol) for rich tool integrations; **YAML** for
  configuration.

## License

MIT, see [LICENSE](LICENSE).
