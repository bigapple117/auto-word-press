import json
from datetime import datetime, timedelta, timezone

import pytest

from src import db, source_watch
from src.source_fetcher import DEFAULT_MAX_CHARS, RelayUnavailableError, Source, SourceError


class FakeWP:
    def __init__(self):
        self.posts = {
            80: {"id": 80, "title": "청년적금", "link": "https://jalcheck.com/80"},
        }
        self.contents = {}
        self.calls = []
        self.list_calls = 0

    def list_published_posts(self):
        self.list_calls += 1
        return list(self.posts.values())

    def get_post_content(self, pid):
        self.calls.append(pid)
        return self.contents.get(pid, "<p>청년적금 소득 기준 200% 이하, 월 50만 원 한도</p>")


class FakeGenerator:
    def __init__(self, affects=True, summary="소득 기준 200%→180%"):
        self.affects = affects
        self.summary = summary
        self.calls = []
        self.fail = False
        self.raw = None
        self.verdicts = {}

    def structured(self, prompt, schema, system):
        self.calls.append((prompt, schema, system))
        if self.fail:
            raise RuntimeError("gemini unavailable")
        if self.raw is not None:
            return self.raw, "fake-model"
        for key, (affects, summary) in self.verdicts.items():
            if key in prompt:
                return json.dumps({"affects": affects, "summary": summary}, ensure_ascii=False), "fake-model"
        return json.dumps({"affects": self.affects, "summary": self.summary}, ensure_ascii=False), "fake-model"


class FakeTelegram:
    def __init__(self):
        self.messages = []
        self.fail = False

    def send_text(self, text):
        if self.fail:
            raise RuntimeError("telegram unavailable")
        self.messages.append(text)


class FakeFetcher:
    def __init__(self):
        self.pages = {}
        self.calls = []

    def __call__(self, url, sid="S1"):
        self.calls.append(url)
        val = self.pages.get(url, "기본 본문 내용입니다.")
        if isinstance(val, Exception):
            raise val
        return Source(id=sid, url=url, title="공식 페이지", text=val)


def publish_job(conn, topic, post_id, post_url, sources, *, title=None, source_ref=None, target=None):
    checkpoint = {"content": {"sources": sources}}
    if target is not None:
        checkpoint["target"] = target
    job_id = db.create_job(
        conn,
        topic=topic,
        mode="blog",
        source="cli",
        source_ref=source_ref,
        checkpoint=checkpoint,
    )
    if title is not None:
        db.update_fields(conn, job_id, title=title)
    db.set_status(conn, job_id, "generating")
    db.set_status(conn, job_id, "awaiting_approval")
    db.record_post(conn, job_id, post_id, post_url)
    assert db.begin_publishing(conn, job_id) is True
    db.mark_published(conn, job_id, post_id, post_url)
    return job_id


@pytest.fixture
def env(tmp_path):
    jobs_path = tmp_path / "jobs.sqlite3"
    db.connect(jobs_path).close()
    fetcher = FakeFetcher()
    deps = source_watch.Deps(
        wp=FakeWP(),
        generator=FakeGenerator(),
        telegram=FakeTelegram(),
        data_dir=tmp_path,
        jobs_conn_factory=lambda: db.connect(jobs_path),
        fetch=fetcher,
        pause=0,
    )
    conn = source_watch.connect(source_watch.store_path(tmp_path))
    yield deps, conn, fetcher, jobs_path
    conn.close()


