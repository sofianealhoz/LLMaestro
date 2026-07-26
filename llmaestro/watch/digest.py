"""Markdown digest, ranked by score."""

from __future__ import annotations

from datetime import date


def render(scored, problems=(), when: str | None = None, floor: int = 0) -> str:
    when = when or date.today().isoformat()
    ranked = sorted(scored, key=lambda s: (-s.total, s.item.source, s.item.title))
    kept = [s for s in ranked if s.total >= floor and not s.error]
    dropped = [s for s in ranked if s.total < floor and not s.error]
    failed = [s for s in ranked if s.error]

    lines = [f"# Watch {when}", ""]
    lines.append(f"{len(kept)} items above the floor, {len(dropped)} below, {len(failed)} unscored.")
    lines.append("")

    for entry in kept:
        lines.extend(_entry(entry))

    if dropped:
        lines.append("## Below the floor")
        lines.append("")
        for entry in dropped:
            lines.append(f"- [{entry.item.title}]({entry.item.url}) ({entry.total})")
        lines.append("")

    if failed:
        lines.append("## Not scored")
        lines.append("")
        for entry in failed:
            lines.append(f"- [{entry.item.title}]({entry.item.url}): {entry.error}")
        lines.append("")

    if problems:
        lines.append("## Sources that failed")
        lines.append("")
        for name, reason in problems:
            lines.append(f"- {name}: {reason}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _entry(entry) -> list[str]:
    item = entry.item
    scores = " ".join(f"{name} {value}" for name, value in entry.scores.items())
    head = f"## {item.title}"
    meta = f"{item.source}" + (f", {item.date}" if item.date else "")
    return [
        head,
        "",
        f"{item.url}",
        "",
        f"total {entry.total} ({scores}) | {meta}",
        "",
        entry.why or "no comment",
        "",
    ]
