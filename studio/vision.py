"""A small visual tie-breaker for candidate clips.

The transcript and reaction judges still choose complete moments. This module only looks at three frames from an
already-valid candidate and boosts moments with a clear human reaction: affection, joy, shock, rage, tension or
embarrassment. If pictures are unavailable, the model is text-only, or the answer is unclear, it returns nothing
and the original score is kept.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import ai, usage
from .media import NO_WINDOW

PROMPT = """Three frames from one candidate short-form clip are attached, in time order from left to right.
Judge only visible actions and facial expressions. Look for a clear, shareable human reaction: affection or warmth,
joy or laughter, surprise or shock, anger or rage, aggressive confrontation, tension, crying, embarrassment or a
strong facecam reaction. Ordinary neutral talking, a posed face, gameplay without a visible person, and unclear
frames are not signals. Do not infer private traits, relationships or intent that cannot be seen.

Answer with exactly one line:
SIGNAL score=0-100 category=affection|joy|shock|rage|conflict|tension|embarrassment|sadness|other reason=short reason
or:
NO SIGNAL"""

_ANSWER = re.compile(
    r"\bSIGNAL\s+score\s*=\s*(\d{1,3})\s+category\s*=\s*([a-z]+)\s+reason\s*=\s*(.{1,180})",
    re.I,
)
_CATEGORIES = {"affection", "joy", "shock", "rage", "conflict", "tension", "embarrassment", "sadness", "other"}


def _parse(text: str) -> dict | None:
    if re.search(r"\bNO\s+SIGNAL\b", text or "", re.I):
        return None
    m = _ANSWER.search((text or "").replace("\n", " "))
    if not m:
        return None
    category = m.group(2).lower()
    if category not in _CATEGORIES:
        category = "other"
    reason = re.sub(r"[\x00-\x1f\x7f]+", " ", m.group(3)).strip()[:180]
    return {"score": max(0, min(100, int(m.group(1)))), "category": category, "reason": reason}


def contact_sheet(path: str | Path, start: float, end: float, width: int = 360) -> bytes:
    """Three evenly-spaced frames as one JPEG strip, or empty bytes when ffmpeg cannot read the video."""
    dur = max(0.6, float(end) - float(start))
    try:
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, float(start)):.2f}", "-t", f"{dur:.2f}",
             "-i", str(path), "-vf", f"fps=3/{dur:.3f},scale={width}:-2,tile=3x1:padding=4:margin=4",
             "-frames:v", "1", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
            capture_output=True, creationflags=NO_WINDOW, timeout=120,
        )
    except Exception:  # noqa: BLE001 - visual review is optional and must never cost the clip
        return b""
    return p.stdout or b"" if p.returncode == 0 else b""


def review(path: str | Path, start: float, end: float, timeout=None) -> dict | None:
    """Return a visible-reaction score for one candidate, or None without changing the main pipeline."""
    image = contact_sheet(path, start, end)
    if not image:
        return None
    try:
        text, sent, wrote = ai.see(PROMPT, image, timeout)
    except Exception:  # noqa: BLE001 - text/loudness selection remains the safe fallback
        return None
    if sent or wrote:
        usage.record(sent, wrote, "Video: visible reaction check")
    return _parse(text)


def apply(candidate: dict, result: dict | None) -> dict:
    """Boost, never punish, a transcript-approved candidate with a strong visible reaction."""
    if not result:
        return candidate
    candidate["visual"] = result
    score = int(result.get("score") or 0)
    if score >= 60:
        candidate["score"] = min(100, int(candidate.get("score") or 0) + min(10, max(2, round((score - 50) / 5))))
        mapped = {"joy": "reaction", "tension": "conflict", "embarrassment": "cringe", "sadness": "story"}
        category = mapped.get(result.get("category"), result.get("category"))
        if category in {"affection", "reaction", "shock", "rage", "conflict", "cringe", "story"}:
            candidate["category"] = category
        if result.get("reason"):
            candidate["reason"] = (str(candidate.get("reason") or "").rstrip(".") +
                                   ". Visible signal: " + str(result["reason"]).rstrip(".") + ".").lstrip(". ")[:200]
    return candidate
