import unittest

from helpers import FakeClock, spec

from llmaestro.limits import Ledger


class Windows(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.ledger = Ledger(":memory:", clock=self.clock)

    def tearDown(self):
        self.ledger.close()

    def test_requests_per_minute_block_then_free_up(self):
        provider = spec("groq", rpm=2)

        for _ in range(2):
            allowed, why = self.ledger.allows(provider)
            self.assertTrue(allowed, why)
            self.ledger.record(provider)

        allowed, why = self.ledger.allows(provider)
        self.assertFalse(allowed)
        self.assertIn("rpm", why)

        self.clock.advance(61)
        self.assertTrue(self.ledger.allows(provider)[0])

    def test_tokens_are_counted_not_just_calls(self):
        provider = spec("groq", tpm=1000)

        self.ledger.record(provider, tokens=900)

        self.assertTrue(self.ledger.allows(provider, tokens=50)[0])
        allowed, why = self.ledger.allows(provider, tokens=200)
        self.assertFalse(allowed)
        self.assertIn("tpm", why)

    def test_a_daily_cap_outlives_the_minute_window(self):
        provider = spec("openrouter", rpd=2)

        self.ledger.record(provider)
        self.ledger.record(provider)
        self.clock.advance(3600)

        allowed, why = self.ledger.allows(provider)
        self.assertFalse(allowed)
        self.assertIn("rpd", why)

    def test_usage_is_tracked_per_model(self):
        one = spec("groq", model="small", rpm=1)
        two = spec("groq", model="large", rpm=1)

        self.ledger.record(one)

        self.assertFalse(self.ledger.allows(one)[0])
        self.assertTrue(self.ledger.allows(two)[0])

    def test_a_provider_without_declared_limits_is_never_blocked(self):
        provider = spec("ollama")

        for _ in range(50):
            self.ledger.record(provider, tokens=10_000)

        self.assertTrue(self.ledger.allows(provider, tokens=10_000)[0])


class Learning(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.ledger = Ledger(":memory:", clock=self.clock)

    def tearDown(self):
        self.ledger.close()

    def test_a_refusal_tightens_an_optimistic_catalogue(self):
        provider = spec("cerebras", rpm=1000)
        for _ in range(3):
            self.ledger.record(provider)

        learned = self.ledger.learn_from_refusal(provider)

        self.assertEqual(3, learned["rpm"])
        allowed, why = self.ledger.allows(provider)
        self.assertFalse(allowed, "the learned ceiling now applies")
        self.assertIn("3", why)

    def test_learning_never_loosens_a_declared_limit(self):
        provider = spec("cerebras", rpm=2)
        for _ in range(5):
            self.ledger.record(provider)

        self.ledger.learn_from_refusal(provider)

        self.assertEqual(2, self.ledger.snapshot(provider)["rpm"]["limit"])

    def test_snapshot_reports_usage_against_the_effective_limit(self):
        provider = spec("groq", rpm=5, tpm=100)
        self.ledger.record(provider, tokens=30)

        report = self.ledger.snapshot(provider)

        self.assertEqual({"used": 1, "limit": 5}, report["rpm"])
        self.assertEqual({"used": 30, "limit": 100}, report["tpm"])
        self.assertIsNone(report["rpd"]["limit"])


if __name__ == "__main__":
    unittest.main()
