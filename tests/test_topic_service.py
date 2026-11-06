from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src import db, topic_store
from src.telegram_notifier import TelegramNotifier, TopicAction, TopicLine
from src.topic_finder import Candidate, FindResult, Seed, Written
from src.topic_service import TopicDeps, handle, register, request_more, run_batch
from tests.test_telegram import CHAT, FakeTG, callback

NOW = datetime(2026, 11, 9, 0, 30, tzinfo=timezone.utc)      # 한국 시간 월요일 09:30


def cand(name, kind="new", year=2026, topic=None, written=None):
    c = Candidate(Seed(name, name, (), "지원금"), kind, year, ["https://www.mohw.go.kr/a"], 120.0,
                  reasons=["신청·납부 시기"], written=written)
    c.topic = topic or f"{name} 신청 자격과 받는 방법"
    return c


class FakeTelegram:
    def __init__(self):
        self.sent, self.marked, self.texts = [], [], []

    def send_topic_candidates(self, header, lines, notes, batch_id, more_left):
        self.sent.append((header, list(lines), list(notes), batch_id, more_left))
        return 500 + len(self.sent)

    def mark_topic_registered(self, message_id, keyboard, cand_id, label):
        self.marked.append((message_id, cand_id, label))

    def send_text(self, text):
        self.texts.append(text)
        return 1


@pytest.fixture
def env(tmp_path):
    settings = SimpleNamespace(data_dir=tmp_path, db_path=tmp_path / "jobs.sqlite3")
    jobs = db.connect(settings.db_path)
    tg = FakeTelegram()
    found = {"cands": [cand("기초연금"), cand("부모급여"),
                       cand("에너지바우처", kind="update",
                            written=Written("에너지바우처", 2026, 6, 70, date(2026, 11, 1)))]}
    calls = []

    def finder(search, writer, seeds, written, today=None, skip_keys=()):
        calls.append({"written": written, "skip": set(skip_keys), "writer": writer})
        return FindResult(list(found["cands"]), ["메모"], 10)

    deps = TopicDeps(settings, search=None, generator=None, telegram=tg, finder=finder)
    return SimpleNamespace(settings=settings, jobs=jobs, tg=tg, deps=deps, calls=calls, found=found)


def store(env):
    return topic_store.connect(env.settings.data_dir / "topics.sqlite3")


def test_run_batch_saves_candidates_and_sends_one_message(env):
    db.create_job(env.jobs, "2026년 자녀장려금 신청 자격", source="cli")
    assert run_batch(env.deps, "weekly", now=NOW) == "후보 3개를 보냈습니다"
    header, lines, notes, batch_id, left = env.tg.sent[0]
    assert header == "이번 주 블로그 주제 후보" and left == 3
    assert [ln.topic for ln in lines][:2] == ["기초연금 신청 자격과 받는 방법", "부모급여 신청 자격과 받는 방법"]
    assert lines[2].kind == "update"
    assert notes[0] == "메모" and "글쓰기 대기 1편" in notes[-1]
    assert [w.name for w in env.calls[0]["written"]] == ["자녀장려금"]
    s = store(env)
    b = topic_store.get_batch(s, batch_id)
    assert b["status"] == "sent" and b["message_id"] == 501 and b["week"] == "2026-W46"


def test_weekly_runs_once_per_week_but_retries_after_failure(env):
    assert run_batch(env.deps, "weekly", now=NOW).startswith("후보")
    assert run_batch(env.deps, "weekly", now=NOW + timedelta(days=2)) == "이번 주 후보는 이미 보냈습니다"
    assert run_batch(env.deps, "weekly", now=NOW + timedelta(days=7)).startswith("후보")   # 다음 주
    # 보내기 전에 실패한 주는 다시 할 수 있음
    finder = env.deps.finder
    env.deps.finder = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("naver down"))
    later = NOW + timedelta(days=14)
    with pytest.raises(RuntimeError):
        run_batch(env.deps, "weekly", now=later)
    env.deps.finder = finder
    assert run_batch(env.deps, "weekly", now=later + timedelta(hours=1)).startswith("후보")
    # 전송 중 오류는 실제로 갔을 수 있어 같은 주에 다시 보내지 않음
    env.deps.telegram.send_topic_candidates = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("timeout"))
    week4 = NOW + timedelta(days=21)
    with pytest.raises(RuntimeError):
        run_batch(env.deps, "weekly", now=week4)
    assert run_batch(env.deps, "weekly", now=week4 + timedelta(hours=1)) == "이번 주 후보는 이미 보냈습니다"


