"""주제 후보 장부 (data/topics.sqlite3).

작업 장부(jobs.sqlite3)와 따로 둔다: 작업 장부는 구조 버전이 다르면 실행기가 멈추게 되어 있어 건드리지 않는다.

- batches: 후보를 찾아 보낸 회차(weekly 주 1회 / more [새 후보 더 받기] / manual 직접 실행)
- candidates: 회차별 후보. 버튼은 후보 번호만 담고, 등록할 주제 문장은 여기서 읽는다(버튼 값을 믿지 않음)
- 등록은 db.create_job(source="web", source_ref="topic:<후보 번호>")로 해서 연타·재전송에도 작업이 한 건만 생긴다
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from src.topic_finder import Candidate

KST = ZoneInfo("Asia/Seoul")
EXPIRE_DAYS = 7
SKIP_DAYS = 21                 # 보냈는데 안 고른 후보는 이 기간 쉼
MORE_PER_DAY = 3
BATCH_KINDS = ("weekly", "more", "manual")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('weekly', 'more', 'manual')),
    week        TEXT,                       -- 한국 시간 기준 주차(예: 2026-W45), weekly만
    status      TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'sending', 'sent', 'failed')),
    message_id  INTEGER,
    error       TEXT,
    created_at  TEXT NOT NULL,
    finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS batches_weekly ON batches (week) WHERE kind = 'weekly' AND status != 'failed';
CREATE TABLE IF NOT EXISTS candidates (
    id          INTEGER PRIMARY KEY,
    batch_id    INTEGER NOT NULL REFERENCES batches(id),
    ckey        TEXT NOT NULL,              -- 제도명:연도:종류 (다시 추천 쉬기용)
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    year        INTEGER NOT NULL,
    category    TEXT NOT NULL,
    topic       TEXT NOT NULL CHECK (length(trim(topic)) > 0),
    info        TEXT NOT NULL DEFAULT '{}', -- 이유·경고·공식 자료 등(보여 주기용)
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    job_id      INTEGER,
    chosen_at   TEXT
);
CREATE INDEX IF NOT EXISTS candidates_key ON candidates (ckey, created_at);
"""


class TopicStoreError(RuntimeError):
    pass


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_SCHEMA)
    return conn


def week_of(t: datetime) -> str:
    y, w, _ = t.astimezone(KST).isocalendar()
    return f"{y}-W{w:02d}"


def start_batch(conn: sqlite3.Connection, kind: str, now: datetime | None = None) -> int | None:
    """회차를 시작한다. weekly는 그 주에 이미 시작·완료한 회차가 있으면 None(한 주 한 번)."""
    if kind not in BATCH_KINDS:
        raise TopicStoreError(f"알 수 없는 회차 종류: {kind}")
    now = now or _now()
    try:
        cur = conn.execute("INSERT INTO batches (kind, week, created_at) VALUES (?, ?, ?)",
                           (kind, week_of(now) if kind == "weekly" else None, _iso(now)))
    except sqlite3.IntegrityError:
        return None
    return int(cur.lastrowid)


def finish_batch(conn: sqlite3.Connection, batch_id: int, message_id: int | None,
                 now: datetime | None = None, note: str | None = None) -> None:
    conn.execute("UPDATE batches SET status = 'sent', message_id = ?, error = ?, finished_at = ? WHERE id = ?",
                 (message_id, note, _iso(now or _now()), batch_id))


