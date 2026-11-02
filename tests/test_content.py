import json

import pytest
from google.genai import errors as genai_errors

from src import source_fetcher
from src.ai_generator import ContentGenerator, GenerationError, check_content
from src.blog_html import SectionImage, render_blog_html
from src.content_schema import MasterContent
from src.source_fetcher import Source, SourceError, fetch_source, html_to_text
from templates.prompt_template import build_prompt, narration_budget

SOURCES = [
    Source("S1", "https://www.nts.go.kr/a", "근로장려금 소개", "자료1 " * 50),
    Source("S2", "https://www.nts.go.kr/b", "신청자격", "자료2 " * 50),
]


def good_data(target_seconds=45):
    low, high = narration_budget(target_seconds)
    per_scene = (low + high) // 2 // 5
    section = {
        "heading": "소득 기준",
        "paragraphs": ["단독가구는 총소득 2,200만 원 미만이어야 합니다. " * 6],
        "table": {"caption": "가구별 기준", "headers": ["구분", "기준"],
                  "rows": [["단독", "2,200만 원"], ["홑벌이", "3,200만 원"]]},
        "checklist": None,
        "source_ids": ["S1"],
        "image_query": "calculator documents",
    }
    return {
        "title": "2026년 근로장려금 신청 자격과 기간 정리",
        "slug": "2026-earned-income-tax-credit",
        "meta_description": "근로장려금 소득 기준과 재산 요건, 신청 기간을 국세청 자료로 정리했습니다.",
        "focus_keyword": "근로장려금 신청 자격",
        "reference_note": "2026년 정기신청(2025년 소득) 기준",
        "intro": ["근로장려금은 소득이 적은 일하는 가구를 돕는 제도입니다."],
        "sections": [dict(section, heading=f"소제목 {i}") for i in range(4)],
        "faq": [{"question": "부채는 빼 주나요?", "answer": "빼지 않습니다.", "source_ids": ["S2"]},
                {"question": "반기신청 대상은?", "answer": "근로소득만 있는 분입니다.", "source_ids": ["S2"]}],
        "closing": "홈택스에서 신청할 수 있습니다.",
        "shorts": {"title": "근로장려금 핵심",
                   "scenes": [{"narration": "가" * per_scene, "video_query": "money desk"}
                              for _ in range(5)]},
        "unverified": [],
    }


def model(data):
    return MasterContent.model_validate(data)


# ---------- 검사 ----------

def test_good_content_passes():
    problems, warnings = check_content(model(good_data()), SOURCES, 45)
    assert problems == []


@pytest.mark.parametrize("mutate, expect", [
    (lambda d: d["sections"].pop(), "소제목은 4~6개"),
    (lambda d: d.update(slug="근로 장려금"), "slug"),
    (lambda d: d["sections"][0].update(source_ids=["S9"]), "없는 자료 번호"),
    (lambda d: d["sections"][0].update(source_ids=[]), "근거 자료 번호가 없습니다"),
    (lambda d: d["sections"][0]["table"]["rows"].append(["칸 하나"]), "칸 수"),
    (lambda d: d["intro"].append("정말 좋아요 😀"), "이모지"),
    (lambda d: d["intro"].append("**강조**는 안 됩니다"), "마크다운"),
    (lambda d: d["intro"].append("오늘은 근로장려금에 대해 알아보겠습니다."), "금지 표현"),
    (lambda d: d["shorts"]["scenes"].__setitem__(slice(None), [{"narration": "짧음", "video_query": "x"}] * 4), "내레이션"),
    (lambda d: d["faq"][0].update(source_ids=["S7"]), "FAQ"),
])
def test_check_catches_problems(mutate, expect):
    data = good_data()
    mutate(data)
    problems, _ = check_content(model(data), SOURCES, 45)
    assert any(expect in p for p in problems), problems


def test_unverified_becomes_warning():
    data = good_data()
    data["unverified"] = ["자녀장려금 최대 금액"]
    problems, warnings = check_content(model(data), SOURCES, 45)
    assert problems == [] and any("자녀장려금" in w for w in warnings)


def test_prompt_contains_sources_budget_and_feedback():
    p = build_prompt("근로장려금", SOURCES, 45, feedback="- 이모지를 빼 주세요.")
    low, high = narration_budget(45)
    assert 'id="S1"' in p and "https://www.nts.go.kr/b" in p
    assert f"{low}~{high}자" in p and "이모지를 빼 주세요" in p


# ---------- 생성기 (가짜 Gemini) ----------

