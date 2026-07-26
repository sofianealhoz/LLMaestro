"""Command line entry point.

    python3 -m llmaestro "translate to French: hello"
    python3 -m llmaestro --check
    python3 -m llmaestro --batch prompts.txt --workers 4 --policy latency
    python3 -m llmaestro --image screenshot.png "what does this say?"

--check is the diagnostic to reach for first: it says which providers the
catalogue declares, which ones have their key, which are unreachable and how
much quota is left. --dry-run runs the whole path through a local fake provider,
so everything is exercisable with no key and no network.
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import POLICIES, DEFAULT_CATALOGUE, load_catalogue, load_env
from .errors import AllProvidersFailed
from .limits import Ledger
from .pool import WorkerPool
from .providers import build_all
from .providers.echo import Echo
from .router import Router, Task


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "watch":
        return _watch(argv[1:])

    args = _parse(argv)
    load_env(args.env)

    if args.dry_run:
        providers, skipped, ledger = [Echo()], [], None
    else:
        try:
            specs, skipped = load_catalogue(args.config)
        except (OSError, ValueError) as error:
            print(f"config error: {error}", file=sys.stderr)
            return 2
        providers = build_all(specs)
        ledger = None if args.no_ledger else Ledger()

    if args.check:
        return _check(providers, skipped, ledger, args)

    if not providers:
        print(
            "no usable provider: copy .env.example to .env and fill in one key, "
            "or run with --dry-run",
            file=sys.stderr,
        )
        return 2

    prompts = _prompts(args)
    if not prompts:
        print("nothing to do: give a prompt, --batch a file, or use --check", file=sys.stderr)
        return 2

    router = Router(providers, ledger=ledger, retries=args.retries)
    options = {
        "policy": args.policy,
        "require": tuple(args.require),
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "timeout": args.timeout,
    }

    try:
        if len(prompts) == 1:
            return _single(router, prompts[0], args, options)
        return _batch(router, prompts, args, options)
    finally:
        if ledger is not None:
            ledger.close()


def _single(router: Router, prompt: str, args, options) -> int:
    task = Task.from_prompt(prompt, images=tuple(args.image), **options)
    try:
        completion = router.complete(task)
    except AllProvidersFailed as failure:
        _report_failure(failure)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "text": completion.text,
                    "provider": completion.provider,
                    "model": completion.model,
                    "latency": round(completion.latency, 3),
                    "tokens": completion.tokens,
                },
                ensure_ascii=False,
            )
        )
    else:
        print(completion.text)
        print(
            f"\n[{completion.provider} {completion.model} "
            f"{completion.latency:.2f}s {completion.tokens} tokens]",
            file=sys.stderr,
        )
    return 0


def _batch(router: Router, prompts: list[str], args, options) -> int:
    tasks = [Task.from_prompt(prompt, **options) for prompt in prompts]
    results = WorkerPool(router, args.workers).run(tasks)
    failures = [result for result in results if not result.ok]

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "index": result.index,
                        "ok": result.ok,
                        "text": result.text,
                        "provider": result.completion.provider if result.completion else None,
                        "error": str(result.error) if result.error else None,
                    }
                    for result in results
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for result in results:
            head = f"{result.index + 1:>3}."
            if result.ok:
                print(f"{head} [{result.completion.provider}] {result.text}")
            else:
                print(f"{head} FAILED {result.error}")

    print(
        f"\n[{len(results) - len(failures)}/{len(results)} succeeded "
        f"on {args.workers} workers]",
        file=sys.stderr,
    )
    return 1 if failures else 0


def _watch(argv) -> int:
    from . import watch as watch_module

    args = _parse_watch(argv)
    load_env(args.env)

    router, ledger = None, None
    if not args.no_score:
        try:
            specs, _ = load_catalogue(args.config)
        except (OSError, ValueError) as error:
            print(f"config error: {error}", file=sys.stderr)
            return 2
        if not specs:
            print("no provider configured: run with --no-score to collect only", file=sys.stderr)
            return 2
        ledger = Ledger()
        router = Router(build_all(specs), ledger=ledger, retries=args.retries)

    try:
        config = watch_module.load_config(args.watch_config)
        report = watch_module.run(
            router,
            config=config,
            workers=args.workers,
            only=args.source or None,
            limit=args.limit,
            write=not args.no_write,
        )
    except (OSError, ValueError) as error:
        print(f"watch error: {error}", file=sys.stderr)
        return 2
    finally:
        if ledger is not None:
            ledger.close()

    for name, reason in report.problems:
        print(f"source {name} failed: {reason}", file=sys.stderr)
    print(
        f"[collected {report.collected}, new {report.fresh}, scored {report.scored}]",
        file=sys.stderr,
    )
    if report.path:
        print(report.path)
    else:
        print(report.markdown)
    return 0


def _check(providers, skipped, ledger, args) -> int:
    print(f"catalogue: {args.config}")
    if not providers:
        print("  no usable provider")
    usable = 0
    for provider in providers:
        spec = provider.spec
        marks = []
        if spec.vision:
            marks.append("vision")
        if spec.tools:
            marks.append("tools")

        state = "ready" if provider.available() else "unreachable"
        served = provider.models() if state == "ready" else None
        if served is not None and spec.model not in served:
            state = "wrong model"
        if state == "ready":
            usable += 1

        print(
            f"  {spec.name:<16} {state:<12} {spec.model:<34} "
            f"ctx {spec.context_window:<7} cost {spec.cost} "
            f"latency {spec.latency} quality {spec.quality}"
            + (f" [{', '.join(marks)}]" if marks else "")
        )
        if state == "wrong model":
            print(f"      not served. available: {', '.join(served) or 'none'}")
        if ledger is not None:
            for kind, values in ledger.snapshot(spec).items():
                if values["limit"] is not None:
                    print(f"      {kind}: {values['used']}/{values['limit']}")

    for name, reason in skipped:
        print(f"  {name:<16} skipped      {reason}")

    if ledger is not None:
        ledger.close()
    return 0 if usable else 1


def _prompts(args) -> list[str]:
    prompts = []
    if args.prompt:
        prompts.append(args.prompt)
    if args.batch:
        with open(args.batch, encoding="utf-8") as handle:
            prompts.extend(
                line.strip()
                for line in handle
                if line.strip() and not line.lstrip().startswith("#")
            )
    return prompts


def _report_failure(failure: AllProvidersFailed) -> None:
    print(str(failure), file=sys.stderr)
    for attempt in failure.attempts:
        prefix = "tried" if attempt.tried else "     "
        print(f"  {prefix} {attempt.provider}: {attempt.error}", file=sys.stderr)


def _parse_watch(argv):
    parser = argparse.ArgumentParser(
        prog="llmaestro watch",
        description="Collect what is new, score it, write a digest.",
    )
    parser.add_argument(
        "--watch-config", metavar="FILE", help="defaults to watch.toml, then watch.example.toml"
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="NAME",
        help="restrict to a source (github, hacker_news, reddit, anthropic)",
    )
    parser.add_argument("--limit", type=int, help="score at most this many new items")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--no-score", action="store_true", help="collect only, no provider needed")
    parser.add_argument(
        "--no-write", action="store_true", help="print the digest instead of writing out/"
    )
    parser.add_argument("--config", default=DEFAULT_CATALOGUE, help="provider catalogue")
    parser.add_argument("--env", default=".env")
    return parser.parse_args(argv)


def _parse(argv):
    parser = argparse.ArgumentParser(
        prog="llmaestro",
        description="Route a task to the provider that can do it cheapest.",
    )
    parser.add_argument("prompt", nargs="?", help="the prompt to send")
    parser.add_argument("--batch", metavar="FILE", help="one prompt per line, run through the pool")
    parser.add_argument("--workers", type=int, default=4, help="pool size for --batch")
    parser.add_argument("--policy", choices=POLICIES, default="cost", help="how to rank providers")
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="CAPABILITY",
        help="only providers with this capability (vision, tools)",
    )
    parser.add_argument(
        "--image", action="append", default=[], metavar="PATH", help="attach an image"
    )
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=1, help="retries per provider")
    parser.add_argument("--config", default=DEFAULT_CATALOGUE, help="provider catalogue")
    parser.add_argument("--env", default=".env", help="file holding the keys")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--check", action="store_true", help="report the configuration and quit")
    parser.add_argument(
        "--dry-run", action="store_true", help="use a local fake provider, no network"
    )
    parser.add_argument("--no-ledger", action="store_true", help="do not persist quota usage")
    return parser.parse_args(argv)
