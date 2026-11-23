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
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from src import db, topic_store
from src.telegram_notifier import TopicAction, TopicLine
from src.topic_finder import TopicWriter, find_candidates, load_seeds, written_from_jobs

log = logging.getLogger(__name__)

_RUN_LOCK = threading.Lock()      # 한 번에 한 회차만
WEEKLY_HOUR = 9                   # 한국 시간 월요일 이 시각부터 그 주 회차를 보냄
WEEKLY_MAX_TRIES = 2              # 실패하면 다음 날 한 번 더
RETRY_AFTER = timedelta(hours=20)
CHECK_SECONDS = 600               # 실행기가 10분마다 확인
# 자동 등록(2026-11-15 사용자 결정, 2026-11-23 변경): 글쓰기·승인 대기 글이 AUTO_TARGET편보다 적으면
# 하루 AUTO_HOURS 회차마다 채운다. 사용자가 하루 3~4편을 올리므로 하루 상한을 소비량보다 크게,
# 낮에 승인해서 줄어든 만큼 그날 오후에 다시 채우려고 회차를 둘로 나눴다(글쓰기는 원래 한 편씩 차례로 돌아 부하는 같다).
AUTO_HOURS = (9, 15)              # 한국 시간 회차 시작 시각(9시는 아침 8시 자동 발행 뒤)
AUTO_TARGET = 8                   # 대기 글을 이만큼 유지
AUTO_MAX_PER_DAY = 5              # 하루(모든 회차 합계) 자동 등록 상한
AUTO_MAX_TRIES = 2                # 찾기가 실패하면 그 회차에 한 번 더
AUTO_RETRY_AFTER = timedelta(hours=1)
NOT_FOUND_NOTE = "못 찾음 알림"    # 이 표시가 오늘 회차 기록에 있으면 '주제 못 찾음' 알림을 다시 보내지 않음(하루 한 번)
EXISTING_TOPICS = 200             # 뉴스 제도가 이미 쓴 글과 같은지 AI가 볼 최근 글 주제 수
NEWS_SEEDS_FILE = "topic_seeds_news.txt"   # 발행한 뉴스 제도가 쌓이는 목록(data 폴더, Git 제외)
PENDING = ("queued", "generating", "tts", "media", "rendering", "awaiting_approval")
HEADERS = {"weekly": "이번 주 블로그 주제 후보", "more": "새 블로그 주제 후보", "manual": "블로그 주제 후보"}


@dataclass
class TopicDeps:
    settings: Any
    search: Any
    generator: Any | None          # None이면 Gemini 없이(기본 목록 + 틀 문장)
    telegram: Any
    finder: Callable = find_candidates


def news_seeds_path(settings: Any):
    return settings.data_dir / NEWS_SEEDS_FILE


def _seeds(settings: Any):
    return load_seeds(extra=news_seeds_path(settings))


def _existing_topics(jobs: list[dict[str, Any]]) -> list[str]:
    """이미 쓴(쓰는 중인) 글 주제, 최근 것부터. 뉴스 제도가 이름만 다른 같은 제도인지 AI가 대조한다."""
    alive = [j for j in jobs if j.get("status") != "canceled" and j.get("topic")]
    alive.sort(key=lambda j: j["id"], reverse=True)
    return list(dict.fromkeys(j["topic"] for j in alive))[:EXISTING_TOPICS]


def _all_jobs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT id, topic, status, post_id, post_url, created_at, source_ref FROM jobs")]


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
            seeds = _seeds(settings)
            jobs = _all_jobs(jobs_conn)
            written = written_from_jobs(jobs, seeds)
            writer = TopicWriter(deps.generator) if deps.generator is not None else None
            result = deps.finder(deps.search, writer, seeds, written, today=today,
                                 skip_keys=topic_store.recent_skip_keys(store, now=now),
                                 existing=_existing_topics(jobs))
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


