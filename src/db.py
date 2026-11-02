"""SQLite 작업 장부: 작업 상태, 단계별 중간 결과(체크포인트), 중복 발행 방지.

- 같은 출처(시트 행 등)에서 같은 작업이 두 번 들어와도 한 건만 만든다 (source + source_ref 고유).
- 워드프레스 글 번호는 한 번만 기록된다. 이미 있으면 다시 발행하지 않는다.
- 작업 가져가기(claim)는 한 번에 한 실행기만 성공한다.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# 상태 흐름: queued → generating → tts → media → rendering → awaiting_approval
#            → publishing → published
# 어느 제작 단계에서든 failed 가능, failed/canceled는 다시 queued로 돌릴 수 있음.
# publishing은 begin_publishing()으로만 들어가며, 그동안은 취소할 수 없다.
#
# 워드프레스 쪽 중복 방지 주의: 글을 만든 뒤 record_post() 전에 멈추면 장부에는 글 번호가
# 없다. wp_client는 글에 작업 번호 표시를 남기고, 새 글을 만들기 전에 그 표시로 먼저 찾아야 한다.
STATUSES = (
    "queued",
    "generating",
    "tts",
    "media",
    "rendering",
    "awaiting_approval",
    "publishing",
    "published",
    "failed",
    "canceled",
)
RUNNING_STATUSES = ("generating", "tts", "media", "rendering")
MODES = ("blog_shorts", "blog", "shorts")
SOURCES = ("sheet", "web", "cli")

# DB 구조를 바꾸면 이 번호를 올리고, connect()에 이전(마이그레이션) 단계를 추가한다.
SCHEMA_VERSION = 1

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic           TEXT    NOT NULL CHECK (length(trim(topic)) > 0),
    mode            TEXT    NOT NULL CHECK (mode IN ({", ".join(repr(m) for m in MODES)})),
    target_seconds  INTEGER NOT NULL CHECK (target_seconds BETWEEN 15 AND 180),
    source          TEXT    NOT NULL CHECK (source IN ({", ".join(repr(s) for s in SOURCES)})),
    source_ref      TEXT,
    status          TEXT    NOT NULL DEFAULT 'queued'
                            CHECK (status IN ({", ".join(repr(s) for s in STATUSES)})),
    title           TEXT,
    checkpoint      TEXT    NOT NULL DEFAULT '{{}}',
    post_id         INTEGER,
    post_url        TEXT,
    video_path      TEXT,
    error           TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_source_ref
    ON jobs (source, source_ref) WHERE source_ref IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS jobs_post_id
    ON jobs (post_id) WHERE post_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status, id);
"""


class JobError(RuntimeError):
    """잘못된 작업 요청(없는 작업, 허용되지 않는 상태 변경 등)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    """DB를 열고 테이블을 준비한다. 자동 커밋 모드로 열어 트랜잭션을 직접 관리한다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        _prepare_schema(conn, path)
    except BaseException:
        conn.close()
        raise
    return conn


def _prepare_schema(conn: sqlite3.Connection, path: Path) -> None:
    with _write(conn):
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchone() is not None
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if not has_table:
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        elif version != SCHEMA_VERSION:
            raise JobError(
                f"작업 DB 구조 버전({version})이 프로그램({SCHEMA_VERSION})과 다릅니다. "
                "DB 이전이 필요합니다: " + str(path)
            )


