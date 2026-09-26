"""cachemiss: why did my Claude Code quota drain?"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .analysis import TTL, detect
from .report import header, ledger, rollups, timeline, to_json, why
from .transcripts import DEFAULT_ROOT, load_calls

_UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_since(text: str) -> datetime | None:
    if text in ("all", "0"):
        return None
    m = re.fullmatch(r"(\d+)([mhdw])", text)
    if not m:
        raise SystemExit(f"--since takes e.g. 5h, 24h, 7d or all, not {text!r}")
    return datetime.now(timezone.utc) - timedelta(seconds=int(m.group(1)) * _UNITS[m.group(2)])


def _load(args) -> tuple[list, datetime | None]:
    since = parse_since(args.since)
    # Load a margin before the window so the first in-window call has its predecessor.
    margin = since - max(TTL.values()) - timedelta(minutes=5) if since else None
    calls = load_calls(Path(args.root), margin)
    return calls, since


def _window(calls, since):
    return [c for c in calls if since is None or c.ts >= since]


def cmd_ledger(args) -> int:
    calls, since = _load(args)
    summary = detect(calls)
    if since:
        summary.rebuilds = [r for r in summary.rebuilds if r.call.ts >= since]
        inwin = _window(calls, since)
        summary.calls = len(inwin)
        summary.sessions = len({c.session_id for c in inwin})
        summary.prompt_tokens = sum(c.prompt_tokens for c in inwin)
        summary.cache_read = sum(c.cache_read for c in inwin)
        summary.cache_creation = sum(c.cache_creation for c in inwin)
        summary.input_tokens = sum(c.input_tokens for c in inwin)
        summary.output_tokens = sum(c.output_tokens for c in inwin)
        summary.diagnostics_present = sum(1 for c in inwin if c.reason)
    if args.json:
        print(json.dumps(to_json(summary, since), indent=2))
        return 0
    print(header(summary, since, args.since))
    print()
    if args.cmd == "why":
        print(why(summary))
    else:
        print(ledger(summary, args.limit))
        print()
        print(rollups(summary))
    return 0


def cmd_session(args) -> int:
    calls, _ = _load(argparse.Namespace(since="all", root=args.root))
    hits = [c for c in calls if c.session_id.startswith(args.session) or (c.slug or "") == args.session]
    if not hits:
        raise SystemExit(f"no session matching {args.session!r} under {args.root}")
    ids = {c.session_id for c in hits}
    if len(ids) > 1:
        raise SystemExit("ambiguous prefix, matches: " + ", ".join(sorted(i[:8] for i in ids)))
    summary = detect(hits)
    if args.json:
        print(json.dumps(to_json(summary, None), indent=2))
        return 0
    print(header(summary, None, f"session {hits[0].session_id[:8]} {hits[0].slug or ''}".strip()))
    print()
    print(timeline(hits, summary))
    return 0


def cmd_sessions(args) -> int:
    calls, since = _load(args)
    inwin = _window(calls, since)
    summary = detect(calls)
    waste = {}
    for r in summary.rebuilds:
        if since is None or r.call.ts >= since:
            waste[r.call.session_id] = waste.get(r.call.session_id, 0.0) + r.premium
    rows = {}
    for c in inwin:
        row = rows.setdefault(c.session_id, {"slug": c.slug, "project": c.project, "calls": 0, "prompt": 0, "first": c.ts, "last": c.ts})
        row["calls"] += 1
        row["prompt"] += c.prompt_tokens
        row["slug"] = row["slug"] or c.slug
        row["last"] = max(row["last"], c.ts)
    print(f"{'session':8} {'slug':30} {'project':28} {'calls':>5} {'prompt':>8} {'premium':>8}")
    for sid, row in sorted(rows.items(), key=lambda kv: -waste.get(kv[0], 0.0)):
        print(f"{sid[:8]:8} {(row['slug'] or '')[:30]:30} {row['project'][:28]:28} {row['calls']:>5} {row['prompt']//1000:>7}k {waste.get(sid, 0.0):>9.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cachemiss", description=__doc__)
    ap.add_argument("--version", action="version", version=f"cachemiss {__version__}")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="transcripts directory (default: ~/.claude/projects)")
    sub = ap.add_subparsers(dest="cmd")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--since", default="5h", help="window: 5h (default), 24h, 7d, all")
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument("--root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    p = sub.add_parser("ledger", parents=[common], help="every rebuild in the window, costliest first (default)")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_ledger)
    p = sub.add_parser("why", parents=[common], help="reasons, what they mean, what to do")
    p.set_defaults(func=cmd_ledger)
    p = sub.add_parser("sessions", parents=[common], help="sessions in the window ranked by avoidable cost")
    p.set_defaults(func=cmd_sessions)
    p = sub.add_parser("session", help="one session as a timeline")
    p.add_argument("session", help="session id prefix or slug")
    p.add_argument("--json", action="store_true")
    p.add_argument("--root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_session)

    argv = list(sys.argv[1:] if argv is None else argv)
    known = {"ledger", "why", "sessions", "session", "-h", "--help", "--version"}
    if not any(a in known for a in argv):
        argv = ["ledger", *argv]  # bare `cachemiss --since 24h` means the ledger
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
