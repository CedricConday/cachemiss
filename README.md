# cachemiss

**Why did my Claude Code quota drain?** One command over the transcripts Claude Code
already keeps on your disk: every prompt-cache rebuild, why it happened, what it cost,
and which sub-agent did it. Nothing leaves your machine.

```bash
pip install cachemiss
cachemiss                 # the last 5 hours, the quota window
cachemiss why             # each reason explained, with what to do about it
cachemiss session <id>    # one session as a timeline
cachemiss sessions        # sessions ranked by rebuild cost
cachemiss --since 7d --json
```

## What it shows

A Claude Code session is a chain of API calls that share a growing prompt. In a healthy
chain each call reads the whole prefix from the prompt cache and writes only the last
turn. A **rebuild** is a call that writes most of the prompt again: the cache entry
expired, a sub-agent started from scratch, the tool list or system prompt changed, the
history was compacted, or the model switched. Rebuilds are where a five-hour window goes.

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

`cachemiss why` turns the reasons into plain language and a fix:

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

## Where the reasons come from

Claude Code records `usage` for every API call. Newer versions also record the API's own
**cache diagnostics** on some calls (`message.diagnostics.cache_miss_reason`): the API
compared the request with the previous one and says whether the model, the system
prompt, the tool list or the message history changed, or whether it had nothing to
compare against (`previous_message_not_found`, which is what an expired entry or a fresh
chain looks like). Those reasons are shown as they are.

Calls without diagnostics get an **inferred** reason, marked with `*`, from what the
record does show: the gap to the previous call against the entry's TTL (Claude Code
writes the main conversation to the 1-hour cache and sub-agents to the 5-minute one),
the first call of a sub-agent, a model or CLI version change, or a prompt that shrank
(compaction). What is left is `prefix_changed*`: something before the last turn changed
and only the diagnostics beta can say what.

## Numbers, stated plainly

* **rewrote**: tokens written to the cache that were already cached on the previous
  call of the same chain. A session's first call writes everything and is not counted.
* **premium**: what those tokens cost as a cache write (1.25x input price on the
  5-minute cache, 2x on the 1-hour cache) minus what they would have cost as a cache
  read (0.1x, 0.025x on Claude Fable 5.1, 0.05x on Claude Opus 5.5). Prices are the
  Anthropic API list prices per model. A subscription is not billed in dollars, but the
  tokens are the same tokens; treat the figure as a proportion, not a bill.
* **avoidable vs expected** (`cachemiss why`): an expired entry, a changed tool list,
  an edited system prompt or a model switch can be avoided; a sub-agent's first call,
  compaction and history edits are the price of the feature.
* A rebuild is counted when at least half of a prompt of 4k tokens or more was
  written, or when the API diagnostics flagged the call and at least 4k tokens were
  written. Thresholds are constants at the top of `cachemiss/analysis.py`.

## What it does not do

* It does not read your conversation content; only the usage and metadata fields of
  `assistant` records.
* It does not talk to any network. There is no telemetry.
* It does not know your plan's quota formula. Anthropic does not publish one; the tool
  reports tokens and their API value and lets you draw the proportion.
* It is not an official Anthropic tool. Transcript fields can change between Claude Code
  versions; the reader is tolerant of missing fields and the tests pin the shapes seen
  on 2.1.228 through 2.1.283.

## Development

```bash
pip install -e ".[dev]"
pytest -q
ruff check cachemiss tests
```

The tests generate synthetic transcripts with known cache behaviour (a healthy loop, an
expired 1-hour entry, a sub-agent on the 5-minute cache, a model switch, compaction,
multi-block responses) and check that every rebuild is attributed to the right reason
with the right token count, before anything is claimed about real transcripts.

MIT. Written by Cedric Conday with Claude (Anthropic) as coding partner.
