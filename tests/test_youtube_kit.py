from src.content_schema import MasterContent
from src.youtube_kit import MAX_HASHTAGS, amounts, build_upload_kit, hashtags_for, numbers_missing_from_post
from tests.test_content import good_data


def content(**over):
    data = good_data()
    data.update(over)
    return MasterContent.model_validate(data)


def test_hashtags_strip_spaces_dedupe_and_keep_channel_tag():
    c = content(focus_keyword="근로 장려금", tags=["근로장려금", "홈택스", "자녀·장려금", "국세청", "세금", "환급"])
    tags = hashtags_for(c)
    assert tags[0] == "#근로장려금" and "#자녀장려금" in tags
    assert len(tags) == MAX_HASHTAGS and tags[-1] == "#잘체크" and len(set(tags)) == len(tags)


def test_kit_has_title_link_reference_and_hashtags_last():
    c = content(tags=["홈택스"])
    kit = build_upload_kit(c, "https://jalcheck.com/a/")
    assert kit.title == c.shorts.title
    assert "https://jalcheck.com/a/" in kit.description and c.reference_note in kit.description
    assert kit.description.splitlines()[-1] == " ".join(kit.hashtags)


def shorts_content(*narrations, title="연금저축 한도", meta="세액공제 한도 정리", note="2026년 기준"):
    data = good_data()
    data["shorts"]["title"] = title
    data["shorts"]["scenes"] = [dict(data["shorts"]["scenes"][0], narration=n) for n in narrations]
    data.update(meta_description=meta, reference_note=note)
    return MasterContent.model_validate(data)


def test_number_missing_from_post_is_reported_with_place():
    c = shorts_content("총급여 5,400만 원 이하라면 16.5%입니다.", "5,500만 원을 넘으면 13.2%입니다.")
    post = "<p>2026년 기준 총급여 <strong>5,500만 원</strong> 이하는 16.5%, 넘으면 13.2%</p>"
    assert numbers_missing_from_post(c, post) == ["장면 1: 5,400만"]


def test_commas_and_entities_do_not_cause_false_alarm():
    c = shorts_content("최대 148만 5천 원, 총 5,500만 원 기준", note="2026년&nbsp;기준")
    post = "<p>2026년 기준 5500만&nbsp;원 이하, 최대 148만 5천 원</p>"
    assert numbers_missing_from_post(c, post) == []


def test_numbers_inside_tag_attributes_do_not_count():
    c = shorts_content("1,200원을 돌려받습니다.")
    post = '<p>2026년</p><img width="1200" src="/a-1200.jpg">'
    assert numbers_missing_from_post(c, post) == ["장면 1: 1,200"]


def test_single_digit_integers_ignored_but_decimals_checked():
    c = shorts_content("3개월 안에 4.6% 할인")
    assert numbers_missing_from_post(c, "<p>2026년 3개월</p>") == ["장면 1: 4.6"]


def test_title_description_and_reference_are_checked_once_each():
    c = shorts_content("2026년", title="2027 새 기준", meta="2027년 바뀝니다", note="2026년 기준")
    assert numbers_missing_from_post(c, "<p>2026년</p>") == ["쇼츠 제목: 2027", "유튜브 설명: 2027"]


def test_korean_units_match_full_won_amounts_with_rounding():
    c = shorts_content("4인 가구 약 311만 원 이하", "서울 1인 최대 36만 9천 원", "최대 152만 원")
    post = "<td>3,117,474원</td><td>369,000원</td><td>1,525,000원</td><p>2026년</p>"
    assert numbers_missing_from_post(c, post) == []


def test_korean_unit_amount_with_different_value_is_reported():
    c = shorts_content("서울 1인 최대 36만 9천 원")
    assert numbers_missing_from_post(c, "<td>2026년 359,000원</td>") == ["장면 1: 36만 9천"]


def test_amounts_join_bigger_then_smaller_units():
    got = [(a.raw, a.value, a.unit) for a in amounts("최대 1억 2천만 원, 148만 5천 원, 5,500만")]
    assert got == [("1억 2천만", 120000000, 10000000), ("148만 5천", 1485000, 1000), ("5,500만", 55000000, 10000)]


def test_decimal_unit_amounts_compare_with_their_precision():
    assert numbers_missing_from_post(shorts_content("최대 1.5억 원"), "<p>2026년 1.5억 원</p>") == []
    assert numbers_missing_from_post(shorts_content("최대 1억 원"), "<p>2026년 1.5억 원</p>") == ["장면 1: 1억"]
    assert numbers_missing_from_post(shorts_content("최대 1.5억 원"), "<p>2026년 150,000,000원</p>") == []


def test_full_won_in_shorts_matches_korean_units_in_post():
    c = shorts_content("서울 1인 최대 369,000원")
    assert numbers_missing_from_post(c, "<p>2026년 36만 9천 원</p>") == []
    assert numbers_missing_from_post(c, "<p>2026년 36만 원</p>") == ["장면 1: 369,000"]


def test_unitless_number_does_not_match_same_digits_with_unit():
    assert numbers_missing_from_post(shorts_content("수수료 36원"), "<p>2026년 36만 원</p>") == ["장면 1: 36"]
    assert numbers_missing_from_post(shorts_content("18개월 이상"), "<p>2026년 18개월</p>") == []


def test_card_and_hook_numbers_are_checked():
    """글을 고친 뒤 화면 카드·위쪽 질문에만 남은 옛 숫자도 잡는다(2026-11-11 검토 반영)."""
    c = shorts_content("기준이 바뀌었습니다.")
    data = c.model_dump()
    data["shorts"]["hook"] = "5,400만 원 이하?"
    data["shorts"]["scenes"][0]["card"] = {"kind": "number", "small": "총급여", "big": "5,400만 원"}
    c = MasterContent.model_validate(data)
    post = "<p>2026년 기준 총급여 5,500만 원 이하</p>"
    assert numbers_missing_from_post(c, post) == ["쇼츠 위쪽 질문: 5,400만", "장면 1 카드: 5,400만"]
