"""Gemini로 마스터 콘텐츠(블로그 + 쇼츠 대본)를 만들고 검사한다.

- 일시적 오류(바쁨 503, 한도 429, 서버 500, 시간 초과)는 기다렸다 다시 시도하고,
  그래도 안 되면 예비 모델로 넘어간다.
- 형식·사실 규칙 검사에 걸리면 고칠 점을 알려 주고 다시 쓰게 한다.
"""
from __future__ import annotations

import logging
import re
import unicodedata
import time
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Sequence

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from src.config import Settings
from src.link_check import check_action_links, home_links_overpromising
from src.content_schema import (MasterContent, Shorts, paragraph_text, section_paragraphs,
                                section_texts)
from src.source_fetcher import Source
from templates.prompt_template import (CHANGE_SYSTEM_INSTRUCTION, FACT_SYSTEM_INSTRUCTION, REVIEW_SYSTEM_INSTRUCTION, SHORTS_SYSTEM_INSTRUCTION, SYSTEM_INSTRUCTION,
                                      build_change_prompt, build_fact_prompt, build_prompt, build_review_prompt, build_shorts_repair_prompt,
                                      narration_budget, BODY_TARGET_CHARS)

log = logging.getLogger(__name__)

DEFAULT_FALLBACK_MODEL = "gemini-3.5-flash-lite"
RETRY_WAITS = (10, 30, 60)       # 일시적 오류 재시도 간격(초)
MAX_QUALITY_ROUNDS = 3           # 검사 불합격 시 최대 작성 횟수
BODY_SHORT_PROBLEM = "본문(문단·주의·요약 글자)"   # 분량 부족 지적의 앞부분
BODY_KEEP_PROBLEM = "[분량 유지]"                  # 다른 지적을 고칠 때 붙이는 분량 유지 안내
MIN_BODY_CHARS = 1200          # 이보다 짧으면 다시 쓰게 함(얇은 글 방지)
MAX_TIMEOUTS_PER_MODEL = 2      # 같은 모델에서 시간 초과가 이만큼 나면 예비 모델로
MAX_SHORTS_REPAIRS = 2           # 쇼츠 길이만 틀렸을 때 대본만 고쳐 쓰는 횟수
NARRATION_PROBLEM = "쇼츠 내레이션 전체 길이"
REQUEST_TIMEOUT_MS = 300_000
# Flex 요청은 Google 대기열에서 1~15분 기다릴 수 있어 공식 문서 권장(10분 이상)대로 길게 잡는다.
FLEX_TIMEOUT_MS = 900_000

_TRANSIENT_CODES = {429, 500, 502, 503, 504}
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0000FE0F\U0000200D]"
)
_MARKDOWN = re.compile(r"\*\*|__|^#{1,6}\s|^\s*[-*]\s", re.MULTILINE)
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
BANNED_PHRASES = (
    "알아보겠습니다", "알아볼까요", "결론적으로", "마무리하며", "도움이 되셨길",
    "도움이 되셨으면", "꿀팁", "총정리", "모르면 손해", "한눈에",
)


class GenerationError(RuntimeError):
    """여러 번 시도해도 쓸 만한 결과를 못 얻었을 때."""


class ModelBusyError(GenerationError):
    """모델이 계속 바쁘거나 한도에 걸려(일시 오류) 지금은 못 씀 → 실행기가 잠시 뒤 다시 시도한다."""


@dataclass
class GenerationResult:
    content: MasterContent
    model: str
    attempts: int
    warnings: list[str]                                  # 사람이 확인할 것
    fixes: list[str] = field(default_factory=list)       # 자동으로 고친 것(보고용)


MAX_TAKEAWAYS = 3
# 발행 전 검토 뒤에도 남으면 숨기는 경고: 글이 틀린 것이 아니라 문체 참고이거나, 자동으로 처리된 것
QUIET_WARNINGS = ("문단이 조금 깁니다", "같은 종결 표현", "같은 블록 구성", "본문이 짧습니다",
                  "자료로 확인 안 된 내용", "바로가기 링크에서 뺀 것", "신청·조회 바로가기 링크가 없습니다")


def needs_person(warning: str) -> bool:
    return not any(mark in warning for mark in QUIET_WARNINGS)


class ReviewOutcome(BaseModel):
    fixes: list[str] = Field(default_factory=list, description="실제로 고친 것, 한 줄씩 쉬운 말로(최대 6개)")
    unresolved: list[str] = Field(default_factory=list, description=(
        "자료끼리 다르거나 자료만으로 판단할 수 없는데 글에 남긴 것. 없으면 빈 목록"))
    content: MasterContent


class ClaimCheck(BaseModel):
    sentence: str = Field(description="글의 문장을 그대로 복사")
    verdict: Literal["supported", "unsupported", "contradicted"]
    source_id: str = ""
    evidence: str = Field(default="", description="근거 자료 본문을 그대로 복사한 짧은 구절")
    note: str = ""


class FactCheckOutcome(BaseModel):
    claims: list[ClaimCheck] = Field(default_factory=list)


MAX_FACT_CLAIMS = 20
MAX_FACT_WARNINGS = 5
FACT_WARNING = "사실 확인:"          # 이 말로 시작하는 경고가 있으면 아침 자동 발행에서 뺀다
_FACT_DROP = re.compile(r"[\s'\"‘’“”`·.,|:;()\[\]「」<>-]")
_QUOTE_GAP = re.compile(r"\.{3,}|…|⋯|(?:\s/\s)")     # AI가 떨어진 구절을 이어 붙일 때 쓰는 표시


def _fact_norm(text: str) -> str:
    """띄어쓰기·따옴표·마침표 차이를 무시하고 비교하려고 지운 글자."""
    return _FACT_DROP.sub("", unicodedata.normalize("NFKC", text))


def _article_units(c: MasterContent) -> list[str]:
    """사실 확인에 넘길 글 조각(문단·표 줄 단위, 대본 제외, 중복 제거)."""
    extra = [f"대상: {c.eligibility}" if c.eligibility.strip() else ""] + [l.note or "" for l in c.action_links]
    return [u for u in dict.fromkeys(extra + [unit for _, unit, _ in _text_units(c)]) if u.strip()]


@dataclass
class FactIssue:
    sentence: str
    verdict: str          # unsupported / contradicted
    note: str

    def line(self) -> str:
        kind = "자료와 다름" if self.verdict == "contradicted" else "자료에서 근거를 못 찾음"
        why = f" — {self.note.strip()}" if self.note.strip() else ""
        return f"{kind}: '{self.sentence.strip()[:80]}'{why[:100]}"


def _quoted_in_sources(evidence: str, texts: Any) -> bool:
    """인용의 낱말(띄어쓰기·줄바꿈·'…'로 나눈 조각)이 모두 같은 자료 한 곳에 있으면 True.
    AI는 표의 여러 칸, 떨어진 구절을 한 줄로 이어 인용하므로 연속된 글로는 대조하지 않는다.
    숫자·기관명 같은 낱말이 하나라도 그 자료에 없으면 근거 없음(기억으로 지어낸 인용을 막음)."""
    words = [_fact_norm(w) for w in re.split(r"\s+", _QUOTE_GAP.sub(" ", evidence))]
    words = [w for w in words if len(w) >= 2 or w.isdigit()]     # '연 3 회'의 3처럼 숫자는 한 글자도 대조
    if sum(len(w) for w in words) < 6:
        return False
    return any(all(w in t for w in words) for t in texts)


def judge_claims(outcome: FactCheckOutcome, sources: Sequence[Source]) -> list[FactIssue]:
    """AI 판정을 프로그램이 다시 확인한다. '근거 있음'이어도 그 인용이 자료 본문에 그대로 없으면 근거 없음으로 본다
    (AI가 기억으로 맞다고 하는 것을 막음). 문제 있는 문장만 돌려준다."""
    texts = {s.id: _fact_norm(s.text) for s in sources}
    issues: list[FactIssue] = []
    seen: set[str] = set()
    for claim in outcome.claims:
        key = _fact_norm(claim.sentence)
        if not key or key in seen:
            continue
        seen.add(key)
        if len(seen) > MAX_FACT_CLAIMS:
            break
        if claim.verdict == "supported":
            if _quoted_in_sources(claim.evidence, texts.values()):
                continue
            log.info("사실 확인: 근거 인용이 자료에 없음 — 문장 %r / 인용 %r", claim.sentence[:80], claim.evidence[:160])
            issues.append(FactIssue(claim.sentence, "unsupported",
                                    "AI가 근거라고 든 구절이 자료 본문에 그대로 없음"))
        else:
            issues.append(FactIssue(claim.sentence, claim.verdict, claim.note))
    return issues


