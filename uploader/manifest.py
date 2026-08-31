# -*- coding: utf-8 -*-
"""Idempotent upload manifest for `sau review-batch`.

sqlite table keyed on (batch_dir, episode) that remembers what already made
it to which platform, so re-running a batch after a crash / rate-limit
retry / partial YouTube quota exhaustion never re-uploads a video that's
already live.

Not a general-purpose DB — read/write API is intentionally small:
- init_db()                  ensure schema
- get_state(dir, ep)         → EpisodeState (all None fields for missing rows)
- upsert(state)              write partial or complete state
- summary(dir)               → dict counts for `sau status`
- list_states(dir)           → all rows for a dir, sorted by ep

Callers use `state.is_yt_done()` etc. to decide whether to skip a step.
Storing the source file path lets us detect "same episode number, different
file" — the caller decides whether that counts as a resume or a fresh run.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, asdict
from pathlib import Path
from datetime import datetime, timezone

from loguru import logger

from conf import BASE_DIR

manifest_logger = logger.bind(business_name="manifest")

DB_PATH = BASE_DIR / "db" / "database.db"
TABLE = "episode_manifest"


@dataclass
class EpisodeState:
    batch_dir: str = ""
    ep: int = 0
    source_file: str = ""
    preflight_ok: bool | None = None
    yt_video_id: str = ""
    yt_published: bool = False
    yt_scheduled_at: str = ""
    shorts_video_id: str = ""
    shorts_published: bool = False
    tt_publish_id: str = ""
    tt_published: bool = False
    last_error: str = ""
    updated_at: str = ""

    def is_yt_done(self) -> bool:
        return self.yt_published and bool(self.yt_video_id)

    def is_shorts_done(self) -> bool:
        return self.shorts_published and bool(self.shorts_video_id)

    def is_tt_done(self) -> bool:
        return self.tt_published and bool(self.tt_publish_id)

    def status_label(self, want_shorts: bool, want_tt: bool) -> str:
        checks = [self.is_yt_done()]
        if want_shorts:
            checks.append(self.is_shorts_done())
        if want_tt:
            checks.append(self.is_tt_done())
        if self.last_error:
            # An error is only interesting if the whole run isn't complete.
            if not all(checks):
                return "error"
        if all(checks):
            return "done"
        if any(checks):
            return "partial"
        return "pending"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                batch_dir TEXT NOT NULL,
                ep INTEGER NOT NULL,
                source_file TEXT NOT NULL,
                preflight_ok INTEGER,
                yt_video_id TEXT NOT NULL DEFAULT '',
                yt_published INTEGER NOT NULL DEFAULT 0,
                yt_scheduled_at TEXT NOT NULL DEFAULT '',
                shorts_video_id TEXT NOT NULL DEFAULT '',
                shorts_published INTEGER NOT NULL DEFAULT 0,
                tt_publish_id TEXT NOT NULL DEFAULT '',
                tt_published INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (batch_dir, ep)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _norm_dir(directory: Path | str) -> str:
    return str(Path(directory).expanduser().resolve())


def get_state(directory: Path | str, ep: int) -> EpisodeState:
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT * FROM {TABLE} WHERE batch_dir = ? AND ep = ?",
            (_norm_dir(directory), ep),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return EpisodeState(batch_dir=_norm_dir(directory), ep=ep)
    return EpisodeState(
        batch_dir=row["batch_dir"],
        ep=row["ep"],
        source_file=row["source_file"],
        preflight_ok=None if row["preflight_ok"] is None else bool(row["preflight_ok"]),
        yt_video_id=row["yt_video_id"],
        yt_published=bool(row["yt_published"]),
        yt_scheduled_at=row["yt_scheduled_at"],
        shorts_video_id=row["shorts_video_id"],
        shorts_published=bool(row["shorts_published"]),
        tt_publish_id=row["tt_publish_id"],
        tt_published=bool(row["tt_published"]),
        last_error=row["last_error"],
        updated_at=row["updated_at"],
    )


def upsert(state: EpisodeState) -> None:
    init_db()
    state.batch_dir = _norm_dir(state.batch_dir)
    state.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = _connect()
    try:
        conn.execute(
            f"""
            INSERT INTO {TABLE} (
                batch_dir, ep, source_file, preflight_ok,
                yt_video_id, yt_published, yt_scheduled_at,
                shorts_video_id, shorts_published,
                tt_publish_id, tt_published,
                last_error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(batch_dir, ep) DO UPDATE SET
                source_file      = excluded.source_file,
                preflight_ok     = excluded.preflight_ok,
                yt_video_id      = CASE WHEN excluded.yt_video_id != '' THEN excluded.yt_video_id ELSE {TABLE}.yt_video_id END,
                yt_published     = MAX({TABLE}.yt_published, excluded.yt_published),
                yt_scheduled_at  = CASE WHEN excluded.yt_scheduled_at != '' THEN excluded.yt_scheduled_at ELSE {TABLE}.yt_scheduled_at END,
                shorts_video_id  = CASE WHEN excluded.shorts_video_id != '' THEN excluded.shorts_video_id ELSE {TABLE}.shorts_video_id END,
                shorts_published = MAX({TABLE}.shorts_published, excluded.shorts_published),
                tt_publish_id    = CASE WHEN excluded.tt_publish_id != '' THEN excluded.tt_publish_id ELSE {TABLE}.tt_publish_id END,
                tt_published     = MAX({TABLE}.tt_published, excluded.tt_published),
                last_error       = excluded.last_error,
                updated_at       = excluded.updated_at
            """,
            (
                state.batch_dir,
                state.ep,
                state.source_file,
                None if state.preflight_ok is None else int(state.preflight_ok),
                state.yt_video_id,
                int(state.yt_published),
                state.yt_scheduled_at,
                state.shorts_video_id,
                int(state.shorts_published),
                state.tt_publish_id,
                int(state.tt_published),
                state.last_error,
                state.updated_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_states(directory: Path | str) -> list[EpisodeState]:
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            f"SELECT * FROM {TABLE} WHERE batch_dir = ? ORDER BY ep",
            (_norm_dir(directory),),
        ).fetchall()
    finally:
        conn.close()
    return [
        EpisodeState(
            batch_dir=r["batch_dir"],
            ep=r["ep"],
            source_file=r["source_file"],
            preflight_ok=None if r["preflight_ok"] is None else bool(r["preflight_ok"]),
            yt_video_id=r["yt_video_id"],
            yt_published=bool(r["yt_published"]),
            yt_scheduled_at=r["yt_scheduled_at"],
            shorts_video_id=r["shorts_video_id"],
            shorts_published=bool(r["shorts_published"]),
            tt_publish_id=r["tt_publish_id"],
            tt_published=bool(r["tt_published"]),
            last_error=r["last_error"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]


def summary(directory: Path | str, want_shorts: bool | None = None, want_tt: bool | None = None) -> dict:
    """Aggregate status labels across a batch dir.

    When want_shorts/want_tt is None, each row is judged with its own signal:
    a row that has a shorts_video_id (or `shorts` in last_error) is treated as
    "shorts was wanted", similarly for TikTok. Pass explicit bool overrides
    when you know the batch's target set upfront.
    """
    states = list_states(directory)
    counts = {"total": len(states), "done": 0, "partial": 0, "pending": 0, "error": 0}
    for s in states:
        ws = want_shorts if want_shorts is not None else (
            bool(s.shorts_video_id) or "shorts" in (s.last_error or "").lower()
        )
        wt = want_tt if want_tt is not None else (
            bool(s.tt_publish_id) or "tiktok" in (s.last_error or "").lower()
        )
        counts[s.status_label(ws, wt)] += 1
    return counts


def forget(directory: Path | str, ep: int | None = None) -> int:
    """Delete manifest entries. `ep=None` clears the whole directory. Returns rows deleted."""
    init_db()
    conn = _connect()
    try:
        if ep is None:
            cursor = conn.execute(f"DELETE FROM {TABLE} WHERE batch_dir = ?", (_norm_dir(directory),))
        else:
            cursor = conn.execute(
                f"DELETE FROM {TABLE} WHERE batch_dir = ? AND ep = ?",
                (_norm_dir(directory), ep),
            )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
