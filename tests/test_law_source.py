"""국가법령정보 조문 자료(src/law_source.py)와 법령 자료를 쓰는 숫자 대조·경고."""
from datetime import date

import pytest

from src.ai_generator import _source_figures, build_law_prompt, extract_figures
from src.law_source import LawArticle, LawClient, LawError, core_query, find_law_sources, to_source
from src.link_check import is_home
from src.source_fetcher import Source


class Resp:
    def __init__(self, data):
        self.data = data

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


class FakeHttp:
    """URL·조문 번호별로 정해 둔 응답을 돌려주는 가짜 requests.Session."""

    def __init__(self, search=None, service=None):
        self.search, self.service, self.calls = search or {}, service or {}, []

    def get(self, url, timeout=None, params=None):
        self.calls.append((url, dict(params)))
        if url.endswith("lawSearch.do"):
            return Resp(self.search.get(params["query"], {"aiSearch": {"법령조문": []}}))
        return Resp(self.service[(params["ID"], params["JO"])])


def row(law_id, law, main, branch, text, eff="20260101"):
    return {"법령ID": law_id, "법령명": law, "조문번호": f"{main:04d}", "조문가지번호": f"{branch:02d}",
            "조문내용": text, "시행일자": eff}


MONTHLY_OLD = row("001584", "조세특례제한법", 95, 2, "제95조의2(월세액에 대한 세액공제) ① 옛 글자 100분의 15")
DECREE = row("002", "조세특례제한법 시행령", 95, 0, "제95조(월세 세액공제) ① 시행령 본문")
UNRELATED = row("009", "전기통신사업법", 1, 0, "제1조(목적) 이 법은 ...")


def current_law(name, unit, eff="20260918"):
    return {"법령": {"기본정보": {"법령명_한글": name, "시행일자": eff}, "조문": {"조문단위": unit}}}


MONTHLY_NOW = current_law("조세특례제한법", [
    {"조문여부": "전문", "조문번호": "95", "조문내용": "제2절 ..."},
    {"조문여부": "조문", "조문번호": "95", "조문가지번호": "2", "조문시행일자": "20260918",
     "조문내용": "제95조의2(월세액에 대한 세액공제)",
     "항": [{"항내용": "① 총급여액이 8천만원 이하인 ... 100분의 15",
             "호": [{"호내용": "1. 총급여액이 5천500만원 이하: 100분의 17"}]},
            {"항내용": "② 배우자도 ..."}]},
])


def client(search=None, service=None):
    return LawClient("test-oc", session=FakeHttp(search, service))


def test_core_query_keeps_program_name_only():
    assert core_query("월세 세액공제 신청 대상과 공제 금액 계산법") == "월세 세액공제 공제"
    assert core_query("신청 방법") == ""


def test_search_parses_rows_and_skips_empty():
    c = client(search={"q": {"aiSearch": {"법령조문": [MONTHLY_OLD, row("1", "", 1, 0, "")]}}})
    [a] = c.search("q")
    assert (a.key, a.law_id, a.jo, a.number, a.effective) == ("001584-0095-02", "001584", "009502", "제95조의2", "2026-01-01")
    assert a.heading == "제95조의2(월세액에 대한 세액공제)"
    assert a.url.startswith("https://www.law.go.kr/") and "test-oc" not in a.url


def test_search_single_row_dict_and_auth_failure():
    assert len(client(search={"q": {"aiSearch": {"법령조문": DECREE}}}).search("q")) == 1
    with pytest.raises(LawError, match="검증에 실패"):
        client(search={"q": {"result": "사용자 정보 검증에 실패하였습니다."}}).search("q")
    with pytest.raises(LawError):
        client(search={"q": ValueError("not json")}).search("q")


def test_candidates_merges_two_queries_without_duplicates():
    topic = "월세 세액공제 신청 방법"
    c = client(search={topic: {"aiSearch": {"법령조문": [MONTHLY_OLD, UNRELATED]}},
                       "월세 세액공제": {"aiSearch": {"법령조문": [MONTHLY_OLD, DECREE]}}})
    assert [a.law for a in c.candidates(topic)] == ["조세특례제한법", "전기통신사업법", "조세특례제한법 시행령"]


