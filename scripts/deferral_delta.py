#!/usr/bin/env python3
"""deferral_delta.py - Live deferral-check stats since a checkpoint, next to the offline baseline.

Reads the Claude stop-audit log (audit.jsonl), keeps records at or after the
checkpoint time, and reports how often the deferral check ran, its verdicts,
hard holds, errors and latency. For each "continue" verdict it reads the next
human message in that session's transcript as a weak outcome proxy: a go-word
("go", "commit đi", "yes") suggests the deferral was needless, a stop-word
("wait", "từ từ", "no") suggests stopping was right.

Usage:
  scripts/deferral_delta.py                       # since the checkpoint in docs/deferral-check/baseline.json
  scripts/deferral_delta.py --since 2026-10-08T12:00:00+00:00 --log /path/audit.jsonl
"""
import argparse
import json
import re
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = REPO / "docs" / "deferral-check" / "baseline.json"
DEFAULT_LOG = Path.home() / ".local" / "state" / "agy-jev-hooks" / "claude-stop-audit" / "audit.jsonl"
GO_RE = re.compile(r"^\s*(go|ok|okay|yes|yep|sure|do it|run it|apply|approve|làm đi|lam di|cứ làm|commit đi|push đi"
                   r"|sửa đi|xoá đi|xóa đi|đồng ý|duyệt|ừ|uh|có)\b", re.IGNORECASE)
STOP_RE = re.compile(r"^\s*(no|nope|wait|hold|stop|khoan|từ từ|tu tu|không|khong|chưa|đừng)\b", re.IGNORECASE)
SKIP_PREFIX = ("<task-notification", "<system-reminder", "Caveat:", "[Request interrupted",
               "This session is being continued", "<command-name>", "<local-command", "Another Claude session")


def parse_ts(text):
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


def next_human(transcript, after):
    """First human text in the transcript written after `after`, or None."""
    try:
        lines = Path(transcript).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") != "user" or not entry.get("timestamp"):
            continue
        if parse_ts(entry["timestamp"]) <= after:
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, list):
            if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                continue
            content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
        text = str(content or "").strip()
        if text and not text.startswith(SKIP_PREFIX):
            return text
    return None


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * q))] if values else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since")
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    args = ap.parse_args()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    since = parse_ts(args.since or baseline["checkpoint"]["ts"])
    rows = []
    for line in Path(args.log).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("ts") and parse_ts(rec["ts"]) >= since and "decision" in rec:
            rows.append(rec)
    checked = [r for r in rows if r.get("deferral")]
    verdicts = Counter(r["deferral"].get("verdict") for r in checked)
    holds = Counter(h for r in checked for h in r["deferral"].get("hard") or [])
    lat = [r["deferral"]["latency_s"] for r in checked if "latency_s" in r["deferral"]]
    proxy = Counter()
    for r in checked:
        if r["deferral"].get("verdict") != "continue":
            continue
        text = next_human(r.get("transcript") or "", parse_ts(r["ts"]))
        proxy["go" if text and GO_RE.search(text) else "stop" if text and STOP_RE.search(text)
              else "other" if text else "no_reply"] += 1
    report = {
        "since": since.isoformat(),
        "stops": len(rows),
        "deferral_checked": len(checked),
        "check_rate": round(len(checked) / len(rows), 3) if rows else None,
        "by_check": dict(Counter(r["deferral"].get("check") for r in checked)),
        "rules_source": dict(Counter(r["deferral"].get("rules") for r in checked)),
        "verdicts": dict(verdicts),
        "hard_holds": dict(holds),
        "latency_s": {"p50": statistics.median(lat) if lat else None, "p95": pct(lat, 0.95), "max": max(lat, default=None)},
        "continue_next_user_proxy": dict(proxy),
        "baseline_offline": baseline["offline"],
    }
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
