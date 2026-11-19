import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from src import freshness
from src.content_schema import slug_without_year


TODAY = date(2026, 6, 1)
WRITTEN = date(2026, 1, 1)
MODIFIED = date(2026, 2, 1)


def item(day):
    return freshness.FreshItem(what=day, recheck_on=day, why="확인")


def post(pid, modified="2026-01-01T00:00:00", published="2026-01-01T00:00:00"):
    return {"id": pid, "title": f"글 {pid}", "link": f"https://example.com/{pid}",
            "date_gmt": published, "modified_gmt": modified}


class FakeWP:
    def __init__(self):
        self.posts = []
        self.calls = []
        self.fail = set()
        self.contents = {}

    def list_published_posts(self):
        return self.posts

    def get_post_content(self, pid):
        self.calls.append(pid)
        if pid in self.fail:
            raise RuntimeError("failed")
        return self.contents.get(pid, "<p>본문</p>")


class FakeGenerator:
    def __init__(self, items=()):
        self.items = list(items)
        self.calls = []

    def structured(self, prompt, schema, system):
        self.calls.append((prompt, schema, system))
        return json.dumps({"items": [it.model_dump() for it in self.items]}), "fake-model"


class FakeTelegram:
    def __init__(self):
        self.messages = []
        self.fail = False

    def send_text(self, text):
        if self.fail:
            raise RuntimeError("telegram unavailable")
        self.messages.append(text)


@pytest.fixture
def env(tmp_path):
    deps = freshness.Deps(FakeWP(), FakeGenerator(), FakeTelegram(), tmp_path)
    conn = freshness.connect(tmp_path / "freshness.sqlite3")
    yield deps, conn
    conn.close()


def rows(conn):
    return {row["post_id"]: row for row in conn.execute("SELECT * FROM posts")}


def test_decide_filters_clamps_and_sorts():
    earliest, kept = freshness.decide(
        [item("2028-01-01"), item("2026-08-01"), item("2025-12-31"),
         item("bad-date"), item("2026-05-31"), item("2026-07-01")], WRITTEN, MODIFIED, TODAY)
    assert earliest == TODAY + timedelta(days=1)
    assert [x["recheck_on"] for x in kept] == [
        "2026-06-02", "2026-07-01", "2026-08-01", (MODIFIED + timedelta(days=400)).isoformat()]
    assert [x["what"] for x in kept] == ["2026-05-31", "2026-07-01", "2026-08-01", "2028-01-01"]


@pytest.mark.parametrize("items", [[], [item("invalid")], [item("2025-12-31")]])
def test_decide_empty_uses_modified_plus_year_and_never_before_tomorrow(items):
    assert freshness.decide(items, WRITTEN, MODIFIED, TODAY) == (MODIFIED + timedelta(days=365), [])
    assert freshness.decide(items, WRITTEN, MODIFIED, date(2028, 1, 1)) == (date(2028, 1, 2), [])


def test_decide_keeps_published_day_and_dates_before_modified():
    earliest, kept = freshness.decide(
        [item("2025-12-31"), item("2026-01-01"), item("2026-01-31"), item("2026-06-01")],
        WRITTEN, MODIFIED, TODAY)
    assert earliest == date(2026, 6, 2)
    assert [it["what"] for it in kept] == ["2026-01-01", "2026-01-31", "2026-06-01"]
    assert {it["recheck_on"] for it in kept} == {"2026-06-02"}


def test_decide_old_modified_never_clamps_before_tomorrow():
    earliest, kept = freshness.decide([item("2030-01-01")], WRITTEN, MODIFIED, date(2028, 1, 1))
    assert earliest == date(2028, 1, 2)
    assert kept[0]["recheck_on"] == "2028-01-02"


