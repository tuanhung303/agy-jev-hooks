"""sage.jev.config.catalog - Category routing and narrative pairs, loaded from jev.yaml.

The routing section of jev.yaml is the single source of truth: each category
binds a routing
class (H hard-escalate / N note-only / A auto-route) and a closed narrative
template. COMPASS_CATEGORIES and ROUTE_FLOORS keep their historical shape so
callers and tests do not change.

Narrative slots: E evidence, R requirement, T target/version, C claim,
K existing check, D affected dependency. Unfilled slots render <unassigned>.
"""
from pathlib import Path
from typing import Dict, Tuple

from sage.jev.config import yaml_lite

ROUTE_HARD = "H"
ROUTE_NOTE = "N"
ROUTE_AUTO = "A"

# Single config file for the whole Jev stack: cases + routing + skills.
JEVS_PATH = Path(__file__).resolve().parent.parent / "jev.yaml"
ROUTING_PATH = JEVS_PATH  # legacy alias used by tests/tools


def load_jevs(path: Path = JEVS_PATH) -> dict:
    """Parse jev.yaml; a non-mapping document is a hard error."""
    with path.open(encoding="utf-8") as handle:
        data = yaml_lite.safe_load(handle.read())
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a mapping")
    return data


def load_routing(path: Path = JEVS_PATH) -> dict:
    """Return the routing section; missing categories/routes/axes is a hard error.

    Uses the hermetic yaml_lite loader: hook subprocesses isolate HOME and
    lose the user-site PyYAML, so runtime code must not import yaml.
    """
    data = load_jevs(path)
    routing = data.get("routing")
    if not isinstance(routing, dict) or not isinstance(routing.get("categories"), dict) \
            or not isinstance(routing.get("routes"), dict):
        raise ValueError(f"{path} missing routing.categories/routes sections")
    for name, spec in routing["categories"].items():
        for key in ("route", "criterion", "narrative"):
            if not isinstance(spec, dict) or not isinstance(spec.get(key), str) or not spec[key]:
                raise ValueError(f"category {name!r} missing {key!r}")
        if spec["route"] not in routing["routes"]:
            raise ValueError(f"category {name!r} routes to unknown class {spec['route']!r}")
    axes = routing.get("axes")
    if isinstance(axes, dict) and axes:
        assigned = [name for spec in axes.values() for name in (spec.get("labels") or [])]
        if len(assigned) != len(set(assigned)):
            raise ValueError(f"{path} assigns a category to more than one axis")
        missing = set(routing["categories"]) - set(assigned)
        unknown = set(assigned) - set(routing["categories"])
        if missing or unknown:
            raise ValueError(f"axis labels mismatch: missing={sorted(missing)} unknown={sorted(unknown)}")
    return routing


_DATA = load_routing()

ROUTE_FLOORS = {code: float(spec["floor"]) for code, spec in _DATA["routes"].items()}

# Axis partition: every category belongs to exactly one failure axis. The
# axis classifies where a failure lives; the route class H/N/A keeps deciding
# whether a fired label may steer the stop.
AXIS_NAMES: Tuple[str, ...] = tuple((_DATA.get("axes") or {}).keys())
_AXIS_OF: Dict[str, str] = {
    name: axis for axis, spec in (_DATA.get("axes") or {}).items()
    for name in (spec.get("labels") or [])
}

COMPASS_CATEGORIES: Dict[str, dict] = {
    name: {
        "route": spec["route"],
        "criterion": spec["criterion"],
        "narrative": spec["narrative"],
        "axis": _AXIS_OF.get(name, ""),
    }
    for name, spec in _DATA["categories"].items()
}


def axis_of(category: str) -> str:
    return _AXIS_OF.get(category, "")


def derive_verdicts(labels: Dict[str, float]) -> dict:
    """Apply route floors, then split fired labels per axis.

    Blocking stays route-driven (H hard-escalate); the axis only records
    whether the failure is missing/unproven delivery (completeness) or a
    defect in present work (quality). Both axes may fire on one turn.
    """
    fired = {
        cat: score for cat, score in labels.items()
        if cat in COMPASS_CATEGORIES and score >= ROUTE_FLOORS[COMPASS_CATEGORIES[cat]["route"]]
    }
    axes: Dict[str, Dict[str, float]] = {axis: {} for axis in AXIS_NAMES}
    for cat, score in fired.items():
        axes.setdefault(axis_of(cat), {})[cat] = score
    return {
        "fired": fired,
        "axes": axes,
        "hard_escalate": sorted(c for c in fired if COMPASS_CATEGORIES[c]["route"] == "H"),
        "notes": sorted(c for c in fired if COMPASS_CATEGORIES[c]["route"] == "N"),
        "auto_route": sorted(c for c in fired if COMPASS_CATEGORIES[c]["route"] == "A"),
    }
