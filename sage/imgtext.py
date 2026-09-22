"""
sage.imgtext - Screenshot OCR and symptom flags for receipt content checks.

A screenshot can contradict the claim it backs: "done" while the image shows
NaN and skeletons. Tesseract CLI (system binary, no Python deps) turns image
receipts into text, and the symptom scanner flags known failure strings so
verdict routing judges what the image shows, not that an image exists.
"""
import base64
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Tuple

MAX_IMAGES = 3
MAX_IMAGE_BYTES = 6 * 1024 * 1024
OCR_TIME_BUDGET_S = 6.0
OCR_CHAR_CAP = 4000

_IMG_PATH_RE = re.compile(r"(/[\w./ -]+?\.(?:png|jpe?g|webp|bmp|tiff?))\b", re.I)
_TESSERACT_CANDIDATES = ("tesseract", "/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract")

_SYMPTOMS = (
    (re.compile(r"\[object Object\]"), "[object Object]"),
    (re.compile(r"\bNaN\b"), "NaN"),
    (re.compile(r"\bundefined\b"), "undefined"),
    (re.compile(r"something went wrong|an error occurred|internal server error", re.I), "error banner"),
    (re.compile(r"^\s*loading(?:\.\.\.|…)?\s*$|skeleton", re.I | re.M), "unrendered/loading"),
    (re.compile(r"rate limit|too many requests|\b429\b", re.I), "rate limited"),
    (re.compile(r"sign in|log in|session expired|unauthorized", re.I), "auth screen"),
    (re.compile(r"traceback \(most recent call last\)|uncaught \w*error", re.I), "runtime error"),
)


def _tesseract_bin() -> str:
    for candidate in _TESSERACT_CANDIDATES:
        found = shutil.which(candidate) if os.sep not in candidate else (candidate if os.path.isfile(candidate) else None)
        if found:
            return found
    return ""


def ocr_image(path: str) -> str:
    """Raw text from one image receipt; empty on any failure."""
    binary = _tesseract_bin()
    if not binary or not os.path.isfile(path):
        return ""
    try:
        done = subprocess.run([binary, path, "stdout"],
                              capture_output=True, text=True, timeout=OCR_TIME_BUDGET_S)
        return (done.stdout or "").strip()[:OCR_CHAR_CAP] if done.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def find_image_paths(steps: List[Dict[str, Any]]) -> List[str]:
    """Existing image paths from tool args and text, deduped, newest last."""
    found: List[str] = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        pools = [str(step.get("content") or "")]
        calls = step.get("tool_calls")
        for call in calls if isinstance(calls, list) else []:
            if isinstance(call, dict):
                pools.append(str(call.get("args") or call.get("arguments") or ""))
        for pool in pools:
            for match in _IMG_PATH_RE.findall(pool):
                if os.path.isfile(match) and match not in found:
                    found.append(match)
    return found[-MAX_IMAGES:]


def symptom_flags(text: str) -> List[str]:
    """Known failure strings present in receipt or screenshot text."""
    return [label for pattern, label in _SYMPTOMS if pattern.search(str(text or ""))]


def find_image_data(steps: List[Dict[str, Any]]) -> List[str]:
    """Base64 image payloads from normalized steps, bounded."""
    found: List[str] = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        for data in step.get("images") or []:
            if isinstance(data, str) and data and data not in found:
                found.append(data)
    return found[:MAX_IMAGES]


def _decode_image(data: str) -> str:
    """Write embedded image bytes to a temp file; empty on any failure."""
    try:
        raw = base64.b64decode(data)
    except (ValueError, TypeError):
        return ""
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        return ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(raw)
            return tmp.name
    except OSError:
        return ""


def visual_text_and_flags(steps: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
    """OCR text of screenshot receipts (paths and embedded) plus symptom flags."""
    chunks: List[str] = []
    temps: List[str] = []
    targets = list(find_image_paths(steps))
    for data in find_image_data(steps):
        decoded = _decode_image(data)
        if decoded:
            temps.append(decoded)
            targets.append(decoded)
    try:
        for path in targets[:MAX_IMAGES]:
            text = ocr_image(path)
            if text:
                label = os.path.basename(path) if path not in temps else "<embedded image>"
                chunks.append(f"[screenshot: {label}]\n{text}")
    finally:
        for temp_path in temps:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
    blob = "\n".join(chunks)
    return blob, symptom_flags(blob)
