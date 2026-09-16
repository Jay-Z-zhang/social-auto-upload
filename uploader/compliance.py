# -*- coding: utf-8 -*-
"""Compliance pre-review orchestrator.

Coordinates the "upload private to YouTube -> check compliance -> publish
everywhere" workflow. YouTube acts as the compliance gate: if the video passes
YouTube's automated content/copyright checks, it is published on YouTube and
(optionally) uploaded to TikTok.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import time

from loguru import logger

from uploader.youtube_uploader.yt_api import ComplianceResult, YouTubeAPI
from uploader.preflight import PreflightResult, run_preflight
from uploader.series import extract_ep_number, load_series, render_for_video
from uploader.episodes import load_episodes
from uploader.manifest import EpisodeState, get_state as manifest_get_state, upsert as manifest_upsert

try:
    from conf import COMPLIANCE_DELETE_ON_FAIL
except ImportError:
    COMPLIANCE_DELETE_ON_FAIL = True


compliance_logger = logger.bind(business_name="compliance")


def _ensure_shorts_in_title(title: str) -> str:
    return title if "#shorts" in title.lower() else f"{title} #Shorts".strip()


def _ensure_shorts_in_desc(desc: str) -> str:
    return desc if "#shorts" in desc.lower() else f"{desc}\n#Shorts".strip()


@dataclass
class ReviewRequest:
    """Everything needed to run the compliance-review-then-publish flow."""

    video_file: Path
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    tt_tags: list[str] = field(default_factory=list)
    youtube_account: str = ""
    tiktok_account: str = ""
    platforms: list[str] = field(default_factory=lambda: ["youtube"])
    schedule: datetime | None = None
    category_id: str = "22"
    made_for_kids: bool = False
    contains_synthetic_media: bool = False
    shorts: bool = False
    tiktok_prep: bool = True
    tiktok_mirror: bool = False
    youtube_playlist_id: str = ""
    cover_path: Path | None = None
    # Optional Shorts companion: additional YT upload (private→public, no compliance recheck)
    shorts_variant_file: Path | None = None
    shorts_playlist_id: str = ""
    # Resume support: skip steps whose outcome is already recorded in the manifest.
    resume_state: "object | None" = None
    # YouTube visibility after compliance passes. "public" (default) makes the
    # video public immediately (or at `schedule`), "unlisted" keeps it unlisted,
    # "private" leaves the private draft on YouTube for manual review.
    yt_visibility: str = "public"
    # TikTok visibility. "auto" (default) uses PUBLIC_TO_EVERYONE when the
    # creator_info list includes it, otherwise the most-open fallback.
    # Explicit values: public / followers / friends / self_only.
    tiktok_privacy: str = "auto"


@dataclass
class ReviewOutcome:
    """Final result of the full review-and-publish pipeline."""

    preflight: PreflightResult | None = None
    compliance: ComplianceResult | None = None
    youtube_published: bool = False
    tiktok_published: bool = False
    tiktok_publish_id: str = ""
    scheduled_at: datetime | None = None
    shorts_published: bool = False
    shorts_video_id: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        if self.preflight and self.preflight.blocked:
            return False
        return (
            self.compliance is not None
            and self.compliance.passed
            and not self.errors
        )

    def print_report(self) -> None:
        print("\n" + "=" * 60)
        print("  COMPLIANCE REVIEW REPORT")
        print("=" * 60)

        if self.preflight and self.preflight.ran:
            head = "BLOCKED" if self.preflight.blocked else ("MATCH" if self.preflight.matches else "PASSED")
            print(f"  Audio preflight:     {head}")
            for m in self.preflight.matches[:3]:
                print(f"    · {m.label()}")
        elif self.preflight and self.preflight.reason:
            print(f"  Audio preflight:     SKIPPED ({self.preflight.reason})")

        if self.compliance:
            status = "PASSED" if self.compliance.passed else "FAILED"
            print(f"  YouTube compliance:  {status}")
            print(f"  Video ID:            {self.compliance.video_id}")
            if self.compliance.rejection_reason:
                print(f"  Rejection reason:    {self.compliance.rejection_reason}")
            if self.compliance.licensed_content:
                print(f"  Content ID claim:    Yes (licensed content detected)")
        else:
            print("  YouTube compliance:  NOT RUN")

        print()
        if self.youtube_published:
            if self.scheduled_at:
                print(f"  YouTube:  Scheduled {self.scheduled_at.strftime('%Y-%m-%d %H:%M')}")
            else:
                print("  YouTube:  Published")
        if self.tiktok_published:
            print(f"  TikTok:   Published (publish_id: {self.tiktok_publish_id})")
        if self.shorts_published:
            print(f"  Shorts:   Published (video_id: {self.shorts_video_id})")

        if self.errors:
            print()
            print("  Errors:")
            for err in self.errors:
                print(f"    - {err}")

        print("=" * 60 + "\n")


def run_review(request: ReviewRequest) -> ReviewOutcome:
    """Execute the full compliance review pipeline (synchronous).

    Steps:
    1. Upload to YouTube as private
    2. Wait for processing & check compliance
    3. If passed, publish on YouTube (public or scheduled)
    4. If TikTok is in the target platforms, upload via Content Posting API
    5. Report results
    """
    outcome = ReviewOutcome()
    resume = request.resume_state

    # Full short-circuit: if the manifest says everything requested is already
    # live, don't touch the source file at all. Preflight/YT/TikTok/Shorts all
    # answered by the manifest.
    yt_done_from_resume = bool(resume and getattr(resume, "yt_video_id", "") and resume.yt_published)
    shorts_needed = bool(request.shorts_variant_file)
    shorts_done_from_resume = bool(resume and getattr(resume, "shorts_video_id", "") and resume.shorts_published)
    tt_needed = "tiktok" in request.platforms
    tt_done_from_resume = bool(resume and getattr(resume, "tt_publish_id", "") and resume.tt_published)

    if resume:
        if yt_done_from_resume:
            outcome.youtube_published = True
            # Prefer the historical schedule the manifest recorded; only fall
            # back to the caller-supplied schedule if the manifest didn't
            # record one (older rows before this field existed).
            if resume.yt_scheduled_at:
                try:
                    outcome.scheduled_at = datetime.fromisoformat(resume.yt_scheduled_at)
                except ValueError:
                    outcome.scheduled_at = request.schedule
            else:
                outcome.scheduled_at = request.schedule
        if shorts_done_from_resume:
            outcome.shorts_published = True
            outcome.shorts_video_id = resume.shorts_video_id
        if tt_done_from_resume:
            outcome.tiktok_published = True
            outcome.tiktok_publish_id = resume.tt_publish_id

    # ------------------------------------------------------------------ #
    # Step 0: Local audio copyright preflight (before we burn YT quota)
    # ------------------------------------------------------------------ #
    # Skip preflight only if there is no upload work left. If TikTok or Shorts
    # is still pending, we still want the copyright signal in case the source
    # was swapped between runs.
    tt_pending = tt_needed and not tt_done_from_resume
    shorts_pending = shorts_needed and not shorts_done_from_resume
    if yt_done_from_resume and not tt_pending and not shorts_pending:
        compliance_logger.info(
            f"Resume: everything requested is already live for {request.video_file.name}; "
            "skipping preflight + upload + compliance."
        )
        preflight = None
    else:
        preflight = run_preflight(request.video_file)
        outcome.preflight = preflight
        if preflight.ran and preflight.matches:
            compliance_logger.warning(preflight.summary())
        if preflight.blocked:
            outcome.errors.append(
                "Blocked by audio preflight — AcoustID matched a copyrighted recording."
            )
            return outcome

    # ------------------------------------------------------------------ #
    # Step 1+2: YouTube upload + compliance check
    # ------------------------------------------------------------------ #
    if not request.youtube_account:
        outcome.errors.append("youtube_account is required for compliance review.")
        return outcome

    yt = YouTubeAPI(request.youtube_account)
    yt.authenticate()

    title = request.title
    description = request.description
    if request.shorts:
        title = _ensure_shorts_in_title(title)
        description = _ensure_shorts_in_desc(description)

    if yt_done_from_resume:
        video_id = resume.yt_video_id
    else:
        try:
            video_id = yt.upload_private(
                file_path=request.video_file,
                title=title,
                description=description,
                tags=request.tags,
                category_id=request.category_id,
                made_for_kids=request.made_for_kids,
                contains_synthetic_media=request.contains_synthetic_media,
            )
        except Exception as exc:
            outcome.errors.append(f"YouTube upload failed: {exc}")
            return outcome

        compliance = yt.check_compliance(video_id)
        outcome.compliance = compliance

        if not compliance.passed:
            compliance_logger.error(compliance.summary)
            if COMPLIANCE_DELETE_ON_FAIL:
                try:
                    yt.delete_video(video_id)
                    compliance_logger.info(f"Deleted failed video {video_id} from YouTube.")
                except Exception as exc:
                    outcome.errors.append(f"Failed to delete video after rejection: {exc}")
            return outcome

    # Record the video_id back onto outcome. When resuming, the compliance
    # verdict came from a previous run — synthesize a marker so downstream
    # code (batch manifest writer, print_report) can uniformly access video_id.
    if outcome.compliance is None:
        outcome.compliance = ComplianceResult(
            video_id=video_id,
            passed=True,
            upload_status="resumed",
        )

    # ------------------------------------------------------------------ #
    # Step 3: Publish on YouTube
    # ------------------------------------------------------------------ #
    if "youtube" in request.platforms and not yt_done_from_resume:
        try:
            if request.schedule:
                yt.schedule_publish(video_id, request.schedule)
                outcome.scheduled_at = request.schedule
            elif request.yt_visibility == "private":
                # Video is already private from upload_private; nothing to do.
                compliance_logger.info(f"Video {video_id} kept PRIVATE per --yt-visibility private.")
            elif request.yt_visibility == "unlisted":
                yt.set_visibility(video_id, "unlisted")
            else:
                yt.make_public(video_id)
            outcome.youtube_published = True
        except Exception as exc:
            outcome.errors.append(f"YouTube publish failed: {exc}")

        if request.youtube_playlist_id and outcome.youtube_published:
            try:
                yt.add_to_playlist(video_id, request.youtube_playlist_id)
            except Exception as exc:
                # non-fatal: video is already published
                compliance_logger.warning(f"Playlist append failed: {exc}")

        if request.cover_path and outcome.youtube_published:
            try:
                yt.set_thumbnail(video_id, request.cover_path)
            except Exception as exc:
                # non-fatal — YouTube may reject custom thumbs on unverified channels
                compliance_logger.warning(f"Thumbnail upload failed: {exc}")

    # ------------------------------------------------------------------ #
    # Step 3b: Shorts companion (private → public, reuses compliance verdict)
    # ------------------------------------------------------------------ #
    if request.shorts_variant_file and outcome.youtube_published and not shorts_done_from_resume:
        try:
            shorts_title = _ensure_shorts_in_title(request.title)
            shorts_desc = _ensure_shorts_in_desc(description)

            shorts_video_id = yt.upload_private(
                file_path=request.shorts_variant_file,
                title=shorts_title[:100],
                description=shorts_desc,
                tags=request.tags,
                category_id=request.category_id,
                made_for_kids=request.made_for_kids,
                contains_synthetic_media=request.contains_synthetic_media,
            )
            outcome.shorts_video_id = shorts_video_id

            if request.schedule:
                yt.schedule_publish(shorts_video_id, request.schedule)
            else:
                yt.make_public(shorts_video_id)
            outcome.shorts_published = True

            shorts_playlist = request.shorts_playlist_id or request.youtube_playlist_id
            if shorts_playlist:
                try:
                    yt.add_to_playlist(shorts_video_id, shorts_playlist)
                except Exception as exc:
                    compliance_logger.warning(f"Shorts playlist append failed: {exc}")

            if request.cover_path:
                try:
                    yt.set_thumbnail(shorts_video_id, request.cover_path)
                except Exception as exc:
                    compliance_logger.warning(f"Shorts thumbnail upload failed: {exc}")
        except Exception as exc:
            # non-fatal — the long-form video is already up
            outcome.errors.append(f"Shorts companion upload failed: {exc}")

    # ------------------------------------------------------------------ #
    # Step 4: Publish on TikTok
    # ------------------------------------------------------------------ #
    if "tiktok" in request.platforms and not tt_done_from_resume:
        if not request.tiktok_account:
            outcome.errors.append("tiktok_account required but not provided.")
        else:
            try:
                from uploader.tk_uploader.tk_api import TikTokAPI

                tk_source = request.video_file
                if request.tiktok_prep:
                    try:
                        from uploader.tt_prep import prepare_for_tiktok

                        prep = prepare_for_tiktok(
                            src=request.video_file,
                            mirror=request.tiktok_mirror,
                        )
                        tk_source = prep.output
                        compliance_logger.info(
                            f"TikTok will upload the prepped variant: {tk_source.name}"
                        )
                    except RuntimeError as exc:
                        compliance_logger.warning(
                            f"TikTok prep failed, falling back to original file: {exc}"
                        )

                tk = TikTokAPI(request.tiktok_account)
                tk.authenticate()

                creator_info = tk.query_creator_info()
                privacy_options = creator_info.get(
                    "privacy_level_options", ["SELF_ONLY"]
                )
                privacy = pick_tiktok_privacy(
                    privacy_options, preferred=request.tiktok_privacy,
                )
                compliance_logger.info(
                    f"TikTok privacy set to {privacy} "
                    f"(preferred={request.tiktok_privacy or 'auto'}; "
                    f"options={privacy_options})"
                )

                publish_id = tk.upload_video(
                    file_path=tk_source,
                    title=_compose_tiktok_caption(request.title, request.tt_tags),
                    privacy_level=privacy,
                )
                outcome.tiktok_publish_id = publish_id

                result = tk.poll_status(publish_id)
                outcome.tiktok_published = result.success
                if not result.success:
                    outcome.errors.append(
                        f"TikTok publish failed: {result.fail_reason}"
                    )
            except Exception as exc:
                outcome.errors.append(f"TikTok upload/publish failed: {exc}")

    return outcome


VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}
YOUTUBE_DAILY_UPLOAD_BUDGET = 6
TIKTOK_CAPTION_LIMIT = 2200
TIKTOK_PRIVACY_ALIASES = {
    "auto": None,
    "public": "PUBLIC_TO_EVERYONE",
    "followers": "FOLLOWER_OF_CREATOR",
    "friends": "MUTUAL_FOLLOW_FRIENDS",
    "self_only": "SELF_ONLY",
}
TIKTOK_PRIVACY_FALLBACK_ORDER = [
    "PUBLIC_TO_EVERYONE",
    "FOLLOWER_OF_CREATOR",
    "MUTUAL_FOLLOW_FRIENDS",
    "SELF_ONLY",
]


def pick_tiktok_privacy(options: list[str] | None, preferred: str = "auto") -> str:
    """Choose a TikTok privacy_level from creator_info options.

    auto: public if allowed, else the most-open remaining level.
    An explicit preferred level that is not in *options* raises RuntimeError
    so a smoke-test --tiktok-privacy self_only cannot silently go public,
    and --tiktok-privacy public cannot silently fall back to SELF_ONLY.
    """
    available = [str(x) for x in (options or []) if str(x).strip()]
    if not available:
        raise RuntimeError("TikTok creator_info returned no privacy_level_options.")

    raw = (preferred or "auto").strip()
    key = raw.lower().replace("-", "_")
    wanted = TIKTOK_PRIVACY_ALIASES.get(key)
    if wanted is None and raw.upper() in TIKTOK_PRIVACY_FALLBACK_ORDER:
        wanted = raw.upper()

    if key in ("", "auto"):
        for level in TIKTOK_PRIVACY_FALLBACK_ORDER:
            if level in available:
                if level != "PUBLIC_TO_EVERYONE":
                    compliance_logger.warning(
                        f"TikTok public posting is not available; using {level}. "
                        f"Options: {available}"
                    )
                return level
        raise RuntimeError(f"No usable TikTok privacy level in {available}.")

    if wanted is None:
        raise RuntimeError(
            f"Unknown TikTok privacy '{preferred}'. "
            f"Use auto, public, followers, friends, or self_only."
        )
    if wanted not in available:
        raise RuntimeError(
            f"TikTok privacy {wanted} is not available for this account. "
            f"Options: {available}"
        )
    return wanted


def _compose_tiktok_caption(title: str, tags: list[str]) -> str:
    """Attach hashtags to the TikTok caption. Caps at TikTok's 2200-char limit."""
    tag_str = " ".join(f"#{t.lstrip('#')}" for t in tags if t)
    caption = f"{title}\n\n{tag_str}".strip() if tag_str else title
    return caption[:TIKTOK_CAPTION_LIMIT]