def _register_candidate(store: sqlite3.Connection, jobs_conn: sqlite3.Connection, cand: dict[str, Any],
                        now: datetime | None) -> tuple[int | None, bool, str]:
    """후보 하나를 글쓰기 대기열에 넣는다(버튼·자동 등록 공용). (작업 번호, 이미 있었는지, 못 넣은 이유)."""
    if cand["kind"] == "update":
        return None, False, "고치기 후보는 등록 대상이 아닙니다"
    ref = f"topic:{cand['id']}"
    existing = jobs_conn.execute("SELECT id FROM jobs WHERE source = 'web' AND source_ref = ?",
                                 (ref,)).fetchone()
    if existing is None:
        same = _job_for_same_topic(store, jobs_conn, cand)   # 다른 메시지에서 같은 주제를 이미 고름
        if same is not None:
            return None, False, f"같은 주제를 이미 등록했습니다(작업 {same}번)"
        if topic_store.is_expired(cand, now):
            return None, False, "기한(7일)이 지난 후보입니다. [새 후보 더 받기]를 눌러 주세요"
    origin = (cand.get("info") or {}).get("origin")
    # 뉴스에서 찾은 제도는 출처를 작업에 남겨 발행 전 추가 확인(news_gate)을 받게 한다(버튼 등록도 같음)
    job_id = db.create_job(jobs_conn, cand["topic"], source="web", source_ref=ref,
                           checkpoint={"topic_origin": origin} if origin else None)
    topic_store.mark_chosen(store, cand["id"], job_id, now=now)
    return job_id, existing is not None, ""


def register(deps: TopicDeps, jobs_conn: sqlite3.Connection, a: TopicAction,
             now: datetime | None = None) -> str:
    """[n번 등록]: 후보를 글쓰기 대기열에 넣는다. 두 번 눌러도 한 건."""
    store = topic_store.connect(deps.settings.data_dir / "topics.sqlite3")
    try:
        cand = topic_store.get_candidate(store, a.ref_id)
        if cand is None:
            return "없는 후보입니다"
        job_id, existed, why = _register_candidate(store, jobs_conn, cand, now)
        if job_id is None:
            return why
    finally:
        store.close()
    # 이미 '등록됨'으로 바뀐 버튼은 건너뛰고, 지난번 바꾸기가 실패했으면 이번에 바꾼다
    deps.telegram.mark_topic_registered(a.message_id, a.keyboard, a.ref_id, f"등록됨(작업 {job_id})")
    if existed:
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


def weekly_due(store: sqlite3.Connection, now: datetime) -> bool:
    """이번 주 회차를 보낼 때인가. 월요일 9시(한국 시간) 이후, 아직 안 보냈고, 실패는 다음 날 1번만 재시도."""
    kst = now.astimezone(topic_store.KST)
    if kst.weekday() == 0 and kst.hour < WEEKLY_HOUR:
        return False
    topic_store.fail_stale_running(store, now=now)
    batches = topic_store.weekly_batches(store, topic_store.week_of(now))
    if any(b["status"] != "failed" for b in batches):
        return False
    if len(batches) >= WEEKLY_MAX_TRIES:
        return False
    if batches:
        last = datetime.fromisoformat(batches[-1]["finished_at"] or batches[-1]["created_at"])
        return now >= last + RETRY_AFTER
    return True


def run_weekly_if_due(deps: TopicDeps, now: datetime | None = None) -> str | None:
    """실행기가 주기적으로 부른다. 보낼 때가 아니면 None, 보냈거나 실패했으면 안내 문장."""
    now = now or datetime.now(timezone.utc)
    store = topic_store.connect(deps.settings.data_dir / "topics.sqlite3")
    try:
        if not weekly_due(store, now):
            return None
        tries = len(topic_store.weekly_batches(store, topic_store.week_of(now))) + 1
    finally:
        store.close()
    try:
        return run_batch(deps, "weekly", now=now)
    except Exception as exc:  # noqa: BLE001 - 실패는 장부에 남고, 사용자에게 알린다
        more = "내일 한 번 더 시도합니다." if tries < WEEKLY_MAX_TRIES else \
            "이번 주는 더 시도하지 않습니다. 필요하면 Claude에게 '주제 찾아줘'라고 해 주세요."
        note = f"[주제 후보] 이번 주 후보 찾기 실패({exc.__class__.__name__}). {more}"
        try:
            deps.telegram.send_text(note)
        except Exception:  # noqa: BLE001
            log.warning("주간 후보 실패 안내도 보내지 못했습니다")
        return note


