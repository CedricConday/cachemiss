"""Plain-text renderers. No colours, no dependencies; reads well in a screenshot."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .analysis import AVOIDABLE, FIXES, Summary, describe


def _k(n: int) -> str:
    return f"{n/1000:.0f}k" if n >= 1000 else str(n)


def _money(x: float) -> str:
    return f"${x:.2f}"


def _short(session_id: str) -> str:
    return session_id[:8]


def _gap(g: timedelta | None) -> str:
    if g is None:
        return "-"
    s = int(g.total_seconds())
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s//60}m"
    return f"{s//3600}h{(s%3600)//60:02d}"


def _local(ts: datetime) -> str:
    return ts.astimezone().strftime("%m-%d %H:%M")


def header(summary: Summary, since: datetime | None, label: str) -> str:
    lines = [f"cachemiss  window: {label}   calls: {summary.calls}   sessions: {summary.sessions}"]
    lines.append(
        f"prompt tokens: {_k(summary.prompt_tokens)}   cache hit rate: {summary.hit_rate:.0%}   "
        f"written: {_k(summary.cache_creation)}   uncached input: {_k(summary.input_tokens)}   output: {_k(summary.output_tokens)}"
    )
    n = len(summary.rebuilds)
    lines.append(
        f"rebuilds: {n}   tokens rewritten that were already cached: {_k(summary.rebuilt_tokens)}   "
        f"paid as writes: {_money(sum(r.write_cost for r in summary.rebuilds))}   "
        f"as reads they would have cost: {_money(sum(r.read_cost for r in summary.rebuilds))}   "
        f"rebuild premium: {_money(summary.premium)}"
    )
    lines.append("Dollar figures are API list prices for the same tokens; a subscription quota is not billed in dollars, but it drains in the same proportion.")
    if summary.diagnostics_present:
        lines.append(f"API diagnostics present on {summary.diagnostics_present} calls; other reasons are inferred.")
    else:
        lines.append("No API cache diagnostics in these transcripts; every reason below is inferred from the record.")
    return "\n".join(lines)


def ledger(summary: Summary, limit: int = 40) -> str:
    rows = sorted(summary.rebuilds, key=lambda r: -r.premium)[:limit]
    if not rows:
        return "No rebuilds in this window."
    out = [f"{'when':11} {'session':8} {'agent':18} {'reason':36} {'gap':7} {'rewrote':>8} {'premium':>8}"]
    for r in rows:
        agent = (r.call.agent_name or ("sub-agent" if r.call.is_subagent else "main"))[:18]
        out.append(
            f"{_local(r.call.ts):11} {_short(r.call.session_id):8} {agent:18} {r.label:36} {_gap(r.gap):7} "
            f"{_k(r.rebuilt_tokens):>8} {_money(r.premium):>8}"
        )
    if len(summary.rebuilds) > limit:
        out.append(f"... {len(summary.rebuilds) - limit} more; use --limit or --json")
    out.append("* = reason inferred from the record (no API diagnostics on that call)")
    return "\n".join(out)


def rollups(summary: Summary) -> str:
    blocks = []
    for title, key in (
        ("by reason", lambda r: r.label),
        ("by agent", lambda r: r.call.agent_name or ("sub-agent" if r.call.is_subagent else "main")),
        ("by session", lambda r: f"{_short(r.call.session_id)} {r.call.slug or ''}".strip()),
        ("by model", lambda r: r.call.model or "?"),
    ):
        table = summary.by(key)
        if not table:
            continue
        lines = [f"{title:38} {'n':>4} {'rewrote':>9} {'premium':>8}"]
        for k, v in list(table.items())[:12]:
            lines.append(f"{str(k)[:38]:38} {v['count']:>4} {_k(v['tokens']):>9} {_money(v['premium']):>8}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def why(summary: Summary) -> str:
    table = summary.by(lambda r: r.reason)
    if not table:
        return "No rebuilds in this window, nothing to explain."
    out = []
    for reason, v in table.items():
        inferred = any(r.inferred for r in summary.rebuilds if r.reason == reason)
        tag = " (inferred)" if inferred else " (API diagnostics)"
        kind = "avoidable" if reason in AVOIDABLE else "expected"
        out.append(f"{reason}{tag}: {v['count']} rebuilds, {_k(v['tokens'])} tokens rewritten, {_money(v['premium'])} premium, {kind}")
        out.append(f"  what it means: {describe(reason)}")
        out.append(f"  what to do:    {FIXES.get(reason, 'No general fix.')}")
        out.append("")
    return "\n".join(out).rstrip()


def timeline(calls, summary: Summary) -> str:
    """Per-call view of one session: prompt size, hit share, and rebuild markers."""
    marks = {id(r.call): r for r in summary.rebuilds}
    out = [f"{'when':11} {'agent':14} {'prompt':>8} {'read':>8} {'wrote':>8} {'hit':>5}  note"]
    for c in calls:
        hit = c.cache_read / c.prompt_tokens if c.prompt_tokens else 0
        agent = (c.agent_name or ("sub-agent" if c.is_subagent else "main"))[:14]
        r = marks.get(id(c))
        note = f"REBUILD {r.label} {_k(r.rebuilt_tokens)} tokens, gap {_gap(r.gap)}" if r else ""
        if c.quota:
            note += f" quota:{c.quota.get('status')}"
        out.append(f"{_local(c.ts):11} {agent:14} {_k(c.prompt_tokens):>8} {_k(c.cache_read):>8} {_k(c.cache_creation):>8} {hit:>5.0%}  {note}")
    return "\n".join(out)


def to_json(summary: Summary, since: datetime | None) -> dict:
    return {
        "window_start": since.isoformat() if since else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "calls": summary.calls,
        "sessions": summary.sessions,
        "prompt_tokens": summary.prompt_tokens,
        "cache_read": summary.cache_read,
        "cache_creation": summary.cache_creation,
        "input_tokens": summary.input_tokens,
        "output_tokens": summary.output_tokens,
        "hit_rate": summary.hit_rate,
        "diagnostics_present": summary.diagnostics_present,
        "rebuilds": [
            {
                "ts": r.call.ts.isoformat(),
                "session_id": r.call.session_id,
                "slug": r.call.slug,
                "project": r.call.project,
                "agent": r.call.agent_name,
                "agent_id": r.call.agent_id,
                "is_subagent": r.call.is_subagent,
                "model": r.call.model,
                "reason": r.reason,
                "inferred": r.inferred,
                "gap_seconds": r.gap.total_seconds() if r.gap else None,
                "rebuilt_tokens": r.rebuilt_tokens,
                "prompt_tokens": r.call.prompt_tokens,
                "share_written": r.share,
                "ttl": r.call.ttl,
                "write_cost": r.write_cost,
                "read_cost": r.read_cost,
                "label": r.label,
                "premium": r.premium,
                "avoidable": r.reason in AVOIDABLE,
                "request_id": r.call.request_id,
            }
            for r in summary.rebuilds
        ],
    }
