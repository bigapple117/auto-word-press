"""블로그 글 + 쇼츠 대본을 한 번에 만드는 Gemini 지시문.

원칙: 제공된 자료에 있는 사실만 쓴다. 자연스러운 한국어, AI 티·광고 티 없이.
"""
from __future__ import annotations

from datetime import date
from typing import Sequence

from src.source_fetcher import Source

# 한국어 성우 속도(공백 포함 글자/초). edge-tts SunHi 실측: 대본 200자 → 31.5초(장면 사이 쉼 포함) ≈ 6.3자/초.
CHARS_PER_SECOND = 6.2

# 블로그 본문 목표 글자 수(공백 포함). 검사(ai_generator.check_content)와 같은 범위만 센다:
# 도입 문단 + 소제목의 문단·주의·요약. 표·체크리스트·FAQ·마무리는 넣지 않는다.
# 모델은 한국어 글자 수를 짧게 잡는 경향이 있어 소제목당 분량도 함께 알려 준다.
BODY_TARGET_CHARS = (1500, 2500)
SECTION_TARGET_CHARS = (300, 450)

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
   다만 지원 대상(소득·수급 자격·나이 등 받을 수 있는 사람의 범위)은 자료에 적힌 대로 eligibility에 쓰고,
   도입부에서도 한 번 밝힌다. 대상이 정해진 제도를 누구나 받을 수 있는 것처럼 읽히게 쓰지 않는다
   (예: 표에 나이만 적고 수급 자격을 빼지 않는다, 대상 여부를 말하지 않고 "서둘러 신청하세요"라고 쓰지 않는다).
6. [최근 뉴스]는 제도가 바뀌었을 수 있다는 신호일 뿐 사실 근거가 아니다. 뉴스에서 숫자·조건을 가져오지 않는다.
   자료 내용이 뉴스와 달라 보이면(제도 종료, 신규 가입 중단, 개편, 후속 상품 등) 도입부에서
   "자료 기준 시점"과 "최신 변경 여부는 담당 기관에서 확인이 필요하다"는 점을 분명히 밝히고,
   지금도 가능한 것처럼 단정하지 않으며, unverified에 무엇이 확인되지 않았는지 적는다.

[독자 관점 - 사람이 쓴 정보 글처럼]
- 독자는 "나도 받을 수 있나, 언제까지, 어디서" 를 빨리 알고 싶은 사람이다. 제도 설명보다 독자의 판단을 돕는다.
- 도입부 첫 문장을 "○○은 ~하는 제도입니다" 같은 사전식 정의로 시작하지 않는다.
  독자가 확인하려는 문제와 이 글에서 다룰 범위(무엇부터 보면 되는지)를 먼저 말한다.
- 법령·공문서 용어는 풀어 쓴다(예: 거주자 → 신청자 본인, 직계존속 → 부모님·조부모님(직계존속)).
- "만약 ~라면", "~에 해당한다면" 처럼 독자가 자기 상황에 대입할 수 있는 조건 문장을 쓴다.
- 지어낸 경험담, 가짜 후기, "제가 직접 받아 보니" 같은 거짓 1인칭은 절대 쓰지 않는다.

[구성 규칙]
- 각 소제목의 blocks에는 필요한 것만 골라 넣는다. 모든 소제목을 같은 구성(문단→표→체크리스트)으로 만들지 않는다.
- 표와 본문의 역할을 나눈다. 숫자·조건 비교는 표에 넣고, 문단에는 "어디에 해당하는지 구분하는 법,
  예외, 주의점, 다음 행동"을 쓴다. 표의 행 내용을 문단에서 하나씩 다시 읽어 주지 않는다.
