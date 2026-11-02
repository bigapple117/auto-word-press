"""서버에서 직접 못 읽는 페이지를 Gemini '페이지 읽기'(url_context)로 대신 읽는다.

우리 서버는 일본에 있어 해외 접속을 막은 한국 기관 사이트가 있다. 구글 서버가 대신 읽고
AI가 본문을 옮겨 적으므로, 결과 Source는 via="gemini"로 표시해 사람이 원문을 대조하게 한다.
"""
from __future__ import annotations

import logging
from typing import Any

from google import genai
from google.genai import types

from src.config import Settings
from src.source_fetcher import (DEFAULT_MAX_CHARS, BlockedUrlError, Source, SourceError, _parse)

log = logging.getLogger(__name__)

DEFAULT_READER_MODEL = "gemini-3.5-flash-lite"
FAILED_MARK = "읽기 실패"

READ_INSTRUCTION = """\
아래 주소의 웹 페이지를 읽고 본문을 옮겨 적는다. 요약하거나 고쳐 쓰지 않는다.
- 첫 줄: 페이지 제목만.
- 둘째 줄부터: 본문을 원문 문장 그대로. 숫자·날짜·금액·비율·기관명은 한 글자도 바꾸지 않는다.
- 메뉴, 로그인, 저작권, 광고, 관련 링크 목록은 뺀다.
- 표는 한 칸씩 '항목: 값' 줄로 옮긴다.
- 페이지를 읽지 못했거나 본문이 없으면 '읽기 실패' 한 줄만 쓴다. 기억이나 추측으로 채우지 않는다.
주소: {url}
"""


class GeminiPageReader:
    def __init__(self, client: Any, model: str = DEFAULT_READER_MODEL,
                 max_chars: int = DEFAULT_MAX_CHARS) -> None:
        self.client = client
        self.model = model
        self.max_chars = max_chars

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs) -> "GeminiPageReader":
        settings.require("gemini_api_key")
        return cls(genai.Client(api_key=settings.gemini_api_key,
                                http_options=types.HttpOptions(timeout=120_000)), **kwargs)

    def __call__(self, url: str, source_id: str) -> Source:
        _parse(url)   # 형식·포트·계정 검사는 직접 읽기와 같게(BlockedUrlError)
        try:
            resp = self.client.models.generate_content(
                model=self.model,
                contents=READ_INSTRUCTION.format(url=url),
                config=types.GenerateContentConfig(
                    tools=[types.Tool(url_context=types.UrlContext())], temperature=0),
            )
        except Exception as exc:  # noqa: BLE001 - 어떤 오류든 이 자료 하나의 실패로 처리
            raise SourceError(f"Gemini 대신 읽기 실패: {exc.__class__.__name__}") from exc

        statuses = []
        meta = getattr(resp.candidates[0], "url_context_metadata", None) if resp.candidates else None
        for item in getattr(meta, "url_metadata", None) or []:
            statuses.append(str(item.url_retrieval_status))
        if not any(st.endswith("SUCCESS") for st in statuses):
            raise SourceError(f"Gemini가 페이지를 가져오지 못했습니다({', '.join(statuses) or '상태 없음'}): {url}")

        text = (getattr(resp, "text", "") or "").strip()
        if not text or text.startswith(FAILED_MARK):
            raise SourceError(f"Gemini가 본문을 찾지 못했습니다: {url}")
        title, _, body = text.partition("\n")
        body = body.strip()
        if len(body) < 200:
            raise SourceError(f"Gemini가 옮긴 본문이 너무 짧습니다: {url}")
        log.info("Gemini로 대신 읽음: %s (%s자)", url, len(body))
        return Source(id=source_id, url=url, title=title.strip() or url,
                      text=body[: self.max_chars], via="gemini")


__all__ = ["GeminiPageReader", "BlockedUrlError"]
