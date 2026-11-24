"""최근 변경(개편안) 찾기: 확정/논의 중 구분과 인용 확인."""
from datetime import date
from types import SimpleNamespace

from src.recent_change import core_keyword, find_recent_changes, is_final, pick_articles
from src.source_fetcher import Source
from src.source_finder import SearchHit
from templates.prompt_template import CHANGE_NOTE, build_prompt

TODAY = date(2026, 11, 21)
PUB = "Tue, 17 Nov 2026 09:00:00 +0900"
ARTICLE = ("소득세법 개정안이 2일 국회 본회의를 통과했다. 개정안에 따라 2027년 1월 1일부터 월세 세액공제 한도가 "
           "연 1000만원에서 1200만원으로 늘어난다. 한편 정부는 공제율 인상도 검토하고 있다.")


class FakeSearch:
    def __init__(self, hits):
        self.hits = hits
        self.queries = []

    def news(self, query, display=10):
        self.queries.append(query)
        return list(self.hits)


def hit(url, title, desc="", published=PUB):
    return SearchHit(title, url, desc, published)


def fake_fetch(texts):
    def fetch(urls, max_sources=None):
        return [Source("S1", u, "제목", texts[u]) for u in urls if u in texts], []
    return fetch


def item(sid, status, change, proof=""):
    return SimpleNamespace(source_id=sid, status=status, change=change, proof=proof)


def test_core_keyword():
    assert core_keyword("월세 세액공제 신청 대상과 공제 금액 계산법") == "월세 세액공제"
    assert core_keyword("부모급여 지원 대상과 신청 방법") == "부모급여"
    assert core_keyword("K-패스 환급 대상과 신청 방법") == "K-패스"
    assert core_keyword("근로장려금과 자녀장려금 신청") == "근로장려금"


def test_is_final():
    assert is_final("소득세법 개정안이 2일 국회 본회의를 통과했다.")
    assert is_final("고용노동부는 2027년 최저임금을 시간당 1만 원으로 고시했다")
    assert not is_final("국회를 통과하면 내년부터 적용된다")
    assert not is_final("개정안은 국회 본회의를 통과한 뒤 시행될 예정이다")
    assert not is_final("정부는 공제율 인상을 검토하고 있다")
    assert is_final("이 제도는 지난 7월부터 시행 중이다")
    assert not is_final("개정안이 국회 상임위를 통과했다")
    assert not is_final("국무회의에서 의결된 개정안이 국회에 제출됐다")
    assert not is_final("개정안은 국회 본회의를 통과돼야 효력이 생긴다")


def test_pick_articles_filters_old_and_unrelated_and_ranks_briefing_first():
    search = FakeSearch([
        hit("https://news.example/a", "월세 세액공제 한도 확대"),
        hit("https://www.korea.kr/b", "직장인 지원 TF", "월세 세액공제 확대 논의"),
        hit("https://news.example/old", "월세 세액공제 개편", published="Mon, 06 Nov 2023 09:00:00 +0900"),
        hit("https://news.example/x", "코스피 급락", "부동산 세금"),
    ])
    urls = [h.url for h, _ in pick_articles(search, "월세 세액공제 신청 대상", TODAY)]
    assert urls == ["https://www.korea.kr/b", "https://news.example/a"]
    assert search.queries[:3] == ["월세 세액공제 개편", "월세 세액공제 개정", "월세 세액공제 달라지는"]
    assert f"{TODAY.year}년 달라지는 월세 세액공제" in search.queries


def test_pick_articles_prefers_annual_change_roundup():
    """최신 논쟁 기사보다 '2026년 이렇게 달라진다' 같은 확정 변경 정리 기사를 먼저 읽는다."""
    search = FakeSearch([
        hit("https://news.example/debate", "실업급여 개편 논란", published="Mon, 23 Nov 2026 09:00:00 +0900"),
        hit("https://news.example/roundup", "2026년 이렇게 달라진다② 최저임금·실업급여",
            published="Fri, 30 Jan 2026 09:00:00 +0900"),
    ])
    urls = [h.url for h, _ in pick_articles(search, "실업급여 신청 자격", date(2026, 11, 24))]
    assert urls == ["https://news.example/roundup", "https://news.example/debate"]
    assert "2027년 달라지는 실업급여" in search.queries   # 연말엔 다음 해 정리 기사도


