# -*- coding: utf-8 -*-
"""Per-episode metadata loaded from `episodes.yaml`.

Layered with `series.yaml`:
- series.yaml   → cross-episode constants (name, base hashtags, disclaimer)
- episodes.yaml → per-episode text (theme for cover, hook for title, description, tags)
- sidecar txt   → legacy fallback when neither yaml file has data for that ep

Example episodes.yaml (dict keyed by episode number):

    1:
      theme: "BETRAYAL AT THE WEDDING"
      hook: "She said YES. He said her sister's name."
      description: "Emma walked down the aisle. Her fiancé said 'I do' — to her sister."
      tags: ["betrayal", "wedding", "revenge"]
      tt_tags: ["wedding", "drama"]   # optional TikTok-specific subset
    2:
      theme: "STRANGER IN A HOSPITAL BED"
      hook: "..."
      ...
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

episodes_logger = logger.bind(business_name="episodes")

EPISODES_YAML = "episodes.yaml"


@dataclass
class EpisodeMeta:
    ep: int
    theme: str = ""
    hook: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    tt_tags: list[str] = field(default_factory=list)


def load_episodes(directory: Path) -> dict[int, EpisodeMeta]:
    """Read `episodes.yaml` from `directory`. Returns `{}` if absent or unparseable."""
    path = directory / EPISODES_YAML
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        episodes_logger.warning("PyYAML not installed; episodes.yaml ignored.")
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        episodes_logger.warning(f"Bad episodes.yaml: {exc}")
        return {}

    result: dict[int, EpisodeMeta] = {}
    for raw_key, item in data.items():
        try:
            ep = int(raw_key)
        except (ValueError, TypeError):
            episodes_logger.warning(f"Skipping non-numeric episode key: {raw_key}")
            continue
        item = item or {}
        result[ep] = EpisodeMeta(
            ep=ep,
            theme=str(item.get("theme", "")).strip(),
            hook=str(item.get("hook", "")).strip(),
            description=str(item.get("description", "")).strip(),
            tags=[str(t).lstrip("#") for t in (item.get("tags") or [])],
            tt_tags=[str(t).lstrip("#") for t in (item.get("tt_tags") or [])],
        )
    return result


def dump_episodes(directory: Path, episodes: dict[int, EpisodeMeta]) -> Path:
    """Write `episodes.yaml`. Returns the path written."""
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML required for dump_episodes.") from exc

    payload = {}
    for ep in sorted(episodes.keys()):
        m = episodes[ep]
        payload[ep] = {
            "theme": m.theme,
            "hook": m.hook,
            "description": m.description,
            "tags": m.tags,
        }
        if m.tt_tags:
            payload[ep]["tt_tags"] = m.tt_tags

    path = directory / EPISODES_YAML
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    return path