def test_watched_pages_filters_direct_excludes_unpublished_wp_and_prefers_latest_refresh_target(tmp_path):
    jobs_conn = db.connect(tmp_path / "jobs.sqlite3")
    try:
        j1 = publish_job(
            jobs_conn,
            "청년적금",
            80,
            "https://jalcheck.com/80-job-url",
            [
                {"url": "https://a.go.kr/direct-old", "text": "옛 직접 본문", "via": "direct"},
                {"url": "https://a.go.kr/shared", "text": "80번 옛 본문", "via": "direct"},
                {"url": "https://a.go.kr/gemini", "text": "대신 읽은 본문", "via": "gemini"},
                {"url": "https://a.go.kr/law", "text": "법령 조문 본문", "via": "law"},
            ],
            title="작업에 적힌 옛 제목",
        )
        # 작업 DB에는 published로 남아 있으나 WP에서 내려간 글(99번)은 제외되어야 함
        publish_job(
            jobs_conn,
            "내려간 글",
            99,
            "https://jalcheck.com/99",
            [{"url": "https://a.go.kr/removed-post", "text": "내려간 글 본문", "via": "direct"}],
            title="내려간 글 제목",
        )
        # 미발행 작업은 제외되어야 함
        db.create_job(
            jobs_conn,
            topic="대기 작업",
            mode="blog",
            source="cli",
            checkpoint={"content": {"sources": [{"url": "https://a.go.kr/queued", "text": "대기", "via": "direct"}]}},
        )

        published = {
            80: {"id": 80, "title": "WP 현재 제목", "link": "https://jalcheck.com/80-wp"},
        }
        first = source_watch.watched_pages(jobs_conn, published)
        assert set(first) == {
            ("https://a.go.kr/direct-old", 80),
            ("https://a.go.kr/shared", 80),
        }
        assert first[("https://a.go.kr/direct-old", 80)] == {
            "title": "WP 현재 제목",
            "link": "https://jalcheck.com/80-wp",
            "job_id": j1,
            "text": "옛 직접 본문",
        }

        # 같은 글(80번)의 갱신 작업: 임시 글 번호는 180이지만 target.post_id=80으로 매핑되고 제목/주소는 published 값을 씀
        j2 = publish_job(
            jobs_conn,
            "청년적금 갱신",
            180,
            "https://jalcheck.com/?p=180",
            [
                {"url": "https://a.go.kr/direct-new", "text": "새 직접 본문", "via": "direct"},
                {"url": "https://a.go.kr/gemini-2", "text": "제미나이 본문", "via": "gemini"},
                {"url": "https://a.go.kr/law-2", "text": "법령 본문", "via": "law"},
            ],
            title="작업 새 제목",
            source_ref="refresh:80:20261119000000",
            target={"post_id": 80, "link": "https://jalcheck.com/youth-saving", "title": "청년적금"},
        )

        published[80] = {"id": 80, "title": "청년적금 새 제목", "link": "https://jalcheck.com/youth-saving"}
        second = source_watch.watched_pages(jobs_conn, published)
        assert set(second) == {("https://a.go.kr/direct-new", 80)}
        assert second[("https://a.go.kr/direct-new", 80)] == {
            "title": "청년적금 새 제목",
            "link": "https://jalcheck.com/youth-saving",
            "job_id": j2,
            "text": "새 직접 본문",
        }

        # 이후 WP에서 80번 글도 내려가면 감시 대상에서 빠짐
        assert source_watch.watched_pages(jobs_conn, {}) == {}
    finally:
        jobs_conn.close()


def test_sync_pages_adds_updates_baseline_on_job_change_and_deletes_missing(env):
    _, conn, _, _ = env
    initial = {
        ("https://a.go.kr/1", 80): {"title": "글 80", "link": "https://jalcheck.com/80", "job_id": 1, "text": "기준 1"},
        ("https://a.go.kr/2", 81): {"title": "글 81", "link": "https://jalcheck.com/81", "job_id": 2, "text": "기준 2"},
    }
    assert source_watch.sync_pages(conn, initial) == (2, 0)

    conn.execute("UPDATE pages SET fails = 3, last_error = '오류', dead_noted = 1 WHERE url = 'https://a.go.kr/1' AND post_id = 80")
    conn.commit()

    # 같은 job_id면 baseline·fails 유지(제목/링크만 갱신), job_id가 바뀌면 baseline 교체 및 fails·dead_noted 초기화, 빠진 항목 삭제
    updated = {
        ("https://a.go.kr/1", 80): {"title": "글 80 갱신", "link": "https://jalcheck.com/80-new", "job_id": 5, "text": "새 기준 1"},
        ("https://a.go.kr/3", 82): {"title": "글 82", "link": "https://jalcheck.com/82", "job_id": 6, "text": "기준 3"},
    }
    assert source_watch.sync_pages(conn, updated) == (2, 1)

    rows = {(r["url"], r["post_id"]): dict(r) for r in conn.execute("SELECT * FROM pages")}
    assert set(rows) == {("https://a.go.kr/1", 80), ("https://a.go.kr/3", 82)}
    assert rows[("https://a.go.kr/1", 80)]["job_id"] == 5
    assert rows[("https://a.go.kr/1", 80)]["baseline"] == "새 기준 1"
    assert rows[("https://a.go.kr/1", 80)]["title"] == "글 80 갱신"
    assert rows[("https://a.go.kr/1", 80)]["link"] == "https://jalcheck.com/80-new"
    assert rows[("https://a.go.kr/1", 80)]["fails"] == 0
    assert rows[("https://a.go.kr/1", 80)]["last_error"] == ""
    assert rows[("https://a.go.kr/1", 80)]["dead_noted"] == 0
    assert rows[("https://a.go.kr/3", 82)]["baseline"] == "기준 3"


