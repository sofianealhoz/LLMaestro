# Tech watch

Collects what is new, scores every item with one cheap call, writes a ranked
digest. It is the first real job built on the router, and the load test that
proves it: a run sends more requests in a minute than a free tier allows, so
fallback, cooldown and quota accounting all get exercised for real.

## Run it

```bash
python3 -m llmaestro watch                      # collect, score, write out/watch-DATE.md
python3 -m llmaestro watch --no-score           # collect only, no provider needed
python3 -m llmaestro watch --limit 40           # score at most 40 new items
python3 -m llmaestro watch --source github      # one source
python3 -m llmaestro watch --no-write           # print the digest, mark nothing as seen
```

`--no-write` is the safe preview: nothing is remembered, so the same items come
back on the next run.

## Sources

No key anywhere.

| Source | What it pulls | How |
|---|---|---|
| `github` | repositories on the configured topics, pushed recently, above a star floor, plus the releases of watched repos | search API, unauthenticated |
| `hacker_news` | stories matching the configured queries | Algolia API |
| `reddit` | posts from the configured subreddits | the `connectors/reddit-search.mjs` connector, over the Pullpush archive |
| `anthropic` | the last entries of the Claude Code changelog | raw file on GitHub |

A source that fails is reported at the end of the digest, never fatal. Inside a
source, a single failing query is skipped: only a total loss counts as a
failure. Pullpush in particular drops requests under load.

## Scoring

One short call per item, sent through the worker pool to whichever provider the
router picks. The model answers a single JSON object: a mark out of 5 per axis
plus one sentence.

Axes live in the configuration, so retuning what you care about never touches
the code. The shipped ones look for methods to adopt rather than news to read:
`workflow`, `tooling`, `cost`.

Parsing is deliberately tolerant: models wrap JSON in prose or fences. Anything
unreadable scores zero and says so rather than failing the run.

## Configure

`watch.toml` when it exists, `watch.example.toml` otherwise. The example is
versioned, `watch.toml` is gitignored: personal topics and axes stay out of the
repository.

```toml
floor = 4                 # items below this total are listed, not detailed

[sources.github]
topics = ["claude-code", "mcp"]
min_stars = 5             # topic search sorted by activity is mostly noise below this
since_days = 14

[axes]
workflow = "a reusable working method I could adopt"
```

## Dedup

Every item carries a stable id (`github:owner/name`, `hn:12345`,
`reddit:abc123`, `changelog:2.1.220`). Ids are stored in the `seen` table of the
same sqlite database as the quota ledger, so an item is digested once and never
again. `--no-write` skips both the file and the memory.

## Cost

An item costs roughly 400 prompt tokens and 60 completion tokens. Sixty items
run in about thirty seconds on four workers. The free tiers are the constraint,
not the wall clock: Cerebras allows five requests a minute, so a large run
spills over to the other providers by design.
