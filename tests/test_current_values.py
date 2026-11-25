from datetime import date

from src.current_values import amounts, find_current_values, value_sentences
from src.source_fetcher import Source
from src.source_finder import SearchHit

TODAY = date(2026, 11, 24)
PUB = "Thu, 29 Oct 2026 09:00:00 +0900"


class FakeSearch:
    def __init__(self, hits):
        self.hits, self.calls = hits, []

    def news(self, query, display=10, sort="date"):
        self.calls.append((query, sort))
        return list(self.hits)


def hit(url, title="실업급여 얼마 받나", desc="2026년 기준 실업급여 하한액", published=PUB):
    return SearchHit(title, url, desc, published)


def fetch_of(texts):
    def fetch(urls, max_sources=None):
        return [Source("S1", u, "제목", texts[u]) for u in urls if u in texts], []
    return fetch


GOOD = "2026년 기준 실업급여 하한액은 하루 6만6048원이다."


def test_amounts_parses_korean_money():
    assert amounts("하루 6만6048원, 월 198만 1440원, 66,048원, 1억 2000만원") == [66048, 1981440, 66048, 120000000]
    assert amounts("주 40시간, 80%") == []


def test_value_sentences_drop_next_year_and_hedged():
    text = (f"{GOOD} 2027년에는 하한액이 6만8480원으로 오른다. 정부는 2026년 기준 하한액 6만원 개편을 검토하고 있다. "
            "합의되면 2026년 기준 하한액은 7만원이 된다. 2025년 하한액은 6만4192원이었다.")
    assert value_sentences(text, 2026) == [GOOD]


def test_needs_two_hosts_with_same_amount():
    a, b = "https://a.example/1", "https://b.example/2"
    r = find_current_values(FakeSearch([hit(a), hit(b)]), fetch_of({a: GOOD, b: "올해 기준 실업급여 하한액은 6만6048원이다."}),
                            "실업급여 신청", 7, today=TODAY)
    assert len(r.sources) == 1 and r.sources[0].id == "S7" and r.sources[0].via == "current"
    assert GOOD in r.sources[0].text


def test_single_host_or_disagreeing_amounts_are_dropped():
    a, b = "https://a.example/1", "https://www.a.example/2"
    r = find_current_values(FakeSearch([hit(a), hit(b)]), fetch_of({a: GOOD, b: GOOD}), "실업급여 신청", 7, today=TODAY)
    assert r.sources == []      # 같은 언론사 두 기사는 한 곳으로 센다
    c = "https://c.example/3"
    r = find_current_values(FakeSearch([hit(a), hit(c)]),
                            fetch_of({a: GOOD, c: "2026년 기준 실업급여 하한액은 하루 6만3104원이다."}),
                            "실업급여 신청", 7, today=TODAY)
    assert r.sources == []


def test_sentence_with_unagreed_extra_amount_is_dropped():
    a, b = "https://a.example/1", "https://b.example/2"
    extra = "2026년 기준 실업급여 하한액은 하루 6만6048원, 월 200만원이다."
    r = find_current_values(FakeSearch([hit(a), hit(b)]), fetch_of({a: GOOD, b: extra}), "실업급여 신청", 7, today=TODAY)
    assert [l["sentence"] for l in r.memo["lines"]] == [GOOD]


def test_old_or_unrelated_articles_are_skipped():
    a, b = "https://a.example/1", "https://b.example/2"
    hits = [hit(a, published="Mon, 29 Dec 2025 09:00:00 +0900"), hit(b, title="여행 이야기", desc="2026년 기준 물가")]
    s = FakeSearch(hits)
    r = find_current_values(s, fetch_of({a: GOOD, b: GOOD}), "실업급여 신청", 7, today=TODAY)
    assert r.sources == [] and r.memo["articles"] == []
    assert all(sort == "sim" for _, sort in s.calls)


def test_search_failure_returns_empty():
    class Broken:
        def news(self, *a, **k):
            raise RuntimeError("down")
    r = find_current_values(Broken(), fetch_of({}), "실업급여 신청", 7, today=TODAY)
    assert r.sources == [] and r.notes == []


