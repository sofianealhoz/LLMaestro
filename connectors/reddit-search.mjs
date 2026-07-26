#!/usr/bin/env node
// reddit-search.mjs : Reddit full-text search via Pullpush.io (Pushshift archive, no key, no auth).
// Works around the withdrawal of self-service Reddit API keys.
//
// Usage:
//   node reddit-search.mjs "my query"                     # posts (submissions)
//   node reddit-search.mjs --type comment "my query"      # comments
//   node reddit-search.mjs --sub selfhosted "wireguard"   # restrict to a subreddit
//   node reddit-search.mjs --size 15 --sort score "rtk"   # count + sort (score|created_utc)
//   node reddit-search.mjs --json "x"                     # raw JSON, for the orchestrator to parse
//
// Pullpush is an archive: it may return content edited or removed since.
// Near real time, with a possible lag on the very latest posts.

const args = process.argv.slice(2);
const opt = { type: "submission", size: 20, sort: "score", sub: null, json: false, q: [] };
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === "--type") opt.type = args[++i];
  else if (a === "--size") opt.size = parseInt(args[++i], 10);
  else if (a === "--sort") opt.sort = args[++i];
  else if (a === "--sub") opt.sub = args[++i];
  else if (a === "--json") opt.json = true;
  else opt.q.push(a);
}
const query = opt.q.join(" ");
if (!query && !opt.sub) {
  console.error("Usage: node reddit-search.mjs [--type submission|comment] [--sub NAME] [--size N] [--sort score|created_utc] [--json] \"query\"");
  process.exit(1);
}

const base = `https://api.pullpush.io/reddit/search/${opt.type === "comment" ? "comment" : "submission"}/`;
const params = new URLSearchParams();
if (query) params.set("q", query);
if (opt.sub) params.set("subreddit", opt.sub);
params.set("size", String(Math.min(opt.size, 100)));
params.set("sort", "desc");
params.set("sort_type", opt.sort === "created_utc" ? "created_utc" : "score");

const url = `${base}?${params.toString()}`;

function fmtDate(utc) {
  if (!utc) return "?";
  return new Date(utc * 1000).toISOString().slice(0, 10);
}
function clip(s, n) {
  if (!s) return "";
  s = String(s).replace(/\s+/g, " ").trim();
  return s.length > n ? s.slice(0, n) + "..." : s;
}

try {
  const res = await fetch(url, { headers: { "User-Agent": "Mozilla/5.0 reddit-search" }, signal: AbortSignal.timeout(30000) });
  if (!res.ok) {
    console.error(`Pullpush HTTP ${res.status} ${res.statusText}. URL: ${url}`);
    process.exit(2);
  }
  const json = await res.json();
  const data = json.data || [];
  // No process.exit after printing: piped stdout is async, exiting truncates
  // large output mid-string.
  if (opt.json) {
    console.log(JSON.stringify(data, null, 2));
  } else if (!data.length) {
    console.log(`No result for "${query}"${opt.sub ? " in r/" + opt.sub : ""}.`);
  } else {
    console.log(`# ${data.length} results (${opt.type}) for "${query}"${opt.sub ? " | r/" + opt.sub : ""} | sort:${opt.sort}\n`);
    for (const d of data) {
      const score = (d.score ?? 0).toString().padStart(5, " ");
      const sub = d.subreddit || "?";
      const date = fmtDate(d.created_utc);
      if (opt.type === "comment") {
        const link = `https://reddit.com${d.permalink || ""}`;
        console.log(`[${score}] r/${sub} | u/${d.author} | ${date}`);
        console.log(`  ${clip(d.body, 280)}`);
        console.log(`  ${link}\n`);
      } else {
        const link = d.url && !d.url.includes("reddit.com") ? d.url : `https://reddit.com${d.permalink || ""}`;
        const nc = d.num_comments ?? 0;
        console.log(`[${score} | ${nc}c] r/${sub} | ${date} | ${clip(d.title, 140)}`);
        if (d.selftext) console.log(`  ${clip(d.selftext, 260)}`);
        console.log(`  ${link}\n`);
      }
    }
  }
} catch (e) {
  console.error("Error:", e.message);
  process.exit(3);
}
