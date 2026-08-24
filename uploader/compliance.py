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

try:
    from conf import COMPLIANCE_DELETE_ON_FAIL
except ImportError:
    COMPLIANCE_DELETE_ON_FAIL = True


compliance_logger = logger.bind(business_name="compliance")


@dataclass
class ReviewRequest:
    """Everything needed to run the compliance-review-then-publish flow."""

    video_file: Path
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    youtube_account: str = ""
    tiktok_account: str = ""
    platforms: list[str] = field(default_factory=lambda: ["youtube"])
    schedule: datetime | None = None
    category_id: str = "22"
    made_for_kids: bool = False
    contains_synthetic_media: bool = False
    shorts: bool = False


@dataclass
class ReviewOutcome:
    """Final result of the full review-and-publish pipeline."""

    compliance: ComplianceResult | None = None
    youtube_published: bool = False
    tiktok_published: bool = False
    tiktok_publish_id: str = ""
    scheduled_at: datetime | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return (
            self.compliance is not None
            and self.compliance.passed
            and not self.errors
        )

    def print_report(self) -> None:
        print("\n" + "=" * 60)
        print("  COMPLIANCE REVIEW REPORT")
        print("=" * 60)

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
        if "#Shorts" not in title and "#shorts" not in title.lower():
            title = f"{title} #Shorts".strip()
        if "#Shorts" not in description and "#shorts" not in description.lower():
            description = f"{description}\n#Shorts".strip()

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

    # ------------------------------------------------------------------ #
    # Step 3: Publish on YouTube
    # ------------------------------------------------------------------ #
    if "youtube" in request.platforms:
        try:
            if request.schedule:
                yt.schedule_publish(video_id, request.schedule)
                outcome.scheduled_at = request.schedule
            else:
                yt.make_public(video_id)
            outcome.youtube_published = True
        except Exception as exc:
            outcome.errors.append(f"YouTube publish failed: {exc}")

    # ------------------------------------------------------------------ #
    # Step 4: Publish on TikTok
    # ------------------------------------------------------------------ #
    if "tiktok" in request.platforms:
        if not request.tiktok_account:
            outcome.errors.append("tiktok_account required but not provided.")
        else:
            try:
                from uploader.tk_uploader.tk_api import TikTokAPI

                tk = TikTokAPI(request.tiktok_account)
                tk.authenticate()

                creator_info = tk.query_creator_info()
                privacy_options = creator_info.get(
                    "privacy_level_options", ["SELF_ONLY"]
                )

                # Unaudited/sandbox apps 403 if we post public, even when
                # PUBLIC_TO_EVERYONE appears in privacy_level_options.
                if "SELF_ONLY" in privacy_options:
                    privacy = "SELF_ONLY"
                elif "PUBLIC_TO_EVERYONE" in privacy_options:
                    privacy = "PUBLIC_TO_EVERYONE"
                else:
                    privacy = privacy_options[0]
                if privacy != "PUBLIC_TO_EVERYONE":
                    compliance_logger.warning(
                        f"TikTok privacy set to {privacy}. "
                        "Public posting requires a passed Content Posting audit."
                    )

                publish_id = tk.upload_video(
                    file_path=request.video_file,
                    title=request.title,
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
        p for p in sorted(directory.iterdir())
        if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
    ]
    if not files:
        raise FileNotFoundError(f"No video files found in {directory}")
    return files


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
) -> list[ReviewOutcome]:
    """Review each video, then schedule YouTube public times so posts are staggered."""
    platforms = platforms or ["youtube"]
    videos = list_videos_in_dir(directory)
    if len(videos) > YOUTUBE_DAILY_UPLOAD_BUDGET:
        raise RuntimeError(
            f"Found {len(videos)} videos, but YouTube API quota allows about "
            f"{YOUTUBE_DAILY_UPLOAD_BUDGET} uploads per day. Split the folder or run again tomorrow."
        )

    planned = plan_batch_schedule(videos, per_day=per_day, daily_hours=daily_hours, start_days=start_days)

    print("Batch schedule (YouTube goes public at these local times):")
    print("-" * 60)
    for video, when in planned:
        title, _, _ = _title_from_sidecar(video)
        print(f"  {when.strftime('%Y-%m-%d %H:%M')}  {video.name}  ({title})")
    print("-" * 60)
    print(f"Pace: {per_day}/day. API uploads happen now; public time is delayed.")
    if "tiktok" in platforms:
        print("TikTok: posted now as SELF_ONLY (unaudited), with a pause between items.")
    print()

    if dry_run:
        print("Dry run only. No uploads.")
        return []

    outcomes: list[ReviewOutcome] = []
    tiktok_count = 0
    for index, (video, when) in enumerate(planned):
        title, desc, tags = _title_from_sidecar(video)
        request = ReviewRequest(
            video_file=video,
            title=title,
            description=desc,
            tags=tags,
            youtube_account=youtube_account,
            tiktok_account=tiktok_account,
            platforms=platforms,
            schedule=when,
            shorts=shorts,
        )
        print(f"[{index + 1}/{len(planned)}] {video.name} -> {when.strftime('%Y-%m-%d %H:%M')}")
        outcome = run_review(request)
        outcome.print_report()
        outcomes.append(outcome)

        if "tiktok" in platforms and outcome.tiktok_published:
            tiktok_count += 1
            if index < len(planned) - 1:
                print(f"Waiting {tiktok_pause_seconds}s before the next TikTok upload …")
                time.sleep(tiktok_pause_seconds)
        elif index < len(planned) - 1:
            time.sleep(3)

    passed = sum(1 for o in outcomes if o.compliance and o.compliance.passed)
    print(f"Batch finished: {passed}/{len(outcomes)} passed YouTube compliance.")
    return outcomes

