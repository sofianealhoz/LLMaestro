import json
import unittest
from unittest import mock

from helpers import FakeClock, Scripted, Sleeps

from llmaestro.router import Router
from llmaestro.transport import Response
from llmaestro.watch import digest, scoring, sources
from llmaestro.watch.sources import Item
from llmaestro.watch.store import Seen

GITHUB_SEARCH = {
    "items": [
        {
            "full_name": "someone/agent-recipes",
            "html_url": "https://github.com/someone/agent-recipes",
            "description": "Reusable agent workflows",
            "stargazers_count": 120,
            "topics": ["claude-code", "mcp"],
            "pushed_at": "2026-07-25T10:00:00Z",
        }
    ]
}

GITHUB_RELEASES = [
    {
        "tag_name": "v2.1.220",
        "html_url": "https://github.com/anthropics/claude-code/releases/tag/v2.1.220",
        "body": "Bug fixes",
        "published_at": "2026-07-24T08:00:00Z",
    }
]

HN = {
    "hits": [
        {
            "objectID": "49056022",
            "title": "How I run three coding agents at once",
            "url": "https://example.com/post",
            "created_at": "2026-07-26T08:45:06Z",
            "story_text": "",
        },
        {"objectID": "1", "title": "No url story", "url": None, "created_at": "2026-07-26T07:00:00Z"},
    ]
}

REDDIT = [
    {
        "id": "abc123",
        "title": "My Claude Code setup",
        "selftext": "Here is the workflow I use every day",
        "permalink": "/r/ClaudeAI/comments/abc123/my_setup/",
        "url": "https://reddit.com/r/ClaudeAI/comments/abc123/my_setup/",
        "subreddit": "ClaudeAI",
        "created_utc": 1784000000,
    }
]

CHANGELOG = """# Changelog

## 2.1.220

- Bug fixes

## 2.1.219

- Added a hook
"""


def response(payload, status=200):
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return Response(status, body)


class GitHub(unittest.TestCase):
    def test_repositories_and_releases_become_items(self):
        answers = [response(GITHUB_SEARCH), response(GITHUB_RELEASES)]
        with mock.patch("llmaestro.watch.sources.get", side_effect=answers) as sent:
            items = sources.github(
                {"topics": ["claude-code"], "repos": ["anthropics/claude-code"]}
            )

        self.assertEqual(
            ["github:someone/agent-recipes", "ghrel:anthropics/claude-code:v2.1.220"],
            [item.id for item in items],
        )
        self.assertIn("120 stars", items[0].text)
        self.assertEqual("2026-07-25", items[0].date)
        self.assertIn("stars%3A%3E%3D", sent.call_args_list[0].args[0])

    def test_one_broken_topic_does_not_lose_the_others(self):
        answers = [RuntimeError("rate limited"), response(GITHUB_SEARCH)]
        with mock.patch("llmaestro.watch.sources.get", side_effect=answers):
            items = sources.github({"topics": ["dead", "alive"]})

        self.assertEqual(1, len(items))

    def test_a_source_that_returns_nothing_at_all_is_a_failure(self):
        with mock.patch("llmaestro.watch.sources.get", side_effect=RuntimeError("down")):
            with self.assertRaises(RuntimeError):
                sources.github({"topics": ["dead"]})


class HackerNews(unittest.TestCase):
    def test_a_story_without_a_url_falls_back_to_the_discussion(self):
        with mock.patch("llmaestro.watch.sources.get", return_value=response(HN)):
            items = sources.hacker_news({"queries": ["agent setup"]})

        self.assertEqual("https://example.com/post", items[0].url)
        self.assertEqual("https://news.ycombinator.com/item?id=1", items[1].url)


class Reddit(unittest.TestCase):
    def test_the_node_connector_output_is_parsed(self):
        done = mock.Mock(returncode=0, stdout=json.dumps(REDDIT), stderr="")
        with mock.patch("llmaestro.watch.sources.subprocess.run", return_value=done):
            items = sources.reddit({"subreddits": ["ClaudeAI"], "queries": ["workflow"]})

        self.assertEqual("reddit:abc123", items[0].id)
        self.assertEqual("r/ClaudeAI", items[0].source)
        self.assertTrue(items[0].url.startswith("https://reddit.com/r/ClaudeAI"))

    def test_a_failing_call_is_tolerated_when_another_works(self):
        ok = mock.Mock(returncode=0, stdout=json.dumps(REDDIT), stderr="")
        ko = mock.Mock(returncode=3, stdout="", stderr="fetch failed")
        with mock.patch("llmaestro.watch.sources.subprocess.run", side_effect=[ko, ok]):
            items = sources.reddit({"subreddits": ["a", "b"], "queries": ["workflow"]})

        self.assertEqual(1, len(items))


