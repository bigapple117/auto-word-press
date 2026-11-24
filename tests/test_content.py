import json

import pytest
import urllib3
from google.genai import errors as genai_errors

from src import source_fetcher
from src.ai_generator import ContentGenerator, GenerationError, GenerationResult, ModelBusyError, check_content
from src.blog_html import SectionImage, render_blog_html
from src.content_schema import MasterContent, Shorts
from src.source_fetcher import Source, SourceError, fetch_source, html_to_text
from templates.prompt_template import build_prompt, narration_budget

SOURCES = [
    Source("S1", "https://www.nts.go.kr/a", "근로장려금 소개", "자료1 " * 50),
    Source("S2", "https://www.nts.go.kr/b", "신청자격", "자료2 " * 50),
]


def P(text, strong=None):
    """문단 블록용 데이터. strong에 든 글자는 굵게."""
    if strong:
        before, after = text.split(strong, 1)
        spans = [{"text": before}, {"text": strong, "strong": True}, {"text": after}]
    else:
        spans = [{"text": text}]
    return {"spans": spans}


def para(text, strong=None):
    return {"type": "paragraph", "paragraph": P(text, strong)}


BODY = ("배우자 소득이 있으면 홑벌이인지 맞벌이인지부터 가려야 합니다. "
        "구분 기준은 아래 표에서 확인해 보세요. 헷갈리면 홈택스 모의계산을 먼저 해 보면 됩니다.")