@dataclass
class PublishPolicy:
    """Per-account rate limits for a single batch run.

    YouTube's Data API quota is 10k units/day; each insert costs 1600 units,
    so ~6 uploads/day is the hard ceiling. TikTok has no public quota but
    treats bursts as spam signals. Minimum interval keeps consecutive
    submissions from looking machine-timed.
    """

    yt_per_day: int = 3
    tt_per_day: int = 5
    min_interval_seconds: int = 90
    hard_yt_daily_max: int = YOUTUBE_DAILY_UPLOAD_BUDGET


def _title_from_sidecar(video: Path) -> tuple[str, str, list[str]]:
    """Read optional sibling .txt: line1 title, line2 hashtags."""
    sidecar = video.with_suffix(".txt")
    if sidecar.exists():
        lines = sidecar.read_text(encoding="utf-8").strip().splitlines()
        title = lines[0].strip() if lines else video.stem
        tags: list[str] = []
        desc = ""
        if len(lines) > 1:
            tags = [t.strip().lstrip("#") for t in lines[1].replace(",", " ").split() if t.strip()]
        if len(lines) > 2:
            desc = "\n".join(lines[2:]).strip()
        return title, desc, tags
    title = video.stem.replace("_", " ").replace("-", " ").strip()
    return title, "", []


