"""One short call per item, sent to the cheapest provider through the pool."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..pool import WorkerPool
from ..router import Task
from .sources import Item

DEFAULT_AXES = {
    "job_search": "automating a job search: sourcing, CV tailoring, ATS autofill, application tracking",
    "agentic": "agentic tooling: subagents, skills, MCP servers, hooks, agent frameworks",
    "local_llm": "local LLMs and token economy: Ollama, quantised models, cheap routing, rate limits",
}

PROMPT = """Rate this item from 0 to 5 on each axis.

5 = I could adopt this as it stands
3 = adjacent, worth a look
1 = same field, nothing to take
0 = unrelated

Judge the name too, not only the description: repositories often ship with an
empty description while the name says what they are. A template, a guide, a
set of skills or a documented setup counts as a method.

Axes:
{axes}

Item:
source: {source}
title: {title}
text: {text}

Answer with one JSON object and nothing else:
{{{keys}, "why": "one short sentence"}}"""

MAX_TOKENS = 160
JSON_BLOCK = re.compile(r"\{.*\}", re.S)


@dataclass
class Scored:
    item: Item
    scores: dict = field(default_factory=dict)
    why: str = ""
    error: str = ""
    provider: str = ""

    @property
    def total(self) -> int:
        return sum(self.scores.values())

    @property
    def best(self) -> tuple[str, int]:
        if not self.scores:
            return ("", 0)
        return max(self.scores.items(), key=lambda pair: pair[1])


def score(router, items, workers: int = 4, axes: dict | None = None) -> list[Scored]:
    axes = axes or DEFAULT_AXES
    tasks = [Task.from_prompt(prompt_for(item, axes), max_tokens=MAX_TOKENS, temperature=0.0)
             for item in items]
    results = WorkerPool(router, workers).run(tasks)

    scored = []
    for item, result in zip(items, results):
        if not result.ok:
            scored.append(Scored(item, error=str(result.error)))
            continue
        values, why = parse(result.text, axes)
        scored.append(
            Scored(item, values, why, provider=result.completion.provider)
        )
    return scored


def prompt_for(item: Item, axes: dict) -> str:
    described = "\n".join(f"- {name}: {what}" for name, what in axes.items())
    keys = ", ".join(f'"{name}": 0' for name in axes)
    return PROMPT.format(
        axes=described,
        keys=keys,
        source=item.source,
        title=item.title,
        text=item.text or "(no description)",
    )


def parse(text: str, axes: dict) -> tuple[dict, str]:
    """Tolerant: models wrap JSON in prose, fences or explanations."""
    match = JSON_BLOCK.search(text or "")
    if not match:
        return ({name: 0 for name in axes}, "unparsed answer")
    try:
        payload = json.loads(match.group(0))
    except ValueError:
        return ({name: 0 for name in axes}, "unparsed answer")
    if not isinstance(payload, dict):
        return ({name: 0 for name in axes}, "unparsed answer")

    values = {name: _clamp(payload.get(name)) for name in axes}
    why = str(payload.get("why") or "").strip()
    return values, why


def _clamp(value) -> int:
    try:
        return max(0, min(5, int(float(value))))
    except (TypeError, ValueError):
        return 0
