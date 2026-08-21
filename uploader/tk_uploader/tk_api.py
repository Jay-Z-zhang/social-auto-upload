# -*- coding: utf-8 -*-
"""TikTok Content Posting API client for the compliance pre-review workflow.

Flow: query creator info -> init direct-post upload (FILE_UPLOAD) -> PUT bytes
      to upload_url -> poll publish status until PUBLISH_COMPLETE or FAILED.

Requires a TikTok developer app with Content Posting API enabled and OAuth 2.0
authorization from the target creator account.

See: https://developers.tiktok.com/doc/content-posting-api-get-started
"""
from __future__ import annotations

import json
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from conf import BASE_DIR
from utils.log import tiktok_logger

try:
    from conf import TIKTOK_CLIENT_KEY
except ImportError:
    TIKTOK_CLIENT_KEY = ""

try:
    from conf import TIKTOK_CLIENT_SECRET
except ImportError:
    TIKTOK_CLIENT_SECRET = ""

try:
    from conf import COMPLIANCE_POLL_INTERVAL
except ImportError:
    COMPLIANCE_POLL_INTERVAL = 10

try:
    from conf import COMPLIANCE_POLL_TIMEOUT
except ImportError:
    COMPLIANCE_POLL_TIMEOUT = 600

API_BASE = "https://open.tiktokapis.com"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = f"{API_BASE}/v2/oauth/token/"

_TOKEN_DIR = Path(BASE_DIR) / "cookies"
_TOKEN_DIR.mkdir(exist_ok=True)