def test_confirmed_change_becomes_source_with_quotes_only():
    url = "https://news.example/a"
    change = "2027년 1월 1일부터 월세 세액공제 한도가 연 1000만원에서 1200만원으로 늘어난다."
    proof = "소득세법 개정안이 2일 국회 본회의를 통과했다."
    read = lambda topic, arts, dates: [item("N1", "confirmed", change, proof)]
    r = find_recent_changes(FakeSearch([hit(url, "월세 세액공제 한도 확대")]), fake_fetch({url: ARTICLE}),
                            read, "월세 세액공제 신청", 5, today=TODAY)
    assert [s.id for s in r.sources] == ["S5"] and r.sources[0].via == "news"
    assert change in r.sources[0].text and proof in r.sources[0].text and "검토" not in r.sources[0].text
    assert r.memo["confirmed"][0]["date"] == "2026-11-17"
    assert r.notes[0].startswith("최근 변경(확정) 반영")


def test_conditional_or_unquoted_proof_is_pending():
    url = "https://news.example/a"
    change = "2027년 1월 1일부터 월세 세액공제 한도가 연 1000만원에서 1200만원으로 늘어난다."
    for proof in ["정부는 공제율 인상도 검토하고 있다.", "개정안이 국회 본회의를 통과했다고 밝혔다."]:
        read = lambda topic, arts, dates: [item("N1", "confirmed", change, proof)]
        r = find_recent_changes(FakeSearch([hit(url, "월세 세액공제 한도 확대")]), fake_fetch({url: ARTICLE}),
                                read, "월세 세액공제 신청", 5, today=TODAY)
        assert r.sources == [] and len(r.memo["pending"]) == 1
        assert r.notes[0].startswith("최근 변경(논의 중, 글에 안 씀)")


def test_pending_notes_one_line_per_article():
    url = "https://news.example/a"
    read = lambda topic, arts, dates: [
        item("N1", "pending", "2027년 1월 1일부터 월세 세액공제 한도가 연 1000만원에서 1200만원으로 늘어난다."),
        item("N1", "pending", "정부는 공제율 인상도 검토하고 있다.")]
    r = find_recent_changes(FakeSearch([hit(url, "월세 세액공제 한도 확대")]), fake_fetch({url: ARTICLE}),
                            read, "월세 세액공제 신청", 5, today=TODAY)
    assert len(r.memo["pending"]) == 2 and len(r.notes) == 1


def test_cut_proof_and_conditional_change_are_pending():
    url = "https://news.example/a"
    art = ("개정안이 국회 본회의를 통과했다면 내년부터 월세 세액공제 한도가 1200만원으로 늘어날 예정이다. "
           "정부는 공제율을 20%로 올리는 방안도 검토하고 있다. 소득세법 개정안이 2일 국회 본회의를 통과했다.")
    cases = [("내년부터 월세 세액공제 한도가 1200만원으로 늘어날", "개정안이 국회 본회의를 통과했다"),
             ("정부는 공제율을 20%로 올리는 방안도 검토하고 있다.", "소득세법 개정안이 2일 국회 본회의를 통과했다.")]
    for change, proof in cases:
        read = lambda topic, arts, dates: [item("N1", "confirmed", change, proof)]
        r = find_recent_changes(FakeSearch([hit(url, "월세 세액공제 한도 확대")]), fake_fetch({url: art}),
                                read, "월세 세액공제 신청", 5, today=TODAY)
        assert r.sources == [] and len(r.memo["pending"]) == 1, change


def test_redirect_to_known_source_is_skipped():
    url, final = "https://news.example/a", "https://www.korea.kr/official"
    def fetch(urls, max_sources=None):
        return [Source("S1", final, "제목", ARTICLE)], []
    called = []
    read = lambda topic, arts, dates: called.append(arts) or []
    r = find_recent_changes(FakeSearch([hit(url, "월세 세액공제 한도 확대")]), fetch, read,
                            "월세 세액공제 신청", 5, skip_urls=[final], today=TODAY)
    assert r.memo["articles"] == [] and called == []


def test_invented_change_is_dropped():
    url = "https://news.example/a"
    read = lambda topic, arts, dates: [item("N1", "confirmed", "월세 세액공제율이 20%로 오른다",
                                            "소득세법 개정안이 2일 국회 본회의를 통과했다.")]
    r = find_recent_changes(FakeSearch([hit(url, "월세 세액공제 한도 확대")]), fake_fetch({url: ARTICLE}),
                            read, "월세 세액공제 신청", 5, today=TODAY)
    assert r.sources == [] and r.memo["confirmed"] == [] and r.memo["pending"] == [] and r.notes == []


def test_failures_never_stop_writing():
    url = "https://news.example/a"
    def boom(*a, **k):
        raise RuntimeError("x")
    r = find_recent_changes(FakeSearch([hit(url, "월세 세액공제 한도 확대")]), fake_fetch({url: ARTICLE}),
                            boom, "월세 세액공제 신청", 5, today=TODAY)
    assert r.sources == [] and r.errors
    r = find_recent_changes(FakeSearch([hit(url, "월세 세액공제 한도 확대")]), boom,
                            boom, "월세 세액공제 신청", 5, today=TODAY)
    assert r.sources == [] and r.memo["fetch_errors"]