@dataclass
class FactReport:
    hold: bool                                   # True면 아침 자동 발행에서 뺀다(사람이 [발행]을 누르면 올라감)
    remaining: list[str] = field(default_factory=list)   # 사람이 확인할 문장
    fixed: int = 0                               # 자동으로 고친(글에서 바뀐) 문장 수
    failed: bool = False                         # 사실 확인 자체를 못 함

    def as_dict(self) -> dict[str, Any]:
        return {"hold": self.hold, "remaining": list(self.remaining), "fixed": self.fixed, "failed": self.failed}


def trim_takeaways(c: MasterContent) -> tuple[MasterContent, list[str]]:
    """요약 줄(takeaway)이 3곳을 넘으면 앞에서부터 3곳만 남긴다(AI 검토가 못 줄였을 때의 마지막 정리)."""
    kept = 0
    sections = []
    for s in c.sections:
        if s.takeaway:
            kept += 1
            if kept > MAX_TAKEAWAYS:
                s = s.model_copy(update={"takeaway": None})
        sections.append(s)
    if kept <= MAX_TAKEAWAYS:
        return c, []
    return (c.model_copy(update={"sections": sections}),
            [f"소제목 요약 줄을 {kept}곳에서 {MAX_TAKEAWAYS}곳으로 줄임"])


_N = r"\d[\d,]*(?:\.\d+)?"
# 한글 단위를 섞은 금액 한 덩어리: '34만 9,700원', '1억 3,500만 원', '1억3천5백만원', '7천2백5십만원', '2.4억원'
_AMOUNT = re.compile(rf"{_N}\s*[억천백십만]+(?:\s*(?:{_N})?\s*[억천백십만]+)*(?:\s*{_N}(?=\s*원))?\s*원?|{_N}\s*원")
_AMOUNT_TOKEN = re.compile(rf"{_N}|[억천백십만]")
_SMALL_UNIT = {"천": 1_000, "백": 100, "십": 10}
_BIG_UNIT = {"억": 100_000_000, "만": 10_000}
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_FRAC_SLASH = re.compile(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)")
_FRAC_KO = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(천만|백만|만|천)?\s*분의\s*(\d[\d,]*(?:\.\d+)?)")
_KO_UNIT = {"천만": 10_000_000, "백만": 1_000_000, "만": 10_000, "천": 1_000, None: 1}


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def _amounts(span: str) -> list[float]:
    """'1억 3,500만 원' → [135000000]. 큰 단위(억·만)가 작아지지 않고 다시 나오면 다른 금액으로 본다('8만 8만원')."""
    out: list[float] = []
    total = block = 0.0
    num: float | None = None
    small_seen = False                      # 천·백·십을 읽었는지('0천만'의 0을 숫자 생략으로 보지 않게)
    last_big = float("inf")
    for tok in _AMOUNT_TOKEN.findall(span):
        if tok[0].isdigit():
            num = _num(tok)
        elif tok in _SMALL_UNIT:
            block += (1 if num is None else num) * _SMALL_UNIT[tok]
            num, small_seen = None, True
        else:
            big = _BIG_UNIT[tok]
            if big >= last_big:
                out.append(total)
                total = 0.0
            base = 1 if num is None and not small_seen else block + (num or 0)   # '만'만 있으면 1만, '0만'은 0
            total += base * big
            block, num, last_big, small_seen = 0.0, None, big, False
    out.append(total + block + (num or 0))
    return out


def extract_figures(text: str) -> dict[str, str]:
    """글에서 금액(원 단위로 환산)과 비율을 뽑는다. {정규화 값: 원래 표기}."""
    found: dict[str, str] = {}

    def take(pattern: re.Pattern, value_of) -> None:
        nonlocal text
        for m in pattern.finditer(text):
            found.setdefault(value_of(m), m.group(0).strip())
        text = pattern.sub(" ", text)   # 이미 센 부분은 다음 규칙에서 다시 세지 않음

    # 분수는 금액보다 먼저: '10만 분의 22'를 10만 원으로 오해하지 않게
    take(_FRAC_KO, lambda m: f"ratio:{_num(m.group(3)):g}/{round(_num(m.group(1)) * _KO_UNIT[m.group(2)])}")
    take(_FRAC_SLASH, lambda m: f"ratio:{_num(m.group(1)):g}/{round(_num(m.group(2)))}")
    for m in _AMOUNT.finditer(text):
        shown = m.group(0).strip()
        # '3천 명'처럼 억·만·원이 없는 덩어리는 금액으로 보지 않는다
        if any(u in shown for u in "억만원"):
            for value in _amounts(shown):
                found.setdefault(f"won:{round(value)}", shown)
    text = _AMOUNT.sub(" ", text)
    take(_PCT, lambda m: f"pct:{_num(m.group(1)):g}")
    return found


def _all_texts(c: MasterContent) -> list[str]:
    texts = [c.title, c.meta_description, c.reference_note, paragraph_text(c.closing),
             *(paragraph_text(p) for p in c.intro), c.shorts.title]
    for s in c.sections:
        texts += section_texts(s)
    for f in c.faq:
        texts += [f.question, f.answer]
    texts += [sc.narration for sc in c.shorts.scenes]
    return texts


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_ENDING = re.compile(r"(합니다|습니다|입니다|됩니다|있습니다|없습니다)[.!?]?$")
_DEFINITION_START = re.compile(r"^\S+(은|는)\s.{0,60}(제도|상품|정책|사업)입니다")
MAX_PARAGRAPH_CHARS = 300
SOFT_PARAGRAPH_CHARS = 250
MAX_SENTENCES = 4
MAX_STRONG_PER_SECTION = 2
MAX_BODY_IMAGES = 2


def _norm(text: str) -> str:
    return re.sub(r"[\s,]+", "", unicodedata.normalize("NFKC", text))


_NUM_TOKEN = re.compile(r"(?<![\d.,])[+-]?\d[\d,]*(?:\.\d+)?\s*(?:억|천만|백만|만|천)?\s*(?:원|%|세|개월|년|월|일|명)?")


def _num_tokens(text: str) -> set[str]:
    """숫자를 단위까지 붙인 덩어리로 뽑는다('100'과 '1,000'을 다른 값으로 본다)."""
    return {_norm(m.group(0)) for m in _NUM_TOKEN.finditer(text) if m.group(0).strip()}


def table_repeated_in_text(rows: Sequence[Sequence[str]], texts: Sequence[str]) -> bool:
    """문단이 표의 '행 이름 + 숫자 값'을 같은 문장 안에서 다시 나열하는지(값 3개 이상, 60% 이상)."""
    sentences = [(_norm(x), _num_tokens(x)) for t in texts for x in _sentences(t)]
    pairs = {(_norm(r[0]), tok) for r in rows if r for v in r[1:] for tok in _num_tokens(v)}
    if len(pairs) < 3:
        return False
    hits = sum(any(k in sent and tok in toks for sent, toks in sentences) for k, tok in pairs)
    return hits >= 3 and hits / len(pairs) >= 0.6


def _sentences(text: str) -> list[str]:
    return [x for x in _SENTENCE_END.split(text.strip()) if x]


def _check_block(i: int, b, problems: list[str]) -> None:
    where = f"소제목 {i}의 {b.type} 블록"
    if b.type == "paragraph" and not paragraph_text(b.paragraph).strip():
        problems.append(f"{where}에 문단 내용이 없습니다.")
    elif b.type == "note" and (not paragraph_text(b.note).strip() or b.note_kind is None):
        problems.append(f"{where}에 note 내용이나 note_kind가 없습니다.")
    elif b.type == "checklist" and len(b.items or []) < 2:
        problems.append(f"{where}의 항목이 2개 미만입니다.")
    elif b.type == "table":
        t = b.table
        if t is None:
            problems.append(f"{where}에 표가 없습니다.")
            return
        width = len(t.headers)
        if not 2 <= width <= 4:
            problems.append(f"소제목 {i} 표의 열은 2~4개로 줄여 주세요(현재 {width}개).")
        if not t.rows or any(len(r) != width for r in t.rows):
            problems.append(f"소제목 {i} 표의 행마다 칸 수가 열 이름 수({width})와 같아야 합니다.")


