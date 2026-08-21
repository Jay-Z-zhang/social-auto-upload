# -*- coding: utf-8 -*-
"""Compliance pre-review orchestrator.

Coordinates the "upload private to YouTube -> check compliance -> publish
everywhere" workflow. YouTube acts as the compliance gate: if the video passes
YouTube's automated content/copyright checks, it is published on YouTube and
(optionally) uploaded to TikTok.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

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


@dataclass
class ReviewOutcome:
    """Final result of the full review-and-publish pipeline."""

    compliance: ComplianceResult | None = None
    youtube_published: bool = False
    tiktok_published: bool = False
    tiktok_publish_id: str = ""
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

    try:
        video_id = yt.upload_private(
            file_path=request.video_file,
            title=request.title,
            description=request.description,
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
