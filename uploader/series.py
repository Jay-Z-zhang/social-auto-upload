# -*- coding: utf-8 -*-
"""Per-series metadata bundle for drama uploads.

A `series.yaml` in the batch directory drives episode naming, description
templates, tags, and the YouTube playlist to append to. Keeps every episode
of a series consistent without you touching each file's sidecar.

Example series.yaml:

    name: "Second Chance"
    total_eps: 60
    hashtags: ["#drama", "#shortdrama", "#romance"]
    disclaimer: "Fictional drama. All characters 18+."
    youtube_playlist_id: "PLxxxxxxxxxxxx"
    description_footer: |
      ▶ Full Playlist: https://youtube.com/playlist?list=PLxxxxxxxxxxxx

      Written by …
      Cast: …

Filenames must contain an episode number: `ep_01.mp4`, `EP03.mp4`, `s2e07.mp4`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

series_logger = logger.bind(business_name="series")

SERIES_YAML = "series.yaml"
EP_PATTERNS = [
    re.compile(r"[eE][pP]?[_-]?(\d{1,3})"),
    re.compile(r"^(\d{1,3})[_. -]"),
    re.compile(r"(\d{1,3})(?:[_.\s-]*(?:final|end))?\s*$"),  # trailing number: mydrama01.mp4
]


@dataclass
class SeriesConfig:
    name: str = ""
    total_eps: int = 0
    hashtags: list[str] = field(default_factory=list)
    disclaimer: str = ""
    description_footer: str = ""
    youtube_playlist_id: str = ""
    title_template: str = "{name} EP{ep:02d}/{total} | {hook}"
    # LLM-generator inputs (only read by sau gen-metadata):
    synopsis: str = ""
    episode_outlines: str = ""
    target_audience: str = ""
    tone: str = ""
    language: str = "en"
    # Shorts companion: 0 disables. When > 0, sources > this duration get a
    # <stem>_short.mp4 slice uploaded alongside the long form.
    shorts_cut_seconds: int = 0
    shorts_playlist_id: str = ""

    def is_active(self) -> bool:
        return bool(self.name)


def load_series(directory: Path) -> SeriesConfig:
    """Return a SeriesConfig from `<directory>/series.yaml`, or an empty one."""
    path = directory / SERIES_YAML
    if not path.exists():
        return SeriesConfig()
    try:
        import yaml
    except ImportError:
        series_logger.warning("PyYAML not installed; series.yaml ignored.")
        return SeriesConfig()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        series_logger.warning(f"Bad series.yaml: {exc}")
        return SeriesConfig()

    return SeriesConfig(
        name=str(data.get("name", "")).strip(),
        total_eps=int(data.get("total_eps", 0) or 0),
        hashtags=[str(h).lstrip("#") for h in (data.get("hashtags") or [])],
        disclaimer=str(data.get("disclaimer", "")).strip(),
        description_footer=str(data.get("description_footer", "")).strip(),
        youtube_playlist_id=str(data.get("youtube_playlist_id", "")).strip(),
        title_template=str(
            data.get("title_template", "{name} EP{ep:02d}/{total} | {hook}")
        ),
        synopsis=str(data.get("synopsis", "")).strip(),
        episode_outlines=str(data.get("episode_outlines", "")).strip(),
        target_audience=str(data.get("target_audience", "")).strip(),
        tone=str(data.get("tone", "")).strip(),
        language=str(data.get("language", "en")).strip(),
        shorts_cut_seconds=int(data.get("shorts_cut_seconds", 0) or 0),
        shorts_playlist_id=str(data.get("shorts_playlist_id", "")).strip(),
    )


def extract_ep_number(video: Path) -> int | None:
    """Extract the episode number from a filename. Returns None if not found."""
    stem = video.stem
    for pattern in EP_PATTERNS:
        m = pattern.search(stem)
        if m:
            return int(m.group(1))
    return None


def render_for_video(
    video: Path,
    hook: str,
    sidecar_desc: str,
    sidecar_tags: list[str],
    series: SeriesConfig,
    episode_meta=None,
) -> tuple[str, str, list[str], list[str]]:
    """Apply series template to one video. Returns (title, description, tags, tt_tags).

    Priority for each field: episodes.yaml > sidecar txt > empty.
    tt_tags is a TikTok-specific subset — if episodes.yaml has tt_tags it wins;
    otherwise falls back to the first 3 items of the merged tag list (TikTok
    tends to down-rank posts with many tags).

    Falls back to raw sidecar values verbatim when no series is loaded.
    """
    if not series.is_active():
        return hook, sidecar_desc, sidecar_tags, sidecar_tags[:3]

    ep = extract_ep_number(video)
    if ep is None:
        series_logger.warning(
            f"{video.name}: no episode number in filename; using sidecar title verbatim."
        )
        return hook, sidecar_desc, sidecar_tags, sidecar_tags[:3]

    eff_hook = (episode_meta.hook if episode_meta and episode_meta.hook else hook) or ""
    eff_desc = (episode_meta.description if episode_meta and episode_meta.description else sidecar_desc) or ""
    eff_tags = list(episode_meta.tags) if (episode_meta and episode_meta.tags) else list(sidecar_tags)

    try:
        title = series.title_template.format(
            name=series.name,
            ep=ep,
            total=series.total_eps or "?",
            hook=eff_hook,
        ).strip(" |")
    except (KeyError, IndexError) as exc:
        series_logger.warning(f"Bad title_template ({exc}); falling back to hook.")
        title = eff_hook or video.stem

    desc_parts = []
    if eff_desc:
        desc_parts.append(eff_desc)
    if series.description_footer:
        desc_parts.append(series.description_footer)
    if series.disclaimer:
        desc_parts.append(series.disclaimer)
    description = "\n\n".join(p for p in desc_parts if p)

    yt_tags = list(dict.fromkeys(eff_tags + series.hashtags))[:12]

    if episode_meta and episode_meta.tt_tags:
        tt_tags = episode_meta.tt_tags[:5]
    else:
        tt_tags = yt_tags[:3]

    return title, description, yt_tags, tt_tags
