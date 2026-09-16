# -*- coding: utf-8 -*-
"""Local web UI for `sau review-batch`.

Run from the project root:

    python sau_review_web.py

Then open http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from conf import BASE_DIR
from uploader.compliance import (
    YOUTUBE_DAILY_UPLOAD_BUDGET,
    _title_from_sidecar,
    list_videos_in_dir,
    plan_batch_schedule,
)
from uploader.cover import CoverSpec, default_cover_path, make_cover
from uploader.episodes import EpisodeMeta, dump_episodes, load_episodes
from uploader.series import extract_ep_number, load_series
from uploader.manifest import (
    DB_PATH as MANIFEST_DB,
    forget as manifest_forget,
    list_states as manifest_list_states,
    summary as manifest_summary,
)

UI_FILE = BASE_DIR / "sau_review_ui" / "index.html"
WORKSPACE_UI_FILE = BASE_DIR / "sau_review_ui" / "workspace.html"
PROGRESS_UI_FILE = BASE_DIR / "sau_review_ui" / "progress.html"
CURRENT_BATCH_FILE = BASE_DIR / "db" / "current_batch.json"
DEFAULT_PORT = 8765
COOKIE_DIR = BASE_DIR / "cookies"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_job_lock = threading.Lock()
_job: dict = {
    "running": False,
    "logs": [],
    "returncode": None,
    "proc": None,
}


def parse_hours(raw: str) -> list[int]:
    hours: list[int] = []
    for part in raw.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            part = part.split(":", 1)[0].strip()
        hours.append(int(part))
    if not hours:
        raise ValueError("至少填写一个发布时间（例如 12,19）")
    for hour in hours:
        if hour < 0 or hour > 23:
            raise ValueError(f"小时必须在 0–23 之间：{hour}")
    return hours


def _list_accounts(prefix: str) -> list[str]:
    if not COOKIE_DIR.is_dir():
        return []
    names: list[str] = []
    for path in sorted(COOKIE_DIR.glob(f"{prefix}*.json")):
        name = path.stem.removeprefix(prefix)
        if name:
            names.append(name)
    return names


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length") or "0")
    raw = handler.rfile.read(length) if length else b"{}"
    if not raw:
        return {}
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def _send_json(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _send_html(handler: BaseHTTPRequestHandler) -> None:
    html = UI_FILE.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(html)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(html)


def _video_payload(directory: Path) -> dict:
    videos = list_videos_in_dir(directory)
    items = []
    for video in videos:
        title, desc, tags = _title_from_sidecar(video)
        items.append(
            {
                "name": video.name,
                "title": title,
                "description": desc,
                "tags": tags,
                "has_sidecar": video.with_suffix(".txt").exists(),
            }
        )
    return {
        "dir": str(directory),
        "count": len(items),
        "quota": YOUTUBE_DAILY_UPLOAD_BUDGET,
        "over_quota": len(items) > YOUTUBE_DAILY_UPLOAD_BUDGET,
        "videos": items,
    }


def _preview_payload(data: dict) -> dict:
    directory = Path(str(data.get("dir") or "")).expanduser()
    if not directory.is_dir():
        raise ValueError(f"找不到文件夹：{directory}")
    per_day = int(data.get("per_day") or 2)
    start_days = int(data.get("start_days") or 1)
    hours = parse_hours(str(data.get("times") or "12,19"))
    if per_day < 1:
        raise ValueError("每天条数至少为 1")
    scanned = _video_payload(directory)
    planned = plan_batch_schedule(
        [directory / item["name"] for item in scanned["videos"]],
        per_day=per_day,
        daily_hours=hours,
        start_days=start_days,
    )
    schedule = []
    for video, when in planned:
        title, _, _ = _title_from_sidecar(video)
        schedule.append(
            {
                "file": video.name,
                "title": title,
                "when": when.strftime("%Y-%m-%d %H:%M"),
            }
        )
    scanned["schedule"] = schedule
    scanned["per_day"] = per_day
    scanned["times"] = hours
    scanned["start_days"] = start_days
    return scanned


def _append_log(line: str) -> None:
    line = _ANSI.sub("", line)
    with _job_lock:
        _job["logs"].append(line)
        if len(_job["logs"]) > 4000:
            _job["logs"] = _job["logs"][-3000:]


def _run_subprocess(cmd: list[str]) -> None:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    with _job_lock:
        _job["proc"] = proc
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            _append_log(line.rstrip("\n"))
        returncode = proc.wait()
    except Exception as exc:
        _append_log(f"[web] 读取日志失败：{exc}")
        returncode = proc.poll()
        if returncode is None:
            proc.kill()
            returncode = proc.wait()
    with _job_lock:
        _job["running"] = False
        _job["returncode"] = returncode
        _job["proc"] = None
    _append_log(f"[web] 结束，退出码 {returncode}")


def _start_job(data: dict) -> dict:
    with _job_lock:
        if _job["running"]:
            raise RuntimeError("已有任务在运行，请先停止或等它结束")
        _job["running"] = True
        _job["logs"] = []
        _job["returncode"] = None
        _job["proc"] = None

    directory = Path(str(data.get("dir") or "")).expanduser()
    if not directory.is_dir():
        with _job_lock:
            _job["running"] = False
        raise ValueError(f"找不到文件夹：{directory}")

    youtube_account = str(data.get("youtube_account") or "").strip()
    if not youtube_account:
        with _job_lock:
            _job["running"] = False
        raise ValueError("请填写 YouTube 账号名")

    tiktok_account = str(data.get("tiktok_account") or "").strip()
    platforms = [p.strip().lower() for p in (data.get("platforms") or ["youtube"]) if str(p).strip()]
    if not platforms:
        platforms = ["youtube"]
    if "tiktok" in platforms and not tiktok_account:
        with _job_lock:
            _job["running"] = False
        raise ValueError("勾选 TikTok 时需要填写 TikTok 账号名")

    per_day = int(data.get("per_day") or 2)
    start_days = int(data.get("start_days") or 1)
    hours = parse_hours(str(data.get("times") or "12,19"))
    dry_run = bool(data.get("dry_run"))
    shorts = bool(data.get("shorts"))

    cmd = [
        sys.executable,
        str(BASE_DIR / "sau_cli.py"),
        "review-batch",
        "--dir",
        str(directory),
        "--youtube-account",
        youtube_account,
        "--platforms",
        ",".join(platforms),
        "--per-day",
        str(per_day),
        "--times",
        ",".join(str(h) for h in hours),
        "--start-days",
        str(start_days),
    ]
    if tiktok_account and "tiktok" in platforms:
        cmd.extend(["--tiktok-account", tiktok_account])
    tiktok_privacy = str(data.get("tiktok_privacy") or "auto").strip() or "auto"
    if tiktok_privacy != "auto":
        cmd.extend(["--tiktok-privacy", tiktok_privacy])
    if shorts:
        cmd.append("--shorts")
    if dry_run:
        cmd.append("--dry-run")

    if not dry_run:
        _persist_current_batch(directory, cmd)

    _append_log("$ " + " ".join(cmd))
    thread = threading.Thread(target=_run_subprocess, args=(cmd,), daemon=True)
    thread.start()
    return {"ok": True, "dry_run": dry_run, "cmd": cmd}


def _persist_current_batch(directory: Path, cmd: list[str]) -> None:
    """Record the running batch so a service restart or a browser reload can find it.

    The file lives at db/current_batch.json — cheap to write, tolerant to corruption
    (worst case: `/api/progress` says no batch is known).
    """
    import os
    try:
        CURRENT_BATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "dir": str(directory),
            "cmd": cmd,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pid": os.getpid(),  # this python process, not the child — child pid captured on start
        }
        CURRENT_BATCH_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        _append_log(f"[web] warn: could not persist current batch: {exc}")


def _read_current_batch() -> dict | None:
    if not CURRENT_BATCH_FILE.is_file():
        return None
    try:
        return json.loads(CURRENT_BATCH_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _job_status() -> dict:
    with _job_lock:
        return {
            "running": _job["running"],
            "logs": list(_job["logs"]),
            "returncode": _job["returncode"],
        }


def _stop_job() -> dict:
    with _job_lock:
        proc = _job["proc"]
        running = _job["running"]
    if not running or proc is None:
        return {"ok": True, "stopped": False}
    proc.terminate()
    _append_log("[web] 已请求停止任务")
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        _append_log("[web] 强制结束进程")
    with _job_lock:
        _job["running"] = False
        _job["returncode"] = proc.returncode
        _job["proc"] = None
    return {"ok": True, "stopped": True}


# ---------------------------------------------------------------------------
# Workspace: cover + episode-metadata review page
# ---------------------------------------------------------------------------

def _resolve_workspace_dir(raw: str) -> Path:
    directory = Path(raw).expanduser()
    if not directory.is_absolute():
        directory = (BASE_DIR / directory).resolve()
    if not directory.is_dir():
        raise ValueError(f"找不到文件夹：{directory}")
    return directory


def _workspace_data(raw_dir: str) -> dict:
    directory = _resolve_workspace_dir(raw_dir)
    series = load_series(directory)
    episodes = load_episodes(directory)
    videos = list_videos_in_dir(directory)
    states = {s.ep: s for s in manifest_list_states(directory)}

    items = []
    for video in videos:
        ep = extract_ep_number(video)
        meta = episodes.get(ep) if ep is not None else None
        cover = default_cover_path(video)
        state = states.get(ep) if ep is not None else None
        if state is None:
            status = "pending"
        else:
            want_shorts = bool(state.shorts_video_id) or "shorts" in (state.last_error or "").lower()
            want_tt = bool(state.tt_publish_id) or "tiktok" in (state.last_error or "").lower()
            status = state.status_label(want_shorts, want_tt)
        items.append({
            "file": video.name,
            "ep": ep,
            "has_cover": cover.exists(),
            "cover_url": f"/api/workspace/cover-file?dir={directory}&name={cover.name}" if cover.exists() else "",
            "theme": meta.theme if meta else "",
            "hook": meta.hook if meta else "",
            "description": meta.description if meta else "",
            "tags": meta.tags if meta else [],
            "tt_tags": meta.tt_tags if meta else [],
            "status": status,
        })

    return {
        "dir": str(directory),
        "series": {
            "name": series.name,
            "total_eps": series.total_eps,
            "active": series.is_active(),
        },
        "episodes_yaml_exists": bool(episodes),
        "items": items,
    }


def _workspace_save(data: dict) -> dict:
    directory = _resolve_workspace_dir(str(data.get("dir") or ""))
    ep_raw = data.get("ep")
    if ep_raw is None:
        raise ValueError("ep is required")
    ep = int(ep_raw)

    episodes = load_episodes(directory)
    current = episodes.get(ep) or EpisodeMeta(ep=ep)
    current.theme = str(data.get("theme", current.theme)).strip()
    current.hook = str(data.get("hook", current.hook)).strip()
    current.description = str(data.get("description", current.description)).strip()
    current.tags = [str(t).lstrip("#").strip() for t in (data.get("tags") or []) if str(t).strip()]
    current.tt_tags = [str(t).lstrip("#").strip() for t in (data.get("tt_tags") or []) if str(t).strip()]
    episodes[ep] = current

    path = dump_episodes(directory, episodes)
    return {"ok": True, "path": str(path), "ep": ep}


def _workspace_regen_cover(data: dict) -> dict:
    directory = _resolve_workspace_dir(str(data.get("dir") or ""))
    file_name = str(data.get("file") or "").strip()
    if not file_name:
        raise ValueError("file is required")
    video = directory / file_name
    if not video.is_file():
        raise ValueError(f"视频不存在：{video}")

    ep = extract_ep_number(video)
    if ep is None:
        raise ValueError(f"从文件名 {file_name} 提不到集数，无法生成封面")

    series = load_series(directory)
    episodes = load_episodes(directory)
    meta = episodes.get(ep)
    theme = str(data.get("theme") or (meta.theme if meta else "")).strip()

    make_cover(
        video,
        CoverSpec(
            ep=ep,
            series_name=series.name,
            theme=theme,
            total_eps=series.total_eps,
        ),
        overwrite=True,
    )
    cover = default_cover_path(video)
    return {
        "ok": True,
        "cover_url": f"/api/workspace/cover-file?dir={directory}&name={cover.name}&t={cover.stat().st_mtime_ns}",
    }


def _send_workspace_cover(handler: BaseHTTPRequestHandler, params: dict[str, list[str]]) -> None:
    raw_dir = (params.get("dir") or [""])[0]
    name = (params.get("name") or [""])[0]
    if not raw_dir or not name or "/" in name or ".." in name:
        handler.send_error(400)
        return
    directory = _resolve_workspace_dir(raw_dir)
    path = directory / name
    if not path.is_file() or path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
        handler.send_error(404)
        return
    body = path.read_bytes()
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _send_workspace_html(handler: BaseHTTPRequestHandler) -> None:
    if not WORKSPACE_UI_FILE.is_file():
        handler.send_error(500, "workspace.html not found")
        return
    html = WORKSPACE_UI_FILE.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(html)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(html)


def _send_progress_html(handler: BaseHTTPRequestHandler) -> None:
    if not PROGRESS_UI_FILE.is_file():
        handler.send_error(500, "progress.html not found")
        return
    html = PROGRESS_UI_FILE.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(html)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(html)


# ---------------------------------------------------------------------------
# Progress: live-status view of a batch backed by the sqlite manifest
# ---------------------------------------------------------------------------

_ACTIVE_EP_RE = re.compile(r"\[(\d+)/(\d+)\]\s+(\S+?)\s*(?:->|→|-\>)")


def _active_episode_from_logs() -> dict | None:
    """Tail the in-memory logs for the latest `[X/Y] filename -> when` line.

    Returns None if no such line exists yet (batch hasn't started printing).
    """
    with _job_lock:
        logs = list(_job["logs"])
    for line in reversed(logs):
        m = _ACTIVE_EP_RE.search(line)
        if m:
            return {
                "index": int(m.group(1)),
                "total": int(m.group(2)),
                "file": m.group(3),
            }
    return None


def _progress_payload(raw_dir: str) -> dict:
    directory = _resolve_workspace_dir(raw_dir)
    series = load_series(directory)
    episodes = load_episodes(directory)
    try:
        videos = list_videos_in_dir(directory)
    except FileNotFoundError:
        videos = []
    states = {s.ep: s for s in manifest_list_states(directory)}
    counts = manifest_summary(directory)

    active = _active_episode_from_logs()
    active_file = active["file"] if active else ""

    # Union: rows in the manifest with no matching video file (rare — video
    # renamed/moved) still show up so history is complete.
    seen_eps = set()
    items = []
    for video in videos:
        ep = extract_ep_number(video)
        seen_eps.add(ep)
        state = states.get(ep) if ep is not None else None
        meta = episodes.get(ep) if ep is not None else None
        status = _status_from_state(state)
        items.append(_progress_row(video.name, ep, meta, state, status, video.name == active_file))
    for ep, state in states.items():
        if ep in seen_eps:
            continue
        meta = episodes.get(ep)
        status = _status_from_state(state)
        items.append(_progress_row(state.source_file or f"ep_{ep}", ep, meta, state, status, False))
    items.sort(key=lambda it: (it["ep"] is None, it["ep"] or 0))

    current_batch = _read_current_batch()

    with _job_lock:
        job_running = _job["running"]
        job_returncode = _job["returncode"]

    return {
        "dir": str(directory),
        "series": {"name": series.name, "total_eps": series.total_eps, "active": series.is_active()},
        "items": items,
        "counts": counts,
        "active": active,
        "job": {"running": job_running, "returncode": job_returncode},
        "current_batch": current_batch,
    }


def _status_from_state(state) -> str:
    if state is None:
        return "pending"
    want_shorts = bool(state.shorts_video_id) or "shorts" in (state.last_error or "").lower()
    want_tt = bool(state.tt_publish_id) or "tiktok" in (state.last_error or "").lower()
    return state.status_label(want_shorts, want_tt)


def _progress_row(file_name: str, ep, meta, state, status: str, is_active: bool) -> dict:
    return {
        "file": file_name,
        "ep": ep,
        "theme": meta.theme if meta else "",
        "hook": meta.hook if meta else "",
        "status": status,
        "yt_video_id": state.yt_video_id if state else "",
        "yt_scheduled_at": state.yt_scheduled_at if state else "",
        "shorts_video_id": state.shorts_video_id if state else "",
        "tt_publish_id": state.tt_publish_id if state else "",
        "last_error": state.last_error if state else "",
        "updated_at": state.updated_at if state else "",
        "is_active": is_active,
    }


def _retry_episode(data: dict) -> dict:
    directory = _resolve_workspace_dir(str(data.get("dir") or ""))
    ep_raw = data.get("ep")
    if ep_raw is None:
        raise ValueError("ep is required")
    ep = int(ep_raw)

    videos = list_videos_in_dir(directory)
    match = None
    for v in videos:
        if extract_ep_number(v) == ep:
            match = v
            break
    if match is None:
        raise ValueError(f"未在 {directory} 找到第 {ep} 集的视频文件")

    with _job_lock:
        if _job["running"]:
            raise RuntimeError("已有任务在运行，请先等它结束或点停止")

    # Reuse account/platforms from the persisted batch if available; fall back
    # to explicit fields on the request.
    batch = _read_current_batch() or {}
    prior_cmd = batch.get("cmd") or []

    def _cmd_flag(name: str, default: str = "") -> str:
        if name in prior_cmd:
            idx = prior_cmd.index(name)
            return prior_cmd[idx + 1] if idx + 1 < len(prior_cmd) else default
        return default

    youtube_account = str(data.get("youtube_account") or _cmd_flag("--youtube-account")).strip()
    tiktok_account = str(data.get("tiktok_account") or _cmd_flag("--tiktok-account")).strip()
    platforms = str(data.get("platforms") or _cmd_flag("--platforms", "youtube")).strip() or "youtube"
    shorts_flag = "--shorts" in prior_cmd or bool(data.get("shorts"))
    yt_visibility = str(data.get("yt_visibility") or _cmd_flag("--yt-visibility", "public")).strip() or "public"

    if not youtube_account:
        raise ValueError("缺少 YouTube 账号（提供 youtube_account 或先跑一次批次）")

    cmd = [
        sys.executable,
        str(BASE_DIR / "sau_cli.py"),
        "review",
        "--file", str(match),
        "--title", "auto",  # ignored when series.yaml is loaded
        "--youtube-account", youtube_account,
        "--platforms", platforms,
        "--yt-visibility", yt_visibility,
    ]
    if tiktok_account and "tiktok" in platforms:
        cmd.extend(["--tiktok-account", tiktok_account])
    tiktok_privacy = str(data.get("tiktok_privacy") or _cmd_flag("--tiktok-privacy", "auto")).strip() or "auto"
    if tiktok_privacy != "auto":
        cmd.extend(["--tiktok-privacy", tiktok_privacy])
    if shorts_flag:
        cmd.append("--shorts")

    with _job_lock:
        _job["running"] = True
        _job["logs"] = []
        _job["returncode"] = None
        _job["proc"] = None
    _append_log(f"$ retry ep {ep}: " + " ".join(cmd))
    thread = threading.Thread(target=_run_subprocess, args=(cmd,), daemon=True)
    thread.start()
    return {"ok": True, "ep": ep, "file": match.name, "cmd": cmd}


def _reset_episode(data: dict) -> dict:
    directory = _resolve_workspace_dir(str(data.get("dir") or ""))
    ep_raw = data.get("ep")
    if ep_raw is None:
        n = manifest_forget(directory)
        return {"ok": True, "cleared": n, "all": True}
    n = manifest_forget(directory, int(ep_raw))
    return {"ok": True, "cleared": n, "ep": int(ep_raw)}


def _resume_current_batch() -> dict:
    """Re-launch the last persisted batch. Manifest guarantees idempotence."""
    batch = _read_current_batch()
    if not batch or not batch.get("cmd"):
        raise ValueError("没有找到上一次批次记录 (db/current_batch.json)")

    with _job_lock:
        if _job["running"]:
            raise RuntimeError("已有任务在运行，请先等它结束或点停止")
        _job["running"] = True
        _job["logs"] = []
        _job["returncode"] = None
        _job["proc"] = None

    cmd = batch["cmd"]
    _append_log("$ resume: " + " ".join(cmd))
    thread = threading.Thread(target=_run_subprocess, args=(cmd,), daemon=True)
    thread.start()
    return {"ok": True, "cmd": cmd}


class ReviewUIHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path in ("/", "/index.html"):
                _send_html(self)
                return
            if path == "/workspace":
                _send_workspace_html(self)
                return
            if path == "/progress":
                _send_progress_html(self)
                return
            if path == "/api/workspace/data":
                from urllib.parse import parse_qs
                qs = parse_qs(parsed.query)
                raw_dir = (qs.get("dir") or [""])[0]
                _send_json(self, _workspace_data(raw_dir))
                return
            if path == "/api/progress":
                from urllib.parse import parse_qs
                qs = parse_qs(parsed.query)
                raw_dir = (qs.get("dir") or [""])[0]
                _send_json(self, _progress_payload(raw_dir))
                return
            if path == "/api/current-batch":
                _send_json(self, {"batch": _read_current_batch()})
                return
            if path == "/api/workspace/cover-file":
                from urllib.parse import parse_qs
                _send_workspace_cover(self, parse_qs(parsed.query))
                return
            if path == "/api/accounts":
                _send_json(
                    self,
                    {
                        "youtube": _list_accounts("youtube_oauth_"),
                        "tiktok": _list_accounts("tiktok_oauth_"),
                    },
                )
                return
            if path == "/api/job":
                _send_json(self, _job_status())
                return
            self.send_error(404)
        except Exception as exc:
            _send_json(self, {"error": str(exc)}, status=500)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            data = _read_json(self)
            if path == "/api/scan":
                directory = Path(str(data.get("dir") or "")).expanduser()
                if not directory.is_dir():
                    raise ValueError(f"找不到文件夹：{directory}")
                _send_json(self, _video_payload(directory))
                return
            if path == "/api/preview":
                _send_json(self, _preview_payload(data))
                return
            if path == "/api/run":
                _send_json(self, _start_job(data))
                return
            if path == "/api/stop":
                _send_json(self, _stop_job())
                return
            if path == "/api/workspace/save":
                _send_json(self, _workspace_save(data))
                return
            if path == "/api/workspace/regen-cover":
                _send_json(self, _workspace_regen_cover(data))
                return
            if path == "/api/retry-episode":
                _send_json(self, _retry_episode(data))
                return
            if path == "/api/reset-episode":
                _send_json(self, _reset_episode(data))
                return
            if path == "/api/resume-batch":
                _send_json(self, _resume_current_batch())
                return
            self.send_error(404)
        except json.JSONDecodeError:
            _send_json(self, {"error": "请求不是合法 JSON"}, status=400)
        except (ValueError, RuntimeError, FileNotFoundError) as exc:
            _send_json(self, {"error": str(exc)}, status=400)
        except Exception as exc:
            _send_json(self, {"error": str(exc)}, status=500)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local UI for sau review-batch")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    if not UI_FILE.is_file():
        raise SystemExit(f"UI file missing: {UI_FILE}")

    class ReviewUIServer(ThreadingHTTPServer):
        allow_reuse_address = True

    server = ReviewUIServer((args.host, args.port), ReviewUIHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Review UI: {url}", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