def check_style(c: MasterContent) -> tuple[list[str], list[str]]:
    """'AI 티' 검사: 표 반복, 긴 문단, 강조 과다, 사진 과다, 사전식 도입, 같은 어미·같은 구성 반복."""
    problems: list[str] = []
    warnings: list[str] = []
    intro_first = paragraph_text(c.intro[0]) if c.intro else ""
    if _DEFINITION_START.match(intro_first.strip()):
        problems.append("도입부를 '○○은 ~제도입니다' 같은 정의로 시작하지 말고, 독자가 확인하려는 문제부터 써 주세요.")

    paragraphs: list[tuple[str, str]] = [("도입부", paragraph_text(p)) for p in c.intro]
    paragraphs.append(("마무리", paragraph_text(c.closing)))
    images = sum(1 for s in c.sections if s.image_query)
    if images > MAX_BODY_IMAGES:
        problems.append(f"사진(image_query)은 글 전체에서 {MAX_BODY_IMAGES}곳까지만 써 주세요(현재 {images}곳).")
    takeaways = sum(1 for s in c.sections if s.takeaway)
    if takeaways > 3:
        warnings.append(f"takeaway가 {takeaways}곳입니다(2~3곳 권장).")

    shapes = []
    for i, s in enumerate(c.sections, 1):
        shown = [b.paragraph if b.type == "paragraph" else b.note for b in s.blocks if b.type in ("paragraph", "note")]
        strong = sum(1 for p in shown if p for sp in p.spans if sp.strong)
        if strong > MAX_STRONG_PER_SECTION:
            problems.append(f"소제목 {i} '{s.heading}'의 강조가 {strong}곳입니다. 2곳 이하로 줄여 주세요.")
        texts = section_paragraphs(s)
        for b in s.blocks:
            if b.type == "table" and b.table and table_repeated_in_text(b.table.rows, texts):
                problems.append(f"소제목 {i} '{s.heading}'의 문단이 표 내용을 다시 나열합니다. "
                                "문단에는 구분법·예외·주의점만 쓰고 숫자 비교는 표에 맡겨 주세요.")
        paragraphs += [(f"소제목 {i}", t) for t in texts]
        shapes.append(tuple(b.type for b in s.blocks))
    for a, b, cc in zip(shapes, shapes[1:], shapes[2:]):
        if a == b == cc:
            warnings.append(f"같은 블록 구성 {list(a)}이 소제목 3개 연속 반복됩니다.")
            break

    for where, text in paragraphs:
        n = len(text)
        sentences = _sentences(text)
        if n > MAX_PARAGRAPH_CHARS or len(sentences) > MAX_SENTENCES:
            problems.append(f"{where}에 긴 문단이 있습니다({n}자, {len(sentences)}문장). 1~3문장으로 나눠 주세요.")
        elif n > SOFT_PARAGRAPH_CHARS:
            warnings.append(f"{where} 문단이 조금 깁니다({n}자).")

    endings = [m.group(1) if (m := _ENDING.search(x.strip())) else "" for _w, t in paragraphs for x in _sentences(t)]
    runs = sum(1 for a, b, cc in zip(endings, endings[1:], endings[2:]) if a and a == b == cc)
    if runs >= 3:
        warnings.append(f"같은 종결 표현이 3문장 연속되는 곳이 {runs}군데 있습니다.")
    return problems, warnings


_CARD_NUMBER = re.compile(r"\d[\d,.]*")


def _digits(text: str) -> set[str]:
    return {m.group().replace(",", "").rstrip(".") for m in _CARD_NUMBER.finditer(text)}


def tidy_shorts_cards(c: MasterContent) -> tuple[MasterContent, list[str]]:
    """쇼츠 카드에 그 장면 대사에 없는 숫자가 있으면 카드를 뺀다(지어낸 숫자가 화면에 뜨지 않게).
    AI를 다시 부르지 않고 고친다. 질문(hook)이 길면 비운다(제목으로 대신 표시)."""
    warnings: list[str] = []
    scenes = []
    for i, sc in enumerate(c.shorts.scenes, start=1):
        card = sc.card
        if card and not _digits(card.small + " " + card.big) <= _digits(sc.narration):
            warnings.append(f"쇼츠 {i}번 장면 카드에 대사에 없는 숫자가 있어 카드를 뺐습니다({card.big}).")
            card = None
        if card and not card.big.strip():
            card = None
        scenes.append(sc.model_copy(update={"card": card}))
    hook = c.shorts.hook.strip()
    if len(hook) > 20:
        warnings.append(f"쇼츠 위쪽 질문이 길어 제목으로 대신합니다({hook}).")
        hook = ""
    shorts = c.shorts.model_copy(update={"scenes": scenes, "hook": hook})
    return c.model_copy(update={"shorts": shorts}), warnings


def check_content(
    c: MasterContent, sources: Sequence[Source], target_seconds: int
) -> tuple[list[str], list[str]]:
    """(고쳐야 할 문제, 참고 경고)를 돌려준다. 문제가 있으면 다시 쓰게 한다."""
    problems: list[str] = []
    warnings: list[str] = []
    valid_ids = {s.id for s in sources}

    if not 4 <= len(c.sections) <= 6:
        problems.append(f"소제목은 4~6개여야 합니다(현재 {len(c.sections)}개).")
    if not 2 <= len(c.faq) <= 4:
        problems.append(f"FAQ는 2~4개여야 합니다(현재 {len(c.faq)}개).")
    if not 4 <= len(c.shorts.scenes) <= 8:
        problems.append(f"쇼츠 장면은 4~8개여야 합니다(현재 {len(c.shorts.scenes)}개).")
    if not _SLUG.match(c.slug):
        problems.append(f"slug는 영문 소문자·숫자·하이픈만 씁니다: {c.slug!r}")
    if not 15 <= len(c.title) <= 60:
        problems.append(f"제목 길이는 25~45자 정도로 맞춥니다(현재 {len(c.title)}자).")
    if not c.intro:
        problems.append("도입 문단이 없습니다.")

    for i, s in enumerate(c.sections, 1):
        if not s.blocks:
            problems.append(f"소제목 {i} '{s.heading}'에 내용 블록이 없습니다.")
        elif not any(b.type in ("paragraph", "note") for b in s.blocks):
            problems.append(f"소제목 {i} '{s.heading}'에 설명 문단이 하나도 없습니다.")
        for b in s.blocks:
            _check_block(i, b, problems)
        if not s.source_ids:
            problems.append(f"소제목 {i} '{s.heading}'에 근거 자료 번호가 없습니다.")
        bad = [x for x in s.source_ids if x not in valid_ids]
        if bad:
            problems.append(f"소제목 {i}에 없는 자료 번호가 있습니다: {', '.join(bad)}")
    for f in c.faq:
        if not f.source_ids:
            problems.append(f"FAQ '{f.question}'에 근거 자료 번호가 없습니다.")
        bad = [x for x in f.source_ids if x not in valid_ids]
        if bad:
            problems.append(f"FAQ '{f.question}'에 없는 자료 번호가 있습니다: {', '.join(bad)}")

    texts = _all_texts(c)
    joined = "\n".join(texts)
    if _EMOJI.search(joined):
        problems.append("이모지를 모두 빼 주세요.")
    if any(_MARKDOWN.search(t) for t in texts):
        problems.append("마크다운 기호(**, #, - 목록)를 쓰지 말고 평문으로 써 주세요(강조는 strong으로).")
    used = [p for p in BANNED_PHRASES if p in joined]
    if used:
        problems.append(f"금지 표현을 다른 말로 바꿔 주세요: {', '.join(used)}")

    if not c.eligibility.strip():
        warnings.append("지원 대상(eligibility)이 비어 있습니다. 대상이 정해진 제도라면 누가 받을 수 있는지 확인하세요.")

    _ok_links, bad_links = check_action_links(c.action_links, sources)
    if bad_links:
        warnings.append("바로가기 링크에서 뺀 것: " + " / ".join(bad_links))
    overpromising = home_links_overpromising(_ok_links)
    if overpromising:
        warnings.append("첫 화면으로 가는 링크인데 이름이 특정 메뉴처럼 보임('○○ 바로가기'로 고칠 것): "
                        + " / ".join(overpromising))
    if not _ok_links:
        warnings.append("신청·조회 바로가기 링크가 없습니다(자료에서 공식 주소를 찾지 못함).")

    style_problems, style_warnings = check_style(c)
    problems += style_problems
    warnings += style_warnings

    low, high = narration_budget(target_seconds)
    total = sum(len(sc.narration) for sc in c.shorts.scenes)
    if not low <= total <= high:
        problems.append(
            f"{NARRATION_PROBLEM}를 공백 포함 {low}~{high}자로 맞춰 주세요(현재 {total}자)."
        )

    body_chars = count_body_chars(c)
    if body_chars < MIN_BODY_CHARS:
        problems.append(f"{BODY_SHORT_PROBLEM}이 {body_chars}자로 너무 짧습니다. 소제목마다 구분법·예외·"
                        f"다음 행동 설명을 보강해 {MIN_BODY_CHARS}자 이상으로 써 주세요(표 내용 반복은 금지).")
    elif body_chars < BODY_TARGET_CHARS[0]:
        warnings.append(f"본문이 짧습니다({body_chars}자).")
    if c.unverified:
        warnings.append("자료로 확인 안 된 내용: " + " / ".join(c.unverified))
    relayed = [f"{s.id} {s.url}" for s in sources if s.via == "gemini"]
    if relayed:
        warnings.append("AI가 옮겨 적은 자료(원문 대조 권장): " + ", ".join(relayed))

    missing = missing_figures(c, sources)
    if missing:
        warnings.append("자료에서 못 찾은 숫자(확인 필요): " + ", ".join(missing))
    return problems, warnings