class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeClient:
    """미리 정한 결과를 차례로 돌려준다. 예외면 던진다."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents))
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return FakeResponse(out)


def api_error(code):
    return genai_errors.APIError(code, {"error": {"code": code, "message": "x", "status": "X"}})


def make_gen(outcomes, **kw):
    client = FakeClient(outcomes)
    gen = ContentGenerator(client, "main-model", "backup-model", sleep=lambda s: None,
                           retry_waits=(1, 1), **kw)
    return gen, client


def test_generate_success_first_try():
    gen, client = make_gen([json.dumps(good_data())])
    r = gen.generate("근로장려금", SOURCES, 45)
    assert r.model == "main-model" and r.attempts == 1
    assert len(client.calls) == 1


def test_transient_errors_retry_then_fallback_model():
    gen, client = make_gen([api_error(503), api_error(503), api_error(429), json.dumps(good_data())])
    r = gen.generate("근로장려금", SOURCES, 45)
    assert [m for m, _ in client.calls] == ["main-model"] * 3 + ["backup-model"]
    assert r.model == "backup-model"


def test_non_transient_error_is_raised_immediately():
    gen, client = make_gen([api_error(400)])
    with pytest.raises(genai_errors.APIError):
        gen.generate("근로장려금", SOURCES, 45)
    assert len(client.calls) == 1


def test_all_models_failing_gives_generation_error():
    gen, _ = make_gen([api_error(503)] * 6)
    with pytest.raises(GenerationError, match="일시적 오류"):
        gen.generate("근로장려금", SOURCES, 45)


def test_quality_problem_triggers_rewrite_with_feedback():
    bad = good_data()
    bad["intro"].append("결론적으로 좋습니다 😀")
    gen, client = make_gen(["{not json", json.dumps(bad), json.dumps(good_data())])
    r = gen.generate("근로장려금", SOURCES, 45)
    assert r.attempts == 3
    assert "JSON 형식 오류" in client.calls[1][1]
    assert "이모지" in client.calls[2][1] and "금지 표현" in client.calls[2][1]


def test_gives_up_after_max_rounds():
    bad = good_data()
    bad["slug"] = "잘못된 슬러그"
    gen, _ = make_gen([json.dumps(bad)] * 3)
    with pytest.raises(GenerationError, match="slug"):
        gen.generate("근로장려금", SOURCES, 45)


def test_refuses_without_sources():
    gen, client = make_gen([])
    with pytest.raises(GenerationError, match="근거 자료"):
        gen.generate("근로장려금", [], 45)
    assert client.calls == []


# ---------- HTML ----------

def test_html_escapes_ai_text_and_lists_only_cited_sources():
    data = good_data()
    data["sections"][0]["paragraphs"] = ['<script>alert(1)</script> "따옴표" & 기호']
    data["sections"][0]["checklist"] = ["<b>확인</b>"]
    html = render_blog_html(model(data), SOURCES + [Source("S3", "https://x.go.kr", "미사용", "t" * 300)],
                            images={0: SectionImage("https://img/a.jpg", 'alt"x', "Photo by A on Pexels",
                                                    "https://pexels.com/a")})
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "&lt;b&gt;확인&lt;/b&gt;" in html
    assert 'alt="alt&quot;x"' in html and "Photo by A on Pexels" in html
    assert html.count("<h2>소제목") == 4 and "<table>" in html
    assert "https://www.nts.go.kr/a" in html and "https://www.nts.go.kr/b" in html
    assert "미사용" not in html


# ---------- 자료 읽기 ----------

def test_html_to_text_prefers_specific_main_region():
    html = """<html><head><title>근로장려금 소개</title><style>x{}</style></head><body>
      <div id="container"><nav>메뉴1 메뉴2</nav><div id="header">머리말</div>
        <div id="contents"><p>본문 시작</p><script>var a=1</script><p>%s</p></div>
      </div></body></html>""" % ("중요한 내용 " * 40)
    title, text = html_to_text(html)
    assert title == "근로장려금 소개"
    assert text.startswith("본문 시작") and "메뉴1" not in text and "머리말" not in text
    assert "var a" not in text


def test_html_to_text_does_not_fall_back_to_menu_when_main_is_empty():
    html = "<body><nav>%s</nav><div class='menu'>%s</div><div id='contents'>준비중</div></body>" % (
        "메뉴 " * 100, "링크 " * 100)
    _, text = html_to_text(html)
    assert text == "준비중"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/wp-admin/",
    "http://169.254.169.254/opc/v2/instance/",
    "http://10.0.0.118/",
    "file:///etc/passwd",
])
def test_internal_addresses_blocked(url):
    with pytest.raises(SourceError):
        fetch_source(url, "S1")


class FakeResp:
    def __init__(self, status, headers=None, body=b""):
        self.status = status
        self.headers = headers or {}
        self._body = body
        self.read_called = False
        self.closed = False

    def stream(self, n, decode_content=True):
        self.read_called = True
        yield self._body

    def close(self):
        self.closed = True


class FakePool:
    def close(self):
        pass


def fake_network(monkeypatch, responses, internal_hosts=()):
    """_resolve_public와 _open을 가짜로 바꾼다. 접속 기록(호스트, 고정 IP)을 돌려준다."""
    opened = []

    def resolve(host, port, timeout):
        if host in internal_hosts:
            raise SourceError(f"내부 주소로는 접속하지 않습니다: {host}")
        return "203.0.113.10"

    def open_(target, ip, timeout, watch):
        opened.append((target.host, target.path, ip))
        return responses.pop(0), FakePool()

    monkeypatch.setattr(source_fetcher, "_resolve_public", resolve)
    monkeypatch.setattr(source_fetcher, "_open", open_)
    return opened


PAGE = ("<html><title>제목</title><main>%s</main></html>" % ("본문 " * 120)).encode()


def test_redirect_to_internal_address_is_blocked(monkeypatch):
    redirect = FakeResp(302, {"Location": "http://metadata.internal/latest"})
    fake_network(monkeypatch, [redirect], internal_hosts={"metadata.internal"})
    with pytest.raises(SourceError, match="내부 주소"):
        fetch_source("https://example.go.kr/a", "S1")
    assert not redirect.read_called and redirect.closed   # 리다이렉트 본문은 읽지 않음


def test_fetch_follows_redirect_and_connects_to_checked_ip(monkeypatch):
    opened = fake_network(monkeypatch, [
        FakeResp(301, {"Location": "/final?x=1"}),
        FakeResp(200, {"Content-Type": "text/html; charset=utf-8"}, PAGE),
    ])
    src = fetch_source("https://example.go.kr/start", "S1")
    assert src.url == "https://example.go.kr/final?x=1" and src.title == "제목"
    assert opened == [("example.go.kr", "/start", "203.0.113.10"),
                      ("example.go.kr", "/final?x=1", "203.0.113.10")]


def test_non_html_and_errors_rejected(monkeypatch):
    fake_network(monkeypatch, [FakeResp(200, {"Content-Type": "application/pdf"}, b"%PDF"),
                               FakeResp(404)])
    with pytest.raises(SourceError, match="웹 페이지가 아닙니다"):
        fetch_source("https://e.go.kr/a.pdf", "S1")
    with pytest.raises(SourceError, match="응답 코드 404"):
        fetch_source("https://e.go.kr/x", "S1")


def test_too_large_page_rejected(monkeypatch):
    fake_network(monkeypatch, [FakeResp(200, {"Content-Type": "text/html"},
                                        b"a" * (source_fetcher.MAX_BYTES + 1))])
    with pytest.raises(SourceError, match="너무 큽니다"):
        fetch_source("https://e.go.kr/big", "S1")


def test_too_many_redirects(monkeypatch):
    fake_network(monkeypatch, [FakeResp(302, {"Location": "/loop"})
                               for _ in range(source_fetcher.MAX_REDIRECTS + 1)])
    with pytest.raises(SourceError, match="리다이렉트가 너무 많습니다"):
        fetch_source("https://e.go.kr/loop", "S1")


def test_total_timeout(monkeypatch):
    fake_network(monkeypatch, [])
    with pytest.raises(SourceError, match="시간 초과"):
        fetch_source("https://e.go.kr/slow", "S1", total_timeout=0)


def test_page_without_charset_is_decoded(monkeypatch):
    body = ("<html><main>%s</main></html>" % ("근로장려금 안내 " * 60)).encode("euc-kr")
    fake_network(monkeypatch, [FakeResp(200, {"Content-Type": "text/html"}, body)])
    assert "근로장려금" in fetch_source("https://e.go.kr/k", "S1").text


def test_network_errors_become_source_errors(monkeypatch):
    def broken(target, ip, timeout, watch):
        raise ConnectionResetError("reset")

    monkeypatch.setattr(source_fetcher, "_resolve_public", lambda h, p, t: "203.0.113.10")
    monkeypatch.setattr(source_fetcher, "_open", broken)
    with pytest.raises(SourceError, match="접속 실패"):
        fetch_source("https://e.go.kr/x", "S1")


@pytest.mark.parametrize("url, expect", [
    ("https://example.com:99999/", "형식"),
    ("https://example.com:8080/", "포트"),
    ("https://user:pw@example.com/", "계정"),
    ("ftp://example.com/", "http/https"),
])
def test_bad_urls_rejected(url, expect):
    with pytest.raises(SourceError, match=expect):
        fetch_source(url, "S1")


def test_one_bad_url_does_not_stop_others(monkeypatch):
    fake_network(monkeypatch, [FakeResp(200, {"Content-Type": "text/html"}, PAGE)])
    sources, errors = source_fetcher.fetch_sources(["https://example.com:99999/", "https://ok.go.kr/a"])
    assert [s.id for s in sources] == ["S1"] and len(errors) == 1


def test_resolve_rejects_mixed_public_and_internal(monkeypatch):
    monkeypatch.setattr(source_fetcher.socket, "getaddrinfo", lambda *a, **k: [
        (2, 1, 6, "", ("203.0.113.5", 443)), (2, 1, 6, "", ("10.0.0.5", 443))])
    with pytest.raises(SourceError, match="내부 주소"):
        source_fetcher._resolve_public("evil.example", 443, 1)
    monkeypatch.setattr(source_fetcher.socket, "getaddrinfo", lambda *a, **k: [
        (10, 1, 6, "", ("2606:4700:4700::1111", 443, 0, 0)), (2, 1, 6, "", ("8.8.8.8", 443))])
    assert source_fetcher._resolve_public("ok.example", 443, 1) == "8.8.8.8"


# ---------- 숫자 대조·URL 안전 ----------

def test_figures_found_in_sources_produce_no_warning():
    srcs = [Source("S1", "https://nts.go.kr", "t", "재산 2.4억원 미만, 3백만원, 2,200만원, 55% " * 5)]
    data = good_data()
    data["sections"][0]["paragraphs"] = ["재산은 2억 4천만 원 미만, 300만 원, 2,200만 원 미만, 55%입니다. " * 10]
    data["sections"][0]["table"] = None
    data["sections"][0]["source_ids"] = ["S1"]
    for s in data["sections"][1:]:
        s["table"] = None
        s["source_ids"] = ["S1"]
        s["paragraphs"] = ["숫자 없는 문장입니다. " * 20]
    for f in data["faq"]:
        f["source_ids"] = ["S1"]
    problems, warnings = check_content(model(data), srcs, 45)
    assert problems == [] and not any("못 찾은 숫자" in w for w in warnings)


def test_invented_figure_is_flagged():
    data = good_data()
    data["intro"].append("최대 500만 원까지 받을 수 있고 감액률은 10%입니다.")
    _, warnings = check_content(model(data), SOURCES, 45)
    w = next(w for w in warnings if "못 찾은 숫자" in w)
    assert "500만 원" in w and "10%" in w


def test_faq_without_source_is_problem():
    data = good_data()
    data["faq"][0]["source_ids"] = []
    problems, _ = check_content(model(data), SOURCES, 45)
    assert any("FAQ" in p and "근거" in p for p in problems)


def test_unsafe_urls_are_dropped_from_html():
    html = render_blog_html(model(good_data()),
                            [Source("S1", "javascript:alert(1)", "나쁜 출처", "x" * 300)],
                            images={0: SectionImage("javascript:alert(1)", "a"),
                                    1: SectionImage("https://img/b.jpg", "b", "credit", "javascript:x")})
    assert "javascript:" not in html
    assert 'src="https://img/b.jpg"' in html and "credit" in html


def test_broken_html_is_source_error_not_crash(monkeypatch):
    monkeypatch.setattr(source_fetcher, "html_to_text",
                        lambda html: (_ for _ in ()).throw(AssertionError("bogus")))
    fake_network(monkeypatch, [FakeResp(200, {"Content-Type": "text/html"}, PAGE),
                               FakeResp(200, {"Content-Type": "text/plain"}, b"plain text " * 40)])
    sources, errors = source_fetcher.fetch_sources(["https://bad.go.kr/", "https://ok.go.kr/t.txt"])
    assert len(sources) == 1 and "HTML을 읽지 못했습니다" in errors[0]


DRIP_CASES = {
    "본문을 조금씩": (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 100000\r\n\r\n", b"a"),
    "헤더를 조금씩": (b"HTTP/1.1 200 OK\r\n", b"X"),
    "Connection: close 본문": (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n", b"a"),
}


@pytest.mark.parametrize("case", list(DRIP_CASES))
def test_slow_drip_server_is_cut_at_deadline(monkeypatch, case):
    """실제 소켓: 0.2초마다 1바이트씩 보내는 서버를 전체 제한 1초에 끊는지."""
    import socket as s
    import threading
    import time

    head, drip_byte = DRIP_CASES[case]
    server = s.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    stop = threading.Event()

    def drip():
        conn, _ = server.accept()
        conn.recv(4096)
        try:
            conn.sendall(head)
            while not stop.is_set():
                conn.sendall(drip_byte)
                time.sleep(0.2)
        except OSError:
            pass
        conn.close()

    threading.Thread(target=drip, daemon=True).start()
    monkeypatch.setattr(source_fetcher, "_resolve_public", lambda h, p, timeout: "127.0.0.1")
    monkeypatch.setattr(source_fetcher, "_parse",
                        lambda url: source_fetcher._Target("http", "localhost", port, "/"))
    started = time.monotonic()
    try:
        with pytest.raises(SourceError, match="시간 초과"):
            source_fetcher._fetch("http://localhost/", time.monotonic() + 1.0)
        assert time.monotonic() - started < 3
    finally:
        stop.set()
        server.close()


def test_connection_made_after_deadline_is_cut_immediately():
    import socket as s
    a, b = s.socketpair()
    watch = source_fetcher._Watch()
    watch.cut()                      # 타이머가 먼저 끝남
    with pytest.raises(SourceError, match="시간 초과"):
        watch.add(a)                 # 그 뒤에 연결이 맺어짐
    a.settimeout(1)
    assert a.recv(1) == b""          # 바로 끊겨서 더 기다리지 않음
    a.close(); b.close()


def test_watch_cuts_registered_sockets():
    import socket as s
    a, b = s.socketpair()
    watch = source_fetcher._Watch()
    watch.add(a)
    watch.cut()
    a.settimeout(1)
    assert a.recv(1) == b""
    a.close(); b.close()


def test_fetch_sources_skips_duplicate_text_and_stops_at_max(monkeypatch):
    same = FakeResp(200, {"Content-Type": "text/html"}, PAGE)
    same2 = FakeResp(200, {"Content-Type": "text/html"}, PAGE)
    other = FakeResp(200, {"Content-Type": "text/html"},
                     ("<html><main>%s</main></html>" % ("다른 본문 " * 80)).encode())
    fake_network(monkeypatch, [same, same2, other])
    sources, _ = source_fetcher.fetch_sources(
        ["https://a.go.kr/1", "https://a.go.kr/2", "https://a.go.kr/3", "https://a.go.kr/4"], max_sources=2)
    assert [s.url for s in sources] == ["https://a.go.kr/1", "https://a.go.kr/3"]


def test_short_narration_is_repaired_without_rewriting_blog():
    short = good_data()
    for sc in short["shorts"]["scenes"]:
        sc["narration"] = "짧은 대본"
    fixed_shorts = good_data()["shorts"]
    gen, client = make_gen([json.dumps(short), json.dumps({"title": "x", "scenes": [{"narration": "아직 짧음", "video_query": "a"}] * 4}),
                            json.dumps(fixed_shorts)])
    r = gen.generate("근로장려금", SOURCES, 45)
    assert r.attempts == 1                                # 블로그 전체를 다시 쓰지 않음
    assert len(client.calls) == 3                         # 본문 1회 + 대본만 2회
    assert "장면마다" in client.calls[1][1] and "[블로그 글]" in client.calls[1][1]
    assert r.content.shorts.model_dump() == fixed_shorts and r.content.title == short["title"]


def test_narration_repair_failing_falls_back_to_full_rewrite():
    short = good_data()
    for sc in short["shorts"]["scenes"]:
        sc["narration"] = "짧음"
    bad_shorts = json.dumps({"title": "x", "scenes": [{"narration": "짧음", "video_query": "a"}] * 4})
    gen, client = make_gen([json.dumps(short), bad_shorts, bad_shorts, json.dumps(good_data())])
    r = gen.generate("근로장려금", SOURCES, 45)
    assert r.attempts == 2 and len(client.calls) == 4


def test_scene_plan_matches_budget():
    from templates.prompt_template import scene_plan
    scenes, per = scene_plan(45)
    low, high = narration_budget(45)
    assert 4 <= scenes <= 8 and low <= scenes * per <= high


def test_news_signals_included_as_non_evidence():
    p = build_prompt("청년도약계좌", SOURCES, 45, news=[("01 Nov 2026", "청년미래적금으로 갈아탈까")])
    assert "[최근 뉴스 - 변경 신호, 사실 근거 아님]" in p and "청년미래적금으로 갈아탈까" in p
    assert "[최근 뉴스" not in build_prompt("청년도약계좌", SOURCES, 45)
