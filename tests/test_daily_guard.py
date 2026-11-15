from datetime import datetime

import pytest

from src import db
from src.daily_guard import KST, DailyGuard
from src.telegram_notifier import Decision
from tests.test_orchestrator import env  # noqa: F401 - 실행기 가짜 환경 재사용


def at(hour, minute=0, day=15):
    return datetime(2026, 11, day, hour, minute, tzinfo=KST)


@pytest.fixture
def guard(env, tmp_path):  # noqa: F811
    orch, svc, conn, _ = env
    return DailyGuard(orch, tmp_path / "daily_guard.json"), orch, svc, conn


def awaiting(orch, conn, *topics):
    ids = [db.create_job(conn, t) for t in topics]
    for _ in ids:
        orch.run_once()
    return ids


def test_nothing_before_8am(guard):
    g, orch, svc, conn = guard
    awaiting(orch, conn, "A")
    assert g.run_if_due(at(7, 59)) is None
    assert svc.wp.published == []


def test_publishes_oldest_once_a_day_like_button(guard):
    g, orch, svc, conn = guard
    a, b = awaiting(orch, conn, "A", "B")
    sent = len(svc.telegram.texts)
    assert g.run_if_due(at(8, 0)) == f"자동 발행: 작업 {a}번"
    assert db.get_job(conn, a)["status"] == "published"
    assert db.get_job(conn, b)["status"] == "awaiting_approval"
    # 버튼을 눌렀을 때와 같은 알림: 승인 요청 메시지가 '발행했습니다'로 바뀜, 따로 '자동 발행' 메시지는 없음
    assert "발행했습니다" in svc.telegram.done[-1][1]
    assert not any("자동" in t for t in svc.telegram.texts[sent:])
    assert g.run_if_due(at(8, 1)) is None and g.run_if_due(at(23, 59)) is None
    assert len(svc.wp.published) == 1
    assert g.run_if_due(at(8, 0, day=16)) == f"자동 발행: 작업 {b}번"   # 다음 날 다음 글


def test_publishes_even_if_already_published_today(guard):
    g, orch, svc, conn = guard
    a, b = awaiting(orch, conn, "A", "B")
    nonce = db.get_job(conn, a)["checkpoint"]["approval"]["nonce"]
    assert orch.handle_decision(Decision(a, "publish", nonce, "cb", 1)) == "발행했습니다"   # 새벽에 직접 발행
    assert g.run_if_due(at(8, 0)) == f"자동 발행: 작업 {b}번"


def test_worker_down_at_8_publishes_when_back_same_day(guard):
    g, orch, svc, conn = guard
    (a,) = awaiting(orch, conn, "A")
    assert g.run_if_due(at(15, 30)) == f"자동 발행: 작업 {a}번"


def test_no_awaiting_jobs_only_notifies_once(guard):
    g, orch, svc, conn = guard
    assert g.run_if_due(at(8, 0)) == "대기 글 없음"
    assert "주제를 등록해 주세요" in svc.telegram.texts[-1]
    sent = len(svc.telegram.texts)
    assert g.run_if_due(at(8, 1)) is None
    assert len(svc.telegram.texts) == sent and svc.wp.published == []


def test_wordpress_rejects_first_then_next_job(guard):
    g, orch, svc, conn = guard
    a, b = awaiting(orch, conn, "A", "B")
    svc.wp.fail_publish = 1                                     # 첫 글은 워드프레스가 거절(403)
    assert g.run_if_due(at(8, 0)) == f"자동 발행: 작업 {b}번"
    assert db.get_job(conn, a)["status"] == "awaiting_approval"