def test_stale_running_batch_is_failed_so_week_can_retry(env):
    s = store(env)
    topic_store.start_batch(s, "weekly", now=NOW)                    # 중간에 꺼진 회차
    assert run_batch(env.deps, "weekly", now=NOW + timedelta(minutes=5)) == "이번 주 후보는 이미 보냈습니다"
    assert run_batch(env.deps, "weekly", now=NOW + timedelta(hours=1)).startswith("후보")


def test_unchosen_candidates_are_skipped_for_three_weeks(env):
    run_batch(env.deps, "manual", now=NOW)
    run_batch(env.deps, "manual", now=NOW + timedelta(days=1))
    assert env.calls[1]["skip"] == {"기초연금:2026:new", "부모급여:2026:new", "에너지바우처:2026:update"}
    run_batch(env.deps, "manual", now=NOW + timedelta(days=30))
    assert env.calls[2]["skip"] == set()


def action(kind, ref, keyboard=()):
    return TopicAction(kind, ref, "cb", 77, keyboard)


def test_register_creates_one_job_even_if_pressed_twice(env):
    run_batch(env.deps, "weekly", now=NOW)
    cid = env.tg.sent[0][1][0].cand_id
    assert register(env.deps, env.jobs, action("register", cid), now=NOW) == "작업 1번으로 등록했습니다"
    assert register(env.deps, env.jobs, action("register", cid), now=NOW) == "이미 등록된 후보입니다(작업 1번)"
    jobs = db.list_jobs(env.jobs)
    assert len(jobs) == 1 and jobs[0]["topic"] == "기초연금 신청 자격과 받는 방법"
    assert jobs[0]["source"] == "web" and jobs[0]["source_ref"] == f"topic:{cid}"
    assert env.tg.marked[0] == (77, cid, "등록됨(작업 1)")
    assert topic_store.get_candidate(store(env), cid)["job_id"] == 1
    # 고른 후보는 다음 추천에서 쉬지 않음(작업 장부로 걸러짐)
    run_batch(env.deps, "manual", now=NOW)
    assert "기초연금:2026:new" not in env.calls[-1]["skip"]


def test_register_rejects_expired_missing_and_update(env):
    run_batch(env.deps, "weekly", now=NOW)
    lines = env.tg.sent[0][1]
    assert register(env.deps, env.jobs, action("register", 999), now=NOW) == "없는 후보입니다"
    assert "등록 대상이 아닙니다" in register(env.deps, env.jobs, action("register", lines[2].cand_id), now=NOW)
    late = NOW + timedelta(days=8)
    assert "기한" in register(env.deps, env.jobs, action("register", lines[0].cand_id), now=late)
    assert db.list_jobs(env.jobs) == []


def test_request_more_limits_three_per_day_and_runs_in_background(env):
    run_batch(env.deps, "weekly", now=NOW)
    started = []
    for _ in range(3):
        note = request_more(env.deps, action("more", 1), now=NOW, start=lambda fn: (started.append(fn), fn()))
        assert note.startswith("새 후보를 찾고 있습니다")
    assert len(env.tg.sent) == 4 and env.tg.sent[1][0] == "새 블로그 주제 후보"
    assert [s[4] for s in env.tg.sent[1:]] == [2, 1, 0]               # 버튼에 남은 횟수
    assert "다 받았습니다" in request_more(env.deps, action("more", 1), now=NOW, start=lambda fn: fn())
    assert len(started) == 3
    assert request_more(env.deps, action("more", 999), now=NOW) == "없는 회차입니다"


