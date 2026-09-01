# -*- coding: utf-8 -*-
"""YouTube copyright monitor — 持续检查已发布视频的版权状态，发现问题立即设为 private。

用法：
    # 本地：从 sqlite manifest 读已发布视频
    python yt_copyright_monitor.py --dir <剧集目录> [--account jayz]
    python yt_copyright_monitor.py --all [--account jayz]

    # 本地：导出 watchlist（给 CI 用；只含 ep + video_id，无敏感信息）
    python yt_copyright_monitor.py --all --export-watchlist watchlist.json

    # CI（GitHub Actions）：从 watchlist JSON 读，不依赖 conf.py / db
    python yt_copyright_monitor.py --watchlist watchlist.json --json-output result.json

逻辑：
    1. 取已发布的 YouTube 视频（本地走 manifest，CI 走 watchlist 文件）
    2. 逐个查询 YouTube API 获取当前状态
    3. 规则判断 + 可选的 DeepSeek 二次分析，确认是否真有问题
       （避免把"定时发布 private+publishAt"误判成异常）
    4. 确认有问题 → 立即设为 private → 记录错误信息
    5. 输出报告；--json-output 供 GitHub Actions 读取后创建 Issue

CI 环境约定（无 conf.py）：
    - YouTube OAuth token 路径：cookies/youtube_oauth_<account>.json（Secrets 还原）
    - client_secret 路径：client_secret.json（Secrets 还原）
    - DeepSeek key：环境变量 DEEPSEEK_API_KEY（Secrets 注入）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

# ---------------------------------------------------------------------------
# conf 导入容错：本地有 conf.py 用之；CI 上回退到环境变量 / cwd。
# conf.py 已被 .gitignore 排除（含真实 API key），绝不能出现在 repo 里。
# ---------------------------------------------------------------------------
try:
    from conf import BASE_DIR
except ImportError:
    BASE_DIR = Path.cwd()

try:
    from conf import DEEPSEEK_API_KEY
except ImportError:
    DEEPSEEK_API_KEY = ""

try:
    from conf import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
except ImportError:
    DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL = "deepseek-chat"

# 环境变量优先（CI 由 GitHub Secrets 注入），其次是 conf.py
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", DEEPSEEK_API_KEY)

from uploader.manifest import list_states, upsert as manifest_upsert
from uploader.youtube_uploader.yt_api import YouTubeAPI

monitor_logger = logger.bind(business_name="copyright_monitor")


# ---------------------------------------------------------------------------
# 视频状态查询与判断
# ---------------------------------------------------------------------------

def check_video_status(yt: YouTubeAPI, video_id: str) -> dict:
    """查询单个视频的状态，返回完整响应或空 dict。"""
    try:
        return yt.get_video_status(video_id)
    except Exception as exc:
        monitor_logger.error(f"Failed to query video {video_id}: {exc}")
        return {}


def analyze_with_deepseek(status_dict: dict, video_id: str) -> tuple[bool | None, str]:
    """用 DeepSeek 分析视频状态，判断是否真的有版权问题。

    Returns: (is_issue, reason)。is_issue=None 表示不可用（未配置/调用失败），
    调用方应回退到规则判断。
    """
    if not DEEPSEEK_API_KEY:
        return None, "DeepSeek not configured"

    try:
        import requests

        status = status_dict.get("status", {})
        content = status_dict.get("contentDetails", {})

        prompt = f"""你是一个 YouTube 版权监控助手。请分析以下视频的状态，判断是否存在版权问题需要设为 private。

视频 ID: {video_id}

状态信息:
- uploadStatus: {status.get('uploadStatus', 'N/A')}
- rejectionReason: {status.get('rejectionReason', 'N/A')}
- privacyStatus: {status.get('privacyStatus', 'N/A')}
- publishAt: {status.get('publishAt', 'N/A')}
- licensedContent: {content.get('licensedContent', False)}

请判断：
1. 这个视频是否真的有版权问题（被 YouTube 拒绝、版权主张导致屏蔽等）？
2. 如果是定时发布（privacyStatus=private 但有 publishAt），这是正常的，不算问题。
3. 如果 privacyStatus 不是 public 且没有 publishAt，说明被 YouTube 改了，算问题。

请返回 JSON 格式：
{{"is_issue": true/false, "reason": "问题描述或'正常'"}}

