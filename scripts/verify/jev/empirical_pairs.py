#!/usr/bin/env python3
"""Empirical check: jev.yaml fixture pairs vs real Jev compass verdicts.

For each pair, builds the compass request through sage.jev.request.parser (evidence
blocks + steering-excluded prompt pair), calls the Jev endpoint once, derives
pass/failed[reasons] locally from q_pass and the route floors, and compares
against the pair's expected verdict and category.

  python3 scripts/verify/jev/empirical_pairs.py
  python3 scripts/verify/jev/empirical_pairs.py --judge "Claude Sonnet 4.6"
  python3 scripts/verify/jev/empirical_pairs.py --json tmp/jev_empirical.json

--judge MODEL additionally asks that agy model for its own verdict per pair
and reports three-way agreement (expected / jev / judge). The Jev call itself
never depends on the judge; a judge failure degrades to two-way.

Exit code: 0 when every pair matches expectation, 1 on any mismatch or
unavailable call (CI-gateable), 2 when no pairs or no Jev key.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def _load_env_file(path: Path) -> None:
    """Minimal KEY=VALUE overlay; existing env wins."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except OSError:
        pass


_load_env_file(Path.home() / ".config" / "agy" / "sage.env")

from sage.jev.request import prompt_pair  # noqa: E402
from sage.jev.config.catalog import axis_of, derive_verdicts, load_routing  # noqa: E402
from sage.jev.evidence.assemble import assemble_evidence, build_payload  # noqa: E402
from sage.jev.request.parser import build_request, parse_boolean_answers  # noqa: E402
from sage.config import JEV_GATE_API_KEY  # noqa: E402
from sage.jev.transport import _call_jev  # noqa: E402


def _steps(pair: dict) -> list:
    """USER_INPUT, tool-calls PLANNER_RESPONSE with identity-bound results,
    then the final reply — the shape compass evidence assembly expects."""
    steps = [{"type": "USER_INPUT", "content": pair["user_prompt"]}]
    calls = pair.get("tool_calls") or []
    if calls:
        tool_calls = [{"id": c["id"], "name": c["name"], "args": c.get("args") or {}}
                      for c in calls]
        steps.append({"type": "PLANNER_RESPONSE", "content": "", "tool_calls": tool_calls})
        for call in calls:
            output = call.get("output") or {}
            steps.append({
                "type": "TOOL_OUTPUT",
                "content": output.get("content", ""),
                "tool_call_id": call["id"],
                "metadata": {"exit_code": output.get("exit_code", 0)},
            })
    steps.append({"type": "PLANNER_RESPONSE", "content": pair["agent_reply"], "tool_calls": []})
    return steps


def _derive(answers: dict, categories: dict) -> dict:
    """Local compass verdict: label-driven, shared with the runtime.

    failed <=> at least one H-route label clears its floor. q_pass is kept as
    logged completion confidence only: v6/v7 showed it straddling 0.75 on
    sloppy-but-complete turns (0.70/0.75 across runs), and Kai's invariant is
    that notes never block and optional improvements never justify FAIL. The
    H labels are the incompleteness/defect signals (undone, not_verified, ...).
    Fired labels also split per failure axis (completeness vs quality): the
    axis classifies where a failure lives, the route decides blocking, and one
    axis must never bleed into the other's labels.
    """
    q_pass = answers.get("q_pass")
    verdicts = derive_verdicts({k: v for k, v in answers.items() if k != "q_pass"})
    fired = [{"label": cat, "p": round(score, 2),
              "route": categories[cat]["route"], "axis": axis_of(cat)}
             for cat, score in sorted(verdicts["fired"].items())]
    hard = [f for f in fired if f["route"] == "H"]
    return {
        "verdict": "pass" if not hard else "failed",
        "q_pass": None if q_pass is None else round(q_pass, 2),
        "completion_confidence": None if q_pass is None else round(q_pass, 2),
        "fired": fired,
        "reasons": hard,
        "axes": {axis: sorted(labels) for axis, labels in verdicts["axes"].items()},
    }


def run_jev(pair: dict, categories: dict) -> dict:
    steps = _steps(pair)
    last = prompt_pair.extract_prompt_pair(steps)
    context = {
        "evidence": build_payload(assemble_evidence(steps)),
        "last_user": last["user"],
        "last_agent": last["agent"],
    }
    body = build_request("compass", context)
    started = time.monotonic()
    # Same wall-clock the stop gate gets (sage.jev.verdict.compass's 8s budget), so
    # calibration measures what production allows.
    data = _call_jev(body["state"], body["questions"],
                     attempt_timeout=8.0, deadline=time.monotonic() + 8.0)
    latency = round(time.monotonic() - started, 2)
    keys = ["q_pass"] + list(categories)
    answers = parse_boolean_answers(data, keys)
    if "q_pass" not in answers:
        raise ValueError("q_pass missing from answers")
    verdict = _derive(answers, categories)
    usage = data.get("usage") or {}
    verdict.update(latency_s=latency, coverage=f"{len(answers)}/{len(keys)}",
                   tokens=usage.get("inputTokens"),
                   all_probabilities={k: round(v, 3) for k, v in sorted(answers.items())})
    return verdict


