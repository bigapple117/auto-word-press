from datetime import datetime, timezone

import pytest

from src import db, news_gate
from src.daily_guard import KST, DailyGuard, auto_hold
from src.news_gate import Checker, Verdict
from src.topic_finder import load_seeds
from tests.test_orchestrator import env  # noqa: F401 - 실행기 가짜 환경 재사용

NOW = datetime(2026, 11, 23, 3, 0, tzinfo=timezone.utc)


def origin(**kw):
    return {"news": True, "name": "청년 이사비 지원", "query": "청년 이사비 지원 신청", "category": "지원금",
            "scope": "전국", "region": "", "field": "기본", "agency": "국토교통부", "group": "",
            "compare_with": [], "news_titles": ["청년 이사비 지원 신청 시작"], **kw}


def cp(sources=None, warnings=(), **o):
    sources = sources if sources is not None else [
        {"id": "S1", "url": "https://www.molit.go.kr/a", "title": "안내", "text": "본문", "via": "direct"},
        {"id": "S2", "url": "https://www.bokjiro.go.kr/b", "title": "신청", "text": "본문", "via": "gemini"}]
    return {"topic_origin": origin(**o),
            "content": {"content": {"title": "제목"}, "sources": sources, "warnings": list(warnings)}}


def checker(verdict=Verdict(exists=True, open_now=True, reason="공식 공고 확인"), allowed=True, codex_fail=False,
            gemini_fail=False):
    calls = []

    def codex(prompt):
        calls.append("codex")
        if codex_fail:
            raise RuntimeError("codex 오류")
        return verdict

    def gemini(prompt):
        calls.append("gemini")
        if gemini_fail:
            raise RuntimeError("agy 오류")
        return verdict

    return Checker(lambda: allowed, codex, gemini), calls


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "jobs.db")


def test_origin_and_region_tags():
    assert news_gate.origin({"checkpoint": {}}) is None
    assert news_gate.origin({"topic_origin": {"news": False}}) is None
    assert news_gate.origin(cp())["name"] == "청년 이사비 지원"
    assert news_gate.region_tags(cp()) == []
    assert news_gate.region_tags(cp(scope="광역", region="서울, 경기")) == ["서울", "경기"]


def test_official_hosts_counts_distinct_official_direct_sources_only():
    sources = [{"url": "https://www.molit.go.kr/a", "via": "direct"},
               {"url": "https://molit.go.kr/b", "via": "direct"},          # 같은 사이트
               {"url": "https://news.example.com/x", "via": "direct"},     # 공식 아님
               {"url": "https://www.bokjiro.go.kr/n", "via": "news"},      # 뉴스 인용은 안 셈
               {"url": "https://www.law.go.kr/l", "via": "law"}]           # 법령 조문은 안 셈
    assert news_gate.official_hosts(sources) == ["molit.go.kr"]


def test_passes_when_all_conditions_met(conn):
    ch, calls = checker()
    gate = news_gate.evaluate(conn, "주제", cp(), ch, NOW)
    assert gate["ok"] is True and gate["checked_by"] == "Codex" and calls == ["codex"]
    assert news_gate.approval_line(gate).startswith(news_gate.READY)


@pytest.mark.parametrize("kw, reason", [
    ({"sources": [{"url": "https://www.molit.go.kr/a", "via": "direct"}]}, "공식 사이트 자료 1곳"),
    ({"warnings": ["자료에서 못 찾은 숫자: 30만 원, 1,500만 원"]}, "못 찾은 숫자 2개"),
    ({"field": "주거"}, "새 분야(주거) 첫 3편 중 1번째"),
])
def test_static_failures_skip_ai(conn, kw, reason):
    ch, calls = checker()
    gate = news_gate.evaluate(conn, "주제", cp(**kw), ch, NOW)
    assert gate["ok"] is False and calls == []
    line = news_gate.approval_line(gate)
    assert line.startswith(news_gate.MANUAL) and reason in line and "AI 확인은 생략" in line


def test_new_field_auto_after_three_published(conn):
    for i in range(3):
        jid = db.create_job(conn, f"주거 {i}", checkpoint={"topic_origin": origin(field="주거")})
        conn.execute("UPDATE jobs SET status = 'published' WHERE id = ?", (jid,))
    conn.commit()
    assert news_gate.published_in_field(conn, "주거") == 3
    gate = news_gate.evaluate(conn, "주제", cp(field="주거"), checker()[0], NOW)
    assert gate["ok"] is True


def test_codex_blocked_uses_gemini(conn):
    ch, calls = checker(allowed=False)
    gate = news_gate.evaluate(conn, "주제", cp(), ch, NOW)
    assert gate["ok"] is True and gate["checked_by"] == "Gemini" and calls == ["gemini"]


def test_codex_error_falls_back_to_gemini(conn):
    ch, calls = checker(codex_fail=True)
    gate = news_gate.evaluate(conn, "주제", cp(), ch, NOW)
    assert gate["checked_by"] == "Gemini" and calls == ["codex", "gemini"] and gate["ok"] is True


