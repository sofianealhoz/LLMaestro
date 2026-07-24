# Connectors

Connectors are **self-contained tools** the orchestrator can call to take a real
action or pull external data. Each one is small, single-purpose, and usable on
its own from the command line, which keeps token overhead near zero (the
orchestrator invokes a connector as a shell call, not as a per-turn injected
tool schema).

> Design rule: if a capability is a single well-defined call, ship it as a
> connector here. Reserve [MCP servers](../docs/MCP-INTEGRATIONS.md) for rich,
> multi-function capabilities (e.g. browser automation).

---

## `reddit-search.mjs`

Full-text Reddit search over the [Pullpush.io](https://pullpush.io) archive
(a Pushshift successor): **no API key, no auth**. Works around the withdrawal
of self-service Reddit API keys and returns clean structured results.

```bash
# posts (submissions)
node connectors/reddit-search.mjs "my query"

# comments instead of posts
node connectors/reddit-search.mjs --type comment "my query"

# restrict to a subreddit
node connectors/reddit-search.mjs --sub selfhosted "wireguard"

# control result count + sort, raw JSON for parsing
node connectors/reddit-search.mjs --size 15 --sort score "rtk"
node connectors/reddit-search.mjs --json "my query"
```

**Options**

| Flag | Values | Default |
|---|---|---|
| `--type` | `submission` \| `comment` | `submission` |
| `--sub` | subreddit name | (all) |
| `--size` | 1–100 | 20 |
| `--sort` | `score` \| `created_utc` | `score` |
| `--json` | raw JSON output (for the orchestrator to parse) | off |

**Notes**

- Pullpush is an archive, so it may include content later edited or removed,
  useful for research, not a guarantee of current live state.
- Near real-time, with a possible slight lag on the very latest posts.

---

## Adding a connector

New connectors are written in **Python**; the existing Node connector stays as-is.
Whatever the language, follow the same contract:

1. Make it runnable standalone, e.g. `python connectors/<name>.py [...args]`
   (or `node connectors/<name>.mjs [...args]` for the existing Node one).
2. Support a `--json` mode so the orchestrator can parse the output.
3. Read any credentials from the environment, never hard-code or commit them.
4. Document it here with usage and options.
