"""
sage.skillpack - Singleton light-skill pack: manifest loading and content injection.

skills/skills.yaml maps each skill id to one self-contained markdown file plus
its source ref (community URL or local path) for repopulation. Prompt-time use
stays a one-line suggestion; full content is injected only at stop, so a turn
is never forced through a skill.
"""
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from sage.jev.config import yaml_lite
except Exception:
    yaml_lite = None

CONTENT_CHAR_CAP = 5000  # safety net against runaway files; real stop payload
# stays under the hook's SKILL_TEXT_LIMIT, and prompt-time never gets content.
FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n?", re.S)


def _pack_roots() -> List[Path]:
    import os
    roots = []
    override = os.environ.get("AGY_SKILL_PACK")
    if override:
        roots.append(Path(override))
    roots.append(Path(__file__).resolve().parent.parent / "skills")
    roots.append(Path.home() / ".config" / "agy" / "skills")
    return roots


def _find_pack_dir() -> Optional[Path]:
    for root in _pack_roots():
        if (root / "skills.yaml").is_file():
            return root
    return None


def load_pack() -> Dict[str, Dict[str, Any]]:
    """Manifest entries by id; malformed entries are dropped, never raised."""
    pack_dir = _find_pack_dir()
    if pack_dir is None or yaml_lite is None:
        return {}
    try:
        data = yaml_lite.safe_load((pack_dir / "skills.yaml").read_text(encoding="utf-8"))
    except Exception:
        return {}
    entries: Dict[str, Dict[str, Any]] = {}
    for entry in (data or {}).get("skills") if isinstance(data, dict) else []:
        if isinstance(entry, dict) and entry.get("id") and entry.get("file"):
            entry["_dir"] = pack_dir
            entries[str(entry["id"])] = entry
    return entries


def skill_markdown(skill_id: str) -> str:
    """Singleton markdown body (frontmatter stripped), bounded for injection."""
    entry = load_pack().get(skill_id)
    if not entry:
        return ""
    try:
        text = (Path(entry["_dir"]) / str(entry["file"])).read_text(encoding="utf-8")
    except OSError:
        return ""
    text = FRONTMATTER_RE.sub("", text).strip()
    return text[:CONTENT_CHAR_CAP]


def render_content_inject(skill_id: str) -> str:
    """Skill link plus its singleton content for stop-time injection."""
    body = skill_markdown(skill_id)
    if not body:
        return ""
    return f"[@{skill_id}](skill://{skill_id})\n\n{body}"


def match_stop_skill(text: str, phase: str = "stop") -> Optional[str]:
    """First manifest entry whose triggers hit the turn text and inject phase."""
    low = str(text or "").lower()
    if not low:
        return None
    for skill_id, entry in load_pack().items():
        phases = str(entry.get("inject") or "").replace(",", " ").split()
        if phase not in phases and "both" not in phases:
            continue
        for trigger in entry.get("triggers") or []:
            # Word-boundary match (plural tolerated): "tdd" must hit "do TDD"
            # and "fix the bugs" but never "tdd_breach" or "boosted".
            pattern = rf"(?<![a-z0-9_]){re.escape(str(trigger).lower())}s?(?![a-z0-9_])"
            if re.search(pattern, low):
                return skill_id
    return None


def stop_skill_steer(prompt: str, reply: str) -> Optional[str]:
    """Content steer for a stop-phase skill when its trigger hits the turn."""
    skill_id = match_stop_skill(f"{prompt}\n{reply}")
    if not skill_id:
        return None
    return render_content_inject(skill_id) or None