def test_shared_url_across_two_posts_fetches_once_judges_per_post_and_keeps_other_baseline_on_single_refresh(env):
    deps, conn, fetcher, _ = env
    shared_url = "https://a.go.kr/shared"
    deps.wp.contents[80] = "<p>80번 청년적금 글 본문</p>"
    deps.wp.contents[81] = "<p>81번 청년도약계좌 글 본문</p>"

    # 두 글이 같은 주소를 공유
    pages = {
        (shared_url, 80): {"title": "글 80", "link": "https://jalcheck.com/80", "job_id": 1, "text": "공통 초기 기준 본문입니다."},
        (shared_url, 81): {"title": "글 81", "link": "https://jalcheck.com/81", "job_id": 2, "text": "공통 초기 기준 본문입니다."},
    }
    source_watch.sync_pages(conn, pages)

    # 1) 페이지 내용이 바뀌었을 때 fetch는 1번만 하고 글별로 각각 판정(80번은 영향 있음, 81번은 영향 없음)
    fetcher.pages[shared_url] = "공통 1차 변경 본문입니다."
    deps.generator.verdicts = {
        "[블로그 글 제목] 글 80": (True, "80번 관련 기준 변경"),
        "[블로그 글 제목] 글 81": (False, "81번과는 무관한 변경"),
    }
    assert source_watch.check_pages(deps, conn, limit=5) == (1, 1, 2, 1)
    assert fetcher.calls == [shared_url]
    assert deps.wp.calls == [80, 81]
    assert len(deps.generator.calls) == 2

    changes = {r["post_id"]: dict(r) for r in conn.execute("SELECT * FROM changes")}
    assert changes[80]["affects"] == 1
    assert changes[81]["affects"] == 0

    # 2) 80번 글만 갱신(job_id 1 -> 3, 새 기준 본문)되어도 81번 글의 기준("공통 1차 변경 본문입니다.")은 그대로 유지
    pages[(shared_url, 80)] = {"title": "글 80 갱신", "link": "https://jalcheck.com/80", "job_id": 3, "text": "80번 갱신 시점 본문입니다."}
    assert source_watch.sync_pages(conn, pages) == (1, 0)

    b80 = conn.execute("SELECT job_id, baseline FROM pages WHERE url = ? AND post_id = 80", (shared_url,)).fetchone()
    b81 = conn.execute("SELECT job_id, baseline FROM pages WHERE url = ? AND post_id = 81", (shared_url,)).fetchone()
    assert (b80["job_id"], b80["baseline"]) == (3, "80번 갱신 시점 본문입니다.")
    assert (b81["job_id"], b81["baseline"]) == (2, "공통 1차 변경 본문입니다.")

    # 다시 점검할 때 페이지가 "80번 갱신 시점 본문입니다."이면 fetch는 1번, 80번은 변화 없음, 81번만 변화로 판정
    fetcher.calls.clear()
    deps.generator.calls.clear()
    fetcher.pages[shared_url] = "80번 갱신 시점 본문입니다."
    assert source_watch.check_pages(deps, conn, limit=5) == (1, 1, 1, 0)
    assert fetcher.calls == [shared_url]
    assert len(deps.generator.calls) == 1
    assert "[블로그 글 제목] 글 81" in deps.generator.calls[0][0]