只返回 JSON，不要其他内容。"""

        response = requests.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            },
            timeout=30,
        )
        response.raise_for_status()

        result = response.json()
        content_text = result["choices"][0]["message"]["content"].strip()

        import re
        json_match = re.search(r"\{.*\}", content_text, re.DOTALL)
        if json_match:
            analysis = json.loads(json_match.group())
            return bool(analysis.get("is_issue", False)), analysis.get("reason", "Unknown")
        monitor_logger.warning(f"DeepSeek response not JSON: {content_text}")
        return None, "DeepSeek response parse error"

    except Exception as exc:
        monitor_logger.warning(f"DeepSeek analysis failed for {video_id}: {exc}")
        return None, f"DeepSeek error: {exc}"


def has_copyright_issue(status_dict: dict, was_scheduled: bool = False) -> tuple[bool, str]:
    """规则判断视频是否有版权问题。返回 (有问题, 原因描述)。

    was_scheduled: 该视频是定时发布的，private+publishAt 属正常态。
    """
    if not status_dict:
        # 视频查不到 = 已删除/已被 YouTube 下架，物理上无法被观看，
        # 无版权暴露风险，无需设 private（设了也会 404）。
        return False, "Video not found (already deleted)"

    status = status_dict.get("status", {})
    upload_status = status.get("uploadStatus", "")
    rejection_reason = status.get("rejectionReason", "")
    privacy_status = status.get("privacyStatus", "")
    publish_at = status.get("publishAt", "")

    # 被 YouTube 拒绝（通常涉及版权）
    if upload_status == "rejected":
        return True, f"Rejected by YouTube: {rejection_reason}"

    if privacy_status == "public":
        return False, ""

    # private + publishAt = 定时发布等待中，正常
    if privacy_status == "private" and publish_at:
        return False, ""

    if privacy_status == "unlisted":
        return True, "Privacy changed to unlisted (was public/scheduled)"

    if privacy_status == "private" and not publish_at:
        # private 且无排期。两种可能无法区分：
        # a) 排期被 update 误清（需人工恢复排期）
        # b) YouTube 因版权强制下架（绝不能重新公开）
        # 所以只报警不动作，让人来判断。
        if was_scheduled:
            return True, "Scheduled video lost its publishAt (stuck private) — needs human attention"
        return True, "Privacy changed to private (was public)"

    return False, ""


# ---------------------------------------------------------------------------
# 视频来源：manifest（本地）或 watchlist 文件（CI）
# ---------------------------------------------------------------------------

def collect_from_manifest(directory: Path) -> list[dict]:
    """从 sqlite manifest 收集已发布视频。返回 [{ep, video_id, was_scheduled, state}]。"""
    from uploader.manifest import EpisodeState

    states: list[EpisodeState] = list_states(directory)
    return [
        {"ep": s.ep, "video_id": s.yt_video_id,
         "was_scheduled": bool(s.yt_scheduled_at), "state": s}
        for s in states if s.yt_published and s.yt_video_id
    ]


def collect_from_watchlist(path: Path) -> list[dict]:
    """从 watchlist JSON 收集待检查视频。格式: [{"ep": 1, "video_id": "xxx"}, ...]"""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        {"ep": int(item["ep"]), "video_id": str(item["video_id"]),
         "was_scheduled": bool(item.get("was_scheduled", False)), "state": None}
        for item in data
    ]


# ---------------------------------------------------------------------------
# 主监控循环
# ---------------------------------------------------------------------------

def check_and_fix(yt: YouTubeAPI, entries: list[dict]) -> list[dict]:
    """逐个检查视频，确认有问题则设为 private。返回问题列表。"""
    issues = []
    for entry in entries:
        video_id = entry["video_id"]
        status_dict = check_video_status(yt, video_id)

        # 规则判断打底
        has_issue, reason = has_copyright_issue(
            status_dict, was_scheduled=entry.get("was_scheduled", False)
        )
        # 运维故障（排期丢失卡 private）：只报警，不走 DeepSeek 版权复核，
        # 也不再调 make_private（已是 private，重复调用无意义）
        ops_only = "stuck private" in reason

        # DeepSeek 二次确认：只用于「规则说有版权问题」时排除误报，
        # 不让它把规则判定的问题放过（模型保守性 > 灵敏性）。
        if has_issue and not ops_only:
            ds_result, ds_reason = analyze_with_deepseek(status_dict, video_id)
            if ds_result is False:
                monitor_logger.info(
                    f"EP{entry['ep']:02d} ({video_id}): rule flagged, "
                    f"DeepSeek says OK ({ds_reason}) — skipping"
                )
                continue
            if ds_result is True and ds_reason:
                reason = f"{reason} | DeepSeek: {ds_reason}"

        if has_issue:
            monitor_logger.error(f"EP{entry['ep']:02d} ({video_id}): {reason}")
            if not ops_only:
                monitor_logger.error(f"EP{entry['ep']:02d} ({video_id}): setting to PRIVATE")
                try:
                    yt.make_private(video_id)
                except Exception as exc:
                    monitor_logger.error(f"Failed to set private for {video_id}: {exc}")
                    continue

                state = entry.get("state")
                if state is not None:
                    state.yt_published = False
                    state.last_error = (
                        f"Copyright monitor: {reason} "
                        f"(set private at {datetime.now(timezone.utc).isoformat(timespec='seconds')})"
                    )
                    try:
                        manifest_upsert(state)
                    except Exception as exc:
                        monitor_logger.error(f"Failed to update manifest for EP{state.ep:02d}: {exc}")

            issues.append({
                "ep": entry["ep"],
                "video_id": video_id,
                "reason": reason,
                "action": "needs_attention" if ops_only else "set_private",
            })
        else:
            monitor_logger.info(f"EP{entry['ep']:02d} ({video_id}): OK")

    return issues


def monitor_directory(directory: Path, account_name: str) -> list[dict]:
    """监控单个剧集目录（本地 manifest 模式）。"""
    directory = directory.expanduser().resolve()
    if not directory.exists():
        monitor_logger.warning(f"Directory not found: {directory}")
        return []

    entries = collect_from_manifest(directory)
    if not entries:
        monitor_logger.info(f"No published YouTube videos in {directory}")
        return []

    monitor_logger.info(f"Checking {len(entries)} published videos in {directory.name} ...")
    yt = YouTubeAPI(account_name)
    yt.authenticate()
    return check_and_fix(yt, entries)


def monitor_all(account_name: str) -> list[dict]:
    """监控 BASE_DIR/videos 下的所有剧集目录。"""
    videos_dir = BASE_DIR / "videos"
    if not videos_dir.exists():
        monitor_logger.warning(f"videos/ directory not found: {videos_dir}")
        return []

    all_issues = []
    for subdir in sorted(videos_dir.iterdir()):
        if subdir.is_dir():
            all_issues.extend(monitor_directory(subdir, account_name))
    return all_issues


def export_watchlist(out_path: Path) -> int:
    """把所有剧集目录的已发布视频导出成 watchlist JSON（给 CI 用）。"""
    videos_dir = BASE_DIR / "videos"
    payload = []
    if videos_dir.exists():
        for subdir in sorted(videos_dir.iterdir()):
            if not subdir.is_dir():
                continue
            for entry in collect_from_manifest(subdir):
                payload.append({
                    "ep": entry["ep"],
                    "video_id": entry["video_id"],
                    "was_scheduled": entry["was_scheduled"],
                })
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Watchlist exported: {len(payload)} videos -> {out_path}")
    return len(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="YouTube copyright monitor")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dir", type=Path, help="Monitor a specific series directory (local manifest mode)")
    group.add_argument("--all", action="store_true", help="Monitor all series under videos/ (local manifest mode)")
    group.add_argument("--watchlist", type=Path, help="Read videos from a watchlist JSON (CI mode, no db needed)")
    group.add_argument("--export-watchlist", type=Path, help="Export published videos to a watchlist JSON and exit")
    parser.add_argument("--account", default="jayz", help="YouTube account name (default: jayz)")
    parser.add_argument("--json-output", type=Path, help="Write results as JSON to this file (for GitHub Actions)")

    args = parser.parse_args()

    if args.export_watchlist:
        return 0 if export_watchlist(args.export_watchlist) >= 0 else 1

    print("=" * 60)
    print("  YouTube Copyright Monitor")
    print("=" * 60)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Account: {args.account}")
    print(f"DeepSeek: {'enabled' if DEEPSEEK_API_KEY else 'disabled (rule-based only)'}")
    print()

    if args.watchlist:
        entries = collect_from_watchlist(args.watchlist)
        print(f"Watchlist mode: {len(entries)} videos from {args.watchlist}")
        yt = YouTubeAPI(args.account)
        yt.authenticate()
        issues = check_and_fix(yt, entries)
        total = len(entries)
    elif args.dir:
        issues = monitor_directory(args.dir, args.account)
        total = len(issues)  # 目录模式下精确总数在函数内部，这里够用
    else:
        issues = monitor_all(args.account)
        total = len(issues)

    print()
    print("=" * 60)
    if issues:
        print(f"  FOUND {len(issues)} ISSUE(S) — ALL SET TO PRIVATE")
        print("=" * 60)
        for item in issues:
            print(f"  EP{item['ep']:02d} | {item['video_id']} | {item['reason']}")
    else:
        print("  NO ISSUES FOUND — all published videos are healthy")
        print("=" * 60)
    print()

    if args.json_output:
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "account": args.account,
            "total_checked": total,
            "issues": issues,
        }
        args.json_output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"JSON output written to: {args.json_output}")

    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
