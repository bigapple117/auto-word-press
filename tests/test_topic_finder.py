from datetime import date

import pytest

from src.source_finder import SearchError, SearchHit
from src.topic_finder import (ANCHOR_QUERY, Candidate, Discovered, DiscoveredList, PhrasedList, Seed,
                              TopicWriter, Written, fallback_topic, find_candidates, is_local_news,
                              load_seeds, measure_interest, pick, valid_topic, validate_discovered,
                              written_from_jobs)

TODAY = date(2026, 11, 6)
NEWS_DATE = "Wed, 04 Nov 2026 09:00:00 +0900"


def seed(name, query=None, months=(), cat="지원금", aliases=(), **kw):
    return Seed(name, query or name, tuple(months), cat, tuple(aliases), **kw)


class FakeSearch:
    """질의 → 결과. 정해 두지 않은 질의는 빈 결과."""

    def __init__(self, web=None, news=None, trend=None, fail=()):
        self.web_map, self.news_map, self.trend_map = web or {}, news or {}, trend or {}
        self.fail = set(fail)
        self.calls = []

    def web(self, q, display=20):
        self.calls.append(("web", q))
        if q in self.fail:
            raise SearchError("boom")
        return self.web_map.get(q, [])

    def news(self, q, display=10):
        self.calls.append(("news", q))
        return self.news_map.get(q, [])

    def trend(self, keywords, start, end, time_unit="month"):
        self.calls.append(("trend", tuple(keywords)))
        return {k: self.trend_map[k] for k in keywords if k in self.trend_map}


def gov(title, url="https://www.mohw.go.kr/a", desc=""):
    return SearchHit(title, url, desc)


def news(title, published=NEWS_DATE):
    return SearchHit(title, "https://news.example/1", "", published)


def series(values):
    """13개월 이전부터 이번 달 직전까지(2025-10 ~ 2026-10) 값."""
    months = [f"2025-{m:02d}-01" for m in (10, 11, 12)] + [f"2026-{m:02d}-01" for m in range(1, 11)]
    return list(zip(months, values))


def test_load_seeds_reads_project_file_and_rejects_bad_lines(tmp_path):
    seeds = load_seeds()
    names = {s.name for s in seeds}
    assert {"기초연금", "고향사랑기부제", "K-패스"} <= names
    assert all(s.category in ("지원금", "세금", "절약") for s in seeds)
    bad = tmp_path / "s.txt"
    bad.write_text("기초연금 | 기초연금 | 13 | 지원금 |\n", encoding="utf-8")
    with pytest.raises(ValueError, match="1~12"):
        load_seeds(bad)
    bad.write_text("기초연금 | 기초연금 | | 보험 |\n", encoding="utf-8")
    with pytest.raises(ValueError, match="형식"):
        load_seeds(bad)


def test_written_from_jobs_matches_aliases_and_years_and_skips_canceled():
    seeds = [seed("연금저축·IRP 세액공제", aliases=("연금저축", "IRP")), seed("자녀장려금"), seed("기초연금")]
    jobs = [
        {"id": 1, "topic": "2025년 자녀장려금 신청 자격", "status": "published", "post_id": 35,
         "created_at": "2026-11-04T04:17:07+00:00"},
        {"id": 8, "topic": "연금저축·IRP 연말 세액공제 한도", "status": "awaiting_approval", "post_id": 76,
         "created_at": "2026-11-04T09:45:39+00:00"},
        {"id": 9, "topic": "기초연금 받는 법", "status": "canceled", "post_id": None,
         "created_at": "2026-11-04T09:45:39+00:00"},
    ]
    got = written_from_jobs(jobs, seeds)
    assert [(w.name, w.year, w.job_id) for w in got] == [("자녀장려금", 2025, 1), ("연금저축·IRP 세액공제", 2026, 8)]
    assert got[1].written_on == date(2026, 11, 4)


def test_measure_interest_scales_by_anchor_period_mean_and_flags_season():
    s = FakeSearch(trend={
        ANCHOR_QUERY: series([50] * 13),
        "연말정산": series([10, 80, 90] + [10] * 10),          # 작년 11·12월 급증
        "기초연금": series([25] * 13),
    })
    out = measure_interest(s, ["연말정산", "기초연금"], TODAY)
    assert out["기초연금"] == (50.0, False)              # 25 / 평균 50 × 100
    assert out["연말정산"][1] is True
    assert s.calls[0] == ("trend", (ANCHOR_QUERY, "연말정산", "기초연금"))


def test_measure_interest_splits_into_groups_of_four_and_skips_zero_anchor():
    qs = [f"q{i}" for i in range(6)]
    s = FakeSearch(trend={ANCHOR_QUERY: series([0] * 13), **{q: series([5] * 13) for q in qs}})
    out = measure_interest(s, qs, TODAY)
    trend_calls = [c for c in s.calls if c[0] == "trend"]
    assert [len(c[1]) for c in trend_calls] == [5, 3]
    assert out == {}                                     # 기준어가 0이면 비교 불가


