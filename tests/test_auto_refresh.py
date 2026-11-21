"""근거 변경 자동 갱신·자동 반영 판정(auto_refresh·source_watch·refresh·daily_guard) 회귀 검사."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src import auto_refresh, daily_guard, db, refresh, source_watch
from src.daily_guard import AUTO_CALLBACK, DailyGuard
from src.freshness import html_to_text
from src.source_fetcher import Source
from src.telegram_notifier import Decision
from src.wp_client import WordPressClient, marker_comment
from tests.test_content import good_data
from tests.test_db import conn  # noqa: F401
from tests.test_orchestrator import env  # noqa: F401
from tests.test_refresh import RefreshHTTPSession

KST = auto_refresh.KST
NOW = datetime(2026, 11, 21, 8, 0, tzinfo=KST)


class FakeEvalGenerator:
    def __init__(
        self,
        outcome: dict | None = None,
        *,
        warning_outcome: dict | None = None,
        model: str = "fake-eval-model",
        models: list[str] | tuple[str, ...] | None = None,
        exc: Exception | None = None,
    ):
        self.outcome = outcome if outcome is not None else {"differences": []}
        self.warning_outcome = warning_outcome if warning_outcome is not None else {"checks": []}
        self.model = model
        if models is not None:
            self.models = list(models)
        self.exc = exc
        self.calls: list[tuple[str, object, str]] = []

    def structured(self, prompt, schema, system):
        self.calls.append((prompt, schema, system))
        if self.exc is not None:
            raise self.exc
        payload = self.warning_outcome if schema is auto_refresh.WarningOutcome else self.outcome
        return json.dumps(payload, ensure_ascii=False), self.model


class FakeEvalWP:
    def __init__(
        self,
        contents: dict[int, str] | None = None,
        titles: dict[int, str] | None = None,
        *,
        error: Exception | None = None,
    ):
        self.contents = dict(contents or {})
        self.titles = dict(titles or {})
        self.error = error
        self.calls: list[int] = []
        self.title_calls: list[int] = []

    def get_post_title(self, post_id: int) -> str:
        self.title_calls.append(post_id)
        if self.error is not None:
            raise self.error
        return self.titles.get(post_id, f"제목 {post_id}")

    def get_post_content(self, post_id: int) -> str:
        self.calls.append(post_id)
        if self.error is not None:
            raise self.error
        return self.contents[post_id]


class FakeTelegram:
    def __init__(self):
        self.messages: list[str] = []

    def send_text(self, text: str) -> None:
        self.messages.append(text)


class FakeOrch:
    def __init__(self, jobs_conn: sqlite3.Connection, wp: FakeEvalWP | None = None, result: str = "발행했습니다"):
        self.conn = jobs_conn
        self.svc = SimpleNamespace(wp=wp or FakeEvalWP())
        self.result = result
        self.decisions = []
        self.notifications: list[str] = []

    def awaiting_jobs(self):
        return list(reversed(db.list_jobs(self.conn, status="awaiting_approval", limit=-1)))

    def _notify(self, text: str) -> None:
        self.notifications.append(text)

    def handle_decision(self, decision):
        self.decisions.append(decision)
        return self.result


def _valid_cp(
    *,
    urls: list[str] | None = None,
    changes: list[dict] | None = None,
    sources: list[dict] | None = None,
    fact_check: dict | None = None,
    fallback_model: bool = False,
    warnings: list[str] | None = None,
    category_warning: str | None = None,
) -> tuple[dict, dict]:
    auto = {
        "urls": list(urls if urls is not None else ["https://a.go.kr/policy"]),
        "changes": list(
            changes
            if changes is not None
            else [{"url": "https://a.go.kr/policy", "summary": "한도 상향", "removed": ["500만"], "added": ["700만"]}]
        ),
    }
    draft: dict = {"post_id": 180}
    if category_warning is not None:
        draft["category_warning"] = category_warning
    cp = {
        "target": {"post_id": 80, "link": "https://jalcheck.com/?p=80", "title": "지원금", "auto": auto},
        "draft": draft,
        "content": {
            "fact_check": {"hold": False, "failed": False} if fact_check is None else fact_check,
            "fallback_model": fallback_model,
            "warnings": list(warnings or []),
            "sources": list(
                sources if sources is not None else [{"url": "https://a.go.kr/policy", "via": "direct"}]
            ),
        },
    }
    return cp, auto


def _make_wp_client(tmp_path) -> tuple[WordPressClient, RefreshHTTPSession]:
    http = RefreshHTTPSession()
    http.posts[80] = {
        "status": "publish",
        "title": "2026년 청년월세지원",
        "content": marker_comment(1) + "\n옛 본문",
        "excerpt": "요약",
        "slug": "youth-rent",
        "date": "2026-01-01T08:00:00",
        "date_gmt": "2025-12-31T23:00:00",
        "featured_media": 11,
        "tags": [1],
        "categories": [2],
    }
    client = WordPressClient("https://jalcheck.com", "admin", "test pass", session=http, lock_dir=tmp_path / "locks")
    return client, http


def _seed_job(
    jobs_conn: sqlite3.Connection,
    *,
    topic: str = "청년월세지원",
    status: str = "awaiting_approval",
    source_ref: str | None = None,
    checkpoint: dict | None = None,
) -> int:
    job_id = db.create_job(jobs_conn, topic, mode="blog", source="cli", source_ref=source_ref, checkpoint=checkpoint)
    if status != "queued":
        jobs_conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
    return job_id


# ---------------------------------------------------------------------------
# 1. static_reasons
# ---------------------------------------------------------------------------

def test_static_reasons_passes_when_all_valid():
    cp, auto = _valid_cp()
    assert auto_refresh.static_reasons(cp, auto) == []


@pytest.mark.parametrize(
    ("fact_check", "expected_fragment"),
    [
        ({}, "사실 확인 기록이 없습니다"),
        ({"failed": True, "hold": False}, "사실 확인을 끝까지 하지 못했습니다"),
        ({"failed": False, "hold": True}, "근거를 못 찾은 문장이 있습니다"),
    ],
)
def test_static_reasons_fact_check_cases(fact_check, expected_fragment):
    cp, auto = _valid_cp(fact_check=fact_check)
    if not fact_check:
        del cp["content"]["fact_check"]
    reasons = auto_refresh.static_reasons(cp, auto)
    assert len(reasons) == 1
    assert expected_fragment in reasons[0]


def test_static_reasons_fallback_model():
    cp, auto = _valid_cp(fallback_model=True)
    reasons = auto_refresh.static_reasons(cp, auto)
    assert len(reasons) == 1
    assert "예비 모델" in reasons[0]


def test_figure_list_splits_deduplicates_and_ignores_other_warnings():
    cp, _ = _valid_cp(
        warnings=[
            "자료에서 못 찾은 숫자(확인 필요): 170만 원, 30%",
            "자료에서 못 찾은 숫자: 30%, 500만 원, ",
            "",
            "예비 모델(gemini-3.6-flash)로 작성",
            "AI가 옮겨 적은 자료(원문 대조 권장)",
        ],
        category_warning="분류 확인 필요",
    )
    assert auto_refresh.figure_list(cp) == ["170만 원", "30%", "500만 원"]

    cp_none, _ = _valid_cp(warnings=["AI가 옮겨 적은 자료(원문 대조 권장)"], category_warning="분류 확인 필요")
    assert auto_refresh.figure_list(cp_none) == []


def test_static_reasons_allows_figure_and_fallback_prefix_warnings_only():
    cp, auto = _valid_cp(
        warnings=[
            "자료에서 못 찾은 숫자(확인 필요): 170만 원, 30%",
            "",
            "예비 모델(gemini-3.6-flash)로 작성",
        ]
    )
    assert auto_refresh.static_reasons(cp, auto) == []


@pytest.mark.parametrize(
    ("warnings", "category_warning", "expected_reason"),
    [
        ([], "분류 확인 필요", "확인할 것이 1건 있습니다: 분류 확인 필요"),
        (["AI가 옮겨 적은 자료(원문 대조 권장)"], None, "확인할 것이 1건 있습니다: AI가 옮겨 적은 자료(원문 대조 권장)"),
        (["자동 검토 실패", "자료에서 못 찾은 숫자: 170만 원", "경고 B"], "분류 확인 필요", "확인할 것이 3건 있습니다: 자동 검토 실패 / 경고 B"),
    ],
)
def test_static_reasons_rejects_non_figure_warnings(warnings, category_warning, expected_reason):
    cp, auto = _valid_cp(warnings=warnings, category_warning=category_warning)
    assert auto_refresh.static_reasons(cp, auto) == [expected_reason]


def test_static_reasons_source_matching_trailing_slash_and_via():
    # 끝 '/' 차이는 같은 주소로 인정
    cp1, auto1 = _valid_cp(
        urls=["https://a.go.kr/policy/"],
        sources=[{"url": "https://a.go.kr/policy", "via": "direct"}],
    )
    assert auto_refresh.static_reasons(cp1, auto1) == []

    cp2, auto2 = _valid_cp(
        urls=["https://a.go.kr/policy"],
        sources=[{"url": "https://a.go.kr/policy/", "via": "direct"}],
    )
    assert auto_refresh.static_reasons(cp2, auto2) == []

    # via=gemini는 직접 읽은 것으로 인정하지 않음
    cp3, auto3 = _valid_cp(
        urls=["https://a.go.kr/policy"],
        sources=[{"url": "https://a.go.kr/policy", "via": "gemini"}],
    )
    reasons = auto_refresh.static_reasons(cp3, auto3)
    assert len(reasons) == 1
    assert "직접 읽지 못했습니다" in reasons[0]
    assert "https://a.go.kr/policy" in reasons[0]


def test_static_reasons_empty_auto_urls():
    cp, auto = _valid_cp(urls=[])
    reasons = auto_refresh.static_reasons(cp, auto)
    assert reasons == ["바뀐 공식 페이지 기록이 없습니다"]

    cp_missing, _ = _valid_cp()
    assert auto_refresh.static_reasons(cp_missing, {}) == ["바뀐 공식 페이지 기록이 없습니다"]


# ---------------------------------------------------------------------------
# 2. evaluate
# ---------------------------------------------------------------------------

def test_evaluate_ok_when_differences_empty():
    cp, _ = _valid_cp()
    gen = FakeEvalGenerator({"differences": []})
    wp = FakeEvalWP(
        {80: "<p>월세 지원 500만 원</p>", 180: "<p>월세 지원 500만 원 안내</p>"},
        {80: "월세 지원 기존", 180: "월세 지원 새 글"},
    )
    expected_target_print = auto_refresh.fingerprint(wp, 80)
    expected_draft_print = auto_refresh.fingerprint(wp, 180)
    wp.calls.clear()
    wp.title_calls.clear()

    verdict = auto_refresh.evaluate(gen, wp, cp, now=NOW)

    assert verdict == {
        "ok": True,
        "reasons": [],
        "changes": [],
        "requested_at": NOW.isoformat(timespec="seconds"),
        "target_print": expected_target_print,
        "draft_print": expected_draft_print,
    }
    assert wp.title_calls == [80, 180]
    assert wp.calls == [80, 180]
    assert len(gen.calls) == auto_refresh.COMPARE_RUNS
    prompt, schema, system = gen.calls[0]
    assert schema is auto_refresh.CompareOutcome
    assert system == auto_refresh.SYSTEM_INSTRUCTION
    assert "제목: 월세 지원 기존" in prompt
    assert "제목: 월세 지원 새 글" in prompt
    assert html_to_text(wp.contents[80]) in prompt
    assert html_to_text(wp.contents[180]) in prompt


def test_comparable_text_and_evaluate_prompt_include_title_and_links():
    old_html = (
        '<p>기존 안내 <a href="https://old.go.kr/apply">신청</a> '
        '<a HREF=\'https://old.go.kr/info\'>정보</a> '
        '<a href="https://old.go.kr/apply">중복</a></p>'
    )
    new_html = '<p>새 안내 <a href="https://new.go.kr/apply">신청</a></p>'

    text = auto_refresh.comparable_text("청년월세지원", old_html)
    assert text.startswith("제목: 청년월세지원\n\n")
    assert "기존 안내 신청 정보 중복" in text
    assert "[글 속 링크 주소]\nhttps://old.go.kr/apply\nhttps://old.go.kr/info" in text
    assert text.count("https://old.go.kr/apply") == 1
    assert "[글 속 링크 주소]" not in auto_refresh.comparable_text("링크 없음", "<p>본문만</p>")

    cp, _ = _valid_cp()
    gen = FakeEvalGenerator({"differences": []})
    wp = FakeEvalWP(
        {80: old_html, 180: new_html},
        {80: "기존 글 제목", 180: "새 글 제목"},
    )
    verdict = auto_refresh.evaluate(gen, wp, cp, now=NOW)
    assert verdict["ok"] is True
    prompt = gen.calls[0][0]
    assert "제목: 기존 글 제목" in prompt
    assert "제목: 새 글 제목" in prompt
    assert "https://old.go.kr/apply" in prompt
    assert "https://old.go.kr/info" in prompt
    assert "https://new.go.kr/apply" in prompt


def test_compare_checks_main_model_and_skips_when_models_missing():
    # models 속성이 없으면 검사 생략
    gen_no_models = FakeEvalGenerator({"differences": []}, model="fallback-lite")
    assert not hasattr(gen_no_models, "models")
    assert auto_refresh.compare(gen_no_models, "old", "new", []).differences == []

    # models 가 비어 있어도 검사 생략
    gen_empty_models = FakeEvalGenerator({"differences": []}, model="fallback-lite", models=[])
    assert auto_refresh.compare(gen_empty_models, "old", "new", []).differences == []

    # models[0] 과 같으면 통과
    gen_main = FakeEvalGenerator(
        {"differences": []},
        model="gemini-3.8-flash",
        models=["gemini-3.8-flash", "gemini-3.6-flash"],
    )
    assert auto_refresh.compare(gen_main, "old", "new", []).differences == []

    # models[0] 과 다르면 FallbackModelError
    gen_fallback = FakeEvalGenerator(
        {"differences": []},
        model="gemini-3.6-flash",
        models=["gemini-3.8-flash", "gemini-3.6-flash"],
    )
    with pytest.raises(auto_refresh.FallbackModelError, match="gemini-3\\.6-flash"):
        auto_refresh.compare(gen_fallback, "old", "new", [])


def test_evaluate_rejects_when_compare_uses_fallback_model():
    cp, _ = _valid_cp()
    gen = FakeEvalGenerator(
        {"differences": []},
        model="gemini-3.6-flash",
        models=["gemini-3.8-flash", "gemini-3.6-flash"],
    )
    wp = FakeEvalWP({80: "<p>기존 글</p>", 180: "<p>새 글</p>"})

    verdict = auto_refresh.evaluate(gen, wp, cp, now=NOW)

    assert verdict["ok"] is False
    assert verdict["changes"] == []
    assert verdict["reasons"] == ["예비 모델(gemini-3.6-flash)이 판정해 믿을 수 없습니다"]
    assert "target_print" not in verdict
    assert "draft_print" not in verdict


def test_evaluate_ok_when_all_differences_explained():
    cp, _ = _valid_cp()
    gen = FakeEvalGenerator(
        {
            "differences": [
                {"item": "월세 공제 한도", "old": "750만 원", "new": "1,000만 원", "explained": True},
                {"item": "신청 마감일", "old": "5월 31일", "new": "6월 30일", "explained": True},
            ]
        }
    )
    wp = FakeEvalWP({80: "<p>기존 글</p>", 180: "<p>새 글</p>"})
    expected_target_print = auto_refresh.fingerprint(wp, 80)
    expected_draft_print = auto_refresh.fingerprint(wp, 180)

    verdict = auto_refresh.evaluate(gen, wp, cp, now=NOW)

    assert verdict["ok"] is True
    assert verdict["reasons"] == []
    assert verdict["changes"] == [
        "월세 공제 한도: 750만 원 → 1,000만 원",
        "신청 마감일: 5월 31일 → 6월 30일",
    ]
    assert verdict["requested_at"] == NOW.isoformat(timespec="seconds")
    assert verdict["target_print"] == expected_target_print
    assert verdict["draft_print"] == expected_draft_print


def test_evaluate_rejects_when_any_difference_unexplained():
    cp, _ = _valid_cp()
    gen = FakeEvalGenerator(
        {
            "differences": [
                {"item": "월세 공제 한도", "old": "750만 원", "new": "1,000만 원", "explained": True},
                {"item": "소득 요건", "old": "연 5,000만 원 이하", "new": "빠짐", "explained": False},
            ]
        }
    )
    wp = FakeEvalWP({80: "<p>기존 글</p>", 180: "<p>새 글</p>"})

    verdict = auto_refresh.evaluate(gen, wp, cp, now=NOW)

    assert verdict["ok"] is False
    assert len(verdict["reasons"]) == 1
    assert "소득 요건: 연 5,000만 원 이하 → 빠짐" in verdict["reasons"][0]
    assert "월세 공제 한도: 750만 원 → 1,000만 원" in verdict["changes"]
    assert "target_print" not in verdict
    assert "draft_print" not in verdict


def test_evaluate_rejects_unexplained_newly_added_info():
    cp, _ = _valid_cp()
    gen = FakeEvalGenerator(
        {
            "differences": [
                {"item": "제외 대상", "old": "없음", "new": "다주택자 제외", "explained": False},
            ]
        }
    )
    wp = FakeEvalWP({80: "<p>기존 글</p>", 180: "<p>새 글</p>"})

    verdict = auto_refresh.evaluate(gen, wp, cp, now=NOW)

    assert verdict["ok"] is False
    assert len(verdict["reasons"]) == 1
    assert "제외 대상: 없음 → 다주택자 제외" in verdict["reasons"][0]
    assert verdict["changes"] == ["제외 대상: 없음 → 다주택자 제외"]
    assert "target_print" not in verdict


def test_evaluate_rejects_empty_response_without_differences():
    with pytest.raises(ValidationError):
        auto_refresh.CompareOutcome.model_validate_json("{}")

    cp, _ = _valid_cp()
    gen = FakeEvalGenerator({})
    wp = FakeEvalWP({80: "<p>기존 글</p>", 180: "<p>새 글</p>"})

    verdict = auto_refresh.evaluate(gen, wp, cp, now=NOW)

    assert verdict["ok"] is False
    assert verdict["changes"] == []
    assert len(verdict["reasons"]) == 1
    assert "모델 오류" in verdict["reasons"][0]
    assert "target_print" not in verdict


@pytest.mark.parametrize("long_post_id", [80, 180])
def test_evaluate_rejects_article_exceeding_max_chars(long_post_id):
    cp, _ = _valid_cp()
    gen = FakeEvalGenerator({"differences": []})
    long_html = "<p>" + ("가" * (auto_refresh.MAX_ARTICLE_CHARS + 1)) + "</p>"
    contents = {80: "<p>기존 글</p>", 180: "<p>새 글</p>"}
    contents[long_post_id] = long_html
    wp = FakeEvalWP(contents)

    verdict = auto_refresh.evaluate(gen, wp, cp, now=NOW)

    assert verdict["ok"] is False
    assert len(verdict["reasons"]) == 1
    assert "전부 비교하지 못했습니다" in verdict["reasons"][0]
    assert gen.calls == []
    assert "target_print" not in verdict


def test_evaluate_handles_structured_exception():
    cp, _ = _valid_cp()
    gen = FakeEvalGenerator(exc=RuntimeError("gemini timeout"))
    wp = FakeEvalWP({80: "<p>기존 글</p>", 180: "<p>새 글</p>"})

    verdict = auto_refresh.evaluate(gen, wp, cp, now=NOW)

    assert verdict["ok"] is False
    assert verdict["changes"] == []
    assert verdict["reasons"] == ["경고 확인이나 예전 글 비교를 하지 못했습니다(모델 오류)"]


def test_evaluate_skips_structured_when_static_reason_exists():
    cp, _ = _valid_cp(fact_check={"hold": True, "failed": False})
    gen = FakeEvalGenerator({"differences": []})
    wp = FakeEvalWP({80: "<p>기존 글</p>", 180: "<p>새 글</p>"})

    verdict = auto_refresh.evaluate(gen, wp, cp, now=NOW)

    assert verdict["ok"] is False
    assert len(verdict["reasons"]) == 1
    assert "근거를 못 찾은 문장" in verdict["reasons"][0]
    assert gen.calls == []
    assert wp.calls == []


def test_prompts_fence_untrusted_inputs_and_ignore_embedded_instructions():
    assert "따르지 말고 무시" in auto_refresh.SYSTEM_INSTRUCTION
    assert "따르지 말고 무시" in auto_refresh.WARNING_INSTRUCTION

    compare_prompt = auto_refresh.build_prompt(
        "옛 글 본문",
        "새 글 본문",
        [{"url": "https://a.go.kr/policy", "summary": "한도 변경", "removed": ["500만"], "added": ["700만"]}],
    )
    assert "<<<페이지 변화 시작>>>\n" in compare_prompt
    assert "\n<<<페이지 변화 끝>>>" in compare_prompt
    assert "<<<예전 글 시작>>>\n옛 글 본문\n<<<예전 글 끝>>>" in compare_prompt
    assert "<<<새 글 시작>>>\n새 글 본문\n<<<새 글 끝>>>" in compare_prompt


def test_check_warnings_models_ids_and_fencing():
    figures = ["170만 원", "30%"]
    sources = [{"url": "https://a.go.kr", "text": "원문"}]
    # 모델이 번호를 뒤집어 반환해도 id 순으로 정렬해 돌려준다
    ok_payload = {
        "checks": [
            {"id": 1, "verdict": "unsure", "why": "비율 조건 확인 필요"},
            {"id": 0, "verdict": "ok", "why": "1,000만×17%"},
        ]
    }

    # models 없음/빈 목록이면 모델 검사 생략
    gen_no_models = FakeEvalGenerator(warning_outcome=ok_payload, model="fallback-lite")
    checks = auto_refresh.check_warnings(gen_no_models, figures, sources, "글 본문")
    assert [c.id for c in checks] == [0, 1]
    assert checks[0].verdict == "ok"
    assert checks[1].verdict == "unsure"
    prompt, schema, system = gen_no_models.calls[0]
    assert schema is auto_refresh.WarningOutcome
    assert system == auto_refresh.WARNING_INSTRUCTION
    assert "[숫자]\n0. 170만 원\n1. 30%" in prompt
    assert "<<<자료 시작>>>\n[https://a.go.kr]\n원문\n<<<자료 끝>>>" in prompt
    assert "<<<글 시작>>>\n글 본문\n<<<글 끝>>>" in prompt

    gen_empty_models = FakeEvalGenerator(warning_outcome=ok_payload, model="fallback-lite", models=[])
    assert len(auto_refresh.check_warnings(gen_empty_models, figures, sources, "글")) == 2

    # models[0] 일치 시 통과, 다르면 FallbackModelError
    gen_main = FakeEvalGenerator(
        warning_outcome=ok_payload,
        model="gemini-3.8-flash",
        models=["gemini-3.8-flash", "gemini-3.6-flash"],
    )
    assert len(auto_refresh.check_warnings(gen_main, figures, sources, "글")) == 2

    gen_fallback = FakeEvalGenerator(
        warning_outcome=ok_payload,
        model="gemini-3.6-flash",
        models=["gemini-3.8-flash", "gemini-3.6-flash"],
    )
    with pytest.raises(auto_refresh.FallbackModelError, match="gemini-3\\.6-flash"):
        auto_refresh.check_warnings(gen_fallback, figures, sources, "글")


@pytest.mark.parametrize(
    ("checks_payload", "expected_exc"),
    [
        # 판정 누락(1건만)
        ([{"id": 0, "verdict": "ok", "why": "일치"}], ValueError),
        # 판정 초과
        (
            [
                {"id": 0, "verdict": "ok", "why": "일치"},
                {"id": 1, "verdict": "ok", "why": "일치"},
                {"id": 2, "verdict": "ok", "why": "일치"},
            ],
            ValueError,
        ),
        # 판정 id 중복
        (
            [
                {"id": 0, "verdict": "ok", "why": "일치"},
                {"id": 0, "verdict": "ok", "why": "중복"},
            ],
            ValueError,
        ),
        # 판정 id 누락/범위 밖
        (
            [
                {"id": 0, "verdict": "ok", "why": "일치"},
                {"id": 2, "verdict": "ok", "why": "번호 오류"},
            ],
            ValueError,
        ),
        # 이상한 verdict 값
        (
            [
                {"id": 0, "verdict": "maybe", "why": "애매"},
                {"id": 1, "verdict": "ok", "why": "일치"},
            ],
            ValidationError,
        ),
        # 빈 why 문자열
        (
            [
                {"id": 0, "verdict": "ok", "why": ""},
                {"id": 1, "verdict": "ok", "why": "일치"},
            ],
            ValidationError,
        ),
    ],
)
def test_check_warnings_rejects_invalid_ids_verdicts_and_why(checks_payload, expected_exc):
    gen = FakeEvalGenerator(warning_outcome={"checks": checks_payload})
    with pytest.raises(expected_exc):
        auto_refresh.check_warnings(
            gen,
            ["170만 원", "30%"],
            [{"url": "https://a.go.kr", "text": "원문"}],
            "글",
        )


def test_check_warnings_rejects_empty_or_oversized_sources_without_calling_model():
    gen_empty = FakeEvalGenerator(warning_outcome={"checks": [{"id": 0, "verdict": "ok", "why": "일치"}]})
    with pytest.raises(ValueError, match="자료가 없거나 너무 길어"):
        auto_refresh.check_warnings(gen_empty, ["170만 원"], [], "글")
    assert gen_empty.calls == []

    gen_too_long = FakeEvalGenerator(warning_outcome={"checks": [{"id": 0, "verdict": "ok", "why": "일치"}]})
    oversized = [
        {"url": "https://a.go.kr/1", "text": "가" * (auto_refresh.MAX_SOURCE_CHARS // 2)},
        {"url": "https://a.go.kr/2", "text": "나" * (auto_refresh.MAX_SOURCE_CHARS // 2 + 100)},
    ]
    with pytest.raises(ValueError, match="자료가 없거나 너무 길어"):
        auto_refresh.check_warnings(gen_too_long, ["170만 원"], oversized, "글")
    assert gen_too_long.calls == []


def test_evaluate_checks_figures_before_compare_and_proceeds_when_all_ok():
    cp, _ = _valid_cp(
        warnings=["예비 모델(gemini-3.6-flash)로 작성", "자료에서 못 찾은 숫자(확인 필요): 170만 원, 30%"],
        sources=[{"url": "https://a.go.kr/policy", "via": "direct", "text": "한도 1,000만 원 공제율 17%, 지원 30%"}],
    )
    gen = FakeEvalGenerator(
        outcome={"differences": [{"item": "공제 한도", "old": "500만", "new": "700만", "explained": True}]},
        warning_outcome={
            "checks": [
                {"id": 0, "verdict": "ok", "why": "1,000만×17%"},
                {"id": 1, "verdict": "ok", "why": "원문 30% 일치"},
            ]
        },
    )
    wp = FakeEvalWP({80: "<p>기존 글</p>", 180: "<p>새 글 본문</p>"})

    verdict = auto_refresh.evaluate(gen, wp, cp, now=NOW)

    assert verdict["ok"] is True
    assert verdict["reasons"] == []
    assert verdict["changes"] == ["공제 한도: 500만 → 700만"]
    assert verdict["warning_checks"] == [
        "170만 원 → ok: 1,000만×17%",
        "30% → ok: 원문 30% 일치",
    ]
    assert "target_print" in verdict and "draft_print" in verdict
    assert [c[1] for c in gen.calls] == [auto_refresh.WarningOutcome] + [auto_refresh.CompareOutcome] * auto_refresh.COMPARE_RUNS
    warn_prompt = gen.calls[0][0]
    assert "0. 170만 원\n1. 30%" in warn_prompt
    assert "<<<자료 시작>>>" in warn_prompt
    assert "한도 1,000만 원 공제율 17%, 지원 30%" in warn_prompt
    assert "<<<글 시작>>>" in warn_prompt
    assert "새 글 본문" in warn_prompt
    assert "예비 모델(" not in warn_prompt


@pytest.mark.parametrize(
    ("warnings", "category_warning", "expected_fragment"),
    [
        ([], "분류 확인 필요", "확인할 것이 1건 있습니다: 분류 확인 필요"),
        (["자료에서 못 찾은 숫자: 170만 원"], "분류 확인 필요", "확인할 것이 1건 있습니다: 분류 확인 필요"),
        (["AI가 옮겨 적은 자료(원문 대조 권장)"], None, "확인할 것이 1건 있습니다: AI가 옮겨 적은 자료(원문 대조 권장)"),
    ],
)
def test_evaluate_rejects_category_and_non_figure_warnings_without_calling_ai(
    warnings, category_warning, expected_fragment
):
    cp, _ = _valid_cp(
        warnings=warnings,
        category_warning=category_warning,
        sources=[{"url": "https://a.go.kr/policy", "via": "direct", "text": "원문"}],
    )
    gen = FakeEvalGenerator(
        outcome={"differences": []},
        warning_outcome={"checks": [{"id": 0, "verdict": "ok", "why": "일치"}]},
    )
    wp = FakeEvalWP({80: "<p>기존 글</p>", 180: "<p>새 글</p>"})

    verdict = auto_refresh.evaluate(gen, wp, cp, now=NOW)

    assert verdict["ok"] is False
    assert verdict["reasons"] == [expected_fragment]
    assert gen.calls == []
    assert wp.calls == []
    assert "target_print" not in verdict
    assert "draft_print" not in verdict


@pytest.mark.parametrize(
    ("figures_csv", "checks", "expected_reasons"),
    [
        (
            "300만 원, 12월 31일",
            [
                {"id": 0, "verdict": "ok", "why": "원문 일치"},
                {"id": 1, "verdict": "wrong", "why": "자료는 11월 30일"},
            ],
            ["AI 판단: 틀림 — 12월 31일 (자료는 11월 30일)"],
        ),
        (
            "170만 원, 30%, 500만 원",
            [
                {"id": 0, "verdict": "ok", "why": "1,000만×17%"},
                {"id": 1, "verdict": "unsure", "why": "적용 조건 확인 필요"},
                {"id": 2, "verdict": "ok", "why": "원문 일치"},
            ],
            ["AI 판단: 확인 불가 — 30% (적용 조건 확인 필요)"],
        ),
        (
            "10만 원, 20%, 30일, 40세, 50건",
            [
                {"id": 0, "verdict": "wrong", "why": "오류1"},
                {"id": 1, "verdict": "unsure", "why": "모름2"},
                {"id": 2, "verdict": "wrong", "why": "오류3"},
                {"id": 3, "verdict": "unsure", "why": "모름4"},
                {"id": 4, "verdict": "wrong", "why": "오류5"},
            ],
            [
                "AI 판단: 틀림 — 10만 원 (오류1)",
                "AI 판단: 확인 불가 — 20% (모름2)",
                "AI 판단: 틀림 — 30일 (오류3)",
                "AI 판단: 확인 불가 — 40세 (모름4)",
            ],
        ),
    ],
)
def test_evaluate_rejects_bad_warning_checks_without_calling_compare(figures_csv, checks, expected_reasons):
    cp, _ = _valid_cp(
        warnings=[f"자료에서 못 찾은 숫자(확인 필요): {figures_csv}"],
        sources=[{"url": "https://a.go.kr/policy", "via": "direct", "text": "원문"}],
    )
    gen = FakeEvalGenerator(
        outcome={"differences": []},
        warning_outcome={"checks": checks},
    )
    wp = FakeEvalWP({80: "<p>기존 글</p>", 180: "<p>새 글</p>"})

    verdict = auto_refresh.evaluate(gen, wp, cp, now=NOW)

    assert verdict["ok"] is False
    assert verdict["reasons"] == expected_reasons
    assert len(verdict["warning_checks"]) == len(checks)
    assert verdict["changes"] == []
    assert "target_print" not in verdict
    assert "draft_print" not in verdict
    assert [c[1] for c in gen.calls] == [auto_refresh.WarningOutcome]


def test_evaluate_rejects_warning_check_fallback_or_error_without_compare():
    cp, _ = _valid_cp(
        warnings=["자료에서 못 찾은 숫자: 170만 원, 30%"],
        sources=[{"url": "https://a.go.kr/policy", "via": "direct", "text": "원문"}],
    )
    wp = FakeEvalWP({80: "<p>기존 글</p>", 180: "<p>새 글</p>"})

    gen_fallback = FakeEvalGenerator(
        warning_outcome={
            "checks": [
                {"id": 0, "verdict": "ok", "why": "일치"},
                {"id": 1, "verdict": "ok", "why": "일치"},
            ]
        },
        model="gemini-3.6-flash",
        models=["gemini-3.8-flash", "gemini-3.6-flash"],
    )
    verdict_fb = auto_refresh.evaluate(gen_fallback, wp, cp, now=NOW)
    assert verdict_fb["ok"] is False
    assert verdict_fb["reasons"] == ["예비 모델(gemini-3.6-flash)이 판정해 믿을 수 없습니다"]
    assert [c[1] for c in gen_fallback.calls] == [auto_refresh.WarningOutcome]

    # id 중복/누락(ValueError)도 모델 오류로 거부하고 compare 미호출
    for bad_checks in (
        [],
        [{"id": 0, "verdict": "ok", "why": "일치"}, {"id": 0, "verdict": "ok", "why": "중복"}],
        [{"id": 0, "verdict": "ok", "why": "일치"}, {"id": 2, "verdict": "ok", "why": "누락"}],
        [{"id": 0, "verdict": "invalid", "why": "형식 오류"}, {"id": 1, "verdict": "ok", "why": "일치"}],
    ):
        gen_bad = FakeEvalGenerator(warning_outcome={"checks": bad_checks})
        verdict_err = auto_refresh.evaluate(gen_bad, wp, cp, now=NOW)
        assert verdict_err["ok"] is False
        assert verdict_err["reasons"] == ["경고 확인이나 예전 글 비교를 하지 못했습니다(모델 오류)"]
        assert [c[1] for c in gen_bad.calls] == [auto_refresh.WarningOutcome]

    # 자료 텍스트 합계가 MAX_SOURCE_CHARS 를 넘으면 모델 호출 없이 거부
    cp_long, _ = _valid_cp(
        warnings=["자료에서 못 찾은 숫자: 170만 원"],
        sources=[{"url": "https://a.go.kr/policy", "via": "direct", "text": "가" * (auto_refresh.MAX_SOURCE_CHARS + 1)}],
    )
    gen_long = FakeEvalGenerator(warning_outcome={"checks": [{"id": 0, "verdict": "ok", "why": "일치"}]})
    verdict_long = auto_refresh.evaluate(gen_long, wp, cp_long, now=NOW)
    assert verdict_long["ok"] is False
    assert verdict_long["reasons"] == ["경고 확인이나 예전 글 비교를 하지 못했습니다(모델 오류)"]
    assert gen_long.calls == []


def test_still_same_detects_content_title_changes_and_errors():
    cp, _ = _valid_cp()
    gen = FakeEvalGenerator({"differences": []})
    wp = FakeEvalWP(
        {80: "<p>원래 본문</p>", 180: "<p>임시 본문</p>"},
        {80: "원래 제목", 180: "임시 제목"},
    )
    verdict = auto_refresh.evaluate(gen, wp, cp, now=NOW)
    job = {"id": 1, "checkpoint": {**cp, "auto_apply": verdict}}

    assert auto_refresh.still_same(wp, job) is True

    # 원래 글 본문 변경
    wp.contents[80] = "<p>수정된 원래 본문</p>"
    assert auto_refresh.still_same(wp, job) is False
    wp.contents[80] = "<p>원래 본문</p>"

    # 원래 글 제목 변경
    wp.titles[80] = "수정된 원래 제목"
    assert auto_refresh.still_same(wp, job) is False
    wp.titles[80] = "원래 제목"

    # 임시 글 본문 변경
    wp.contents[180] = "<p>수정된 임시 본문</p>"
    assert auto_refresh.still_same(wp, job) is False
    wp.contents[180] = "<p>임시 본문</p>"

    # 임시 글 제목 변경
    wp.titles[180] = "수정된 임시 제목"
    assert auto_refresh.still_same(wp, job) is False
    wp.titles[180] = "임시 제목"

    assert auto_refresh.still_same(wp, job) is True

    # 조회 오류도 False
    wp.error = RuntimeError("wp connection error")
    assert auto_refresh.still_same(wp, job) is False
    wp.error = None
    del wp.contents[180]
    assert auto_refresh.still_same(wp, job) is False


# ---------------------------------------------------------------------------
# 3. due
# ---------------------------------------------------------------------------

def _due_job(
    *,
    status: str = "awaiting_approval",
    has_auto: bool = True,
    ok: bool = True,
    held: bool = False,
    attempted: str | None = None,
    requested_at: str = "2026-11-20T14:59:59+00:00",
) -> dict:
    target: dict = {"post_id": 80}
    if has_auto:
        target["auto"] = {"urls": ["https://a.go.kr/policy"], "changes": []}
    verdict: dict = {"ok": ok, "requested_at": requested_at}
    if held:
        verdict["held"] = True
    if attempted is not None:
        verdict["attempted"] = attempted
    return {"status": status, "checkpoint": {"target": target, "auto_apply": verdict}}


def test_due_true_only_when_all_conditions_met_and_yesterday_kst():
    # 2026-11-20T14:59:59+00:00 = 한국 2026-11-20 23:59:59
    # now = 2026-11-20T23:00:00+00:00 = 한국 2026-11-21 08:00:00 (UTC 날짜는 같지만 한국 날짜는 다음 날)
    now_kst_morning = datetime(2026, 11, 20, 23, 0, tzinfo=timezone.utc)
    job = _due_job(requested_at="2026-11-20T14:59:59+00:00")
    assert auto_refresh.due(job, now_kst_morning) is True


def test_due_false_on_same_kst_date_even_when_utc_dates_differ():
    # requested_at 2026-11-20T16:00:00+00:00 = 한국 2026-11-21 01:00:00
    # now          2026-11-21T10:00:00+00:00 = 한국 2026-11-21 19:00:00
    # UTC 날짜(20일 vs 21일)는 다르지만 한국 날짜는 둘 다 2026-11-21이므로 False
    job = _due_job(requested_at="2026-11-20T16:00:00+00:00")
    assert auto_refresh.due(job, datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)) is False
    assert auto_refresh.due(job, datetime(2026, 11, 21, 8, 0, tzinfo=KST)) is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "queued"},
        {"status": "published"},
        {"has_auto": False},
        {"ok": False},
        {"held": True},
        {"attempted": "2026-11-21"},
        {"requested_at": "invalid-iso"},
    ],
)
def test_due_false_for_disqualifying_conditions(overrides):
    job = _due_job(**overrides)
    assert auto_refresh.due(job, NOW) is False


# ---------------------------------------------------------------------------
# 4. approval_line
# ---------------------------------------------------------------------------

def test_approval_line_formats_ok_and_rejected():
    ok_line = auto_refresh.approval_line({"ok": True, "changes": ["지원금: 50만 → 60만"]})
    assert ok_line.startswith("[자동 반영 예정]")
    assert "지원금: 50만 → 60만" in ok_line
    assert "경고는 AI가 자료와 대조해 확인함" not in ok_line

    ok_with_warnings = auto_refresh.approval_line(
        {
            "ok": True,
            "changes": ["지원금: 50만 → 60만"],
            "warning_checks": ["숫자 확인 → ok: 원문 일치", "분류 확인 → ok: 적합"],
        }
    )
    assert ok_with_warnings.startswith("[자동 반영 예정]")
    assert "경고는 AI가 자료와 대조해 확인함: 숫자 확인 → ok: 원문 일치 / 분류 확인 → ok: 적합" in ok_with_warnings

    ok_empty = auto_refresh.approval_line({"ok": True, "changes": []})
    assert ok_empty.startswith("[자동 반영 예정]")
    assert "표현만 바뀜" in ok_empty

    held_ok = auto_refresh.approval_line({"ok": True, "held": True, "changes": ["지원금: 50만 → 60만"]})
    assert held_ok == "[자동 반영 안 함] [보류]를 누른 갱신입니다. 내용을 확인하고 직접 [발행]을 눌러 주세요"

    held_rejected = auto_refresh.approval_line({"ok": False, "held": True, "reasons": ["확인할 것이 1건 있습니다"]})
    assert held_rejected == held_ok

    no_line = auto_refresh.approval_line(
        {
            "ok": False,
            "reasons": ["예비 모델로 쓴 글입니다", "AI 판단: 틀림 — 마감일 (원문과 다름)"],
            "warning_checks": ["마감일 → wrong: 원문과 다름"],
        }
    )
    assert no_line.startswith("[자동 반영 안 함]")
    assert "예비 모델로 쓴 글입니다; AI 판단: 틀림 — 마감일 (원문과 다름)" in no_line
    assert "경고는 AI가 자료와 대조해 확인함" not in no_line
    assert "직접 [발행]" in no_line

    no_empty = auto_refresh.approval_line({"ok": False, "reasons": []})
    assert no_empty.startswith("[자동 반영 안 함]")
    assert "판정 기록 없음" in no_empty


# ---------------------------------------------------------------------------
# 5. source_watch: message(status), _auto_entry, register_refreshes, notify, request_refresh(auto)
# ---------------------------------------------------------------------------

def test_source_watch_message_with_status_and_auto_entry(tmp_path):
    sw_conn = source_watch.connect(tmp_path / "sw.sqlite3")
    try:
        source_watch.sync_pages(
            sw_conn,
            {
                ("https://a.go.kr/1", 80): {"title": "글 80", "link": "https://jalcheck.com/80", "job_id": 1, "text": "a"},
                ("https://a.go.kr/2", 80): {"title": "글 80", "link": "https://jalcheck.com/80", "job_id": 1, "text": "b"},
                ("https://a.go.kr/3", 81): {"title": "글 81", "link": "https://jalcheck.com/81", "job_id": 2, "text": "c"},
            },
        )
        sw_conn.execute(
            "INSERT INTO changes (url, post_id, job_id, found_at, affects, summary, removed, added, model) VALUES "
            "('https://a.go.kr/1', 80, 1, '2026-11-21T00:00:00+00:00', 1, '한도 변경', '[\"옛 한도\"]', '[\"새 한도\"]', 'm'),"
            "('https://a.go.kr/2', 80, 1, '2026-11-21T00:00:00+00:00', 1, '기간 변경', '[\"5월\"]', '[\"6월\"]', 'm'),"
            "('https://a.go.kr/3', 81, 2, '2026-11-21T00:00:00+00:00', 1, '대상 변경', '[\"무주택\"]', '[\"청년\"]', 'm')"
        )
        sw_conn.commit()
        rows = source_watch.pending(sw_conn)

        rows_80 = [r for r in rows if r["post_id"] == 80]
        entry = source_watch._auto_entry(rows_80)
        assert entry == {
            "urls": ["https://a.go.kr/1", "https://a.go.kr/2"],
            "change_ids": [int(r["id"]) for r in rows_80],
            "changes": [
                {"url": "https://a.go.kr/1", "summary": "한도 변경", "removed": ["옛 한도"], "added": ["새 한도"]},
                {"url": "https://a.go.kr/2", "summary": "기간 변경", "removed": ["5월"], "added": ["6월"]},
            ],
        }

        text, sent_c, sent_d = source_watch.message(
            rows,
            [],
            status={80: "고치는 작업 5번 자동 등록", 81: "자동 등록 못 함: 오늘 갱신 요청은 3건까지입니다"},
        )
        assert len(sent_c) == 3 and sent_d == []
        assert text.count(" → 고치는 작업 5번 자동 등록") == 1
        assert text.count(" → 자동 등록 못 함: 오늘 갱신 요청은 3건까지입니다") == 1
        assert "글 80번 글 80\n https://jalcheck.com/80\n → 고치는 작업 5번 자동 등록\n - 한도 변경" in text
    finally:
        sw_conn.close()


def test_register_refreshes_and_notify_save_target_auto(tmp_path, monkeypatch):
    wp_client, http = _make_wp_client(tmp_path)
    # 80: 정상 발행 글(성공), 81: draft 글(거절), 82: 조회 시 예외(오류)
    http.posts[81] = dict(http.posts[80], status="draft", title="초안 글")
    orig_get_post = wp_client.get_post

    def fake_get_post(pid: int):
        if int(pid) == 82:
            raise RuntimeError("wp connection error")
        return orig_get_post(pid)

    monkeypatch.setattr(wp_client, "get_post", fake_get_post)

    jobs_path = tmp_path / "jobs.sqlite3"
    db.connect(jobs_path).close()

    tg = FakeTelegram()
    deps = source_watch.Deps(
        wp=wp_client,
        generator=FakeEvalGenerator(),
        telegram=tg,
        data_dir=tmp_path,
        jobs_conn_factory=lambda: db.connect(jobs_path),
        fetch=lambda url, sid="S1": Source(id=sid, url=url, title="", text=""),
        pause=0,
        auto_refresh=True,
    )

    sw_conn = source_watch.connect(source_watch.store_path(tmp_path))
    try:
        source_watch.sync_pages(
            sw_conn,
            {
                ("https://a.go.kr/p80", 80): {"title": "글 80", "link": "https://jalcheck.com/80", "job_id": 1, "text": "t"},
                ("https://a.go.kr/p81", 81): {"title": "글 81", "link": "https://jalcheck.com/81", "job_id": 2, "text": "t"},
                ("https://a.go.kr/p82", 82): {"title": "글 82", "link": "https://jalcheck.com/82", "job_id": 3, "text": "t"},
            },
        )
        sw_conn.execute(
            "INSERT INTO changes (url, post_id, job_id, found_at, affects, summary, removed, added, model) VALUES "
            "('https://a.go.kr/p80', 80, 1, '2026-11-21T00:00:00+00:00', 1, '금액 변경', '[\"30만\"]', '[\"40만\"]', 'm'),"
            "('https://a.go.kr/p80', 80, 1, '2026-11-21T00:01:00+00:00', 1, '기한 변경', '[\"5월\"]', '[\"6월\"]', 'm'),"
            "('https://a.go.kr/p81', 81, 2, '2026-11-21T00:00:00+00:00', 1, '조건 변경', '[]', '[\"신규\"]', 'm'),"
            "('https://a.go.kr/p82', 82, 3, '2026-11-21T00:00:00+00:00', 1, '서류 변경', '[\"주민등록\"]', '[]', 'm')"
        )
        sw_conn.commit()
        rows = source_watch.pending(sw_conn)

        status = source_watch.register_refreshes(deps, rows)
        jobs_conn = db.connect(jobs_path)
        try:
            jobs = db.list_jobs(jobs_conn)
            assert len(jobs) == 1
            job = jobs[0]
        finally:
            jobs_conn.close()

        assert status[80] == f"고치는 작업 {job['id']}번 자동 등록"
        assert f"작업 {job['id']}번 자동 등록" in status[80]
        assert status[81].startswith("자동 등록 못 함: ")
        assert "발행된 글이 아니라" in status[81]
        assert status[82] == "자동 등록 못 함(오류)"

        auto = job["checkpoint"]["target"]["auto"]
        assert auto["urls"] == ["https://a.go.kr/p80"]
        assert auto["change_ids"] == [int(r["id"]) for r in rows if r["post_id"] == 80]
        assert len(auto["changes"]) == 2
        assert isinstance(auto["changes"][0]["removed"], list)
        assert isinstance(auto["changes"][0]["added"], list)
        assert auto["changes"][0]["removed"] == ["30만"]
        assert auto["changes"][0]["added"] == ["40만"]

        # notify도 상태 줄을 붙여 보내고 notified를 채운다
        # 이미 80번 변화들은 작업에 등록됐으므로(change_ids 일치) '이미 자동 등록한 변화' 문구가 붙는다
        sent_count = source_watch.notify(deps, sw_conn, "2026-11-21")
        assert sent_count == 4
        assert len(tg.messages) == 1
        assert " → 이미 자동 등록한 변화입니다(다시 고치려면 '갱신 번호')" in tg.messages[0]
        assert " → 자동 등록 못 함(오류)" in tg.messages[0]
    finally:
        sw_conn.close()


def test_register_refreshes_skips_change_ids_from_canceled_jobs(tmp_path):
    wp_client, _ = _make_wp_client(tmp_path)
    jobs_path = tmp_path / "jobs.sqlite3"
    db.connect(jobs_path).close()

    deps = source_watch.Deps(
        wp=wp_client,
        generator=FakeEvalGenerator(),
        telegram=FakeTelegram(),
        data_dir=tmp_path,
        jobs_conn_factory=lambda: db.connect(jobs_path),
        fetch=lambda url, sid="S1": Source(id=sid, url=url, title="", text=""),
        pause=0,
        auto_refresh=True,
    )

    sw_conn = source_watch.connect(source_watch.store_path(tmp_path))
    try:
        source_watch.sync_pages(
            sw_conn,
            {("https://a.go.kr/p80", 80): {"title": "글 80", "link": "https://jalcheck.com/80", "job_id": 1, "text": "t"}},
        )
        sw_conn.execute(
            "INSERT INTO changes (url, post_id, job_id, found_at, affects, summary, removed, added, model) VALUES "
            "('https://a.go.kr/p80', 80, 1, '2026-11-21T00:00:00+00:00', 1, '금액 변경', '[\"30만\"]', '[\"40만\"]', 'm')"
        )
        sw_conn.commit()
        rows = source_watch.pending(sw_conn)
        first_change_id = int(rows[0]["id"])

        first_status = source_watch.register_refreshes(deps, rows)
        jobs_conn = db.connect(jobs_path)
        try:
            jobs = db.list_jobs(jobs_conn)
            assert len(jobs) == 1
            first_job_id = jobs[0]["id"]
            assert first_status[80] == f"고치는 작업 {first_job_id}번 자동 등록"
            db.set_status(jobs_conn, first_job_id, "canceled")
            # 같은 초 안에 다음 요청이 들어와도 source_ref 충돌이 나지 않도록 이전 시각 작업으로 둔다
            jobs_conn.execute("UPDATE jobs SET source_ref = ? WHERE id = ?", ("refresh:80:2026-11-20T00:00:00+00:00", first_job_id))
            jobs_conn.commit()
        finally:
            jobs_conn.close()

        # 취소된 작업의 target.auto.change_ids 에 있던 변화는 다시 등록하지 않는다
        second_status = source_watch.register_refreshes(deps, rows)
        assert second_status[80] == "이미 자동 등록한 변화입니다(다시 고치려면 '갱신 번호')"
        jobs_conn = db.connect(jobs_path)
        try:
            assert len(db.list_jobs(jobs_conn)) == 1
        finally:
            jobs_conn.close()

        # 같은 글에 새 변화가 생기면 기존 change_id 는 빼고 새 변화만 등록한다
        sw_conn.execute(
            "INSERT INTO changes (url, post_id, job_id, found_at, affects, summary, removed, added, model) VALUES "
            "('https://a.go.kr/p80', 80, 1, '2026-11-21T01:00:00+00:00', 1, '기한 변경', '[\"5월\"]', '[\"6월\"]', 'm')"
        )
        sw_conn.commit()
        rows2 = source_watch.pending(sw_conn)
        second_change_id = max(int(r["id"]) for r in rows2)
        assert second_change_id != first_change_id

        third_status = source_watch.register_refreshes(deps, rows2)
        jobs_conn = db.connect(jobs_path)
        try:
            jobs = db.list_jobs(jobs_conn)
            assert len(jobs) == 2
            new_job = max(jobs, key=lambda j: j["id"])
            assert third_status[80] == f"고치는 작업 {new_job['id']}번 자동 등록"
            assert new_job["checkpoint"]["target"]["auto"]["change_ids"] == [second_change_id]
        finally:
            jobs_conn.close()
    finally:
        sw_conn.close()


def test_request_refresh_saves_auto_in_memory_db(tmp_path):
    wp_client, _ = _make_wp_client(tmp_path)
    mem_conn = sqlite3.connect(":memory:")
    mem_conn.row_factory = sqlite3.Row
    db._prepare_schema(mem_conn, tmp_path / "unused.db")
    try:
        auto = {
            "urls": ["https://a.go.kr/p80"],
            "changes": [{"url": "https://a.go.kr/p80", "summary": "한도 변경", "removed": ["100만"], "added": ["200만"]}],
        }
        job_id, _ = refresh.request_refresh(mem_conn, wp_client, 80, source="cli", now=NOW, auto=auto)
        assert job_id is not None
        saved = db.get_job(mem_conn, job_id)
        assert saved["checkpoint"]["target"]["auto"] == auto
    finally:
        mem_conn.close()


# ---------------------------------------------------------------------------
# 6. DailyGuard.apply_refreshes
# ---------------------------------------------------------------------------

def test_daily_guard_apply_refreshes_conditions_and_once_per_day(conn, tmp_path):  # noqa: F811
    yesterday = (NOW - timedelta(days=1)).isoformat(timespec="seconds")
    today_same_day = NOW.replace(hour=1).isoformat(timespec="seconds")
    wp = FakeEvalWP({80: "<p>옛 글</p>", 180: "<p>새 글</p>"}, {80: "청년월세지원", 180: "청년월세지원 새"})
    target_print = auto_refresh.fingerprint(wp, 80)
    draft_print = auto_refresh.fingerprint(wp, 180)

    # 1) 대상: due 자동 갱신 + still_same 통과 (실행돼야 함)
    due_id = _seed_job(
        conn,
        topic="대상 갱신",
        source_ref="refresh:80:due",
        checkpoint={
            "target": {"post_id": 80, "auto": {"urls": ["https://a.go.kr/1"], "changes": []}},
            "draft": {"post_id": 180},
            "auto_apply": {
                "ok": True,
                "reasons": [],
                "changes": ["50만 → 60만"],
                "requested_at": yesterday,
                "target_print": target_print,
                "draft_print": draft_print,
            },
            "approval": {"nonce": "nonce-due", "message_id": 101},
        },
    )
    # 2) 같은 날 요청된 자동 갱신 (건너뜀)
    _seed_job(
        conn,
        topic="오늘 요청 갱신",
        source_ref="refresh:81:today",
        checkpoint={
            "target": {"post_id": 81, "auto": {"urls": ["https://a.go.kr/2"], "changes": []}},
            "auto_apply": {"ok": True, "reasons": [], "changes": [], "requested_at": today_same_day},
            "approval": {"nonce": "nonce-today", "message_id": 102},
        },
    )
    # 3) 수동 갱신 (auto 없음, 건너뜀)
    _seed_job(
        conn,
        topic="수동 갱신",
        source_ref="refresh:82:manual",
        checkpoint={
            "target": {"post_id": 82},
            "auto_apply": {"ok": True, "reasons": [], "changes": [], "requested_at": yesterday},
            "approval": {"nonce": "nonce-manual", "message_id": 103},
        },
    )
    # 4) 일반 새 글 (is_refresh False, 건너뜀)
    _seed_job(
        conn,
        topic="일반 새 글",
        source_ref="topic:1",
        checkpoint={
            "target": {"post_id": 83, "auto": {"urls": ["https://a.go.kr/4"], "changes": []}},
            "auto_apply": {"ok": True, "reasons": [], "changes": [], "requested_at": yesterday},
            "approval": {"nonce": "nonce-plain", "message_id": 104},
        },
    )
    # 5) 보류([보류] 누름)된 자동 갱신 (건너뜀)
    _seed_job(
        conn,
        topic="보류 갱신",
        source_ref="refresh:84:held",
        checkpoint={
            "target": {"post_id": 84, "auto": {"urls": ["https://a.go.kr/5"], "changes": []}},
            "auto_apply": {"ok": True, "reasons": [], "changes": [], "requested_at": yesterday, "held": True},
            "approval": {"nonce": "nonce-held", "message_id": 105},
        },
    )

    orch = FakeOrch(conn, wp=wp)
    state_path = tmp_path / "daily_guard.json"
    guard = DailyGuard(orch, state_path)

    # (a) 8시 전에는 None
    assert guard.apply_refreshes(NOW.replace(hour=7, minute=59)) is None
    assert orch.decisions == []
    assert orch.notifications == []
    assert not state_path.exists()

    # (b) 8시 이후 due인 자동 갱신만 AUTO_CALLBACK으로 호출
    note = guard.apply_refreshes(NOW)
    assert note == f"갱신 자동 반영 — 작업 {due_id}번: 발행했습니다"
    assert len(orch.decisions) == 1
    assert orch.notifications == []
    d = orch.decisions[0]
    assert (d.job_id, d.action, d.nonce, d.callback_id, d.message_id) == (
        due_id,
        "publish",
        "nonce-due",
        AUTO_CALLBACK,
        101,
    )

    # (c) 같은 날 두 번 불러도 state["refreshed"]로 인해 한 번만 호출, 장부에도 attempted 기록
    saved_state = json.loads(state_path.read_text())
    assert saved_state == {"date": "2026-11-21", "refreshed": [due_id]}
    assert db.get_job(conn, due_id)["checkpoint"]["auto_apply"]["attempted"] == "2026-11-21"
    assert guard.apply_refreshes(NOW + timedelta(minutes=10)) is None
    assert len(orch.decisions) == 1


def test_daily_guard_apply_refreshes_skips_and_notifies_when_post_changed(conn, tmp_path):  # noqa: F811
    yesterday = (NOW - timedelta(days=1)).isoformat(timespec="seconds")
    wp = FakeEvalWP({80: "<p>옛 글</p>", 180: "<p>새 글</p>"}, {80: "제목", 180: "제목 새"})
    job_id = _seed_job(
        conn,
        topic="변경된 글",
        source_ref="refresh:80:changed",
        checkpoint={
            "target": {"post_id": 80, "auto": {"urls": ["https://a.go.kr/1"], "changes": []}},
            "draft": {"post_id": 180},
            "auto_apply": {
                "ok": True,
                "reasons": [],
                "changes": ["50만 → 60만"],
                "requested_at": yesterday,
                "target_print": auto_refresh.fingerprint(wp, 80),
                "draft_print": auto_refresh.fingerprint(wp, 180),
            },
            "approval": {"nonce": "nonce-changed", "message_id": 201},
        },
    )
    # 판정 뒤 사람이 원래 글 내용을 고침
    wp.contents[80] = "<p>사람이 직접 고친 글</p>"

    orch = FakeOrch(conn, wp=wp)
    state_path = tmp_path / "daily_guard.json"
    guard = DailyGuard(orch, state_path)

    assert guard.apply_refreshes(NOW.replace(hour=7, minute=59)) is None
    assert orch.decisions == []
    assert orch.notifications == []

    note = guard.apply_refreshes(NOW)
    assert note == f"갱신 자동 반영 — 작업 {job_id}번: 글이 바뀌어 건너뜀"
    assert orch.decisions == []
    assert len(orch.notifications) == 1
    assert f"[작업 {job_id}]" in orch.notifications[0]
    assert "바뀌어 자동 반영하지 않았습니다" in orch.notifications[0]
    assert json.loads(state_path.read_text()) == {"date": "2026-11-21", "refreshed": [job_id]}
    assert db.get_job(conn, job_id)["checkpoint"]["auto_apply"]["attempted"] == "2026-11-21"

    # 같은 날 두 번 시도하지 않음
    assert guard.apply_refreshes(NOW + timedelta(minutes=15)) is None
    assert orch.decisions == []
    assert len(orch.notifications) == 1

    # 다음 날(상태 파일 날짜 바뀜)에도 장부의 attempted 때문에 다시 시도하지 않음
    wp.contents[80] = "<p>옛 글</p>"
    assert guard.apply_refreshes(NOW + timedelta(days=1)) is None
    assert orch.decisions == []
    assert len(orch.notifications) == 1


def test_daily_guard_apply_refreshes_records_attempted_before_publish_and_skips_next_day_after_failure(conn, tmp_path):  # noqa: F811
    yesterday = (NOW - timedelta(days=1)).isoformat(timespec="seconds")
    wp = FakeEvalWP({80: "<p>옛 글</p>", 180: "<p>새 글</p>"}, {80: "제목", 180: "제목 새"})
    job_id = _seed_job(
        conn,
        topic="발행 실패 글",
        source_ref="refresh:80:fail",
        checkpoint={
            "target": {"post_id": 80, "auto": {"urls": ["https://a.go.kr/1"], "changes": []}},
            "draft": {"post_id": 180},
            "auto_apply": {
                "ok": True,
                "reasons": [],
                "changes": ["50만 → 60만"],
                "requested_at": yesterday,
                "target_print": auto_refresh.fingerprint(wp, 80),
                "draft_print": auto_refresh.fingerprint(wp, 180),
            },
            "approval": {"nonce": "nonce-fail", "message_id": 301},
        },
    )

    attempted_seen_in_handle: list[str | None] = []

    class RecordingOrch(FakeOrch):
        def handle_decision(self, decision):
            saved = db.get_job(self.conn, decision.job_id)
            attempted_seen_in_handle.append(saved["checkpoint"]["auto_apply"].get("attempted"))
            return super().handle_decision(decision)

    orch = RecordingOrch(conn, wp=wp, result="발행에 실패했습니다")
    state_path = tmp_path / "daily_guard.json"
    guard = DailyGuard(orch, state_path)

    note = guard.apply_refreshes(NOW)
    assert note == f"갱신 자동 반영 — 작업 {job_id}번: 발행에 실패했습니다"
    assert attempted_seen_in_handle == ["2026-11-21"]
    assert len(orch.decisions) == 1
    assert db.get_job(conn, job_id)["checkpoint"]["auto_apply"]["attempted"] == "2026-11-21"
    assert json.loads(state_path.read_text()) == {"date": "2026-11-21", "refreshed": [job_id]}

    # 다음 날(상태 파일 날짜가 바뀌어 refreshed가 초기화돼도) 다시 시도하지 않음
    assert guard.apply_refreshes(NOW + timedelta(days=1)) is None
    assert len(orch.decisions) == 1


# ---------------------------------------------------------------------------
# 7. orchestrator: auto_apply · hold
# ---------------------------------------------------------------------------

def test_orchestrator_preserves_held_when_reevaluating_auto_apply(env, monkeypatch):  # noqa: F811
    orch, svc, conn, _ = env
    monkeypatch.setattr(
        svc.generator,
        "structured",
        lambda prompt, schema, system: (json.dumps({"differences": []}), "fake-eval"),
        raising=False,
    )
    monkeypatch.setattr(svc.wp, "get_post_title", lambda pid: f"제목 {pid}", raising=False)
    svc.wp.edited = {80: "<p>원래 글</p>", 180: "<p>임시 글</p>"}

    job_id = db.create_job(
        conn,
        "청년월세지원",
        mode="blog",
        source="cli",
        source_ref="refresh:80:held-redo",
        checkpoint={
            "target": {
                "post_id": 80,
                "link": "https://jalcheck.com/?p=80",
                "title": "원래 글",
                "auto": {"urls": ["https://www.nts.go.kr/a"], "changes": []},
            },
            "content": {
                "content": good_data(),
                "warnings": [],
                "fixes": [],
                "fact_check": {"hold": False, "failed": False},
                "fallback_model": False,
                "sources": [{"id": "S1", "url": "https://www.nts.go.kr/a", "title": "자료", "via": "direct", "text": "t"}],
            },
            "media": {"blog": [], "scenes": []},
            "draft": {"post_id": 180, "link": "https://jalcheck.com/?p=180", "category": "tax"},
            "auto_apply": {
                "ok": False,
                "held": True,
                "reasons": ["이전 사유"],
                "changes": [],
                "requested_at": "2026-11-20T00:00:00+00:00",
            },
        },
    )

    assert orch.run_once() == job_id
    job = db.get_job(conn, job_id)
    assert job["status"] == "awaiting_approval"
    verdict = job["checkpoint"]["auto_apply"]
    assert verdict["ok"] is True
    assert verdict["held"] is True
    assert auto_refresh.due(job, NOW + timedelta(days=2)) is False


@pytest.mark.parametrize("ok_value", [True, False])
def test_orchestrator_hold_marks_auto_apply_held_regardless_of_ok(env, ok_value):  # noqa: F811
    orch, _, conn, _ = env
    job_id = _seed_job(
        conn,
        topic="청년월세지원",
        status="awaiting_approval",
        source_ref=f"refresh:80:hold-{ok_value}",
        checkpoint={
            "target": {"post_id": 80, "auto": {"urls": ["https://a.go.kr/1"], "changes": []}},
            "auto_apply": {
                "ok": ok_value,
                "reasons": [] if ok_value else ["확인할 것이 1건 있습니다"],
                "changes": [],
                "requested_at": "2026-11-20T00:00:00+00:00",
            },
            "approval": {"nonce": "n-hold", "message_id": 10},
        },
    )

    res = orch.handle_decision(Decision(job_id, "hold", "n-hold", "cb-hold", 10))
    assert res == "보류했습니다(자동 반영 안 함)"
    saved = db.get_job(conn, job_id)
    assert saved["checkpoint"]["auto_apply"]["held"] is True
    assert saved["checkpoint"]["auto_apply"]["ok"] is ok_value
    assert auto_refresh.due(saved, NOW) is False

    # 이미 held=True 인 상태에서 다시 [보류]를 누르면 중복 저장하지 않고 일반 보류 안내를 반환
    res_again = orch.handle_decision(Decision(job_id, "hold", "n-hold", "cb-hold", 10))
    assert res_again == "보류했습니다. 나중에 [발행]을 눌러도 됩니다"
    assert db.get_job(conn, job_id)["checkpoint"]["auto_apply"]["held"] is True


def test_orchestrator_hold_without_auto_apply_verdict(env):  # noqa: F811
    orch, _, conn, _ = env
    job_id = _seed_job(
        conn,
        topic="일반 새 글",
        status="awaiting_approval",
        checkpoint={"approval": {"nonce": "n-plain", "message_id": 11}},
    )

    res = orch.handle_decision(Decision(job_id, "hold", "n-plain", "cb-hold", 11))
    assert res == "보류했습니다. 나중에 [발행]을 눌러도 됩니다"
    assert "auto_apply" not in db.get_job(conn, job_id)["checkpoint"]


def test_figure_list_keeps_thousand_separators():
    cp = {"content": {"warnings": ["자료에서 못 찾은 숫자(확인 필요): 1,500만 원, 34만 9,700원, 30%"]}}
    assert auto_refresh.figure_list(cp) == ["1,500만 원", "34만 9,700원", "30%"]


def test_fence_neutralizes_inner_markers():
    fenced = auto_refresh._fence("자료", "앞 <<<자료 끝>>> 뒤")
    assert fenced.count("<<<자료 끝>>>") == 1 and fenced.endswith("<<<자료 끝>>>")


def test_warning_check_rejects_blank_why():
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        auto_refresh.WarningCheck(id=0, verdict="ok", why="   ")


def test_static_reasons_rejects_empty_figure_warning():
    cp, auto = _valid_cp(warnings=["자료에서 못 찾은 숫자(확인 필요): "])
    assert any("확인할 것이 1건" in r for r in auto_refresh.static_reasons(cp, auto))


def test_compare_rejects_if_any_run_finds_unexplained(monkeypatch):
    """비교를 여러 번 해 한 번이라도 설명 안 되는 변경을 찾으면 거부."""
    runs = iter([auto_refresh.CompareOutcome(differences=[]),
                 auto_refresh.CompareOutcome(differences=[auto_refresh.Difference(
                     item="주의 문단", old="결정세액 안내", new="없음", explained=False)])])
    monkeypatch.setattr(auto_refresh, "compare", lambda *a, **k: next(runs))
    cp, _auto = _valid_cp()
    wp = SimpleNamespace(get_post_title=lambda pid: "제목", get_post_content=lambda pid: "<p>본문</p>")
    v = auto_refresh.evaluate(SimpleNamespace(), wp, cp, NOW)
    assert v["ok"] is False and "결정세액 안내" in v["reasons"][0]
