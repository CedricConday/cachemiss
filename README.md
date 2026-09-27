# cachemiss

[![tests](https://github.com/CedricConday/cachemiss/actions/workflows/tests.yml/badge.svg)](https://github.com/CedricConday/cachemiss/actions/workflows/tests.yml)

**Why did my Claude Code quota drain?** One command over the transcripts Claude Code
already keeps on your disk: every prompt-cache rebuild, why it happened, what it cost,
and which sub-agent did it. Nothing leaves your machine.

```bash
pip install cachemiss
cachemiss                 # the last 5 hours, the quota window
cachemiss why             # each reason explained, with what to do about it
cachemiss session <id>    # one session as a timeline
cachemiss sessions        # sessions ranked by rebuild cost
cachemiss watch           # follow the transcripts and print each rebuild as it lands
cachemiss --since 7d --json
```

## Watching live

`cachemiss watch` polls the transcript directory (every 5 s by default), reads only the
bytes appended since the last poll, and prints one ledger line per rebuild or cold start
the moment its record lands, with the same reason, tokens-rewritten and premium columns
as the ledger. On start it feeds the last two hours of history to the classifier
silently, so a chain that is mid-conversation already has its predecessor and the first
live call is judged against it rather than counted as a cold start. Ctrl-C prints the
totals for the watched period. `--once` does a single poll for scripts; `--json` emits one
object per event.

## What it shows

A Claude Code session is a chain of API calls that share a growing prompt. In a healthy
chain each call reads the whole prefix from the prompt cache and writes only the last
turn. A **rebuild** is a call that writes again what the previous call had already
cached: the cache entry expired, the tool list or system prompt changed, the history was
compacted, or the model switched. A **cold start** is the first call of a chain (a new
session, a sub-agent, a fork); nothing was cached yet, so it is priced separately and
never counted as a rebuild. Rebuilds are where a five-hour window goes.

Real output from the machine this was written on, last 24 hours:

```
cachemiss  window: 24h   calls: 624   sessions: 34
prompt tokens: 152450k   cache hit rate: 97%   written: 3959k   uncached input: 12k   output: 692k
rebuilds: 10   tokens rewritten that were already cached: 986k   paid as writes: $18.08   as reads they would have cost: $0.25   rebuild premium: $17.83
Dollar figures are API list prices for the same tokens; a subscription quota is not billed in dollars, but it drains in the same proportion.
API diagnostics present on 7 calls; other reasons are inferred.

when        session  agent              reason                               gap      rewrote  premium
09-26 16:37 0f82a131 main               previous_message_not_found (gap>TTL) 3h00        374k    $7.38
09-26 08:50 e7185911 main               previous_message_not_found (gap>TTL) 14h40       369k    $7.29
09-26 17:33 0f82a131 general-purpose    subagent_start*                      -            47k    $0.58
09-26 17:33 0f82a131 general-purpose    subagent_start*                      -            47k    $0.57
09-26 12:31 0f82a131 general-purpose    subagent_start*                      -            47k    $0.57
09-26 17:33 0f82a131 general-purpose    subagent_start*                      -            47k    $0.57
09-26 17:45 0f82a131 general-purpose    subagent_start*                      -            30k    $0.37
09-26 09:50 ec11008d main               messages_changed                     12s          12k    $0.23
09-26 09:13 e7185911 main               messages_changed                     8s            9k    $0.17
09-26 10:25 ba739404 main               messages_changed                     2s            4k    $0.09
* = reason inferred from the record (no API diagnostics on that call)

by reason                                 n   rewrote  premium
previous_message_not_found (gap>TTL)      2      743k   $14.67
subagent_start*                           5      218k    $2.67
messages_changed                          3       25k    $0.50

by agent                                  n   rewrote  premium
main                                      5      768k   $15.16
general-purpose                           5      218k    $2.67

by session                                n   rewrote  premium
0f82a131 serialized-moseying-snowglobe    6      591k   $10.05
e7185911 synthetic-squishing-platypus     2      378k    $7.46
ec11008d                                  1       12k    $0.23
ba739404 majestic-puzzling-flask          1        4k    $0.09

by model                                  n   rewrote  premium
claude-fable-5-1                         10      986k   $17.83
```
cachemiss  window: 24h   calls: 650   sessions: 34
prompt tokens: 166521k   cache hit rate: 97%   written: 4187k   uncached input: 12k   output: 722k
rebuilds: 5   tokens rewritten that were already cached: 739k   paid as writes: $14.78   as reads they would have cost: $0.18   rebuild premium: $14.59
cold starts (not rebuilds): 38, of which 6 sub-agents; written: 1270k   paid: $12.66
Dollar figures are API list prices for the same tokens, a proportion to compare, not a bill.
API diagnostics present on 7 calls; other reasons are inferred.

when        session  agent              reason                               gap      rewrote  premium
09-26 16:37 0f82a131 main               previous_message_not_found (gap>TTL) 3h00        357k    $7.05
09-26 08:50 e7185911 main               previous_message_not_found (gap>TTL) 14h40       357k    $7.05
09-26 09:50 ec11008d main               messages_changed                     12s          12k    $0.23
09-26 09:13 e7185911 main               messages_changed                     8s            9k    $0.17
09-26 10:25 ba739404 main               messages_changed                     2s            4k    $0.09
* = reason inferred from the record (no API diagnostics on that call)

by reason                                 n   rewrote  premium
previous_message_not_found (gap>TTL)      2      714k   $14.10
messages_changed                          3       25k    $0.50

by agent                                  n   rewrote  premium
main                                      5      739k   $14.59

by session                                n   rewrote  premium
e7185911 synthetic-squishing-platypus     2      366k    $7.22
0f82a131 serialized-moseying-snowglobe    1      357k    $7.05
ec11008d                                  1       12k    $0.23
ba739404 majestic-puzzling-flask          1        4k    $0.09

by model                                  n   rewrote  premium
claude-fable-5-1                          5      739k   $14.59
```
cachemiss  window: 24h   calls: 624   sessions: 34
prompt tokens: 152450k   cache hit rate: 97%   written: 3959k   uncached input: 12k   output: 692k
rebuilds: 10   tokens rewritten that were already cached: 986k   paid as writes: $18.08   as reads they would have cost: $0.25   rebuild premium: $17.83
Dollar figures are API list prices for the same tokens; a subscription quota is not billed in dollars, but it drains in the same proportion.
API diagnostics present on 7 calls; other reasons are inferred.

previous_message_not_found (API diagnostics): 2 rebuilds, 743k tokens rewritten, $14.67 premium, avoidable
  what it means: The API had no fingerprint of the previous request to compare against. In Claude Code this is what an expired cache entry, an aborted request or a fresh chain looks like.
  what to do:    Same as ttl_expired when it follows a long gap; otherwise the previous request carried no diagnostics.

subagent_start (inferred): 5 rebuilds, 218k tokens rewritten, $2.67 premium, expected
  what it means: A sub-agent started; it builds its own prefix from scratch, on the 5-minute cache.
  what to do:    Fewer, larger sub-agent tasks; a sub-agent that returns after five minutes idle pays the rebuild again.

messages_changed (API diagnostics): 3 rebuilds, 25k tokens rewritten, $0.50 premium, expected
  what it means: The message history differs from the previous request before the last turn: earlier messages were edited, compacted or reordered.
  what to do:    Usually compaction or a resumed session; unavoidable, but worth knowing when it happened.
```
cachemiss  window: 24h   calls: 650   sessions: 34
prompt tokens: 166521k   cache hit rate: 97%   written: 4187k   uncached input: 12k   output: 722k
rebuilds: 5   tokens rewritten that were already cached: 739k   paid as writes: $14.78   as reads they would have cost: $0.18   rebuild premium: $14.59
cold starts (not rebuilds): 38, of which 6 sub-agents; written: 1270k   paid: $12.66
Dollar figures are API list prices for the same tokens, a proportion to compare, not a bill.
API diagnostics present on 7 calls; other reasons are inferred.

previous_message_not_found (API diagnostics): 2 rebuilds, 714k tokens rewritten, $14.10 premium, avoidable
  what it means: The API had no fingerprint of the previous request to compare against. In Claude Code this is what an expired cache entry, an aborted request or a fresh chain looks like.
  what to do:    Same as ttl_expired when it follows a long gap; otherwise the previous request carried no diagnostics.

messages_changed (API diagnostics): 3 rebuilds, 25k tokens rewritten, $0.50 premium, expected
  what it means: The message history differs from the previous request before the last turn: earlier messages were edited, compacted or reordered.
  what to do:    Usually compaction or a resumed session; unavoidable, but worth knowing when it happened.
```bash
pip install -e ".[dev]"
pytest -q
ruff check cachemiss tests
```

The tests generate synthetic transcripts with known cache behaviour (a healthy loop, an
expired 1-hour entry, a sub-agent on the 5-minute cache, a model switch, compaction, a
growing prompt, a partial rebuild, a fork, error records, multi-block responses, a
window that starts after the break) and check that every rebuild is attributed to the
right reason with the right token count, before anything is claimed about real
transcripts.

MIT. Written by Cedric Conday with Claude (Anthropic) as coding partner.