def test_valid_topic_rejects_invented_numbers_and_missing_name():
    c = Candidate(seed("기초연금"), "new", 2026)
    assert valid_topic("2026년 기초연금 신청 자격과 받는 금액", c)
    assert valid_topic("기초연금 신청 자격과 받는 금액 계산법", c)
    assert not valid_topic("기초연금 월 34만 원 받는 조건 정리", c)     # 지어낸 금액
    assert not valid_topic("노인 연금 신청 자격과 받는 금액", c)        # 제도명 없음
    assert not valid_topic("기초연금", c)                               # 너무 짧음
    assert fallback_topic(c) == "기초연금 신청 자격과 받는 방법"
    assert fallback_topic(Candidate(seed("연말정산", cat="세금"), "new_year", 2027)).startswith("2027년 연말정산")


def test_validate_discovered_keeps_only_names_in_titles_national_and_new():
    titles = ["스포츠강좌이용권 신청 접수 시작", "평창군 청소년 지원", "기초연금 인상"]
    items = [
        Discovered(ids=[0], name="스포츠강좌이용권", query="스포츠강좌이용권", category="지원금", national=True),
        Discovered(ids=[1], name="평창 청소년 바우처", query="평창 바우처", category="지원금", national=True),
        Discovered(ids=[1], name="평창군 청소년 지원", query="x", category="지원금", national=False),
        Discovered(ids=[2], name="기초연금", query="기초연금", category="지원금", national=True),
        Discovered(ids=[9], name="없는제도", query="없는제도", category="지원금", national=True),
    ]
    out = validate_discovered(items, titles, [seed("기초연금")])
    assert [(s.name, s.discovered, s.news_titles) for s in out] == [
        ("스포츠강좌이용권", True, ("스포츠강좌이용권 신청 접수 시작",))]


def test_pick_respects_limit_category_cap_and_update_cap():
    def cand(name, cat, sc, kind="new"):
        c = Candidate(seed(name, cat=cat), kind, 2026)
        c.score = sc
        return c
    cands = [cand(f"세금{i}", "세금", 10 - i) for i in range(8)] + [cand("지원", "지원금", 1)]
    got = pick(cands, limit=10, cap=7)
    assert sum(c.seed.category == "세금" for c in got) == 7 and got[-1].seed.name == "지원"
    ups = [cand(f"u{i}", "지원금", 5, kind="update") for i in range(5)]
    assert sum(c.kind == "update" for c in pick(ups, limit=10, max_updates=3)) == 3


@pytest.mark.parametrize("title, local", [
    ("부산시, 소상공인 에너지바우처 사용처 확대", True),
    ("[수원시] 청년 지원", True),
    ("서울 청년월세 확대", True),
    ("정부, 에너지바우처 지원 확대", False),
    ("에너지바우처 단가 인상", False),
])
def test_is_local_news(title, local):
    assert is_local_news(title) is local


def written(name, year=2026, post_id=70, on=date(2026, 11, 1)):
    return Written(name, year, 6, post_id, on)


def test_classification_new_update_and_skip():
    seeds = [seed("기초연금"), seed("에너지바우처"), seed("연말정산", cat="세금"), seed("부모급여")]
    s = FakeSearch(
        web={"2026년 기초연금": [gov("기초연금 안내"), SearchHit("블로그", "https://blog.naver.com/x", "")]},
        news={
            "에너지바우처": [news("에너지바우처 지원 단가 인상"), news("에너지바우처 지원 대상 확대"),
                         news("부산시, 소상공인 에너지바우처 확대")],
            "연말정산": [news("연말정산 공제 확대 검토"), news("연말정산 달라진 점", "Mon, 26 Oct 2026 09:00:00 +0900")],
        },
    )
    result = find_candidates(s, None, seeds, [written("에너지바우처"), written("연말정산")], today=TODAY)
    by_name = {c.seed.name: c for c in result.candidates}
    assert by_name["기초연금"].kind == "new" and by_name["기초연금"].official_hosts == ["mohw.go.kr"]
    assert by_name["에너지바우처"].kind == "update"
    assert by_name["에너지바우처"].news_titles == ["에너지바우처 지원 단가 인상", "에너지바우처 지원 대상 확대"]
    assert by_name["에너지바우처"].topic == "2026년 에너지바우처 글 내용 확인"
    assert "연말정산" not in by_name        # 검토 기사·글 쓰기 전 기사뿐
    assert "부모급여" not in by_name        # 공식 자료 없음
    assert any("2개뿐" in n for n in result.notes)


