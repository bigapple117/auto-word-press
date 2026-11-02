"""AI가 돌려줄 마스터 콘텐츠의 형식. 블로그 글과 쇼츠 대본을 한 번에 담는다.

AI는 HTML을 쓰지 않고 구조화된 글자만 돌려준다. HTML은 blog_html.py가 만든다.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Table(BaseModel):
    caption: str = Field(description="표 제목")
    headers: list[str] = Field(description="열 이름 2~5개")
    rows: list[list[str]] = Field(description="각 행의 칸. 칸 수는 headers와 같게")


class Section(BaseModel):
    heading: str = Field(description="소제목. 검색어를 자연스럽게 포함")
    paragraphs: list[str] = Field(description="문단 1~4개. 각 문단은 2~5문장의 평문(HTML·마크다운 금지)")
    table: Table | None = Field(default=None, description="비교가 필요할 때만")
    checklist: list[str] | None = Field(default=None, description="독자가 스스로 확인할 항목. 필요할 때만, 3~7개")
    source_ids: list[str] = Field(description="이 소제목의 사실 근거가 된 자료 번호(S1 등)")
    image_query: str = Field(description="이 부분에 어울리는 스톡 사진 영어 검색어 2~4단어")


class Faq(BaseModel):
    question: str
    answer: str = Field(description="1~3문장")
    source_ids: list[str]


class Scene(BaseModel):
    narration: str = Field(description="성우가 읽을 한국어 문장. 1~2문장")
    video_query: str = Field(description="배경 영상 영어 검색어 2~4단어. 사람 얼굴 클로즈업·로고 피하기")


class Shorts(BaseModel):
    title: str = Field(description="쇼츠 제목 40자 이내")
    scenes: list[Scene] = Field(description="장면 4~8개. 첫 장면은 시선을 끄는 질문이나 핵심 숫자")


class MasterContent(BaseModel):
    title: str = Field(description="블로그 제목 25~45자, 핵심 검색어를 앞쪽에")
    slug: str = Field(description="영문 소문자와 하이픈만 쓴 주소용 이름")
    meta_description: str = Field(description="검색 결과 설명 80~150자")
    focus_keyword: str = Field(description="핵심 검색어 하나")
    reference_note: str = Field(description="정보 기준 시점 설명. 예: '2026년 정기신청(2025년 소득) 기준'")
    intro: list[str] = Field(description="도입 문단 1~2개")
    sections: list[Section] = Field(description="본문 소제목 4~6개")
    faq: list[Faq] = Field(description="자주 묻는 질문 2~4개")
    closing: str = Field(description="마무리 문단 1개. 다음 행동(신청처 등)을 안내")
    shorts: Shorts
    unverified: list[str] = Field(
        default_factory=list,
        description="독자에게 필요하지만 제공된 자료로 확인되지 않아 본문에 쓰지 않은 내용",
    )
