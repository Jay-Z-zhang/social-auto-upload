# -*- coding: utf-8 -*-
"""YouTube Data API v3 client for the compliance pre-review workflow.

Flow: upload as *private* -> poll processing -> check for rejections/claims ->
      make public (or scheduled) only after passing review.

Requires a Google Cloud project with the YouTube Data API v3 enabled and OAuth 2.0
desktop-app credentials saved as ``client_secret.json`` (configurable via
``conf.YOUTUBE_CLIENT_SECRET_FILE``).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from conf import BASE_DIR
from utils.log import youtube_logger

try:
    from conf import YOUTUBE_CLIENT_SECRET_FILE
except ImportError:
    YOUTUBE_CLIENT_SECRET_FILE = "client_secret.json"

try:
    from conf import COMPLIANCE_POLL_INTERVAL
except ImportError:
    COMPLIANCE_POLL_INTERVAL = 10

try:
    from conf import COMPLIANCE_POLL_TIMEOUT
except ImportError:
    COMPLIANCE_POLL_TIMEOUT = 600

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

_TOKEN_DIR = Path(BASE_DIR) / "cookies"
_TOKEN_DIR.mkdir(exist_ok=True)


@dataclass
class ComplianceResult:
    """Outcome of the YouTube compliance check."""

    passed: bool
    video_id: str
    upload_status: str = ""
    rejection_reason: str = ""
    licensed_content: bool = False
    processing_status: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        if self.passed:
            return f"Video {self.video_id} passed compliance checks."
        parts = [f"Video {self.video_id} FAILED compliance."]
        if self.rejection_reason:
            parts.append(f"Rejection reason: {self.rejection_reason}")
        if self.upload_status:
            parts.append(f"Upload status: {self.upload_status}")
        return " ".join(parts)


class YouTubeAPI:
    """Thin wrapper around the YouTube Data API v3."""

    def __init__(self, account_name: str):
        self.account_name = account_name
        self._token_path = _TOKEN_DIR / f"youtube_oauth_{account_name}.json"
        self._creds: Credentials | None = None
        self._service = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _client_secret_path(self) -> Path:
        p = Path(YOUTUBE_CLIENT_SECRET_FILE)
        if not p.is_absolute():
            p = Path(BASE_DIR) / p
        if not p.exists():
            raise FileNotFoundError(
                f"YouTube OAuth client secret file not found: {p}\n"
                "Download it from Google Cloud Console -> APIs & Services -> Credentials."
            )
        return p

    def authenticate(self) -> None:
        """Load cached credentials or run the OAuth consent flow."""
        creds: Credentials | None = None

        if self._token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self._token_path), SCOPES)

        if creds and creds.expired and creds.refresh_token:
            youtube_logger.info("Refreshing expired YouTube OAuth token …")
            creds.refresh(Request())
        elif not creds or not creds.valid:
            youtube_logger.info("Starting YouTube OAuth consent flow (browser will open) …")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self._client_secret_path()), SCOPES
            )
            creds = flow.run_local_server(
                host="localhost",
                bind_addr="127.0.0.1",
                port=8080,
                open_browser=True,
                access_type="offline",
                prompt="consent",
            )

        self._token_path.write_text(creds.to_json(), encoding="utf-8")
        self._creds = creds
        self._service = build("youtube", "v3", credentials=creds)
        youtube_logger.success(f"YouTube API authenticated (account: {self.account_name})")

    @property
    def service(self):
        if self._service is None:
            self.authenticate()
        return self._service

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_private(
        self,
        file_path: str | Path,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        category_id: str = "22",
        made_for_kids: bool = False,
        contains_synthetic_media: bool = False,
    ) -> str:
        """Upload a video as *private*. Returns the video ID."""
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Video file not found: {file_path}")

        body: dict[str, Any] = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": tags or [],
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": "private",
                "selfDeclaredMadeForKids": made_for_kids,
            },
        }
        if contains_synthetic_media:
            body["status"]["containsSyntheticMedia"] = True

        media = MediaFileUpload(
            str(file_path), mimetype="video/*", resumable=True, chunksize=10 * 1024 * 1024,
        )

        youtube_logger.info(f"Uploading {file_path.name} as private via YouTube Data API …")
        request = self.service.videos().insert(
            part="snippet,status", body=body, media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                youtube_logger.info(f"Upload progress: {pct}%")

        video_id = response["id"]
        youtube_logger.success(f"Upload complete. Video ID: {video_id} (private)")
        return video_id

    # ------------------------------------------------------------------
    # Processing & compliance
    # ------------------------------------------------------------------

    def poll_processing(self, video_id: str) -> dict[str, Any]:
        """Block until YouTube finishes processing. Returns the video resource."""
        youtube_logger.info(f"Waiting for YouTube to process video {video_id} …")
        deadline = time.time() + COMPLIANCE_POLL_TIMEOUT

        while time.time() < deadline:
            resp = self.service.videos().list(
                part="processingDetails,status,contentDetails", id=video_id,
            ).execute()

            items = resp.get("items", [])
            if not items:
                raise RuntimeError(f"Video {video_id} not found (may have been deleted).")

            video = items[0]
            proc = video.get("processingDetails", {})
            proc_status = proc.get("processingStatus", "")

            if proc_status not in ("processing", ""):
                youtube_logger.info(f"Processing finished: {proc_status}")
                return video

            youtube_logger.info(
                f"Still processing … (checking again in {COMPLIANCE_POLL_INTERVAL}s)"
            )
            time.sleep(COMPLIANCE_POLL_INTERVAL)

        youtube_logger.warning("Processing poll timed out; returning last known state.")
        resp = self.service.videos().list(
            part="processingDetails,status,contentDetails", id=video_id,
        ).execute()
        return resp["items"][0] if resp.get("items") else {}

    def check_compliance(self, video_id: str) -> ComplianceResult:
        """Poll processing, then inspect the video resource for rejections."""
        video = self.poll_processing(video_id)
        if not video:
            return ComplianceResult(
                passed=False, video_id=video_id, upload_status="unknown",
                details={"error": "Video resource not found after processing."},
            )

        status = video.get("status", {})
        content = video.get("contentDetails", {})
        proc = video.get("processingDetails", {})

        upload_status = status.get("uploadStatus", "")
        rejection = status.get("rejectionReason", "")
        licensed = content.get("licensedContent", False)
        proc_status = proc.get("processingStatus", "")

        passed = upload_status in ("processed", "uploaded") and not rejection

        result = ComplianceResult(
            passed=passed,
            video_id=video_id,
            upload_status=upload_status,
            rejection_reason=rejection,
            licensed_content=licensed,
            processing_status=proc_status,
            details={"status": status, "contentDetails": content, "processingDetails": proc},
        )

        if passed:
            youtube_logger.success(result.summary)
        else:
            youtube_logger.error(result.summary)

        return result

    # ------------------------------------------------------------------
    # Publish / update
    # ------------------------------------------------------------------

    def make_public(self, video_id: str) -> None:
        """Switch a private video to public."""
        self.service.videos().update(
            part="status",
            body={"id": video_id, "status": {"privacyStatus": "public"}},
        ).execute()
        youtube_logger.success(f"Video {video_id} is now PUBLIC.")

    def schedule_publish(self, video_id: str, publish_at: datetime) -> None:
        """Schedule a private video to go public at *publish_at* (UTC ISO-8601)."""
        iso = publish_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self.service.videos().update(
            part="status",
            body={
                "id": video_id,
                "status": {"privacyStatus": "private", "publishAt": iso},
            },
        ).execute()
        youtube_logger.success(f"Video {video_id} scheduled to publish at {iso}.")

    def delete_video(self, video_id: str) -> None:
        """Delete a video (e.g. after a failed compliance check)."""
        self.service.videos().delete(id=video_id).execute()
        youtube_logger.info(f"Video {video_id} deleted.")
