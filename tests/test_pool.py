import threading
import time
import unittest

from helpers import FakeClock, Scripted, Sleeps, spec

from llmaestro.errors import AuthError, ProviderError
from llmaestro.messages import user
from llmaestro.pool import WorkerPool, run_prompts
from llmaestro.providers.base import Completion, Provider
from llmaestro.router import Router, Task


class Counting(Provider):
    """Records how many workers are inside complete() at the same time."""

    def __init__(self, name="counting", fail_on=(), delay=0.0):
        super().__init__(spec(name))
        self.fail_on = set(fail_on)
        self.delay = delay
        self.calls = 0
        self.peak = 0
        self._inside = 0
        self._lock = threading.Lock()

    def complete(self, messages, *, max_tokens=512, temperature=0.2, timeout=30.0):
        with self._lock:
            self.calls += 1
            self._inside += 1
            self.peak = max(self.peak, self._inside)
        try:
            if self.delay:
                time.sleep(self.delay)  # stands in for network wait
            prompt = messages[-1].text
            if prompt in self.fail_on:
                raise ProviderError(self.name, f"refusing {prompt}")
            return Completion(f"answer to {prompt}", self.name, self.spec.model, 0.0)
        finally:
            with self._lock:
                self._inside -= 1


def router(providers, **options):
    options.setdefault("clock", FakeClock())
    options.setdefault("sleep", Sleeps())
    options.setdefault("retries", 0)
    return Router(providers, **options)


class Ordering(unittest.TestCase):
    def test_results_come_back_in_input_order(self):
        provider = Counting()
        tasks = [Task(messages=[user(f"task {n}")]) for n in range(20)]

        results = WorkerPool(router([provider]), workers=5).run(tasks)

        self.assertEqual(list(range(20)), [result.index for result in results])
        self.assertEqual(
            [f"answer to task {n}" for n in range(20)], [result.text for result in results]
        )
        self.assertEqual(20, provider.calls)

    def test_an_empty_batch_does_nothing(self):
        self.assertEqual([], WorkerPool(router([Counting()])).run([]))

    def test_workers_actually_overlap(self):
        # Each call blocks like a network round trip, so a sequential pool
        # would never show more than one caller inside at a time.
        provider = Counting(delay=0.02)
        tasks = [Task(messages=[user(str(n))]) for n in range(16)]

        WorkerPool(router([provider]), workers=8).run(tasks)

        self.assertGreater(provider.peak, 1, "the pool ran everything sequentially")

    def test_more_workers_than_tasks_is_harmless(self):
        results = run_prompts(router([Counting()]), ["only one"], workers=16)

        self.assertEqual(1, len(results))
        self.assertTrue(results[0].ok)


class Isolation(unittest.TestCase):
    def test_one_failing_task_does_not_take_the_batch_down(self):
        provider = Counting(fail_on={"poison"})
        prompts = ["fine", "poison", "also fine"]

        results = run_prompts(router([provider]), prompts, workers=3)

        self.assertEqual([True, False, True], [result.ok for result in results])
        self.assertIn("refusing poison", str(results[1].error))
        self.assertEqual("", results[1].text)


class SharedState(unittest.TestCase):
    def test_a_cooldown_earned_by_one_worker_applies_to_all(self):
        broken = Scripted("broken", [AuthError("broken", "invalid key")], cost=1)
        healthy = Counting("healthy")
        subject = router([broken, healthy])
        prompts = [f"task {n}" for n in range(24)]

        results = run_prompts(subject, prompts, workers=6)

        self.assertTrue(all(result.ok for result in results))
        self.assertLessEqual(
            broken.calls, 6, "once cooling, the broken provider should stop being chosen"
        )
        self.assertGreaterEqual(healthy.calls, 18)
        cooling = {row["name"]: row["cooling_for"] for row in subject.describe()}
        self.assertGreater(cooling["broken"], 0)


if __name__ == "__main__":
    unittest.main()