- 표는 열 2~4개, 칸은 짧게 쓴다. 모든 행에 같은 값이 들어가는 열은 빼고 표 위 문단에 한 번만 쓴다.
- 놓치면 불이익이 있는 내용은 note(caution)로, 편집자의 확인 요령은 note(tip)로 짧게 쓴다.
- checklist와 FAQ는 본문과 겹치지 않을 때만 쓴다. 개수를 채우려고 만들지 않는다.
- takeaway는 꼭 필요한 소제목 2~3곳에만, 사진(image_query)은 글 전체에서 최대 2곳만 쓴다.
- 소제목은 독자 질문형("내 가구 유형은 어디일까"), 행동형("신청 전 확인할 것"), 명사형을 섞는다.
- 마무리는 본문 요약을 반복하지 말고, 지금 할 수 있는 다음 행동과 공식 확인처를 짧게 안내한다.
- action_links에는 독자가 실제로 신청하거나 자격·결과를 조회할 수 있는 공식 사이트 1~4개를 넣는다.
  주소는 반드시 [자료]의 주소이거나 [자료] 본문에 적힌 주소만 쓴다(예: 본문에 'www.hometax.go.kr'이 있으면 사용 가능).
  주소를 짐작하거나 지어내지 않는다. 마땅한 주소가 없으면 비워 둔다.
  특정 시·군·구청 안내 페이지보다 제도를 운영하는 전국 공식 누리집(예: 문화누리카드 www.mnuri.kr)을 먼저 넣는다.
  서로 다른 정부 자료(.go.kr 등) 2곳 이상의 본문에 도메인이 안내된 사업 누리집은 .go.kr이 아니어도 첫 화면 주소(예: https://www.mnuri.kr/)로 쓸 수 있다.
  지자체 페이지는 그 지역 주민만을 위한 글일 때만 쓴다.
  주소가 사이트 첫 화면(예: https://www.hometax.go.kr/)이면 label은 "홈택스 바로가기"처럼 쓰고,
  "장려금 신청하기"·"심사진행상황 조회"처럼 특정 화면으로 바로 가는 것 같은 이름을 붙이지 않는다.
  어느 메뉴로 들어가야 하는지는 note에 적는다.

[문장 규칙]
- 문단은 1~3문장, 공백 포함 250자 이내. 긴 설명은 문단을 나눈다.
- 기본은 ~습니다체. 같은 종결 표현(~합니다, ~있습니다, ~됩니다)을 3문장 연속 쓰지 않고
  "~해 보세요", "~하면 됩니다", "~인지 먼저 확인하세요" 같은 안내형을 자연스럽게 섞는다.
  어미를 바꾸려고 불필요한 문장을 늘리지는 않는다.
- 강조(strong=true)는 꼭 기억할 금액·기한·예외에만, 소제목당 최대 2곳.
- 이모지, 특수 기호 장식, 느낌표 남발, 마크다운(**, #, -) 금지(강조는 strong으로만).
- 상투 표현 금지: "오늘은 ~에 대해 알아보겠습니다", "~에 대해 자세히 알아볼까요", "결론적으로",
  "마무리하며", "도움이 되셨길 바랍니다", "꿀팁", "총정리", "한눈에".
- 과장·낚시 금지("무조건", "100%", "모르면 손해"). 광고 문구처럼 쓰지 않는다.

[쇼츠 대본 규칙]
- 블로그와 같은 사실만 쓴다. 금액·날짜·비율은 아라비아 숫자로 쓴다(예: '4,400만 원', '12월 1일', '50%'). 자막에 그대로 보인다.
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
    previous: str | None = None,
) -> str:
    """사용자 지시문을 만든다. feedback은 이전 결과가 검사에 걸렸을 때 고칠 점.

    news: (날짜, 제목) 목록. 변경 신호로만 쓴다.
    previous: 검사에 걸린 이전 결과 JSON. 있으면 새로 쓰지 않고 고칠 점만 고치게 한다.
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
        f"블로그 본문은 소제목 4~6개. 도입 문단과 소제목의 문단·주의(note)·요약(takeaway) 글자만 합쳐"
        f" 공백 포함 {BODY_TARGET_CHARS[0]:,}~{BODY_TARGET_CHARS[1]:,}자로 쓴다. 표·체크리스트·FAQ·마무리는 이 글자 수에"
        f" 들어가지 않으므로, 표가 있는 소제목도 문단으로 구분법·예외·다음 행동을 설명한다"
        f" (권장: 소제목마다 문단·주의·요약 합계 약 {SECTION_TARGET_CHARS[0]}~{SECTION_TARGET_CHARS[1]}자).",
        "아래 [자료]만 근거로 마스터 콘텐츠 JSON을 만든다. 자료 밖 지식으로 숫자나 기준을 채우지 않는다.",
        "[자료]",
        "\n\n".join(blocks),
    ]
    if news:
        lines = "\n".join(f"- {d} {t}" for d, t in news)
        parts.append(f"[최근 뉴스 - 변경 신호, 사실 근거 아님]\n{lines}")
    if feedback:
        parts.append(f"[이전 결과에서 고칠 점]\n{feedback}")
    if previous:
        parts.append(
            "[이전 결과 - 이것을 고쳐서 돌려준다]\n"
            "처음부터 새로 쓰지 않는다. 위 [이전 결과에서 고칠 점]에 적힌 곳만 고치고, 나머지 제목·문장·표·구성은 "
            "그대로 둔다. 단, 사실 규칙이 이 지시보다 우선한다. 고치는 중에 [자료]로 확인되지 않는 숫자·조건이 "
            "보이면 그대로 두지 말고 자료에 맞게 고치거나 빼고 unverified에 적는다. 내용을 보강할 때도 [자료]에 있는 "
            "사실만 쓴다. 고친 결과는 빠짐없이 전체 JSON으로 돌려준다.\n"
            + previous
        )
    return "\n\n".join(parts)


SHORTS_SYSTEM_INSTRUCTION = """\
너는 한국 생활 정보 쇼츠 대본 작가다. 주어진 [블로그 글]에 있는 사실만 써서 성우가 읽을 대본을 고친다.
금액·날짜·비율은 아라비아 숫자로 쓰고(예: 4,400만 원, 12월 1일), 구어체 존댓말, 이모지·마크다운 금지.
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
