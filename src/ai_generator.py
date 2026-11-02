"""Gemini로 마스터 콘텐츠(블로그 + 쇼츠 대본)를 만들고 검사한다.

- 일시적 오류(바쁨 503, 한도 429, 서버 500, 시간 초과)는 기다렸다 다시 시도하고,
  그래도 안 되면 예비 모델로 넘어간다.
- 형식·사실 규칙 검사에 걸리면 고칠 점을 알려 주고 다시 쓰게 한다.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import ValidationError

from src.config import Settings
from src.content_schema import MasterContent
from src.source_fetcher import Source
from templates.prompt_template import SYSTEM_INSTRUCTION, build_prompt, narration_budget

log = logging.getLogger(__name__)

DEFAULT_FALLBACK_MODEL = "gemini-3.5-flash-lite"
RETRY_WAITS = (10, 30, 60)       # 일시적 오류 재시도 간격(초)
MAX_QUALITY_ROUNDS = 3           # 검사 불합격 시 최대 작성 횟수
REQUEST_TIMEOUT_MS = 300_000

_TRANSIENT_CODES = {429, 500, 502, 503, 504}
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0000FE0F\U0000200D]"
)
_MARKDOWN = re.compile(r"\*\*|__|^#{1,6}\s|^\s*[-*]\s", re.MULTILINE)
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
BANNED_PHRASES = (
    "알아보겠습니다", "알아볼까요", "결론적으로", "마무리하며", "도움이 되셨길",
    "도움이 되셨으면", "꿀팁", "총정리", "모르면 손해",
)


class GenerationError(RuntimeError):
    """여러 번 시도해도 쓸 만한 결과를 못 얻었을 때."""


@dataclass
class GenerationResult:
    content: MasterContent
    model: str
    attempts: int
    warnings: list[str]


_EOK = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*억(?:\s*(\d)\s*천)?(?:\s*(\d[\d,]*)\s*만)?\s*원?")
_MAN = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(천만|백만|만)\s*원?")
_WON = re.compile(r"(\d[\d,]*)\s*원")
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_UNIT = {"천만": 10_000_000, "백만": 1_000_000, "만": 10_000}


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def extract_figures(text: str) -> dict[str, str]:
    """글에서 금액(원 단위로 환산)과 비율을 뽑는다. {정규화 값: 원래 표기}."""
    found: dict[str, str] = {}

    def take(pattern: re.Pattern, value_of) -> None:
        nonlocal text
        for m in pattern.finditer(text):
            found.setdefault(value_of(m), m.group(0).strip())
        text = pattern.sub(" ", text)   # 이미 센 부분은 다음 규칙에서 다시 세지 않음

    take(_EOK, lambda m: f"won:{round(_num(m.group(1)) * 1e8 + int(m.group(2) or 0) * 1e7 + _num(m.group(3) or '0') * 1e4)}")
    take(_MAN, lambda m: f"won:{round(_num(m.group(1)) * _UNIT[m.group(2)])}")
    take(_WON, lambda m: f"won:{round(_num(m.group(1)))}")
    take(_PCT, lambda m: f"pct:{_num(m.group(1)):g}")
    return found


def _all_texts(c: MasterContent) -> list[str]:
    texts = [c.title, c.meta_description, c.reference_note, c.closing, *c.intro, c.shorts.title]
    for s in c.sections:
        texts += [s.heading, *s.paragraphs, *(s.checklist or [])]
        if s.table:
            texts += [s.table.caption, *s.table.headers, *(cell for row in s.table.rows for cell in row)]
    for f in c.faq:
        texts += [f.question, f.answer]
    texts += [sc.narration for sc in c.shorts.scenes]
    return texts


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

    for i, s in enumerate(c.sections, 1):
        if not s.paragraphs:
            problems.append(f"소제목 {i} '{s.heading}'에 문단이 없습니다.")
        if not s.source_ids:
            problems.append(f"소제목 {i} '{s.heading}'에 근거 자료 번호가 없습니다.")
        bad = [x for x in s.source_ids if x not in valid_ids]
        if bad:
            problems.append(f"소제목 {i}에 없는 자료 번호가 있습니다: {', '.join(bad)}")
        if s.table:
            width = len(s.table.headers)
            if not 2 <= width <= 5:
                problems.append(f"소제목 {i} 표의 열은 2~5개여야 합니다.")
            if not s.table.rows or any(len(r) != width for r in s.table.rows):
                problems.append(f"소제목 {i} 표의 행마다 칸 수가 열 이름 수({width})와 같아야 합니다.")
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
        problems.append("마크다운 기호(**, #, - 목록)를 쓰지 말고 평문으로 써 주세요.")
    used = [p for p in BANNED_PHRASES if p in joined]
    if used:
        problems.append(f"금지 표현을 다른 말로 바꿔 주세요: {', '.join(used)}")

    low, high = narration_budget(target_seconds)
    total = sum(len(sc.narration) for sc in c.shorts.scenes)
    if not low <= total <= high:
        problems.append(
            f"쇼츠 내레이션 전체 길이를 공백 포함 {low}~{high}자로 맞춰 주세요(현재 {total}자)."
        )

    body_chars = sum(len(p) for s in c.sections for p in s.paragraphs) + sum(len(p) for p in c.intro)
    if body_chars < 1500:
        warnings.append(f"본문이 짧습니다({body_chars}자).")
    if c.unverified:
        warnings.append("자료로 확인 안 된 내용: " + " / ".join(c.unverified))

    # 금액·비율이 자료에 있는지 대조. 표기 차이로 틀리게 걸릴 수 있어 경고로 남기고 사람이 확인한다.
    # 쇼츠 내레이션은 숫자를 한글로 풀어 쓰므로 대조하지 않는다.
    source_figures = extract_figures("\n".join(s.text for s in sources))
    blog_text = "\n".join(t for t in texts if t not in {sc.narration for sc in c.shorts.scenes})
    missing = [shown for key, shown in extract_figures(blog_text).items() if key not in source_figures]
    if missing:
        warnings.append("자료에서 못 찾은 숫자(확인 필요): " + ", ".join(missing))
    return problems, warnings


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, genai_errors.APIError):
        return getattr(exc, "code", None) in _TRANSIENT_CODES
    name = exc.__class__.__name__.lower()
    return "timeout" in name or "connect" in name


class ContentGenerator:
    def __init__(
        self,
        client: Any,
        model: str,
        fallback_model: str | None = DEFAULT_FALLBACK_MODEL,
        sleep: Callable[[float], None] = time.sleep,
        retry_waits: Sequence[float] = RETRY_WAITS,
    ) -> None:
        self.client = client
        self.models = [m for m in (model, fallback_model) if m]
        self.sleep = sleep
        self.retry_waits = tuple(retry_waits)

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs) -> "ContentGenerator":
        settings.require("gemini_api_key", "gemini_model")
        client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        )
        return cls(client, settings.gemini_model, **kwargs)

    def _call(self, model: str, prompt: str) -> str:
        response = self.client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=MasterContent,
                temperature=0.7,
            ),
        )
        text = getattr(response, "text", None)
        if not text:
            raise GenerationError("모델이 빈 응답을 돌려줬습니다")
        return text

    def _call_with_retry(self, prompt: str) -> tuple[str, str]:
        """(응답 글자, 실제 쓴 모델). 모든 모델이 일시적 오류로 실패하면 GenerationError."""
        last: Exception | None = None
        for model in self.models:
            for attempt in range(len(self.retry_waits) + 1):
                try:
                    return self._call(model, prompt), model
                except Exception as exc:  # noqa: BLE001 - 종류를 보고 재시도 여부 결정
                    if not _is_transient(exc):
                        raise
                    last = exc
                    if attempt < len(self.retry_waits):
                        wait = self.retry_waits[attempt]
                        log.warning("%s 일시 오류(%s), %s초 후 재시도", model, exc, wait)
                        self.sleep(wait)
            log.warning("%s 계속 실패, 다음 모델로 넘어감", model)
        raise GenerationError(f"모든 모델이 일시적 오류로 실패했습니다: {last}")

    def generate(
        self, topic: str, sources: Sequence[Source], target_seconds: int = 45
    ) -> GenerationResult:
        if not sources:
            raise GenerationError("근거 자료가 없습니다. 자료 없이 글을 쓰지 않습니다.")
        feedback: str | None = None
        last_problems: list[str] = []
        for round_no in range(1, MAX_QUALITY_ROUNDS + 1):
            prompt = build_prompt(topic, sources, target_seconds, feedback=feedback)
            raw, model = self._call_with_retry(prompt)
            try:
                content = MasterContent.model_validate_json(raw)
            except ValidationError as exc:
                last_problems = [f"JSON 형식 오류: {exc.errors()[:3]}"]
            else:
                problems, warnings = check_content(content, sources, target_seconds)
                if not problems:
                    return GenerationResult(content, model, round_no, warnings)
                last_problems = problems
            log.info("검사 불합격(%s회차): %s", round_no, last_problems)
            feedback = "\n".join(f"- {p}" for p in last_problems)
        raise GenerationError("검사를 통과하지 못했습니다: " + " / ".join(last_problems))
