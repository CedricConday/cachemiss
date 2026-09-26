"""Turn a sequence of calls into rebuild events with a reason, a cost and a culprit.

A *rebuild* is a call that wrote to the cache a large share of a prompt that
had already been cached on the previous call of the same chain. The healthy
loop writes only the delta of the last turn; a rebuild rewrites what was there.

Reasons come from the API's own cache diagnostics when the transcript has
them (``messages_changed``, ``tools_changed``, ``system_changed``,
``model_changed``, ``previous_message_not_found``, ``unavailable``). Without
diagnostics the reason is inferred from what the record does show and is
labelled as inferred:

  subagent_start        first call of a sub-agent; it starts its own prefix
  ttl_expired           the previous call's cache entry (1h on the main
                        chain, 5m in sub-agents) had expired by this call
  model_changed         a different model than the previous call
  cli_version_changed   Claude Code was updated between the calls
  context_shrank        the prompt is much smaller than before: history was
                        compacted or rewritten
  prefix_changed        none of the above: system prompt, tool list or the
                        message history changed (the diagnostics beta would
                        say which)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from .pricing import price_for
from .transcripts import Call

TTL = {"1h": timedelta(hours=1), "5m": timedelta(minutes=5), "mixed": timedelta(minutes=5), "none": timedelta(minutes=5)}
MIN_PROMPT = 4000  # below this a rewrite is noise, not a rebuild
REBUILD_SHARE = 0.5  # share of the prompt written to cache that counts as a rebuild
SHRINK_SHARE = 0.7  # prompt smaller than this share of the previous one = compaction

API_REASONS = {
    "messages_changed": "The message history differs from the previous request before the last turn: earlier messages were edited, compacted or reordered.",
    "tools_changed": "The tool list differs from the previous request: an MCP server reconnected, a deferred tool was loaded, a skill or plugin changed the tool set.",
    "system_changed": "The system prompt differs from the previous request: CLAUDE.md, memory, a skill, the date or an injected reminder changed.",
    "model_changed": "A different model served this call; caches are per model.",
    "previous_message_not_found": "The API had no fingerprint of the previous request to compare against. In Claude Code this is what an expired cache entry, an aborted request or a fresh chain looks like.",
    "unavailable": "The diagnostics could not determine a reason for this request.",
}
INFERRED_REASONS = {
    "subagent_start": "A sub-agent started; it builds its own prefix from scratch, on the 5-minute cache.",
    "ttl_expired": "The previous cache entry had expired before this call; the whole prefix was written again.",
    "model_changed": "A different model served this call; caches are per model.",
    "cli_version_changed": "Claude Code was updated between calls; the system prompt and tool set changed with it.",
    "context_shrank": "The prompt shrank sharply: the history was compacted or rewritten, so the cached prefix no longer matched.",
    "prefix_changed": "Something before the last turn changed (system prompt, tool list or history). Run Claude Code with cache diagnostics to see which.",
}
FIXES = {
    "subagent_start": "Fewer, larger sub-agent tasks; a sub-agent that returns after five minutes idle pays the rebuild again.",
    "ttl_expired": "Keep the gap between turns under the TTL, or accept the rebuild as the price of a break.",
    "previous_message_not_found": "Same as ttl_expired when it follows a long gap; otherwise the previous request carried no diagnostics.",
    "tools_changed": "Check for an MCP server that disconnects and reconnects, and avoid loading deferred tools one at a time mid-session.",
    "system_changed": "Avoid editing CLAUDE.md, memory files or skills mid-session; each edit rewrites the prefix.",
    "messages_changed": "Usually compaction or a resumed session; unavoidable, but worth knowing when it happened.",
    "model_changed": "Stay on one model within a session; a switch rebuilds the whole prefix.",
    "cli_version_changed": "Nothing to do; the update itself is the cause.",
    "context_shrank": "Compaction pays one rebuild to make the rest of the session cheaper; expected.",
    "prefix_changed": "Enable cache diagnostics to get the exact reason.",
    "unavailable": "No action; the API could not say.",
}


@dataclass
class Rebuild:
    call: Call
    prev: Call | None
    reason: str
    inferred: bool
    rebuilt_tokens: int  # tokens written that were already cached before
    gap: timedelta | None
    write_cost: float  # dollars paid to write the rebuilt tokens
    read_cost: float  # what the same tokens would have cost as a cache read
    share: float  # share of this prompt that was written

    @property
    def premium(self) -> float:
        """What the rebuild cost beyond a cache read of the same tokens."""
        return self.write_cost - self.read_cost

    @property
    def label(self) -> str:
        """Reason as shown: API reasons plain, inferred ones starred, expiry tagged."""
        tag = "*" if self.inferred else ""
        expired = (
            self.reason == "previous_message_not_found"
            and self.gap is not None
            and self.prev is not None
            and self.gap > TTL.get(self.prev.ttl, TTL["5m"])
        )
        return f"{self.reason} (gap>TTL)" if expired else self.reason + tag


@dataclass
class Summary:
    calls: int = 0
    sessions: int = 0
    prompt_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    rebuilds: list[Rebuild] = field(default_factory=list)
    diagnostics_present: int = 0

    @property
    def hit_rate(self) -> float:
        return self.cache_read / self.prompt_tokens if self.prompt_tokens else 0.0

    @property
    def rebuilt_tokens(self) -> int:
        return sum(r.rebuilt_tokens for r in self.rebuilds)

    @property
    def premium(self) -> float:
        return sum(r.premium for r in self.rebuilds)

    def by(self, key) -> dict:
        out: dict = defaultdict(lambda: {"count": 0, "tokens": 0, "premium": 0.0})
        for r in self.rebuilds:
            k = key(r)
            out[k]["count"] += 1
            out[k]["tokens"] += r.rebuilt_tokens
            out[k]["premium"] += r.premium
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["premium"]))


def infer_reason(call: Call, prev: Call | None, gap: timedelta | None) -> str:
    if prev is None:
        return "subagent_start" if call.is_subagent else "session_start"
    if gap is not None and gap > TTL.get(prev.ttl, TTL["5m"]):
        return "ttl_expired"
    if call.model != prev.model:
        return "model_changed"
    if call.version != prev.version:
        return "cli_version_changed"
    if prev.prompt_tokens and call.prompt_tokens < SHRINK_SHARE * prev.prompt_tokens:
        return "context_shrank"
    return "prefix_changed"


def detect(calls: list[Call]) -> Summary:
    """Classify every call; ``calls`` must be in time order and may span chains."""
    summary = Summary(calls=len(calls))
    summary.sessions = len({c.session_id for c in calls})
    last: dict[str, Call] = {}
    for call in calls:
        summary.prompt_tokens += call.prompt_tokens
        summary.cache_read += call.cache_read
        summary.cache_creation += call.cache_creation
        summary.input_tokens += call.input_tokens
        summary.output_tokens += call.output_tokens
        if call.reason:
            summary.diagnostics_present += 1
        prev = last.get(call.chain)
        last[call.chain] = call
        gap = (call.ts - prev.ts) if prev else None
        share = call.cache_creation / call.prompt_tokens if call.prompt_tokens else 0.0
        big = call.prompt_tokens >= MIN_PROMPT and share >= REBUILD_SHARE
        if not big and (not call.reason or call.cache_creation < MIN_PROMPT):
            # Either nothing much was rewritten, or the API flagged a miss that cost
            # next to nothing. Neither belongs in a ledger of what drained the quota.
            continue
        previously_cached = prev.prompt_tokens if prev else 0
        rebuilt = min(call.cache_creation, previously_cached) if prev else call.cache_creation
        if prev is None and not call.is_subagent:
            # The first call of a session writes everything; that is not a rebuild.
            continue
        reason = call.reason or infer_reason(call, prev, gap)
        inferred = call.reason is None
        price = price_for(call.model)
        write_cost = rebuilt / 1e6 * price.write_per_m(call.ttl)
        read_cost = rebuilt / 1e6 * price.read_per_m
        summary.rebuilds.append(
            Rebuild(call, prev, reason, inferred, rebuilt, gap, write_cost, read_cost, share)
        )
    return summary


AVOIDABLE = {"ttl_expired", "tools_changed", "system_changed", "model_changed", "cli_version_changed", "prefix_changed", "previous_message_not_found"}
EXPECTED = {"subagent_start", "context_shrank", "messages_changed", "unavailable", "session_start"}


def describe(reason: str) -> str:
    return API_REASONS.get(reason) or INFERRED_REASONS.get(reason) or reason
