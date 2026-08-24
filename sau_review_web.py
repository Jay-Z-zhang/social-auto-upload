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
from pathlib import Path
from urllib.parse import urlparse

from conf import BASE_DIR
from uploader.compliance import (
    YOUTUBE_DAILY_UPLOAD_BUDGET,
    _title_from_sidecar,
    list_videos_in_dir,
    plan_batch_schedule,
)

UI_FILE = BASE_DIR / "sau_review_ui" / "index.html"
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
    if shorts:
        cmd.append("--shorts")
    if dry_run:
        cmd.append("--dry-run")

    _append_log("$ " + " ".join(cmd))
    thread = threading.Thread(target=_run_subprocess, args=(cmd,), daemon=True)
    thread.start()
    return {"ok": True, "dry_run": dry_run, "cmd": cmd}


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


class ReviewUIHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                _send_html(self)
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
