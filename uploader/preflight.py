# -*- coding: utf-8 -*-
"""Pre-upload audio copyright check.

Runs BEFORE the private YouTube upload so we don't burn API quota on a video
that YouTube's Content ID would flag anyway. Uses Chromaprint (`fpcalc`) to
fingerprint the audio track and queries AcoustID for known matches.

Not a substitute for YouTube Content ID — AcoustID's coverage is user-contributed
and skews toward music tracks. Treat a hit as a strong signal, a miss as neutral.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import requests
from loguru import logger

try:
    from conf import ACOUSTID_API_KEY, PREFLIGHT_BLOCK_ON_MATCH, PREFLIGHT_MIN_SCORE
except ImportError:
    ACOUSTID_API_KEY = ""
    PREFLIGHT_BLOCK_ON_MATCH = True
    PREFLIGHT_MIN_SCORE = 0.85

preflight_logger = logger.bind(business_name="preflight")

ACOUSTID_ENDPOINT = "https://api.acoustid.org/v2/lookup"
MIN_DURATION_SECONDS = 15  # AcoustID needs ~15s of audio to match reliably


@dataclass
class PreflightMatch:
    score: float
    recording_id: str
    title: str = ""
    artists: list[str] = field(default_factory=list)

    def label(self) -> str:
        who = ", ".join(self.artists) if self.artists else "unknown"
        return f"{self.title or self.recording_id} — {who} (score={self.score:.2f})"


@dataclass
class PreflightResult:
    ran: bool = False
    blocked: bool = False
    matches: list[PreflightMatch] = field(default_factory=list)
    reason: str = ""

    def summary(self) -> str:
        if not self.ran:
            return f"Preflight skipped: {self.reason}"
        if not self.matches:
            return "Preflight passed: no AcoustID matches."
        head = "BLOCKED" if self.blocked else "WARN"
        lines = [f"Preflight {head}: {len(self.matches)} match(es)"]
        lines.extend(f"  - {m.label()}" for m in self.matches[:5])
        return "\n".join(lines)


def _fpcalc_available() -> bool:
    return shutil.which("fpcalc") is not None


def _run_fpcalc(video: Path) -> tuple[int, str] | None:
    """Return (duration_seconds, fingerprint) or None on failure."""
    try:
        proc = subprocess.run(
            ["fpcalc", "-json", str(video)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        preflight_logger.warning(f"fpcalc timed out on {video.name}")
        return None
    if proc.returncode != 0:
        preflight_logger.warning(f"fpcalc failed on {video.name}: {proc.stderr.strip()}")
        return None
    try:
        data = json.loads(proc.stdout)
        return int(round(float(data["duration"]))), data["fingerprint"]
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        preflight_logger.warning(f"fpcalc output parse failed: {exc}")
        return None


def _lookup_acoustid(duration: int, fingerprint: str, api_key: str, min_score: float) -> list[PreflightMatch]:
    params = {
        "client": api_key,
        "duration": duration,
        "fingerprint": fingerprint,
        "meta": "recordings",
        "format": "json",
    }
    try:
        resp = requests.get(ACOUSTID_ENDPOINT, params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        preflight_logger.warning(f"AcoustID lookup failed: {exc}")
        return []

    if payload.get("status") != "ok":
        preflight_logger.warning(f"AcoustID replied non-ok: {payload}")
        return []

    matches: list[PreflightMatch] = []
    for hit in payload.get("results", []):
        score = float(hit.get("score", 0))
        if score < min_score:
            continue
        recordings = hit.get("recordings") or [{}]
        for rec in recordings:
            artists = [a.get("name", "") for a in rec.get("artists", []) if a.get("name")]
            matches.append(
                PreflightMatch(
                    score=score,
                    recording_id=rec.get("id", hit.get("id", "")),
                    title=rec.get("title", ""),
                    artists=artists,
                )
            )
    return matches


def run_preflight(
    video: Path,
    api_key: str | None = None,
    min_score: float | None = None,
    block_on_match: bool | None = None,
) -> PreflightResult:
    """Fingerprint the audio and query AcoustID. Returns a PreflightResult.

    If fpcalc or the API key is missing, `ran=False` and `blocked=False` —
    callers should treat that as "not checked" and decide their own policy.
    """
    result = PreflightResult()
    key = api_key if api_key is not None else ACOUSTID_API_KEY
    threshold = min_score if min_score is not None else PREFLIGHT_MIN_SCORE
    block = block_on_match if block_on_match is not None else PREFLIGHT_BLOCK_ON_MATCH

    if not key:
        result.reason = "ACOUSTID_API_KEY not set in conf.py"
        return result
    if not _fpcalc_available():
        result.reason = "fpcalc not on PATH (install chromaprint)"
        return result

    fp = _run_fpcalc(video)
    if fp is None:
        result.reason = "fpcalc could not fingerprint this file"
        return result

    duration, fingerprint = fp
    if duration < MIN_DURATION_SECONDS:
        result.reason = f"clip too short for AcoustID ({duration}s < {MIN_DURATION_SECONDS}s)"
        return result
    result.ran = True
    result.matches = _lookup_acoustid(duration, fingerprint, key, threshold)
    result.blocked = block and bool(result.matches)
    return result
