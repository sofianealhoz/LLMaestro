"""Worker pool: a queue drained by N threads, all sharing one router.

Threads and not asyncio: the work is network waiting, and a shared router under
a lock keeps cooldowns visible to every worker at once.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass

from .providers.base import Completion
from .router import Router, Task

DEFAULT_WORKERS = 4


@dataclass
class Result:
    index: int
    completion: Completion | None = None
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def text(self) -> str:
        return self.completion.text if self.completion else ""


class WorkerPool:
    def __init__(self, router: Router, workers: int = DEFAULT_WORKERS):
        self.router = router
        self.workers = max(1, int(workers))

    def run(self, tasks) -> list[Result]:
        """Every task, results in input order. A failed task carries its error instead of killing the batch."""
        tasks = list(tasks)
        if not tasks:
            return []

        pending: queue.Queue = queue.Queue()
        for index, task in enumerate(tasks):
            pending.put((index, task))

        results: list[Result | None] = [None] * len(tasks)
        threads = [
            threading.Thread(
                target=self._consume,
                args=(pending, results),
                name=f"llmaestro-worker-{number}",
                daemon=True,
            )
            for number in range(min(self.workers, len(tasks)))
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return [result for result in results if result is not None]

    def _consume(self, pending: queue.Queue, results: list) -> None:
        while True:
            try:
                index, task = pending.get_nowait()
            except queue.Empty:
                return
            try:
                results[index] = Result(index, completion=self.router.complete(task))
            except Exception as error:  # a failed task is a result, not a crash
                results[index] = Result(index, error=error)
            finally:
                pending.task_done()


def run_prompts(router: Router, prompts, workers: int = DEFAULT_WORKERS, **options) -> list[Result]:
    """Convenience wrapper: a list of prompts through the pool."""
    tasks = [Task.from_prompt(prompt, **options) for prompt in prompts]
    return WorkerPool(router, workers).run(tasks)