# ---------- 주제가 약속한 내용이 글에 있는지 ----------
# 2026-11-24 국가장학금 글: 주제는 '지원 금액'인데 금액이 없고, 자료에 있던 신청 기간(~1.7)도 빠진 채 검사를 통과했다.
COVERAGE_PROBLEM = "주제에 맞는 핵심 내용 빠짐"
# 프로그램 이름(장려금·수당·지원금)만으로는 걸지 않는다: '근로장려금 신청 방법'은 금액이 주제가 아닐 수 있음
_TOPIC_AMOUNT = re.compile(r"금액|한도|지급액|수령액|얼마|공제액|급여액|받는\s*돈|지원\s*액")
_TOPIC_APPLY = re.compile(r"신청|접수|기한|마감|기간")
_PERIOD_KEY = re.compile(r"(?:신청|접수)\s*(?:기간|일정|기한)|신청일정|접수일정")
# 날짜로 볼 표기만: 연도가 있으면 2027.01.07·2027-01-07·2027년 1월 7일, 없으면 '1월 7일'·'1. 7.'(끝 점 필수).
# 소수(2.15%)·시각(12.30, 9:00)은 끝 점이나 '일'이 없어 날짜로 보지 않는다.
_DATE_Y = re.compile(r"(?<!\d)(20\d{2})\s*(?:년|[.\-/])\s*(\d{1,2})\s*(?:월|[.\-/])\s*(\d{1,2})(?!\d)")
_DATE_NOY = re.compile(r"(?<![\d.\-/])(\d{1,2})\s*(?:월\s*(\d{1,2})\s*일|\.\s*(\d{1,2})\s*\.(?!\d))")
_OTHER_FIELD = re.compile(r"[가-힣]{1,4}\s*(?:기간|기한|일정)|[○●※■▶□◆]")
_KST = timezone(timedelta(hours=9))


def _dates(text: str) -> list[tuple[int, int | None, int, int]]:
    """text 안 날짜들 (위치, 연도 또는 None, 월, 일), 앞에서부터. 연도 있는 표기 안쪽의 '1. 7.'은 겹쳐 세지 않는다."""
    found = [(m.start(), int(m.group(1)), int(m.group(2)), int(m.group(3))) for m in _DATE_Y.finditer(text)]
    spans = [(m.start(), m.end()) for m in _DATE_Y.finditer(text)]
    for m in _DATE_NOY.finditer(text):
        if not any(a <= m.start() < b for a, b in spans):
            found.append((m.start(), None, int(m.group(1)), int(m.group(2) or m.group(3))))
    return sorted(d for d in found if 1 <= d[2] <= 12 and 1 <= d[3] <= 31)


def _period_ends(texts: Sequence[str], today: date) -> set[tuple[int, int]]:
    """자료의 '신청 기간: A ~ B'(여러 차수면 모두)에서 끝나는 날(월, 일)을 모은다. 이미 지난 날은 뺀다.
    끝 날에 연도가 없으면 시작 날의 연도(끝 달이 더 앞이면 다음 해), 둘 다 없으면 올해로 본다."""
    ends: set[tuple[int, int]] = set()
    for text in texts:
        for key in _PERIOD_KEY.finditer(text):
            window = text[key.end():key.end() + 160]
            # 뒤에 붙은 다른 항목('사용기간: 발급일 ~ 12. 31.' 등)의 범위는 신청 마감이 아니다
            stop = _OTHER_FIELD.search(window)
            if stop:
                window = window[:stop.start()]
            pos = 0
            while (tilde := window.find("~", pos)) != -1:
                pos = tilde + 1
                after = _dates(window[tilde + 1:tilde + 25])
                if not after or after[0][0] > 8:
                    continue
                before = _dates(window[max(0, tilde - 30):tilde])
                start = before[-1] if before else None
                _, year, month, day = after[0]
                if year is None:
                    if start and start[1]:
                        year = start[1] + (1 if month < start[2] else 0)
                    else:
                        year = today.year + (1 if start and month < start[2] else 0)
                try:
                    if date(year, month, day) < today:
                        continue
                except ValueError:
                    continue
                ends.add((month, day))
    return ends


def _mentions_day(text: str, month: int, day: int) -> bool:
    return re.search(rf"(?<!\d)0?{month}\s*(?:월\s*0?{day}\s*일|[.\-/]\s*0?{day}(?!\d))", text) is not None


_UNIT_WON = re.compile(r"단위\s*[:：]?\s*(?:천|만|백만|억)?\s*원")


def _has_money(text: str) -> bool:
    """'원'이 붙은 1만 원 이상 금액, 또는 '(단위: 만원)' 표가 있는지. '10만 명'은 금액이 아니다."""
    if any(k.startswith("won:") and float(k[4:]) >= 10_000 and "원" in shown
           for k, shown in extract_figures(text).items()):
        return True
    return bool(_UNIT_WON.search(text)) and re.search(r"\d", text) is not None


def coverage_problems(topic: str, c: MasterContent, sources: Sequence[Source],
                      today: date | None = None) -> list[str]:
    """주제가 금액·신청을 다루는데 자료에 있는 금액·신청 마감일이 글에 하나도 없으면 다시 쓰게 한다."""
    problems: list[str] = []
    joined = "\n".join(_all_texts(c))
    src_texts = [s.text for s in sources]
    if _TOPIC_AMOUNT.search(topic):
        if any(_has_money(t) for t in src_texts) and not _has_money(joined):
            problems.append(f"{COVERAGE_PROBLEM}: 주제가 금액·한도를 다루는데 글에 금액이 하나도 없습니다. "
                            "자료에 있는 지원 금액(구간·대상별 금액표 등)을 표나 문단으로 넣어 주세요.")
    if _TOPIC_APPLY.search(topic):
        ends = _period_ends(src_texts, today or datetime.now(_KST).date())
        if ends and not any(_mentions_day(joined, m, d) for m, d in ends):
            shown = ", ".join(f"{m}월 {d}일" for m, d in sorted(ends))
            problems.append(f"{COVERAGE_PROBLEM}: 자료에 신청 기간(마감 {shown})이 있는데 글에 없습니다. "
                            "신청 기간과 마감일을 본문에 넣어 주세요.")
    return problems


# 단위가 바로 붙지 않은 맨숫자(표의 '(단위: 원)' 아래 숫자). '30만원'의 30, '2026년'의 2026은 빼다.
_BARE_NUMBER = re.compile(rf"(?<![\d.,\-/:]){_N}(?![\d.,\-/:]|\s*[억천백십만원%세년월일개명인회건차호분초시])")


