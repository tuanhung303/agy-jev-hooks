#!/usr/bin/env python3
"""Jev prompt bench for the grep hint hook.

Ranks the grep matches of each labelled case in cases.json with every prompt arm in arms.py and
reports how well the known answers rank. The searched code is a `git archive` snapshot of this
repository at a pinned commit (--ref), so results stay comparable while the repository changes;
the prompt and hook code under test are the working tree's.

Sends the snapshot's matching code to Jev through the provider of the agy-jev-hooks jevgrep
profile, about 25 s per arm per run. The score cache is off: it keys on version strings, not on
the prompt text.

  .venv/bin/python bench/jev_prompt/run.py                          # every arm, 2 runs
  .venv/bin/python bench/jev_prompt/run.py --arms shipped --runs 1
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

PINNED_REF = "a05a4fd"  # changing it changes every number: rerun `shipped` for a new reference
PROFILE = Path.home() / ".config/jevgrep/agy-jev-hooks.yaml"
RESULTS = Path.home() / ".local/state/agy-jev-hooks/bench"


def load_hook():
    spec = importlib.util.spec_from_file_location("claude_grep_filter", REPO / "hooks/claude-grep-filter.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot(ref, workdir):
    """The tracked files at ref, plus a jevgrep profile for them outside the snapshot."""
    corpus = workdir / "corpus"
    corpus.mkdir()
    archive = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", ref],
                             check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(corpus)], input=archive, check=True)
    corpus = Path(os.path.realpath(corpus))
    config_home = workdir / "config"
    config_home.mkdir()
    profile = re.sub(r"(?m)^repository_root: .*$", f"repository_root: {corpus}",
                     PROFILE.read_text(encoding="utf-8"))
    (config_home / "bench.yaml").write_text(profile, encoding="utf-8")
    os.environ["JEVGREP_CONFIG_HOME"] = str(config_home)
    os.environ["JEVGREP_CACHE_HOME"] = str(workdir / "cache")
    return corpus


def gateway_builder(arm, original):
    if not {"question", "true", "false"} & set(arm):
        return original  # exactly what the live adapter sends
    from mcp.jev.grep import jev
    question = arm.get("question", jev.RELEVANCE_QUESTION)
    criteria = {"true": arm.get("true", jev.RELEVANCE_CRITERION), "false": arm.get("false", jev.FALSE_CRITERION)}

    def build(batch):
        return {"state": {"search_question": batch.query},
                "questions": {item.id: {"type": "boolean",
                                        "instructions": {"question": question,
                                                         "excerpt": jev.question_instructions(item)["excerpt"]},
                                        "criteria": criteria} for item in batch.items}}
    return build


def auc(best, truth):
    positive = [score for path, score in best.items() if path in truth]
    negative = [score for path, score in best.items() if path not in truth]
    if not positive or not negative:
        return None
    wins = sum((a > b) + 0.5 * (a == b) for a in positive for b in negative)
    return round(wins / (len(positive) * len(negative)), 3)


def run_case(hook, case, corpus, build_query):
    from mcp.jev.grep.config import find_profile_for, load_configuration
    from mcp.jev.grep.engine import SearchEngine
    matches, _ = hook.grep_matches(["-e", case["pattern"]], [str(corpus)])
    loaded = load_configuration(find_profile_for(cwd=str(corpus)))
    root = loaded["repository_root"]
    lines = {os.path.relpath(os.path.realpath(path), root): found for path, found in matches.items()}
    config = loaded["config"]
    config["search"]["deadline_ms"] = 120_000
    config["search"]["concurrency"] = hook.CONCURRENCY  # as the live hook sends
    config["scan_caps"]["transmitted_bytes"] = 2_000_000
    config["cache"]["enabled"] = False
    started = time.time()
    result = SearchEngine(loaded, env=dict(os.environ)).search(
        {"query": build_query(case["prompts"], case["pattern"], case.get("purpose", "")),
         "scope": hook.cover(lines), "max_context_tokens": config["search"]["max_response_tokens"]},
        {"fragment_map": hook.snippet_mapper(lines, hook.SNIPPET_LINES, per_file=hook.SNIPPETS_PER_FILE)})
    report = result["outcome"].get("report") or {}
    best = {}
    for path, _, _, score in result.get("fragment_scores") or ():
        best[path] = max(score, best.get(path, 0.0))
    ranked = sorted(best, key=lambda path: -best[path])
    truth = set(case["relevant"])
    return {"status": result["outcome"].get("status"), "seconds": round(time.time() - started, 1),
            "kb": round(((report.get("usage") or {}).get("transmitted_bytes") or 0) / 1024),
            "files": len(best), "auc": auc(best, truth),
            "ranks": sorted(ranked.index(path) + 1 for path in truth if path in ranked)}


def summarize(rows, arms, runs):
    print(f"{'arm':16} run  mean AUC  top3  top8     KB  per-case AUC")
    for name in arms:
        for run in range(1, runs + 1):
            picked = [row for row in rows if row["arm"] == name and row["run"] == run]
            scored = [row["auc"] for row in picked if row["auc"] is not None]
            top = lambda k: sum(1 for row in picked for rank in row["ranks"] if rank <= k)
            total = sum(len(row["relevant"]) for row in picked)
            print(f"{name:16} {run:>3}  {sum(scored) / max(len(scored), 1):8.3f}  {top(3):>2}/{total}  "
                  f"{top(8):>2}/{total}  {sum(row['kb'] for row in picked):5}  "
                  + " ".join(f"{row['auc']:.2f}" if row["auc"] is not None else "-" for row in picked))


def main():
    from arms import ARMS
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ref", default=PINNED_REF, help="commit whose code is searched")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--arms", default=",".join(ARMS), help="comma-separated arm names from arms.py")
    parser.add_argument("--out", help="result JSON (default: ~/.local/state/agy-jev-hooks/bench/)")
    args = parser.parse_args()
    names = [name for name in args.arms.split(",") if name]
    unknown = [name for name in names if name not in ARMS]
    if unknown:
        parser.error(f"unknown arms: {', '.join(unknown)}")

    from mcp.jev.grep import vercel_gateway
    hook = load_hook()
    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    original = vercel_gateway._build_gateway_input
    rows = []
    with tempfile.TemporaryDirectory(prefix="jev-bench-") as workdir:
        corpus = snapshot(args.ref, Path(workdir))
        for run in range(1, args.runs + 1):
            for case in cases:
                for name in names:
                    arm = ARMS[name]
                    vercel_gateway._build_gateway_input = gateway_builder(arm, original)
                    try:
                        row = run_case(hook, case, corpus, arm.get("build_query", hook.build_query))
                    finally:
                        vercel_gateway._build_gateway_input = original
                    row.update({"run": run, "arm": name, "case": case["name"], "relevant": case["relevant"]})
                    rows.append(row)
                    if row["status"] != "complete":
                        print(f"warning: {name} / {case['name']} run {run}: {row['status']}", file=sys.stderr)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else RESULTS / f"jev-prompt-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"ref": args.ref, "arms": {n: ARMS[n] for n in names if "build_query" not in ARMS[n]},
                               "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
    summarize(rows, names, args.runs)
    print(f"\nresults: {out}")
    return 0 if all(row["status"] == "complete" for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