def test_diff_lines_ignores_noise_dates_short_lines_order_and_capped_tail():
    old = "\n".join([
        "안내",
        "2026-05-01 12:00",
        "조회수 1,234",
        "최종 수정일 2026.05.01",
        "지원 대상은 만 19세 이상 34세 이하 청년입니다.",
        "월 납입 한도는 50만 원입니다.",
    ])
    new = "\n".join([
        "123",
        "2026년 11월 19일 오후 2:00",
        "조회수 9,999",
        "담당부서 청년정책과",
        "월 납입 한도는 50만 원입니다.",
        "지원 대상은 만 19세 이상 34세 이하 청년입니다.",
    ])
    assert source_watch.diff_lines(old, new) == ([], [])

    # 긴 본문 문장 속 '로그인 후 조회'(및 40자 초과 줄)는 노이즈로 버리지 않고 비교에 남아야 함
    # '~'가 포함된 신청기간 줄도 날짜 전용 줄로 버리지 않고 변화를 잡아야 함
    old_body = "\n".join([
        "홈택스에 로그인 후 조회하면 본인의 청년적금 소득 기준 충족 여부를 바로 확인할 수 있습니다.",
        "신청기간 2026. 5. 1. ~ 5. 31.",
        "2026. 5. 1. ~ 5. 31.",
    ])
    new_body = "\n".join([
        "복지로에 로그인 후 조회하면 본인의 청년적금 소득 기준 충족 여부를 바로 확인할 수 있습니다.",
        "신청기간 2026. 5. 1. ~ 6. 30.",
        "2026. 5. 1. ~ 6. 30.",
    ])
    assert source_watch.diff_lines(old_body, new_body) == (
        [
            "홈택스에 로그인 후 조회하면 본인의 청년적금 소득 기준 충족 여부를 바로 확인할 수 있습니다.",
            "신청기간 2026. 5. 1. ~ 5. 31.",
            "2026. 5. 1. ~ 5. 31.",
        ],
        [
            "복지로에 로그인 후 조회하면 본인의 청년적금 소득 기준 충족 여부를 바로 확인할 수 있습니다.",
            "신청기간 2026. 5. 1. ~ 6. 30.",
            "2026. 5. 1. ~ 6. 30.",
        ],
    )

    # cap 근처 길이(len >= cap - 50)일 때 끝 20% 줄 차이는 무시하고 앞부분 변화만 감지
    common = [f"공통 본문 핵심 안내 문장 번호 {i:02d}번입니다." for i in range(8)]
    old_capped = "\n".join(common + ["끝부분 잘릴 옛 문장 첫째입니다.", "끝부분 잘릴 옛 문장 둘째입니다."])
    new_capped = "\n".join(common + ["끝부분 잘릴 새 문장 첫째입니다.", "끝부분 잘릴 새 문장 둘째입니다."])
    cap = min(len(old_capped), len(new_capped)) + 20
    assert len(old_capped) >= cap - 50 and len(new_capped) >= cap - 50
    assert source_watch.diff_lines(old_capped, new_capped, cap=cap) == ([], [])

    # 앞 80% 본문이 실제로 바뀌면 감지
    changed_head = "\n".join(["바뀐 본문 첫째 핵심 안내 문장입니다."] + common[1:] + ["끝부분 잘릴 새 문장 첫째입니다.", "끝부분 잘릴 새 문장 둘째입니다."])
    assert source_watch.diff_lines(old_capped, changed_head, cap=cap) == (
        [common[0]],
        ["바뀐 본문 첫째 핵심 안내 문장입니다."],
    )
    assert DEFAULT_MAX_CHARS >= cap