def test_request_more_failure_is_reported(env):
    run_batch(env.deps, "weekly", now=NOW)

    def boom(*a, **k):
        raise RuntimeError("naver down")
    env.deps.finder = boom
    request_more(env.deps, action("more", 1), now=NOW, start=lambda fn: fn())
    assert "찾기 실패" in env.tg.texts[-1]


def test_handle_routes_unknown(env):
    assert handle(env.deps, env.jobs, action("weird", 1)) == "알 수 없는 요청입니다"


# ---- 텔레그램 메시지·버튼 ----

def make_tg(updates=None):
    fake = FakeTG(updates)
    return TelegramNotifier("123:SECRET", CHAT, session=fake), fake


def test_topic_message_has_numbered_buttons_without_update_and_more_button():
    tg, fake = make_tg()
    lines = [TopicLine(11, "new", "기초연금 <신청>", "지원금", 120.4, ["신청·납부 시기"], ["확인 필요"],
                       ["mohw.go.kr"]),
             TopicLine(12, "update", "에너지바우처 글 내용 확인", "지원금", None, news=["단가 인상"],
                       post_link="https://jalcheck.com/a/"),
             TopicLine(13, "new_year", "2027년 연말정산", "세금", 50)]
    mid = tg.send_topic_candidates("이번 주 블로그 주제 후보", lines, ["메모"], batch_id=3, more_left=2)
    body = fake.calls[0][1]
    assert mid == 11 and body["parse_mode"] == "HTML"
    assert "기초연금 &lt;신청&gt;" in body["text"] and "기존 글 고치기" in body["text"]
    assert "원래 글 보기" in body["text"] and "관심도 120" in body["text"]
    rows = body["reply_markup"]["inline_keyboard"]
    assert [b["text"] for b in rows[0]] == ["1번 등록", "3번 등록"]
    assert rows[-1][0]["text"] == "새 후보 더 받기 (오늘 2번 남음)"
    assert tg.parse_topic_callback(rows[0][0]["callback_data"]) == ("register", 11)
    assert tg.parse_topic_callback(rows[-1][0]["callback_data"]) == ("more", 3)
    assert all(len(b["callback_data"].encode()) <= 64 for r in rows for b in r)


def test_topic_callbacks_are_signed_and_from_our_chat_only():
    tg, _ = make_tg()
    good = tg.topic_callback("t", 11)
    other = TelegramNotifier("999:OTHER", CHAT, session=FakeTG()).topic_callback("t", 11)
    kb = [[{"text": "1번 등록", "callback_data": good}]]
    upd = callback(1, good, update_id=1)
    upd["callback_query"]["message"]["reply_markup"] = {"inline_keyboard": kb}
    updates = [upd, callback(2, other, update_id=2), callback(3, good, chat="666", update_id=3),
               callback(4, "t:11:0000000000000000", update_id=4),
               callback(5, tg.callback_data(7, "p", "aabbccdd"), update_id=5)]
    tg2, fake = make_tg(updates)
    got, nxt = tg2.poll_decisions(None)
    assert isinstance(got[0], TopicAction) and (got[0].action, got[0].ref_id) == ("register", 11)
    assert got[0].keyboard == (tuple(kb[0]),)
    assert got[1].action == "publish" and len(got) == 2 and nxt == 6
    assert sum(c[0] == "answerCallbackQuery" for c in fake.calls) == 3


def test_mark_topic_registered_changes_only_that_button():
    tg, fake = make_tg()
    a, b = tg.topic_callback("t", 11), tg.topic_callback("t", 12)
    kb = ((({"text": "1번 등록", "callback_data": a}), {"text": "2번 등록", "callback_data": b}),)
    tg.mark_topic_registered(77, kb, 12, "등록됨(작업 5)")
    rows = fake.calls[0][1]["reply_markup"]["inline_keyboard"]
    assert [x["text"] for x in rows[0]] == ["1번 등록", "등록됨(작업 5)"]
    assert rows[0][1]["callback_data"] == b                       # 다시 눌러도 '이미 등록' 안내


