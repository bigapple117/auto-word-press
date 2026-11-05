from src.content_schema import MasterContent
from src.youtube_kit import MAX_HASHTAGS, build_upload_kit, hashtags_for
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