def fail_batch(conn: sqlite3.Connection, batch_id: int, error: str, now: datetime | None = None) -> None:
    conn.execute("UPDATE batches SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                 (error[:500], _iso(now or _now()), batch_id))


def get_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    return dict(row) if row else None


def more_count_today(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    """오늘(한국 시간) [새 후보 더 받기]로 시작한 회차 수(실패 포함)."""
    now = now or _now()
    start = datetime.combine(now.astimezone(KST).date(), datetime.min.time(), KST)
    return conn.execute("SELECT count(*) FROM batches WHERE kind = 'more' AND created_at >= ?",
                        (_iso(start),)).fetchone()[0]


def add_candidates(conn: sqlite3.Connection, batch_id: int, cands: Sequence[Candidate],
                   now: datetime | None = None) -> list[int]:
    now = now or _now()
    ids: list[int] = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        for c in cands:
            info = {"reasons": c.reasons, "warnings": c.warnings, "hosts": c.official_hosts[:4],
                    "news": c.news_titles[:2], "interest": c.interest,
                    "post_id": c.written.post_id if c.written else None,
                    "written_year": c.written.year if c.written else None}
            cur = conn.execute(
                "INSERT INTO candidates (batch_id, ckey, kind, name, year, category, topic, info, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (batch_id, c.key, c.kind, c.seed.name, c.year, c.seed.category, c.topic,
                 json.dumps(info, ensure_ascii=False), _iso(now), _iso(now + timedelta(days=EXPIRE_DAYS))))
            ids.append(int(cur.lastrowid))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return ids


def get_candidate(conn: sqlite3.Connection, cid: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM candidates WHERE id = ?", (cid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["info"] = json.loads(d["info"] or "{}")
    return d


def is_expired(cand: dict[str, Any], now: datetime | None = None) -> bool:
    return datetime.fromisoformat(cand["expires_at"]) <= (now or _now())


def mark_chosen(conn: sqlite3.Connection, cid: int, job_id: int, now: datetime | None = None) -> None:
    conn.execute("UPDATE candidates SET job_id = ?, chosen_at = coalesce(chosen_at, ?) WHERE id = ?",
                 (job_id, _iso(now or _now()), cid))


def recent_skip_keys(conn: sqlite3.Connection, now: datetime | None = None, days: int = SKIP_DAYS) -> set[str]:
    """최근 보냈는데 아직 안 고른 후보 key(다음 추천에서 쉼). 고른 것은 작업 장부로 걸러진다."""
    since = _iso((now or _now()) - timedelta(days=days))
    rows = conn.execute(
        "SELECT c.ckey FROM candidates c JOIN batches b ON b.id = c.batch_id "
        "WHERE c.created_at >= ? AND c.job_id IS NULL AND b.status = 'sent'", (since,)).fetchall()
    return {r[0] for r in rows}


def mark_sending(conn: sqlite3.Connection, batch_id: int) -> None:
    """텔레그램에 보내기 직전에 기록한다. 이 뒤로 멈추면 메시지가 갔을 수 있어 다시 보내지 않는다."""
    conn.execute("UPDATE batches SET status = 'sending' WHERE id = ?", (batch_id,))


def fail_stale_running(conn: sqlite3.Connection, minutes: int = 30, now: datetime | None = None) -> int:
    """중간에 꺼져 남은 회차를 정리한다.

    'sending'(보내는 중에 멈춤)은 메시지가 갔을 수 있어 'sent'로 둔다(같은 주에 두 번 보내지 않음).
    'running'(보내기 전에 멈춤)은 'failed'로 돌려 그 주 회차를 다시 할 수 있게 한다.
    """
    now = now or _now()
    cutoff = _iso(now - timedelta(minutes=minutes))
    conn.execute("BEGIN IMMEDIATE")
    try:
        sent = conn.execute(
            "UPDATE batches SET status = 'sent', error = '보내는 중에 멈춤(전송 여부 불명)', finished_at = ? "
            "WHERE status = 'sending' AND created_at < ?", (_iso(now), cutoff)).rowcount
        failed = conn.execute(
            "UPDATE batches SET status = 'failed', error = '중간에 멈춤', finished_at = ? "
            "WHERE status = 'running' AND created_at < ?", (_iso(now), cutoff)).rowcount
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return sent + failed


def reserve_more(conn: sqlite3.Connection, limit: int = MORE_PER_DAY, now: datetime | None = None) -> int | None:
    """오늘 남은 횟수를 확인하고 [더 받기] 회차를 한 번에 예약한다(연타해도 한도를 못 넘게). 다 썼으면 None."""
    now = now or _now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if more_count_today(conn, now) >= limit:
            conn.execute("ROLLBACK")
            return None
        cur = conn.execute("INSERT INTO batches (kind, created_at) VALUES ('more', ?)", (_iso(now),))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return int(cur.lastrowid)


def candidate_ids_for_key(conn: sqlite3.Connection, ckey: str) -> list[int]:
    """같은 주제(제도명:연도:종류)로 보낸 모든 후보 번호(회차 상관없이)."""
    return [int(r[0]) for r in conn.execute("SELECT id FROM candidates WHERE ckey = ? ORDER BY id", (ckey,))]