def test_check_pages_unchanged_changed_errors_and_oldest_first_limit(env):
    deps, conn, fetcher, _ = env
    base_text = "지원 금액은 월 50만 원입니다.\n신청 기간은 5월 1일부터입니다."
    source_watch.sync_pages(conn, {
        ("https://a.go.kr/u1", 80): {"title": "글 80", "link": "https://jalcheck.com/80", "job_id": 1, "text": base_text},
        ("https://a.go.kr/u2", 81): {"title": "글 81", "link": "https://jalcheck.com/81", "job_id": 2, "text": base_text},
        ("https://a.go.kr/u3", 82): {"title": "글 82", "link": "https://jalcheck.com/82", "job_id": 3, "text": base_text},
    })
    conn.execute("UPDATE pages SET last_checked = '2026-06-02T00:00:00+00:00' WHERE url = 'https://a.go.kr/u1'")
    conn.execute("UPDATE pages SET last_checked = '2026-06-01T00:00:00+00:00' WHERE url = 'https://a.go.kr/u2'")
    conn.commit()

    # 1) limit=2 & 오래 안 본 순서: u3(NULL) -> u2(2026-06-01) 순서로만 확인, 변화 없음 -> AI 호출 안 함
    fetcher.pages["https://a.go.kr/u3"] = base_text + "\n조회수 500"
    fetcher.pages["https://a.go.kr/u2"] = base_text
    assert source_watch.check_pages(deps, conn, limit=2) == (2, 2, 0, 0)
    assert fetcher.calls == ["https://a.go.kr/u3", "https://a.go.kr/u2"]
    assert deps.generator.calls == []
    assert deps.wp.calls == []
    u3 = conn.execute("SELECT * FROM pages WHERE url = 'https://a.go.kr/u3'").fetchone()
    assert u3["last_checked"] is not None

    # 2) 변화 있음 -> AI 판정, changes 기록, baseline 갱신
    fetcher.calls.clear()
    new_text = "지원 금액은 월 60만 원입니다.\n신청 기간은 5월 1일부터입니다."
    fetcher.pages["https://a.go.kr/u1"] = new_text
    deps.generator.affects = True
    deps.generator.summary = "지원 금액 월 50만→60만 원"
    assert source_watch.check_pages(deps, conn, limit=1) == (1, 1, 1, 1)
    assert fetcher.calls == ["https://a.go.kr/u1"]
    assert deps.wp.calls == [80]
    assert len(deps.generator.calls) == 1
    u1 = conn.execute("SELECT * FROM pages WHERE url = 'https://a.go.kr/u1'").fetchone()
    assert u1["baseline"] == new_text
    change = conn.execute("SELECT * FROM changes WHERE url = 'https://a.go.kr/u1'").fetchone()
    assert change["affects"] == 1
    assert change["summary"] == "지원 금액 월 50만→60만 원"
    assert json.loads(change["removed"]) == ["지원 금액은 월 50만 원입니다."]
    assert json.loads(change["added"]) == ["지원 금액은 월 60만 원입니다."]

    # 3) SourceError -> tried=1, checked=0, fails 증가, baseline 유지
    conn.execute("UPDATE pages SET last_checked = NULL WHERE url = 'https://a.go.kr/u1'")
    conn.commit()
    fetcher.pages["https://a.go.kr/u1"] = SourceError("연결 시간 초과")
    assert source_watch.check_pages(deps, conn, limit=1) == (1, 0, 0, 0)
    u1 = conn.execute("SELECT * FROM pages WHERE url = 'https://a.go.kr/u1'").fetchone()
    assert u1["fails"] == 1
    assert u1["baseline"] == new_text
    assert "연결 시간 초과" in u1["last_error"]

    # 4) 판정 실패(generator 예외) -> baseline 유지, changes 추가 없음
    conn.execute("UPDATE pages SET last_checked = NULL WHERE url = 'https://a.go.kr/u1'")
    conn.commit()
    fetcher.pages["https://a.go.kr/u1"] = "지원 금액은 월 70만 원입니다.\n신청 기간은 5월 1일부터입니다."
    deps.generator.fail = True
    assert source_watch.check_pages(deps, conn, limit=1) == (1, 1, 1, 0)
    u1 = conn.execute("SELECT * FROM pages WHERE url = 'https://a.go.kr/u1'").fetchone()
    assert u1["baseline"] == new_text
    assert "판정 실패" in u1["last_error"]
    assert conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0] == 1

    # 5) RelayUnavailableError -> 예외 전파, fails 증가 안 함
    deps.generator.fail = False
    conn.execute("UPDATE pages SET last_checked = NULL, fails = 1 WHERE url = 'https://a.go.kr/u1'")
    conn.commit()
    fetcher.pages["https://a.go.kr/u1"] = RelayUnavailableError("중계 한도 초과")
    with pytest.raises(RelayUnavailableError):
        source_watch.check_pages(deps, conn, limit=1)
    u1 = conn.execute("SELECT * FROM pages WHERE url = 'https://a.go.kr/u1'").fetchone()
    assert u1["fails"] == 1
    assert u1["baseline"] == new_text