def test_current_returns_current_text_and_name():
    [old] = client(search={"q": {"aiSearch": {"법령조문": [MONTHLY_OLD]}}}).search("q")
    now = client(service={("001584", "009502"): MONTHLY_NOW}).current(old)
    assert "옛 글자" not in now.text
    assert now.text.splitlines() == ["제95조의2(월세액에 대한 세액공제)", "① 총급여액이 8천만원 이하인 ... 100분의 15",
                                     "1. 총급여액이 5천500만원 이하: 100분의 17", "② 배우자도 ..."]
    assert (now.number, now.effective, now.key) == ("제95조의2", "2026-09-18", old.key)


def test_current_is_none_when_article_or_law_is_gone():
    [old] = client(search={"q": {"aiSearch": {"법령조문": [MONTHLY_OLD]}}}).search("q")
    no_article = {"법령": {"기본정보": {"법령명_한글": "조세특례제한법"}}}
    assert client(service={("001584", "009502"): no_article}).current(old) is None
    repealed = {"Law": "일치하는 법령이 없습니다.  법령명을 확인하여 주십시오."}
    assert client(service={("001584", "009502"): repealed}).current(old) is None
    with pytest.raises(LawError):
        client(service={("001584", "009502"): {"result": "사용자 정보 검증에 실패하였습니다."}}).current(old)


def test_current_uses_renamed_law_name():
    old = LawArticle("010037-0001-02", "010037", "000102", "저출산ㆍ고령사회기본법 시행령", "제1조의2", "옛", "")
    renamed = current_law("인구전략기본법 시행령", {"조문번호": "1", "조문가지번호": "2", "조문내용": "제1조의2(정의) 새 글자"})
    now = client(service={("010037", "000102"): renamed}).current(old)
    assert now.law == "인구전략기본법 시행령" and "ㆍ" not in now.url


def test_find_law_sources_uses_current_text_and_only_listed_keys():
    topic = "월세 세액공제"
    c = client(search={topic: {"aiSearch": {"법령조문": [MONTHLY_OLD, DECREE, UNRELATED]}}},
               service={("001584", "009502"): MONTHLY_NOW,
                        ("002", "009500"): {"법령": {"기본정보": {"법령명_한글": "조세특례제한법 시행령"}}}})
    choose = lambda t, cands: ["지어낸-키", "001584-0095-02", "002-0095-00", "001584-0095-02"]
    sources, errors = find_law_sources(c, choose, topic, first_number=3)
    assert [s.id for s in sources] == ["S3"]
    s = sources[0]
    assert s.via == "law" and s.title == "조세특례제한법 제95조의2(월세액에 대한 세액공제)"
    assert s.text.startswith("[현행 법령 원문 · 국가법령정보센터 · 조세특례제한법 · 시행일 2026-09-18]")
    assert "옛 글자" not in s.text
    assert errors == ["현행 시행 중인 같은 조문이 없어 뺌(폐지·개정·시행 전): 조세특례제한법 시행령 제95조"]
    # 지어낸 키와 중복 키로는 본문 조회를 하지 않는다
    assert sum(1 for url, _ in c.http.calls if url.endswith("lawService.do")) == 2


def test_find_law_sources_respects_max_and_failures():
    topic = "월세"
    rows = [row(str(i), f"법{i}", i, 0, f"제{i}조(제목) 본문") for i in range(1, 7)]
    service = {(str(i), f"{i:04d}00"): current_law(f"법{i}", {"조문번호": str(i), "조문내용": f"제{i}조(제목) 본문"})
               for i in range(1, 7)}
    c = client(search={topic: {"aiSearch": {"법령조문": rows}}}, service=service)
    sources, _ = find_law_sources(c, lambda t, cs: [a.key for a in cs], topic, 1, max_articles=4)
    assert len(sources) == 4

    def boom(t, cs):
        raise RuntimeError("model down")
    assert find_law_sources(c, boom, topic, 1) == ([], ["법령 조문 고르기 실패: RuntimeError"])
    bad = client(search={topic: {"result": "사용자 정보 검증에 실패하였습니다."}})
    sources, errors = find_law_sources(bad, boom, topic, 1)
    assert sources == [] and "검증에 실패" in errors[0]
    assert find_law_sources(client(), boom, "결과없음", 1) == ([], [])


def test_law_prompt_lists_only_candidates():
    a = LawArticle("k1", "1", "000100", "법", "제1조", "제1조(목적) 이 법은", "")
    prompt = build_law_prompt("주제", [a], 4)
    assert "k1" in prompt and "법 제1조" in prompt


