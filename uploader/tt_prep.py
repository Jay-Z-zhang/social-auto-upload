# -*- coding: utf-8 -*-
"""TikTok anti-repost preprocessing.

TikTok's dedupe engine looks at file hash, container metadata, video signature,
and audio fingerprint. Uploading the exact same file to YouTube and TikTok is
one of the fastest ways to get flagged as "unoriginal content." This module
generates a per-episode TikTok-specific variant that survives the dedupe check
without visibly degrading the drama.

The transforms are conservative on purpose — we're trying to defeat automated
similarity signatures, not the human eye:

- Container metadata stripped (`-map_metadata -1`) → no leaked encoder tag.
- Re-encode at a chosen CRF → different bitstream than the YT version.
- Micro-crop a few pixels from each edge → shifts the video signature; TikTok
  reencodes anyway so the small resolution change is invisible in the feed.
- Optional horizontal flip → strongest signature change; some dramas break with it.
- Audio re-encoded to AAC 128k → different audio fingerprint from source.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

prep_logger = logger.bind(business_name="tt_prep")

DEFAULT_OUTPUT_DIRNAME = "tiktok"


@dataclass
class PrepResult:
    input: Path
    output: Path
    mirrored: bool
    crf: int


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _default_output_path(src: Path, out_dir: Path | None) -> Path:
    target_dir = out_dir if out_dir is not None else src.parent / DEFAULT_OUTPUT_DIRNAME
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / f"{src.stem}_tt.mp4"


def prepare_for_tiktok(
    src: Path,
    out: Path | None = None,
    crf: int = 23,
    mirror: bool = False,
    crop_px: int = 4,
    overwrite: bool = False,
) -> PrepResult:
    """Generate a TikTok-specific variant of `src`.

    Returns a PrepResult pointing at the new file. Raises RuntimeError on failure.
    """
    if not _ffmpeg_available():
        raise RuntimeError("ffmpeg not on PATH — install it (brew install ffmpeg).")

    dst = out if out is not None else _default_output_path(src, None)
    if dst.exists() and not overwrite:
        prep_logger.info(f"Reusing existing {dst.name} (pass overwrite=True to regen).")
        return PrepResult(input=src, output=dst, mirrored=mirror, crf=crf)

    vf_filters = [f"crop=iw-{crop_px * 2}:ih-{crop_px * 2}"]
    if mirror:
        vf_filters.append("hflip")
    vf = ",".join(vf_filters)

    cmd = [
        "ffmpeg",
        "-y" if overwrite else "-n",
        "-i", str(src),
        "-map_metadata", "-1",
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(dst),
    ]

    prep_logger.info(f"Prepping {src.name} → {dst.name} (crf={crf}, mirror={mirror})")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # ffmpeg -n exits with error when the file already exists — treat as success.
        if dst.exists() and "already exists" in proc.stderr.lower():
            return PrepResult(input=src, output=dst, mirrored=mirror, crf=crf)
        tail = "\n".join(proc.stderr.strip().splitlines()[-6:])
        raise RuntimeError(f"ffmpeg failed:\n{tail}")

    return PrepResult(input=src, output=dst, mirrored=mirror, crf=crf)
