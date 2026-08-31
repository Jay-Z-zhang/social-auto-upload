# -*- coding: utf-8 -*-
"""LLM-driven metadata generator for `episodes.yaml`.

Given a series.yaml with synopsis + rough episode outlines, this calls
DeepSeek (OpenAI-compatible chat completions) once and writes a full
episodes.yaml with per-episode theme/hook/description/tags.

You review and edit the file before running `sau review-batch`. Nothing here
runs at publish time — DeepSeek is not a runtime dependency.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from loguru import logger

from uploader.episodes import EpisodeMeta, dump_episodes, load_episodes
from uploader.series import SeriesConfig, load_series

try:
    from conf import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
except ImportError:
    DEEPSEEK_API_KEY = ""
    DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL = "deepseek-chat"

genmeta_logger = logger.bind(business_name="genmeta")


SYSTEM_PROMPT = """You are a short-form drama copywriter for YouTube Shorts and TikTok.
Your job is to produce click-driving titles, cover themes, descriptions and hashtags
for a series aimed at Western viewers. Every episode ends on a cliffhanger — your
copy should preserve that tension without spoiling the resolution.

Style rules:
- ALL text in English, targeted at US/UK audiences.
- theme: 3 to 6 UPPERCASE words. Reads like a movie poster. No punctuation.
- hook: one line, 6 to 14 words. Reads like a whisper of the twist. May include
  quotes, ellipses, or a colon.
- description: 2 to 3 short sentences. First sentence teases the setup, second
  raises the stakes, optional third pushes the viewer to the next episode.
- tags: 5 to 8 lowercase hashtags without the '#'. Prefer common ones in the
  drama-romance space (revenge, billionaire, betrayal, ceo, romance, secretbaby)
  over overly niche terms.
- tt_tags: 3 to 5 shorter subset for TikTok (TikTok penalises tag stuffing).

Return ONLY a JSON object, no markdown fences, matching this schema:
{
  "episodes": [
    {"ep": 1, "theme": "...", "hook": "...", "description": "...",
     "tags": ["..."], "tt_tags": ["..."]},
    ...
  ]
}
"""


@dataclass
class GenResult:
    total_generated: int
    written_path: Path | None
    merged_with_existing: int


def build_user_prompt(series: SeriesConfig, episode_range: list[int]) -> str:
    parts = [
        f"Series name: {series.name}",
        f"Total episodes: {series.total_eps}",
        f"Target audience: {series.target_audience or 'US/UK drama fans, 25-45'}",
        f"Tone: {series.tone or 'high-stakes revenge romance with cliffhanger endings'}",
    ]
    if series.synopsis:
        parts.append(f"Overall synopsis:\n{series.synopsis}")
    if series.episode_outlines:
        parts.append(f"Episode outlines (rough):\n{series.episode_outlines}")
    parts.append(
        "Generate metadata for these episode numbers: "
        + ", ".join(str(n) for n in episode_range)
        + ". Return one entry per episode."
    )
    return "\n\n".join(parts)


def _strip_json_fence(raw: str) -> str:
    """Remove ```json / ``` fences if the model added them despite instructions."""
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped


def _call_deepseek(system: str, user: str, timeout: float = 120.0) -> str:
    if not DEEPSEEK_API_KEY:
        raise RuntimeError(
            "DEEPSEEK_API_KEY not set in conf.py; sau gen-metadata needs it."
        )
    resp = httpx.post(
        f"{DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.9,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    return body["choices"][0]["message"]["content"]


def _parse_response(raw: str) -> list[EpisodeMeta]:
    payload = json.loads(_strip_json_fence(raw))
    episodes_raw = payload.get("episodes") or []
    out: list[EpisodeMeta] = []
    for item in episodes_raw:
        try:
            ep = int(item["ep"])
        except (KeyError, ValueError, TypeError):
            continue
        out.append(
            EpisodeMeta(
                ep=ep,
                theme=str(item.get("theme", "")).strip(),
                hook=str(item.get("hook", "")).strip(),
                description=str(item.get("description", "")).strip(),
                tags=[str(t).lstrip("#") for t in (item.get("tags") or [])],
                tt_tags=[str(t).lstrip("#") for t in (item.get("tt_tags") or [])],
            )
        )
    return out


def generate_metadata(
    directory: Path,
    episode_range: list[int] | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
) -> GenResult:
    """Generate episodes.yaml for a folder.

    - episode_range=None regenerates all episodes 1..total_eps.
    - When existing entries are present and overwrite=False, only fills gaps.
    - dry_run prints the JSON but writes nothing.
    """
    series = load_series(directory)
    if not series.is_active():
        raise RuntimeError(f"{directory}/series.yaml missing or empty.")
    if series.total_eps <= 0:
        raise RuntimeError("series.yaml needs total_eps to plan generation.")

    existing = load_episodes(directory)
    target = episode_range or list(range(1, series.total_eps + 1))
    if not overwrite:
        target = [ep for ep in target if ep not in existing]
    if not target:
        genmeta_logger.info("Nothing to do — episodes.yaml already covers the range.")
        return GenResult(total_generated=0, written_path=None, merged_with_existing=len(existing))

    system = SYSTEM_PROMPT
    user = build_user_prompt(series, target)
    genmeta_logger.info(f"Asking DeepSeek for {len(target)} episodes …")
    raw = _call_deepseek(system, user)

    new_metas = _parse_response(raw)
    if not new_metas:
        raise RuntimeError("DeepSeek returned no parseable episodes.")

    if dry_run:
        print(json.dumps({"episodes": [m.__dict__ for m in new_metas]}, indent=2))
        return GenResult(total_generated=len(new_metas), written_path=None, merged_with_existing=len(existing))

    merged = dict(existing)
    for m in new_metas:
        merged[m.ep] = m
    path = dump_episodes(directory, merged)
    genmeta_logger.success(f"Wrote {len(new_metas)} entries → {path}")
    return GenResult(total_generated=len(new_metas), written_path=path, merged_with_existing=len(existing))