def test_law_ratio_matches_percent_and_law_source_is_not_relayed():
    src = to_source(LawArticle("k", "1", "000100", "법", "제1조", "제1조(공제) 100분의 17", ""), "S1")
    assert "pct:17" in _source_figures([src])
    assert src.via == "law"
    assert "pct:17" not in _source_figures([Source("S1", "u", "t", "17명")])


@pytest.mark.parametrize("url, home", [
    ("https://hometax.go.kr/", True), ("https://www.hometax.go.kr", True),
    ("https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=239025", False),
])
def test_home_pages_are_not_used_as_evidence(url, home):
    assert is_home(url) is home


# ---------- 검토(Gemini) 지적 반영 ----------

def test_future_article_is_not_used_and_latest_in_force_is_chosen():
    old = LawArticle("k", "1", "000900", "법", "제9조", "제9조(지원) 옛", "")
    unit = lambda d, t: {"조문번호": "9", "조문시행일자": d, "조문내용": f"제9조(지원) {t}"}
    only_future = current_law("법", [unit("20270101", "새")])
    assert client(service={("1", "000900"): only_future}).current(old, today=date(2026, 11, 16)) is None
    both = current_law("법", [unit("20270101", "미래"), unit("20250101", "현재")])
    now = client(service={("1", "000900"): both}).current(old, today=date(2026, 11, 16))
    assert "현재" in now.text and now.effective == "2025-01-01"


def test_same_number_with_different_title_is_dropped():
    old = LawArticle("k", "1", "000102", "법", "제1조의2", "제1조의2(출산 지원) 옛", "")
    moved = current_law("법", {"조문번호": "1", "조문가지번호": "2", "조문내용": "제1조의2(위원회 구성) 새"})
    assert client(service={("1", "000102"): moved}).current(old) is None
    spaced = current_law("법", {"조문번호": "1", "조문가지번호": "2", "조문내용": "제1조의2(출산  지원) 새"})
    assert client(service={("1", "000102"): spaced}).current(old) is not None


def test_ho_directly_under_article_and_list_text():
    old = LawArticle("k", "1", "000300", "법", "제3조", "제3조(정의)", "")
    unit = {"조문번호": "3", "조문내용": ["제3조(정의) 이 법에서"],
            "호": [{"호내용": "1. 가", "목": [{"목내용": ["가. 첫째", "나. 둘째"]}]}]}
    now = client(service={("1", "000300"): current_law("법", unit)}).current(old)
    assert now.text.splitlines() == ["제3조(정의) 이 법에서", "1. 가", "가. 첫째", "나. 둘째"]
    assert "[" not in now.text


def test_odd_responses_never_stop_writing():
    topic = "월세"
    weird_rows = {"aiSearch": {"법령조문": ["문자열", {"법령명": "법", "조문내용": "x", "조문번호": "abc"}, MONTHLY_OLD]}}
    c = client(search={topic: weird_rows}, service={("001584", "009502"): {"법령": {"기본정보": "", "조문": "\n"}}})
    sources, errors = find_law_sources(c, lambda t, cs: [a.key for a in cs], topic, 1)
    assert sources == [] and len(errors) == 1

    class Broken:
        def candidates(self, topic):
            raise TypeError("odd")
    assert find_law_sources(Broken(), None, topic, 1) == ([], ["법령 검색 처리 실패: TypeError"])


def test_one_failed_search_keeps_other_results():
    topic = "월세 세액공제 신청 방법"
    c = client(search={topic: {"result": "일시 오류"}, "월세 세액공제": {"aiSearch": {"법령조문": [DECREE]}}})
    assert [a.law for a in c.candidates(topic)] == ["조세특례제한법 시행령"]


def test_error_message_does_not_dump_whole_response():
    with pytest.raises(LawError) as e:
        client(search={"q": {"echo": {"OC": "test-oc"}}}).search("q")
    assert "test-oc" not in str(e.value)


def test_decimal_law_ratio():
    assert "ratio:16.5/100" in extract_figures("100분의 16.5")
    src = to_source(LawArticle("k", "1", "000100", "법", "제1조", "제1조(율) 100분의 16.5", ""), "S1")
    assert "pct:16.5" in _source_figures([src])