def run_judge(pair: dict, model: str) -> dict:
    """Reference verdict from an agy model; never touches the Jev path."""
    prompt = (
        "Judge this agent turn. USER: {u}\nAGENT: {a}\n"
        'Reply with ONLY JSON: {{"verdict": "pass"|"failed", '
        '"category": "<one of undone|not_verified|incorrect|claim_conflict|'
        'constraint_breach|premature_stop|blast_radius_unchecked|needs_simplification|'
        'delivery_condition|security_privacy|data_integrity|contract_regression|reliability|'
        'performance|usability|test_integrity|faked_evidence|tdd_breach|code_slop|'
        'one_off_hardcode|bad_abstraction|over_engineered_contract|review_bias|null>"}}'
    ).format(u=pair["user_prompt"], a=pair["agent_reply"])
    env = dict(os.environ, AGY_STOP_AUDIT_ACTIVE="1")
    res = subprocess.run(
        [os.environ.get("AGY_BIN") or "agy", "-p", prompt, "--model", model,
         "--disable-slash-commands"],
        input="", capture_output=True, text=True, timeout=45, env=env,
    )
    if res.returncode != 0 or not res.stdout.strip():
        raise RuntimeError(f"judge rc={res.returncode}: {res.stderr[:200]}")
    from sage.executor import extract_json_from_llm_output
    data = extract_json_from_llm_output(res.stdout, schema_keys=("verdict",))
    verdict = str(data.get("verdict") or "").lower()
    if verdict not in ("pass", "failed"):
        raise ValueError(f"judge returned no verdict: {res.stdout[:200]}")
    return {"verdict": verdict, "category": data.get("category")}


def main() -> int:
    argp = argparse.ArgumentParser()
    argp.add_argument("--judge", default="", help="agy model name for reference verdicts")
    argp.add_argument("--json", default="", help="write full results JSON here")
    args = argp.parse_args()

    if not JEV_GATE_API_KEY:
        print("no AGY_JEV_API_KEY (checked env, then ~/.config/agy/sage.env)")
        return 2
    routing = load_routing()
    pairs = routing.get("pairs") or []
    if not pairs:
        print("no fixture pairs in jev.yaml")
        return 2
    categories = routing["categories"]

    results, matched = [], 0
    for pair in pairs:
        row = {"id": pair["id"], "expected": pair["expected"],
               "expect_labels": pair.get("expect_labels") or [],
               "expect_absent": pair.get("expect_absent") or []}
        try:
            row["jev"] = run_jev(pair, categories)
        except Exception as exc:
            row["jev"] = {"verdict": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
        if args.judge:
            try:
                row["judge"] = run_judge(pair, args.judge)
            except Exception as exc:
                row["judge"] = {"verdict": "unavailable", "error": str(exc)[:200]}
        fired_labels = {f["label"] for f in row["jev"].get("fired", [])}
        verdict_ok = row["jev"].get("verdict") == pair["expected"]
        missing = [l for l in row["expect_labels"] if l not in fired_labels]
        spilled = sorted(fired_labels & set(row["expect_absent"]))
        ok = verdict_ok and not missing and not spilled
        row.update(ok=bool(verdict_ok), missing_labels=missing, spilled_labels=spilled)
        matched += ok
        results.append(row)
        fired = ",".join(f"{f['label']}[{f.get('axis', '?')}]={f['p']}"
                         for f in row["jev"].get("fired", [])) or "-"
        judge = f" judge={row['judge']['verdict']}" if "judge" in row else ""
        notes = ""
        if not verdict_ok:
            notes += " verdict_miss"
        if missing:
            notes += f" missing={missing}"
        if spilled:
            notes += f" spilled={spilled}"
        print(f"{'OK ' if ok else 'MISS'} {pair['id']}: expected={pair['expected']}"
              f" got={row['jev'].get('verdict')} q_pass={row['jev'].get('q_pass')}"
              f" fired=[{fired}]{judge}{notes}"
              + (f" err={row['jev'].get('error')}" if row["jev"].get("error") else "")
              + (f" {row['jev'].get('latency_s')}s" if row["jev"].get("latency_s") else ""))

    agree = sum(1 for r in results
                if r.get("judge", {}).get("verdict") == r.get("jev", {}).get("verdict"))
    print(f"\n{matched}/{len(results)} matched expected"
          + (f"; jev/judge agreement {agree}/{len(results)}" if args.judge else ""))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0 if matched == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
