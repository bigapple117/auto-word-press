from src.content_schema import ActionLink
from src.link_check import check_action_links
from src.source_fetcher import Source

SOURCES = [
    Source("S1", "https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?mi=2452&cntntsId=7783", "신청자격",
           "국세청 홈택스(www.hometax.go.kr)에 접속하여 신청. 정부24 https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000338 참고"),
]


def L(url, purpose="apply", label="신청하기"):
    return ActionLink(label=label, url=url, purpose=purpose)


def test_accepts_source_url_text_url_and_bare_domain_home():
    ok, bad = check_action_links([
        L("https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?mi=2452&cntntsId=7783", "info"),
        L("www.hometax.go.kr"),
        L("http://hometax.go.kr/"),                                      # 같은 사이트 → 중복 제거
        L("https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000338", "check"),
    ], SOURCES)
    assert [c.url for c in ok] == [
        "https://www.nts.go.kr/nts/cm/cntnts/cntntsView.do?mi=2452&cntntsId=7783",
        "https://www.hometax.go.kr/",
        "https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=12100000338",
    ] and bad == []


def test_rejects_invented_unofficial_and_bad_urls():
    ok, bad = check_action_links([
        L("https://www.hometax.go.kr/websquare/apply-eitc"),              # 자료에 없는 깊은 주소(지어냄)
        L("https://blog.naver.com/eitc"),                                 # 공식 아님
        L("javascript:alert(1)"),
        L("https://www.nts.go.kr:8443/x"),
        L("https://evil.com/?q=www.hometax.go.kr"),
    ], SOURCES)
    assert ok == [] and len(bad) == 5
    assert any("자료에서 확인되지 않은" in b for b in bad) and any("공식 사이트가 아님" in b for b in bad)


def test_limit_four_and_trim():
    srcs = [Source(f"S{i}", f"https://www.a{i}.go.kr/p", "t", "") for i in range(6)]
    ok, bad = check_action_links([L(f"https://www.a{i}.go.kr/p", label="  가" * 30) for i in range(6)], srcs)
    assert len(ok) == 4 and len(bad) == 2 and len(ok[0].label) <= 40


def S(text, url="https://www.nts.go.kr/x"):
    return [Source("S1", url, "t", text)]


def test_encoded_host_in_text_is_not_split_into_another_host():
    ok, _ = check_action_links([L("https://77ww.hometax.go.kr/apply")], S("주소 https://%77ww.hometax.go.kr/apply 참고"))
    assert ok == []


def test_full_path_in_text_does_not_allow_homepage():
    ok, _ = check_action_links([L("https://www.gov.kr/")], S("정부24 https://www.gov.kr/apply 에서 신청"))
    assert ok == []
    ok, _ = check_action_links([L("https://www.gov.kr/apply")], S("정부24 https://www.gov.kr/apply 에서 신청"))
    assert len(ok) == 1


def test_trailing_slashes_and_params_compared_exactly():
    src = S("", url="https://www.gov.kr/apply;mode=1")
    assert check_action_links([L("https://www.gov.kr/apply")], src)[0] == []
    assert check_action_links([L("https://www.gov.kr/apply;mode=1")], src)[0] != []
    src2 = S("", url="https://www.gov.kr/apply")
    assert check_action_links([L("https://www.gov.kr/apply//")], src2)[0] == []


def test_query_without_path_and_korean_suffix_are_extracted():
    ok, _ = check_action_links([L("https://www.gov.kr?service=apply")], S("신청: https://www.gov.kr?service=apply 에서"))
    assert len(ok) == 1
    ok, _ = check_action_links([L("https://www.hometax.go.kr")], S("국세청 홈택스(www.hometax.go.kr)에서 신청"))
    assert len(ok) == 1
    ok, _ = check_action_links([L("https://www.hometax.go.kr")], S("www.hometax.go.kr에서 바로 신청"))
    assert len(ok) == 1


def test_at_sign_and_backslash_rejected():
    for bad in ("https://www.nts.go.kr@evil.com/", "https://evil.com\\@www.nts.go.kr/"):
        assert check_action_links([L(bad)], S(bad))[0] == []


def test_backslash_and_trailing_punctuation_in_path_not_trimmed():
    assert check_action_links([L("https://www.hometax.go.kr")], S("www.hometax.go.kr\\@evil.example 접속"))[0] == []
    assert check_action_links([L("https://www.hometax.go.kr/apply")], S("https://www.hometax.go.kr/apply! 참고"))[0] == []
    assert check_action_links([L("https://www.hometax.go.kr")], S("주소는 www.hometax.go.kr. 입니다"))[0] != []


def test_odd_ascii_does_not_cut_url():
    for text in ("https://www.hometax.go.kr/apply|detail", "https://www.hometax.go.kr/apply^x", "https://www.hometax.go.kr/apply`x"):
        assert check_action_links([L("https://www.hometax.go.kr/apply")], S(text))[0] == []