def _source_figures(sources: Sequence[Source]) -> set[str]:
    """자료의 금액·비율 키. 자료의 표는 '(단위: 원)' 아래 숫자만 늘어놓는 일이 많아 맨숫자도 금액 후보로 인정한다."""
    source_text = "\n".join(s.text for s in sources)
    found = set(extract_figures(source_text))
    # 법령은 비율을 '100분의 17'로 쓴다 → 글의 '17%'와 같은 값으로 본다
    for key in list(found):
        if key.startswith("ratio:"):
            num, den = (float(x) for x in key[6:].split("/"))
            if den:
                found.add(f"pct:{num * 100 / den:g}")
    return found | {f"won:{round(_num(m))}" for m in _BARE_NUMBER.findall(source_text) if _num(m) >= 100}


# 예시로 든 가정값을 인정하는 문장 표시('만약 600만 원을 냈다면'). 단정하는 문장의 금액은 가정값으로 보지 않는다
_EXAMPLE_SENTENCE = re.compile(r"만약|예를 들어|예컨대|가령|가정|했다면|낸다면|냈다면|이라면|라면")


def _calc_tolerance(value: float) -> float:
    """검산 허용 오차: 글에 적힌 자릿수의 반올림만('102만 원'이면 5천 원). 큰 금액도 0.5%를 넘지 않게."""
    digits = str(round(value))
    unit = 10 ** (len(digits) - len(digits.rstrip("0")))
    return max(1.0, min(unit / 2, value * 0.005))


def _derive_in_text(text: str, known: set[str]) -> dict[str, str]:
    """한 문단 안에서 '금액 × 자료에 있는 비율'로 계산되는 자료 밖 금액. {키: 설명}.
    곱하는 쪽 금액이 자료에 없으면, 예시라고 밝힌 문장에 있을 때만 가정값으로 인정한다."""
    figs = extract_figures(text)
    sentences = _SENTENCE_END.split(text)
    example_keys: set[str] = set()
    for sent in sentences:
        if _EXAMPLE_SENTENCE.search(sent):
            example_keys |= set(extract_figures(sent))
    wons = [(k, float(k[4:]), shown) for k, shown in figs.items() if k.startswith("won:")]
    wons.sort(key=lambda w: w[0] not in known)          # 자료에 있는 금액을 먼저 곱해 본다
    rates = [float(k[4:]) for k in figs if k.startswith("pct:") and k in known and 0 < float(k[4:]) < 100]
    out: dict[str, str] = {}
    for key, value, shown in wons:
        if key in known or value <= 0:
            continue
        for bkey, base, bshown in wons:
            if bkey == key or base <= value or not (bkey in known or bkey in example_keys):
                continue
            hit = next((r for r in rates if abs(base * r / 100 - value) <= _calc_tolerance(value)), None)
            if hit is None:
                continue
            out[key] = f"{shown} = {bshown} × {hit:g}%"
            if bkey not in known:
                out.setdefault(bkey, f"{bshown}(계산 예시의 가정값)")
            break
    return out


def _text_units(c: MasterContent) -> list[tuple[str, str, dict[str, str] | None]]:
    """숫자 대조할 글 조각마다 (글, 그 조각의 문단 단위, 계산 근거를 찾을 소제목의 숫자들).
    문단 단위는 보통 그 조각 자체, 표 칸이면 같은 줄 전체. 소제목 밖 글(제목·도입·FAQ 등)은 근거 범위가 없다(None).
    글자가 같은 조각이 여러 곳에 있어도 섞이지 않게 글의 구조를 따라 만든다(_all_texts와 같은 순서)."""
    narrations = {sc.narration for sc in c.shorts.scenes}
    units: list[tuple[str, str, dict[str, str] | None]] = [
        (t, t, None) for t in [c.title, c.meta_description, c.reference_note, paragraph_text(c.closing),
                               *(paragraph_text(p) for p in c.intro), c.shorts.title]]
    for sec in c.sections:
        figs = extract_figures("\n".join(section_texts(sec)))
        units.append((sec.heading, sec.heading, figs))
        if sec.takeaway:
            units.append((sec.takeaway, sec.takeaway, figs))
        for b in sec.blocks:
            if b.type == "paragraph":
                t = paragraph_text(b.paragraph)
                units.append((t, t, figs))
            elif b.type == "note":
                t = paragraph_text(b.note)
                units.append((t, t, figs))
            elif b.type == "checklist":
                units += [(t, t, figs) for t in b.items or []]
            elif b.type == "table" and b.table:
                units += [(t, t, figs) for t in [b.table.caption, *b.table.headers]]
                units += [(cell, " | ".join(row), figs) for row in b.table.rows for cell in row]
    for f in c.faq:
        units += [(f.question, f.question, None), (f.answer, f.answer, None)]
    return [u for u in units if u[0] not in narrations]


def _derive_in_context(unit: str, figs: dict[str, str], known: set[str]) -> dict[str, str]:
    """'15%가 적용됩니다. … 최대 150만 원'처럼 곱하는 금액(한도)이 같은 소제목의 다른 문단에 있는 결과 금액. {키: 설명}.
    인정 조건: 곱하는 금액과 비율이 모두 자료에 있는 숫자이고 같은 소제목에 적혀 있으며,
    둘 중 하나는 이 문단(표는 같은 줄)에 직접 적혀 있을 것. '가입 한도는 170만 원입니다'처럼
    계산과 무관하게 단정한 문장은 다른 곳에 같은 계산이 있어도 인정하지 않는다."""
    own = extract_figures(unit)
    bases = [(k, float(k[4:]), shown) for k, shown in figs.items() if k.startswith("won:") and k in known]
    rates = [k for k in figs if k.startswith("pct:") and k in known and 0 < float(k[4:]) < 100]
    out: dict[str, str] = {}
    for key, shown in own.items():
        if not key.startswith("won:") or key in known:
            continue
        value = float(key[4:])
        for bkey, base, bshown in bases:
            if base <= value:
                continue
            hit = next((r for r in rates if (bkey in own or r in own)
                        and abs(base * float(r[4:]) / 100 - value) <= _calc_tolerance(value)), None)
            if hit is not None:
                out[key] = f"{shown} = {bshown} × {float(hit[4:]):g}%"
                break
    return out


def _unsourced(c: MasterContent, sources: Sequence[Source], with_calc: bool) -> tuple[list[str], dict[str, str]]:
    known = _source_figures(sources)
    missing: dict[str, str] = {}
    derived: dict[str, str] = {}
    # 제목·요약 문구는 본문을 줄여 쓴 것이라, 본문 어딘가에서 검산된 금액이면 인정한다
    headlines = {c.title, c.meta_description, c.shorts.title}
    pending: dict[str, str] = {}
    for text, unit, section_figs in _text_units(c):
        here: dict[str, str] = {}
        if with_calc:
            here = _derive_in_text(text, known)
            if section_figs is not None:
                # 자료의 숫자만으로 맞춘 계산식이 예시 가정값 계산식보다 우선
                here.update(_derive_in_context(unit, section_figs, known))
        for key, shown in extract_figures(text).items():
            if key in known:
                continue
            if key in here:
                derived.setdefault(key, here[key])
            elif text in headlines:
                pending.setdefault(key, shown)
            else:
                missing.setdefault(key, shown)      # 다른 문단에서 검산됐어도 여기서 근거 없이 쓰였으면 남긴다
    for key, shown in pending.items():
        if key not in derived:
            missing.setdefault(key, shown)
    return list(missing.values()), {k: v for k, v in derived.items() if k not in missing}


def derived_figures(c: MasterContent, sources: Sequence[Source]) -> dict[str, str]:
    """자료에 없지만 같은 문단에서 '금액 × 자료의 비율'로 검산되는 금액. {키: 설명}.
    예: '만약 월세로 600만 원을 냈다면 17%를 적용해 102만 원' → 102만 원은 검산 통과, 600만 원은 예시의 가정값."""
    return _unsourced(c, sources, with_calc=True)[1]


def missing_figures(c: MasterContent, sources: Sequence[Source], with_calc: bool = True) -> list[str]:
    """블로그 글의 금액·비율 중 자료에서 못 찾고, 같은 문단에서 자료의 숫자로 검산도 안 되는 것(원래 표기).
    with_calc=False면 검산 없이 자료에 있는지만 본다(자동 검토 채택 판정용: 검토가 숫자를 새로 지어내지 못하게)."""
    return _unsourced(c, sources, with_calc)[0]


CALC_NOTE = "자료의 숫자로 검산 확인:"


