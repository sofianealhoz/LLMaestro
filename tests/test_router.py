import unittest

from helpers import FakeClock, Scripted, Sleeps

from llmaestro.errors import (
    AllProvidersFailed,
    AuthError,
    ContextTooLarge,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
)
from llmaestro.limits import Ledger
from llmaestro.messages import user
from llmaestro.router import Router, Task


def router(providers, **options):
    options.setdefault("clock", FakeClock())
    options.setdefault("sleep", Sleeps())
    return Router(providers, **options)


def prompt(text="hello", **options):
    return Task(messages=[user(text)], **options)


class RateLimitFallback(unittest.TestCase):
    def test_moves_to_the_next_provider_and_cools_the_first(self):
        first = Scripted("first", [RateLimited("first", "slow down", retry_after=120)], cost=1)
        second = Scripted("second", ["done"], cost=2)
        clock = FakeClock()
        subject = router([first, second], clock=clock)

        completion = subject.complete(prompt())

        self.assertEqual("done", completion.text)
        self.assertEqual("second", completion.provider)
        self.assertEqual(1, first.calls, "a long Retry-After should not be waited out")
        cooling = {row["name"]: row["cooling_for"] for row in subject.describe()}
        self.assertGreater(cooling["first"], 0)
        self.assertEqual(0, cooling["second"])

    def test_a_short_retry_after_is_waited_out_on_the_same_provider(self):
        only = Scripted("only", [RateLimited("only", "slow down", retry_after=1.0), "done"])
        sleeps = Sleeps()
        subject = router([only], sleep=sleeps)

        self.assertEqual("done", subject.complete(prompt()).text)
        self.assertEqual(2, only.calls)
        self.assertEqual([1.0], sleeps.waits)


class Retries(unittest.TestCase):
    def test_a_timeout_is_retried_on_the_same_provider(self):
        only = Scripted("only", [ProviderTimeout("only", "no response"), "done"])
        sleeps = Sleeps()

        self.assertEqual("done", router([only], sleep=sleeps).complete(prompt()).text)
        self.assertEqual(2, only.calls)
        self.assertEqual(1, len(sleeps.waits), "backoff should have been applied once")

    def test_context_too_large_is_not_retried_but_falls_through(self):
        small = Scripted("small", [ContextTooLarge("small", "context length exceeded")], cost=1)
        large = Scripted("large", ["done"], cost=2)
        subject = router([small, large])

        self.assertEqual("done", subject.complete(prompt()).text)
        self.assertEqual(1, small.calls, "retrying the same prompt cannot help")
        self.assertEqual(0, subject.describe()[0]["cooling_for"], "the provider is not at fault")


class Cooldown(unittest.TestCase):
    def test_bad_credentials_take_a_provider_out_for_an_hour(self):
        broken = Scripted("broken", [AuthError("broken", "invalid key")], cost=1)
        healthy = Scripted("healthy", ["done"], cost=2)
        clock = FakeClock()
        subject = router([broken, healthy], clock=clock)

        subject.complete(prompt())
        self.assertEqual(1, broken.calls)

        clock.advance(600)
        subject.complete(prompt())
        self.assertEqual(1, broken.calls, "still cooling ten minutes later")

        clock.advance(3600)
        broken.script = ["recovered"]
        self.assertEqual("recovered", subject.complete(prompt()).text)

    def test_a_skipped_provider_explains_itself_in_the_attempt_log(self):
        broken = Scripted("broken", [AuthError("broken", "invalid key")])
        subject = router([broken])

        with self.assertRaises(AllProvidersFailed):
            subject.complete(prompt())
        with self.assertRaises(AllProvidersFailed) as caught:
            subject.complete(prompt())

        skipped = [a for a in caught.exception.attempts if not a.tried]
        self.assertEqual(1, len(skipped))
        self.assertIn("cooling down", skipped[0].error)


