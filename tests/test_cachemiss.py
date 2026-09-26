from datetime import timedelta
from pathlib import Path

import pytest

from cachemiss.analysis import detect
from cachemiss.cli import main
from cachemiss.pricing import price_for
from cachemiss.transcripts import load_calls
from tests.synth import T0, healthy_loop, record, write


def run(root: Path):
    return detect(load_calls(root))


def test_healthy_loop_has_no_rebuilds(tmp_path):
    healthy_loop(tmp_path)
    s = run(tmp_path)
    assert s.calls == 6 and s.rebuilds == []
    assert s.hit_rate > 0.8


def test_ttl_gap_on_main_chain_is_expiry(tmp_path):
    recs = healthy_loop(tmp_path)
    last = recs[-1]
    prompt = last["message"]["usage"]["cache_read_input_tokens"] + last["message"]["usage"]["cache_creation_input_tokens"] + 2000
    # 90 minutes later the 1h entry is gone: the whole prompt is written again
    late = record(T0 + timedelta(minutes=95), "aaaaaaaa-0000", prompt=prompt, read=0, wrote=prompt)
    write(tmp_path / "proj" / "aaaaaaaa-0000.jsonl", recs + [late])
    s = run(tmp_path)
    assert len(s.rebuilds) == 1
    r = s.rebuilds[0]
    assert r.reason == "ttl_expired" and r.inferred
    assert r.rebuilt_tokens == prompt - 2000  # only what was cached before counts
    assert r.gap > timedelta(hours=1)
    assert r.premium == pytest.approx(r.rebuilt_tokens / 1e6 * (5.0 * 2.0 - 0.5))


def test_short_gap_on_subagent_uses_5m_ttl(tmp_path):
    session = "bbbbbbbb-0000"
    sub = tmp_path / "proj" / session / "subagents" / "agent-x1.jsonl"
    r1 = record(T0, session, prompt=30_000, read=0, wrote=30_000, ttl="5m", sidechain=True, agent="x1")
    r2 = record(T0 + timedelta(minutes=2), session, prompt=32_000, read=30_000, wrote=2_000, ttl="5m", sidechain=True, agent="x1")
    r3 = record(T0 + timedelta(minutes=9), session, prompt=34_000, read=0, wrote=34_000, ttl="5m", sidechain=True, agent="x1")
    write(sub, [r1, r2, r3])
    s = run(tmp_path)
    reasons = [r.reason for r in s.rebuilds]
    assert reasons == ["subagent_start", "ttl_expired"]
    assert s.rebuilds[0].rebuilt_tokens == 30_000 and s.rebuilds[1].rebuilt_tokens == 32_000


def test_api_diagnostics_win_over_inference(tmp_path):
    recs = healthy_loop(tmp_path)
    prompt = 60_000
    flagged = record(T0 + timedelta(minutes=6), "aaaaaaaa-0000", prompt=prompt, read=0, wrote=prompt, reason="tools_changed")
    write(tmp_path / "proj" / "aaaaaaaa-0000.jsonl", recs + [flagged])
    s = run(tmp_path)
    assert [r.reason for r in s.rebuilds] == ["tools_changed"]
    assert not s.rebuilds[0].inferred and s.diagnostics_present == 1


def test_expired_api_reason_is_tagged(tmp_path):
    recs = healthy_loop(tmp_path)
    flagged = record(T0 + timedelta(hours=3), "aaaaaaaa-0000", prompt=60_000, read=0, wrote=60_000, reason="previous_message_not_found")
    write(tmp_path / "proj" / "aaaaaaaa-0000.jsonl", recs + [flagged])
    s = run(tmp_path)
    assert s.rebuilds[0].label == "previous_message_not_found (gap>TTL)"


def test_model_switch_and_compaction(tmp_path):
    recs = healthy_loop(tmp_path)
    switched = record(T0 + timedelta(minutes=6), "aaaaaaaa-0000", prompt=52_000, read=0, wrote=52_000, model="claude-sonnet-5")
    compacted = record(T0 + timedelta(minutes=7), "aaaaaaaa-0000", prompt=20_000, read=0, wrote=20_000, model="claude-sonnet-5")
    write(tmp_path / "proj" / "aaaaaaaa-0000.jsonl", recs + [switched, compacted])
    s = run(tmp_path)
    assert [r.reason for r in s.rebuilds] == ["model_changed", "context_shrank"]
    assert s.rebuilds[0].call.model == "claude-sonnet-5"


def test_multi_block_requests_count_once(tmp_path):
    session = "cccccccc-0000"
    a = record(T0, session, prompt=40_000, read=0, wrote=40_000, request_id="req_1", block=0)
    b = record(T0, session, prompt=40_000, read=0, wrote=40_000, request_id="req_1", block=1)
    c = record(T0 + timedelta(minutes=1), session, prompt=42_000, read=40_000, wrote=2_000, request_id="req_2")
    write(tmp_path / "proj" / f"{session}.jsonl", [a, b, c])
    s = run(tmp_path)
    assert s.calls == 2 and s.rebuilds == []


def test_small_prompts_are_not_rebuilds(tmp_path):
    session = "dddddddd-0000"
    recs = [record(T0 + timedelta(minutes=i), session, prompt=3_000, read=0, wrote=3_000) for i in range(4)]
    write(tmp_path / "proj" / f"{session}.jsonl", recs)
    assert run(tmp_path).rebuilds == []


def test_window_keeps_predecessor_for_attribution(tmp_path):
    recs = healthy_loop(tmp_path)
    late = record(T0 + timedelta(minutes=95), "aaaaaaaa-0000", prompt=60_000, read=0, wrote=60_000)
    write(tmp_path / "proj" / "aaaaaaaa-0000.jsonl", recs + [late])
    calls = load_calls(tmp_path, since=T0 + timedelta(minutes=90))
    # load_calls honours since strictly; the CLI loads a margin so the gap is known
    assert len(calls) == 1
    s = detect(load_calls(tmp_path, since=T0 - timedelta(hours=2)))
    assert s.rebuilds[0].gap is not None


def test_pricing_table():
    assert price_for("claude-fable-5-1").read_per_m == pytest.approx(0.25)
    assert price_for("claude-opus-5-5").read_per_m == pytest.approx(0.20)
    assert price_for("claude-opus-5").write_per_m("1h") == pytest.approx(10.0)
    assert price_for("claude-opus-5").write_per_m("5m") == pytest.approx(6.25)
    assert price_for("claude-sonnet-5").input_per_m == 2.0
    assert price_for("something-new").input_per_m == 5.0


def test_cli_json_and_text(tmp_path, capsys):
    recs = healthy_loop(tmp_path)
    late = record(T0 + timedelta(minutes=95), "aaaaaaaa-0000", prompt=60_000, read=0, wrote=60_000)
    write(tmp_path / "proj" / "aaaaaaaa-0000.jsonl", recs + [late])
    assert main(["--root", str(tmp_path), "--since", "all", "--json"]) == 0
    import json
    data = json.loads(capsys.readouterr().out)
    assert data["calls"] == 7 and len(data["rebuilds"]) == 1 and data["rebuilds"][0]["reason"] == "ttl_expired"
    assert main(["--root", str(tmp_path), "why", "--since", "all"]) == 0
    out = capsys.readouterr().out
    assert "ttl_expired" in out and "what to do" in out
    assert main(["--root", str(tmp_path), "session", "aaaaaaaa"]) == 0
    assert "REBUILD" in capsys.readouterr().out
    assert main(["--root", str(tmp_path), "sessions", "--since", "all"]) == 0