def test_orchestrator_routes_topic_buttons(env):
    from src.orchestrator import Orchestrator
    run_batch(env.deps, "weekly", now=NOW)
    cid = env.tg.sent[0][1][0].cand_id
    answers = []

    class TG(FakeTelegram):
        def poll_decisions(self, offset, wait_seconds=25):
            return [TopicAction("register", cid, "cb1", 77, ())], 5

        def answer(self, cb, text):
            answers.append(text)

    svc = SimpleNamespace(settings=env.settings, search=None, generator=None, telegram=TG())
    assert Orchestrator(env.jobs, svc).poll_once(None) == 5
    assert answers == ["[주제 등록] 작업 1번으로 등록했습니다"]
    assert db.list_jobs(env.jobs)[0]["topic"] == "기초연금 신청 자격과 받는 방법"


def test_long_message_drops_whole_candidates_not_html():
    tg, fake = make_tg()
    long = "가" * 75
    lines = [TopicLine(i, "new", f"{long}{i}", "지원금", 1.0, ["이유" * 70], ["경고" * 55, "경고2" * 40],
                       ["a.go.kr", "b.go.kr", "c.go.kr"], ["뉴스" * 34, "뉴스2" * 23],
                       "https://jalcheck.com/" + "x" * 50) for i in range(1, 11)]
    tg.send_topic_candidates("후보", lines, ["메모"], 1, 3)
    body = fake.calls[0][1]
    assert len(body["text"]) <= 3900 and "개는 생략" in body["text"]
    shown = body["text"].count("원래 글 보기")
    assert body["text"].count("<b>") == body["text"].count("</b>")
    buttons = [b for row in body["reply_markup"]["inline_keyboard"][:-1] for b in row]
    assert len(buttons) == shown < 10                               # 보이는 후보만 버튼


def test_stale_sending_counts_as_sent_even_without_candidates(env):
    s = store(env)
    bid = topic_store.start_batch(s, "weekly", now=NOW)
    topic_store.mark_sending(s, bid)                                  # 후보 0개 메시지 전송 중 꺼짐
    assert run_batch(env.deps, "weekly", now=NOW + timedelta(hours=1)) == "이번 주 후보는 이미 보냈습니다"
    assert topic_store.get_batch(s, bid)["status"] == "sent"
    assert env.tg.sent == []


def test_same_topic_from_another_message_is_not_registered_twice(env):
    run_batch(env.deps, "manual", now=NOW)
    first = env.tg.sent[0][1][0].cand_id
    run_batch(env.deps, "manual", now=NOW)     # 같은 주제가 다시 온 경우(예: 전송 결과 불명 뒤 재전송)
    second = env.tg.sent[1][1][0].cand_id
    assert first != second
    register(env.deps, env.jobs, action("register", first), now=NOW)
    assert register(env.deps, env.jobs, action("register", second), now=NOW) == "같은 주제를 이미 등록했습니다(작업 1번)"
    assert len(db.list_jobs(env.jobs)) == 1


def test_more_while_running_is_refused_and_lock_released_after(env):
    from src import topic_service
    run_batch(env.deps, "weekly", now=NOW)
    pending = []
    assert request_more(env.deps, action("more", 1), now=NOW, start=pending.append).startswith("새 후보")
    assert "찾는 중" in request_more(env.deps, action("more", 1), now=NOW, start=pending.append)
    assert len(pending) == 1 and topic_service._RUN_LOCK.locked()
    pending[0]()
    assert not topic_service._RUN_LOCK.locked()
    assert request_more(env.deps, action("more", 999), now=NOW) == "없는 회차입니다"
    assert not topic_service._RUN_LOCK.locked()


def test_same_topic_check_uses_jobs_even_if_candidate_record_missing(env):
    run_batch(env.deps, "manual", now=NOW)
    first = env.tg.sent[0][1][0].cand_id
    run_batch(env.deps, "manual", now=NOW)
    second = env.tg.sent[1][1][0].cand_id
    # 작업은 만들었는데 후보 장부 기록 전에 꺼진 상황
    db.create_job(env.jobs, "기초연금 신청 자격과 받는 방법", source="web", source_ref=f"topic:{first}")
    assert topic_store.get_candidate(store(env), first)["job_id"] is None
    assert register(env.deps, env.jobs, action("register", second), now=NOW) == "같은 주제를 이미 등록했습니다(작업 1번)"
    assert topic_store.get_candidate(store(env), first)["job_id"] == 1          # 빠진 기록 복구
    assert len(db.list_jobs(env.jobs)) == 1
    # 취소한 작업이면 다시 등록 가능
    db.set_status(env.jobs, 1, "canceled")
    assert register(env.deps, env.jobs, action("register", second), now=NOW) == "작업 2번으로 등록했습니다"


