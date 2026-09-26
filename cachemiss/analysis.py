"""Turn a sequence of calls into rebuild events with a reason, a cost and a culprit.

A *rebuild* is a call that wrote to the cache tokens that were already cached
on the previous call of the same chain: what the previous call had cached
minus what this call read back, capped by what this call wrote. The healthy
loop reads everything cached so far and writes only the last turn.

A *cold start* is the first call of a chain: a session's first call, a
sub-agent's first call, or a forked session copying its parent's history.
Nothing was cached before, so nothing was rewritten; it is reported and priced
separately, never counted as a rebuild.

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

TTL = {"1h": timedelta(hours=1), "5m": timedelta(minutes=5)}
MIN_PROMPT = 4000  # fewer rewritten tokens than this is noise, not a rebuild
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
    "session_start": "A session's first call, or a forked session copying its parent's history; nothing was cached yet.",
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
    chain_ttl: str = "1h"  # TTL the chain's cache entries carried before this call

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
            and self.gap > TTL[self.chain_ttl]
        )
        return f"{self.reason} (gap>TTL)" if expired else self.reason + tag


@dataclass
class ColdStart:
    call: Call
    kind: str  # session_start or subagent_start
    written: int
    write_cost: float



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
    cold_starts: list[ColdStart] = field(default_factory=list)
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


def chain_ttl_after(previous: str, call: Call) -> str:
    """The TTL the chain's entries carry after ``call``: a pure read keeps the old one."""
    if call.ttl in ("1h", "mixed"):
        return "1h"
    if call.ttl == "5m":
        return "5m"
    return previous


def infer_reason(call: Call, prev: Call | None, gap: timedelta | None, chain_ttl: str) -> str:
    if prev is None:
        return "subagent_start" if call.is_subagent else "session_start"
    if gap is not None and gap > TTL[chain_ttl]:
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
    ttl_of: dict[str, str] = {}
    for call in calls:
        summary.prompt_tokens += call.prompt_tokens
        summary.cache_read += call.cache_read
        summary.cache_creation += call.cache_creation
        summary.input_tokens += call.input_tokens
        summary.output_tokens += call.output_tokens
        if call.reason:
            summary.diagnostics_present += 1
        prev = last.get(call.chain)
        chain_ttl = ttl_of.get(call.chain, "1h" if not call.is_subagent else "5m")
        last[call.chain] = call
        ttl_of[call.chain] = chain_ttl_after(chain_ttl, call)
        price = price_for(call.model)

        if prev is None:
            if call.cache_creation >= MIN_PROMPT:
                kind = "subagent_start" if call.is_subagent else "session_start"
                cost = call.cache_creation / 1e6 * price.write_per_m(call.ttl)
                summary.cold_starts.append(ColdStart(call, kind, call.cache_creation, cost))
            continue

        gap = call.ts - prev.ts
        previously_cached = prev.cache_read + prev.cache_creation
        lost = call.missed if call.missed is not None else max(0, previously_cached - call.cache_read)
        rebuilt = min(lost, call.cache_creation)
        if rebuilt < MIN_PROMPT:
            continue
        reason = call.reason or infer_reason(call, prev, gap, chain_ttl)
        share = call.cache_creation / call.prompt_tokens if call.prompt_tokens else 0.0
        write_cost = rebuilt / 1e6 * price.write_per_m(call.ttl)
        read_cost = rebuilt / 1e6 * price.read_per_m
        summary.rebuilds.append(
            Rebuild(call, prev, reason, call.reason is None, rebuilt, gap, write_cost, read_cost, share, chain_ttl)
        )
    return summary


AVOIDABLE = {"ttl_expired", "tools_changed", "system_changed", "model_changed", "cli_version_changed", "prefix_changed", "previous_message_not_found"}
EXPECTED = {"subagent_start", "context_shrank", "messages_changed", "unavailable", "session_start"}


def describe(reason: str) -> str:
    return API_REASONS.get(reason) or INFERRED_REASONS.get(reason) or reason
