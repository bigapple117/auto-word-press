import pytest

from src import source_fetcher
from src.ai_generator import check_content
from src.remote_reader import GeminiPageReader
from src.source_fetcher import BlockedUrlError, Source, SourceError, fetch_sources
from tests.test_content import SOURCES, good_data, model

BODY = "지원대상\n(나이) 19~34세 이하, 총급여액 7,500만원 이하 " * 10


class Meta:
    def __init__(self, statuses):
        self.url_metadata = [type("U", (), {"url_retrieval_status": s})() for s in statuses]


class Cand:
    def __init__(self, statuses):
        self.url_context_metadata = Meta(statuses)


class Resp:
    def __init__(self, text, statuses=("URL_RETRIEVAL_STATUS_SUCCESS",)):
        self.text = text
        self.candidates = [Cand(statuses)]


class Client:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append(contents)
        if self.exc:
            raise self.exc
        return self.resp


def test_reader_returns_relayed_source():
    client = Client(Resp("청년도약계좌 | 서민금융진흥원\n" + BODY))
    src = GeminiPageReader(client)("https://www.kinfa.or.kr/a", "S2")
    assert src.via == "gemini" and src.id == "S2" and src.title.startswith("청년도약계좌")
    assert "7,500만원" in src.text and "https://www.kinfa.or.kr/a" in client.calls[0]


@pytest.mark.parametrize("resp, msg", [
    (Resp("제목\n" + BODY, ("URL_RETRIEVAL_STATUS_ERROR",)), "가져오지 못했습니다"),
    (Resp("읽기 실패"), "본문을 찾지 못했습니다"),
    (Resp("제목\n짧음"), "너무 짧습니다"),
])
def test_reader_rejects_failed_reads(resp, msg):
    with pytest.raises(SourceError, match=msg):
        GeminiPageReader(Client(resp))("https://www.kinfa.or.kr/a", "S1")


def test_reader_api_error_is_source_error():
    with pytest.raises(SourceError, match="대신 읽기 실패"):
        GeminiPageReader(Client(exc=RuntimeError("503")))("https://www.kinfa.or.kr/a", "S1")


def test_reader_refuses_policy_blocked_urls():
    client = Client(Resp("t\n" + BODY))
    with pytest.raises(BlockedUrlError):
        GeminiPageReader(client)("https://example.com:8080/", "S1")
    assert client.calls == []


def test_fallback_used_only_for_non_policy_failures(monkeypatch):
    def fake_fetch(url, source_id, **kw):
        if "internal" in url:
            raise BlockedUrlError("내부 주소로는 접속하지 않습니다")
        raise SourceError("접속 실패 (ConnectTimeoutError)")

    used = []

    def fallback(url, source_id):
        used.append(url)
        return Source(source_id, url, "대신 읽음", BODY, via="gemini")

    monkeypatch.setattr(source_fetcher, "fetch_source", fake_fetch)
    sources, errors = fetch_sources(["http://internal/", "https://www.kinfa.or.kr/a"], fallback=fallback)
    assert used == ["https://www.kinfa.or.kr/a"]
    assert [s.via for s in sources] == ["gemini"] and "내부 주소" in errors[0]


def test_fallback_failure_reports_both_reasons(monkeypatch):
    monkeypatch.setattr(source_fetcher, "fetch_source",
                        lambda url, sid, **kw: (_ for _ in ()).throw(SourceError("접속 실패")))
    sources, errors = fetch_sources(["https://a.go.kr/"],
                                    fallback=lambda u, i: (_ for _ in ()).throw(SourceError("Gemini 실패")))
    assert sources == [] and "접속 실패" in errors[0] and "Gemini 실패" in errors[0]


def test_relayed_source_triggers_warning():
    srcs = SOURCES[:1] + [Source("S2", "https://www.kinfa.or.kr/a", "t", "자료2 " * 50, via="gemini")]
    _, warnings = check_content(model(good_data()), srcs, 45)
    assert any("AI가 옮겨 적은 자료" in w and "kinfa" in w for w in warnings)