@dataclass
class TikTokPublishResult:
    """Outcome of a TikTok publish attempt."""

    success: bool
    publish_id: str = ""
    status: str = ""
    fail_reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        if self.success:
            return f"TikTok publish {self.publish_id} completed successfully."
        parts = [f"TikTok publish {self.publish_id} FAILED."]
        if self.fail_reason:
            parts.append(f"Reason: {self.fail_reason}")
        if self.status:
            parts.append(f"Status: {self.status}")
        return " ".join(parts)


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Tiny HTTP handler to capture the OAuth redirect."""

    auth_code: str | None = None

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        code = qs.get("code", [None])[0]
        if code:
            _OAuthCallbackHandler.auth_code = code
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Authorization successful. You may close this tab.")
        else:
            self.send_response(400)
            self.end_headers()
            error = qs.get("error", ["unknown"])[0]
            self.wfile.write(f"Authorization failed: {error}".encode())

    def log_message(self, format, *args):
        pass


class TikTokAPI:
    """Thin wrapper around the TikTok Content Posting API."""

    REQUIRED_SCOPES = "user.info.basic,video.publish"

    def __init__(self, account_name: str):
        self.account_name = account_name
        self._token_path = _TOKEN_DIR / f"tiktok_oauth_{account_name}.json"
        self._access_token: str = ""
        self._open_id: str = ""

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _save_token(self, data: dict) -> None:
        data["_saved_at"] = time.time()
        self._token_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load_token(self) -> dict | None:
        if not self._token_path.exists():
            return None
        try:
            return json.loads(self._token_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _refresh_token(self, refresh_token: str) -> dict:
        resp = httpx.post(
            TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": TIKTOK_CLIENT_KEY,
                "client_secret": TIKTOK_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def authenticate(self) -> None:
        """Load cached token or run the OAuth 2.0 authorization code flow."""
        if not TIKTOK_CLIENT_KEY or not TIKTOK_CLIENT_SECRET:
            raise RuntimeError(
                "TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET must be set in conf.py"
            )

        cached = self._load_token()

        if cached and cached.get("refresh_token"):
            saved_at = cached.get("_saved_at", 0)
            expires_in = cached.get("refresh_expires_in", 0)
            if time.time() - saved_at < expires_in - 300:
                if time.time() - saved_at < cached.get("expires_in", 0) - 60:
                    self._access_token = cached["access_token"]
                    self._open_id = cached.get("open_id", "")
                    tiktok_logger.info("Using cached TikTok access token.")
                    return
                tiktok_logger.info("Refreshing TikTok access token …")
                token_data = self._refresh_token(cached["refresh_token"])
                self._access_token = token_data["access_token"]
                self._open_id = token_data.get("open_id", cached.get("open_id", ""))
                token_data["open_id"] = self._open_id
                self._save_token(token_data)
                return

        # TikTok Login Kit requires https://. GitHub Pages receives the code,
        # then bounces the browser to the local callback server.
        redirect_uri = "https://www.jayzzhang.online/social-auto-upload/tiktok-callback.html"
        params = {
            "client_key": TIKTOK_CLIENT_KEY,
            "response_type": "code",
            "scope": self.REQUIRED_SCOPES,
            "redirect_uri": redirect_uri,
            "state": "sau_compliance",
        }
        auth_url = f"{AUTH_URL}?{urlencode(params)}"

        tiktok_logger.info("Opening browser for TikTok authorization …")
        print(f"Please visit this URL to authorize TikTok: {auth_url}")
        webbrowser.open(auth_url)

        _OAuthCallbackHandler.auth_code = None
        server = HTTPServer(("localhost", 19876), _OAuthCallbackHandler)
        server.timeout = 300
        while _OAuthCallbackHandler.auth_code is None:
            server.handle_request()
        server.server_close()

        code = _OAuthCallbackHandler.auth_code
        if not code:
            raise RuntimeError("TikTok authorization failed: no auth code received.")

        resp = httpx.post(
            TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": TIKTOK_CLIENT_KEY,
                "client_secret": TIKTOK_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        resp.raise_for_status()
        token_data = resp.json()

        if "access_token" not in token_data:
            raise RuntimeError(f"TikTok token exchange failed: {token_data}")

        self._access_token = token_data["access_token"]
        self._open_id = token_data.get("open_id", "")
        self._save_token(token_data)
        tiktok_logger.success(f"TikTok API authenticated (account: {self.account_name})")

    @property
    def access_token(self) -> str:
        if not self._access_token:
            self.authenticate()
        return self._access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    # ------------------------------------------------------------------
    # Creator info (required before posting per TikTok UX guidelines)
    # ------------------------------------------------------------------

    def query_creator_info(self) -> dict[str, Any]:
        """Fetch creator's privacy options, avatar, username, etc."""
        resp = httpx.post(
            f"{API_BASE}/v2/post/publish/creator_info/query/",
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error", {}).get("code") != "ok":
            raise RuntimeError(f"TikTok creator info query failed: {data}")
        tiktok_logger.info(f"Creator info retrieved for {self.account_name}.")
        return data.get("data", {})

    # ------------------------------------------------------------------
    # Upload & publish
    # ------------------------------------------------------------------

    def init_video_upload(
        self,
        file_path: str | Path,
        title: str,
        privacy_level: str = "SELF_ONLY",
        disable_comment: bool = False,
        disable_duet: bool = False,
        disable_stitch: bool = False,
    ) -> dict[str, Any]:
        """Initialize a direct-post video upload via FILE_UPLOAD mode.

        Returns dict with ``publish_id`` and ``upload_url``.
        """
        file_path = Path(file_path)
        file_size = file_path.stat().st_size
        chunk_size = min(file_size, 64 * 1024 * 1024)
        total_chunk_count = max(1, -(-file_size // chunk_size))

        body: dict[str, Any] = {
            "post_info": {
                "title": title[:2200],
                "privacy_level": privacy_level,
                "disable_comment": disable_comment,
                "disable_duet": disable_duet,
                "disable_stitch": disable_stitch,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunk_count,
            },
        }

        resp = httpx.post(
            f"{API_BASE}/v2/post/publish/video/init/",
            headers=self._headers(),
            json=body,
            timeout=30,
        )
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or data.get("error", {}).get("code") not in (None, "ok"):
            raise RuntimeError(f"TikTok video init failed ({resp.status_code}): {data}")

        result = data.get("data", {})
        tiktok_logger.info(f"TikTok upload initialized. publish_id={result.get('publish_id')}")
        return result

    def upload_video_chunks(self, upload_url: str, file_path: str | Path) -> None:
        """PUT the video file bytes to the TikTok upload URL."""
        file_path = Path(file_path)
        file_size = file_path.stat().st_size

        tiktok_logger.info(f"Uploading {file_path.name} to TikTok ({file_size / 1024 / 1024:.1f} MB) …")

        with open(file_path, "rb") as f:
            data = f.read()

        headers = {
            "Content-Type": "video/mp4",
            "Content-Length": str(file_size),
            "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
        }

        resp = httpx.put(upload_url, headers=headers, content=data, timeout=600)
        resp.raise_for_status()
        tiktok_logger.success("TikTok file upload complete.")

    def upload_video(
        self,
        file_path: str | Path,
        title: str,
        privacy_level: str = "SELF_ONLY",
    ) -> str:
        """Full upload flow: init + PUT file. Returns the publish_id."""
        init_data = self.init_video_upload(
            file_path=file_path, title=title, privacy_level=privacy_level,
        )
        publish_id = init_data["publish_id"]
        upload_url = init_data["upload_url"]
        self.upload_video_chunks(upload_url, file_path)
        return publish_id

    # ------------------------------------------------------------------
    # Status polling
    # ------------------------------------------------------------------

    def poll_status(self, publish_id: str) -> TikTokPublishResult:
        """Poll until the publish reaches a terminal state."""
        tiktok_logger.info(f"Polling TikTok publish status for {publish_id} …")
        deadline = time.time() + COMPLIANCE_POLL_TIMEOUT

        while time.time() < deadline:
            resp = httpx.post(
                f"{API_BASE}/v2/post/publish/status/fetch/",
                headers=self._headers(),
                json={"publish_id": publish_id},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            status = data.get("status", "")

            if status == "PUBLISH_COMPLETE":
                tiktok_logger.success(f"TikTok publish complete: {publish_id}")
                return TikTokPublishResult(
                    success=True, publish_id=publish_id, status=status, details=data,
                )

            if status == "FAILED":
                reason = data.get("fail_reason", "unknown")
                tiktok_logger.error(f"TikTok publish failed: {reason}")
                return TikTokPublishResult(
                    success=False, publish_id=publish_id, status=status,
                    fail_reason=reason, details=data,
                )

            tiktok_logger.info(
                f"TikTok status: {status} (checking again in {COMPLIANCE_POLL_INTERVAL}s)"
            )
            time.sleep(COMPLIANCE_POLL_INTERVAL)

        tiktok_logger.warning("TikTok status poll timed out.")
        return TikTokPublishResult(
            success=False, publish_id=publish_id, status="TIMEOUT",
            fail_reason="Poll timed out", details={},
        )
