"""주제 후보 회차 실행(찾기 → 저장 → 텔레그램)과 버튼 처리(등록·더 받기).

- 찾기는 1~2분 걸리므로 [새 후보 더 받기]는 별도 스레드에서 돌린다(그동안 승인 버튼도 계속 받음).
- 동시에 한 회차만(잠금). 이미 찾는 중이면 "찾는 중" 안내만.
- 등록할 주제 문장은 버튼 값이 아니라 후보 장부에서 읽는다.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

from src import db, topic_store
from src.telegram_notifier import TopicAction, TopicLine
from src.topic_finder import TopicWriter, find_candidates, load_seeds, written_from_jobs

log = logging.getLogger(__name__)

_RUN_LOCK = threading.Lock()      # 한 번에 한 회차만
HEADERS = {"weekly": "이번 주 블로그 주제 후보", "more": "새 블로그 주제 후보", "manual": "블로그 주제 후보"}


@dataclass
class TopicDeps:
    settings: Any
    search: Any
    generator: Any | None          # None이면 Gemini 없이(기본 목록 + 틀 문장)
    telegram: Any
    finder: Callable = find_candidates


def _all_jobs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT id, topic, status, post_id, post_url, created_at FROM jobs")]


def _queue_note(jobs: list[dict[str, Any]]) -> str:
    queued = sum(j["status"] in ("queued", "generating", "tts", "media", "rendering") for j in jobs)
    waiting = sum(j["status"] == "awaiting_approval" for j in jobs)
    return f"지금 글쓰기 대기 {queued}편, 발행 승인 대기 {waiting}편"


def run_batch(deps: TopicDeps, kind: str, today: date | None = None,
              now: datetime | None = None, batch_id: int | None = None) -> str:
    """후보를 찾아 보낸다. 결과 요약 문장을 돌려준다(이미 이번 주 회차가 있으면 그렇다고).
    batch_id: 미리 예약한 회차([더 받기]). 이때는 부르는 쪽이 이미 잠금을 잡고 있다."""
    if batch_id is not None:
        return _run_batch(deps, kind, today, now, batch_id)
    if not _RUN_LOCK.acquire(blocking=False):
        return "이미 주제를 찾는 중입니다"
    try:
        return _run_batch(deps, kind, today, now, None)
    finally:
        _RUN_LOCK.release()


def _run_batch(deps: TopicDeps, kind: str, today: date | None, now: datetime | None,
               reserved: int | None) -> str:
    settings = deps.settings
    store = topic_store.connect(settings.data_dir / "topics.sqlite3")
    jobs_conn = db.connect(settings.db_path)
    try:
        topic_store.fail_stale_running(store, now=now)
        batch_id = reserved if reserved is not None else topic_store.start_batch(store, kind, now=now)
        if batch_id is None:
            return "이번 주 후보는 이미 보냈습니다"
        sending = False
        try:
            seeds = load_seeds()
            jobs = _all_jobs(jobs_conn)
            written = written_from_jobs(jobs, seeds)
            writer = TopicWriter(deps.generator) if deps.generator is not None else None
            result = deps.finder(deps.search, writer, seeds, written, today=today,
                                 skip_keys=topic_store.recent_skip_keys(store, now=now))
            ids = topic_store.add_candidates(store, batch_id, result.candidates, now=now)
            links = {j["post_id"]: j.get("post_url") or "" for j in jobs if j.get("post_id")}
            lines = [TopicLine(cid, c.kind, c.topic, c.seed.category, c.interest, c.reasons, c.warnings,
                               c.official_hosts, c.news_titles,
                               links.get(c.written.post_id, "") if c.written else "")
                     for cid, c in zip(ids, result.candidates)]
            left = max(0, topic_store.MORE_PER_DAY - topic_store.more_count_today(store, now=now))
            topic_store.mark_sending(store, batch_id)
            sending = True     # 여기부터 난 오류는 메시지가 갔을 수도 있음
            msg_id = deps.telegram.send_topic_candidates(
                HEADERS.get(kind, HEADERS["manual"]), lines, [*result.notes, _queue_note(jobs)], batch_id, left)
            topic_store.finish_batch(store, batch_id, msg_id, now=now)
            return f"후보 {len(lines)}개를 보냈습니다"
        except Exception as exc:
            log.exception("주제 후보 회차 %s 실패", batch_id)
            if sending:   # 실제로는 전송됐을 수 있어 같은 주에 다시 보내지 않는다
                topic_store.finish_batch(store, batch_id, None, now=now, note=f"전송 여부 불명: {exc.__class__.__name__}")
            else:
                topic_store.fail_batch(store, batch_id, f"{exc.__class__.__name__}: {exc}", now=now)
            raise
    finally:
        store.close()
        jobs_conn.close()


def _job_for_same_topic(store: sqlite3.Connection, jobs_conn: sqlite3.Connection,
                        cand: dict[str, Any]) -> int | None:
    """같은 주제의 다른 후보로 이미 만든 작업(취소 제외). 후보 장부 기록이 빠졌어도 작업 장부로 직접 확인한다."""
    others = [i for i in topic_store.candidate_ids_for_key(store, cand["ckey"]) if i != cand["id"]]
    if not others:
        return None
    marks = ",".join("?" * len(others))
    row = jobs_conn.execute(
        f"SELECT id, source_ref FROM jobs WHERE source = 'web' AND status != 'canceled' "
        f"AND source_ref IN ({marks}) ORDER BY id LIMIT 1", [f"topic:{i}" for i in others]).fetchone()
    if row is None:
        return None
    topic_store.mark_chosen(store, int(row["source_ref"].split(":")[1]), int(row["id"]))  # 빠진 기록 복구
    return int(row["id"])


def register(deps: TopicDeps, jobs_conn: sqlite3.Connection, a: TopicAction,
             now: datetime | None = None) -> str:
    """[n번 등록]: 후보를 글쓰기 대기열에 넣는다. 두 번 눌러도 한 건."""
    store = topic_store.connect(deps.settings.data_dir / "topics.sqlite3")
    try:
        cand = topic_store.get_candidate(store, a.ref_id)
        if cand is None:
            return "없는 후보입니다"
        if cand["kind"] == "update":
            return "고치기 후보는 등록 대상이 아닙니다"
        ref = f"topic:{cand['id']}"
        existing = jobs_conn.execute("SELECT id FROM jobs WHERE source = 'web' AND source_ref = ?",
                                     (ref,)).fetchone()
        if existing is None:
            same = _job_for_same_topic(store, jobs_conn, cand)   # 다른 메시지에서 같은 주제를 이미 고름
            if same is not None:
                return f"같은 주제를 이미 등록했습니다(작업 {same}번)"
            if topic_store.is_expired(cand, now):
                return "기한(7일)이 지난 후보입니다. [새 후보 더 받기]를 눌러 주세요"
        job_id = db.create_job(jobs_conn, cand["topic"], source="web", source_ref=ref)
        topic_store.mark_chosen(store, cand["id"], job_id, now=now)
    finally:
        store.close()
    deps.telegram.mark_topic_registered(a.message_id, a.keyboard, a.ref_id, f"등록됨(작업 {job_id})")
    if existing is not None:
        return f"이미 등록된 후보입니다(작업 {job_id}번)"
    return f"작업 {job_id}번으로 등록했습니다"


def request_more(deps: TopicDeps, a: TopicAction, now: datetime | None = None,
                 start: Callable[[Callable[[], None]], None] | None = None) -> str:
    """[새 후보 더 받기]: 하루 3번까지, 별도 스레드에서 찾는다.
    잠금을 여기서 잡고 회차를 예약한 뒤 스레드가 끝날 때 푼다(연타해도 한도·동시 실행을 못 넘게)."""
    if not _RUN_LOCK.acquire(blocking=False):
        return "이미 주제를 찾는 중입니다. 1~2분 뒤 메시지가 옵니다"
    handed_over = False
    try:
        store = topic_store.connect(deps.settings.data_dir / "topics.sqlite3")
        try:
            if topic_store.get_batch(store, a.ref_id) is None:
                return "없는 회차입니다"
            batch_id = topic_store.reserve_more(store, now=now)
        finally:
            store.close()
        if batch_id is None:
            return f"오늘은 {topic_store.MORE_PER_DAY}번 다 받았습니다. 내일 다시 눌러 주세요"

        def job() -> None:
            try:
                note = run_batch(deps, "more", now=now, batch_id=batch_id)
                if not note.startswith("후보"):
                    deps.telegram.send_text(f"[주제 후보] {note}")
            except Exception as exc:  # noqa: BLE001
                try:
                    deps.telegram.send_text(f"[주제 후보] 찾기 실패: {exc.__class__.__name__}. 잠시 뒤 다시 눌러 주세요.")
                except Exception:  # noqa: BLE001
                    log.warning("주제 후보 실패 안내도 보내지 못했습니다")
            finally:
                _RUN_LOCK.release()

        (start or (lambda fn: threading.Thread(target=fn, name="topic-more", daemon=True).start()))(job)
        handed_over = True
        return "새 후보를 찾고 있습니다. 1~2분 뒤 메시지가 옵니다"
    finally:
        if not handed_over:
            _RUN_LOCK.release()


def handle(deps: TopicDeps, jobs_conn: sqlite3.Connection, a: TopicAction) -> str:
    if a.action == "register":
        return register(deps, jobs_conn, a)
    if a.action == "more":
        return request_more(deps, a)
    return "알 수 없는 요청입니다"