def test_someone_is_publishing_oldest_now_waits(guard):
    g, orch, svc, conn = guard
    a, b = awaiting(orch, conn, "A", "B")
    real = orch.handle_decision

    def pressed_first(d):                                        # 자동 발행 직전 사용자가 A의 [발행]을 먼저 누름
        nonce = db.get_job(conn, a)["checkpoint"]["approval"]["nonce"]
        real(Decision(a, "publish", nonce, "cb", 1))
        return real(d)
    orch.handle_decision = pressed_first
    assert "이미 발행된 글" in g.run_if_due(at(8, 0))
    orch.handle_decision = real
    assert g.run_if_due(at(8, 1)) == f"발행됨: 작업 {a}번"      # 직접 올린 글을 오늘 아침 1편으로 인정
    assert g.run_if_due(at(8, 2)) is None
    assert db.get_job(conn, b)["status"] == "awaiting_approval"   # 두 번째 글은 안 올림


def test_uses_latest_nonce_after_resend(guard):
    g, orch, svc, conn = guard
    (a,) = awaiting(orch, conn, "A")
    orch.resend_approval(db.get_job(conn, a))                   # [다음 글]로 승인 요청이 새 번호로 바뀜
    assert g.run_if_due(at(8, 0)) == f"자동 발행: 작업 {a}번"


def test_uncertain_result_that_actually_published_is_not_repeated(guard):
    g, orch, svc, conn = guard
    a, b = awaiting(orch, conn, "A", "B")
    svc.wp.timeout_publish = 1                                   # 실제로는 발행됐지만 응답이 끊김
    assert "확인 중" in g.run_if_due(at(8, 0))
    assert g.run_if_due(at(8, 1)) is None                        # '발행 중' 정리될 때까지 기다림
    db.mark_published(conn, a, 500 + a, "https://jalcheck.com/a/")   # 실행기가 실제 상태로 맞춤: 올라갔음
    assert g.run_if_due(at(8, 2)) == f"발행됨: 작업 {a}번"
    assert g.run_if_due(at(8, 3)) is None
    assert db.get_job(conn, b)["status"] == "awaiting_approval"


def test_uncertain_result_that_failed_retries_same_job(guard):
    g, orch, svc, conn = guard
    a, b = awaiting(orch, conn, "A", "B")
    svc.wp.timeout_publish = 1
    g.run_if_due(at(8, 0))
    db.set_status(conn, a, "awaiting_approval")                  # 실제로는 안 올라갔다고 정리됨
    assert g.run_if_due(at(8, 5)) == f"자동 발행: 작업 {a}번"
    assert db.get_job(conn, b)["status"] == "awaiting_approval"


def test_publish_outage_stops_after_max_attempts(guard):
    g, orch, svc, conn = guard
    a, b = awaiting(orch, conn, "A", "B")
    svc.wp.timeout_publish = 99                                  # 발행 요청이 계속 끊김(실제로는 안 올라감)
    for minute in range(0, 30):
        g.run_if_due(at(8, minute))
        if db.get_job(conn, a)["status"] == "publishing":
            db.set_status(conn, a, "awaiting_approval")          # 실행기가 '안 올라감'으로 정리
    assert len(svc.wp.published) == 3                            # 시도 3번에서 멈춤
    assert sum("여러 번 올리려 했지만" in t for t in svc.telegram.texts) == 1
    assert g.run_if_due(at(9, 0)) is None


def test_rejected_job_is_not_retried_same_day(guard):
    g, orch, svc, conn = guard
    a, b = awaiting(orch, conn, "A", "B")
    svc.wp.fail_publish = 1                                     # A는 거절
    svc.wp.timeout_publish = 1                                  # B는 응답 끊김
    assert "확인 중" in g.run_if_due(at(8, 0))
    db.set_status(conn, b, "awaiting_approval")                 # B는 실제로는 안 올라감
    fails = sum("발행 실패" in t for t in svc.telegram.texts)
    assert g.run_if_due(at(8, 5)) == f"자동 발행: 작업 {b}번"
    assert sum("발행 실패" in t for t in svc.telegram.texts) == fails   # A 실패 알림이 다시 오지 않음
    assert db.get_job(conn, a)["status"] == "awaiting_approval"
