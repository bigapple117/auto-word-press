import sqlite3
import threading

import pytest

from src import db
from src.db import JobError


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "jobs.sqlite3")
    yield c
    c.close()


def run_to_approval(conn, job_id):
    db.set_status(conn, job_id, "tts")
    db.set_status(conn, job_id, "media")
    db.set_status(conn, job_id, "rendering")
    db.set_status(conn, job_id, "awaiting_approval")


def test_create_and_get(conn):
    job_id = db.create_job(conn, "  근로장려금 신청 자격  ", "blog", 45, "web")
    job = db.get_job(conn, job_id)
    assert job["topic"] == "근로장려금 신청 자격"
    assert job["status"] == "queued"
    assert job["checkpoint"] == {}


def test_same_sheet_row_is_registered_once(conn):
    a = db.create_job(conn, "주제", source="sheet", source_ref="row-7")
    b = db.create_job(conn, "주제(수정)", source="sheet", source_ref="row-7")
    assert a == b
    assert len(db.list_jobs(conn)) == 1
    # 출처가 다르면 별개 작업
    c = db.create_job(conn, "주제", source="web", source_ref="row-7")
    assert c != a


@pytest.mark.parametrize("kwargs", [
    {"topic": "   "},
    {"topic": "x", "mode": "video"},
    {"topic": "x", "source": "email"},
    {"topic": "x", "target_seconds": 5},
])
def test_invalid_jobs_rejected(conn, kwargs):
    with pytest.raises(JobError):
        db.create_job(conn, **kwargs)


def test_claim_takes_oldest_once(conn):
    first = db.create_job(conn, "A")
    db.create_job(conn, "B")
    job = db.claim_next(conn)
    assert job["id"] == first and job["status"] == "generating"
    assert db.claim_next(conn)["topic"] == "B"
    assert db.claim_next(conn) is None


