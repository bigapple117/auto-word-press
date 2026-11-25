

def test_source_labels_disambiguate_same_site_title():
    from src.blog_html import source_labels
    from src.source_fetcher import Source
    menu = "글자크기\n가\n탭 하나\n탭 둘\n"
    srcs = [
        Source("S1", "https://a.kr/1", "한국장학재단", "다자녀 국가장학금\n본문"),
        Source("S2", "https://a.kr/2", "한국장학재단", "주거안정장학금\n본문"),
        Source("S3", "https://b.kr/1", "복지로", "아무 줄"),
        Source("S4", "https://c.kr/1", "국세청", menu + "월세액 세액공제\n본문"),
        Source("S5", "https://c.kr/2", "국세청", menu + "기부금 세액공제\n본문"),
        Source("S6", "https://d.kr/1", "연금", "전체\n" + "긴 설명 문장 " * 10),
        Source("S7", "https://d.kr/2", "연금", "x = a - b\n" + "다른 긴 설명 " * 10),
        Source("S8", "https://d.kr/3", "연금", "x = a − b\n" + "또 다른 긴 설명 " * 10),
    ]
    assert source_labels(srcs) == [
        "한국장학재단 · 다자녀 국가장학금",
        "한국장학재단 · 주거안정장학금",
        "복지로",
        "국세청 · 월세액 세액공제",
        "국세청 · 기부금 세액공제",
        "연금 (1)",
        "연금 (2)",
        "연금 (3)",
    ]
