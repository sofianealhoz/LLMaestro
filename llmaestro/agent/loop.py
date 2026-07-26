"""Boucle bornée: modèle, outil, modèle. S'arrête sur finish ou sur budget."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..errors import AllProvidersFailed
from ..messages import assistant, system, tool_result, user
from ..router import Task
from .tools import Toolbox

SYSTEM = """Tu travailles dans une copie jetable d'un dépôt git. Tu ne peux rien supprimer.

Marche à suivre:
1. regarde les fichiers avant d'écrire, avec list_files, read_file et search
2. fais une modification à la fois, avec edit_file de préférence à write_file
3. vérifie ton travail avec run_command quand des tests existent
4. appelle finish avec un résumé quand la tâche est faite

Un outil qui répond "refusé" ne se retente pas à l'identique: change d'approche."""


@dataclass
class Budget:
    steps: int = 24
    tokens: int = 120_000
    files: int = 40
    bytes: int = 400_000
    seconds: float = 900.0


@dataclass
class Outcome:
    stopped: str = ""
    steps: int = 0
    tokens: int = 0
    summary: str = ""
    error: str = ""
    providers: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.stopped == "finished"


def drive(router, workspace, instruction: str, journal, budget: Budget | None = None) -> Outcome:
    budget = budget or Budget()
    toolbox = Toolbox(workspace)
    messages = [system(SYSTEM), user(instruction)]
    outcome = Outcome()
    started = time.monotonic()

    for step in range(1, budget.steps + 1):
        exceeded = _over_budget(budget, outcome, workspace, time.monotonic() - started)
        if exceeded:
            journal.write("budget_exceeded", limit=exceeded, step=step)
            outcome.stopped = f"budget: {exceeded}"
            return outcome

        journal.write("step_started", step=step)
        task = Task(
            messages=list(messages),
            tools=tuple(toolbox.schemas),
            max_tokens=1200,
            temperature=0.1,
            timeout=120.0,
        )
        try:
            completion = router.complete(task)
        except AllProvidersFailed as failure:
            journal.write("provider_failed", error=str(failure))
            outcome.stopped = "no provider"
            outcome.error = str(failure)
            return outcome

        journal.write(
            "provider_selected", provider=completion.provider, model=completion.model, step=step
        )
        outcome.steps = step
        outcome.tokens += completion.tokens
        if completion.provider not in outcome.providers:
            outcome.providers.append(completion.provider)

        if not completion.tool_calls:
            # Plus d'outil demandé: le modèle a répondu en clair, on considère le travail fini.
            outcome.stopped = "finished"
            outcome.summary = completion.text.strip()
            journal.write("run_finished", reason="answered", summary=outcome.summary[:400])
            return outcome

        messages.append(assistant(completion.text, completion.tool_calls))

        for call in completion.tool_calls:
            journal.write("tool_called", tool=call.name, arguments=call.arguments, step=step)
            if call.name in ("write_file", "edit_file"):
                workspace.snapshot(f"before {call.name} at step {step}")
            result = toolbox.call(call.name, call.arguments)
            journal.write(
                "tool_result", tool=call.name, content=result.content[:800], step=step
            )
            messages.append(tool_result(call, result.content))

            if result.finished:
                workspace.snapshot(f"step {step}: {call.name}")
                outcome.stopped = "finished"
                outcome.summary = toolbox.summary
                journal.write("run_finished", reason="finish", summary=outcome.summary[:400])
                return outcome

        workspace.snapshot(f"step {step}")

    outcome.stopped = "budget: steps"
    journal.write("budget_exceeded", limit="steps", step=budget.steps)
    return outcome


def _over_budget(budget: Budget, outcome: Outcome, workspace, elapsed: float) -> str:
    if outcome.tokens >= budget.tokens:
        return "tokens"
    if len(workspace.files_written) > budget.files:
        return "files"
    if workspace.bytes_written > budget.bytes:
        return "bytes"
    if elapsed >= budget.seconds:
        return "seconds"
    return ""