def calc_check_note(c: MasterContent, sources: Sequence[Source]) -> list[str]:
    """사람에게 보여 줄 검산 결과 한 줄(없으면 빈 목록)."""
    derived = derived_figures(c, sources).values()
    calcs = [v for v in derived if " = " in v]
    assumed = [v.split("(")[0] for v in derived if " = " not in v]
    if not calcs:
        return []
    note = CALC_NOTE + " " + ", ".join(calcs)
    if assumed:
        note += " (예시로 가정한 값: " + ", ".join(assumed) + ")"
    return [note]


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, genai_errors.APIError):
        return getattr(exc, "code", None) in _TRANSIENT_CODES
    name = exc.__class__.__name__.lower()
    return "timeout" in name or "connect" in name


def count_body_chars(c: MasterContent) -> int:
    """검사에서 세는 본문 글자 수: 도입 문단 + 소제목의 문단·주의·요약(표·체크리스트·FAQ 제외)."""
    return sum(len(t) for s in c.sections for t in section_paragraphs(s)) + \
        sum(len(paragraph_text(p)) for p in c.intro)


class ChangeItem(BaseModel):
    source_id: str = Field(description="기사 id(N1 등)")
    status: str = Field(description="confirmed 또는 pending")
    change: str = Field(description="무엇이 어떻게 바뀌는지 적힌 기사 문장 그대로 복사")
    proof: str = Field(default="", description="confirmed이면 확정(통과·의결·공포·시행)이 적힌 기사 문장 그대로 복사")


class ChangeMemo(BaseModel):
    items: list[ChangeItem] = Field(default_factory=list)


class LawChoice(BaseModel):
    keys: list[str] = Field(description="[후보 조문]에서 고른 번호(key)를 그대로. 맞는 조문이 없으면 빈 목록")


LAW_SYSTEM_INSTRUCTION = (
    "너는 법령 조사 담당이다. 생활 제도 안내 글의 주제를 보고, 검색으로 나온 [후보 조문] 가운데 "
    "그 제도의 지원 대상·금액·비율·한도·요건·신청 절차를 직접 정한 조문만 고른다. 이름만 비슷한 다른 제도"
    "(예: 전기요금 ↔ 전기통신 요금)나 목적·정의·권한 위임·과태료 조문은 고르지 않는다. 제도가 법령이 아니라 "
    "공기업 약관·지자체 공고로 운영되어 맞는 조문이 없으면 빈 목록을 돌려준다. 목록에 없는 번호는 쓰지 않는다."
)


LAW_CANDIDATE_CHARS = 500  # 금액·요건이 ②항 이하에 있는 조문도 고를 수 있게 앞부분을 넉넉히


def build_law_prompt(topic: str, candidates: Sequence[Any], limit: int) -> str:
    lines = "\n".join(f"- key={a.key} | {a.law} {a.heading} | {a.text[:LAW_CANDIDATE_CHARS].replace(chr(10), ' ')}"
                      for a in candidates)
    return (f"주제: {topic}\n\n[후보 조문]\n{lines}\n\n"
            f"이 주제의 근거 조문을 중요한 순서로 최대 {limit}개 골라 JSON으로 돌려준다.")


class CategoryChoice(BaseModel):
    slug: str = Field(description="[분류 목록]에 있는 slug 하나를 그대로")


CATEGORY_SYSTEM_INSTRUCTION = (
    "너는 블로그 편집자다. 글의 제목·요약·태그를 보고 [분류 목록]에서 독자가 이 글을 찾을 때 "
    "가장 먼저 들어가 볼 분류 하나를 고른다. 목록에 없는 slug는 쓰지 않는다."
)


def build_category_prompt(title: str, summary: str, tags: Sequence[str],
                          options: Sequence[tuple[str, str, str]]) -> str:
    """options: (slug, 이름, 설명) 목록."""
    lines = "\n".join(f"- slug={slug} | {name} | {desc}" for slug, name, desc in options)
    return (f"[글]\n제목: {title}\n요약: {summary}\n태그: {', '.join(tags)}\n\n"
            f"[분류 목록]\n{lines}\n\n이 글에 맞는 분류의 slug 하나를 JSON으로 돌려준다.")