def test_build_prompt_includes_published_modified_today_and_limits_text():
    prompt = freshness.build_prompt("제목", "가" * freshness.MAX_TEXT + "잘릴 끝부분", WRITTEN, MODIFIED, TODAY)
    assert "[오늘] 2026-06-01" in prompt
    assert "[글 작성일] 2026-01-01" in prompt
    assert "[마지막 수정일] 2026-02-01" in prompt
    assert "[제목] 제목" in prompt
    assert prompt.split("[본문]\n", 1)[1] == "가" * freshness.MAX_TEXT


def test_plan_post_passes_both_dates_and_validates_response():
    generator = FakeGenerator([item("2026-01-31")])
    recheck, kept, model = freshness.plan_post(
        generator, "제목", "<p>신청 &amp; 마감</p>", WRITTEN, MODIFIED, TODAY)
    assert recheck == date(2026, 6, 2)
    assert kept == [{"what": "2026-01-31", "recheck_on": "2026-06-02", "why": "확인"}]
    assert model == "fake-model"
    assert generator.calls == [(
        freshness.build_prompt("제목", "신청 & 마감", WRITTEN, MODIFIED, TODAY),
        freshness.FreshPlan, freshness.SYSTEM_INSTRUCTION)]


@pytest.mark.parametrize("raw", ["invalid json", '{"items": [{"what": "기한"}]}'])
def test_plan_post_rejects_invalid_response(raw, monkeypatch):
    generator = FakeGenerator()
    monkeypatch.setattr(generator, "structured", lambda *args: (raw, "fake-model"))
    with pytest.raises(ValueError, match="유효기간 응답 형식 오류"):
        freshness.plan_post(generator, "제목", "<p>본문</p>", WRITTEN, MODIFIED, TODAY)


def test_sync_plans_new_unchanged_modified_and_unpublished(env):
    deps, conn = env
    deps.wp.posts = [post(1), post(2)]
    assert freshness.sync_plans(deps, conn, TODAY) == (["글 1", "글 2"], [])
    conn.execute("UPDATE posts SET last_notified = '2026-05-01' WHERE post_id = 1")
    conn.commit()
    assert freshness.sync_plans(deps, conn, TODAY) == ([], [])
    assert deps.wp.calls == [1, 2]
    deps.wp.posts = [post(1, "2026-02-01T00:00:00"), post(3)]
    assert freshness.sync_plans(deps, conn, TODAY) == (["글 3", "글 1"], [])
    saved = rows(conn)
    assert set(saved) == {1, 3}
    assert saved[1]["modified"] == "2026-02-01T00:00:00"
    assert saved[1]["last_notified"] is None
    assert deps.wp.calls == [1, 2, 3, 1]


def test_sync_continues_after_failure_and_reports_limit(env):
    deps, conn = env
    deps.wp.posts = [post(1), post(2), post(3)]
    deps.wp.fail.add(1)
    planned, failed = freshness.sync_plans(deps, conn, TODAY, limit=2)
    assert planned == ["글 2"]
    assert failed == ["글 1: RuntimeError", "남은 1편은 내일 이어서 판단"]
    assert deps.wp.calls == [1, 2]
    assert set(rows(conn)) == {2}


def test_sync_photo_only_edit_with_expired_deadline_is_due_tomorrow(env):
    deps, conn = env
    deps.wp.posts = [post(1, modified="2026-05-31T16:00:00", published="2025-12-31T16:00:00")]
    deps.wp.contents[1] = '<p>신청 마감: 2026년 3월 31일</p><img src="new-photo.jpg">'
    deps.generator.items = [freshness.FreshItem(
        what="신청 마감", recheck_on="2026-04-01", why="마감 다음 날 확인")]
    assert freshness.sync_plans(deps, conn, TODAY) == (["글 1"], [])
    saved = rows(conn)[1]
    assert saved["recheck_on"] == "2026-06-02"
    assert json.loads(saved["items"]) == [
        {"what": "신청 마감", "recheck_on": "2026-06-02", "why": "마감 다음 날 확인"}]
    prompt = deps.generator.calls[0][0]
    assert "[글 작성일] 2026-01-01" in prompt
    assert "[마지막 수정일] 2026-06-01" in prompt
    assert "신청 마감: 2026년 3월 31일" in prompt
    assert freshness.notify_due(deps, conn, TODAY) == 0
    assert freshness.notify_due(deps, conn, TODAY + timedelta(days=1)) == 1


