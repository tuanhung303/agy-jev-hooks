"""Replay bench for the Compass stop gate (hooks/claude-stop-audit.py).

Real cases are Claude transcripts cut at an audited stop, labelled by hand:
`pass` (a steer there is a false alarm), `fail` (a steer is wanted) or
`unsure` (reported, not scored). The jev.yaml pairs are the synthetic
positives and negatives. Cases and labels live outside the repository, since
the transcripts hold client work; see README.md.

    .venv/bin/python bench/jev_compass/run.py --label shipped --runs 2
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
BENCH = Path(os.environ.get("COMPASS_BENCH_DIR")
             or Path.home() / ".local/state/agy-jev-hooks/bench/compass")
os.environ.setdefault("AGY_SAGE_LOG", str(BENCH / "sage.log"))  # keep the live sage log clean

from sage.jev.config.catalog import load_routing  # noqa: E402
from sage.jev.verdict import compass  # noqa: E402

DEADLINE_S = 8.0  # the live gate budget (claude-stop-audit JEV_GATE_BUDGET_SECONDS): a slower answer abstains there too


def pair_transcript(pair: dict) -> str:
    """A jev.yaml pair as a Claude transcript: prompt, calls, results, reply."""
    path = BENCH / "pairs" / f"{pair['id']}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = "2026-01-01T00:00:%02dZ"
    rows = [{"type": "user", "timestamp": ts % 0,
             "message": {"role": "user", "content": pair["user_prompt"]}}]
    for n, call in enumerate(pair.get("tool_calls") or [], start=1):
        rows.append({"type": "assistant", "timestamp": ts % n, "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": call["id"], "name": call["name"], "input": call.get("args") or {}}]}})
        out = call.get("output") or {}
        rows.append({"type": "user", "timestamp": ts % n, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call["id"], "content": str(out.get("content") or ""),
             "is_error": bool(out.get("exit_code"))}]}})
    rows.append({"type": "assistant", "timestamp": ts % 59,
                 "message": {"role": "assistant", "content": [{"type": "text", "text": pair["agent_reply"]}]}})
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(path)


def load_cases() -> list:
    cases = [{**c, "path": str(BENCH / "cases" / f"{c['id']}.jsonl"), "source": "real"}
             for c in json.loads((BENCH / "cases.json").read_text())]
    for pair in load_routing().get("pairs") or []:
        cases.append({"id": pair["id"], "source": "pair", "path": pair_transcript(pair),
                      "expected": "fail" if pair["expected"] == "failed" else "pass",
                      "expect_labels": pair.get("expect_labels") or [],
                      "expect_absent": pair.get("expect_absent") or []})
    return cases


def judge(case: dict) -> dict:
    """One live gate decision: Compass labels plus the steer the hook would send."""
    started = time.monotonic()
    result = compass.jev_compass_classify(case["path"], deadline=time.monotonic() + DEADLINE_S)
    steer = compass.compass_steer(result)
    result = result or {}
    return {"id": case["id"], "steer": (steer or "").split(":")[0].replace("jev_compass ", "") or None,
            "hard": result.get("hard_escalate") or [], "notes": result.get("notes") or [],
            "abstained": not result, "latency_s": round(time.monotonic() - started, 1)}


def score(cases: list, rows: dict) -> dict:
    real = [c for c in cases if c["source"] == "real" and c.get("expected") in ("pass", "fail")]
    pairs = [c for c in cases if c["source"] == "pair"]
    false_alarms = [c["id"] for c in real if c["expected"] == "pass" and rows[c["id"]]["steer"]]
    caught = [c["id"] for c in real if c["expected"] == "fail" and rows[c["id"]]["steer"]]
    pair_ok = []
    for c in pairs:
        hard = set(rows[c["id"]]["hard"])
        wanted = set(c["expect_labels"]) & set(compass.COMPASS_CATEGORIES)
        routed_hard = {lab for lab in wanted if compass.COMPASS_CATEGORIES[lab].get("route") == "H"}
        good = (routed_hard <= hard if c["expected"] == "fail" else not hard) and not (hard & set(c["expect_absent"]))
        pair_ok.append(c["id"]) if good else None
    return {"real_pass_cases": sum(c["expected"] == "pass" for c in real),
            "false_alarms": false_alarms,
            "real_fail_cases": sum(c["expected"] == "fail" for c in real), "caught": caught,
            "pairs": len(pairs), "pairs_ok": len(pair_ok),
            "pairs_wrong": [c["id"] for c in pairs if c["id"] not in pair_ok]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--only", default="", help="comma-separated case ids")
    args = parser.parse_args()
    cases = load_cases()
    if args.only:
        cases = [c for c in cases if c["id"] in args.only.split(",")]
    out_dir = BENCH / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    for run in range(1, args.runs + 1):
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows = {r["id"]: r for r in pool.map(judge, cases)}
        summary = score(cases, rows)
        record = {"label": args.label, "run": run, "summary": summary, "rows": rows}
        (out_dir / f"{args.label}.run{run}.json").write_text(json.dumps(record, indent=1))
        print(f"== {args.label} run {run}: false alarms {len(summary['false_alarms'])}/{summary['real_pass_cases']}"
              f" | caught {len(summary['caught'])}/{summary['real_fail_cases']}"
              f" | pairs {summary['pairs_ok']}/{summary['pairs']}")
        for c in cases:
            r = rows[c["id"]]
            print(f"  {c['id']:<34} exp={c.get('expected', '?'):<6} steer={r['steer'] or '-':<22}"
                  f" hard={','.join(r['hard']) or '-'}{' ABSTAIN' if r['abstained'] else ''} {r['latency_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