def good_data(target_seconds=45):
    low, high = narration_budget(target_seconds)
    per_scene = (low + high) // 2 // 5
    section = {
        "heading": "소득 기준",
        "takeaway": None,
        "blocks": [
            para(BODY, strong="홈택스 모의계산"),
            {"type": "table", "table": {"caption": "가구별 기준", "headers": ["구분", "기준"],
                                        "rows": [["단독", "2,200만 원"], ["홑벌이", "3,200만 원"]]}},
            para("재산이 많으면 금액이 줄어들 수 있습니다. 신청 전 재산 합계를 확인해 두세요. " * 2),
            para("기한 후 신청도 가능합니다. 신청 경로는 홈택스와 전화 두 가지가 있습니다. " * 2),
            para("자세한 기준은 국세청 안내 페이지에서 다시 확인하면 됩니다. 조건이 바뀔 수 있으니까요. " * 2),
        ],
        "source_ids": ["S1"],
        "image_query": None,
    }
    return {
        "title": "2026년 근로장려금 신청 자격과 기간 정리",
        "slug": "2026-earned-income-tax-credit",
        "meta_description": "근로장려금 소득 기준과 재산 요건, 신청 기간을 국세청 자료로 정리했습니다.",
        "focus_keyword": "근로장려금 신청 자격",
        "reference_note": "2026년 정기신청(2025년 소득) 기준",
        "intro": [P("내 소득과 재산으로 근로장려금을 받을 수 있을지, 가장 많이 걸리는 기준부터 살펴보겠습니다.")],
        "sections": [dict(section, heading=f"소제목 {i}", image_query="calculator desk" if i == 0 else None)
                     for i in range(4)],
        "faq": [{"question": "부채는 빼 주나요?", "answer": "빼지 않습니다.", "source_ids": ["S2"]},
                {"question": "반기신청 대상은?", "answer": "근로소득만 있는 분입니다.", "source_ids": ["S2"]}],
        "closing": P("12월 1일까지는 기한 후 신청이 가능하니 홈택스에서 먼저 조회해 보세요."),
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
    (lambda d: d["sections"][0]["blocks"][1]["table"]["rows"].append(["칸 하나"]), "칸 수"),
    (lambda d: d["intro"].append(P("정말 좋아요 😀")), "이모지"),
    (lambda d: d["intro"].append(P("**강조**는 안 됩니다")), "마크다운"),
    (lambda d: d["intro"].append(P("오늘은 근로장려금에 대해 알아보겠습니다.")), "금지 표현"),
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


def test_missing_eligibility_is_warned_and_given_one_is_shown():
    data = good_data()
    problems, warnings = check_content(model(data), SOURCES, 45)
    assert problems == [] and any("지원 대상" in w for w in warnings)
    data["eligibility"] = "근로·사업소득이 있는 <가구>"
    _, warnings = check_content(model(data), SOURCES, 45)
    assert not any("지원 대상" in w for w in warnings)
    html = render_blog_html(model(data), SOURCES)
    assert '<p class="jc-reference">대상: 근로·사업소득이 있는 &lt;가구&gt;</p>' in html


def test_prompt_contains_sources_budget_and_feedback():
    p = build_prompt("근로장려금", SOURCES, 45, feedback="- 이모지를 빼 주세요.")
    low, high = narration_budget(45)
    assert 'id="S1"' in p and "https://www.nts.go.kr/b" in p
    assert f"{low}~{high}자" in p and "이모지를 빼 주세요" in p


def test_prompt_body_target_matches_checker_scope():
    """지시문의 본문 분량은 검사와 같은 범위(표·FAQ 제외)를 말하고, 하한이 검사 기준 이상이어야 한다."""
    from src.ai_generator import MIN_BODY_CHARS
    from templates.prompt_template import BODY_TARGET_CHARS
    p = build_prompt("근로장려금", SOURCES, 45)
    assert f"{BODY_TARGET_CHARS[0]:,}~{BODY_TARGET_CHARS[1]:,}자" in p
    assert "표·체크리스트·FAQ·마무리는 이 글자 수에" in p
    assert BODY_TARGET_CHARS[0] > MIN_BODY_CHARS


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
    gen = ContentGenerator(client, "main-model", fallback_model="backup-model", sleep=lambda s: None,
                           retry_waits=(1, 1), **kw)
    return gen, client


def test_generate_success_first_try():
    gen, client = make_gen([json.dumps(good_data())])
    r = gen.generate("근로장려금", SOURCES, 45)
    assert r.model == "main-model" and r.attempts == 1
    assert len(client.calls) == 1


def test_transient_errors_retry_then_succeed_on_main_model():
    gen, client = make_gen([api_error(503), api_error(503), json.dumps(good_data())])
    r = gen.generate("근로장려금", SOURCES, 45)
    assert [m for m, _ in client.calls] == ["main-model"] * 3 and r.model == "main-model"


def test_quota_exceeded_skips_to_second_model_without_waiting():
    waits = []
    client = FakeClient([api_error(429), json.dumps(good_data())])
    gen = ContentGenerator(client, "main-model", second_model="second-model",
                           fallback_model="backup-model", sleep=waits.append, retry_waits=(1, 1))
    r = gen.generate("근로장려금", SOURCES, 45)
    assert [m for m, _ in client.calls] == ["main-model", "second-model"]
    assert r.model == "second-model" and waits == []


def test_blog_tries_main_then_second_but_never_lite():
    client = FakeClient([api_error(503)] * 3 + [api_error(429)])
    gen = ContentGenerator(client, "main-model", second_model="second-model",
                           fallback_model="backup-model", sleep=lambda s: None, retry_waits=(1, 1))
    with pytest.raises(ModelBusyError):
        gen.generate("근로장려금", SOURCES, 45)
    assert [m for m, _ in client.calls] == ["main-model"] * 3 + ["second-model"]


def test_main_model_used_again_when_available():
    client = FakeClient([api_error(429), json.dumps(good_data()), json.dumps(good_data())])
    gen = ContentGenerator(client, "main-model", second_model="second-model",
                           fallback_model="backup-model", sleep=lambda s: None, retry_waits=(1, 1))
    assert gen.generate("근로장려금", SOURCES, 45).model == "second-model"
    assert gen.generate("근로장려금", SOURCES, 45).model == "main-model"


def test_quota_exceeded_on_only_model_is_busy():
    gen, client = make_gen([api_error(429)])
    with pytest.raises(ModelBusyError):
        gen.generate("근로장려금", SOURCES, 45)
    assert len(client.calls) == 1


def test_busy_main_model_never_falls_back_for_blog():
    gen, client = make_gen([api_error(503)] * 3)
    with pytest.raises(ModelBusyError):
        gen.generate("근로장려금", SOURCES, 45)
    assert {m for m, _ in client.calls} == {"main-model"}


def test_blog_fallback_option_still_available():
    gen, client = make_gen([api_error(503)] * 3 + [json.dumps(good_data())], blog_fallback=True)
    r = gen.generate("근로장려금", SOURCES, 45)
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
    bad["intro"].append(P("결론적으로 좋습니다 😀"))
    gen, client = make_gen(["{not json", json.dumps(bad), json.dumps(good_data())])
    r = gen.generate("근로장려금", SOURCES, 45)
    assert r.attempts == 3
    assert "JSON 형식 오류" in client.calls[1][1]
    assert "이모지" in client.calls[2][1] and "금지 표현" in client.calls[2][1]


def test_failed_draft_is_fixed_not_rewritten():
    bad = good_data()
    bad["slug"] = "잘못된 슬러그"
    gen, client = make_gen([json.dumps(bad), json.dumps(good_data())])
    r = gen.generate("근로장려금", SOURCES, 45)
    assert r.attempts == 2
    assert "[이전 결과" not in client.calls[0][1]
    second = client.calls[1][1]
    assert "[이전 결과 - 이것을 고쳐서" in second and "처음부터 새로 쓰지 않는다" in second
    assert "잘못된 슬러그" in second and "slug" in second


def test_fix_round_asks_to_keep_body_length():
    """분량 말고 다른 곳만 걸렸으면, 고치면서 본문을 줄이지 말라고 현재 글자 수를 알려 준다."""
    from src.ai_generator import BODY_KEEP_PROBLEM, count_body_chars
    bad = good_data()
    bad["slug"] = "잘못된 슬러그"
    gen, client = make_gen([json.dumps(bad), json.dumps(good_data())])
    gen.generate("근로장려금", SOURCES, 45)
    second = client.calls[1][1]
    assert BODY_KEEP_PROBLEM in second and f"{count_body_chars(model(bad))}자" in second


def test_keep_length_hint_is_not_a_failure_reason():
    """분량 유지 안내는 고칠 지시일 뿐, 최종 불합격 이유에는 들어가지 않는다."""
    bad = good_data()
    bad["slug"] = "잘못된 슬러그"
    gen, _ = make_gen([json.dumps(bad)] * 3)
    with pytest.raises(GenerationError) as exc:
        gen.generate("근로장려금", SOURCES, 45)
    assert "분량 유지" not in str(exc.value)


def test_short_body_round_has_no_keep_length_hint():
    from src.ai_generator import BODY_KEEP_PROBLEM
    short = good_data()
    for s in short["sections"]:
        s["blocks"] = [b for b in s["blocks"] if b["type"] != "paragraph"][:1] or s["blocks"][:1]
    gen, client = make_gen([json.dumps(short), json.dumps(good_data())])
    gen.generate("근로장려금", SOURCES, 45)
    assert "너무 짧습니다" in client.calls[1][1] and BODY_KEEP_PROBLEM not in client.calls[1][1]


def test_broken_fix_retries_previous_draft_with_same_problems():
    bad = good_data()
    bad["slug"] = "잘못된 슬러그"
    gen, client = make_gen([json.dumps(bad), "{not json", json.dumps(good_data())])
    r = gen.generate("근로장려금", SOURCES, 45)
    assert r.attempts == 3
    third = client.calls[2][1]
    assert "잘못된 슬러그" in third and "slug" in third and "JSON 형식 오류" not in third
    from src.ai_generator import BODY_KEEP_PROBLEM
    assert BODY_KEEP_PROBLEM in third   # 형식이 깨져 앞 결과를 다시 고칠 때도 분량 유지 안내를 함께 준다


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
    data["sections"][0]["blocks"][0] = para('<script>alert(1)</script> "따옴표" & 기호')
    data["sections"][0]["blocks"].append({"type": "checklist", "items": ["<b>확인</b>", "둘째"]})
    html = render_blog_html(model(data), SOURCES + [Source("S3", "https://x.go.kr", "미사용", "t" * 300)],
                            images={0: SectionImage("https://img/a.jpg", 'alt"x', "Photo by A on Pexels",
                                                    "https://pexels.com/a")})
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "&lt;b&gt;확인&lt;/b&gt;" in html
    assert 'alt="alt&quot;x"' in html and "Photo by A on Pexels" in html
    assert html.count("<h2>소제목") == 4 and '<table class="jc-table' in html
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


def test_html_to_text_survives_duplicate_head_and_contents_area():
    # 마이홈포털: <head>를 두 번 열고 한 번만 닫으며, 본문은 <main> 밖 contentsArea 상자에 있다
    html = """<html><head><meta charset="utf-8"><head><title>마이홈포털</title></head><body>
      <main id="main"><div id="container"></div></main>
      <div class="contents-area" id='contentsArea'><dl><dt>대출대상</dt><dd>%s</dd></dl></div>
      </body></html>""" % ("부부합산 연소득 1.3억원 이하 " * 20)
    title, text = html_to_text(html)
    assert title == "마이홈포털"
    assert text.startswith("대출대상") and "1.3억원" in text


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
        self.headers = urllib3.HTTPHeaderDict(headers or {})
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
    cookies = fake_network.cookies = []

    def resolve(host, port, timeout):
        if host in internal_hosts:
            raise SourceError(f"내부 주소로는 접속하지 않습니다: {host}")
        return "203.0.113.10"

    def open_(target, ip, timeout, watch, cookie=""):
        opened.append((target.host, target.path, ip))
        cookies.append(cookie)
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


def test_cookie_redirect_loop_is_followed_with_cookie_only_on_same_host(monkeypatch):
    """국토교통부처럼 쿠키를 주고 같은 주소로 다시 보내는 곳: 쿠키를 그 호스트에만 돌려준다."""
    fake_network(monkeypatch, [
        FakeResp(307, {"Location": "https://www.molit.go.kr/a", "Set-Cookie": "TMOS=abc; Path=/; HttpOnly"}),
        FakeResp(302, {"Location": "http://www.molit.go.kr/a", "Set-Cookie": "BAD=x\x01y"}),
        FakeResp(302, {"Location": "https://other.go.kr/b"}),
        FakeResp(200, {"Content-Type": "text/html; charset=utf-8"}, PAGE),
    ])
    src = fetch_source("https://www.molit.go.kr/a", "S1")
    assert src.url == "https://other.go.kr/b"
    # https 쿠키는 http 주소·다른 호스트에 안 보내고, 제어문자 든 쿠키는 버린다
    assert fake_network.cookies == ["", "TMOS=abc", "", ""]


def test_official_page_redirected_outside_cannot_vouch_for_links(monkeypatch):
    """공식 주소에서 외부로 넘어간 페이지는 최종(외부) 주소로 기록돼 바로가기 링크 보증에 쓰이지 않는다."""
    from src.content_schema import ActionLink
    from src.link_check import check_action_links
    body = ("<html><title>t</title><main>%s 누리집 www.mnurinew.kr</main></html>" % ("본문 " * 120)).encode()
    fake_network(monkeypatch, [
        FakeResp(302, {"Location": "https://ad.example.com/land"}),
        FakeResp(200, {"Content-Type": "text/html; charset=utf-8"}, body),
        FakeResp(200, {"Content-Type": "text/html; charset=utf-8"}, body),
    ])
    moved = fetch_source("https://www.korea.kr/news/1", "S1")
    assert moved.url == "https://ad.example.com/land"
    real = fetch_source("https://www.gangdong.go.kr/a", "S2")
    link = ActionLink(label="누리집", url="https://www.mnurinew.kr/", purpose="info")
    ok, bad = check_action_links([link], [moved, real])
    assert ok == [] and any("공식 사이트가 아님" in b for b in bad)


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
    def broken(target, ip, timeout, watch, cookie=""):
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
    data["sections"][0]["blocks"] = [para("재산은 2억 4천만 원 미만, 300만 원, 2,200만 원 미만, 55%입니다.")]
    for s in data["sections"][1:]:
        s["blocks"] = [para("숫자 없는 문장입니다. 이것도 숫자가 없습니다. 설명을 조금 더 붙여 둡니다.")] * 9
    for s in data["sections"]:
        s["source_ids"] = ["S1"]
    for f in data["faq"]:
        f["source_ids"] = ["S1"]
    problems, warnings = check_content(model(data), srcs, 45)
    assert problems == [] and not any("못 찾은 숫자" in w for w in warnings)


def test_invented_figure_is_flagged():
    data = good_data()
    data["intro"].append(P("최대 500만 원까지 받을 수 있고 감액률은 10%입니다."))
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
    monkeypatch.setattr(source_fetcher, "_parse_html",
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
    assert r.content.shorts == Shorts.model_validate(fixed_shorts) and r.content.title == short["title"]


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


def test_refresh_checks_included_only_when_present():
    p = build_prompt("근로장려금", SOURCES, 45, checks=["2026년 지급액 (확인 이유: 고시 확인)"])
    assert "[다시 확인할 항목 - 예전 글 기준, 사실 근거 아님]" in p
    assert "- 2026년 지급액 (확인 이유: 고시 확인)" in p
    assert "[다시 확인할 항목" not in build_prompt("근로장려금", SOURCES, 45)


def test_image_markup_has_size_and_wp_class():
    html = render_blog_html(model(good_data()), SOURCES,
                            images={1: SectionImage("https://jalcheck.com/a.jpg", "alt", width=1200, height=675,
                                                    media_id=16)})
    assert 'width="1200" height="675" class="wp-image-16"' in html



# ---------- 사람이 쓴 글처럼: 구성·문체 검사 ----------

from src.ai_generator import check_style, table_repeated_in_text  # noqa: E402


def test_table_repeated_in_paragraph_is_problem():
    data = good_data()
    rows = [["단독가구", "2,200만 원 미만", "165만 원"], ["홑벌이가구", "3,200만 원 미만", "285만 원"],
            ["맞벌이가구", "4,400만 원 미만", "330만 원"]]
    data["sections"][1]["blocks"] = [
        para("단독가구는 2,200만 원 미만이고 최대 165만 원, 홑벌이가구는 3,200만 원 미만이고 285만 원입니다."),
        para("맞벌이가구는 4,400만 원 미만이면 330만 원까지 받습니다."),
        {"type": "table", "table": {"caption": "가구별", "headers": ["가구", "소득", "최대"], "rows": rows}},
    ]
    problems, _ = check_style(model(data))
    assert any("표 내용을 다시 나열" in p for p in problems)
    assert table_repeated_in_text(rows, ["배우자 소득으로 가구를 구분합니다."]) is False


def test_long_paragraph_and_definition_intro_are_problems():
    data = good_data()
    data["intro"] = [P("근로장려금은 일하는 저소득 가구를 돕는 제도입니다.")]
    data["sections"][2]["blocks"].append(para("가나다라마바사. " * 60))
    problems, _ = check_style(model(data))
    assert any("정의로 시작" in p for p in problems)
    assert any("긴 문단" in p for p in problems)


def test_too_many_strong_and_images_are_problems():
    data = good_data()
    data["sections"][0]["blocks"] = [para("가 강조 나 다.", "강조")] * 3
    for s in data["sections"]:
        s["image_query"] = "office desk"
    problems, _ = check_style(model(data))
    assert any("강조가 3곳" in p for p in problems)
    assert any("사진(image_query)" in p for p in problems)


def test_repeated_endings_and_same_shape_warn():
    data = good_data()
    same = para("소득을 확인합니다. 재산을 확인합니다. 기간을 확인합니다. 서류를 확인합니다.")
    for s in data["sections"]:
        s["blocks"] = [same, same]
    _, warnings = check_style(model(data))
    assert any("같은 종결 표현" in w for w in warnings)
    assert any("같은 블록 구성" in w for w in warnings)


def test_blocks_must_be_complete():
    data = good_data()
    data["sections"][0]["blocks"].append({"type": "note"})
    data["sections"][1]["blocks"] = [{"type": "table", "table": {"caption": "c", "headers": ["a", "b"],
                                                                 "rows": [["1", "2"]]}}]
    problems, _ = check_content(model(data), SOURCES, 45)
    assert any("note 내용" in p for p in problems)
    assert any("설명 문단이 하나도 없습니다" in p for p in problems)


def test_render_blocks_strong_note_summary_and_table_structure():
    data = good_data()
    data["sections"][0]["takeaway"] = "재산 기준에서 가장 많이 걸립니다"
    data["sections"][0]["blocks"].append({"type": "note", "note_kind": "caution",
                                          "note": P("부채는 빼 주지 않습니다.", "부채")})
    data["sections"][0]["blocks"].append({"type": "table", "table": {
        "caption": "넓은 표", "headers": ["a", "b", "c", "d"], "rows": [["긴 첫 칸 설명이 들어간 행입니다", "1", "2", "3"]]}})
    html = render_blog_html(model(data), SOURCES)
    assert html.startswith('<div class="jc-article">') and html.endswith("</div>")
    assert '<p class="jc-summary">재산 기준에서 가장 많이 걸립니다</p>' in html
    assert '<strong>홈택스 모의계산</strong>' in html
    assert 'jc-note--caution' in html and '<span class="jc-note__label">주의</span> <strong>부채</strong>' in html
    assert '<div class="jc-scroll" role="region" aria-labelledby="jc-table-1" tabindex="0">' in html
    assert '<caption id="jc-table-1">가구별 기준</caption>' in html
    assert '<th scope="col">구분</th>' in html and '<th scope="row" data-label="구분">단독</th>' in html
    assert 'class="jc-table jc-table--key"' in html                    # 짧은 첫 열 → 줄바꿈 방지
    assert 'class="jc-table jc-table--wide"' in html                   # 4열 → 넓은 표, 긴 첫 열은 줄바꿈 허용
    assert 'class="jc-closing"' in html


def test_strong_requires_real_boolean_and_text_is_escaped():
    data = good_data()
    data["intro"] = [{"spans": [{"text": "<b>x</b>", "strong": True}, {"text": " & y"}]}]
    html = render_blog_html(model(data), SOURCES)
    assert "<strong>&lt;b&gt;x&lt;/b&gt;</strong> &amp; y" in html


def test_table_repeat_ignores_partial_number_matches():
    rows = [["단독가구", "100만 원"], ["홑벌이가구", "200만 원"], ["맞벌이가구", "300만 원"]]
    texts = ["단독가구는 1,000만 원, 홑벌이가구는 2,000만 원, 맞벌이가구는 3,000만 원이 소득 기준입니다."]
    assert table_repeated_in_text(rows, texts) is False
    texts = ["단독가구는 100만 원입니다. 홑벌이가구는 200만 원입니다. 맞벌이가구는 300만 원입니다."]
    assert table_repeated_in_text(rows, texts) is True


def test_strong_count_ignores_hidden_fields_and_five_columns_rejected():
    data = good_data()
    data["sections"][0]["blocks"][0] = {"type": "paragraph", "paragraph": P("가 강조 나", "강조"),
                                        "note": {"spans": [{"text": "x", "strong": True}] * 3}}
    problems, _ = check_style(model(data))
    assert not any("강조가" in p for p in problems)
    data["sections"][1]["blocks"][1]["table"] = {"caption": "c", "headers": list("abcde"),
                                                  "rows": [list("12345")]}
    problems, _ = check_content(model(data), SOURCES, 45)
    assert any("2~4개" in p for p in problems)


def test_table_repeat_keeps_sign():
    rows = [["가", "-100원"], ["나", "-200원"], ["다", "-300원"]]
    assert table_repeated_in_text(rows, ["가는 100원입니다. 나는 200원입니다. 다는 300원입니다."]) is False


def test_two_timeouts_give_up_quickly_as_busy():
    gen, client = make_gen([api_error(504), api_error(504), json.dumps(good_data())])
    with pytest.raises(ModelBusyError):
        gen.generate("근로장려금", SOURCES, 45)
    assert [m for m, _ in client.calls] == ["main-model", "main-model"]


def test_two_timeouts_switch_to_backup_when_fallback_allowed():
    gen, client = make_gen([api_error(504), api_error(504), json.dumps(good_data())], blog_fallback=True)
    r = gen.generate("근로장려금", SOURCES, 45)
    assert [m for m, _ in client.calls] == ["main-model", "main-model", "backup-model"]


def test_links_box_rendered_only_with_verified_links():
    data = good_data()
    data["action_links"] = [
        {"label": "국세청 안내 보기", "url": "https://www.nts.go.kr/a", "purpose": "info", "note": "공식 안내"},
        {"label": "가짜 신청", "url": "https://www.hometax.go.kr/made-up", "purpose": "apply"},
    ]
    html = render_blog_html(model(data), SOURCES)
    assert 'class="jc-links"' in html and 'href="https://www.nts.go.kr/a"' in html
    assert "made-up" not in html
    _, warnings = check_content(model(data), SOURCES, 45)
    assert any("바로가기 링크에서 뺀 것" in w for w in warnings)
    data["action_links"] = []
    assert 'class="jc-links"' not in render_blog_html(model(data), SOURCES)


def test_too_short_body_is_problem():
    data = good_data()
    for sec in data["sections"]:
        sec["blocks"] = [para("짧습니다. 정말 짧아요.")]
    problems, _ = check_content(model(data), SOURCES, 45)
    assert any("너무 짧습니다" in p for p in problems)


def test_fraction_is_ratio_not_money():
    from src.ai_generator import extract_figures
    assert extract_figures("가산세(1일 22/100,000)") == {"ratio:22/100000": "22/100,000"}
    got = extract_figures("1일당 10만 분의 22가 붙습니다")
    assert list(got) == ["ratio:22/100000"]
    data = good_data()
    data["intro"].append(P("허위 신청은 가산세(1일당 10만 분의 22)가 붙습니다."))
    srcs = [Source("S1", "https://www.nts.go.kr/a", "t", "환수액 : 지급액 + 가산세(1일 22/100,000)")]
    _, warnings = check_content(model(data), srcs, 45)
    assert not any("10만" in w for w in warnings if "못 찾은 숫자" in w)


def test_table_cells_carry_column_labels():
    data = good_data()
    data["sections"][0]["blocks"][1]["table"] = {"caption": "c", "headers": ["가구", "소득 \"구간\"", "지급액", "비고"],
                                                 "rows": [["홑벌이", "2,100만 원 이하", "100만 원", "-"]]}
    html = render_blog_html(model(data), SOURCES)
    assert '<th scope="row" data-label="가구">홑벌이</th>' in html
    assert 'data-label="소득 &quot;구간&quot;"' in html and 'jc-table--wide' in html


CATS = [("benefits", "지원금·복지", "받는 지원금"), ("tax", "세금·절세", "세금 제도")]


def test_choose_category_returns_listed_slug_and_sends_list():
    gen, client = make_gen(['{"slug": " tax "}'])
    assert gen.choose_category("자동차세 연납", "연납 할인", ["자동차세"], CATS) == "tax"
    assert "slug=benefits" in client.calls[0][1] and "자동차세 연납" in client.calls[0][1]


def test_choose_category_rejects_unknown_or_empty_list():
    from src.ai_generator import GenerationError
    gen, _ = make_gen(['{"slug": "health"}'])
    with pytest.raises(GenerationError):
        gen.choose_category("t", "s", [], CATS)
    with pytest.raises(GenerationError):
        make_gen([])[0].choose_category("t", "s", [], [])


# ---------- 한글 단위 금액 읽기 · 발행 전 자동 검토 ----------

def test_korean_compound_amounts_read_as_one_value():
    from src.ai_generator import extract_figures
    cases = {"34만 9,700원": 349_700, "1억 3,500만 원": 135_000_000, "1억3천5백만원": 135_000_000,
             "7천2백5십만원": 72_500_000, "152만 5,000원": 1_525_000, "2.4억원": 240_000_000}
    for text, value in cases.items():
        assert set(extract_figures(text)) == {f"won:{value}"}, text
    assert set(extract_figures("8만 8만원")) == {"won:80000"}          # 같은 단위가 다시 나오면 다른 금액
    assert set(extract_figures("0만원")) == {"won:0"}                  # 명시한 0을 1로 보지 않음
    assert set(extract_figures("0천만원")) == {"won:0"} and set(extract_figures("천만 원")) == set()
    assert extract_figures("3천 명") == {}


def test_bare_numbers_in_source_tables_count_but_not_unit_numbers():
    from src.ai_generator import missing_figures
    srcs = [Source("S1", "https://a.go.kr", "t", "(단위: 원)\n2인\n6,299,000\n229,357\n지원금 30만원, 2026년, 공고 2025-11-13, 쪽 45")]
    data = good_data()
    data["intro"].append(P("2인 가구는 6,299,000원, 보험료는 229,357원입니다. 잘못 쓴 30원과 2026원, 2025원, 45원."))
    missing = missing_figures(model(data), srcs)
    assert "6,299,000원" not in missing and "229,357원" not in missing
    assert {"30원", "2026원", "2025원", "45원"} <= set(missing)


def _with_takeaways(n):
    data = good_data()
    for i, s in enumerate(data["sections"][:n]):
        s["takeaway"] = f"요약 {i}"
    return data


def test_review_adopts_fixed_content_and_reports_fixes():
    fixed = good_data()
    fixed["title"] = "2026년 근로장려금 신청 자격과 기간 정리(검토)"
    reply = {"fixes": ["표를 자료대로 고침"], "unresolved": [], "content": fixed}
    gen, client = make_gen([json.dumps(reply)])
    first = GenerationResult(model(_with_takeaways(4)), "main-model", 1, [], ["카드 뺌"])
    r = gen.review("근로장려금", SOURCES, 45, first)
    assert r.content.title.endswith("(검토)") and "카드 뺌" in r.fixes and "표를 자료대로 고침" in r.fixes
    assert "[검수할 글]" in client.calls[0][1]
    assert not any("자동 검토" in w for w in r.warnings)


def test_review_trims_takeaways_when_model_leaves_four():
    reply = {"fixes": [], "unresolved": ["자료끼리 금액이 다름"], "content": _with_takeaways(4)}
    gen, _ = make_gen([json.dumps(reply)])
    r = gen.review("근로장려금", SOURCES, 45, GenerationResult(model(_with_takeaways(4)), "m", 1, []))
    assert sum(1 for s in r.content.sections if s.takeaway) == 3
    assert any("요약 줄" in f for f in r.fixes)
    assert any("판단하지 못한 것: 자료끼리 금액이 다름" in w for w in r.warnings)


def test_review_keeps_original_when_result_fails_checks():
    broken = good_data()
    broken["sections"] = broken["sections"][:2]                       # 소제목 개수 불합격
    reply = {"fixes": ["무언가 고침"], "unresolved": [], "content": broken}
    gen, _ = make_gen([json.dumps(reply)])
    original = model(good_data())
    r = gen.review("근로장려금", SOURCES, 45, GenerationResult(original, "m", 1, []))
    assert r.content == original and "무언가 고침" not in r.fixes
    assert any("처음 글을 그대로" in w for w in r.warnings)


def test_review_keeps_original_when_new_unsourced_figure_appears():
    invented = good_data()
    invented["intro"].append(P("최대 777만 원까지 받습니다."))
    gen, _ = make_gen([json.dumps({"fixes": [], "unresolved": [], "content": invented})])
    original = model(good_data())
    assert gen.review("근로장려금", SOURCES, 45, GenerationResult(original, "m", 1, [])).content == original


def test_review_model_error_keeps_original_and_asks_person():
    gen, _ = make_gen([api_error(400)])
    original = model(good_data())
    r = gen.review("근로장려금", SOURCES, 45, GenerationResult(original, "m", 1, []))
    assert r.content == original and any("자동 검토를 하지 못했습니다" in w for w in r.warnings)


def test_style_only_warnings_are_hidden_after_review():
    from src.ai_generator import needs_person
    assert not needs_person("소제목 1 문단이 조금 깁니다(260자).")
    assert not needs_person("자료로 확인 안 된 내용: 지자체 추가 지원")
    assert needs_person("자료에서 못 찾은 숫자(확인 필요): 500만 원")


def test_calculated_example_figures_pass_but_wrong_math_is_still_flagged():
    from src.ai_generator import calc_check_note, missing_figures
    srcs = [Source("S1", "https://a.go.kr", "t", "월세액 연 1,000만 원 한도, 총급여 5,500만 원 이하 17%, 8,000만 원 이하 15%")]
    data = good_data()
    data["intro"].append(P("월세로 600만 원을 냈다면 17%를 적용해 102만 원을 공제받습니다. "
                           "한도 1,000만 원을 채우면 최대 170만 원입니다."))
    data["intro"].append(P("15%를 적용하면 1,000만 원에서 160만 원이 공제됩니다."))      # 틀린 계산
    data["title"] = "월세 세액공제 최대 170만 원 받는 법 정리"          # 제목은 본문 검산을 따른다
    c = model(data)
    missing = missing_figures(c, srcs)
    assert "160만 원" in missing
    assert not {"600만 원", "170만 원"} & set(missing)
    note = calc_check_note(c, srcs)[0]
    assert "170만 원 = 1,000만 원 × 17%" in note and "가정한 값: 600만 원" in note and "160만" not in note


def test_calculation_needs_rate_from_sources():
    from src.ai_generator import missing_figures
    srcs = [Source("S1", "https://a.go.kr", "t", "한도 1,000만 원")]
    data = good_data()
    data["intro"].append(P("1,000만 원에 30%를 적용하면 300만 원입니다."))         # 30%가 자료에 없음
    assert {"300만 원", "30%"} <= set(missing_figures(model(data), srcs))


def test_calculation_does_not_excuse_invented_limits_or_other_paragraphs():
    from src.ai_generator import missing_figures
    srcs = [Source("S1", "https://a.go.kr", "t", "공제율 17%, 지원율 100%, 한도 1,000만 원")]
    data = good_data()
    data["intro"].append(P("한도는 600만 원이며 17%를 적용해 최대 102만 원입니다."))   # 단정문의 600만 원은 가정값 아님
    data["intro"].append(P("가입 한도는 170만 원입니다."))         # 다른 문단에서 검산돼도 여기서는 근거 없음
    data["intro"].append(P("1,000만 원의 17%인 170만 원을 공제합니다."))
    data["intro"].append(P("1,000만 원에 100%를 적용해 995만 원을 받습니다."))        # 100%는 검산에 안 씀
    data["intro"].append(P("1,000만 원에 17%를 적용하면 169만 원입니다."))            # 반올림 범위를 넘는 오차
    missing = set(missing_figures(model(data), srcs))
    # 단정문의 600만 원은 근거가 없어 곱하는 쪽으로도 못 쓰므로 102만 원도 함께 남는다
    assert {"600만 원", "102만 원", "170만 원", "995만 원", "169만 원"} <= missing
    assert "600만 원" in missing_figures(model(data), srcs, with_calc=False)


def test_max_amount_with_limit_in_another_paragraph_of_same_section():
    """'15%가 적용됩니다. … 최대 150만 원'처럼 한도가 같은 소제목의 다른 문단에 있으면 검산으로 인정."""
    from src.ai_generator import derived_figures, missing_figures
    srcs = [Source("S1", "https://a.go.kr", "t", "공제율 100분의 15, 100분의 17. 연 1천만원 한도. 3,200만 원")]
    data = good_data()
    sec = data["sections"][0]
    sec["blocks"] = [
        para("월세액은 연 1,000만 원까지 인정됩니다."),
        para("이 구간은 15% 공제율이 적용되어 연간 최대 150만 원까지 공제됩니다."),     # 비율은 이 문단, 한도는 위 문단
        {"type": "table", "table": {"caption": "구간별", "headers": ["구간", "공제율", "최대"],
                                    "rows": [["낮은 구간", "17%", "170만 원"]]}},       # 표는 같은 줄의 비율
        para("가입 한도는 102만 원입니다."),                  # 비율도 한도도 이 문단에 없음 → 인정 안 함
        para("3,200만 원 이하이면 최대 480만 원입니다."),     # 금액이 이 문단에 있어 인정되지만 계산식이 그대로 보고됨
    ]
    missing = set(missing_figures(model(data), srcs))
    derived = derived_figures(model(data), srcs)
    assert derived["won:1500000"] == "150만 원 = 1,000만 원 × 15%"
    assert derived["won:1700000"] == "170만 원 = 1,000만 원 × 17%"
    assert "102만 원" in missing and not {"150만 원", "170만 원"} & missing
    # 억지로 맞춘 계산도 사람에게 계산식으로 보이므로 승인 때 걸러진다(한도만 적힌 요약 문장을 살리기 위한 선택)
    assert derived["won:4800000"] == "480만 원 = 3,200만 원 × 15%"


def test_max_amount_needs_limit_and_rate_from_sources_in_same_section():
    from src.ai_generator import missing_figures
    srcs = [Source("S1", "https://a.go.kr", "t", "공제율 15%")]
    data = good_data()
    data["sections"][0]["blocks"] = [para("한도는 1,000만 원입니다."), para("15%를 적용해 최대 150만 원입니다.")]
    data["sections"][1]["blocks"] = [para("연 1,000만 원까지입니다.")]
    data["sections"][2]["blocks"] = [para("다른 소제목에서는 20%를 적용해 최대 200만 원입니다.")]
    missing = set(missing_figures(model(data), [*srcs, Source("S2", "https://b.go.kr", "t", "비율 20%")]))
    # 1,000만 원이 자료에 없으니 150만 원도 인정 안 함. 200만 원은 한도가 다른 소제목에만 있어 인정 안 함
    assert {"1,000만 원", "150만 원", "200만 원"} <= missing


def test_text_units_cover_same_texts_as_all_texts_without_narration():
    from src.ai_generator import _all_texts, _text_units
    c = model(good_data())
    narr = {sc.narration for sc in c.shorts.scenes}
    assert [u[0] for u in _text_units(c)] == [t for t in _all_texts(c) if t not in narr]


def test_wide_calc_only_inside_sections_and_by_structure():
    """소제목 밖(도입)은 넓은 범위 검산 안 함. 같은 글자의 표 칸이 다른 소제목에 있어도 그 소제목 근거로만 본다."""
    from src.ai_generator import missing_figures
    srcs = [Source("S1", "https://a.go.kr", "t", "한도 1천만원, 공제율 100분의 15")]
    data = good_data()
    data["intro"].append(P("15%를 적용하면 최대 150만 원입니다."))      # 한도가 본문 소제목에만 있음
    data["sections"][0]["blocks"] = [para("한도는 1,000만 원입니다."),
                                     {"type": "table", "table": {"caption": "표", "headers": ["율", "최대"],
                                                                 "rows": [["15%", "최대 150만 원"]]}}]
    data["sections"][1]["blocks"] = [{"type": "table", "table": {"caption": "표", "headers": ["율", "최대"],
                                                                 "rows": [["15%", "최대 150만 원"]]}}]
    assert "150만 원" in missing_figures(model(data), srcs)     # 도입과 둘째 소제목은 한도 근거 없음
    data["intro"].pop()
    data["sections"][1]["blocks"].insert(0, para("연 1,000만 원까지입니다."))
    assert "150만 원" not in missing_figures(model(data), srcs)


# ---------- 사실 확인 단계 ----------

FACT_SOURCES = [
    Source("S1", "https://www.nts.go.kr/a", "근로장려금", "부채는 재산에서 빼지 않습니다. 반기신청은 근로소득만 있는 거주자가 할 수 있습니다. " + "자료1 " * 30),
    Source("S2", "https://www.nts.go.kr/b", "신청자격", "자료2 " * 50),
]


def _claims(*items):
    return json.dumps({"claims": [dict(zip(("sentence", "verdict", "source_id", "evidence", "note"), i))
                                  for i in items]})


def test_fact_check_passes_when_quotes_are_in_sources():
    gen, client = make_gen([_claims(("빼지 않습니다.", "supported", "S1", "부채는 재산에서  빼지 않습니다", ""))])
    first = GenerationResult(model(good_data()), "m", 1, ["경고"], ["고침"])
    r, report = gen.fact_check("근로장려금", FACT_SOURCES, 45, first)
    assert report.hold is False and report.remaining == [] and r is first
    assert len(client.calls) == 1 and "[확인할 글" in client.calls[0][1]


def test_fact_check_quote_not_in_source_counts_as_unsupported():
    from src.ai_generator import FactCheckOutcome, judge_claims
    outcome = FactCheckOutcome.model_validate_json(_claims(
        ("빼지 않습니다.", "supported", "S1", "부채는 빼 줍니다", ""),            # 자료에 없는 인용
        ("빼지 않습니다.", "supported", "S1", "부채는 재산에서 빼지 않습니다", ""),  # 같은 문장 반복은 한 번만
        ("가 나 다", "supported", "S2", "자료", "")))                             # 너무 짧은 인용
    issues = judge_claims(outcome, FACT_SOURCES)
    assert [(i.sentence, i.verdict) for i in issues] == [("빼지 않습니다.", "unsupported"), ("가 나 다", "unsupported")]


def test_fact_check_fixes_contradicted_sentence_and_does_not_hold():
    fixed = good_data()
    fixed["faq"][1]["answer"] = "근로소득만 있는 거주자입니다."
    gen, client = make_gen([
        _claims(("근로소득만 있는 분입니다.", "contradicted", "S1", "근로소득만 있는 거주자", "자료에는 거주자로 적힘")),
        json.dumps({"fixes": ["반기신청 대상을 자료대로 고침"], "unresolved": [], "content": fixed}),
        _claims(("근로소득만 있는 거주자입니다.", "supported", "S1", "근로소득만 있는 거주자", ""))])
    r, report = gen.fact_check("근로장려금", FACT_SOURCES, 45, GenerationResult(model(good_data()), "m", 1, []))
    assert report.hold is False and report.fixed == 1 and report.remaining == []
    assert r.content.faq[1].answer == "근로소득만 있는 거주자입니다."
    assert "사실 확인 후 고침: 반기신청 대상을 자료대로 고침" in r.fixes
    assert "사실 확인 결과 자료와 다름" in client.calls[1][1] and not any(w.startswith("사실 확인:") for w in r.warnings)
    article_part = client.calls[2][1].split("[확인할 글")[1]
    assert "근로소득만 있는 거주자입니다." in article_part and "빼지 않습니다" not in article_part  # 바뀐 문장만 재대조


def test_fact_check_holds_when_fix_is_rejected():
    broken = good_data()
    broken["sections"] = broken["sections"][:2]                       # 소제목 개수 불합격 → 고친 글 버림
    gen, _ = make_gen([_claims(("12월 1일까지는 기한 후 신청이 가능하니 홈택스에서 먼저 조회해 보세요.",
                                "unsupported", "", "", "기한 후 신청 마감일이 자료에 없음")),
                       json.dumps({"fixes": ["무언가"], "unresolved": [], "content": broken})])
    original = model(good_data())
    r, report = gen.fact_check("근로장려금", FACT_SOURCES, 45, GenerationResult(original, "m", 1, []))
    assert r.content == original and report.hold is True and report.fixed == 0
    assert any(w.startswith("사실 확인: 자료에서 근거를 못 찾음") and "마감일" in w for w in r.warnings)


def test_fact_check_keeps_unlocatable_sentence_as_remaining():
    fixed = good_data()
    fixed["closing"] = P("기한 후 신청 기간은 국세청 안내에서 확인해 보세요.")
    gen, _ = make_gen([_claims(("12월 1일까지 신청 가능", "unsupported", "", "", "")),   # 글과 다르게 옮겨 적음
                       json.dumps({"fixes": ["마감일 뺌"], "unresolved": [], "content": fixed}),
                       _claims()])
    r, report = gen.fact_check("근로장려금", FACT_SOURCES, 45, GenerationResult(model(good_data()), "m", 1, []))
    assert report.hold is True and report.remaining and report.fixed == 0


def test_fact_check_model_error_holds_and_keeps_content():
    gen, _ = make_gen([api_error(400)])
    original = model(good_data())
    r, report = gen.fact_check("근로장려금", FACT_SOURCES, 45, GenerationResult(original, "m", 1, []))
    assert r.content == original and report.hold and report.failed
    assert any("사실 확인을 하지 못했습니다" in w for w in r.warnings)


def test_fact_quote_allows_table_pipes_and_joined_pieces():
    from src.ai_generator import _quoted_in_sources, _fact_norm
    texts = [_fact_norm("예정신고\n1.1.~3.31.\n4.1.~4.25.\n법인사업자\n신청방법\n인터넷, 방문, 우편\n수수료\n수수료 없음")]
    assert _quoted_in_sources("예정신고 | 1.1.~3.31. | 4.1.~4.25. | 법인사업자", texts)
    assert _quoted_in_sources("인터넷, 방문, 우편 … 수수료 없음", texts)
    assert not _quoted_in_sources("인터넷, 방문, 우편 … 수수료 5천 원", texts)     # 조각 하나라도 없으면 안 됨
    assert not _quoted_in_sources("방문 … 우편", texts)                            # 너무 짧은 인용
    assert _quoted_in_sources("신청방법\n인터넷, 방문, 우편\n수수료 없음", texts)   # 떨어진 구절을 줄바꿈으로 이음
    assert not _quoted_in_sources("예정신고 1.1.~3.31. 5.1.~5.25. 법인사업자", texts)  # 날짜 하나가 자료에 없음


def test_fact_check_rechecks_rewritten_sentence_and_holds_new_unsupported():
    fixed = good_data()
    fixed["faq"][1]["answer"] = "근로소득만 있는 분이 7월에 신청합니다."       # 고치면서 자료에 없는 내용이 들어감
    gen, _ = make_gen([
        _claims(("근로소득만 있는 분입니다.", "contradicted", "S1", "근로소득만 있는 거주자", "")),
        json.dumps({"fixes": ["고침"], "unresolved": ["반기 신청 시기"], "content": fixed}),
        _claims(("근로소득만 있는 분이 7월에 신청합니다.", "supported", "S1", "근로소득만 있는 7월", ""))])
    r, report = gen.fact_check("근로장려금", FACT_SOURCES, 45, GenerationResult(model(good_data()), "m", 1, []))
    assert report.hold is True and report.fixed == 1
    assert any("7월에 신청" in line for line in report.remaining)
    assert any("반기 신청 시기" in line for line in report.remaining)       # 고치는 AI가 판단 못 한 것도 보류


def test_fact_check_recheck_error_holds():
    fixed = good_data()
    fixed["faq"][1]["answer"] = "근로소득만 있는 거주자입니다."
    gen, _ = make_gen([_claims(("근로소득만 있는 분입니다.", "contradicted", "S1", "", "")),
                       json.dumps({"fixes": [], "unresolved": [], "content": fixed}), api_error(400)])
    _, report = gen.fact_check("근로장려금", FACT_SOURCES, 45, GenerationResult(model(good_data()), "m", 1, []))
    assert report.hold and report.failed


def test_fact_units_include_eligibility_and_link_notes():
    from src.ai_generator import _article_units, _quoted_in_sources, _fact_norm
    data = good_data()
    data["eligibility"] = "총급여 7천만 원 이하 무주택 세대주"
    data["action_links"] = [{"label": "홈택스", "url": "https://hometax.go.kr", "purpose": "apply", "note": "평일 9~18시 상담"}]
    units = _article_units(model(data))
    assert "대상: 총급여 7천만 원 이하 무주택 세대주" in units and "평일 9~18시 상담" in units
    assert not _quoted_in_sources("연 3 회 신청", [_fact_norm("연 2 회 신청할 수 있습니다")])   # 한 글자 숫자도 대조


def test_reference_line_does_not_repeat_gijun():
    from src.blog_html import reference_note_text
    assert reference_note_text("2026년 정기신청(2025년 소득) 기준") == "2026년 정기신청(2025년 소득)"
    assert reference_note_text("금융위원회 공식 안내 및 정책 자료 기준 ") == "금융위원회 공식 안내 및 정책 자료"
    assert reference_note_text("2026년 1월 공고") == "2026년 1월 공고"
    assert reference_note_text("기준") == "기준"
    html = render_blog_html(MasterContent.model_validate(good_data()), [])
    assert '<p class="jc-reference">기준: 2026년 정기신청(2025년 소득)</p>' in html


def test_structured_main_only_never_calls_other_models():
    # 자동 반영 판정은 1순위 결과만 믿으므로, 1순위가 한도면 2·3순위를 부르지 않는다(호출 낭비 방지)
    client = FakeClient([api_error(429)])
    gen = ContentGenerator(client, "main-model", second_model="second-model",
                           fallback_model="backup-model", sleep=lambda s: None, retry_waits=(1, 1))
    with pytest.raises(ModelBusyError):
        gen.structured("q", dict, "sys", main_only=True)
    assert [m for m, _ in client.calls] == ["main-model"]


def test_structured_default_still_falls_back():
    client = FakeClient([api_error(429), '{"a": 1}'])
    gen = ContentGenerator(client, "main-model", second_model="second-model",
                           fallback_model="backup-model", sleep=lambda s: None, retry_waits=(1, 1))
    assert gen.structured("q", dict, "sys")[1] == "second-model"


# ---------- 같은 페이지 탭 함께 읽기 ----------

TAB_PAGE = ("""<html><title>국가장학금</title><body>
  <nav><a href="/ko/scholar.do?pg=other01">대출 신청</a></nav>
  <ul class="tab"><li><a href="/ko/scholar.do?pg=sch01&ttab1=1">지원금액</a></li>
  <li><a href="/ko/scholar.do?pg=sch01&ttab1=4">유의사항</a></li>
  <li><a href="https://other.go.kr/ko/scholar.do?pg=sch01&ttab1=2">지원절차</a></li></ul>
  <main>%s</main></body></html>""" % ("지원 대상 설명 " * 40)).encode()


def test_tab_links_only_same_page_tabs():
    from src.source_fetcher import tab_links
    links = [("/ko/scholar.do?pg=other01", "대출 신청"), ("/ko/scholar.do?pg=sch01&ttab1=1", "지원금액"),
             ("/ko/scholar.do?pg=sch01&ttab1=4", "유의사항"), ("https://other.go.kr/ko/scholar.do?pg=sch01&ttab1=2", "지원절차"),
             ("/ko/scholar.do?pg=sch01", "지원대상"), ("/ko/scholar.do?pg=sch01&ttab1=1#top", "지원금액")]
    got = tab_links("https://www.kosaf.go.kr/ko/scholar.do?pg=sch01", links)
    assert got == [("https://www.kosaf.go.kr/ko/scholar.do?pg=sch01&ttab1=1", "지원금액")]
    assert tab_links("https://a.go.kr/page.do", [("/page.do?x=1", "지원금액")]) == []   # 기준 주소 값이 없으면 탭으로 안 봄


def test_fetch_source_appends_tab_text(monkeypatch):
    tab = ("<html><main>%s<p>가 구간 300만원 600만원</p></main></html>" % ("지원 대상 설명 " * 40)).encode()
    opened = fake_network(monkeypatch, [FakeResp(200, {"Content-Type": "text/html"}, TAB_PAGE),
                                        FakeResp(200, {"Content-Type": "text/html"}, tab)])
    src = fetch_source("https://www.kosaf.go.kr/ko/scholar.do?pg=sch01", "S1")
    assert src.text.endswith("[탭: 지원금액]\n가 구간 300만원 600만원")   # 겹치는 줄은 다시 안 붙임
    assert [p for _h, p, _ip in opened] == ["/ko/scholar.do?pg=sch01", "/ko/scholar.do?pg=sch01&ttab1=1"]   # 다른 메뉴·사이트·유의사항은 안 읽음


def test_failed_tab_is_skipped(monkeypatch):
    fake_network(monkeypatch, [FakeResp(200, {"Content-Type": "text/html"}, TAB_PAGE),
                               FakeResp(500, {"Content-Type": "text/html"}, b"x")])
    src = fetch_source("https://www.kosaf.go.kr/ko/scholar.do?pg=sch01", "S1")
    assert "[탭:" not in src.text and "지원 대상 설명" in src.text


# ---------- 주제가 약속한 금액·신청 기간 ----------

def test_coverage_requires_amount_and_deadline_when_sources_have_them():
    from datetime import date
    from src.ai_generator import COVERAGE_PROBLEM, coverage_problems
    c = MasterContent.model_validate(good_data())
    srcs = [Source("S1", "https://a.go.kr", "t", "신청일정 : 2026. 11. 24.(화) 9시 ~ 2027. 1. 7.(목) 18시. 가 구간 600만원")]
    probs = coverage_problems("국가장학금 신청 대상과 지원 금액", c, srcs, today=date(2026, 11, 24))
    # good_data에는 '2,200만 원' 금액이 있으니 금액은 통과, 마감일(1월 7일)은 없어서 지적
    assert len(probs) == 1 and probs[0].startswith(COVERAGE_PROBLEM) and "1월 7일" in probs[0]
    assert coverage_problems("국가장학금 자격 정리", c, srcs, today=date(2026, 11, 24)) == []   # 주제가 금액·신청이 아님


def test_coverage_amount_missing_and_past_deadline_ignored():
    from datetime import date
    from src.ai_generator import coverage_problems
    data = good_data()
    data["sections"][0]["blocks"][1]["table"]["rows"] = [["단독", "기준 A"], ["홑벌이", "기준 B"]]
    c = MasterContent.model_validate(data)
    srcs = [Source("S1", "https://a.go.kr", "t", "접수기간 2025. 3. 2. ~ 2025. 3. 20. 지원 한도 최대 4억원")]
    probs = coverage_problems("신생아 특례대출 대출 한도와 신청 기간", c, srcs, today=date(2026, 11, 24))
    assert len(probs) == 1 and "금액" in probs[0]   # 지난 접수 기간은 요구하지 않음


def test_tab_links_menu_sibling_and_order():
    from src.source_fetcher import _parse_html, tab_links
    html = ("<html><header><a href='?pg=sch01&lang=ko'>신청 안내</a></header>"
            "<nav><a href='?pg=sch01&ttab1=9'>신청방법</a></nav><main>"
            "<a href='?ttab1=1&pg=sch01'>지원금액</a><a href='?pg=sch01&ttab1=1'>지원금액</a>"
            "<a href='?pg=sch02&ttab1=0'>신청 대상</a></main>"
            "<footer><a href='?pg=sch01&x=1'>개인정보 신청</a></footer></html>")
    _t, _x, links = _parse_html(html)
    assert all(label not in ("신청 안내", "신청방법", "개인정보 신청") for _h, label in links)   # 머리·메뉴·바닥 링크 제외
    page = "https://www.kosaf.go.kr/ko/scholar.do?pg=sch01&ttab1=0"
    got = tab_links(page, links)
    assert [label for _u, label in got] == ["지원금액"]   # 순서만 다른 같은 주소는 하나, pg가 다른 메뉴는 제외
    assert tab_links(page, [("?pg=sch01&ttab1=0", "지원대상")]) == []   # 지금 페이지 자신
    assert tab_links("https://a.go.kr/p.do?pg=1", [("?pg=2", "신청 방법")]) == []   # tab이 아닌 값이 바뀌면 다른 메뉴


def test_tab_moved_to_other_host_is_skipped(monkeypatch):
    fake_network(monkeypatch, [FakeResp(200, {"Content-Type": "text/html"}, TAB_PAGE),
                               FakeResp(302, {"Location": "https://other.go.kr/x?ttab1=1"}, b""),
                               FakeResp(200, {"Content-Type": "text/html"}, b"<main>" + "가 구간 300만원 ".encode() * 5 + b"</main>")])
    src = fetch_source("https://www.kosaf.go.kr/ko/scholar.do?pg=sch01", "S1")
    assert "[탭:" not in src.text


def test_tab_text_stays_within_max_chars(monkeypatch):
    tab = ("<html><main>%s</main></html>" % "".join(f"<p>금액 줄 {i} 300만원</p>" for i in range(500))).encode()
    fake_network(monkeypatch, [FakeResp(200, {"Content-Type": "text/html"}, TAB_PAGE),
                               FakeResp(200, {"Content-Type": "text/html"}, tab)])
    src = fetch_source("https://www.kosaf.go.kr/ko/scholar.do?pg=sch01", "S1", max_chars=1500)
    assert len(src.text) <= 1500 and "[탭: 지원금액]" in src.text and "지원 대상 설명" in src.text


def test_period_ends_date_forms():
    from datetime import date
    from src.ai_generator import _mentions_day, _period_ends
    t = date(2026, 11, 24)
    assert _period_ends(["신청기간: 2027.01.07 ~ 2027-02-10"], t) == {(2, 10)}          # 0 붙은 날짜·하이픈
    assert _period_ends(["신청 기간 11월 24일 ~ 1월 7일"], t) == {(1, 7)}                 # 연도 없음, 해를 넘김
    assert _period_ends(["신청기간: 3월 1일 ~ 3월 31일"], t) == set()                      # 연도 없고 올해 이미 지남
    assert _period_ends(["접수기간 9.00 ~ 12.30, 금리 1.5 ~ 2.15%"], t) == set()          # 시각·소수는 날짜 아님
    assert _period_ends(["신청 기간: 1차 2026.11.24 ~ 2027.1.7, 2차 2027.3.2 ~ 2027.3.20"], t) == {(1, 7), (3, 20)}
    assert _mentions_day("마감은 2027-01-07입니다", 1, 7) and _mentions_day("1월 7일까지", 1, 7)
    assert not _mentions_day("11월 7일", 1, 7)


def test_coverage_money_needs_won_and_topic_wording():
    from datetime import date
    from src.ai_generator import _has_money, coverage_problems
    assert not _has_money("수혜 인원 10만 명") and _has_money("(단위: 만원) 가 구간 300")
    data = good_data()
    data["sections"][0]["blocks"][1]["table"]["rows"] = [["단독", "기준 A"], ["홑벌이", "기준 B"]]
    c = MasterContent.model_validate(data)
    srcs = [Source("S1", "https://a.go.kr", "t", "최대 330만원 지급")]
    assert coverage_problems("근로장려금 자격 요건", c, srcs, today=date(2026, 11, 24)) == []   # 프로그램 이름만으로는 안 걸림
    people = [Source("S1", "https://a.go.kr", "t", "지원 인원 10만 명")]
    assert coverage_problems("지원 금액 정리", c, people, today=date(2026, 11, 24)) == []       # 자료에 돈이 없음


def test_period_ends_stops_at_next_field():
    from datetime import date
    from src.ai_generator import _period_ends
    t = date(2026, 11, 24)
    text = "신청기간: 2026. 2. 25.(수) ~ 2026. 6. 30.(화) *지역별 선착순 ○ 사용기간: 발급일 ~ 2026. 12. 31.(목)"
    assert _period_ends([text], t) == set()   # 사용 마감(12. 31.)을 신청 마감으로 보지 않음
    assert _period_ends(["신청기간 2026.11.24 ~ 2027.1.7 사용기간 ~ 2027.12.31"], t) == {(1, 7)}