def test_both_ai_fail_goes_manual(conn):
    ch, calls = checker(codex_fail=True, gemini_fail=True)
    gate = news_gate.evaluate(conn, "주제", cp(), ch, NOW)
    assert gate["ok"] is False and "다른 회사 AI 확인 실패" in news_gate.approval_line(gate)


@pytest.mark.parametrize("verdict, reason", [
    (Verdict(exists=False, open_now=False, reason="민간 상품"), "실제 운영 제도인지"),
    (Verdict(exists=True, open_now=False, reason="10월 마감"), "지금 신청 가능한지"),
])
def test_ai_says_no(conn, verdict, reason):
    gate = news_gate.evaluate(conn, "주제", cp(), checker(verdict)[0], NOW)
    line = news_gate.approval_line(gate)
    assert gate["ok"] is False and reason in line and verdict.reason in line


def test_parse_finds_json_in_text():
    v = news_gate._parse('설명\n{"exists": true, "open_now": false, "reason": "마감"}\n끝')
    assert v.exists and not v.open_now
    with pytest.raises(ValueError):
        news_gate._parse("JSON 없음")


def test_codex_gate_reads_usage_tool():
    class R:
        def __init__(self, out, code=0):
            self.stdout, self.returncode = out, code
    assert news_gate.codex_allowed(lambda *a, **k: R("허용 10% (상한 50%)")) is True
    assert news_gate.codex_allowed(lambda *a, **k: R("막음 60% (상한 50%)")) is False

    def boom(*a, **k):
        raise OSError("ssh 없음")
    assert news_gate.codex_allowed(boom) is False


def test_hold():
    assert news_gate.hold({"checkpoint": {}}) is False                               # 뉴스 주제 아님
    assert news_gate.hold({"checkpoint": cp()}) is True                              # 판정 기록 없음
    assert news_gate.hold({"checkpoint": {**cp(), "news_gate": {"ok": False}}}) is True
    assert news_gate.hold({"checkpoint": {**cp(), "news_gate": {"ok": True}}}) is False
    assert auto_hold({"checkpoint": cp()}) is True


def test_remember_published(tmp_path):
    path = tmp_path / "topic_seeds_news.txt"
    job = {"checkpoint": cp(scope="광역", region="서울", field="주거")}
    assert news_gate.remember_published(path, job) is True
    assert path.read_text().startswith("#")
    seeds = load_seeds(extra=path)
    s = next(s for s in seeds if s.name == "청년 이사비 지원")
    assert (s.scope, s.region, s.field) == ("광역", "서울", "주거")
    assert news_gate.remember_published(path, job) is False                         # 이미 있음
    assert news_gate.remember_published(path, {"checkpoint": cp(name="A 지역별 비교", compare_with=["서울"])}) is False
    assert news_gate.remember_published(path, {"checkpoint": {}}) is False


# ---------- 실행기·아침 자동 발행 연결 ----------

def test_orchestrator_holds_news_job_and_shows_reason(env, tmp_path):  # noqa: F811
    orch, svc, conn, _ = env
    ch, calls = checker()
    orch.news_checker = ch
    plain = db.create_job(conn, "일반 주제")
    news = db.create_job(conn, "청년 이사비 지원 신청", checkpoint={"topic_origin": origin()})
    orch.run_once()
    orch.run_once()
    job = db.get_job(conn, news)
    gate = job["checkpoint"]["news_gate"]
    # 가짜 자료는 nts.go.kr 한 곳뿐 → 공식 자료 부족으로 버튼 승인, AI는 부르지 않음
    assert job["status"] == "awaiting_approval" and gate["ok"] is False and calls == []
    warnings = next(r[2] for r in svc.telegram.requests if r[0] == news)
    assert warnings[0].startswith(news_gate.MANUAL) and "공식 사이트 자료 1곳" in warnings[0]
    plain_warnings = next(r[2] for r in svc.telegram.requests if r[0] == plain)
    assert not any(news_gate.MANUAL in w or news_gate.READY in w for w in plain_warnings)

    g = DailyGuard(orch, tmp_path / "daily_guard.json")
    assert g.run_if_due(datetime(2026, 11, 24, 8, 0, tzinfo=KST)) == f"자동 발행: 작업 {plain}번"
    assert db.get_job(conn, news)["status"] == "awaiting_approval"


def test_orchestrator_passing_news_job_is_auto_published_and_remembered(env, tmp_path):  # noqa: F811
    orch, svc, conn, _ = env
    orch.news_checker = checker()[0]
    news = db.create_job(conn, "청년 이사비 지원 신청", checkpoint={"topic_origin": origin()})
    orch.run_once()
    gate = db.get_job(conn, news)["checkpoint"]["news_gate"]
    if gate["ok"] is False:                       # 가짜 자료가 한 사이트라 통과시키려면 판정을 직접 넣는다
        db.save_checkpoint(conn, news, "news_gate", {**gate, "ok": True, "reasons": []})
    g = DailyGuard(orch, tmp_path / "daily_guard.json")
    assert g.run_if_due(datetime(2026, 11, 24, 8, 0, tzinfo=KST)) == f"자동 발행: 작업 {news}번"
    assert db.get_job(conn, news)["status"] == "published"
    path = orch.svc.settings.data_dir / "topic_seeds_news.txt"
    assert "청년 이사비 지원" in path.read_text()