# ---- 주 1회 ----
from src.topic_service import run_weekly_if_due  # noqa: E402

MON_0830 = datetime(2026, 11, 8, 23, 30, tzinfo=timezone.utc)   # 한국 시간 월요일 08:30


def test_weekly_waits_for_monday_9am_kst_then_sends_once(env):
    assert run_weekly_if_due(env.deps, now=MON_0830) is None
    assert run_weekly_if_due(env.deps, now=MON_0830 + timedelta(minutes=40)) == "후보 3개를 보냈습니다"
    assert run_weekly_if_due(env.deps, now=MON_0830 + timedelta(hours=3)) is None
    assert run_weekly_if_due(env.deps, now=MON_0830 + timedelta(days=3)) is None
    assert len(env.tg.sent) == 1
    # 다음 주 월요일 9시 이후 다시
    assert run_weekly_if_due(env.deps, now=MON_0830 + timedelta(days=7, hours=1)).startswith("후보")


def test_weekly_catches_up_later_in_week_if_worker_was_down(env):
    thu = MON_0830 + timedelta(days=3)
    assert run_weekly_if_due(env.deps, now=thu).startswith("후보")


def test_weekly_failure_notifies_and_retries_next_day_only_once(env):
    good = env.deps.finder
    env.deps.finder = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("naver down"))
    t0 = MON_0830 + timedelta(hours=1)
    assert "내일 한 번 더" in run_weekly_if_due(env.deps, now=t0)
    assert "내일 한 번 더" in env.tg.texts[-1]
    assert run_weekly_if_due(env.deps, now=t0 + timedelta(hours=5)) is None        # 아직 하루 안 됨
    note = run_weekly_if_due(env.deps, now=t0 + timedelta(hours=21))
    assert "더 시도하지 않습니다" in note
    env.deps.finder = good
    assert run_weekly_if_due(env.deps, now=t0 + timedelta(days=3)) is None         # 이번 주는 끝
    assert run_weekly_if_due(env.deps, now=t0 + timedelta(days=7)).startswith("후보")


def test_weekly_skips_while_more_is_running_without_counting_failure(env):
    from src import topic_service
    topic_service._RUN_LOCK.acquire()
    try:
        assert run_weekly_if_due(env.deps, now=MON_0830 + timedelta(hours=1)) == "이미 주제를 찾는 중입니다"
    finally:
        topic_service._RUN_LOCK.release()
    assert run_weekly_if_due(env.deps, now=MON_0830 + timedelta(hours=1, minutes=10)).startswith("후보")


def test_reclick_fixes_button_if_first_edit_failed_but_skips_if_already_done():
    tg, fake = make_tg()
    a = tg.topic_callback("t", 11)
    tg.mark_topic_registered(77, (({"text": "등록됨(작업 5)", "callback_data": a},),), 11, "등록됨(작업 5)")
    assert fake.calls == []                                   # 이미 바뀜 → 안 보냄
    tg.mark_topic_registered(77, (({"text": "1번 등록", "callback_data": a},),), 11, "등록됨(작업 5)")
    assert fake.calls[0][0] == "editMessageReplyMarkup"


def test_register_again_still_calls_mark_so_failed_edit_can_recover(env):
    run_batch(env.deps, "weekly", now=NOW)
    cid = env.tg.sent[0][1][0].cand_id
    register(env.deps, env.jobs, action("register", cid), now=NOW)
    register(env.deps, env.jobs, action("register", cid), now=NOW)
    assert len(env.tg.marked) == 2                            # 건너뛸지는 텔레그램 쪽이 버튼을 보고 판단
