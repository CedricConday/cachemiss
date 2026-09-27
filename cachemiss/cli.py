"""cachemiss: why did my Claude Code quota drain?"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .analysis import Classifier, ColdStart, Rebuild, detect
from .report import header, ledger, rollups, timeline, to_json, why
from .transcripts import DEFAULT_ROOT, iter_files, load_calls, read_new

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
    # Files touched in the window are read in full, so every in-window call has its
    # predecessor even when that predecessor is older than the window.
    calls = load_calls(Path(args.root), since)
    return calls, since


def _window(calls, since):
    return [c for c in calls if since is None or c.ts >= since]


def cmd_ledger(args) -> int:
    calls, since = _load(args)
    summary = detect(calls)
    if since:
        summary.rebuilds = [r for r in summary.rebuilds if r.call.ts >= since]
        summary.cold_starts = [c for c in summary.cold_starts if c.call.ts >= since]
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


class Watcher:
    """Follows the transcript tree and classifies each API call as its record lands.

    On start it feeds the recent history (files modified within ``prime``) to the
    classifier silently, so every live chain has its predecessor; then each poll reads
    only the bytes appended since the last one.
    """

    def __init__(self, root: Path, prime: datetime | None) -> None:
        self.root = root
        self.clf = Classifier()
        self.offsets: dict[Path, int] = {}
        self.seen: dict[Path, set[str]] = {}
        self.events: list[Rebuild | ColdStart] = []
        for path in iter_files(root):
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if prime is not None and mtime >= prime:
                calls, offset = read_new(path, 0, root, self.seen.setdefault(path, set()))
                for call in sorted(calls, key=lambda c: c.ts):
                    self.clf.feed(call)
                self.offsets[path] = offset
            else:
                self.offsets[path] = path.stat().st_size

    def poll(self) -> list[Rebuild | ColdStart]:
        """One pass over the tree; returns the events from records appended since the last pass."""
        fresh: list = []
        for path in iter_files(self.root):
            offset = self.offsets.get(path, 0)
            if path.stat().st_size <= offset:
                continue
            calls, new_offset = read_new(path, offset, self.root, self.seen.setdefault(path, set()))
            self.offsets[path] = new_offset
            fresh.extend(calls)
        out = []
        for call in sorted(fresh, key=lambda c: c.ts):
            event = self.clf.feed(call)
            if event is not None:
                out.append(event)
        self.events.extend(out)
        return out


def _event_line(event: Rebuild | ColdStart) -> str:
    from .report import _gap, _k, _local, _money, _short  # same formatting as the ledger

    c = event.call
    agent = (c.agent_name or ("sub-agent" if c.is_subagent else "main"))[:18]
    if isinstance(event, ColdStart):
        return f"{_local(c.ts):11} {_short(c.session_id):8} {agent:18} {event.kind:36} {'':7} {_k(event.written):>8} {_money(event.write_cost):>8}  cold start"
    return f"{_local(c.ts):11} {_short(c.session_id):8} {agent:18} {event.label:36} {_gap(event.gap):7} {_k(event.rebuilt_tokens):>8} {_money(event.premium):>8}"


def cmd_watch(args) -> int:
    import time

    root = Path(args.root)
    prime = datetime.now(timezone.utc) - timedelta(hours=args.prime_hours)
    w = Watcher(root, prime)
    chains = len(w.clf.last)
    print(f"watching {root} every {args.interval}s; {chains} chain(s) primed from the last {args.prime_hours}h. Ctrl-C to stop.")
    print(f"{'when':11} {'session':8} {'agent':18} {'reason':36} {'gap':7} {'rewrote':>8} {'premium':>8}")
    try:
        while True:
            for event in w.poll():
                if args.json:
                    print(json.dumps({"kind": "cold_start" if isinstance(event, ColdStart) else "rebuild", "line": _event_line(event)}))
                else:
                    print(_event_line(event), flush=True)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    rebuilds = [e for e in w.events if isinstance(e, Rebuild)]
    colds = [e for e in w.events if isinstance(e, ColdStart)]
    from .report import _money
    print(f"\nwatched: {len(rebuilds)} rebuild(s), premium {_money(sum(r.premium for r in rebuilds))}; {len(colds)} cold start(s), {_money(sum(c.write_cost for c in colds))}")
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
    p = sub.add_parser("sessions", parents=[common], help="sessions in the window ranked by rebuild premium")
    p.set_defaults(func=cmd_sessions)
    p = sub.add_parser("session", help="one session as a timeline")
    p.add_argument("session", help="session id prefix or slug")
    p.add_argument("--json", action="store_true")
    p.add_argument("--root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_session)
    p = sub.add_parser("watch", help="follow the transcripts and print each rebuild as it happens")
    p.add_argument("--interval", type=float, default=5.0, help="seconds between polls (default 5)")
    p.add_argument("--prime-hours", type=float, default=2.0, help="feed this much recent history first so live chains have their predecessor (default 2)")
    p.add_argument("--once", action="store_true", help="one poll, then exit (for scripts and tests)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_watch)

    argv = list(sys.argv[1:] if argv is None else argv)
    subcommands = ("ledger", "why", "sessions", "session", "watch")
    found = next((a for a in argv if a in subcommands), None)
    if found is None and not any(a in ("-h", "--help", "--version") for a in argv):
        argv = ["ledger", *argv]  # bare `cachemiss --since 24h` means the ledger
    elif found is not None:
        argv.remove(found)
        argv = [found, *argv]  # `cachemiss --since 5h why` is fine too
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