def test_notify_sends_only_affecting_once_and_handles_dead_pages_recovery(env):
    deps, conn, fetcher, _ = env
    base_text = "지원 대상은 무주택 세대주입니다."
    source_watch.sync_pages(conn, {
        ("https://a.go.kr/affect", 80): {"title": "글 80", "link": "https://jalcheck.com/80", "job_id": 1, "text": base_text},
        ("https://a.go.kr/minor", 81): {"title": "글 81", "link": "https://jalcheck.com/81", "job_id": 2, "text": base_text},
        ("https://a.go.kr/dead", 82): {"title": "글 82", "link": "https://jalcheck.com/82", "job_id": 3, "text": base_text},
    })

    # 영향 있음 1건 + 영향 없음 1건 기록
    conn.execute(
        "INSERT INTO changes (url, post_id, job_id, found_at, affects, summary, removed, added, model) "
        "VALUES ('https://a.go.kr/affect', 80, 1, '2026-06-01T00:00:00+00:00', 1, '소득 기준 변경', '[]', '[]', 'm'), "
        "       ('https://a.go.kr/minor', 81, 2, '2026-06-01T00:00:00+00:00', 0, '문구 정리', '[]', '[]', 'm')"
    )
    conn.commit()

    # send_text 예외 시 notified는 NULL로 남아야 함
    deps.telegram.fail = True
    with pytest.raises(RuntimeError):
        source_watch.notify(deps, conn, "2026-06-01")
    assert conn.execute("SELECT notified FROM changes WHERE url = 'https://a.go.kr/affect'").fetchone()[0] is None

    deps.telegram.fail = False
    assert source_watch.notify(deps, conn, "2026-06-01") == 1
    assert len(deps.telegram.messages) == 1
    assert "글 80번" in deps.telegram.messages[0]
    assert "소득 기준 변경" in deps.telegram.messages[0]
    assert "문구 정리" not in deps.telegram.messages[0]
    assert conn.execute("SELECT notified FROM changes WHERE url = 'https://a.go.kr/affect'").fetchone()[0] == "2026-06-01"
    assert conn.execute("SELECT notified FROM changes WHERE url = 'https://a.go.kr/minor'").fetchone()[0] is None

    # 같은 변화는 두 번 보내지 않음
    assert source_watch.notify(deps, conn, "2026-06-01") == 0
    assert len(deps.telegram.messages) == 1

    # fails >= MAX_FAILS 페이지는 한 번만 알림
    fetcher.pages["https://a.go.kr/dead"] = SourceError("404 not found")
    for _ in range(source_watch.MAX_FAILS):
        conn.execute("UPDATE pages SET last_checked = NULL WHERE url = 'https://a.go.kr/dead'")
        conn.execute("UPDATE pages SET last_checked = '2099-01-01' WHERE url != 'https://a.go.kr/dead'")
        conn.commit()
        source_watch.check_pages(deps, conn, limit=1)

    assert source_watch.notify(deps, conn, "2026-06-02") == 1
    assert len(deps.telegram.messages) == 2
    assert "https://a.go.kr/dead" in deps.telegram.messages[1]
    assert conn.execute("SELECT dead_noted FROM pages WHERE url = 'https://a.go.kr/dead'").fetchone()[0] == 1

    # 다시 실패해도 dead_noted=1이면 중복 알림 안 함
    conn.execute("UPDATE pages SET last_checked = NULL WHERE url = 'https://a.go.kr/dead'")
    conn.commit()
    source_watch.check_pages(deps, conn, limit=1)
    assert source_watch.notify(deps, conn, "2026-06-03") == 0
    assert len(deps.telegram.messages) == 2

    # 이후 성공하면 dead_noted=0 및 fails=0으로 복구
    fetcher.pages["https://a.go.kr/dead"] = base_text
    conn.execute("UPDATE pages SET last_checked = NULL WHERE url = 'https://a.go.kr/dead'")
    conn.commit()
    source_watch.check_pages(deps, conn, limit=1)
    dead_row = conn.execute("SELECT fails, dead_noted FROM pages WHERE url = 'https://a.go.kr/dead'").fetchone()
    assert (dead_row["fails"], dead_row["dead_noted"]) == (0, 0)


def test_notify_respects_max_message_leaving_unsent_changes_notified_null(env):
    deps, conn, _, _ = env
    pages = {
        (f"https://a.go.kr/page-{i:02d}", 80 + i): {
            "title": f"청년 정책 안내 글 제목 {i:02d}번입니다",
            "link": f"https://jalcheck.com/post-{80 + i}",
            "job_id": i + 1,
            "text": "기준 본문",
        }
        for i in range(30)
    }
    source_watch.sync_pages(conn, pages)
    for i in range(30):
        conn.execute(
            "INSERT INTO changes (url, post_id, job_id, found_at, affects, summary, removed, added, model) "
            "VALUES (?, ?, ?, '2026-06-01T00:00:00+00:00', 1, ?, '[]', '[]', 'm')",
            (
                f"https://a.go.kr/page-{i:02d}",
                80 + i,
                i + 1,
                f"지원 기준 및 신청 자격 세부 항목 변경 요약 설명 문장 {i:02d}번입니다 (" + ("가" * 40) + ")",
            ),
        )
    conn.commit()

    sent_first = source_watch.notify(deps, conn, "2026-06-01")
    assert 0 < sent_first < 30
    assert len(deps.telegram.messages) == 1
    assert len(deps.telegram.messages[0]) <= source_watch.MAX_MESSAGE
    assert f"(나머지 {30 - sent_first}건은 다음 알림에)" in deps.telegram.messages[0]

    notified_count = conn.execute("SELECT COUNT(*) FROM changes WHERE notified = '2026-06-01'").fetchone()[0]
    remaining_null = conn.execute("SELECT COUNT(*) FROM changes WHERE notified IS NULL").fetchone()[0]
    assert notified_count == sent_first
    assert remaining_null == 30 - sent_first

    # 다음 알림에서 나머지 항목이 발송되고 notified가 채워짐
    sent_second = source_watch.notify(deps, conn, "2026-06-02")
    assert sent_second == 30 - sent_first
    assert conn.execute("SELECT COUNT(*) FROM changes WHERE notified IS NULL").fetchone()[0] == 0