def test_new_year_only_after_year_turns_and_with_year_in_official_title():
    seeds = [seed("고향사랑기부제", cat="세금")]
    hits = {"2027년 고향사랑기부제": [gov("2027년 고향사랑기부제 세액공제 안내", "https://www.mois.go.kr/a")]}
    old = [Written("고향사랑기부제", 2026, 3, 57, date(2026, 11, 4))]
    # 2026년 11월: 아직 해가 안 바뀜 → 새 연도 글 아님(검색도 안 함)
    s = FakeSearch(web=hits)
    assert find_candidates(s, None, seeds, old, today=TODAY).candidates == []
    assert ("web", "2027년 고향사랑기부제") not in s.calls
    # 2027년 1월: 공식 자료 제목에 2027년 → 새 연도 글
    got = find_candidates(FakeSearch(web=hits), None, seeds, old, today=date(2027, 1, 10)).candidates
    assert [(c.kind, c.year, c.written.post_id) for c in got] == [("new_year", 2027, 57)]
    # 제목에 연도가 없으면 인정 안 함
    weak = {"2027년 고향사랑기부제": [gov("고향사랑기부제 안내", desc="2027년 계획")]}
    assert find_candidates(FakeSearch(web=weak), None, seeds, old, today=date(2027, 1, 10)).candidates == []


def test_skip_keys_and_end_warning_and_search_failure():
    seeds = [seed("기초연금"), seed("부모급여")]
    s = FakeSearch(web={"2026년 기초연금": [gov("기초연금")], "2026년 부모급여": [gov("부모급여")]},
                   news={"부모급여": [news("부모급여 신청 마감 임박")]}, fail={"2026년 기초연금"})
    got = find_candidates(s, None, seeds, [], today=TODAY).candidates
    assert [c.seed.name for c in got] == ["부모급여"]           # 검색 실패한 기초연금은 제외
    assert any("종료·마감" in w for w in got[0].warnings)
    s2 = FakeSearch(web={"2026년 부모급여": [gov("부모급여")]})
    assert find_candidates(s2, None, seeds, [], today=TODAY, skip_keys=["부모급여:2026:new"]).candidates == []


def test_budget_stops_and_reports_unchecked():
    seeds = [seed(f"제도{i}") for i in range(5)]
    s = FakeSearch(web={f"2026년 제도{i}": [gov(f"제도{i}")] for i in range(5)})
    result = find_candidates(s, None, seeds, [], today=TODAY, max_calls=4)
    assert len(result.candidates) == 2
    assert any("3개 제도는 확인 못 함" in n for n in result.notes)
    assert result.api_calls == 4


class FakeGen:
    def __init__(self, replies, fail=False):
        self.replies, self.fail, self.prompts = list(replies), fail, []

    def _call_with_retry(self, prompt, schema=None, system=None):
        self.prompts.append((schema, prompt))
        if self.fail:
            raise RuntimeError("busy")
        return self.replies.pop(0), "m"


def test_writer_discovers_and_phrases_with_validation_and_fallback():
    seeds = [seed("기초연금")]
    disc = DiscoveredList(items=[Discovered(ids=[0], name="스포츠강좌이용권", query="스포츠강좌이용권",
                                            category="지원금", national=True)]).model_dump_json()
    phrased = PhrasedList(items=[
        {"key": "기초연금:2026:new", "topic": "기초연금 월 34만 원 받는 법"},          # 숫자 지어냄 → 틀 문장
        {"key": "스포츠강좌이용권:2026:new", "topic": "스포츠강좌이용권 지원 대상과 신청 방법"},
    ]).model_dump_json()
    gen = FakeGen([disc, phrased])
    s = FakeSearch(
        web={"2026년 기초연금": [gov("기초연금")], "2026년 스포츠강좌이용권": [gov("스포츠강좌이용권")]},
        news={"지원금 신청 시작": [news("스포츠강좌이용권 신청 접수 시작")]},
    )
    result = find_candidates(s, TopicWriter(gen), seeds, [], today=TODAY)
    topics = {c.seed.name: c.topic for c in result.candidates}
    assert topics == {"기초연금": "기초연금 신청 자격과 받는 방법",
                      "스포츠강좌이용권": "스포츠강좌이용권 지원 대상과 신청 방법"}
    assert result.candidates[0].seed.discovered            # 새 제도 가점으로 앞에 옴
    assert "뉴스에서 새 제도 1개 발견" in result.notes


def test_writer_failure_falls_back_to_seeds_and_template():
    s = FakeSearch(web={"2026년 기초연금": [gov("기초연금")]},
                   news={"지원금 신청 시작": [news("스포츠강좌이용권 신청 접수 시작")]})
    result = find_candidates(s, TopicWriter(FakeGen([], fail=True)), [seed("기초연금")], [], today=TODAY)
    assert [c.topic for c in result.candidates] == ["기초연금 신청 자격과 받는 방법"]
    assert any("기본 목록만" in n for n in result.notes) and any("틀 문장" in n for n in result.notes)
