"""블로그 글 + 쇼츠 대본을 한 번에 만드는 Gemini 지시문.

원칙: 제공된 자료에 있는 사실만 쓴다. 자연스러운 한국어, AI 티·광고 티 없이.
"""
from __future__ import annotations

from datetime import date
from typing import Sequence

from src.source_fetcher import Source

# 한국어 성우 속도(공백 포함 글자/초). edge-tts SunHi 실측 약 5자/초.
CHARS_PER_SECOND = 5.0

SYSTEM_INSTRUCTION = """\
너는 한국 생활 정보 블로그 '잘체크'의 편집자다. 정부 지원금, 세금, 제도, 생활비 정보를
독자가 스스로 판단할 수 있게 정확하고 쉽게 정리한다.

[사실 규칙 - 가장 중요]
1. 금액, 기준, 날짜, 기간, 비율, 신청 방법, 기관명은 제공된 [자료]에 적힌 것만 쓴다.
   자료에 없으면 쓰지 말고 unverified 목록에 무엇이 빠졌는지 적는다.
2. 자료끼리 내용이 다르면 더 최신이고 공식적인 쪽을 쓰고, 다르다는 사실을 본문에 짧게 밝힌다.
3. 연도는 자료에 적힌 대로 쓴다. '올해', '작년'처럼 기준이 흐린 말 대신 '2026년'처럼 쓴다.
   '오늘 날짜'와 비교해 이미 끝난 기간은 과거형으로 쓰고, 지금 할 수 있는 신청·절차를 앞세운다.
   블로그와 쇼츠 모두 같은 규칙을 지킨다.
4. 각 소제목과 FAQ의 source_ids에는 실제로 근거로 쓴 자료 번호만 넣는다.
5. 개인 상황에 따라 달라지는 판단(받을 수 있다/없다)은 단정하지 말고 확인 방법을 알려 준다.
6. [최근 뉴스]는 제도가 바뀌었을 수 있다는 신호일 뿐 사실 근거가 아니다. 뉴스에서 숫자·조건을 가져오지 않는다.
   자료 내용이 뉴스와 달라 보이면(제도 종료, 신규 가입 중단, 개편, 후속 상품 등) 도입부에서
   "자료 기준 시점"과 "최신 변경 여부는 담당 기관에서 확인이 필요하다"는 점을 분명히 밝히고,
   지금도 가능한 것처럼 단정하지 않으며, unverified에 무엇이 확인되지 않았는지 적는다.

[문체 규칙]
- 사람이 직접 정리한 듯한 자연스러운 존댓말(~습니다/~요 섞지 말고 ~습니다체로 통일).
- 이모지, 특수 기호 장식, 느낌표 남발 금지. 마크다운(**, #, -) 금지.
- 이런 상투 표현 금지: "오늘은 ~에 대해 알아보겠습니다", "~에 대해 자세히 알아볼까요",
  "결론적으로", "마무리하며", "도움이 되셨길 바랍니다", "꿀팁", "총정리".
- 같은 어미·같은 문장 구조를 연달아 반복하지 않는다. 짧은 문장과 긴 문장을 섞는다.
- 목록(checklist)과 표는 정말 비교·확인에 도움이 될 때만 쓴다. 문단 대신 목록으로 때우지 않는다.
- 과장·낚시 제목 금지("무조건", "100%", "모르면 손해"). 광고 문구처럼 쓰지 않는다.

[쇼츠 대본 규칙]
- 블로그와 같은 사실만 쓴다. 숫자는 성우가 읽기 쉽게 쓴다(예: '2,200만 원' → '이천이백만 원').
- 장면당 1~2문장, 구어체 존댓말. 첫 장면은 궁금증을 만드는 질문이나 핵심 숫자.
- 마지막 장면은 '자세한 조건은 블로그 글에서 확인하세요'처럼 블로그로 안내.
"""


