"""Read Claude Code transcripts into one API call per record.

Layout (observed on Claude Code 2.1.x):
  <root>/<project>/<session>.jsonl                      main conversation
  <root>/<project>/<session>/subagents/agent-<id>.jsonl  one file per sub-agent

An API call appears as one or more ``type: "assistant"`` lines that share a
``requestId`` (one per content block). Usage is identical on each, so the
first block is taken and the rest are dropped. The cache diagnostics, when
the CLI was run with them, sit at ``message.diagnostics.cache_miss_reason``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"


@dataclass
class Call:
    ts: datetime
    file: str
    project: str
    session_id: str
    is_subagent: bool
    agent_id: str | None
    agent_name: str | None
    model: str | None
    version: str | None
    request_id: str | None
    uuid: str | None
    input_tokens: int
    cache_creation: int
    cache_read: int
    output_tokens: int
    creation_5m: int
    creation_1h: int
    reason: str | None  # diagnostics.cache_miss_reason.type when present
    missed: int | None = None  # diagnostics.cache_miss_reason.cache_missed_input_tokens
    quota: dict | None = None
    effort: str | None = None
    slug: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_creation + self.cache_read

    @property
    def ttl(self) -> str:
        """Which cache TTL this call wrote to ('1h', '5m', 'mixed' or 'none')."""
        if self.creation_1h and self.creation_5m:
            return "mixed"
        if self.creation_1h:
            return "1h"
        if self.creation_5m:
            return "5m"
        return "none"

    @property
    def chain(self) -> str:
        """Identity of the cache chain: a sub-agent has its own prefix and file."""
        return self.file


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def iter_files(root: Path = DEFAULT_ROOT) -> Iterator[Path]:
    if not root.exists():
        return
    yield from sorted(root.rglob("*.jsonl"))


def _project_of(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).parts[0]
    except ValueError:
        return path.parent.name


def parse_record(d: dict, path: Path, project: str, is_sub: bool, seen: set[str]) -> Call | None:
    """One transcript record to a Call, or None when it carries no API usage."""
    if d.get("type") != "assistant":
        return None
    m = d.get("message") or {}
    u = m.get("usage") or {}
    if not u:
        return None
    if m.get("model") == "<synthetic>" or d.get("isApiErrorMessage"):
        return None  # a failed request; no prompt was cached or read
    rid = d.get("requestId") or m.get("id")
    if rid:
        if rid in seen:
            return None
        seen.add(rid)
    ts = _parse_ts(d.get("timestamp", ""))
    if ts is None:
        return None
    cc = u.get("cache_creation") or {}
    diag = (m.get("diagnostics") or {}).get("cache_miss_reason") or {}
    prompt = int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
    if prompt == 0:
        return None  # nothing was sent; keeps zero-usage blocks out of the chain
    return Call(
        ts=ts,
        file=str(path),
        project=project,
        session_id=d.get("sessionId") or d.get("session_id") or path.stem,
        is_subagent=bool(d.get("isSidechain")) or is_sub,
        agent_id=d.get("agentId"),
        agent_name=d.get("attributionAgent"),
        model=m.get("model"),
        version=d.get("version"),
        request_id=rid,
        uuid=d.get("uuid"),
        input_tokens=int(u.get("input_tokens") or 0),
        cache_creation=int(u.get("cache_creation_input_tokens") or 0),
        cache_read=int(u.get("cache_read_input_tokens") or 0),
        output_tokens=int(u.get("output_tokens") or 0),
        creation_5m=int(cc.get("ephemeral_5m_input_tokens") or 0),
        creation_1h=int(cc.get("ephemeral_1h_input_tokens") or 0),
        reason=diag.get("type"),
        missed=diag.get("cache_missed_input_tokens"),
        quota=d.get("quotaLimits"),
        effort=d.get("effort"),
        slug=d.get("slug"),
    )


def _is_subagent_file(path: Path) -> bool:
    return "subagents" in path.parts or path.name.startswith("agent-")


def read_file(path: Path, root: Path = DEFAULT_ROOT) -> Iterator[Call]:
    seen: set[str] = set()
    project = _project_of(path, root)
    is_sub = _is_subagent_file(path)
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            call = parse_record(d, path, project, is_sub, seen)
            if call is not None:
                yield call


def read_new(path: Path, offset: int, root: Path = DEFAULT_ROOT, seen: set[str] | None = None) -> tuple[list[Call], int]:
    """Calls from the bytes appended to ``path`` since ``offset``; returns them and the new offset.

    A partial last line (a record still being written) is left for the next read.
    """
    seen = set() if seen is None else seen
    project = _project_of(path, root)
    is_sub = _is_subagent_file(path)
    calls: list[Call] = []
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read()
    if not data:
        return calls, offset
    if not data.endswith(b"\n"):
        cut = data.rfind(b"\n")
        if cut < 0:
            return calls, offset
        data = data[: cut + 1]
    for line in data.decode(errors="replace").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        call = parse_record(d, path, project, is_sub, seen)
        if call is not None:
            calls.append(call)
    return calls, offset + len(data)


def load_calls(root: Path = DEFAULT_ROOT, since: datetime | None = None) -> list[Call]:
    """Every API call under ``root``, oldest first.

    With ``since``, files not modified since then are skipped without parsing,
    which keeps a 5-hour window fast on a large history. Files that were
    touched are read in full, so the first in-window call of a chain still has
    its predecessor; the report layer filters calls to the window.
    """
    calls: list[Call] = []
    for path in iter_files(root):
        if since is not None:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime < since:
                continue
        calls.extend(read_file(path, root))
    calls.sort(key=lambda c: c.ts)
    return calls
