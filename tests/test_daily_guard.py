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
    svc.wp.today_posts = []
    svc.wp.published_since = lambda since: list(svc.wp.today_posts)
    return DailyGuard(orch, tmp_path / "daily_guard.json"), orch, svc, conn


def awaiting(orch, conn, *topics):
    ids = [db.create_job(conn, t) for t in topics]
    for _ in ids:
        orch.run_once()
    return ids


def test_nothing_before_7pm(guard):
    g, orch, svc, conn = guard
    awaiting(orch, conn, "A")
    sent = len(svc.telegram.texts)
    assert g.run_if_due(at(18, 59)) is None
    assert len(svc.telegram.texts) == sent


def test_remind_once_then_auto_publish_oldest_once(guard):
    g, orch, svc, conn = guard
    a, b = awaiting(orch, conn, "A", "B")
    sent = len(svc.telegram.texts)
    assert g.run_if_due(at(19, 0)) == f"알림: 작업 {a}번 예정"
    assert "자동 발행합니다" in svc.telegram.texts[-1] and svc.telegram.replies[-1] is not None
    assert g.run_if_due(at(19, 5)) is None                       # 알림은 하루 한 번
    assert len(svc.telegram.texts) == sent + 1
    assert g.run_if_due(at(22, 0)) == f"자동 발행: 작업 {a}번"
    assert db.get_job(conn, a)["status"] == "published"
    assert db.get_job(conn, b)["status"] == "awaiting_approval"
    assert "자동 발행했습니다" in svc.telegram.texts[-1]
    assert g.run_if_due(at(22, 5)) is None                       # 자동 발행도 하루 한 번
    assert len(svc.wp.published) == 1


def test_skip_when_already_published_today(guard):
    g, orch, svc, conn = guard
    awaiting(orch, conn, "A")
    svc.wp.today_posts = [77]                                    # 관리자 화면에서 직접 올린 글 포함
    sent = len(svc.telegram.texts)
    assert g.run_if_due(at(19, 0)) == "오늘 이미 발행한 글이 있음"
    svc.wp.today_posts = []
    assert g.run_if_due(at(22, 0)) is None                       # 그날은 더 안 함
    assert len(svc.telegram.texts) == sent and svc.wp.published == []


def test_new_day_starts_fresh_and_restart_after_10pm_still_publishes(guard):
    g, orch, svc, conn = guard
    a, b = awaiting(orch, conn, "A", "B")
    assert g.run_if_due(at(22, 30, day=14)) == f"자동 발행: 작업 {a}번"
    # 다음 날: 실행기가 밤 10시 넘어 켜져 저녁 알림을 놓쳐도 자동 발행은 함
    assert g.run_if_due(at(23, 10, day=15)) == f"자동 발행: 작업 {b}번"


def test_no_awaiting_jobs_only_notifies(guard):
    g, orch, svc, conn = guard
    assert g.run_if_due(at(19, 0)) == "알림: 대기 글 없음"
    assert g.run_if_due(at(22, 0)) == "대기 글 없음"
    assert "자동 발행하지 못했습니다" in svc.telegram.texts[-1]
    assert svc.wp.published == []


def test_low_stock_warning(guard):
    g, orch, svc, conn = guard
    awaiting(orch, conn, "A", "B")
    g.run_if_due(at(19, 0))
    assert "2개 남았습니다" in svc.telegram.texts[-1]


def test_wordpress_rejects_first_then_next_job(guard):
    g, orch, svc, conn = guard
    a, b = awaiting(orch, conn, "A", "B")
    svc.wp.fail_publish = 1                                     # 첫 글은 워드프레스가 거절(403)
    assert g.run_if_due(at(22, 0)) == f"자동 발행: 작업 {b}번"
    assert db.get_job(conn, a)["status"] == "awaiting_approval"


def test_wordpress_check_error_retries_later(guard):
    g, orch, svc, conn = guard
    awaiting(orch, conn, "A")

    def broken(since):
        raise ConnectionError("워드프레스 응답 없음")
    svc.wp.published_since = broken
    with pytest.raises(ConnectionError):
        g.run_if_due(at(22, 0))
    svc.wp.published_since = lambda since: []
    assert g.run_if_due(at(22, 5)).startswith("자동 발행")


def test_someone_is_publishing_now_waits(guard):
    g, orch, svc, conn = guard
    (a,) = awaiting(orch, conn, "A")
    nonce = db.get_job(conn, a)["checkpoint"]["approval"]["nonce"]
    assert db.begin_publishing(conn, a, nonce=nonce)
    assert g.run_if_due(at(22, 0)) is None
    assert svc.wp.published == []


def test_race_with_button_never_publishes_second_job(guard):
    g, orch, svc, conn = guard
    a, b = awaiting(orch, conn, "A", "B")
    real = orch.handle_decision

    def pressed_first(d):                                        # 자동 발행 직전 사용자가 A의 [발행]을 먼저 누름
        nonce = db.get_job(conn, a)["checkpoint"]["approval"]["nonce"]
        real(Decision(a, "publish", nonce, "cb", 1))
        return real(d)
    orch.handle_decision = pressed_first
    note = g.run_if_due(at(22, 0))
    assert "이미 발행된 글" in note
    assert db.get_job(conn, b)["status"] == "awaiting_approval"
    orch.handle_decision = real
    svc.wp.today_posts = [500 + a]
    assert g.run_if_due(at(22, 5)) == "오늘 이미 발행한 글이 있음"
    assert svc.wp.published == [(a, 500 + a)]


def test_uses_latest_nonce_after_resend(guard):
    g, orch, svc, conn = guard
    (a,) = awaiting(orch, conn, "A")
    orch.resend_approval(db.get_job(conn, a))                   # [다음 글]로 승인 요청이 새 번호로 바뀜
    assert g.run_if_due(at(22, 0)) == f"자동 발행: 작업 {a}번"


def test_uncertain_result_is_checked_again(guard):
    g, orch, svc, conn = guard
    a, b = awaiting(orch, conn, "A", "B")
    svc.wp.timeout_publish = 1                                   # 실제로는 발행됐지만 응답이 끊김
    assert "확인 중" in g.run_if_due(at(22, 0))
    assert db.get_job(conn, b)["status"] == "awaiting_approval"
    assert g.run_if_due(at(22, 5)) is None                       # '발행 중' 정리될 때까지 기다림
    db.set_status(conn, a, "awaiting_approval")                  # 실제로는 안 올라갔다고 정리된 경우
    assert g.run_if_due(at(22, 10)) == f"자동 발행: 작업 {a}번"


def test_telegram_failure_after_publish_still_records(guard):
    g, orch, svc, conn = guard
    awaiting(orch, conn, "A", "B")
    real = svc.telegram.send_text

    def flaky(text, **kw):
        if "자동 발행했습니다" in text:
            raise RuntimeError("텔레그램 끊김")
        return real(text, **kw)
    svc.telegram.send_text = flaky
    assert g.run_if_due(at(22, 0)).startswith("자동 발행")
    assert g.run_if_due(at(22, 5)) is None
    assert len(svc.wp.published) == 1