def narration_budget(target_seconds: int) -> tuple[int, int]:
    """목표 길이에 맞는 쇼츠 내레이션 글자 수 범위(공백 포함)."""
    center = target_seconds * CHARS_PER_SECOND
    return int(center * 0.8), int(center * 1.15)


def scene_plan(target_seconds: int) -> tuple[int, int]:
    """(장면 수, 장면당 글자 수). 모델이 한국어 글자 수를 짧게 잡는 경향이 있어 장면별로 알려 준다."""
    low, high = narration_budget(target_seconds)
    scenes = max(4, min(8, round(target_seconds / 8)))
    return scenes, round((low + high) / 2 / scenes)


def build_prompt(
    topic: str,
    sources: Sequence[Source],
    target_seconds: int,
    today: date | None = None,
    feedback: str | None = None,
    news: Sequence[tuple[str, str]] = (),
) -> str:
    """사용자 지시문을 만든다. feedback은 이전 결과가 검사에 걸렸을 때 고칠 점.

    news: (날짜, 제목) 목록. 변경 신호로만 쓴다.
    """
    today = today or date.today()
    low, high = narration_budget(target_seconds)
    blocks = []
    for s in sources:
        blocks.append(f"<자료 id=\"{s.id}\" 제목=\"{s.title}\" 주소=\"{s.url}\">\n{s.text}\n</자료>")
    parts = [
        f"오늘 날짜: {today.isoformat()}",
        f"주제: {topic}",
        f"쇼츠 목표 길이: {target_seconds}초 → 모든 장면 narration을 합쳐 공백 포함 {low}~{high}자"
        f" (권장: {scene_plan(target_seconds)[0]}장면 × 장면당 약 {scene_plan(target_seconds)[1]}자)",
        "블로그 본문은 소제목 4~6개, 전체 2,000~3,500자 정도로 쓴다.",
        "아래 [자료]만 근거로 마스터 콘텐츠 JSON을 만든다. 자료 밖 지식으로 숫자나 기준을 채우지 않는다.",
        "[자료]",
        "\n\n".join(blocks),
    ]
    if news:
        lines = "\n".join(f"- {d} {t}" for d, t in news)
        parts.append(f"[최근 뉴스 - 변경 신호, 사실 근거 아님]\n{lines}")
    if feedback:
        parts.append(f"[이전 결과에서 고칠 점]\n{feedback}")
    return "\n\n".join(parts)


SHORTS_SYSTEM_INSTRUCTION = """\
너는 한국 생활 정보 쇼츠 대본 작가다. 주어진 [블로그 글]에 있는 사실만 써서 성우가 읽을 대본을 고친다.
숫자는 한글로 풀어 쓰고(예: 이천이백만 원), 구어체 존댓말, 이모지·마크다운 금지.
이미 끝난 기간은 과거형으로 쓰고 지금 할 수 있는 일을 안내한다. 마지막 장면은 블로그 글로 안내한다.
"""


def build_shorts_repair_prompt(
    blog_facts: str, shorts_json: str, target_seconds: int, current_chars: int,
    today: date | None = None,
) -> str:
    """쇼츠 대본 길이만 맞지 않을 때 대본만 다시 쓰게 하는 지시문."""
    today = today or date.today()
    low, high = narration_budget(target_seconds)
    scenes, per_scene = scene_plan(target_seconds)
    return "\n\n".join([
        f"오늘 날짜: {today.isoformat()}",
        f"현재 대본은 공백 포함 {current_chars}자라서 {target_seconds}초에 맞지 않는다.",
        f"모든 장면 narration 합계를 공백 포함 {low}~{high}자로 다시 쓴다. "
        f"{scenes}장면, 장면마다 {per_scene - 5}~{per_scene + 5}자(공백 포함)를 지킨다.",
        "짧으면 블로그 글의 사실(조건, 예시, 신청 방법)을 더 넣어 채운다. 없는 사실을 지어내지 않는다.",
        "[현재 대본]", shorts_json,
        "[블로그 글]", blog_facts,
    ])