class Changelog(unittest.TestCase):
    def test_versions_become_items(self):
        with mock.patch("llmaestro.watch.sources.get", return_value=response(CHANGELOG)):
            items = sources.anthropic({"limit": 5})

        self.assertEqual(["changelog:2.1.220", "changelog:2.1.219"], [i.id for i in items])
        self.assertIn("Bug fixes", items[0].text)


class Dedup(unittest.TestCase):
    def test_an_item_never_comes_back(self):
        store = Seen(":memory:")
        items = [Item("a", "A", "", "github"), Item("b", "B", "", "github")]

        self.assertEqual(2, len(store.unseen(items)))
        store.remember(items[:1])

        self.assertEqual(["b"], [i.id for i in store.unseen(items)])
        self.assertEqual(1, store.count())
        store.close()

    def test_collect_drops_duplicates_inside_one_run(self):
        with mock.patch(
            "llmaestro.watch.sources.get",
            side_effect=[response(GITHUB_SEARCH), response(GITHUB_SEARCH)],
        ):
            items, problems = sources.collect(
                {"github": {"topics": ["one", "two"]}}, only=["github"]
            )

        self.assertEqual(1, len(items))
        self.assertEqual([], problems)


class Scoring(unittest.TestCase):
    axes = {"workflow": "a method", "cost": "cheaper"}

    def test_a_clean_answer_is_read(self):
        values, why = scoring.parse('{"workflow": 5, "cost": 2, "why": "a template"}', self.axes)

        self.assertEqual({"workflow": 5, "cost": 2}, values)
        self.assertEqual("a template", why)

    def test_json_wrapped_in_prose_or_fences_still_parses(self):
        answer = 'Sure!\n```json\n{"workflow": 3, "cost": 1, "why": "ok"}\n```\n'

        self.assertEqual({"workflow": 3, "cost": 1}, scoring.parse(answer, self.axes)[0])

    def test_nonsense_scores_zero_instead_of_crashing(self):
        values, why = scoring.parse("I cannot rate this", self.axes)

        self.assertEqual({"workflow": 0, "cost": 0}, values)
        self.assertEqual("unparsed answer", why)

    def test_out_of_range_values_are_clamped(self):
        values, _ = scoring.parse('{"workflow": 99, "cost": -4}', self.axes)

        self.assertEqual({"workflow": 5, "cost": 0}, values)

    def test_scoring_runs_through_the_pool_and_keeps_order(self):
        provider = Scripted("fake", ['{"workflow": 4, "cost": 1, "why": "good"}'])
        router = Router([provider], clock=FakeClock(), sleep=Sleeps())
        items = [Item(str(n), f"item {n}", "", "github") for n in range(5)]

        scored = scoring.score(router, items, workers=3, axes=self.axes)

        self.assertEqual([i.id for i in items], [s.item.id for s in scored])
        self.assertEqual(5, scored[0].total)
        self.assertEqual("fake", scored[0].provider)

    def test_a_failed_call_becomes_an_unscored_entry(self):
        from llmaestro.errors import ProviderError

        provider = Scripted("fake", [ProviderError("fake", "nope")])
        router = Router([provider], retries=0, clock=FakeClock(), sleep=Sleeps())

        scored = scoring.score(router, [Item("a", "A", "", "github")], axes=self.axes)

        self.assertTrue(scored[0].error)
        self.assertEqual(0, scored[0].total)


class Digest(unittest.TestCase):
    def test_entries_are_ranked_and_split_by_the_floor(self):
        high = scoring.Scored(Item("a", "High", "http://a", "github"), {"workflow": 5}, "useful")
        low = scoring.Scored(Item("b", "Low", "http://b", "hn"), {"workflow": 1}, "meh")

        text = digest.render([low, high], floor=3, when="2026-07-26")

        self.assertIn("# Watch 2026-07-26", text)
        self.assertLess(text.index("## High"), text.index("## Below the floor"))
        self.assertIn("[Low](http://b) (1)", text)
        self.assertIn("total 5 (workflow 5)", text)

    def test_failures_are_reported_not_hidden(self):
        broken = scoring.Scored(Item("c", "Broken", "http://c", "hn"), error="all providers failed")

        text = digest.render([broken], problems=[("reddit", "fetch failed")], when="2026-07-26")

        self.assertIn("## Not scored", text)
        self.assertIn("## Sources that failed", text)
        self.assertIn("reddit: fetch failed", text)


if __name__ == "__main__":
    unittest.main()