def test_sync_skips_after_three_failures_without_blocking_other_posts_and_retries_edit(env):
    deps, conn = env
    deps.wp.posts = [post(1)]
    deps.wp.fail.add(1)
    assert freshness.MAX_POST_FAILURES == 3
    for tries in range(1, 4):
        assert freshness.sync_plans(deps, conn, TODAY, limit=1) == ([], ["글 1: RuntimeError"])
        failure = conn.execute("SELECT * FROM failures WHERE post_id = 1").fetchone()
        assert failure["tries"] == tries
        assert failure["modified"] == deps.wp.posts[0]["modified_gmt"]
        assert failure["last_error"] == "failed"
    deps.wp.posts.append(post(2, "2026-02-01T00:00:00"))
    planned, problems = freshness.sync_plans(deps, conn, TODAY, limit=1)
    assert planned == ["글 2"]
    assert problems == ["3번 실패해 건너뛴 글 1편(글 1번)"]
    assert deps.wp.calls == [1, 1, 1, 2]
    assert conn.execute("SELECT tries FROM failures WHERE post_id = 1").fetchone()[0] == 3
    deps.wp.posts[0]["modified_gmt"] = "2026-03-01T00:00:00"
    deps.wp.fail.clear()
    assert freshness.sync_plans(deps, conn, TODAY) == (["글 1"], [])
    assert deps.wp.calls == [1, 1, 1, 2, 1]
    assert conn.execute("SELECT count(*) FROM failures").fetchone()[0] == 0
    assert rows(conn)[1]["modified"] == "2026-03-01T00:00:00"


def test_sync_failure_count_restarts_when_modified_changes(env):
    deps, conn = env
    deps.wp.posts = [post(1)]
    deps.wp.fail.add(1)
    for _ in range(3):
        freshness.sync_plans(deps, conn, TODAY)
    deps.wp.posts[0]["modified_gmt"] = "2026-02-01T00:00:00"
    assert freshness.sync_plans(deps, conn, TODAY) == ([], ["글 1: RuntimeError"])
    failure = conn.execute("SELECT * FROM failures WHERE post_id = 1").fetchone()
    assert failure["tries"] == 1
    assert failure["modified"] == "2026-02-01T00:00:00"


def test_sync_removes_unpublished_failure_records(env):
    deps, conn = env
    deps.wp.posts = [post(1), post(2)]
    deps.wp.fail.add(1)
    freshness.sync_plans(deps, conn, TODAY)
    assert conn.execute("SELECT count(*) FROM failures").fetchone()[0] == 1
    assert set(rows(conn)) == {2}
    deps.wp.posts = []
    assert freshness.sync_plans(deps, conn, TODAY) == ([], [])
    assert conn.execute("SELECT count(*) FROM failures").fetchone()[0] == 0
    assert rows(conn) == {}


def test_notify_due_groups_records_and_repeats_after_seven_days(env):
    deps, conn = env
    deps.wp.posts = [post(1), post(2), post(3)]
    freshness.sync_plans(deps, conn, TODAY)
    conn.execute("UPDATE posts SET recheck_on = ? WHERE post_id = 1", ((TODAY - timedelta(days=1)).isoformat(),))
    conn.execute("UPDATE posts SET recheck_on = ? WHERE post_id = 2", (TODAY.isoformat(),))
    conn.execute("UPDATE posts SET recheck_on = ? WHERE post_id = 3", ((TODAY + timedelta(days=30)).isoformat(),))
    conn.commit()
    assert freshness.notify_due(deps, conn, TODAY) == 2
    assert len(deps.telegram.messages) == 1
    assert "글 1" in deps.telegram.messages[0] and "글 2" in deps.telegram.messages[0]
    assert "글 3" not in deps.telegram.messages[0]
    saved = rows(conn)
    assert saved[1]["last_notified"] == saved[2]["last_notified"] == TODAY.isoformat()
    assert saved[3]["last_notified"] is None
    assert freshness.notify_due(deps, conn, TODAY + timedelta(days=6)) == 0
    assert len(deps.telegram.messages) == 1
    assert freshness.notify_due(deps, conn, TODAY + timedelta(days=7)) == 2
    assert len(deps.telegram.messages) == 2
    assert rows(conn)[1]["last_notified"] == (TODAY + timedelta(days=7)).isoformat()