def test_claim_is_exclusive_across_connections(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    setup = db.connect(path)
    for i in range(20):
        db.create_job(setup, f"주제 {i}")
    setup.close()

    claimed, errors = [], []

    def worker():
        c = db.connect(path)
        try:
            while (job := db.claim_next(c)) is not None:
                claimed.append(job["id"])
        except Exception as exc:  # pragma: no cover - 실패 시 원인 확인용
            errors.append(exc)
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sorted(claimed) == list(range(1, 21))


def test_status_flow_and_invalid_transition(conn):
    job_id = db.create_job(conn, "A")
    with pytest.raises(JobError, match="허용되지"):
        db.set_status(conn, job_id, "rendering")
    db.claim_next(conn)
    run_to_approval(conn, job_id)
    assert db.get_job(conn, job_id)["status"] == "awaiting_approval"
    with pytest.raises(JobError, match="mark_published"):
        db.set_status(conn, job_id, "published")


def test_failed_keeps_error_and_retry_clears_it(conn):
    job_id = db.create_job(conn, "A")
    db.claim_next(conn)
    db.set_status(conn, job_id, "failed", error="Gemini 응답 오류")
    assert db.get_job(conn, job_id)["error"] == "Gemini 응답 오류"
    db.set_status(conn, job_id, "queued")
    job = db.claim_next(conn)
    assert job["id"] == job_id and job["error"] is None


def test_checkpoint_merges_stages(conn):
    job_id = db.create_job(conn, "A")
    db.save_checkpoint(conn, job_id, "script", {"title": "제목", "scenes": [1, 2]})
    db.save_checkpoint(conn, job_id, "tts", {"audio": "a.mp3"})
    assert db.get_job(conn, job_id)["checkpoint"] == {
        "script": {"title": "제목", "scenes": [1, 2]},
        "tts": {"audio": "a.mp3"},
    }


def test_checkpoint_rejects_non_json_without_changing(conn):
    job_id = db.create_job(conn, "A")
    db.save_checkpoint(conn, job_id, "script", {"ok": True})
    with pytest.raises(TypeError):
        db.save_checkpoint(conn, job_id, "bad", {"x": object()})
    assert db.get_job(conn, job_id)["checkpoint"] == {"script": {"ok": True}}


def test_update_fields_only_allows_result_columns(conn):
    job_id = db.create_job(conn, "A")
    db.update_fields(conn, job_id, title="근로장려금 총정리")
    assert db.get_job(conn, job_id)["title"] == "근로장려금 총정리"
    with pytest.raises(JobError):
        db.update_fields(conn, job_id, status="published")
    with pytest.raises(JobError):
        db.update_fields(conn, job_id, post_id=5)


def test_post_recorded_once_and_published(conn):
    job_id = db.create_job(conn, "A")
    db.claim_next(conn)
    db.record_post(conn, job_id, 101, "https://jalcheck.com/?p=101")
    db.record_post(conn, job_id, 101, "https://jalcheck.com/?p=101")  # 재실행: 그대로
    with pytest.raises(JobError, match="이미 글 101"):
        db.record_post(conn, job_id, 102, "https://jalcheck.com/?p=102")
    with pytest.raises(JobError, match="발행 중"):
        db.mark_published(conn, job_id, 101, "https://jalcheck.com/a")
    run_to_approval(conn, job_id)
    with pytest.raises(JobError, match="발행 중"):
        db.mark_published(conn, job_id, 101, "https://jalcheck.com/a")  # 선점 없이 발행 불가
    assert db.begin_publishing(conn, job_id) is True
    db.mark_published(conn, job_id, 101, "https://jalcheck.com/a")
    job = db.get_job(conn, job_id)
    assert job["status"] == "published" and job["post_url"] == "https://jalcheck.com/a"
    db.mark_published(conn, job_id, 101, "https://jalcheck.com/a")  # 같은 결과 다시 기록: 무해
    with pytest.raises(JobError, match="이미 글 101"):
        db.mark_published(conn, job_id, 999, "https://jalcheck.com/b")
    with pytest.raises(JobError):
        db.set_status(conn, job_id, "queued")  # 발행된 작업은 되돌리지 않음


def test_same_post_cannot_belong_to_two_jobs(conn):
    a = db.create_job(conn, "A")
    b = db.create_job(conn, "B")
    db.record_post(conn, a, 7, "u")
    with pytest.raises(JobError, match="다른 작업"):
        db.record_post(conn, b, 7, "u")


def test_unknown_job(conn):
    with pytest.raises(JobError, match="없습니다"):
        db.set_status(conn, 999, "failed")
    assert db.get_job(conn, 999) is None


def test_schema_check_blocks_bad_status_even_with_raw_sql(conn):
    job_id = db.create_job(conn, "A")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE jobs SET status = 'done' WHERE id = ?", (job_id,))


def ready_for_publish(conn, topic="A", post_id=1):
    job_id = db.create_job(conn, topic)
    db.claim_next(conn)
    db.record_post(conn, job_id, post_id, f"https://jalcheck.com/?p={post_id}")
    run_to_approval(conn, job_id)
    return job_id


def test_begin_publishing_wins_only_once(conn):
    job_id = ready_for_publish(conn)
    assert db.begin_publishing(conn, job_id) is True
    assert db.begin_publishing(conn, job_id) is False  # 두 번째 승인 요청은 진행 못 함


def test_begin_publishing_exclusive_across_connections(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    setup = db.connect(path)
    job_id = ready_for_publish(setup)
    setup.close()
    results = []

    def approve():
        c = db.connect(path)
        results.append(db.begin_publishing(c, job_id))
        c.close()

    threads = [threading.Thread(target=approve) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1


def test_begin_publishing_needs_post_and_approval_state(conn):
    no_post = db.create_job(conn, "A")
    db.claim_next(conn)
    run_to_approval(conn, no_post)
    with pytest.raises(JobError, match="워드프레스 글이 없습니다"):
        db.begin_publishing(conn, no_post)
    queued = db.create_job(conn, "B")
    db.record_post(conn, queued, 2, "u")
    assert db.begin_publishing(conn, queued) is False


def test_cannot_cancel_while_publishing_and_failure_rolls_back(conn):
    job_id = ready_for_publish(conn)
    db.begin_publishing(conn, job_id)
    with pytest.raises(JobError, match="허용되지"):
        db.set_status(conn, job_id, "canceled")
    with pytest.raises(JobError, match="begin_publishing"):
        db.set_status(conn, job_id, "publishing")
    db.set_status(conn, job_id, "awaiting_approval")  # 워드프레스 오류 → 다시 승인 대기
    assert db.begin_publishing(conn, job_id) is True


def test_cancel_after_publishing_started_is_blocked(conn):
    # 승인 처리기가 먼저 선점하면 취소 요청은 실패해야 한다
    job_id = ready_for_publish(conn)
    assert db.begin_publishing(conn, job_id)
    with pytest.raises(JobError):
        db.set_status(conn, job_id, "canceled")
    db.mark_published(conn, job_id, 1, "https://jalcheck.com/a")
    assert db.get_job(conn, job_id)["status"] == "published"


def test_canceled_job_cannot_be_published(conn):
    job_id = ready_for_publish(conn)
    db.set_status(conn, job_id, "canceled")
    assert db.begin_publishing(conn, job_id) is False


def test_recover_interrupted(conn):
    in_tts = db.create_job(conn, "TTS 도중")
    db.claim_next(conn)
    db.set_status(conn, in_tts, "tts")
    db.save_checkpoint(conn, in_tts, "script", {"title": "t"})
    publishing = ready_for_publish(conn, "발행 도중", post_id=9)
    db.begin_publishing(conn, publishing)
    done = ready_for_publish(conn, "끝남", post_id=10)
    db.begin_publishing(conn, done)
    db.mark_published(conn, done, 10, "u")
    waiting = db.create_job(conn, "대기")

    assert db.recover_interrupted(conn) == [in_tts, publishing]

    a = db.get_job(conn, in_tts)
    assert a["status"] == "queued" and a["checkpoint"] == {"script": {"title": "t"}}
    b = db.get_job(conn, publishing)
    assert b["status"] == "awaiting_approval" and b["post_id"] == 9
    assert "확인 필요" in b["error"]
    assert db.get_job(conn, waiting)["status"] == "queued"
    assert db.get_job(conn, done)["status"] == "published"
    assert db.claim_next(conn)["id"] == in_tts  # 오래된 작업부터 다시 처리
    assert db.recover_interrupted(conn) == [in_tts]  # 지금 실행 중인 것도 대상이 됨: 시작 시 한 번만


def test_record_post_blocked_while_publishing(conn):
    job_id = db.create_job(conn, "A")
    db.claim_next(conn)
    run_to_approval(conn, job_id)
    db.record_post(conn, job_id, 3, "u")
    db.begin_publishing(conn, job_id)
    db.record_post(conn, job_id, 3, "u")  # 같은 번호는 무해
    with pytest.raises(JobError):
        db.record_post(conn, job_id, 4, "u")


def test_new_db_gets_schema_version(tmp_path):
    c = db.connect(tmp_path / "jobs.sqlite3")
    assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    c.close()
    db.connect(tmp_path / "jobs.sqlite3").close()  # 다시 열어도 정상


def test_old_schema_db_is_refused(tmp_path):
    path = tmp_path / "old.sqlite3"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT)")
    raw.commit()
    raw.close()
    with pytest.raises(JobError, match="DB 이전이 필요"):
        db.connect(path)