class ContentGenerator:
    def __init__(
        self,
        client: Any,
        model: str,
        second_model: str | None = None,
        fallback_model: str | None = DEFAULT_FALLBACK_MODEL,
        sleep: Callable[[float], None] = time.sleep,
        retry_waits: Sequence[float] = RETRY_WAITS,
        blog_fallback: bool = False,
        flex: bool = False,
    ) -> None:
        self.client = client
        # True면 매 요청을 Flex 요금(50% 할인)으로 먼저 보내고, 혼잡·시간 초과면 같은 모델을 일반 요금으로 다시 보낸다.
        # 모델·지시문·자료는 그대로라 글 내용은 같고 응답만 늦어질 수 있다.
        self.flex = flex
        self.models = list(dict.fromkeys(m for m in (model, second_model, fallback_model) if m))
        # 블로그 본문은 1순위 모델 → 차선 모델 순서로만 쓴다(매번 1순위부터 시도하므로
        # 1순위 한도가 다시 채워지면 자동으로 1순위로 돌아감). 가장 가벼운 예비 모델 글은
        # 짧고 얕아 품질 검사를 자주 못 넘으므로 쇼츠 대본 고쳐 쓰기처럼 가벼운 일에만 쓴다.
        blog = [m for m in (model, second_model) if m and m != fallback_model]
        self.blog_models = self.models if blog_fallback else list(dict.fromkeys(blog))
        self.sleep = sleep
        self.retry_waits = tuple(retry_waits)

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs) -> "ContentGenerator":
        settings.require("gemini_api_key", "gemini_model")
        client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        )
        kwargs.setdefault("second_model", settings.gemini_second_model or None)
        kwargs.setdefault("flex", settings.gemini_flex)
        return cls(client, settings.gemini_model, **kwargs)

    def _call(self, model: str, prompt: str, schema: type = MasterContent,
              system: str = SYSTEM_INSTRUCTION, flex: bool | None = None) -> str:
        """flex: None이면 self.flex를 따른다. 사용자가 텔레그램에서 바로 기다리는 짧은 요청은 False."""
        if self.flex if flex is None else flex:
            try:
                return self._request(model, prompt, schema, system, flex=True)
            except Exception as exc:  # noqa: BLE001 - 일시 오류만 일반 요금으로 넘긴다
                if not _is_transient(exc):
                    raise
                # Flex는 서버가 자동으로 일반 요금으로 바꿔 주지 않으므로 여기서 한 번 넘긴다.
                log.warning("%s Flex 혼잡·지연(%s), 일반 요금으로 다시 요청", model, exc)
        return self._request(model, prompt, schema, system, flex=False)

    def _request(self, model: str, prompt: str, schema: type, system: str, flex: bool) -> str:
        extra: dict[str, Any] = {}
        if flex:
            extra = {"service_tier": types.ServiceTier.FLEX,
                     "http_options": types.HttpOptions(timeout=FLEX_TIMEOUT_MS)}
        response = self.client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.7,
                **extra,
            ),
        )
        text = getattr(response, "text", None)
        if not text:
            raise GenerationError("모델이 빈 응답을 돌려줬습니다")
        return text

    def _call_with_retry(self, prompt: str, models: Sequence[str] | None = None,
                         **call_kwargs) -> tuple[str, str]:
        """(응답 글자, 실제 쓴 모델). 모든 모델이 일시적 오류로 실패하면 ModelBusyError."""
        last: Exception | None = None
        for model in models or self.models:
            timeouts = 0
            for attempt in range(len(self.retry_waits) + 1):
                try:
                    return self._call(model, prompt, **call_kwargs), model
                except Exception as exc:  # noqa: BLE001 - 종류를 보고 재시도 여부 결정
                    if not _is_transient(exc):
                        raise
                    last = exc
                    # 사용 한도 초과(429)는 몇 분 기다려도 안 풀리는 경우가 많아 바로 다음 모델로
                    if getattr(exc, "code", None) == 429:
                        log.warning("%s 사용 한도 초과, 다음 모델로 넘어감", model)
                        break
                    # 시간 초과(504·타임아웃)는 한 번에 수 분씩 걸리므로 두 번 나면 바로 다음 모델로
                    if getattr(exc, "code", None) == 504 or "timeout" in exc.__class__.__name__.lower():
                        timeouts += 1
                        if timeouts >= MAX_TIMEOUTS_PER_MODEL:
                            break
                    if attempt < len(self.retry_waits):
                        wait = self.retry_waits[attempt]
                        log.warning("%s 일시 오류(%s), %s초 후 재시도", model, exc, wait)
                        self.sleep(wait)
            log.warning("%s 계속 실패, 다음 모델로 넘어감", model)
        raise ModelBusyError(f"모든 모델이 일시적 오류로 실패했습니다: {last}")

    def generate(
        self, topic: str, sources: Sequence[Source], target_seconds: int = 30,
        news: Sequence[tuple[str, str]] = (), checks: Sequence[str] = (),
    ) -> GenerationResult:
        if not sources:
            raise GenerationError("근거 자료가 없습니다. 자료 없이 글을 쓰지 않습니다.")
        feedback: str | None = None
        last_problems: list[str] = []
        # 마지막으로 형식이 맞았던 결과와 그 불합격 이유. 다음 회차는 이것을 고치게 한다(새로 쓰면 잘된 부분까지 버려짐).
        draft: tuple[str, list[str]] | None = None
        for round_no in range(1, MAX_QUALITY_ROUNDS + 1):
            prompt = build_prompt(topic, sources, target_seconds, feedback=feedback, news=news,
                                  previous=draft[0] if draft else None, checks=checks)
            raw, model = self._call_with_retry(prompt, models=self.blog_models)
            try:
                content = MasterContent.model_validate_json(raw)
            except ValidationError as exc:
                last_problems = [f"JSON 형식 오류: {exc.errors()[:3]}"]
                # 고친 결과의 형식이 깨졌으면 앞의 결과를 같은 지적으로 다시 고치게 한다
                problems_for_next = draft[1] if draft else last_problems
            else:
                problems, warnings = check_content(content, sources, target_seconds)
                problems = problems + coverage_problems(topic, content, sources)
                if problems and all(p.startswith(NARRATION_PROBLEM) for p in problems):
                    content, problems, warnings = self._repair_shorts(content, sources, target_seconds)
                if not problems:
                    content, card_fixes = tidy_shorts_cards(content)
                    return GenerationResult(content, model, round_no, warnings, card_fixes)
                last_problems = problems_for_next = list(problems)
                if not any(p.startswith(BODY_SHORT_PROBLEM) for p in problems):
                    # 다른 곳을 고치다가 본문을 줄여 다시 불합격하는 일을 막는다(불합격 이유 기록에는 넣지 않음)
                    problems_for_next = problems_for_next + [
                        f"{BODY_KEEP_PROBLEM} 지금 {BODY_SHORT_PROBLEM}은 {count_body_chars(content)}자입니다. "
                        f"고치면서 이보다 줄이지 말고 {MIN_BODY_CHARS}자 이상을 유지해 주세요."]
                draft = (content.model_dump_json(), problems_for_next)
            log.info("검사 불합격(%s회차): %s", round_no, last_problems)
            feedback = "\n".join(f"- {p}" for p in problems_for_next)
        raise GenerationError("검사를 통과하지 못했습니다: " + " / ".join(last_problems))

    def review(self, topic: str, sources: Sequence[Source], target_seconds: int,
               result: GenerationResult) -> GenerationResult:
        """발행 전 자동 검토: AI가 글을 자료와 다시 대조해 고친다. 사람에게는 고친 것 요약과,
        끝내 판단하지 못한 것만 넘긴다. 검토 결과가 검사를 통과하지 못하면 처음 글을 그대로 쓴다."""
        original = result.content
        _, before = check_content(original, sources, target_seconds)
        fixes = list(result.fixes)
        notes: list[str] = []
        content = original
        try:
            raw, _model = self._call_with_retry(
                build_review_prompt(topic, sources, original.model_dump_json(), before),
                models=self.blog_models, schema=ReviewOutcome, system=REVIEW_SYSTEM_INSTRUCTION)
            outcome = ReviewOutcome.model_validate_json(raw)
        except Exception as exc:  # noqa: BLE001 - 검토는 덧붙이는 단계라 실패해도 처음 글로 진행
            log.warning("발행 전 자동 검토 실패(처음 글 유지): %s: %s", exc.__class__.__name__, exc)
            notes.append("자동 검토를 하지 못했습니다(모델 오류). 숫자·표를 직접 한 번 봐 주세요.")
        else:
            try:
                candidate, why = self._accept_review(original, outcome, sources, target_seconds, topic)
            except Exception as exc:  # noqa: BLE001 - 검토 결과 확인 중 오류도 처음 글로 진행
                candidate, why = None, [f"{exc.__class__.__name__}: {exc}"]
            if candidate is None:
                log.warning("자동 검토 결과 불합격(처음 글 유지): %s", why)
                notes.append("자동 검토 결과가 검사를 통과하지 못해 처음 글을 그대로 두었습니다. 숫자·표를 직접 한 번 봐 주세요.")
            else:
                content = candidate
                fixes += [f.strip() for f in outcome.fixes if f.strip()][:6]
                notes += [f"자동 검토가 판단하지 못한 것: {u.strip()}" for u in outcome.unresolved if u.strip()]
        trimmed_content, trimmed = trim_takeaways(content)
        if trimmed and check_content(trimmed_content, sources, target_seconds)[0]:
            trimmed = []          # 요약 줄을 빼면 분량 검사에 걸리면 그대로 둔다(경고로 남음)
        else:
            content = trimmed_content
        content, card_fixes = tidy_shorts_cards(content)
        _, after = check_content(content, sources, target_seconds)
        warnings = [w for w in after if needs_person(w)] + notes
        return GenerationResult(content, result.model, result.attempts, warnings,
                                calc_check_note(content, sources) + fixes + trimmed + card_fixes)

    def _fact_issues(self, topic: str, sources: Sequence[Source], units: Sequence[str]) -> list[FactIssue]:
        raw, _model = self._call_with_retry(build_fact_prompt(topic, sources, units), models=self.blog_models,
                                            schema=FactCheckOutcome, system=FACT_SYSTEM_INSTRUCTION)
        return judge_claims(FactCheckOutcome.model_validate_json(raw), sources)

    def fact_check(self, topic: str, sources: Sequence[Source], target_seconds: int,
                   result: GenerationResult) -> tuple[GenerationResult, FactReport]:
        """자동 검토 뒤 사실 확인: 사실 문장을 자료와 하나씩 대조하고, 근거 없거나 자료와 다른 문장은
        한 번 고쳐 보게 한 뒤 고치면서 바뀐 문장만 다시 대조한다. 끝내 남은 문장은 사람에게 넘기고
        아침 자동 발행에서 뺀다(hold). AI 호출: 대조 1번, 문제 있으면 고치기 1번 + 바뀐 문장 대조 1번.
        모델 오류로 확인을 못 하면 글 전체를 다시 쓰지 않고 보류로 둔다(사람이 보고 [발행] 가능)."""
        content = result.content
        units = _article_units(content)
        try:
            issues = self._fact_issues(topic, sources, units)
        except Exception as exc:  # noqa: BLE001 - 확인을 못 했으면 글은 두고 자동 발행만 막는다
            log.warning("사실 확인 실패(자동 발행 보류): %s: %s", exc.__class__.__name__, exc)
            note = f"{FACT_WARNING} 사실 확인을 하지 못했습니다(모델 오류). 직접 보고 [발행]을 눌러 주세요."
            return (GenerationResult(content, result.model, result.attempts, list(result.warnings) + [note],
                                     list(result.fixes)), FactReport(hold=True, failed=True))
        if not issues:
            return result, FactReport(hold=False)
        fixes = list(result.fixes)
        warnings = list(result.warnings)
        located = _fact_norm("\n".join(units))
        extra: list[FactIssue] = []           # 고치기·재대조에서 새로 나온 문제
        failed = False
        try:
            problems = [f"사실 확인 결과 {i.line()}. 자료대로 고치거나, 자료에 없으면 빼거나 '담당 기관 확인'으로 바꾼다."
                        for i in issues]
            raw, _model = self._call_with_retry(
                build_review_prompt(topic, sources, content.model_dump_json(), problems),
                models=self.blog_models, schema=ReviewOutcome, system=REVIEW_SYSTEM_INSTRUCTION)
            outcome = ReviewOutcome.model_validate_json(raw)
            candidate, why = self._accept_review(content, outcome, sources, target_seconds, topic)
        except Exception as exc:  # noqa: BLE001 - 고치기에 실패하면 문장을 그대로 사람에게 넘긴다
            candidate, why = None, [f"{exc.__class__.__name__}: {exc}"]
        if candidate is None:
            log.warning("사실 확인 후 고치기 불합격(글 유지): %s", why)
        else:
            candidate, trimmed = trim_takeaways(candidate)
            candidate, card_fixes = tidy_shorts_cards(candidate)
            new_units = [u for u in _article_units(candidate) if u not in set(units)]
            try:                               # 고치면서 바뀐·새로 생긴 문장만 다시 대조(말만 바꿔 넘어가는 것 방지)
                extra = self._fact_issues(topic, sources, new_units) if new_units else []
            except Exception as exc:  # noqa: BLE001
                log.warning("고친 문장 재대조 실패(자동 발행 보류): %s: %s", exc.__class__.__name__, exc)
                failed = True
            extra += [FactIssue(u.strip(), "unsupported", "고치는 AI가 자료만으로 판단하지 못함")
                      for u in outcome.unresolved if u.strip()]
            before_checks = {w for w in check_content(content, sources, target_seconds)[1] if needs_person(w)}
            notes = [w for w in warnings if w not in before_checks]
            after = [w for w in check_content(candidate, sources, target_seconds)[1] if needs_person(w)]
            content, warnings = candidate, after + notes
            fixes = ([f for f in fixes if not f.startswith(CALC_NOTE)] + calc_check_note(content, sources)
                     + [f"사실 확인 후 고침: {f.strip()}" for f in outcome.fixes if f.strip()][:4]
                     + trimmed + card_fixes)
        article = _fact_norm("\n".join(_article_units(content)))
        # 처음 글에서 찾지 못한 문장(AI가 바꿔 옮겨 적음)은 고쳐졌는지 알 수 없으므로 남은 것으로 본다
        remaining = [i for i in issues if _fact_norm(i.sentence) in article
                     or _fact_norm(i.sentence) not in located] + extra
        lines = [i.line() for i in remaining]
        if failed:
            lines.append("고친 문장을 다시 대조하지 못했습니다(모델 오류). 고친 부분을 직접 봐 주세요.")
        shown = [f"{FACT_WARNING} {line}" for line in lines[:MAX_FACT_WARNINGS]]
        if len(lines) > MAX_FACT_WARNINGS:
            shown.append(f"{FACT_WARNING} 그 밖에 {len(lines) - MAX_FACT_WARNINGS}개 문장도 근거 확인 필요")
        report = FactReport(hold=bool(lines), remaining=lines, fixed=len(issues) - (len(remaining) - len(extra)),
                            failed=failed)
        return GenerationResult(content, result.model, result.attempts, warnings + shown, fixes), report

    def _accept_review(self, original: MasterContent, outcome: ReviewOutcome, sources: Sequence[Source],
                       target_seconds: int, topic: str = "") -> tuple[MasterContent | None, list[str]]:
        """검토 결과를 쓸 수 있으면 (글, []), 아니면 (None, 이유)."""
        candidate = outcome.content.model_copy(update={"slug": original.slug})
        problems, _ = check_content(candidate, sources, target_seconds)
        if problems and all(p.startswith(NARRATION_PROBLEM) for p in problems):
            # 대본 길이만 틀어졌으면 고친 글을 기준으로 대본 길이만 다시 맞춘다
            # (처음 대본으로 되돌리면 검토에서 바로잡은 숫자가 대본에서 다시 틀어질 수 있음)
            candidate, problems, _ = self._repair_shorts(candidate, sources, target_seconds)
        if problems:
            return None, problems[:2]
        added = (len(missing_figures(candidate, sources, with_calc=False))
                 - len(missing_figures(original, sources, with_calc=False)))
        if added > 0:
            return None, [f"자료에 없는 숫자가 {added}개 늘어남"]
        # 검토가 금액표·마감일을 지워 버리는 경우도 받지 않는다(처음 글에 있던 것만 따짐)
        lost = coverage_problems(topic, candidate, sources)
        if lost and not coverage_problems(topic, original, sources):
            return None, lost[:1]
        return candidate, []

    def choose_law_articles(self, topic: str, candidates: Sequence[Any], limit: int = 4) -> list[str]:
        """법령 검색 후보 가운데 주제의 근거 조문 번호. 목록 밖 번호는 버린다."""
        if not candidates:
            return []
        raw, _model = self._call_with_retry(build_law_prompt(topic, candidates, limit),
                                            schema=LawChoice, system=LAW_SYSTEM_INSTRUCTION)
        try:
            keys = LawChoice.model_validate_json(raw).keys
        except ValidationError as exc:
            raise GenerationError(f"조문 고르기 응답 형식 오류: {exc.errors()[:1]}") from exc
        valid = {a.key for a in candidates}
        return [k for k in dict.fromkeys(k.strip() for k in keys) if k in valid][:limit]

    def read_changes(self, topic: str, articles: Sequence[Source], dates: dict) -> list[ChangeItem]:
        """기사에서 주제 제도의 최근 변경을 확정/논의 중으로 나눈다(프로그램이 recent_change에서 다시 확인)."""
        raw, _model = self._call_with_retry(build_change_prompt(topic, articles, dates),
                                            schema=ChangeMemo, system=CHANGE_SYSTEM_INSTRUCTION)
        try:
            return ChangeMemo.model_validate_json(raw).items[:6]
        except ValidationError as exc:
            raise GenerationError(f"최근 변경 응답 형식 오류: {exc.errors()[:1]}") from exc

    def structured(self, prompt: str, schema: type, system: str, main_only: bool = False) -> tuple[str, str]:
        """정해진 형식(JSON)으로 답을 받는 짧은 질문. (응답 글자, 실제 쓴 모델).
        main_only: 1순위 모델 판정만 믿는 곳(자동 반영 판정)용. 1순위가 실패하면 예비 모델을 부르지 않고
        ModelBusyError(예비 모델 결과는 어차피 버리므로 호출만 낭비)."""
        return self._call_with_retry(prompt, models=self.models[:1] if main_only else None,
                                     schema=schema, system=system)

    def choose_category(self, title: str, summary: str, tags: Sequence[str],
                        options: Sequence[tuple[str, str, str]]) -> str:
        """글에 맞는 분류 slug 하나. 목록 밖의 답이면 GenerationError."""
        if not options:
            raise GenerationError("고를 분류가 없습니다")
        raw, _model = self._call_with_retry(build_category_prompt(title, summary, tags, options),
                                            schema=CategoryChoice, system=CATEGORY_SYSTEM_INSTRUCTION)
        try:
            slug = CategoryChoice.model_validate_json(raw).slug.strip()
        except ValidationError as exc:
            raise GenerationError(f"분류 응답 형식 오류: {exc.errors()[:1]}") from exc
        if slug not in {o[0] for o in options}:
            raise GenerationError(f"목록에 없는 분류를 골랐습니다: {slug[:40]}")
        return slug

    def _repair_shorts(
        self, content: MasterContent, sources: Sequence[Source], target_seconds: int
    ) -> tuple[MasterContent, list[str], list[str]]:
        """쇼츠 길이만 틀렸을 때 대본만 다시 쓴다. 블로그 글은 그대로 둔다."""
        blog_facts = "\n".join([content.title, *(paragraph_text(p) for p in content.intro)] + [
            "\n".join(section_texts(s)) for s in content.sections
        ] + [f"{f.question} {f.answer}" for f in content.faq] + [paragraph_text(content.closing)])
        problems, warnings = check_content(content, sources, target_seconds)
        for _ in range(MAX_SHORTS_REPAIRS):
            current = sum(len(sc.narration) for sc in content.shorts.scenes)
            prompt = build_shorts_repair_prompt(blog_facts, content.shorts.model_dump_json(),
                                                target_seconds, current)
            raw, _model = self._call_with_retry(prompt, schema=Shorts,
                                                system=SHORTS_SYSTEM_INSTRUCTION)
            try:
                shorts = Shorts.model_validate_json(raw)
            except ValidationError:
                continue
            candidate = content.model_copy(update={"shorts": shorts})
            problems, warnings = check_content(candidate, sources, target_seconds)
            content = candidate
            if not problems:
                break
            log.info("쇼츠 대본 고쳐 쓰기 불합격: %s", problems)
        return content, problems, warnings