class Eligibility(unittest.TestCase):
    def test_an_image_restricts_routing_to_vision_providers(self):
        blind = Scripted("blind", ["wrong"], vision=False, cost=1)
        seeing = Scripted("seeing", ["right"], vision=True, cost=9)
        subject = router([blind, seeing])

        completion = subject.complete(Task(messages=[user("what is this", ["a.png"])]))

        self.assertEqual("right", completion.text)
        self.assertEqual(0, blind.calls)

    def test_a_required_capability_is_enforced(self):
        plain = Scripted("plain", ["wrong"], tools=False, cost=1)
        capable = Scripted("capable", ["right"], tools=True, cost=9)

        completion = router([plain, capable]).complete(prompt(require=("tools",)))

        self.assertEqual("right", completion.text)
        self.assertEqual(0, plain.calls)

    def test_a_prompt_larger_than_the_context_window_skips_the_provider(self):
        small = Scripted("small", ["wrong"], context_window=100, cost=1)
        big = Scripted("big", ["right"], context_window=100000, cost=9)

        completion = router([small, big]).complete(prompt("x" * 40000, max_tokens=16))

        self.assertEqual("right", completion.text)
        self.assertEqual(0, small.calls)

    def test_a_local_provider_that_is_not_listening_costs_no_attempt(self):
        class Down(Scripted):
            probe_before_use = True

            def available(self):
                return False

        down = Down("local", ["never"], cost=1)
        cloud = Scripted("cloud", ["done"], cost=9)
        subject = router([down, cloud])

        completion = subject.complete(prompt())

        self.assertEqual("done", completion.text)
        self.assertEqual(0, down.calls)

    def test_a_refused_call_still_counts_against_the_quota(self):
        # Otherwise a burst of 429s teaches the ledger a ceiling far below the
        # real one, and the provider stays crippled afterwards.
        clock = FakeClock()
        ledger = Ledger(":memory:", clock=clock)
        flaky = Scripted("flaky", [RateLimited("flaky", "slow down", retry_after=90)], cost=1)
        backup = Scripted("backup", ["done"], cost=2)
        subject = router([flaky, backup], ledger=ledger, retries=0)

        subject.complete(prompt())

        self.assertEqual(1, ledger.snapshot(flaky.spec)["rpm"]["used"])
        ledger.close()

    def test_with_patience_the_router_waits_out_the_window(self):
        clock = FakeClock()
        ledger = Ledger(":memory:", clock=clock)
        sleeps = Sleeps()
        only = Scripted("only", ["first", "second"], rpm=1)
        # The ledger runs on its own clock, so waiting has to move it too.
        subject = router([only], ledger=ledger, patience=120, sleep=lambda s: (
            sleeps(s), clock.advance(s)
        ))

        self.assertEqual("first", subject.complete(prompt()).text)
        self.assertEqual("second", subject.complete(prompt()).text)
        self.assertGreater(sleeps.total, 55, "it should have sat out the minute window")
        ledger.close()

    def test_without_patience_an_exhausted_quota_fails_immediately(self):
        clock = FakeClock()
        ledger = Ledger(":memory:", clock=clock)
        sleeps = Sleeps()
        only = Scripted("only", ["first", "second"], rpm=1)
        subject = router([only], ledger=ledger, sleep=sleeps)

        subject.complete(prompt())
        with self.assertRaises(AllProvidersFailed):
            subject.complete(prompt())
        self.assertEqual([], sleeps.waits)
        ledger.close()

    def test_an_exhausted_quota_removes_a_provider_before_the_call(self):
        clock = FakeClock()
        ledger = Ledger(":memory:", clock=clock)
        thrifty = Scripted("thrifty", ["first", "second"], cost=1, rpm=1)
        backup = Scripted("backup", ["backup"], cost=2)
        subject = router([thrifty, backup], ledger=ledger)

        self.assertEqual("first", subject.complete(prompt()).text)
        self.assertEqual("backup", subject.complete(prompt()).text)
        self.assertEqual(1, thrifty.calls, "its minute quota was already spent")

        clock.advance(61)
        self.assertEqual("second", subject.complete(prompt()).text)
        ledger.close()


class Policies(unittest.TestCase):
    def setUp(self):
        self.cheap = Scripted("cheap", ["cheap"], cost=1, latency=3, quality=5)
        self.fast = Scripted("fast", ["fast"], cost=3, latency=1, quality=4)
        self.good = Scripted("good", ["good"], cost=5, latency=5, quality=1)
        self.providers = [self.good, self.cheap, self.fast]

    def test_cost_is_the_default(self):
        self.assertEqual("cheap", router(self.providers).complete(prompt()).text)

    def test_latency_and_quality_pick_differently(self):
        self.assertEqual("fast", router(self.providers).complete(prompt(policy="latency")).text)
        self.assertEqual("good", router(self.providers).complete(prompt(policy="quality")).text)

    def test_reliable_prefers_whatever_has_not_been_failing(self):
        flaky = Scripted("flaky", [ProviderUnavailable("flaky", "boom")], cost=1, quality=1)
        steady = Scripted("steady", ["steady"], cost=9, quality=2)
        subject = router([flaky, steady], retries=0)

        subject.complete(prompt())
        flaky.script = ["flaky"]
        # Cost would still choose flaky; reliable remembers it just failed.
        self.assertEqual("steady", subject.complete(prompt(policy="reliable")).text)


class TotalFailure(unittest.TestCase):
    def test_the_error_carries_the_whole_sequence(self):
        first = Scripted("first", [ProviderUnavailable("first", "boom")], cost=1)
        second = Scripted("second", [RateLimited("second", "slow down", retry_after=99)], cost=2)
        subject = router([first, second], retries=0)

        with self.assertRaises(AllProvidersFailed) as caught:
            subject.complete(prompt())

        message = str(caught.exception)
        self.assertIn("first", message)
        self.assertIn("second", message)
        tried = [a.provider for a in caught.exception.attempts if a.tried]
        self.assertEqual(["first", "second"], tried)

    def test_a_router_needs_a_provider(self):
        with self.assertRaises(ValueError):
            Router([])


if __name__ == "__main__":
    unittest.main()
