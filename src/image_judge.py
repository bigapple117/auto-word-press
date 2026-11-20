"""대표 사진 후보를 Gemini가 직접 보고 고른다.

Pixabay 검색은 태그 글자만 맞춰 보므로 종합부동산세 글에 스페인 식당 영수증이 걸리는 식의 일이
생긴다(2026-11-20 발행 글 34개 중 13개가 주제와 어긋남). 대표 사진은 홈 카드·공유 미리보기에 보이는
첫인상이라 이 한 장만 AI가 보고 고른다. 본문 사진은 지금처럼 검색 순서대로 쓴다.

판정 실패(오류·한도·이상한 응답)는 JudgeError로 알리고, 부르는 쪽은 예전 방식(첫 후보)으로 돌아간다.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Sequence

from google import genai
from google.genai import types

from src.config import Settings

log = logging.getLogger(__name__)

JUDGE_INSTRUCTION = """\
한국의 생활 제도 정보 블로그 '잘체크'의 대표 사진(목록 썸네일·공유 미리보기)을 고른다.
글 제목: {topic}
사진 검색어: {query}

아래 사진 {count}장(0번부터 차례대로)을 직접 보고, 다음 조건을 모두 지키는 사진 중 글 주제에 가장 잘 어울리는 한 장을 고른다.
1. 주제와 어울린다. 세금·지원금 글이면 서류·계산·집·가족·일상처럼 내용과 이어지는 장면. 식당 영수증, 웹사이트 통계 화면, 공장 배관처럼 주제와 엉뚱하거나 오해를 부를 사진은 안 된다. 계산기·서류 같은 일반 사진은 엉뚱한 사진보다는 낫다.
2. 상표·로고·브랜드 이름이 눈에 띄게 보이지 않는다(작아서 거의 안 보이면 괜찮다).
3. 얼굴을 알아볼 수 있는 사람이 주인공이 아니다. 특히 빈곤·빚·질병·장애·복지 지원처럼 어려운 처지를 떠올리게 하는 주제에서는 안 된다. 손·뒷모습·멀리 있는 사람은 괜찮다.
4. 외국어 글자(영어 문서 제목, 외국 영수증 등)가 크게 읽히지 않는다.

조건을 지키는 사진이 하나도 없으면 best를 -1로 한다.
reason에는 고른 이유나 모두 뺀 이유를 한국어 한 문장으로 쓴다.
"""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "best": {"type": "INTEGER"},
        "reason": {"type": "STRING"},
    },
    "required": ["best", "reason"],
}


class JudgeError(RuntimeError):
    """판정을 못 했다(부르는 쪽은 예전 방식으로 고른다)."""


class GeminiImageJudge:
    def __init__(self, client: Any, model: str) -> None:
        self.client = client
        self.model = model

    @classmethod
    def from_settings(cls, settings: Settings) -> "GeminiImageJudge":
        settings.require("gemini_api_key")
        return cls(genai.Client(api_key=settings.gemini_api_key,
                                http_options=types.HttpOptions(timeout=90_000)), settings.gemini_model)

    def __call__(self, topic: str, query: str, previews: Sequence[Path]) -> int | None:
        """고른 사진의 순번(0부터). 조건에 맞는 사진이 없으면 None."""
        if not previews:
            return None
        try:
            parts: list[Any] = [JUDGE_INSTRUCTION.format(topic=topic, query=query, count=len(previews))]
            for i, path in enumerate(previews):
                parts.append(f"{i}번 사진:")
                parts.append(types.Part.from_bytes(data=Path(path).read_bytes(), mime_type=_mime(path)))
            resp = self.client.models.generate_content(
                model=self.model, contents=parts,
                config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json",
                                                   response_schema=RESPONSE_SCHEMA, max_output_tokens=1024),
            )
            data = json.loads(getattr(resp, "text", "") or "")
            best = int(data["best"])
        except Exception as exc:  # noqa: BLE001 - 어떤 실패든 예전 방식으로 돌아가면 된다
            raise JudgeError(f"대표 사진 판정 실패: {exc.__class__.__name__}") from exc
        reason = str(data.get("reason", "")).strip()
        if best == -1:
            log.info("대표 사진 후보 %s장 모두 부적합(%s): %s", len(previews), topic, reason)
            return None
        if not 0 <= best < len(previews):
            raise JudgeError(f"대표 사진 판정 응답이 범위를 벗어남: {best}")
        log.info("대표 사진 %s번 선택(%s): %s", best, topic, reason)
        return best


def _mime(path: Path) -> str:
    return "image/png" if Path(path).suffix.lower() == ".png" else "image/jpeg"


__all__ = ["GeminiImageJudge", "JudgeError"]
