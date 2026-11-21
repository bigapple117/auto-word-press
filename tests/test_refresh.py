"""발행 글 갱신 회귀 검사. DB·HTTP·실행기 가짜 환경은 기존 테스트를 재사용한다."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from src import db, refresh, topic_service
from scripts import jobctl
from src.telegram_notifier import RefreshCommand
from src.wp_client import WPError, WPPost, WordPressClient, marker_comment
from tests.test_daily_guard import at, awaiting, guard  # noqa: F401
from tests.test_db import conn  # noqa: F401
from tests.test_orchestrator import decision, env  # noqa: F401
from tests.test_telegram import CHAT, callback, make as make_telegram
from tests.test_wp_client import FakeWP as FakeHTTPSession


NOW = datetime(2026, 11, 18, 0, 0, tzinfo=timezone.utc)
TARGET = {"post_id": 80, "link": "https://jalcheck.com/?p=80", "title": "2026년 근로장려금 신청"}


class RefreshHTTPSession(FakeHTTPSession):
    """기존 HTTP 가짜에 edit 문맥 필드와 휴지통 처리를 추가한다."""

    def request(self, method, url, **kwargs):
        response = super().request(method, url, **kwargs)
        path = url.split("/wp-json/wp/v2", 1)[1]
        if path.startswith("/posts/") and response.status_code < 400:
            post = self.posts[int(path.rsplit("/", 1)[1])]
            if method == "DELETE":
                post["status"] = "trash"
            response._data.update({
                "status": post["status"],
                "title": {"raw": post.get("title", "")},
                "excerpt": {"raw": post.get("excerpt", "")},
                "featured_media": post.get("featured_media", 0),
                "tags": list(post.get("tags", [])),
                "categories": list(post.get("categories", [])),
            })
        return response


@pytest.fixture
def refresh_wp(tmp_path):
    http = RefreshHTTPSession()
    http.posts[80] = {
        "status": "publish", "title": TARGET["title"], "content": marker_comment(1) + "\n옛 본문",
        "excerpt": "옛 요약", "slug": "original-2025", "date": "2025-01-01T08:00:00",
        "date_gmt": "2024-12-31T23:00:00", "featured_media": 11, "tags": [1], "categories": [2],
    }
    http.posts[180] = {
        "status": "draft", "title": "새 제목", "content": marker_comment(7) + "\n<p>새 본문</p>",
        "excerpt": "새 요약", "slug": "temporary", "date": "2026-11-18T09:00:00",
        "featured_media": 22, "tags": [3, 4], "categories": [5],
    }
    client = WordPressClient("https://jalcheck.com", "admin", "test password", session=http,
                             lock_dir=tmp_path / "locks")
    return client, http


@pytest.fixture
def frozen_now(monkeypatch):
    # request_refresh의 시계와 DB 저장 시각을 맞춰 실제 실행 날짜와 무관하게 검사한다.
    def freeze(value):
        monkeypatch.setattr(db, "_now", lambda: value.isoformat(timespec="seconds"))
        return value

    freeze(NOW)
    return freeze


def seed_refresh(conn, post_id=80, status="queued", created_at=NOW):
    job_id = db.create_job(conn, "갱신할 주제", mode="blog", source="cli",
                           source_ref=f"refresh:{post_id}:seed-{len(db.list_jobs(conn, limit=-1))}",
                           checkpoint={"target": {**TARGET, "post_id": post_id}})
    # 상태별 조회·집계용 자료. 파이프라인 전이 자체는 실행기 통합 검사에서 확인한다.
    conn.execute("UPDATE jobs SET status = ?, created_at = ? WHERE id = ?",
                 (status, created_at.isoformat(), job_id))
    return job_id


@pytest.mark.parametrize(("title", "topic"), [
    ("2026년 근로장려금 신청", "근로장려금 신청"),
    ("근로장려금 2025 신청과 2026년 변경", "근로장려금 신청과 변경"),
    ("  1999년 제도   신청  ", "제도 신청"),
    ("연 12026원 지원과 20260명 대상", "연 12026원 지원과 20260명 대상"),
    ("  연도 없는 주제  ", "연도 없는 주제"),
    ("2026년", "2026년"),
])
def test_topic_from_title_removes_years_only(title, topic):
    assert refresh.topic_from_title(title) == topic


@pytest.mark.parametrize("status", [None, "draft", "pending", "private", "future", "trash"])
def test_request_refresh_accepts_only_published_posts(conn, refresh_wp, status, frozen_now):
    client, http = refresh_wp
    if status is None:
        del http.posts[80]
    else:
        http.posts[80]["status"] = status
    job_id, note = refresh.request_refresh(conn, client, 80, now=NOW)
    assert job_id is None and "발행된 글이 아니라" in note
    assert db.list_jobs(conn) == []
    assert all(call[0] == "GET" for call in http.calls)


def test_request_refresh_creates_blog_job_with_target_in_same_insert(conn, refresh_wp, frozen_now, monkeypatch):
    client, http = refresh_wp
    create = db.create_job
    observed = []
    worker = db.connect(conn.execute("PRAGMA database_list").fetchone()[2])

    expected_target = {**TARGET, "checks": []}

    def create_and_claim(*args, **kwargs):
        assert kwargs["checkpoint"] == {"target": expected_target}
        job_id = create(*args, **kwargs)
        # create_job이 반환하자마자 다른 연결의 실행기가 가져가도 대상이 이미 있어야 한다.
        observed.append(db.claim_next(worker))
        return job_id

    monkeypatch.setattr(db, "create_job", create_and_claim)
    try:
        job_id, note = refresh.request_refresh(conn, client, 80, source="web", now=NOW)
    finally:
        worker.close()
    job = db.get_job(conn, job_id)
    assert observed[0]["checkpoint"] == job["checkpoint"] == {"target": expected_target}
    assert job["topic"] == "근로장려금 신청" and job["mode"] == "blog"
    assert job["source"] == "web" and job["source_ref"].startswith("refresh:80:")
    assert refresh.is_refresh(job) and refresh.target_of(job) == expected_target
    assert "등록했습니다" in note and "[발행]" in note
    assert all(call[0] == "GET" for call in http.calls)  # 승인 전 원문 변경 없음


def test_create_job_checkpoint_round_trip_defaults_and_duplicate_preserves_target(conn):
    checkpoint = {"target": TARGET, "content": {"warnings": ["확인"]}}
    first = db.create_job(conn, "갱신", source_ref="refresh:80:first", checkpoint=checkpoint)
    duplicate = db.create_job(conn, "다른 주제", source_ref="refresh:80:first",
                              checkpoint={"target": {"post_id": 99}})
    assert duplicate == first
    assert db.get_job(conn, first)["checkpoint"] == checkpoint
    assert db.claim_next(conn)["checkpoint"] == checkpoint
    plain = db.create_job(conn, "새 글")
    assert db.get_job(conn, plain)["checkpoint"] == {}
    assert not refresh.is_refresh(db.get_job(conn, plain))
    assert refresh.target_of(db.get_job(conn, plain)) is None


def test_request_refresh_file_database_creates_lock(tmp_path, refresh_wp, frozen_now):
    conn = db.connect(tmp_path / "jobs.db")
    try:
        job_id, _ = refresh.request_refresh(conn, refresh_wp[0], 80, now=NOW)
        assert db.get_job(conn, job_id)["checkpoint"]["target"] == {**TARGET, "checks": []}
        assert (tmp_path / "refresh.lock").is_file()
    finally:
        conn.close()


def test_rework_blocker_detects_applied_and_other_active_refresh(conn, refresh_wp):
    client, http = refresh_wp
    http.posts[80]["content"] = marker_comment(99) + "\n옛 본문"
    job_id = seed_refresh(conn, status="failed")
    job = db.get_job(conn, job_id)
    assert refresh.rework_blocker(conn, client, job) is None
    other = seed_refresh(conn, status="queued")
    assert f"갱신 작업 {other}번이 진행 중" in refresh.rework_blocker(conn, client, job)
    http.posts[80]["content"] += "\n" + marker_comment(job_id)
    assert f"작업 {job_id}번은 이미 글 80번에 반영" in refresh.rework_blocker(conn, client, job)


def test_rework_blocker_blocks_even_when_applied_original_is_private(conn, refresh_wp):
    client, http = refresh_wp
    job_id = seed_refresh(conn, status="failed")
    http.posts[80]["status"] = "private"
    http.posts[80]["content"] = marker_comment(job_id) + "\n비공개 전환된 원문"
    reason = refresh.rework_blocker(conn, client, db.get_job(conn, job_id))
    assert reason is not None and f"작업 {job_id}번은 이미 글 80번에 반영" in reason


@pytest.mark.parametrize("command", ["retry", "redo", "redraft", "cancel"])
def test_jobctl_rework_commands_hold_refresh_lock(conn, monkeypatch, command):
    events = []

    @contextmanager
    def fake_lock(lock_conn):
        assert lock_conn is conn
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    def fake_run(args, settings, run_conn):
        assert args.cmd == command
        assert run_conn is conn
        assert events == ["enter"]
        return 0

    monkeypatch.setattr(jobctl, "load_settings", lambda: SimpleNamespace(db_path="unused"))
    monkeypatch.setattr(db, "connect", lambda _: conn)
    monkeypatch.setattr(refresh, "lock", fake_lock)
    monkeypatch.setattr(jobctl, "_run", fake_run)
    monkeypatch.setattr("sys.argv", ["jobctl.py", command, "1"])
    assert jobctl.main() == 0
    assert events == ["enter", "exit"]


@pytest.mark.parametrize("command", ["retry", "redo", "redraft", "cancel"])
def test_jobctl_blocks_rework_of_applied_refresh(conn, refresh_wp, monkeypatch, capsys, command):
    client, http = refresh_wp
    job_id = seed_refresh(conn, status="failed")
    http.posts[80]["content"] += "\n" + marker_comment(job_id)
    settings = SimpleNamespace(db_path="unused")
    monkeypatch.setattr(jobctl, "load_settings", lambda: settings)
    monkeypatch.setattr(db, "connect", lambda _: conn)
    from_settings = Mock(return_value=client)
    monkeypatch.setattr(WordPressClient, "from_settings", from_settings)
    monkeypatch.setattr("sys.argv", ["jobctl.py", command, str(job_id)])
    before = db.get_job(conn, job_id)
    assert jobctl.main() == 1
    assert "이미 글 80번에 반영" in capsys.readouterr().out
    assert db.get_job(conn, job_id) == before
    from_settings.assert_called_once_with(settings)


@pytest.mark.parametrize("status", ["queued", "generating", "tts", "media", "rendering",
                                   "awaiting_approval", "publishing"])
def test_request_refresh_rejects_same_target_while_unfinished(conn, refresh_wp, frozen_now, status):
    existing = seed_refresh(conn, status=status)
    job_id, note = refresh.request_refresh(conn, refresh_wp[0], 80, source="web", now=NOW)
    assert job_id is None and f"갱신 작업 {existing}번이 진행 중" in note
    assert len(db.list_jobs(conn)) == 1


@pytest.mark.parametrize("status", ["published", "canceled", "failed"])
def test_request_refresh_allows_finished_target_again(conn, refresh_wp, frozen_now, status):
    seed_refresh(conn, status=status)
    job_id, _ = refresh.request_refresh(conn, refresh_wp[0], 80, now=NOW)
    assert job_id is not None and len(db.list_jobs(conn)) == 2


def test_daily_limit_counts_finished_requests_but_not_new_posts(conn, refresh_wp, frozen_now):
    client, http = refresh_wp
    for i in range(4):
        db.create_job(conn, f"일반 글 {i}")
    for post_id, status in [(81, "published"), (82, "canceled"), (83, "failed")]:
        http.posts[post_id] = dict(http.posts[80])
        job_id, _ = refresh.request_refresh(conn, client, post_id, now=NOW)
        assert job_id is not None
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
    job_id, note = refresh.request_refresh(conn, client, 80, now=NOW)
    assert job_id is None and "3건까지" in note
    assert len(db.list_jobs(conn)) == 7


def test_daily_limit_rolls_over_at_korean_midnight_not_utc(conn, refresh_wp, frozen_now):
    # UTC 11/17 15:00부터 한국 11/18. UTC 날짜가 달라도 같은 한국 날짜의 요청 3건이다.
    for post_id, stamp in [(81, "2026-11-17T15:00:00+00:00"),
                           (82, "2026-11-17T23:59:59+00:00"),
                           (83, "2026-11-18T00:00:00+00:00")]:
        seed_refresh(conn, post_id, "canceled", datetime.fromisoformat(stamp))
    before_midnight = frozen_now(datetime(2026, 11, 18, 14, 59, 59, tzinfo=timezone.utc))
    job_id, note = refresh.request_refresh(conn, refresh_wp[0], 80, now=before_midnight)
    assert job_id is None and "3건까지" in note
    midnight = frozen_now(before_midnight + timedelta(seconds=1))
    job_id, _ = refresh.request_refresh(conn, refresh_wp[0], 80, now=midnight)
    assert job_id is not None


def test_apply_refresh_copies_content_without_url_date_or_status_then_trashes_draft(refresh_wp):
    client, http = refresh_wp
    original = dict(http.posts[80])
    draft = dict(http.posts[180])
    post = client.apply_refresh(7, 180, 80)
    assert post == WPPost(80, "publish", TARGET["link"])
    changes = [c for c in http.calls if c[0] != "GET"]
    assert [(c[0], c[1]) for c in changes] == [("POST", "/posts/80"), ("DELETE", "/posts/180")]
    expected = {key: draft[key] for key in
                ("title", "content", "excerpt", "featured_media", "tags", "categories")}
    expected["content"] = marker_comment(1) + "\n" + draft["content"]
    assert changes[0][3] == expected
    assert not {"slug", "date", "date_gmt", "status"} & changes[0][3].keys()
    assert all(http.posts[80][key] == original[key] for key in ("slug", "date", "date_gmt", "status"))
    assert changes[1][2] is None and changes[1][3] is None  # force 없는 DELETE = 휴지통
    assert http.posts[180]["status"] == "trash"
    assert client.refresh_applied(7, 80) == post
    http.calls.clear()
    assert client.apply_refresh(7, 180, 80) == post
    assert all(c[0] == "GET" for c in http.calls)


@pytest.mark.parametrize("draft_state", ["draft", "trash", "missing"])
def test_apply_refresh_already_applied_never_posts_again(refresh_wp, draft_state):
    client, http = refresh_wp
    http.posts[80]["content"] = marker_comment(7) + "\n이미 반영"
    if draft_state == "missing":
        del http.posts[180]
    else:
        http.posts[180]["status"] = draft_state
    assert client.apply_refresh(7, 180, 80).id == 80
    mutations = [(c[0], c[1]) for c in http.calls if c[0] != "GET"]
    assert mutations == ([("DELETE", "/posts/180")] if draft_state == "draft" else [])


@pytest.mark.parametrize("failure_stage", ["POST", "DELETE"])
def test_apply_refresh_retry_after_lost_response_or_cleanup_failure(refresh_wp, monkeypatch, caplog, failure_stage):
    client, http = refresh_wp
    real_request = http.request
    failed = False

    def interrupted(method, url, **kwargs):
        nonlocal failed
        if method == failure_stage and not failed:
            failed = True
            if method == "POST":
                real_request(method, url, **kwargs)  # 원문 반영 후 응답만 유실
            raise requests.Timeout("시험: 응답 끊김")
        return real_request(method, url, **kwargs)

    monkeypatch.setattr(http, "request", interrupted)
    if failure_stage == "POST":
        with pytest.raises(WPError):
            client.apply_refresh(7, 180, 80)
    else:
        assert client.apply_refresh(7, 180, 80) == WPPost(80, "publish", TARGET["link"])
        assert "임시 글 180번 휴지통 이동 실패" in caplog.text
        assert http.posts[180]["status"] == "draft"
    assert client.refresh_applied(7, 80).id == 80
    assert client.apply_refresh(7, 180, 80).id == 80
    assert len([c for c in http.calls if c[0] == "POST"]) == 1
    assert http.posts[180]["status"] == "trash"


@pytest.mark.parametrize("status", [None, "draft", "pending", "private", "future", "trash"])
def test_apply_refresh_rejects_unpublished_target_with_409(refresh_wp, status):
    client, http = refresh_wp
    if status is None:
        del http.posts[80]
    else:
        http.posts[80]["status"] = status
    with pytest.raises(WPError) as error:
        client.apply_refresh(7, 180, 80)
    assert error.value.status == 409
    assert all(c[0] == "GET" for c in http.calls)


@pytest.mark.parametrize("draft_state", ["missing", "trash", "wrong_marker", "publish", "pending"])
def test_apply_refresh_rejects_unusable_draft_with_409(refresh_wp, draft_state):
    client, http = refresh_wp
    if draft_state == "missing":
        del http.posts[180]
    elif draft_state == "wrong_marker":
        http.posts[180]["content"] = marker_comment(70)
    else:
        http.posts[180]["status"] = draft_state
    with pytest.raises(WPError) as error:
        client.apply_refresh(7, 180, 80)
    assert error.value.status == 409
    assert all(c[0] == "GET" for c in http.calls)


@pytest.mark.parametrize(("status", "marker", "applied"), [
    ("publish", 7, True), ("publish", 70, False), ("draft", 7, False), ("trash", 7, False),
    (None, 7, False),
])
def test_refresh_applied_requires_published_target_and_exact_marker(refresh_wp, status, marker, applied):
    client, http = refresh_wp
    if status is None:
        del http.posts[80]
    else:
        http.posts[80].update(status=status, content=marker_comment(marker))
    assert client.refresh_applied(7, 80) == (WPPost(80, "publish", TARGET["link"]) if applied else None)
    assert all(c[0] == "GET" for c in http.calls)


@pytest.mark.parametrize(("status", "marker", "marked"), [
    ("publish", 7, True), ("private", 7, True), ("draft", 7, True), ("trash", 7, True),
    ("private", 70, False), (None, 7, False),
])
def test_refresh_marked_checks_marker_regardless_of_status_and_false_on_404(refresh_wp, status, marker, marked):
    client, http = refresh_wp
    if status is None:
        del http.posts[80]
    else:
        http.posts[80].update(status=status, content=marker_comment(marker))
    assert client.refresh_marked(7, 80) is marked
    assert all(c[0] == "GET" for c in http.calls)


def refresh_message(text="갱신 80", *, chat=CHAT, user=CHAT, message_id=77):
    return {"message_id": message_id, "chat": {"id": int(chat)}, "from": {"id": int(user)}, "text": text}


@pytest.mark.parametrize("text", ["갱신 80", "글 80번 갱신", "  갱신 글 80번  ", "80 갱신"])
def test_parse_refresh_message_recognizes_commands(text):
    tg, _ = make_telegram()
    assert tg.parse_refresh_message(refresh_message(text)) == RefreshCommand(80, 77)


@pytest.mark.parametrize("message", [
    refresh_message(chat="666"), refresh_message(user="666"), refresh_message(chat="666", user="666"),
    refresh_message("안녕하세요"), refresh_message("글 80번 갱신 부탁해"), refresh_message("갱신"),
    refresh_message("갱신 -80"), refresh_message(None), {},
])
def test_parse_refresh_message_ignores_other_users_chats_and_plain_text(message):
    tg, _ = make_telegram()
    assert tg.parse_refresh_message(message) is None


def test_poll_decisions_accepts_refresh_messages_and_keeps_buttons_and_offset():
    signer, _ = make_telegram()
    messages = [refresh_message(), refresh_message("글 81번 갱신"), refresh_message(user="666"),
                refresh_message(chat="666"), refresh_message("평범한 글자"), {}]
    updates = [{"update_id": i, "message": msg} for i, msg in enumerate(messages, 20)]
    updates.append(callback(1, signer.callback_data(9, "p", "aabbccdd"), update_id=26))
    tg, http = make_telegram(updates)
    got, offset = tg.poll_decisions(20, wait_seconds=0)
    assert got[:2] == [RefreshCommand(80, 77), RefreshCommand(81, 77)]
    assert len(got) == 3 and got[2].job_id == 9 and got[2].action == "publish"
    assert offset == 27
    params = http.calls[0][1]
    assert params["offset"] == 20 and set(params["allowed_updates"]) == {"message", "callback_query"}
    assert [c[0] for c in http.calls] == ["getUpdates"]  # 글자 명령에는 버튼 응답을 보내지 않음


def prepare_refresh(env):
    orch, svc, conn, _ = env
    # 원문 번호는 예전 발행 작업이 이미 소유한다. 갱신 작업에는 임시 글 번호가 남아야 한다.
    original = db.create_job(conn, "원래 글", mode="blog")
    db.set_status(conn, original, "generating")
    db.record_post(conn, original, 80, TARGET["link"])
    db.set_status(conn, original, "awaiting_approval")
    assert db.begin_publishing(conn, original)
    db.mark_published(conn, original, 80, TARGET["link"])
    job_id = seed_refresh(conn)
    assert orch.run_once() == job_id
    job = db.get_job(conn, job_id)
    assert job["status"] == "awaiting_approval"
    return job


def test_orchestrator_refresh_approval_and_publish_preserve_temporary_post_id(env, monkeypatch):
    orch, svc, conn, calls = env
    job = prepare_refresh(env)
    warnings = svc.telegram.requests[-1][2]
    assert warnings[0].startswith("[갱신]") and TARGET["link"] in warnings[0]
    assert "주소·작성일은 그대로" in warnings[0] and "휴지통" in warnings[0]
    assert "경고 하나" in warnings[1:]
    assert calls == {"render": 0, "tts": 0}
    apply = Mock(return_value=WPPost(80, "publish", TARGET["link"]))
    monkeypatch.setattr(svc.wp, "apply_refresh", apply, raising=False)
    publish = Mock(side_effect=AssertionError("갱신은 일반 발행 금지"))
    monkeypatch.setattr(svc.wp, "publish", publish)
    d = decision(job["id"], "publish", job["checkpoint"]["approval"]["nonce"], msg=99)
    assert orch.handle_decision(d) == "발행했습니다"
    apply.assert_called_once_with(job["id"], job["post_id"], 80)
    publish.assert_not_called()
    result = db.get_job(conn, job["id"])
    assert result["status"] == "published" and result["post_id"] == job["post_id"] != 80
    assert result["post_url"] == TARGET["link"] and result["checkpoint"]["target"] == TARGET
    assert svc.telegram.done[-1] == (job["checkpoint"]["approval"]["message_id"], f"[작업 {job['id']}] 글 80번을 갱신했습니다(주소 그대로).\n{TARGET['link']}")
    assert orch.handle_decision(d) == "이미 발행된 글입니다"
    assert apply.call_count == 1


def test_approval_warnings_puts_refresh_note_before_content_and_category_warnings(env):
    orch, svc, conn, _ = env
    job = prepare_refresh(env)
    cp = job["checkpoint"]
    cp["content"]["warnings"] = [f"경고 {i}" for i in range(10)]
    cp["draft"]["category_warning"] = "분류 확인"
    from src.content_schema import MasterContent
    content = MasterContent.model_validate(cp["content"]["content"])
    warnings = orch.approval_warnings(cp, content, job["post_id"])
    assert warnings[0].startswith("[갱신]")
    assert warnings[1:] == cp["content"]["warnings"] + ["분류 확인"]


@pytest.mark.parametrize("applied", [True, False])
def test_reconcile_refresh_checks_original_marker_and_retains_draft_id(env, monkeypatch, applied):
    orch, svc, conn, _ = env
    job = prepare_refresh(env)
    assert db.begin_publishing(conn, job["id"])
    conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", job["id"]))
    check = Mock(return_value=WPPost(80, "publish", TARGET["link"]) if applied else None)
    monkeypatch.setattr(svc.wp, "refresh_applied", check, raising=False)
    apply = Mock(return_value=WPPost(80, "publish", TARGET["link"]))
    monkeypatch.setattr(svc.wp, "apply_refresh", apply, raising=False)
    get = Mock(side_effect=AssertionError("갱신은 임시 글 발행 상태로 판단 금지"))
    monkeypatch.setattr(svc.wp, "get_post", get, raising=False)
    assert orch.reconcile_publishing() == [job["id"]]
    check.assert_called_once_with(job["id"], 80)
    if applied:
        apply.assert_called_once_with(job["id"], job["post_id"], 80)
    else:
        apply.assert_not_called()
    get.assert_not_called()
    result = db.get_job(conn, job["id"])
    assert result["status"] == ("published" if applied else "awaiting_approval")
    assert result["post_id"] == job["post_id"]
    if applied:
        assert result["post_url"] == TARGET["link"]


def test_poll_once_routes_refresh_command_to_handler(env, monkeypatch):
    orch, svc, _, _ = env
    command = RefreshCommand(80, 77)
    poll = Mock(return_value=([command], 42))
    handle = Mock(return_value="갱신 등록")
    monkeypatch.setattr(svc.telegram, "poll_decisions", poll, raising=False)
    monkeypatch.setattr(orch, "handle_refresh", handle)
    assert orch.poll_once(40, wait=0) == 42
    handle.assert_called_once_with(command)
    poll.assert_called_once_with(40, wait_seconds=0)


def test_handle_refresh_registers_web_job_and_reports_result(env, refresh_wp, frozen_now):
    orch, svc, conn, _ = env
    svc.wp = refresh_wp[0]
    note = orch.handle_refresh(RefreshCommand(80, 77))
    job = db.list_jobs(conn)[0]
    assert job["source"] == "web" and job["mode"] == "blog"
    assert job["checkpoint"]["target"] == {**TARGET, "checks": []}
    assert "등록했습니다" in note
    assert svc.telegram.texts == [note]


def test_handle_refresh_saves_freshness_checks_into_target(env, refresh_wp, frozen_now):
    import json
    from src import freshness
    orch, svc, conn, _ = env
    svc.wp = refresh_wp[0]
    fconn = freshness.connect(freshness.store_path(svc.settings.data_dir))
    try:
        fconn.execute(
            "INSERT INTO posts (post_id, link, title, modified, recheck_on, items, model, planned_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (80, TARGET["link"], TARGET["title"], "2026-01-01T00:00:00", "2026-11-01",
             json.dumps([{"what": "2026년 지급액 330만 원", "recheck_on": "2027-01-05", "why": "2027년 고시 확인"}],
                        ensure_ascii=False),
             "gemini", "2026-01-02T00:00:00"),
        )
        fconn.commit()
    finally:
        fconn.close()
    orch.handle_refresh(RefreshCommand(80, 77))
    job = db.list_jobs(conn)[0]
    assert job["checkpoint"]["target"]["checks"] == ["2026년 지급액 330만 원 (확인 이유: 2027년 고시 확인)"]


def test_refresh_process_passes_target_checks_to_make_content_and_generate(env, refresh_wp, frozen_now, monkeypatch):
    orch, svc, conn, _ = env
    checks = ["2026년 지급액 330만 원 (확인 이유: 2027년 고시 확인)"]
    job_id, _ = refresh.request_refresh(conn, refresh_wp[0], 80, now=NOW, checks=checks)
    seen = []
    orig_generate = svc.generator.generate

    def spy_generate(topic, sources, seconds, news=(), checks=()):
        seen.append(list(checks))
        return orig_generate(topic, sources, seconds, news=news, checks=checks)

    monkeypatch.setattr(svc.generator, "generate", spy_generate)
    assert orch.run_once() == job_id
    assert seen == [checks]


@pytest.mark.parametrize("with_new_post", [True, False])
def test_morning_auto_publish_excludes_refresh_jobs(guard, with_new_post):
    g, orch, svc, conn = guard
    job = prepare_refresh((orch, svc, conn, {}))
    new_id = awaiting(orch, conn, "새로 발행할 글")[0] if with_new_post else None
    note = g.run_if_due(at(8))
    assert db.get_job(conn, job["id"])["status"] == "awaiting_approval"
    assert svc.wp.published == ([(new_id, 500 + new_id)] if with_new_post else [])
    assert note == (f"자동 발행: 작업 {new_id}번" if with_new_post else "대기 글 없음")


@pytest.mark.parametrize("status", ["queued", "generating", "tts", "media", "rendering", "awaiting_approval",
                                   "publishing", "published", "canceled", "failed"])
def test_topic_pending_excludes_refresh_jobs_in_every_status(conn, status):
    seed_refresh(conn, status=status)
    plain = db.create_job(conn, "일반 작업", source_ref="topic:1")
    assert topic_service._pending(conn) == 1
    conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, plain))
    assert topic_service._pending(conn) == int(status in topic_service.PENDING)