def test_notify_due_sends_only_ten_and_records_only_shown_posts(env):
    deps, conn = env
    assert freshness.MAX_NOTIFY == 10
    deps.wp.posts = [post(pid) for pid in range(12, 0, -1)]
    freshness.sync_plans(deps, conn, TODAY)
    conn.execute("UPDATE posts SET recheck_on = ?", (TODAY.isoformat(),))
    conn.commit()
    assert freshness.notify_due(deps, conn, TODAY) == 10
    assert len(deps.telegram.messages) == 1
    message = deps.telegram.messages[0]
    assert "다시 확인할 때가 된 글 12편 중 10편" in message
    assert "나머지 2편은 내일" in message
    for pid, saved in rows(conn).items():
        assert saved["last_notified"] == (TODAY.isoformat() if pid <= 10 else None)
        assert (f"(글 {pid}번," in message) == (pid <= 10)
    assert freshness.notify_due(deps, conn, TODAY + timedelta(days=1)) == 2
    assert len(deps.telegram.messages) == 2
    assert "(글 11번," in deps.telegram.messages[1]
    assert "(글 12번," in deps.telegram.messages[1]
    assert rows(conn)[11]["last_notified"] == "2026-06-02"
    assert rows(conn)[12]["last_notified"] == "2026-06-02"


def test_notify_due_send_failure_rolls_back_new_and_previous_notification_dates(env):
    deps, conn = env
    deps.wp.posts = [post(pid) for pid in range(1, 13)]
    freshness.sync_plans(deps, conn, TODAY)
    conn.execute("UPDATE posts SET recheck_on = ?", (TODAY.isoformat(),))
    conn.execute("UPDATE posts SET last_notified = ? WHERE post_id % 2 = 0",
                 ((TODAY - timedelta(days=7)).isoformat(),))
    conn.commit()
    before = {pid: dict(saved) for pid, saved in rows(conn).items()}
    deps.telegram.fail = True
    with pytest.raises(RuntimeError, match="telegram unavailable"):
        freshness.notify_due(deps, conn, TODAY)
    assert not conn.in_transaction
    assert {pid: dict(saved) for pid, saved in rows(conn).items()} == before
    assert len(freshness.due_posts(conn, TODAY)) == 12
    assert deps.telegram.messages == []
    deps.telegram.fail = False
    assert freshness.notify_due(deps, conn, TODAY) == 10
    assert len(deps.telegram.messages) == 1
    for pid, saved in rows(conn).items():
        assert saved["last_notified"] == (TODAY.isoformat() if pid % 2 or pid <= 8 else before[pid]["last_notified"])


def test_notify_due_respects_message_length_and_records_only_sent(env):
    deps, conn = env
    deps.wp.posts = [dict(post(pid), title="긴 제목" * 30) for pid in range(1, 11)]
    deps.generator.items = [freshness.FreshItem(what="항목", recheck_on="2026-06-02", why="긴 사유" * 50)
                            for _ in range(3)]
    freshness.sync_plans(deps, conn, TODAY)
    tomorrow = TODAY + timedelta(days=1)
    due = freshness.due_posts(conn, tomorrow)
    assert len(freshness.due_message(due, tomorrow)) > freshness.MAX_MESSAGE
    sent = freshness.notify_due(deps, conn, tomorrow)
    assert 1 <= sent < 10
    assert len(deps.telegram.messages[0]) <= freshness.MAX_MESSAGE
    assert len(freshness.due_message(due[:sent + 1], tomorrow, more=10 - sent - 1)) > freshness.MAX_MESSAGE
    for pid, saved in rows(conn).items():
        assert saved["last_notified"] == (tomorrow.isoformat() if pid <= sent else None)
        assert (f"(글 {pid}번," in deps.telegram.messages[0]) == (pid <= sent)