def plan_batch_schedule(
    videos: list[Path],
    per_day: int = 2,
    daily_hours: list[int] | None = None,
    start_days: int = 1,
) -> list[tuple[Path, datetime]]:
    """Spread videos across days/hours. Default: 2 per day at 12:00 and 19:00, starting tomorrow."""
    from utils.files_times import generate_schedule_time_next_day

    hours = daily_hours or [12, 19]
    if per_day > len(hours):
        raise ValueError(f"--per-day {per_day} exceeds available --times ({len(hours)})")
    times = generate_schedule_time_next_day(
        total_videos=len(videos),
        videos_per_day=per_day,
        daily_times=hours,
        start_days=start_days,
    )
    return list(zip(videos, times))


def list_videos_in_dir(directory: Path) -> list[Path]:
    files = [
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
    ]
    if not files:
        raise FileNotFoundError(f"No video files found in {directory}")

    # Sort by parsed episode number so ep_02.mp4 comes before ep_10.mp4.
    # Files without a parseable ep number go last, ordered by name for stability.
    def _sort_key(p: Path) -> tuple[int, int | str]:
        ep = extract_ep_number(p)
        if ep is None:
            return (1, p.name)
        return (0, ep)

    files.sort(key=_sort_key)
    return files


def _prepare_shorts(video: Path, series) -> Path | None:
    """Slice a Shorts companion if series.shorts_cut_seconds > 0 and source is long enough.

    Returns the shorts file path or None (source already short / feature disabled / error).
    """
    cut = getattr(series, "shorts_cut_seconds", 0) or 0
    if cut <= 0:
        return None
    try:
        from uploader.split import split_shorts

        result = split_shorts(video, duration=cut)
        return result.output if result.performed else None
    except (RuntimeError, ValueError) as exc:
        compliance_logger.warning(f"Shorts split failed for {video.name}: {exc}")
        return None


