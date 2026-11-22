import pytest

from src.source_finder import (NaverSearch, SearchError, SearchHit, is_official,
                               find_official_urls, is_local, official_candidates)


@pytest.mark.parametrize("url, ok", [
    ("https://www.nts.go.kr/nts/a", True),
    ("https://www.gov.kr/mw/x", True),
    ("https://www.nhis.or.kr/x", True),
    ("https://www.mnuri.kr/", True),
    ("https://www.energyv.or.kr/", True),
    ("https://mnuri.kr.evil.com/", False),
    ("https://blog.naver.com/abc", False),
    ("https://fake-nts.go.kr.evil.com/", False),
    ("https://cafe.daum.net/x", False),
])
def test_is_official(url, ok):
    assert is_official(url) is ok


def test_candidates_keep_order_skip_unofficial_unreadable_and_duplicates():
    hits = [SearchHit("a", u, "") for u in [
        "https://blog.naver.com/x",
        "https://www.nts.go.kr/1",
        "https://hometax.go.kr/websquare/websquare.html?w2xPath=/ui/pp/index_pp.xml",
        "https://www.nts.go.kr/1",
        "https://www.gov.kr/2",
    ]]
    assert official_candidates(hits) == ["https://www.nts.go.kr/1", "https://www.gov.kr/2"]
    assert official_candidates(hits, limit=1) == ["https://www.nts.go.kr/1"]


class FakeResp:
    def __init__(self, status, data=None, text=""):
        self.status_code = status
        self._data = data
        self.text = text

    def json(self):
        if self._data is None:
            raise ValueError
        return self._data


class FakeSession:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.resp


def test_web_search_sends_hub_headers_and_cleans_text():
    session = FakeSession(FakeResp(200, {"items": [
        {"title": "국세청&gt;<b>근로장려금</b>", "link": "https://www.nts.go.kr/a", "description": "<b>신청</b>자격"}]}))
    hits = NaverSearch("ID", "SECRET", session=session).web("근로장려금", display=5)
    method, url, kw = session.calls[0]
    assert method == "GET" and url == "https://naverapihub.apigw.ntruss.com/search/v1/webkr"
    assert kw["headers"] == {"X-NCP-APIGW-API-KEY-ID": "ID", "X-NCP-APIGW-API-KEY": "SECRET"}
    assert kw["params"]["query"] == "근로장려금" and kw["params"]["display"] == 5
    assert hits == [SearchHit("국세청>근로장려금", "https://www.nts.go.kr/a", "신청자격", "")]


def test_news_prefers_original_link_and_sorts_by_date():
    session = FakeSession(FakeResp(200, {"items": [
        {"title": "t", "originallink": "https://news.example/1", "link": "https://n.news.naver.com/1",
         "description": "", "pubDate": "Mon, 02 Nov 2026 07:50:00 +0900"}]}))
    hits = NaverSearch("i", "s", session=session).news("근로장려금")
    assert hits[0].url == "https://news.example/1" and hits[0].published.startswith("Mon")
    assert session.calls[0][2]["params"]["sort"] == "date"


def test_trend_parses_results():
    from datetime import date
    session = FakeSession(FakeResp(200, {"results": [
        {"title": "근로장려금", "data": [{"period": "2026-08-01", "ratio": 100}]}]}))
    out = NaverSearch("i", "s", session=session).trend(["근로장려금"], date(2026, 8, 1), date(2026, 10, 31))
    assert out == {"근로장려금": [("2026-08-01", 100)]}
    assert session.calls[0][0] == "POST"


@pytest.mark.parametrize("resp, msg", [
    (FakeResp(401), "인증 실패"),
    (FakeResp(429), "한도 초과"),
    (FakeResp(500, text="boom"), "오류 500"),
    (FakeResp(200), "JSON"),
])
def test_errors(resp, msg):
    with pytest.raises(SearchError, match=msg):
        NaverSearch("i", "s", session=FakeSession(resp)).web("x")


def test_local_government_pages_go_last_unless_title_names_the_program():
    class S:
        def web(self, q, display=30):
            return [SearchHit(t, u, "") for t, u in [
                ("사업개요 | The 경기패스", "https://www.gg.go.kr/a"),
                ("대중교통비 환급지원(K-패스)", "https://www.busan.go.kr/b"),
                ("모두의카드 - 국토교통부", "https://www.molit.go.kr/c"),
                ("기후동행카드 안내", "https://news.seoul.go.kr/d")]]
    assert is_local("https://www.incheon.go.kr/x") and not is_local("https://www.smartcity.go.kr/x")
    assert find_official_urls(S(), "K-패스 환급 대상", limit=3) == [
        "https://www.busan.go.kr/b", "https://www.molit.go.kr/c", "https://www.gg.go.kr/a"]
    assert find_official_urls(S(), "기후동행카드 신청 방법", limit=2) == [
        "https://www.molit.go.kr/c", "https://news.seoul.go.kr/d"]   # 제목에 이름이 있는 서울시 페이지는 안 밀림