def _two(a_text, b_text, title="실업급여 얼마 받나"):
    a, b = "https://a.example/1", "https://b.example/2"
    return find_current_values(FakeSearch([hit(a, title), hit(b, title)]), fetch_of({a: a_text, b: b_text}),
                               "실업급여 신청", 7, today=TODAY)


def test_swapped_labels_are_not_agreement():
    r = _two("2026년 기준 실업급여 하한액은 6만원, 상한액은 7만원이다.",
             "2026년 기준 실업급여 하한액은 7만원, 상한액은 6만원이다.")
    assert r.sources == []


def test_unparsed_amount_drops_sentence():
    r = _two("2026년 기준 실업급여는 월 200만원, 하루 6만6천원이다.", "2026년 기준 실업급여는 월 200만원, 하루 6만7천원이다.")
    assert r.sources == []
    assert amounts("하루 6만6천원") == [66000]


def test_previous_sentence_or_title_context_blocks():
    plan = "다음은 추진 중인 개편안의 계산 결과다. 2026년 기준 실업급여 하한액은 7만원이다."
    assert value_sentences(plan, 2026) == []
    assert value_sentences("새 소식. 2026년 기준 실업급여 하한액은 적용한다면 7만원이다.", 2026) == []
    r = _two(GOOD, GOOD, title="내년부터 실업급여 월 198만→176만원")
    assert r.sources == []


def test_ordinary_previous_sentence_does_not_block():
    # 앞 문장의 평범한 '경우'·'~라면서도'는 개편안 소개가 아니다(실제 기사 m-i.kr·economist.co.kr)
    a = "실직한 경우 고용센터에 신청해야 한다. 2026년 기준 실업급여 하한액은 6만6048원이다."
    b = "그는 \"부담이라면서도\" 신청했다. 2026년 기준 실업급여 하한액은 6만6048원이다."
    assert value_sentences(a, 2026) and value_sentences(b, 2026)
    assert value_sentences("실직 기간 동안 생활을 지원한다. 2026년 기준 실업급여 하한액은 6만6048원이다.", 2026)
    assert value_sentences("실직자 가정의 생활을 돕는다. 2026년 기준 실업급여 하한액은 6만6048원이다.", 2026)
    for prev in ("정부가 가정한 수치다.", "노사가 논의 중인 수치는 다음과 같다.", "지급률을 높이면 다음과 같다.",
                 "개편안에 따른 수치다.", "인상안대로라면 이렇다.", "지급률을 90%로 높이면 얼마나 받을까?"):
        assert value_sentences(f"{prev} 2026년 기준 실업급여 하한액은 7만원이다.", 2026) == [], prev
    assert value_sentences("2026년 기준 실업급여 하한액을 7만원으로 가정한다.", 2026) == []


def test_same_press_subdomain_counts_once():
    a, b = "https://press.co.kr/1", "https://m.press.co.kr/2"
    r = find_current_values(FakeSearch([hit(a), hit(b)]), fetch_of({a: GOOD, b: GOOD}), "실업급여 신청", 7, today=TODAY)
    assert r.sources == []


def test_other_program_sentence_is_dropped():
    other = "2026년 기준 국민연금 월 평균 수령액은 66만원이다."
    r = _two(f"{GOOD} {other}", f"{GOOD} {other}", title="실업급여·국민연금 비교")
    assert [l["sentence"] for l in r.memo["lines"]] == [GOOD]


def test_future_change_within_year_is_dropped():
    assert value_sentences("2026년 기준 실업급여 하한액은 12월부터 7만원으로 오른다.", 2026) == []


def test_real_style_sentences_agree_by_label():
    a = "올해 기준 실업급여 하한액은 하루 6만6048원으로, 월 기준으로는 약 198만1440원 수준이다."
    b = "2026년 기준 실업급여 1일 상한액은 6만8100원, 하한액은 6만6048원이다. 2026년 기준 실업급여 하한액은 월 198만1440원이다."
    r = _two(a, b)
    assert [l["sentence"] for l in r.memo["lines"]] == [a]