def test_pending_and_checks_for_exclude_changes_after_job_id_refresh(tmp_path):
    assert source_watch.checks_for(tmp_path, 80) == []
    assert not source_watch.store_path(tmp_path).exists()

    conn = source_watch.connect(source_watch.store_path(tmp_path))
    try:
        source_watch.sync_pages(conn, {
            ("https://a.go.kr/1", 80): {"title": "글 80", "link": "https://jalcheck.com/80", "job_id": 1, "text": "본문"},
            ("https://a.go.kr/1", 81): {"title": "글 81", "link": "https://jalcheck.com/81", "job_id": 2, "text": "본문"},
        })
        conn.execute(
            "INSERT INTO changes (url, post_id, job_id, found_at, affects, summary, removed, added, model) VALUES "
            "('https://a.go.kr/1', 80, 1, '2026-05-01T00:00:00+00:00', 1, '갱신 전 작업 변화', '[]', '[]', 'm'), "
            "('https://a.go.kr/1', 80, 1, '2026-05-02T00:00:00+00:00', 0, '영향 없는 변화', '[]', '[]', 'm'), "
            "('https://a.go.kr/1', 81, 2, '2026-06-02T00:00:00+00:00', 1, '다른 글 변화', '[]', '[]', 'm')"
        )
        conn.commit()

        # 갱신 전(job_id=1)에는 pending과 checks_for에 모두 나타남
        assert [r["summary"] for r in source_watch.pending(conn)] == ["갱신 전 작업 변화", "다른 글 변화"]
        assert source_watch.checks_for(tmp_path, 80) == [
            "근거 페이지 변경: 갱신 전 작업 변화 (https://a.go.kr/1)"
        ]

        # 80번 글이 갱신되어 job_id가 2로 바뀌면 기존(job_id=1) 미알림 변화는 pending과 checks_for에서 모두 빠짐
        source_watch.sync_pages(conn, {
            ("https://a.go.kr/1", 80): {"title": "글 80 갱신", "link": "https://jalcheck.com/80", "job_id": 2, "text": "새 본문"},
            ("https://a.go.kr/1", 81): {"title": "글 81", "link": "https://jalcheck.com/81", "job_id": 2, "text": "본문"},
        })
        assert [r["summary"] for r in source_watch.pending(conn)] == ["다른 글 변화"]
        assert source_watch.checks_for(tmp_path, 80) == []

        # 새 작업(job_id=2) 기준으로 생긴 변화만 다시 포함됨
        conn.execute(
            "INSERT INTO changes (url, post_id, job_id, found_at, affects, summary, removed, added, model) VALUES "
            "('https://a.go.kr/1', 80, 2, '2026-06-03T00:00:00+00:00', 1, '갱신 후 작업 변화', '[]', '[]', 'm')"
        )
        conn.commit()
        assert [r["summary"] for r in source_watch.pending(conn)] == ["갱신 후 작업 변화", "다른 글 변화"]
        assert source_watch.checks_for(tmp_path, 80) == [
            "근거 페이지 변경: 갱신 후 작업 변화 (https://a.go.kr/1)"
        ]
    finally:
        conn.close()


def test_run_if_due_respects_run_hour_once_daily_and_max_tries_on_exception(env):
    deps, conn, fetcher, jobs_path = env
    jobs_conn = db.connect(jobs_path)
    try:
        publish_job(
            jobs_conn,
            "청년적금",
            80,
            "https://jalcheck.com/80",
            [{"url": "https://a.go.kr/1", "text": "초기 본문 내용입니다.", "via": "direct"}],
        )
    finally:
        jobs_conn.close()

    # RUN_HOUR(14시 KST = 05:00 UTC) 전에는 None
    before = datetime(2026, 6, 1, 4, 59, tzinfo=timezone.utc)
    assert source_watch.run_if_due(deps, now=before) is None
    assert conn.execute("SELECT COUNT(*) FROM days").fetchone()[0] == 0

    # RUN_HOUR 이후 하루 한 번만 실행
    at_due = datetime(2026, 6, 1, 5, 0, tzinfo=timezone.utc)
    fetcher.pages["https://a.go.kr/1"] = "초기 본문 내용입니다."
    note = source_watch.run_if_due(deps, now=at_due)
    assert note is not None and "감시 페이지 1곳" in note
    assert source_watch.run_if_due(deps, now=at_due + timedelta(hours=2)) is None
    assert dict(conn.execute("SELECT day, tries, done, fetched FROM days WHERE day = '2026-06-01'").fetchone()) == {
        "day": "2026-06-01",
        "tries": 1,
        "done": 1,
        "fetched": 1,
    }

    # 다음 날 예외 발생 시 tries 증가 후 MAX_TRIES_PER_DAY(3)에서 멈춤
    next_day = datetime(2026, 6, 2, 5, 0, tzinfo=timezone.utc)
    fetcher.pages["https://a.go.kr/1"] = RelayUnavailableError("중계 장애")
    fetcher.calls.clear()
    for expected_tries in range(1, source_watch.MAX_TRIES_PER_DAY + 1):
        assert source_watch.run_if_due(deps, now=next_day + timedelta(minutes=10 * expected_tries)) is None
        row = conn.execute("SELECT tries, done, fetched, note FROM days WHERE day = '2026-06-02'").fetchone()
        assert (row["tries"], row["done"], row["fetched"]) == (expected_tries, 0, expected_tries)
        assert "실패:" in row["note"]
        assert len(fetcher.calls) == expected_tries

    # MAX_TRIES_PER_DAY 도달 후에는 더 이상 시도하지 않음
    assert source_watch.run_if_due(deps, now=next_day + timedelta(hours=2)) is None
    assert len(fetcher.calls) == source_watch.MAX_TRIES_PER_DAY
    assert conn.execute("SELECT tries FROM days WHERE day = '2026-06-02'").fetchone()[0] == source_watch.MAX_TRIES_PER_DAY


