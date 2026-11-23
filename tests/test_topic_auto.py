import sqlite3
from datetime import date, datetime, timedelta, timezone

from src import db, topic_service, topic_store
from src.topic_finder import Seed, Written
from src.topic_service import auto_register_if_due
from tests.test_topic_service import cand, env, store  # noqa: F401 - 가짜 환경 재사용

AM9 = datetime(2026, 11, 16, 0, 10, tzinfo=timezone.utc)      # 한국 시간 09:10


def fill(env, n, status="awaiting_approval"):
    for i in range(n):
        jid = db.create_job(env.jobs, f"기존 글 {i}")
        force(env, jid, status)


def force(env, jid, status):                                    # 테스트 준비용: 흐름 검사 없이 상태만
    env.jobs.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, jid))


def jobs(env):
    return [dict(r) for r in env.jobs.execute("SELECT id, topic, source, source_ref, status FROM jobs ORDER BY id")]


def test_nothing_before_9am_or_when_queue_is_full(env):
    fill(env, 2)
    assert auto_register_if_due(env.deps, now=AM9 - timedelta(minutes=20)) is None
    fill(env, 6, status="queued")                              # 글쓰기 중 6 + 승인 대기 2 = 8
    assert auto_register_if_due(env.deps, now=AM9) is None
    assert env.calls == [] and env.tg.texts == []


def test_fills_up_to_target_with_button_registration(env):
    fill(env, 3)
    assert auto_register_if_due(env.deps, now=AM9) == "자동 등록 2편"
    new = [j for j in jobs(env) if (j["source_ref"] or "").startswith("topic:")]
    assert [j["topic"] for j in new] == ["기초연금 신청 자격과 받는 방법", "부모급여 신청 자격과 받는 방법"]
    assert all(j["status"] == "queued" and j["source_ref"].startswith("topic:") for j in new)
    assert env.calls[-1].get("discover", True)                 # 뉴스 새 제도도 찾음(2026-11-23)
    assert "existing" in env.calls[-1]                         # 이미 쓴 글 주제를 넘겨 같은 제도인지 AI가 봄
    assert "작업 4번: 기초연금" in env.tg.texts[-1] and "[취소]" in env.tg.texts[-1]
    s = store(env)
    assert all(topic_store.get_candidate(s, int(j["source_ref"][6:]))["job_id"] == j["id"] for j in new)


def test_max_five_a_day_and_once_per_slot(env):
    env.found["cands"] = [cand(n) for n in ("기초연금", "부모급여", "아동수당", "실업급여", "근로장려금", "자녀장려금")]
    assert auto_register_if_due(env.deps, now=AM9) == "자동 등록 5편"   # 0편 → 상한 5편
    force(env, 1, "published")
    assert auto_register_if_due(env.deps, now=AM9 + timedelta(hours=5)) is None   # 같은 회차
    assert len(env.calls) == 1
    env.found["cands"] = [cand(n) for n in ("자녀장려금", "청년수당")]
    assert auto_register_if_due(env.deps, now=AM9 + timedelta(days=1)) == "자동 등록 2편"


def test_afternoon_fills_queue_after_publication(env):
    fill(env, 6)
    assert auto_register_if_due(env.deps, now=AM9) == "자동 등록 2편"
    force(env, 1, "published")
    force(env, 2, "published")
    env.found["cands"] = [cand("아동수당"), cand("실업급여")]
    assert auto_register_if_due(env.deps, now=AM9 + timedelta(hours=6)) == "자동 등록 2편"
    assert topic_service._pending(env.jobs) == 8
    assert topic_store.auto_registered_today(store(env), AM9 + timedelta(hours=6)) == 4


def test_daily_limit_is_shared_between_slots(env):
    env.found["cands"] = [cand(n) for n in ("기초연금", "부모급여", "아동수당")]
    assert auto_register_if_due(env.deps, now=AM9) == "자동 등록 3편"
    force(env, 1, "published")
    env.found["cands"] = [cand(n) for n in ("실업급여", "근로장려금", "자녀장려금")]
    afternoon = AM9 + timedelta(hours=6)
    assert auto_register_if_due(env.deps, now=afternoon) == "자동 등록 2편"
    assert topic_store.auto_registered_today(store(env), afternoon) == 5
    assert len(jobs(env)) == 5


def test_success_is_not_repeated_at_13_in_same_slot(env):
    assert auto_register_if_due(env.deps, now=AM9) == "자동 등록 2편"
    env.found["cands"] = [cand("아동수당")]
    assert auto_register_if_due(env.deps, now=AM9 + timedelta(hours=4)) is None
    assert len(env.calls) == 1
    assert len(jobs(env)) == 2