@contextmanager
def _write(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """쓰기 잠금을 먼저 잡는 트랜잭션. 동시에 두 곳이 같은 작업을 바꾸지 못하게 한다."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    job = dict(row)
    job["checkpoint"] = json.loads(job["checkpoint"])
    return job


def _get_for_update(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise JobError(f"작업 {job_id}번이 없습니다")
    return row


def create_job(
    conn: sqlite3.Connection,
    topic: str,
    mode: str = "blog_shorts",
    target_seconds: int = 45,
    source: str = "web",
    source_ref: str | None = None,
) -> int:
    """작업을 등록하고 번호를 돌려준다. 같은 source + source_ref가 이미 있으면 그 번호를 돌려준다."""
    topic = topic.strip()
    if not topic:
        raise JobError("주제가 비어 있습니다")
    if mode not in MODES:
        raise JobError(f"알 수 없는 작업모드: {mode}")
    if source not in SOURCES:
        raise JobError(f"알 수 없는 출처: {source}")
    now = _now()
    with _write(conn):
        if source_ref is not None:
            row = conn.execute(
                "SELECT id FROM jobs WHERE source = ? AND source_ref = ?",
                (source, source_ref),
            ).fetchone()
            if row is not None:
                return row["id"]
        try:
            cur = conn.execute(
                """INSERT INTO jobs (topic, mode, target_seconds, source, source_ref,
                                     created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (topic, mode, target_seconds, source, source_ref, now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise JobError(f"작업 값이 올바르지 않습니다: {exc}") from exc
        return cur.lastrowid


def get_job(conn: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_dict(row)


def list_jobs(
    conn: sqlite3.Connection, status: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    if status is None:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def claim_next(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """가장 오래된 대기 작업을 가져가 첫 단계(generating)로 바꾼다. 없으면 None."""
    with _write(conn):
        row = conn.execute(
            "SELECT id FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE jobs SET status = 'generating', error = NULL, updated_at = ? WHERE id = ?",
            (_now(), row["id"]),
        )
        return _row_to_dict(_get_for_update(conn, row["id"]))


# 허용되는 상태 변경 (현재 → 다음)
_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"generating", "canceled"}),
    "generating": frozenset({"tts", "media", "awaiting_approval", "failed"}),
    "tts": frozenset({"media", "failed"}),
    "media": frozenset({"rendering", "awaiting_approval", "failed"}),
    "rendering": frozenset({"awaiting_approval", "failed"}),
    # publishing은 begin_publishing(), published는 mark_published()로만
    "awaiting_approval": frozenset({"failed", "canceled"}),
    "publishing": frozenset({"awaiting_approval", "failed"}),  # 발행 실패 시 되돌림
    "published": frozenset(),
    "failed": frozenset({"queued", "canceled"}),
    "canceled": frozenset({"queued"}),
}


def set_status(
    conn: sqlite3.Connection, job_id: int, status: str, error: str | None = None
) -> None:
    """상태를 바꾼다. 흐름에 맞지 않는 변경은 막는다. failed일 때만 error를 남긴다."""
    if status not in STATUSES:
        raise JobError(f"알 수 없는 상태: {status}")
    if status == "published":
        raise JobError("발행 완료는 mark_published()로만 기록합니다")
    if status == "publishing":
        raise JobError("발행 시작은 begin_publishing()으로만 합니다")
    with _write(conn):
        current = _get_for_update(conn, job_id)["status"]
        if status not in _TRANSITIONS[current]:
            raise JobError(f"작업 {job_id}번: {current} → {status} 변경은 허용되지 않습니다")
        conn.execute(
            "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, error if status == "failed" else None, _now(), job_id),
        )


def save_checkpoint(
    conn: sqlite3.Connection, job_id: int, stage: str, data: Any
) -> None:
    """단계 결과를 저장한다. 다시 실행할 때 끝난 단계는 건너뛸 수 있게 한다."""
    payload = json.dumps(data, ensure_ascii=False)  # 저장 전에 JSON 가능 여부 확인
    with _write(conn):
        checkpoint = json.loads(_get_for_update(conn, job_id)["checkpoint"])
        checkpoint[stage] = json.loads(payload)
        conn.execute(
            "UPDATE jobs SET checkpoint = ?, updated_at = ? WHERE id = ?",
            (json.dumps(checkpoint, ensure_ascii=False), _now(), job_id),
        )


def update_fields(conn: sqlite3.Connection, job_id: int, **values: Any) -> None:
    """제목·영상 경로 같은 결과 칸을 채운다. 상태·글 번호는 여기서 바꾸지 않는다."""
    allowed = {"title", "video_path"}
    bad = set(values) - allowed
    if bad:
        raise JobError(f"여기서 바꿀 수 없는 칸: {', '.join(sorted(bad))}")
    if not values:
        return
    cols = ", ".join(f"{k} = ?" for k in values)
    with _write(conn):
        _get_for_update(conn, job_id)
        conn.execute(
            f"UPDATE jobs SET {cols}, updated_at = ? WHERE id = ?",
            (*values.values(), _now(), job_id),
        )


def record_post(
    conn: sqlite3.Connection, job_id: int, post_id: int, post_url: str
) -> None:
    """워드프레스에 만든 글(임시 저장 포함)의 번호를 한 번만 기록한다.

    재실행 시 이 번호가 있으면 새 글을 만들지 말고 이 글을 고쳐 써야 한다.
    """
    with _write(conn):
        job = _get_for_update(conn, job_id)
        if job["post_id"] is not None:
            if job["post_id"] != post_id:
                raise JobError(
                    f"작업 {job_id}번에는 이미 글 {job['post_id']}번이 연결돼 있습니다"
                )
            return
        if job["status"] in ("publishing", "published", "canceled"):
            raise JobError(f"작업 {job_id}번은 끝난 작업입니다 ({job['status']})")
        try:
            conn.execute(
                "UPDATE jobs SET post_id = ?, post_url = ?, updated_at = ? WHERE id = ?",
                (post_id, post_url, _now(), job_id),
            )
        except sqlite3.IntegrityError as exc:
            raise JobError(f"글 {post_id}번은 다른 작업에 이미 기록돼 있습니다") from exc


def begin_publishing(conn: sqlite3.Connection, job_id: int) -> bool:
    """승인 대기 → 발행 중으로 선점한다. 성공한 호출 하나만 True를 받아 실제 발행을 진행한다.

    워드프레스 호출은 이 함수가 끝난 뒤(트랜잭션 밖에서) 한다.
    """
    with _write(conn):
        job = _get_for_update(conn, job_id)
        if job["post_id"] is None:
            raise JobError(f"작업 {job_id}번에는 발행할 워드프레스 글이 없습니다")
        cur = conn.execute(
            """UPDATE jobs SET status = 'publishing', updated_at = ?
               WHERE id = ? AND status = 'awaiting_approval'""",
            (_now(), job_id),
        )
        return cur.rowcount == 1


def recover_interrupted(conn: sqlite3.Connection) -> list[int]:
    """실행기가 멈췄다가 다시 켜질 때 한 번 부른다. 다른 실행기가 돌고 있을 때는 부르면 안 된다.

    - 제작 단계에 멈춘 작업은 대기열로 되돌린다(체크포인트·글 번호는 그대로).
    - 발행 중에 멈춘 작업은 승인 대기로 되돌리고 오류 칸에 표시한다.
      실제 워드프레스 글 상태를 확인한 뒤 다시 발행해야 한다.
    돌려준 값: 되돌린 작업 번호들.
    """
    now = _now()
    running = ", ".join(repr(s) for s in RUNNING_STATUSES)
    with _write(conn):
        ids = [r["id"] for r in conn.execute(
            f"SELECT id FROM jobs WHERE status IN ({running}, 'publishing') ORDER BY id"
        )]
        conn.execute(
            f"""UPDATE jobs SET status = 'queued', error = '중단 후 재시작으로 다시 대기',
                               updated_at = ? WHERE status IN ({running})""",
            (now,),
        )
        conn.execute(
            """UPDATE jobs SET status = 'awaiting_approval',
                               error = '발행 중 중단: 워드프레스 글 상태 확인 필요',
                               updated_at = ? WHERE status = 'publishing'""",
            (now,),
        )
    return ids


def mark_published(
    conn: sqlite3.Connection, job_id: int, post_id: int, post_url: str
) -> None:
    """발행 결과를 한 번만 기록한다. 같은 작업에 다른 글 번호를 덮어쓰지 않는다."""
    with _write(conn):
        job = _get_for_update(conn, job_id)
        if job["post_id"] is not None and job["post_id"] != post_id:
            raise JobError(
                f"작업 {job_id}번은 이미 글 {job['post_id']}번으로 발행됐습니다"
            )
        if job["status"] not in ("publishing", "published"):
            raise JobError(f"작업 {job_id}번은 발행 중 상태가 아닙니다 ({job['status']})")
        try:
            conn.execute(
                """UPDATE jobs SET status = 'published', post_id = ?, post_url = ?,
                                  error = NULL, updated_at = ? WHERE id = ?""",
                (post_id, post_url, _now(), job_id),
            )
        except sqlite3.IntegrityError as exc:
            raise JobError(f"글 {post_id}번은 다른 작업에 이미 기록돼 있습니다") from exc
