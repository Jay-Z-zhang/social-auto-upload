# -*- coding: utf-8 -*-
"""Slice a long episode into a YouTube Shorts variant.

Given `foo.mp4` at say 5 minutes, produce `foo_short.mp4` at 60 seconds
suitable for YouTube Shorts (must be ≤ 60s and 9:16). We keep the crop
at the source aspect ratio — reframing horizontal to vertical is a
creative decision, not something to hide inside a batch step. If your
source is already 9:16 (typical for short drama), this just clips duration.

Also enforces YT Shorts hard rules:
- Duration ≤ 60s (we default to 59s to be safe against ffmpeg rounding)
- Container mp4, faststart, yuv420p — same profile as tt_prep
- Re-encodes so the shorts variant has its own fingerprint from the original
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

split_logger = logger.bind(business_name="split")

DEFAULT_OUTPUT_SUFFIX = "_short"
DEFAULT_SHORTS_DURATION = 59  # keep 1s margin under YT's 60s hard cap


@dataclass
class SplitResult:
    input: Path
    output: Path
    duration_seconds: int
    source_duration_seconds: float
    performed: bool  # False when source was already short enough → output == input


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _probe_duration(src: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()}")
    return float(proc.stdout.strip())


def default_short_path(src: Path) -> Path:
    return src.with_name(f"{src.stem}{DEFAULT_OUTPUT_SUFFIX}.mp4")


def split_shorts(
    src: Path,
    duration: int = DEFAULT_SHORTS_DURATION,
    out: Path | None = None,
    overwrite: bool = False,
) -> SplitResult:
    """Create a Shorts-length variant of `src`. Returns a SplitResult.

    If the source is already shorter than `duration + 1s`, no work is done
    and `performed=False`; the "output" points at `src` itself so callers
    can uniformly hand it to the uploader.
    """
    if not _ffmpeg_available():
        raise RuntimeError("ffmpeg/ffprobe not on PATH — install ffmpeg.")
    if duration < 15:
        raise ValueError("Shorts must be at least 15s.")
    if duration > 60:
        raise ValueError("YouTube Shorts must be ≤ 60s.")

    src_dur = _probe_duration(src)
    if src_dur <= duration + 1:
        split_logger.info(f"{src.name} is already {src_dur:.1f}s — no split needed.")
        return SplitResult(
            input=src, output=src, duration_seconds=int(src_dur),
            source_duration_seconds=src_dur, performed=False,
        )

    dst = out if out is not None else default_short_path(src)
    if dst.exists() and not overwrite:
        split_logger.info(f"Reusing existing {dst.name} (pass overwrite=True to regen).")
        return SplitResult(
            input=src, output=dst, duration_seconds=duration,
            source_duration_seconds=src_dur, performed=True,
        )

    cmd = [
        "ffmpeg",
        "-y" if overwrite else "-n",
        "-i", str(src),
        "-t", str(duration),
        "-map_metadata", "-1",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(dst),
    ]

    split_logger.info(f"Slicing {src.name} first {duration}s → {dst.name}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        if dst.exists() and "already exists" in proc.stderr.lower():
            return SplitResult(
                input=src, output=dst, duration_seconds=duration,
                source_duration_seconds=src_dur, performed=True,
            )
        tail = "\n".join(proc.stderr.strip().splitlines()[-6:])
        raise RuntimeError(f"ffmpeg failed:\n{tail}")

    return SplitResult(
        input=src, output=dst, duration_seconds=duration,
        source_duration_seconds=src_dur, performed=True,
    )
