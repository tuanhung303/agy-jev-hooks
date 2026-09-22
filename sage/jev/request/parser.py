"""sage.jev.request.parser - Build spec-v4 Jev request bodies from jev.yaml.

jev.yaml holds everything: per-call prompt structures (cases), routing +
category (criterion, narrative) pairs + prompt-pair config + fixture pairs
(routing), and the skill route table (skills). `{{placeholder}}` templates
are filled from the caller's context dict; a missing placeholder raises
instead of shipping an unfinished prompt. Choice questions take criteria
from the context key named by "$key", falling back to criteria_default.
Bodies over the case's exact request_char_cap raise (never estimate: a
460KB request once slipped an estimated cap).
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from sage.jev.config.catalog import JEVS_PATH, axis_of, load_routing

CASES_PATH = JEVS_PATH  # legacy alias: one file now holds every case
_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def load_cases(path: Path = CASES_PATH) -> Dict[str, Any]:
    """Parse jev.yaml once; case names are the keys under `cases` (or top
    level for legacy single-purpose files)."""
    from sage.jev.config.catalog import load_jevs
    data = load_jevs(path)
    cases = data.get("cases")
    if not isinstance(cases, dict):
        cases = {k: v for k, v in data.items() if k not in ("version", "routing", "skills")}
    if not cases:
        raise ValueError(f"{path} declares no cases")
    return cases


def _validate_case(name: str, case: Any) -> Dict[str, Any]:
    if not isinstance(case, dict):
        raise ValueError(f"case {name!r} is not a mapping")
    for key in ("state", "questions", "budgets", "parse"):
        if key not in case:
            raise ValueError(f"case {name!r} missing {key!r}")
    return case


def load_case(name: str, cases_path: Path = CASES_PATH) -> Dict[str, Any]:
    """Load and minimally validate one case structure."""
    cases = load_cases(cases_path)
    if name not in cases:
        raise ValueError(f"case {name!r} not declared in {cases_path}")
    return _validate_case(name, cases[name])


def _fill(template: str, context: Dict[str, Any]) -> str:
    missing = []

    def sub(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in context:
            missing.append(key)
            return match.group(0)
        return str(context[key])

    out = _PLACEHOLDER.sub(sub, template)
    if missing:
        raise ValueError(f"missing placeholders: {sorted(set(missing))}")
    return out


def _build_question(spec: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    raw = spec.get("instructions")
    if raw is None:
        raw = spec.get("instructions_template")
    if raw is None:
        raise ValueError("question spec lacks instructions or instructions_template")
    question: Dict[str, Any] = {"type": spec["type"], "instructions": _fill(raw, context)}
    if spec["type"] == "choice":
        ref = spec.get("criteria")
        if isinstance(ref, str) and ref.startswith("$"):
            criteria = context.get(ref[1:])
            if not isinstance(criteria, dict) or not criteria:
                criteria = spec.get("criteria_default")
                if not isinstance(criteria, dict) or not criteria:
                    raise ValueError(f"missing context criteria {ref!r} and no criteria_default")
            question["criteria"] = dict(criteria)
        elif isinstance(ref, dict):
            question["criteria"] = dict(ref)
        else:
            default = spec.get("criteria_default")
            if not isinstance(default, dict) or not default:
                raise ValueError("choice question lacks criteria")
            question["criteria"] = dict(default)
    return question


def build_request(
    case_name: str,
    context: Optional[Dict[str, Any]] = None,
    routing: Optional[dict] = None,
) -> Dict[str, Any]:
    """Assemble {state, questions, providerOptions}; enforce the exact cap."""
    case = load_case(case_name)
    routing = routing if isinstance(routing, dict) else load_routing()
    ctx: Dict[str, Any] = dict(context or {})

    state = {}
    for key, value in case["state"].items():
        state[key] = _fill(value, ctx) if isinstance(value, str) else value

    questions: Dict[str, Any] = {}
    for qid, spec in case["questions"].items():
        if qid.startswith("@"):
            source = spec.get("instructions_from", "")
            if source != "routing.categories.*.criterion":
                raise ValueError(f"unsupported question expansion source: {source!r}")
            for category, cat_spec in routing["categories"].items():
                # Short axis tag only: a long per-question scope paragraph
                # buries the criterion and misbinds the judged proposition.
                instructions = f"[{axis_of(category)}] {cat_spec['criterion']}"
                questions[category] = {"type": spec["type"], "instructions": instructions}
            continue
        questions[qid] = _build_question(spec, ctx)

    body = {"state": state, "questions": questions, "providerOptions": {}}
    cap = int(case["budgets"].get("request_char_cap") or 0)
    if cap and len(json.dumps(body)) > cap:
        raise ValueError(f"serialized request over cap: cap={cap}")
    return body


def parse_probability(value: Any) -> Optional[float]:
    """Finite real in [0, 1]; booleans, strings and NaN/inf fail."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")) or not 0.0 <= out <= 1.0:
        return None
    return out


def parse_boolean_answers(data: Any, keys: list) -> Dict[str, float]:
    """Extract typed boolean probabilities; malformed answers are dropped."""
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        raise ValueError("missing answers record")
    out: Dict[str, float] = {}
    for key in keys:
        answer = answers.get(key)
        if not isinstance(answer, dict) or answer.get("type") != "boolean":
            continue
        score = parse_probability(answer.get("probability"))
        if score is not None:
            out[key] = score
    return out


def parse_choice_answer(data: Any, key: str, valid_keys: set) -> Optional[tuple]:
    """Return (choice, probability) or None; choice must be in valid_keys."""
    answers = data.get("answers") if isinstance(data, dict) else None
    answer = answers.get(key) if isinstance(answers, dict) else None
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        return None
    choice = answer.get("choice")
    if choice not in valid_keys:
        return None
    probabilities = answer.get("probabilities")
    score = parse_probability(probabilities.get(choice)) if isinstance(probabilities, dict) else None
    if score is None:
        return None
    return choice, score