def _slot_start(now: datetime) -> datetime | None:
    """지금 속한 자동 등록 회차의 시작 시각(한국 시간). 첫 회차 전이면 None."""
    local = now.astimezone(topic_store.KST)
    hours = [h for h in AUTO_HOURS if h <= local.hour]
    return local.replace(hour=max(hours), minute=0, second=0, microsecond=0) if hours else None


def _slot_batches(store: sqlite3.Connection, now: datetime) -> list[dict[str, Any]]:
    start = _slot_start(now)
    if start is None:
        return []
    return [b for b in topic_store.auto_batches_today(store, now)
            if datetime.fromisoformat(b["created_at"]) >= start]


def auto_due(store: sqlite3.Connection, now: datetime) -> bool:
    """이번 회차에 자동 등록을 해도 되는가(대기 글 수는 따로 본다).
    회차마다: 성공한 시도가 있으면 끝, 실패는 1시간 뒤 1번만 재시도."""
    if _slot_start(now) is None:
        return False
    topic_store.fail_stale_running(store, now=now)
    batches = _slot_batches(store, now)
    if any(b["status"] != "failed" for b in batches) or len(batches) >= AUTO_MAX_TRIES:
        return False
    if batches:
        last = datetime.fromisoformat(batches[-1]["finished_at"] or batches[-1]["created_at"])
        return now >= last + AUTO_RETRY_AFTER
    return True


def auto_register_if_due(deps: TopicDeps, now: datetime | None = None) -> str | None:
    """실행기가 주기적으로 부른다. 대기 글이 넉넉하거나 오늘 이미 했으면 None.

    - 기본 제도 목록 + 뉴스에서 새로 찾은 제도(2026-11-23 사용자 결정). 기존 글 고치기와 '지역별 비교' 후보는
      자동 등록하지 않는다(비교 글은 월요일 후보로만). 뉴스 제도 글은 발행 전 news_gate 추가 확인을 받는다.
    - 등록은 [n번 등록] 버튼과 같은 코드(같은 주제 두 번 등록 방지 포함).
    - 텔레그램에 무엇을 넣었는지 알린다. 마음에 안 들면 승인 요청 때 [취소].
    """
    now = now or datetime.now(timezone.utc)
    jobs_conn = db.connect(deps.settings.db_path)
    store = topic_store.connect(deps.settings.data_dir / "topics.sqlite3")
    try:
        if not auto_due(store, now):
            return None
        pending = _pending(jobs_conn)
        need = min(AUTO_TARGET - pending, AUTO_MAX_PER_DAY - topic_store.auto_registered_today(store, now))
        if need <= 0:
            return None
        if not _RUN_LOCK.acquire(blocking=False):
            return None                                   # 다른 후보 찾기가 도는 중 → 다음 확인 때
        try:
            if not auto_due(store, now):                  # 잠금 잡는 사이 바뀌었을 수 있음
                return None
            tries = len(_slot_batches(store, now)) + 1
            batch_id = topic_store.start_batch(store, "auto", now=now)
            try:
                return _auto_register(deps, store, jobs_conn, batch_id, need, now)
            except Exception as exc:  # noqa: BLE001 - 실패는 장부에 남기고 알린다
                log.exception("주제 자동 등록 회차 %s 실패", batch_id)
                err = f"{exc.__class__.__name__}: {exc}"
                if topic_store.get_batch(store, batch_id)["status"] != "running":
                    # 등록을 시작한 뒤 멈춤: 일부가 이미 들어갔을 수 있어 오늘은 다시 돌리지 않는다
                    topic_store.finish_batch(store, batch_id, None, now=now, note=f"등록 중 오류: {err}"[:500])
                    _send(deps, f"[주제 자동 등록] 등록 도중 오류가 났습니다({exc.__class__.__name__}). "
                                "일부만 들어갔을 수 있어 이번 회차는 더 시도하지 않습니다.")
                    return f"등록 중 오류: {exc.__class__.__name__}"
                topic_store.fail_batch(store, batch_id, err, now=now)
                note = f"[주제 자동 등록] 실패({exc.__class__.__name__})"
                if tries >= AUTO_MAX_TRIES:
                    _send(deps, f"{note}. 이번 회차는 더 시도하지 않습니다. 대기 글이 {pending}편이니 "
                                f"월요일 후보 메시지에서 직접 등록해 주세요.")
                return note
        finally:
            _RUN_LOCK.release()
    finally:
        store.close()
        jobs_conn.close()


