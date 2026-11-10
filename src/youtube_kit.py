"""유튜브 쇼츠 업로드용 제목·설명·해시태그를 만든다(사용자가 휴대폰에서 복사해 붙여 넣는 용도).

AI를 다시 부르지 않고 이미 검사를 통과한 글 내용(쇼츠 제목, 검색 설명, 태그, 기준 시점)만 쓴다.
→ 추가 비용이 없고, 검사받지 않은 새 문장이 끼어들지 않는다.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

from src.content_schema import MasterContent

TITLE_MAX = 100          # 유튜브 제목 한도
DESCRIPTION_MAX = 5000   # 유튜브 설명 한도
MAX_HASHTAGS = 5         # 많으면 유튜브가 스팸으로 볼 수 있어 적게. 앞의 3개가 제목 위에 보인다
CHANNEL_TAG = "잘체크"
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
MAX_REPORTED_NUMBERS = 10


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


# 쇼츠 배경음악(assets/music/CREDITS.md) 출처 표기 - CC BY 4.0 조건
MUSIC_CREDIT = ('배경 음악: "Daily Beetle" Kevin MacLeod (incompetech.com), '
                "Licensed under Creative Commons: By Attribution 4.0 License")


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
        MUSIC_CREDIT,
        "",
        " ".join(tags),
    ]
    return UploadKit(title, "\n".join(lines)[:DESCRIPTION_MAX], tags)


def _norm_number(raw: str) -> str:
    """'5,400'과 '5400'을 같은 숫자로 본다."""
    return raw.rstrip(",").replace(",", "")


def post_text(post_html: str) -> str:
    """태그 속 속성값(사진 크기·주소 번호 등)이 숫자 비교에 섞이지 않게 글자만 남긴다."""
    return html.unescape(re.sub(r"<[^>]+>", " ", post_html))


UNIT_VALUE = {"억": 10 ** 8, "천만": 10 ** 7, "만": 10 ** 4, "천": 10 ** 3}
NUMBER_UNIT = re.compile(r"(\d[\d,]*(?:\.\d+)?)(?:\s*(억|천만|만|천))?")


@dataclass(frozen=True)
class Amount:
    raw: str        # 쓰인 그대로(예: '36만 9천')
    digits: str     # 첫 숫자의 쉼표 뺀 모양(단위 없는 숫자 비교용)
    value: int      # 원 단위로 바꾼 값(단위 있는 금액 비교용)
    unit: int       # 가장 작은 자리(만 원 단위로 말했으면 10000, '1.5억'이면 1000만)


def amounts(text: str) -> list[Amount]:
    """숫자와 단위(억·천만·만·천)를 읽는다. '36만 9천'처럼 큰 단위 뒤에 작은 단위가 이어지면 하나로 합친다."""
    out: list[Amount] = []
    starts: list[int] = []
    prev_end, prev_unit = -1, 0
    for m in NUMBER_UNIT.finditer(text):
        digits = _norm_number(m.group(1))
        unit = UNIT_VALUE.get(m.group(2) or "", 1)
        try:
            value = round(float(digits) * unit)
        except ValueError:
            continue
        if "." in digits:            # 소수점 아래 자리만큼 비교 단위를 잘게(1.5억 → 1천만 단위)
            unit = max(1, unit // 10 ** len(digits.split(".")[1]))
        joined = (out and 1 < unit < prev_unit and m.group(2) and not text[prev_end:m.start()].strip())
        if joined:
            last = out[-1]
            out[-1] = Amount(text[starts[-1]:m.end()], last.digits, last.value + value, unit)
        else:
            out.append(Amount(m.group(0).rstrip(","), digits, value, unit))
            starts.append(m.start())
        prev_end, prev_unit = m.end(), unit
    return out


def _same_amount(said: Amount, written: int) -> bool:
    """만·천 단위로 말한 금액이 글의 원 단위 금액을 버림·반올림한 값과 같으면 같은 금액으로 본다.
    예: '약 311만'과 3,117,474원, '152만'과 1,525,000원, '36만 9천'과 369,000원. 차이가 10% 이상이면 다른 값."""
    u = said.unit
    if written == said.value:
        return True
    near = abs(written - said.value) < said.value * 0.1     # '1억'과 1.5억처럼 단위가 커서 크게 다른 값은 제외
    return near and (written // u * u == said.value or round(written / u) * u == said.value)


def numbers_missing_from_post(content: MasterContent, post_html: str) -> list[str]:
    """쇼츠 영상(제목·대사)과 유튜브 설명에 쓰는 숫자 중 지금 글 본문에 없는 것을 '어디: 숫자'로 돌려준다.

    글을 직접 고친 뒤 쇼츠는 옛 내용 그대로인 경우, 또는 처음부터 쇼츠와 글이 어긋난 경우를 잡기 위한 것.
    억·만·천 단위 금액은 글의 원 단위 금액과 값으로 비교하고(버림·반올림 허용), 나머지 숫자는 표기로 비교한다.
    한 자리 정수(장면 번호·'3개월' 등)는 흔하고 판단에 도움이 안 돼 제외한다. 결과는 '확인 요청'으로만 쓴다.
    """
    written = amounts(post_text(post_html))
    written_digits = {a.digits for a in written}
    written_values = [a.value for a in written]
    places = [("쇼츠 제목", content.shorts.title)]
    places += [(f"장면 {i}", s.narration) for i, s in enumerate(content.shorts.scenes, 1)]
    places += [("유튜브 설명", content.meta_description), ("정보 기준", content.reference_note)]
    missing: list[str] = []
    for where, text in places:
        for a in amounts(text):
            if a.unit > 1:
                found = any(_same_amount(a, w) for w in written_values)
            elif "." in a.digits:
                found = a.digits in written_digits
            else:   # 값으로만 비교: '369,000원'과 글의 '36만 9천 원'은 같고, '36원'과 '36만 원'은 다르다
                found = len(a.digits) < 2 or a.value in written_values
            item = f"{where}: {a.raw}"
            if not found and item not in missing:
                missing.append(item)
    return missing
