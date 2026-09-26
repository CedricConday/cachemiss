"""Write synthetic Claude Code transcripts with known cache behaviour."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

T0 = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def record(ts, session, *, prompt, read, wrote, ttl="1h", model="claude-opus-5", version="2.1.282",
           sidechain=False, agent=None, agent_name=None, reason=None, request_id=None, block=0, output=300):
    cc = {"ephemeral_5m_input_tokens": wrote if ttl == "5m" else 0,
          "ephemeral_1h_input_tokens": wrote if ttl == "1h" else 0}
    msg = {
        "model": model, "id": f"msg_{request_id or ts.timestamp()}", "role": "assistant",
        "usage": {"input_tokens": prompt - read - wrote, "cache_creation_input_tokens": wrote,
                  "cache_read_input_tokens": read, "output_tokens": output, "cache_creation": cc},
        "content": [{"type": "text", "text": "ok"}],
    }
    if reason:
        msg["diagnostics"] = {"cache_miss_reason": {"type": reason}}
    d = {"type": "assistant", "timestamp": ts.isoformat().replace("+00:00", "Z"), "sessionId": session,
         "isSidechain": sidechain, "requestId": request_id or f"req_{ts.timestamp()}_{block}", "uuid": f"u{ts.timestamp()}",
         "version": version, "message": msg, "apiBlockIndex": block}
    if agent:
        d["agentId"] = agent
        d["attributionAgent"] = agent_name or "general-purpose"
    return d


def write(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in records)
        fh.write('{"type":"user","message":{"role":"user","content":"hi"}}\n')
        fh.write("not json\n")


def healthy_loop(root: Path, session="aaaaaaaa-0000", n=6, base=40_000, step=2_000, model="claude-opus-5"):
    """Every turn reads everything so far and writes only the last turn's delta."""
    recs = []
    prompt = base
    for i in range(n):
        ts = T0 + timedelta(minutes=i)
        if i == 0:
            recs.append(record(ts, session, prompt=prompt, read=0, wrote=prompt, model=model))
        else:
            recs.append(record(ts, session, prompt=prompt, read=prompt - step, wrote=step, model=model))
        prompt += step
    write(root / "proj" / f"{session}.jsonl", recs)
    return recs
