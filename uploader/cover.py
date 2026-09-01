# -*- coding: utf-8 -*-
"""Auto-generate YouTube-friendly cover art for a drama episode.

Pipeline: sample one frame with ffmpeg → darken it slightly → overlay a large
`EP xx` block and (optional) series name. Output is a 1280×720 JPEG suitable
for `youtube.thumbnails.set` (must be < 2MB, jpg/png).

Design goals — same look across every episode of a series:

- One font stack, one weight, one color.
- EP number is the loudest thing on the frame — that's what drives click-through
  when the audience is skimming a playlist.
- Series name is the second read; kept smaller and top-anchored.
- A darkened bottom strip carries the text so it stays legible over any frame.

The style is intentionally minimal. If you want fancier covers, generate them
in Figma and drop the file at `<video_stem>_cover.jpg` — we'll pick it up.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

cover_logger = logger.bind(business_name="cover")

TARGET_WIDTH = 1280
TARGET_HEIGHT = 720

# macOS / Windows / Linux system fonts; the loader falls back through them in order.
DEFAULT_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/impact.ttf",       # Windows
    "C:/Windows/Fonts/ariblk.ttf",       # Windows Arial Black
    "C:/Windows/Fonts/arialbd.ttf",      # Windows Arial Bold（无 Impact/Black 时的回退）
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux fallback
]


@dataclass
class CoverSpec:
    ep: int
    series_name: str = ""
    theme: str = ""
    frame_at: float = 0.35  # 0..1 position in the clip
    font_path: str = ""  # empty → auto-pick from DEFAULT_FONT_CANDIDATES
    ep_prefix: str = "EPISODE"
    total_eps: int = 0  # if set, rendered as progress dots


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


def _extract_frame(src: Path, at_seconds: float, dst: Path) -> None:
    """Grab one frame at `at_seconds`, scale-and-crop to 1280x720."""
    vf = (
        f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT}"
    )
    proc = subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{at_seconds:.2f}", "-i", str(src),
         "-frames:v", "1", "-vf", vf, "-q:v", "2", str(dst)],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-4:])
        raise RuntimeError(f"ffmpeg frame extract failed:\n{tail}")


def _resolve_font(preferred: str) -> str:
    candidates = ([preferred] if preferred else []) + DEFAULT_FONT_CANDIDATES
    for path in candidates:
        if path and Path(path).exists():
            return path
    raise RuntimeError(
        "No usable font found. Install Impact/Arial Black or pass --font."
    )


def _draw_overlay(frame_path: Path, out_path: Path, spec: CoverSpec) -> None:
    from PIL import Image, ImageDraw, ImageFont, ImageEnhance

    img = Image.open(frame_path).convert("RGB")
    img = ImageEnhance.Brightness(img).enhance(0.70)  # deeper darken for ReelShort feel
    img_rgba = img.convert("RGBA")

    # Bottom gradient strip so EPISODE text always reads.
    strip_h = int(TARGET_HEIGHT * 0.24)
    strip = Image.new("RGBA", (TARGET_WIDTH, strip_h), (0, 0, 0, 0))
    strip_draw = ImageDraw.Draw(strip)
    for y in range(strip_h):
        alpha = int(220 * (y / strip_h) ** 1.4)
        strip_draw.rectangle([0, y, TARGET_WIDTH, y + 1], fill=(0, 0, 0, alpha))
    img_rgba.paste(strip, (0, TARGET_HEIGHT - strip_h), strip)

    font_path = _resolve_font(spec.font_path)
    draw = ImageDraw.Draw(img_rgba)

    _draw_theme(draw, spec.theme or spec.series_name.upper(), font_path)
    _draw_episode_label(draw, f"{spec.ep_prefix} {spec.ep:02d}", font_path)
    if spec.total_eps > 0:
        _draw_progress_dots(draw, spec.ep, spec.total_eps)

    img_rgba.convert("RGB").save(out_path, "JPEG", quality=88, optimize=True)


def _draw_theme(draw, text: str, font_path: str) -> None:
    """Center a bold multi-line title on the frame. Font auto-shrinks to fit."""
    if not text:
        return
    max_width = int(TARGET_WIDTH * 0.86)
    max_height = int(TARGET_HEIGHT * 0.5)

    from PIL import ImageFont

    size = int(TARGET_HEIGHT * 0.16)
    while size >= 40:
        font = ImageFont.truetype(font_path, size=size)
        lines = _wrap_lines(draw, text.upper(), font, max_width)
        line_h = size + int(size * 0.15)
        total_h = line_h * len(lines)
        if total_h <= max_height:
            break
        size -= 8

    y = int((TARGET_HEIGHT * 0.42) - total_h / 2)
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = (TARGET_WIDTH - w) // 2
        _draw_with_shadow(draw, (x, y), line, font, (255, 255, 255))
        y += line_h


def _wrap_lines(draw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    line: list[str] = []
    for word in words:
        candidate = " ".join(line + [word])
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if (bbox[2] - bbox[0]) <= max_width or not line:
            line.append(word)
        else:
            lines.append(" ".join(line))
            line = [word]
    if line:
        lines.append(" ".join(line))
    return lines


def _draw_episode_label(draw, text: str, font_path: str) -> None:
    from PIL import ImageFont

    font = ImageFont.truetype(font_path, size=int(TARGET_HEIGHT * 0.065))
    bbox = draw.textbbox((0, 0), text, font=font)
    x = 60
    y = TARGET_HEIGHT - (bbox[3] - bbox[1]) - 60
    _draw_with_shadow(draw, (x, y), text, font, (255, 235, 60))


def _draw_progress_dots(draw, current: int, total: int) -> None:
    """Row of dots along the bottom-right; filled up to `current`."""
    if total <= 0 or current <= 0:
        return
    dot_count = min(total, 10)
    dot_size = 12
    gap = 10
    filled_dots = round(current / total * dot_count)
    total_width = dot_count * dot_size + (dot_count - 1) * gap
    start_x = TARGET_WIDTH - total_width - 60
    y = TARGET_HEIGHT - 60
    for i in range(dot_count):
        x = start_x + i * (dot_size + gap)
        fill = (255, 235, 60) if i < filled_dots else (255, 255, 255, 120)
        draw.ellipse([x, y, x + dot_size, y + dot_size], fill=fill)


def _draw_with_shadow(draw, xy, text, font, fill):
    x, y = xy
    for dx, dy in ((3, 3), (-2, 2), (2, -2)):
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0))
    draw.text(xy, text, font=font, fill=fill)


def default_cover_path(src: Path) -> Path:
    return src.parent / f"{src.stem}_cover.jpg"


def make_cover(
    src: Path,
    spec: CoverSpec,
    out: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Generate a cover for `src` per `spec`. Returns the cover file path.

    If a manual cover already exists at the target path and `overwrite=False`,
    returns it as-is (respecting hand-crafted covers).
    """
    if not _ffmpeg_available():
        raise RuntimeError("ffmpeg/ffprobe not on PATH — install ffmpeg.")

    try:
        from PIL import Image  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("Pillow not installed. `uv pip install Pillow`.") from exc

    dst = out if out is not None else default_cover_path(src)
    if dst.exists() and not overwrite:
        cover_logger.info(f"Reusing existing {dst.name} (pass overwrite=True to regen).")
        return dst

    duration = _probe_duration(src)
    at = max(0.5, min(duration - 0.5, duration * spec.frame_at))

    with tempfile.TemporaryDirectory() as td:
        frame = Path(td) / "frame.jpg"
        _extract_frame(src, at, frame)
        _draw_overlay(frame, dst, spec)

    cover_logger.success(f"Cover written: {dst.name}")
    return dst