def _send(deps: TopicDeps, text: str) -> None:
    try:
        deps.telegram.send_text(text)
    except Exception:  # noqa: BLE001
        log.warning("자동 등록 안내를 보내지 못했습니다")


def _pending(jobs_conn: sqlite3.Connection) -> int:
    # 갱신 작업(이미 있는 글 고치기)은 새 글 대기 수에서 뺀다
    return sum(j["status"] in PENDING and not str(j.get("source_ref") or "").startswith("refresh:")
               for j in _all_jobs(jobs_conn))


def _auto_register(deps: TopicDeps, store: sqlite3.Connection, jobs_conn: sqlite3.Connection,
                   batch_id: int, need: int, now: datetime) -> str:
    seeds = _seeds(deps.settings)
    jobs = _all_jobs(jobs_conn)
    written = written_from_jobs(jobs, seeds)
    writer = TopicWriter(deps.generator) if deps.generator is not None else None
    # 쉬는 후보(보냈는데 안 고른 것)도 본다: 사람이 안 눌렀다고 빼면 좋은 주제가 3주씩 빠지고 목록이 금방 바닥난다.
    # 자동으로 넣은 뒤 월요일 메시지에서 같은 주제를 누르면 _job_for_same_topic이 막는다.
    result = deps.finder(deps.search, writer, seeds, written, today=now.astimezone(topic_store.KST).date(),
                         skip_keys=(), existing=_existing_topics(jobs))
    picks = [c for c in result.candidates if c.kind != "update" and not c.seed.compare_with]
    pending = _pending(jobs_conn)                     # 찾는 1~2분 사이 버튼으로 등록했을 수 있어 다시 셈
    need = min(need, AUTO_TARGET - pending)
    ids = topic_store.add_candidates(store, batch_id, picks, now=now) if need > 0 else []
    topic_store.mark_sending(store, batch_id)        # 여기부터는 작업이 생기므로 같은 회차에 다시 돌리지 않는다
    done: list[str] = []
    for cid in ids:
        if len(done) >= need:
            break
        cand = topic_store.get_candidate(store, cid)
        job_id, _, why = _register_candidate(store, jobs_conn, cand, now)
        if job_id is None:
            log.info("자동 등록 건너뜀(후보 %s): %s", cid, why)
            continue
        origin = (cand.get("info") or {}).get("origin")
        done.append(f"- 작업 {job_id}번: {cand['topic']}" + (" (뉴스에서 찾은 제도: 발행 전 추가 확인)" if origin else ""))
    topic_store.finish_batch(store, batch_id, None, now=now, note=f"자동 등록 {len(done)}편")
    if done:
        _send(deps, f"[주제 자동 등록] 대기 글이 {pending}편으로 줄어 {len(done)}편을 글쓰기 대기열에 넣었습니다.\n"
                    + "\n".join(done)
                    + "\n주제가 마음에 안 들면 승인 요청이 왔을 때 [취소]를 눌러 주세요.")
        return f"자동 등록 {len(done)}편"
    if need <= 0:
        return "그사이 대기 글이 채워져 등록 안 함"
    # '못 찾음' 알림은 하루 한 번만(사용자 결정). 오늘 이미 보냈으면 기록만 남긴다
    told = any(NOT_FOUND_NOTE in (b.get("error") or "") for b in topic_store.auto_batches_today(store, now)
               if b["id"] != batch_id)
    topic_store.finish_batch(store, batch_id, None, now=now, note=f"자동 등록 0편({NOT_FOUND_NOTE})")
    if told:
        return "자동 등록할 후보 없음(오늘 이미 알림)"
    _send(deps, f"[주제 자동 등록] 대기 글이 {pending}편인데 자동으로 넣을 만한 주제를 찾지 못했습니다. "
                "월요일 후보 메시지에서 직접 등록하거나 [새 후보 더 받기]를 눌러 주세요.")
    return "자동 등록할 후보 없음"
