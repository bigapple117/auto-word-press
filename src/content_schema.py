"""AI가 돌려줄 마스터 콘텐츠의 형식. 블로그 글과 쇼츠 대본을 한 번에 담는다.

AI는 HTML을 쓰지 않고 구조화된 글자만 돌려준다. HTML은 blog_html.py가 만든다.
소제목 안은 '블록' 목록이라, 필요한 것만 골라 원하는 순서로 쓴다(같은 틀 반복 방지).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Span(BaseModel):
    text: str
    strong: bool = Field(default=False, description="꼭 기억할 숫자·기한·주의점만 true. 소제목당 최대 2곳")


class Paragraph(BaseModel):
    spans: list[Span] = Field(description="문단을 이루는 조각들. 1~3문장, 공백 포함 250자 이내")


class Table(BaseModel):
    caption: str = Field(description="표 제목(무엇을 비교하는 표인지)")
    headers: list[str] = Field(description="열 이름 2~4개. 짧게")
    rows: list[list[str]] = Field(description="각 행의 칸. 칸 수는 headers와 같게, 칸 내용은 짧게")


class Block(BaseModel):
    type: Literal["paragraph", "table", "checklist", "note"]
    paragraph: Paragraph | None = Field(default=None, description="type=paragraph일 때")
    table: Table | None = Field(default=None, description="type=table일 때. 숫자·조건 비교는 표로")
    items: list[str] | None = Field(default=None, description="type=checklist일 때 독자가 스스로 확인할 항목 3~6개")
    note_kind: Literal["caution", "tip"] | None = Field(
        default=None, description="type=note일 때: caution(놓치면 불이익) 또는 tip(편집자 안내)")
    note: Paragraph | None = Field(default=None, description="type=note일 때 1~2문장")


class Section(BaseModel):
    heading: str = Field(description="소제목. 독자 질문형·행동형·명사형을 섞는다. 모두 질문형으로 만들지 않는다")
    takeaway: str | None = Field(default=None, description=(
        "이 소제목에서 가장 중요한 해석이나 주의점 한 문장. 정말 필요한 소제목에만(글 전체 2~3곳)"))
    blocks: list[Block] = Field(description="이 소제목의 내용 블록 1~5개. 필요한 것만, 순서 자유")
    source_ids: list[str] = Field(description="이 소제목의 사실 근거가 된 자료 번호(S1 등)")
    image_query: str | None = Field(default=None, description=(
        "사진이 이해를 도울 때만 영어 검색어 2~4단어. 글 전체에서 최대 2곳, 나머지는 null. "
        "특정 나라 지폐, 달력, 국기, 글자가 보이는 장면은 피한다"))


class Faq(BaseModel):
    question: str
    answer: str = Field(description="1~3문장. 본문과 같은 내용 반복 금지")
    source_ids: list[str]


class Scene(BaseModel):
    narration: str = Field(description="성우가 읽을 한국어 문장. 1~2문장")
    video_query: str = Field(description=(
        "배경 영상 영어 검색어 2~4단어. 분위기를 보여 주는 일반적인 장면(예: 'hands typing laptop', "
        "'family living room', 'city street night'). 특정 나라 지폐·동전, 달력·날짜, 국기, 글자·숫자가 "
        "보이는 화면, 로고, 사람 얼굴 클로즈업은 내용과 어긋날 수 있으니 피한다"))


class Shorts(BaseModel):
    title: str = Field(description="쇼츠 제목 40자 이내")
    scenes: list[Scene] = Field(description="장면 4~8개. 첫 장면은 시선을 끄는 질문이나 핵심 숫자")


class ActionLink(BaseModel):
    label: str = Field(description="버튼 글자. 예: '홈택스에서 신청하기', '국세청 근로장려금 안내 보기'")
    url: str = Field(description="[자료]의 주소 또는 [자료] 본문에 적힌 공식 사이트 주소만. 지어내기 금지")
    purpose: Literal["apply", "check", "info"] = Field(description="apply=신청, check=자격·결과 조회, info=공식 안내")
    note: str | None = Field(default=None, description="한 줄 설명(선택). 예: '평일 9~18시 상담 가능'")


class MasterContent(BaseModel):
    title: str = Field(description="블로그 제목 25~45자, 핵심 검색어를 앞쪽에. '총정리'·'한눈에' 같은 상투어 금지")
    slug: str = Field(description="영문 소문자와 하이픈만 쓴 주소용 이름")
    meta_description: str = Field(description="검색 결과 설명 80~150자")
    focus_keyword: str = Field(description="핵심 검색어 하나")
    reference_note: str = Field(description="정보 기준 시점 설명. 예: '2026년 정기신청(2025년 소득) 기준'")
    intro: list[Paragraph] = Field(description=(
        "도입 문단 1~2개. 제도 정의로 시작하지 말고 독자가 확인하려는 문제와 이 글에서 다룰 범위를 먼저"))
    action_links: list[ActionLink] = Field(default_factory=list, description=(
        "독자가 바로 신청·조회할 수 있는 공식 사이트 1~4개. 도입부 아래 '바로가기' 상자로 보인다"))
    sections: list[Section] = Field(description="본문 소제목 4~6개")
    faq: list[Faq] = Field(description="본문에서 다루지 않은 궁금증 2~4개")
    closing: Paragraph = Field(description="마무리 1문단. 본문 요약 반복 금지, 다음 행동(확인처·신청처)을 안내")
    shorts: Shorts
    unverified: list[str] = Field(
        default_factory=list,
        description="독자에게 필요하지만 제공된 자료로 확인되지 않아 본문에 쓰지 않은 내용",
    )


def paragraph_text(p: Paragraph | None) -> str:
    return "".join(s.text for s in p.spans) if p else ""


def section_texts(section: Section) -> list[str]:
    """소제목 안의 모든 글자(검사·대조용)."""
    out = [section.heading]
    if section.takeaway:
        out.append(section.takeaway)
    for b in section.blocks:
        if b.type == "paragraph":
            out.append(paragraph_text(b.paragraph))
        elif b.type == "note":
            out.append(paragraph_text(b.note))
        elif b.type == "checklist":
            out += b.items or []
        elif b.type == "table" and b.table:
            out += [b.table.caption, *b.table.headers, *(c for r in b.table.rows for c in r)]
    return out


def section_paragraphs(section: Section) -> list[str]:
    """표·체크리스트를 뺀 서술 문장(문단·주의·요약)."""
    out = [section.takeaway] if section.takeaway else []
    for b in section.blocks:
        if b.type == "paragraph":
            out.append(paragraph_text(b.paragraph))
        elif b.type == "note":
            out.append(paragraph_text(b.note))
    return out