def test_run_if_due_caps_total_daily_fetches_at_max_per_run_across_notify_failure_retry(env):
    deps, conn, fetcher, jobs_path = env
    total_urls = source_watch.MAX_PER_RUN + 5
    sources = [
        {"url": f"https://a.go.kr/p-{i:02d}", "text": "초기 기준 본문입니다.", "via": "direct"}
        for i in range(total_urls)
    ]
    jobs_conn = db.connect(jobs_path)
    try:
        publish_job(jobs_conn, "청년적금", 80, "https://jalcheck.com/80", sources)
    finally:
        jobs_conn.close()

    for i in range(total_urls):
        fetcher.pages[f"https://a.go.kr/p-{i:02d}"] = "변경된 기준 본문입니다."

    # 1차 시도: MAX_PER_RUN(20)곳을 읽은 뒤 텔레그램 알림 실패
    at_due = datetime(2026, 6, 3, 5, 0, tzinfo=timezone.utc)
    deps.telegram.fail = True
    assert source_watch.run_if_due(deps, now=at_due) is None
    assert len(fetcher.calls) == source_watch.MAX_PER_RUN
    day_row = conn.execute("SELECT tries, done, fetched FROM days WHERE day = '2026-06-03'").fetchone()
    assert (day_row["tries"], day_row["done"], day_row["fetched"]) == (1, 0, source_watch.MAX_PER_RUN)
    assert conn.execute("SELECT COUNT(*) FROM changes WHERE notified IS NULL").fetchone()[0] == source_watch.MAX_PER_RUN

    # 2차 재시도(10분 뒤): 남은 5곳이 있어도 오늘 이미 MAX_PER_RUN곳을 읽었으므로 추가 fetch 없이 알림만 마침
    deps.telegram.fail = False
    note = source_watch.run_if_due(deps, now=at_due + timedelta(minutes=10))
    assert note is not None
    assert len(fetcher.calls) == source_watch.MAX_PER_RUN
    assert len(deps.telegram.messages) == 1
    day_row = conn.execute("SELECT tries, done, fetched FROM days WHERE day = '2026-06-03'").fetchone()
    assert (day_row["tries"], day_row["done"], day_row["fetched"]) == (2, 1, source_watch.MAX_PER_RUN)


def test_run_with_send_false_records_change_leaving_notified_null(env):
    deps, conn, fetcher, jobs_path = env
    jobs_conn = db.connect(jobs_path)
    try:
        publish_job(
            jobs_conn,
            "청년적금",
            80,
            "https://jalcheck.com/80",
            [{"url": "https://a.go.kr/1", "text": "지원 금액은 월 50만 원입니다.", "via": "direct"}],
        )
    finally:
        jobs_conn.close()

    fetcher.pages["https://a.go.kr/1"] = "지원 금액은 월 60만 원입니다."
    deps.generator.affects = True
    deps.generator.summary = "월 50만→60만 원"

    summary = source_watch.run(deps, send=False, now=datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc))
    assert "글에 영향 1건, 알림 0건" in summary
    assert deps.telegram.messages == []

    change = conn.execute("SELECT affects, notified, summary FROM changes").fetchone()
    assert change["affects"] == 1
    assert change["notified"] is None
    assert change["summary"] == "월 50만→60만 원"
