"""Watch collectors. None of them needs a key."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from ..transport import get

GITHUB_SEARCH = "https://api.github.com/search/repositories"
GITHUB_RELEASES = "https://api.github.com/repos/{repo}/releases"
GITHUB_HEADERS = {"accept": "application/vnd.github+json"}
HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
CHANGELOG = "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md"
CHANGELOG_URL = "https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md"

CLIP = 400
TIMEOUT = 20.0


@dataclass(frozen=True)
class Item:
    id: str
    title: str
    url: str
    source: str
    text: str = ""
    date: str = ""


def github(settings: dict) -> list[Item]:
    items, failures = [], []
    days = int(settings.get("since_days", 14))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    per_topic = int(settings.get("per_topic", 15))

    # Topic search sorted by activity surfaces a lot of empty repos pushed an
    # hour ago. Filtering on stars before scoring saves the calls.
    min_stars = int(settings.get("min_stars", 5))

    for topic in settings.get("topics", []):
        query = quote_plus(f"topic:{topic} pushed:>={since} stars:>={min_stars}")
        url = f"{GITHUB_SEARCH}?q={query}&sort=updated&order=desc&per_page={per_topic}"
        try:
            found = _payload(url, "github", GITHUB_HEADERS).get("items", [])
        except Exception as error:
            failures.append(f"topic {topic}: {error}")
            continue
        for repo in found:
            topics = ", ".join(repo.get("topics") or [])
            items.append(
                Item(
                    id=f"github:{repo['full_name']}",
                    title=repo["full_name"],
                    url=repo.get("html_url", ""),
                    source="github",
                    text=_clip(
                        f"{repo.get('description') or ''} "
                        f"[{repo.get('stargazers_count', 0)} stars, {topics}]"
                    ),
                    date=(repo.get("pushed_at") or "")[:10],
                )
            )

    limit = int(settings.get("release_limit", 5))
    for repo in settings.get("repos", []):
        url = f"{GITHUB_RELEASES.format(repo=repo)}?per_page={limit}"
        try:
            releases = _payload(url, "github", GITHUB_HEADERS, expect=list)
        except Exception as error:
            failures.append(f"releases {repo}: {error}")
            continue
        for release in releases:
            tag = release.get("tag_name") or release.get("name") or "?"
            items.append(
                Item(
                    id=f"ghrel:{repo}:{tag}",
                    title=f"{repo} {tag}",
                    url=release.get("html_url", ""),
                    source="github",
                    text=_clip(release.get("body") or ""),
                    date=(release.get("published_at") or "")[:10],
                )
            )
    return _partial(items, failures)


def hacker_news(settings: dict) -> list[Item]:
    items, failures = [], []
    limit = int(settings.get("limit", 15))
    for query in settings.get("queries", []):
        url = f"{HN_SEARCH}?query={quote_plus(query)}&tags=story&hitsPerPage={limit}"
        try:
            hits = _payload(url, "hn").get("hits", [])
        except Exception as error:
            failures.append(f"query {query}: {error}")
            continue
        for hit in hits:
            identifier = hit.get("objectID")
            if not identifier:
                continue
            items.append(
                Item(
                    id=f"hn:{identifier}",
                    title=hit.get("title") or "",
                    url=hit.get("url") or f"https://news.ycombinator.com/item?id={identifier}",
                    source="hn",
                    text=_clip(hit.get("story_text") or hit.get("title") or ""),
                    date=(hit.get("created_at") or "")[:10],
                )
            )
    return _partial(items, failures)


def reddit(settings: dict) -> list[Item]:
    """Runs the existing Node connector, which needs no Reddit key."""
    connector = settings.get("connector", "connectors/reddit-search.mjs")
    node = settings.get("node", "node")
    size = str(int(settings.get("size", 15)))
    items, failures = [], []

    for sub in settings.get("subreddits") or [None]:
        for query in settings.get("queries") or [""]:
            argv = [node, connector, "--json", "--size", size, "--sort", "created_utc"]
            if sub:
                argv += ["--sub", sub]
            if query:
                argv.append(query)
            try:
                done = subprocess.run(argv, capture_output=True, text=True, timeout=90)
                if done.returncode != 0:
                    raise RuntimeError(done.stderr.strip()[:120] or "connector failed")
                items.extend(_reddit_items(done.stdout))
            except Exception as error:
                # Pullpush is an archive and drops requests under load.
                failures.append(f"r/{sub} {query}: {error}")
    return _partial(items, failures)


def anthropic(settings: dict) -> list[Item]:
    body = get(CHANGELOG, TIMEOUT, "anthropic").body
    limit = int(settings.get("limit", 8))
    items = []
    for block in re.split(r"^##\s+", body, flags=re.M)[1:][:limit]:
        head, _, rest = block.partition("\n")
        version = head.strip()
        items.append(
            Item(
                id=f"changelog:{version}",
                title=f"claude-code {version}",
                url=CHANGELOG_URL,
                source="anthropic",
                text=_clip(rest),
            )
        )
    return items


SOURCES = {
    "github": github,
    "hacker_news": hacker_news,
    "reddit": reddit,
    "anthropic": anthropic,
}


def collect(config: dict, only=None) -> tuple[list[Item], list[tuple[str, str]]]:
    """Every enabled source. A source that breaks is reported, not fatal."""
    items, problems = [], []
    for name, fetch in SOURCES.items():
        settings = config.get(name)
        if not settings or settings.get("enabled") is False:
            continue
        if only and name not in only:
            continue
        try:
            items.extend(fetch(settings))
        except Exception as error:
            problems.append((name, f"{type(error).__name__}: {error}"))
    return _unique(items), problems


def _reddit_items(stdout: str) -> list[Item]:
    posts = json.loads(stdout or "[]")
    items = []
    for post in posts:
        identifier = post.get("id")
        if not identifier:
            continue
        permalink = post.get("permalink") or ""
        link = post.get("url") or ""
        if not link or "reddit.com" in link:
            link = f"https://reddit.com{permalink}"
        created = post.get("created_utc")
        date = (
            datetime.fromtimestamp(created, timezone.utc).date().isoformat() if created else ""
        )
        items.append(
            Item(
                id=f"reddit:{identifier}",
                title=post.get("title") or "",
                url=link,
                source=f"r/{post.get('subreddit', '?')}",
                text=_clip(post.get("selftext") or post.get("title") or ""),
                date=date,
            )
        )
    return items


def _partial(items: list[Item], failures: list[str]) -> list[Item]:
    """Keep what came back. Only a total loss is worth reporting as a failure."""
    if not items and failures:
        raise RuntimeError("; ".join(failures[:3]))
    return items


def _payload(url: str, who: str, headers: dict | None = None, expect=dict):
    response = get(url, TIMEOUT, who, headers)
    if not 200 <= response.status < 300:
        raise RuntimeError(f"HTTP {response.status} on {url.split('?')[0]}")
    payload = response.json()
    return payload if isinstance(payload, expect) else expect()


def _unique(items: list[Item]) -> list[Item]:
    seen, kept = set(), []
    for item in items:
        if item.id in seen:
            continue
        seen.add(item.id)
        kept.append(item)
    return kept


def _clip(text: str, limit: int = CLIP) -> str:
    flat = " ".join(str(text).split())
    return flat[:limit] + "..." if len(flat) > limit else flat