def test_prompt_explains_news_source():
    srcs = [Source("S1", "https://gov.example", "안내", "한도 1000만원"),
            Source("S2", "https://news.example/a", "기사", "- 변경: …", "news")]
    p = build_prompt("월세 세액공제", srcs, 60, today=TODAY)
    assert CHANGE_NOTE in p and '종류="최근 확정 변경"' in p
    assert CHANGE_NOTE not in build_prompt("월세 세액공제", srcs[:1], 60, today=TODAY)


def test_roundup_year_only_this_year_or_next_in_late_year():
    from src.recent_change import roundup_year
    assert roundup_year("2026년 이렇게 달라진다② 최저임금·실업급여", TODAY) == 2026
    assert roundup_year("2027년부터 달라지는 정부 정책은?", TODAY) == 2027
    assert roundup_year("2025년 달라지는 것들", TODAY) is None
    assert roundup_year("2027년부터 달라지는 정부 정책", date(2026, 5, 1)) is None
    assert roundup_year("실업급여 개편 논의", TODAY) is None


ROUNDUP = ("새해부터 달라지는 제도를 정리했다. 구직급여(실업급여) 하루 상한액은 기존 6만6000원에서 6만8100원으로 오른다. "
           "정부는 반복 수급자 감액도 검토하고 있다.")


def test_roundup_article_future_tense_change_is_confirmed():
    url = "https://news.example/r"
    change = "구직급여(실업급여) 하루 상한액은 기존 6만6000원에서 6만8100원으로 오른다."
    read = lambda topic, arts, dates: [item("N1", "pending", change)]
    r = find_recent_changes(FakeSearch([hit(url, "2026년 이렇게 달라진다② 최저임금·실업급여")]),
                            fake_fetch({url: ROUNDUP}), read, "실업급여 신청", 5, today=TODAY)
    assert [s.via for s in r.sources] == ["news"] and change in r.sources[0].text
    assert len(r.memo["confirmed"]) == 1 and r.memo["pending"] == []


def test_roundup_article_conditional_or_unquoted_change_stays_pending():
    url = "https://news.example/r"
    for change in ["정부는 반복 수급자 감액도 검토하고 있다.", "하루 상한액은 7만원으로 오른다."]:
        read = lambda topic, arts, dates: [item("N1", "pending", change)]
        r = find_recent_changes(FakeSearch([hit(url, "2026년 이렇게 달라진다② 최저임금·실업급여")]),
                                fake_fetch({url: ROUNDUP}), read, "실업급여 신청", 5, today=TODAY)
        assert r.sources == [] and r.memo["confirmed"] == [], change


def test_old_year_roundup_future_tense_stays_pending():
    url = "https://news.example/r"
    change = "구직급여(실업급여) 하루 상한액은 기존 6만6000원에서 6만8100원으로 오른다."
    read = lambda topic, arts, dates: [item("N1", "pending", change)]
    r = find_recent_changes(FakeSearch([hit(url, "2025년 이렇게 달라진다 실업급여")]),
                            fake_fetch({url: ROUNDUP}), read, "실업급여 신청", 5, today=TODAY)
    assert r.sources == []


def test_roundup_hedged_title_or_context_stays_pending():
    url = "https://news.example/r"
    change = "실업급여 상한액은 7만원으로 오른다."
    cases = [("2026년 실업급여 어떻게 바뀌나…인상안 검토", "새해 제도 정리. 실업급여 상한액은 7만원으로 오른다."),
             ("2026년 이렇게 달라진다", "다음 내용은 아직 논의 중이다. 실업급여 상한액은 7만원으로 오른다."),
             ("2026년 이렇게 달라진다", "새해 정리. 합의가 이뤄지면 실업급여 상한액은 7만원으로 오른다.")]
    for title, text in cases:
        read = lambda topic, arts, dates: [item("N1", "pending", change)]
        r = find_recent_changes(FakeSearch([hit(url, title)]), fake_fetch({url: text}), read,
                                "실업급여 신청", 5, today=TODAY)
        assert r.sources == [], (title, text)


def test_hedged_annual_title_gets_no_rank_bonus():
    s = FakeSearch([hit("https://a.example/1", "실업급여 개편 내년부터 시행 추진", published="Fri, 20 Nov 2026 09:00:00 +0900"),
                    hit("https://a.example/2", "실업급여 상한 인상 고시", published="Sat, 21 Nov 2026 09:00:00 +0900")])
    picked = pick_articles(s, "실업급여 신청", today=TODAY)
    assert picked[0][0].url == "https://a.example/2"