def test_afternoon_retries_after_morning_fails_twice(env):
    good = env.deps.finder
    def fail(*a, **k):
        raise RuntimeError("naver down")
    env.deps.finder = fail
    assert "실패" in auto_register_if_due(env.deps, now=AM9)
    assert "실패" in auto_register_if_due(env.deps, now=AM9 + timedelta(hours=1))
    env.deps.finder = good
    assert auto_register_if_due(env.deps, now=AM9 + timedelta(hours=4)) is None
    assert auto_register_if_due(env.deps, now=AM9 + timedelta(hours=6)) == "자동 등록 2편"
    assert [b["status"] for b in topic_store.auto_batches_today(store(env), AM9 + timedelta(hours=6))] == ["failed", "failed", "sent"]


def test_registers_news_topic_with_origin_but_skips_update_and_compare(env):
    news = cand("새제도")
    news.seed = Seed("새제도", "새제도", (), "지원금", discovered=True, scope="광역", region="서울시",
                     field="주거", agency="서울시")
    cmp = cand("청년 교통비 지원 지역별 비교")
    cmp.seed = Seed("청년 교통비 지원 지역별 비교", "청년 교통비", (), "지원금", discovered=True, scope="광역",
                    region="서울시, 부산시", group="청년 교통비 지원", compare_with=("서울시", "부산시"))
    env.found["cands"] = [news, cmp, cand("에너지바우처", kind="update",
                                          written=Written("에너지바우처", 2026, 6, 70, date(2026, 11, 1))),
                          cand("기초연금")]
    assert auto_register_if_due(env.deps, now=AM9) == "자동 등록 2편"
    assert [j["topic"] for j in jobs(env)] == ["새제도 신청 자격과 받는 방법", "기초연금 신청 자격과 받는 방법"]
    origin = db.get_job(env.jobs, 1)["checkpoint"]["topic_origin"]
    assert origin["news"] and origin["region"] == "서울시" and origin["field"] == "주거"
    assert "topic_origin" not in db.get_job(env.jobs, 2)["checkpoint"]
    assert "뉴스에서 찾은 제도" in env.tg.texts[-1]


def test_same_topic_already_registered_from_weekly_is_not_doubled(env):
    from src.topic_service import run_batch
    fill(env, 3)
    run_batch(env.deps, "weekly", now=AM9 - timedelta(hours=2))
    s = store(env)
    weekly_id = s.execute("SELECT id FROM candidates WHERE name = '기초연금'").fetchone()[0]
    jid = db.create_job(env.jobs, "기초연금 신청 자격과 받는 방법", source="web", source_ref=f"topic:{weekly_id}")
    topic_store.mark_chosen(s, weekly_id, jid)
    force(env, jid, "published")                  # 대기 3편 그대로
    assert auto_register_if_due(env.deps, now=AM9) == "자동 등록 1편"
    assert sum("기초연금" in j["topic"] for j in jobs(env)) == 1


def test_no_candidates_notifies_once(env):
    env.found["cands"] = []
    assert auto_register_if_due(env.deps, now=AM9) == "자동 등록할 후보 없음"
    assert "찾지 못했습니다" in env.tg.texts[-1]
    assert auto_register_if_due(env.deps, now=AM9 + timedelta(hours=3)) is None
    assert len(env.tg.texts) == 1
    # 오후 회차도 못 찾으면 기록만 하고 알림은 하루 한 번(2026-11-23 사용자 결정)
    assert auto_register_if_due(env.deps, now=AM9 + timedelta(hours=6)) == "자동 등록할 후보 없음(오늘 이미 알림)"
    assert len(env.tg.texts) == 1
    assert auto_register_if_due(env.deps, now=AM9 + timedelta(days=1)) == "자동 등록할 후보 없음"
    assert len(env.tg.texts) == 2


def test_failure_retries_once_after_an_hour_then_notifies(env):
    good = env.deps.finder
    env.deps.finder = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("naver down"))
    assert "실패" in auto_register_if_due(env.deps, now=AM9)
    assert env.tg.texts == []                                   # 첫 실패는 조용히
    assert auto_register_if_due(env.deps, now=AM9 + timedelta(minutes=30)) is None
    assert "실패" in auto_register_if_due(env.deps, now=AM9 + timedelta(hours=1, minutes=1))
    assert "더 시도하지 않습니다" in env.tg.texts[-1]
    env.deps.finder = good
    assert auto_register_if_due(env.deps, now=AM9 + timedelta(hours=5)) is None
    assert auto_register_if_due(env.deps, now=AM9 + timedelta(days=1)) == "자동 등록 2편"