def _prepare_cover(video: Path, series, episode_meta, auto: bool) -> Path | None:
    """Locate or generate a cover jpg for `video`. Returns None when unavailable.

    Priority: hand-crafted <stem>_cover.jpg beats auto-generation. If none exists
    and auto=True + series is active + filename has an ep number, generate one.
    Uses episode_meta.theme as the cover's main line when available.
    """
    from uploader.cover import CoverSpec, default_cover_path, make_cover

    manual = default_cover_path(video)
    if manual.exists():
        return manual
    if not auto or not series.is_active():
        return None
    ep = extract_ep_number(video)
    if ep is None:
        return None
    theme = episode_meta.theme if episode_meta and episode_meta.theme else ""
    try:
        return make_cover(
            video,
            CoverSpec(
                ep=ep,
                series_name=series.name,
                theme=theme,
                total_eps=series.total_eps,
            ),
        )
    except RuntimeError as exc:
        compliance_logger.warning(f"Cover generation failed for {video.name}: {exc}")
        return None


def run_batch_review(
    directory: Path,
    youtube_account: str,
    tiktok_account: str = "",
    platforms: list[str] | None = None,
    per_day: int = 2,
    daily_hours: list[int] | None = None,
    start_days: int = 1,
    shorts: bool = False,
    dry_run: bool = False,
    tiktok_pause_seconds: int = 90,
    tiktok_prep: bool = True,
    tiktok_mirror: bool = False,
    policy: PublishPolicy | None = None,
    auto_cover: bool = True,
    max_items: int = 0,
    tiktok_privacy: str = "auto",
) -> list[ReviewOutcome]:
    """Review each video, then schedule YouTube public times so posts are staggered."""
    platforms = platforms or ["youtube"]
    policy = policy or PublishPolicy()

    videos = list_videos_in_dir(directory)  # raises FileNotFoundError on empty
    series = load_series(directory)
    episodes = load_episodes(directory)

    yt_uploads_per_ep = 2 if (series.shorts_cut_seconds > 0 and "youtube" in platforms) else 1

    # 统计 pending（manifest 已完成的集数不占本次额度），并按 max_items 截断，
    # 让「每天重跑、每次续 N 集」的长剧排期跑在 YT 配额之内。
    pending_eps: list[int] = []
    for v in videos:
        ep = extract_ep_number(v)
        st = manifest_get_state(directory, ep) if ep is not None else None
        if st and st.is_yt_done():
            continue
        if ep is not None:
            pending_eps.append(ep)
    if max_items > 0:
        pending_eps = pending_eps[:max_items]

    quota_used = len(pending_eps) * yt_uploads_per_ep
    if quota_used > policy.hard_yt_daily_max:
        raise RuntimeError(
            f"{quota_used} pending YT uploads (> {policy.hard_yt_daily_max}/day quota). "
            f"Run again tomorrow, or use --max-items to chunk, or disable shorts_cut_seconds."
        )
    if "youtube" in platforms and per_day > policy.yt_per_day:
        compliance_logger.warning(
            f"--per-day {per_day} exceeds YouTube pacing limit "
            f"({policy.yt_per_day}/day). Bursts of public posts trigger spam signals."
        )
    if "tiktok" in platforms and len(videos) > policy.tt_per_day:
        compliance_logger.warning(
            f"{len(videos)} TikTok posts in one batch exceeds "
            f"{policy.tt_per_day}/day; expect distribution throttling."
        )

    if series.is_active():
        print(f"Series: {series.name} (total {series.total_eps or '?'} eps)")
        if series.youtube_playlist_id:
            print(f"  → episodes will be appended to YouTube playlist {series.youtube_playlist_id}")
    if episodes:
        print(f"Loaded episodes.yaml with {len(episodes)} entries.")
    if series.shorts_cut_seconds > 0 and "youtube" in platforms:
        print(f"Shorts companion enabled: sources > {series.shorts_cut_seconds}s will also upload a "
              f"<stem>_short.mp4 clip. YT quota usage doubles.")

    planned = plan_batch_schedule(videos, per_day=per_day, daily_hours=daily_hours, start_days=start_days)

    # 与配额统计同一口径：只保留 pending 的前 N 集（done 的不占名额也不重跑）
    pending_set = set(pending_eps)
    planned = [
        (video, when)
        for video, when in planned
        if (ep := extract_ep_number(video)) is None or ep in pending_set
    ]

    print("Batch schedule (YouTube goes public at these local times):")
    print("-" * 60)
    for video, when in planned:
        hook, _, _ = _title_from_sidecar(video)
        ep = extract_ep_number(video)
        meta = episodes.get(ep) if ep is not None else None
        display_title, _, _, _ = render_for_video(video, hook, "", [], series, meta)
        print(f"  {when.strftime('%Y-%m-%d %H:%M')}  {video.name}  ({display_title})")
    print("-" * 60)
    print(f"Pace: {per_day}/day. API uploads happen now; public time is delayed.")
    if "tiktok" in platforms:
        print(
            "TikTok: posted now (no schedule). "
            f"Visibility: {tiktok_privacy or 'auto'} "
            "(public if the account allows it)."
        )
    print()

    if dry_run:
        print("Dry run only. No uploads.")
        return []

    outcomes: list[ReviewOutcome] = []
    for index, (video, when) in enumerate(planned):
        submit_started = time.monotonic()
        hook, sc_desc, sc_tags = _title_from_sidecar(video)
        ep = extract_ep_number(video)
        meta = episodes.get(ep) if ep is not None else None
        title, desc, tags, tt_tags = render_for_video(video, hook, sc_desc, sc_tags, series, meta)
        cover = _prepare_cover(video, series, meta, auto_cover) if "youtube" in platforms else None
        shorts_variant = _prepare_shorts(video, series) if "youtube" in platforms else None

        prior_state = manifest_get_state(directory, ep) if ep is not None else None
        want_shorts = shorts_variant is not None
        want_tt = "tiktok" in platforms
        prior_label = prior_state.status_label(want_shorts, want_tt) if prior_state else "unknown"
        if prior_state and prior_label == "done":
            print(f"[{index + 1}/{len(planned)}] {video.name} → manifest says DONE; skipping.")
            outcome = ReviewOutcome(
                youtube_published=True,
                shorts_published=prior_state.is_shorts_done(),
                shorts_video_id=prior_state.shorts_video_id,
                tiktok_published=prior_state.is_tt_done(),
                tiktok_publish_id=prior_state.tt_publish_id,
                scheduled_at=when,
            )
            outcomes.append(outcome)
            continue

        if prior_state and prior_label == "partial":
            print(f"[{index + 1}/{len(planned)}] {video.name} → manifest says PARTIAL; resuming.")

        request = ReviewRequest(
            video_file=video,
            title=title,
            description=desc,
            tags=tags,
            tt_tags=tt_tags,
            youtube_account=youtube_account,
            tiktok_account=tiktok_account,
            platforms=platforms,
            schedule=when,
            shorts=shorts,
            tiktok_prep=tiktok_prep,
            tiktok_mirror=tiktok_mirror,
            youtube_playlist_id=series.youtube_playlist_id,
            cover_path=cover,
            shorts_variant_file=shorts_variant,
            shorts_playlist_id=series.shorts_playlist_id,
            resume_state=prior_state,
            tiktok_privacy=tiktok_privacy,
        )
        print(f"[{index + 1}/{len(planned)}] {video.name} -> {when.strftime('%Y-%m-%d %H:%M')}")
        outcome = run_review(request)
        outcome.print_report()
        outcomes.append(outcome)

        if ep is not None:
            state = EpisodeState(
                batch_dir=str(directory.expanduser().resolve()),
                ep=ep,
                source_file=video.name,
                preflight_ok=(None if outcome.preflight is None
                              else (not outcome.preflight.blocked)),
                yt_video_id=(outcome.compliance.video_id if outcome.compliance else ""),
                yt_published=outcome.youtube_published,
                yt_scheduled_at=(outcome.scheduled_at.isoformat() if outcome.scheduled_at else ""),
                shorts_video_id=outcome.shorts_video_id,
                shorts_published=outcome.shorts_published,
                tt_publish_id=outcome.tiktok_publish_id,
                tt_published=outcome.tiktok_published,
                last_error=" | ".join(outcome.errors),
            )
            try:
                manifest_upsert(state)
            except Exception as exc:
                compliance_logger.warning(f"Manifest write failed for ep {ep}: {exc}")

        if index < len(planned) - 1:
            elapsed = time.monotonic() - submit_started
            wait = max(0, policy.min_interval_seconds - int(elapsed))
            if wait > 0:
                print(f"Pacing: sleeping {wait}s (min interval {policy.min_interval_seconds}s) before next submit …")
                time.sleep(wait)

    passed = sum(1 for o in outcomes if o.compliance and o.compliance.passed)
    print(f"Batch finished: {passed}/{len(outcomes)} passed YouTube compliance.")
    return outcomes

