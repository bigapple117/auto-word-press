"""유튜브 쇼츠 업로드용 제목·설명·해시태그를 만든다(사용자가 휴대폰에서 복사해 붙여 넣는 용도).

AI를 다시 부르지 않고 이미 검사를 통과한 글 내용(쇼츠 제목, 검색 설명, 태그, 기준 시점)만 쓴다.
→ 추가 비용이 없고, 검사받지 않은 새 문장이 끼어들지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.content_schema import MasterContent

TITLE_MAX = 100          # 유튜브 제목 한도
DESCRIPTION_MAX = 5000   # 유튜브 설명 한도
MAX_HASHTAGS = 5         # 많으면 유튜브가 스팸으로 볼 수 있어 적게. 앞의 3개가 제목 위에 보인다
CHANNEL_TAG = "잘체크"


@dataclass(frozen=True)
class UploadKit:
    title: str
    description: str
    hashtags: list[str]


def _hashtag(word: str) -> str:
    """해시태그는 띄어쓰기·기호가 있으면 끊기므로 한글·영문·숫자만 남긴다."""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", word)


def hashtags_for(content: MasterContent) -> list[str]:
    seen: list[str] = []
    for word in [content.focus_keyword, *content.tags, CHANNEL_TAG]:
        tag = _hashtag(word or "")
        if tag and tag not in seen:
            seen.append(tag)
    if CHANNEL_TAG not in seen[:MAX_HASHTAGS]:            # 채널 이름 태그는 항상 남긴다
        seen = [t for t in seen if t != CHANNEL_TAG][:MAX_HASHTAGS - 1] + [CHANNEL_TAG]
    return ["#" + t for t in seen[:MAX_HASHTAGS]]


def build_upload_kit(content: MasterContent, post_url: str) -> UploadKit:
    title = content.shorts.title.strip()[:TITLE_MAX]
    tags = hashtags_for(content)
    lines = [
        content.meta_description.strip(),
        "",
        f"정보 기준: {content.reference_note.strip()}",
        "",
        "자세한 조건과 신청 방법은 블로그 잘체크에서 확인하세요(프로필 링크).",
        post_url,
        "",
        "영상은 공식 자료를 바탕으로 만들었지만, 신청 전에는 담당 기관의 최신 안내를 꼭 다시 확인해 주세요.",
        "",
        " ".join(tags),
    ]
    return UploadKit(title, "\n".join(lines)[:DESCRIPTION_MAX], tags)