def test_waits_while_other_topic_search_is_running(env):
    topic_service._RUN_LOCK.acquire()
    try:
        assert auto_register_if_due(env.deps, now=AM9) is None
    finally:
        topic_service._RUN_LOCK.release()
    assert topic_store.auto_batches_today(store(env), AM9) == []
    assert auto_register_if_due(env.deps, now=AM9 + timedelta(minutes=10)).startswith("자동 등록")


def test_crash_after_registering_does_not_register_again_same_day(env):
    calls = {"n": 0}
    real = topic_service._register_candidate

    def boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk")
        return real(*a, **k)
    topic_service._register_candidate = boom
    try:
        assert "등록 중 오류" in auto_register_if_due(env.deps, now=AM9)
    finally:
        topic_service._register_candidate = real
    assert len(jobs(env)) == 1
    assert "일부만 들어갔을 수 있어" in env.tg.texts[-1]                # 첫 시도라도 알림
    assert auto_register_if_due(env.deps, now=AM9 + timedelta(hours=2)) is None   # 같은 날 다시 안 돌림


def test_old_store_is_migrated_keeping_ids(tmp_path):
    path = tmp_path / "topics.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(topic_store._SCHEMA.replace(", 'auto'", ""))
    old.execute("INSERT INTO batches (id, kind, week, status, created_at) VALUES (7, 'weekly', '2026-W46', 'sent', '2026-11-09T00:30:00+00:00')")
    old.execute("INSERT INTO candidates (batch_id, ckey, kind, name, year, category, topic, created_at, expires_at) "
                "VALUES (7, 'k', 'new', 'n', 2026, '지원금', 't', 'x', 'y')")
    old.commit()
    old.close()
    conn = topic_store.connect(path)
    assert conn.execute("SELECT id, kind, status FROM batches").fetchall()[0][:] == (7, "weekly", "sent")
    assert topic_store.start_batch(conn, "auto") == 8
    assert topic_store.start_batch(conn, "weekly", now=datetime(2026, 11, 10, tzinfo=timezone.utc)) is None  # 주 1회 색인 유지
    assert conn.execute("SELECT batch_id FROM candidates").fetchone()[0] == 7
    topic_store.connect(path).close()                          # 두 번째 연결은 그대로


def test_auto_ignores_rest_rule_and_its_leftovers_do_not_rest_weekly(env):
    from src.topic_service import run_batch
    fill(env, 7)
    run_batch(env.deps, "weekly", now=AM9 - timedelta(hours=1))   # 기초연금·부모급여 보냈는데 안 고름
    assert auto_register_if_due(env.deps, now=AM9) == "자동 등록 1편"
    assert env.calls[-1]["skip"] == set()                      # 자동 등록은 쉬는 후보도 봄
    s = store(env)
    # 자동 회차에서 등록 안 된 남은 후보(부모급여 자동 행)는 쉬기 목록에 새로 넣지 않음(주간 보낸 것만)
    auto_left = s.execute("SELECT c.ckey FROM candidates c JOIN batches b ON b.id = c.batch_id "
                          "WHERE b.kind = 'auto' AND c.job_id IS NULL").fetchall()
    assert auto_left
    weekly_unchosen = {r[0] for r in s.execute("SELECT c.ckey FROM candidates c JOIN batches b ON b.id = c.batch_id "
                                               "WHERE b.kind = 'weekly' AND c.job_id IS NULL")}
    assert topic_store.recent_skip_keys(s, now=AM9) == weekly_unchosen


def test_skipped_duplicate_is_replaced_by_next_candidate(env):
    fill(env, 3)
    real = topic_service._register_candidate
    first = {"done": False}

    def dup_first(store_, jc, cand_, now):
        if not first["done"]:
            first["done"] = True
            return None, False, "같은 주제를 이미 등록했습니다(작업 1번)"
        return real(store_, jc, cand_, now)
    topic_service._register_candidate = dup_first
    try:
        env.found["cands"] = [cand(n) for n in ("기초연금", "부모급여", "아동수당")]
        assert auto_register_if_due(env.deps, now=AM9) == "자동 등록 2편"   # 기초연금 건너뛰고 부모급여·아동수당
    finally:
        topic_service._register_candidate = real


def test_need_is_recounted_after_search(env):
    fill(env, 6)
    real = env.deps.finder

    def slow(*a, **k):
        fill(env, 1)                                            # 찾는 사이 버튼으로 1편 등록
        return real(*a, **k)
    env.deps.finder = slow
    assert auto_register_if_due(env.deps, now=AM9) == "자동 등록 1편"
    assert "7편으로 줄어" in env.tg.texts[-1]