def test_due_posts_prioritizes_never_notified_then_oldest_notification(env):
    deps, conn = env
    deps.wp.posts = [post(pid) for pid in range(1, 5)]
    freshness.sync_plans(deps, conn, TODAY)
    conn.execute("UPDATE posts SET recheck_on = ?", (TODAY.isoformat(),))
    conn.execute("UPDATE posts SET last_notified = ? WHERE post_id = 1", ("2026-05-01",))
    conn.execute("UPDATE posts SET last_notified = ? WHERE post_id = 2", ("2026-05-20",))
    conn.execute("UPDATE posts SET recheck_on = ? WHERE post_id = 4", ("2026-05-30",))
    conn.commit()
    assert [r["post_id"] for r in freshness.due_posts(conn, TODAY)] == [4, 3, 1, 2]


def test_days_and_failures_replace_runs_table(env):
    _, conn = env
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"posts", "days", "failures"} <= tables
    assert "runs" not in tables
    assert [r["name"] for r in conn.execute("PRAGMA table_info(days)")] == ["day", "tries", "done", "planned", "note"]


def test_connect_adds_planned_to_legacy_days_preserving_data(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as legacy:
        legacy.execute("CREATE TABLE days (day TEXT PRIMARY KEY, tries INTEGER NOT NULL, "
                       "done INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '')")
        legacy.execute("INSERT INTO days VALUES ('2026-06-01', 2, 1, '기존 기록')")
    conn = freshness.connect(path)
    try:
        assert "planned" in [r["name"] for r in conn.execute("PRAGMA table_info(days)")]
        assert dict(conn.execute("SELECT * FROM days").fetchone()) == {
            "day": "2026-06-01", "tries": 2, "done": 1, "note": "기존 기록", "planned": 0}
    finally:
        conn.close()


def test_run_if_due_korean_time_and_once_daily(env):
    deps, conn = env
    before = datetime(2026, 6, 1, 0, 59, tzinfo=timezone.utc)
    assert freshness.run_if_due(deps, before) is None
    assert conn.execute("SELECT count(*) FROM days").fetchone()[0] == 0
    assert freshness.run_if_due(deps, before + timedelta(minutes=1)) == "날짜 정함 0편, 알림 0편"
    assert freshness.run_if_due(deps, before + timedelta(hours=5)) is None
    assert freshness.run_if_due(deps, before + timedelta(days=1, minutes=1)) == "날짜 정함 0편, 알림 0편"
    assert [dict(r) for r in conn.execute("SELECT * FROM days ORDER BY day")] == [
        {"day": "2026-06-01", "tries": 1, "done": 1, "planned": 0, "note": "날짜 정함 0편, 알림 0편"},
        {"day": "2026-06-02", "tries": 1, "done": 1, "planned": 0, "note": "날짜 정함 0편, 알림 0편"}]


def test_run_if_due_exception_counts_attempt_and_can_retry(env, monkeypatch):
    deps, conn = env
    now = datetime(2026, 6, 1, 1, tzinfo=timezone.utc)

    def fail():
        raise RuntimeError("unavailable")

    monkeypatch.setattr(deps.wp, "list_published_posts", fail)
    assert freshness.run_if_due(deps, now) is None
    assert dict(conn.execute("SELECT * FROM days").fetchone()) == {
        "day": "2026-06-01", "tries": 1, "done": 0, "planned": 0, "note": "실패: unavailable"}
    monkeypatch.setattr(deps.wp, "list_published_posts", lambda: [])
    assert freshness.run_if_due(deps, now) == "날짜 정함 0편, 알림 0편"
    assert dict(conn.execute("SELECT * FROM days").fetchone()) == {
        "day": "2026-06-01", "tries": 2, "done": 1, "planned": 0, "note": "날짜 정함 0편, 알림 0편"}
    assert freshness.run_if_due(deps, now + timedelta(minutes=10)) is None
    assert conn.execute("SELECT tries FROM days").fetchone()[0] == 2


def test_run_if_due_stops_after_three_failures_and_resets_next_korean_day(env, monkeypatch):
    deps, conn = env
    now = datetime(2026, 6, 1, 14, tzinfo=timezone.utc)  # 한국 시간 6월 1일 23시
    calls = []

    def fail():
        calls.append(True)
        raise RuntimeError("unavailable")

    assert freshness.MAX_TRIES_PER_DAY == 3
    monkeypatch.setattr(deps.wp, "list_published_posts", fail)
    for tries in range(1, 4):
        assert freshness.run_if_due(deps, now + timedelta(minutes=10 * tries)) is None
        assert dict(conn.execute("SELECT * FROM days").fetchone()) == {
            "day": "2026-06-01", "tries": tries, "done": 0, "planned": 0, "note": "실패: unavailable"}
        assert len(calls) == tries
    assert freshness.run_if_due(deps, now + timedelta(minutes=40)) is None
    assert len(calls) == 3
    assert conn.execute("SELECT tries FROM days").fetchone()[0] == 3
    monkeypatch.setattr(deps.wp, "list_published_posts", lambda: [])
    assert freshness.run_if_due(deps, now + timedelta(hours=11)) == "날짜 정함 0편, 알림 0편"
    assert [dict(r) for r in conn.execute("SELECT * FROM days ORDER BY day")] == [
        {"day": "2026-06-01", "tries": 3, "done": 0, "planned": 0, "note": "실패: unavailable"},
        {"day": "2026-06-02", "tries": 1, "done": 1, "planned": 0, "note": "날짜 정함 0편, 알림 0편"}]


def test_run_if_due_retries_notification_without_replanning(env):
    deps, conn = env
    deps.wp.posts = [post(1)]
    freshness.sync_plans(deps, conn, TODAY)
    conn.execute("UPDATE posts SET recheck_on = ?", (TODAY.isoformat(),))
    conn.commit()
    deps.wp.posts.append(post(2))
    deps.telegram.fail = True
    now = datetime(2026, 6, 1, 1, tzinfo=timezone.utc)
    assert freshness.run_if_due(deps, now) is None
    assert deps.wp.calls == [1, 2]
    assert set(rows(conn)) == {1, 2}  # 전송 전에 끝낸 날짜 판단은 보존된다.
    assert rows(conn)[1]["last_notified"] is None
    assert conn.execute("SELECT tries, done FROM days").fetchone()[:] == (1, 0)
    deps.telegram.fail = False
    assert freshness.run_if_due(deps, now + timedelta(minutes=10)) == "날짜 정함 0편, 알림 1편"
    assert deps.wp.calls == [1, 2]
    assert rows(conn)[1]["last_notified"] == TODAY.isoformat()
    assert conn.execute("SELECT tries, done FROM days").fetchone()[:] == (2, 1)


def test_notification_retry_keeps_daily_ai_call_limit(env):
    deps, conn = env
    deps.wp.posts = [post(1)]
    freshness.sync_plans(deps, conn, TODAY)
    conn.execute("UPDATE posts SET recheck_on = ?", (TODAY.isoformat(),))
    conn.commit()
    deps.generator.calls.clear()
    deps.wp.posts.extend(post(pid) for pid in range(2, freshness.MAX_PLANS_PER_RUN + 3))
    deps.telegram.fail = True
    now = datetime(2026, 6, 1, 1, tzinfo=timezone.utc)
    assert freshness.run_if_due(deps, now) is None
    assert len(deps.generator.calls) == freshness.MAX_PLANS_PER_RUN
    assert conn.execute("SELECT planned FROM days").fetchone()[0] == freshness.MAX_PLANS_PER_RUN
    deps.telegram.fail = False
    assert freshness.run_if_due(deps, now + timedelta(minutes=10)) is not None
    assert len(deps.generator.calls) == freshness.MAX_PLANS_PER_RUN
    assert conn.execute("SELECT planned FROM days").fetchone()[0] == freshness.MAX_PLANS_PER_RUN
    assert rows(conn)[1]["last_notified"] == TODAY.isoformat()
    assert len(rows(conn)) == freshness.MAX_PLANS_PER_RUN + 1


@pytest.mark.parametrize("entrypoint", ["run", "run_if_due"])
def test_file_lock_skips_run_and_releases_for_next_attempt(env, entrypoint):
    deps, conn = env
    deps.wp.posts = [post(1)]
    now = datetime(2026, 6, 1, 1, tzinfo=timezone.utc)
    execute = getattr(freshness, entrypoint)
    with freshness._exclusive(deps.data_dir) as acquired:
        assert acquired is True
        with freshness._exclusive(deps.data_dir) as second:
            assert second is False
        result = execute(deps, now)
        if entrypoint == "run":
            assert "진행 중" in result
        else:
            assert result is None
        assert deps.wp.calls == []
        assert deps.generator.calls == []
        assert deps.telegram.messages == []
        assert conn.execute("SELECT count(*) FROM days").fetchone()[0] == 0
        assert rows(conn) == {}
    assert execute(deps, now) == "날짜 정함 1편, 알림 0편"
    assert deps.wp.calls == [1]


def test_file_lock_is_released_after_exception(env, monkeypatch):
    deps, _ = env
    now = datetime(2026, 6, 1, 1, tzinfo=timezone.utc)

    def fail():
        raise RuntimeError("unavailable")

    monkeypatch.setattr(deps.wp, "list_published_posts", fail)
    with pytest.raises(RuntimeError, match="unavailable"):
        freshness.run(deps, now)
    monkeypatch.setattr(deps.wp, "list_published_posts", lambda: [])
    assert freshness.run(deps, now) == "날짜 정함 0편, 알림 0편"


@pytest.mark.parametrize(("slug", "expected"), [
    ("2026-year-end-tax-preview", "year-end-tax-preview"),
    ("2026", "2026"),
    ("covid19-support", "covid19-support"),
])
def test_slug_without_year(slug, expected):
    assert slug_without_year(slug) == expected


def test_checks_for_returns_empty_when_store_missing_post_missing_or_broken_json(tmp_path):
    assert freshness.checks_for(tmp_path, 80) == []
    assert not freshness.store_path(tmp_path).exists()

    conn = freshness.connect(freshness.store_path(tmp_path))
    try:
        assert freshness.checks_for(tmp_path, 80) == []
        conn.execute(
            "INSERT INTO posts (post_id, link, title, modified, recheck_on, items, model, planned_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (80, "https://jalcheck.com/?p=80", "제목", "2026-01-01", "2026-12-01", "{broken", "m", "2026-01-02"),
        )
        conn.commit()
    finally:
        conn.close()
    assert freshness.checks_for(tmp_path, 80) == []


def test_checks_for_formats_items_from_store(tmp_path):
    conn = freshness.connect(freshness.store_path(tmp_path))
    try:
        conn.execute(
            "INSERT INTO posts (post_id, link, title, modified, recheck_on, items, model, planned_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (80, "https://jalcheck.com/?p=80", "제목", "2026-01-01", "2026-12-01",
             json.dumps([
                 {"what": "2026년 지급액 월 10만 원", "recheck_on": "2027-01-05", "why": "2027년 고시 확인"},
                 {"what": "신청 마감 6월 30일", "recheck_on": "2026-07-01", "why": "마감 경과"},
             ], ensure_ascii=False),
             "m", "2026-01-02"),
        )
        conn.commit()
    finally:
        conn.close()
    assert freshness.checks_for(tmp_path, 80) == [
        "2026년 지급액 월 10만 원 (확인 이유: 2027년 고시 확인)",
        "신청 마감 6월 30일 (확인 이유: 마감 경과)",
    ]
