"""최근 기사 속 '올해 기준' 금액 확인: 공식 안내 페이지가 지난 금액을 고치지 않았을 때 대비.

- 예: 고용보험 안내 페이지는 2026년에도 실업급여 하한액을 2019년 값(6만120원)으로 보여 줬지만, 기사들은
  '2026년 기준 구직급여 하한액은 하루 6만6048원'이라고 썼다. 이런 값은 '바뀐다'는 기사가 아니라 현재값 문장으로만
  나와서 최근 변경 확인(recent_change)으로는 못 잡는다(2026-11-24 작업 54).
- AI를 쓰지 않는다. 기사 본문에서 '{올해}년 기준'·'올해 기준'과 금액이 함께 든 문장을 그대로 옮긴다.
- 잘못 쓴 기사 하나에 휘둘리지 않게, 문장 속 모든 금액이 서로 다른 언론사 2곳 이상의 그런 문장에 똑같이 나와야 한다.
- 추진·검토·~하면·내년 연도 같은 말이 든 문장은 버린다(개편안 금액이 현행처럼 들어가지 않게).
- 덧붙이는 조사라 어떤 실패도 글 작성을 멈추지 않는다.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Sequence
from urllib.parse import urlparse

from src.ai_generator import _fact_norm
from src.recent_change import _CONDITIONAL, _HEDGE, _SENTENCE, _published
from src.source_fetcher import Source
from src.source_finder import core_keyword

log = logging.getLogger(__name__)

MAX_ARTICLES = 4        # 읽을 기사 수(언론사가 겹치지 않게)
MAX_CANDIDATES = 10
MAX_LINES = 8           # 자료에 넣을 문장 수
MAX_SENTENCE = 220      # 이보다 긴 '문장'은 문장 나누기가 실패한 덩어리로 본다
RECENT_DAYS = 300
MIN_HOSTS = 2           # 같은 금액을 적은 서로 다른 언론사 수
PORTALS = {"naver.com", "daum.net", "nate.com", "msn.com", "google.com"}   # 재전송이라 언론사로 세지 않음

# '6만6048원', '66,048원', '198만 1440원', '1억 2000만원', '6만6천원', '215만원'
_MONEY = re.compile(r"(?<![\d,.])(?:(\d[\d,]*)\s*억\s*)?(?:(\d[\d,]*)\s*만\s*)?(?:(\d[\d,]*)\s*천\s*)?(\d[\d,]*)?\s*원")
_LEFTOVER = re.compile(r"\d\s*(?:억|만|천)")      # 못 읽은 금액 꼴이 남으면 그 문장은 쓰지 않는다
# 앞으로 바뀌는 금액('12월부터 7만원으로 오른다')은 현재값이 아니다
_FUTURE = re.compile(r"부터|오른다|오를|올린다|인상(?:된다|될|한다|할)|인하(?:된다|될|한다|할)|바뀐다|바뀔|달라진다|달라질"
                     r"|늘어난다|늘어날|줄어든다|줄어들|변경(?:된다|될)|적용(?:된다|될)|예상")
_IF = re.compile(r"다면|라면|(?:으|하|되|지|이)면(?=[\s,]|$)")
# 앞 문장이 개편안·가정 계산·논의 중인 수치를 소개하면 다음 문장의 '올해 기준' 금액은 현행이 아닐 수 있다.
# '경우'·'~라면서도'·'동안'·'(실직자) 가정' 같은 평범한 말에는 걸리지 않게 구체적인 꼴만 본다.
_SETUP = re.compile(r"(?:개편|인상|인하|개정|조정|수정|정부|법률|시행령|대체|절충|방)안|가정(?:하|한|해|할|치)"
                    r"|시뮬레이션|추산|추정|계산\s*결과|만약|산출|논의|검토|추진|쟁점|합의|제안|거론")
# '지급률을 높이면 다음과 같다'·'높이면 얼마나 받을까?' 처럼 조건을 달고 뒷문장을 소개하는 말
_INTRO = re.compile(r"다음|아래|이렇|이와\s*같|얼마|어떻게|\?")
_PARTICLE = re.compile(r"(?:으로는|로는|으로|에서|은|는|이|가|을|를|도|의|로|에)$")
# 금액 바로 앞의 이런 말은 항목 이름이 아니다('하한액은 하루 6만6048원'의 '하루')
_FILLER = {"하루", "일", "월", "연", "연간", "약", "최대", "최소", "기준", "수준", "총", "한", "달", "매월", "매달",
           "인당", "각각", "모두", "올해", "현재", "이", "그"}


def _parse(m: re.Match) -> int:
    eok, man, cheon, rest = (int(g.replace(",", "")) if g and g.replace(",", "") else 0 for g in m.groups())
    return eok * 100_000_000 + man * 10_000 + cheon * 1_000 + rest


def amounts(text: str) -> list[int]:
    return [n for m in _MONEY.finditer(text) if any(m.groups()) and (n := _parse(m)) >= 1_000]


def facts(sentence: str) -> list[tuple[str | None, int]] | None:
    """문장 속 (항목 이름, 금액). 항목 이름은 금액 바로 앞의 낱말('상한액은 6만8100원' → 상한액), 없으면 None.
    읽지 못한 금액 꼴이 남으면 None(그 문장은 비교할 수 없음)."""
    out, prev, rest = [], 0, []
    for m in _MONEY.finditer(sentence):
        if not any(m.groups()):
            continue
        rest.append(sentence[prev:m.start()])
        words = [_PARTICLE.sub("", w) for w in re.findall(r"[가-힣]+", sentence[prev:m.start()])]
        label = next((w for w in reversed(words) if len(w) >= 2 and w not in _FILLER), None)
        n = _parse(m)
        if n >= 1_000:
            out.append((label, n))
        prev = m.end()
    rest.append(sentence[prev:])
    if _LEFTOVER.search(" ".join(rest)):
        return None
    return out


@dataclass
class CurrentValues:
    sources: list[Source] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    memo: dict = field(default_factory=dict)


def _host(url: str) -> str:
    """언론사 단위 주소(m.press.co.kr → press.co.kr). 모바일·하위 주소를 다른 언론사로 세지 않게."""
    parts = (urlparse(url).hostname or "").lower().split(".")
    keep = 3 if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"co", "or", "go", "ne", "re", "ac", "pe",
                                                                             "com", "net", "org"} else 2
    return ".".join(parts[-keep:])


def _bad(text: str, year: int) -> bool:
    return bool(re.search(rf"{year + 1}\s*년|내년", text) or _CONDITIONAL.search(text) or _HEDGE.search(text)
                or _IF.search(text))


def _setup(prev: str) -> bool:
    return bool(_CONDITIONAL.search(prev) or _SETUP.search(prev) or (_IF.search(prev) and _INTRO.search(prev)))


def value_sentences(text: str, year: int, words: Sequence[str] = ()) -> list[str]:
    """올해 기준 현재 금액 문장. 제도 이름이 문장에 있어야 하고, 내년·조건·논의·앞으로 바뀜 표현이 문장이나
    바로 앞 문장에 있으면 뺀다('다음은 개편안 계산이다. 2026년 기준 하한액은 7만원이다.')."""
    basis = re.compile(rf"{year}\s*년\s*기준|올해\s*기준")
    parts = [x.strip() for x in _SENTENCE.split(text or "") if x.strip()]
    out = []
    for i, s in enumerate(parts):
        if (len(s) <= MAX_SENTENCE and basis.search(s) and amounts(s)
                and all(w in _fact_norm(s) for w in words)
                and not _bad(s, year) and not _SETUP.search(s) and not _FUTURE.search(s) and not (i and _setup(parts[i - 1]))
                and facts(s) is not None):
            out.append(s)
    return out


def _pick(search: Any, topic: str, today: date) -> list[tuple[Any, Any]]:
    """올해 기사 중 요약에 '올해 기준'이 있는 것 → 제목에 제도 이름이 있는 것 순. 정확도순 검색(몇 달 전 기사도 나오게)."""
    core = core_keyword(topic)
    words = [_fact_norm(w) for w in core.split() if _fact_norm(w)]
    basis = re.compile(rf"{today.year}\s*년\s*기준|올해\s*기준")
    found: dict[str, tuple[Any, Any, bool, bool]] = {}
    for q in (f"{core} {today.year}년 기준", f"{today.year}년 {core} 금액"):
        try:
            hits = search.news(q, display=30, sort="sim")
        except Exception as exc:  # noqa: BLE001 - 검색 하나가 안 돼도 나머지로 진행
            log.warning("현재 금액 뉴스 검색 실패(%s): %s", q, exc)
            continue
        for h in hits:
            when = _published(h)
            if (when is None or when.date().year != today.year or when.date() < today - timedelta(days=RECENT_DAYS)
                    or h.url in found):
                continue
            has_basis = bool(basis.search(h.description))
            in_title = bool(words) and all(w in _fact_norm(h.title) for w in words)
            in_desc = bool(words) and all(w in _fact_norm(h.description) for w in words)
            if (has_basis and (in_title or in_desc)) or in_title:
                found[h.url] = (h, when, has_basis, in_title)
    ranked = sorted(found.values(), key=lambda x: (not x[2], not x[3], -x[1].timestamp()))
    return [(h, w) for h, w, _, _ in ranked[:MAX_CANDIDATES]]


def find_current_values(search: Any, fetch: Callable, topic: str, first_number: int,
                        skip_urls: Sequence[str] = (), today: date | None = None) -> CurrentValues:
    """서로 다른 언론사 2곳 이상이 같은 금액으로 적은 '올해 기준' 문장을 자료 하나로 돌려준다."""
    today = today or date.today()
    out = CurrentValues(memo={"articles": [], "lines": []})
    words = [_fact_norm(w) for w in core_keyword(topic).split() if _fact_norm(w)]
    try:
        picked = [(h, w) for h, w in _pick(search, topic, today) if h.url not in set(skip_urls)]
    except Exception as exc:  # noqa: BLE001
        log.warning("현재 금액 기사 고르기 실패: %r", exc)
        return out
    per_host: dict[str, tuple[dict, list[str]]] = {}
    for h, when in picked:
        if len(per_host) >= MAX_ARTICLES:
            break
        host = _host(h.url)
        if host in per_host or host in PORTALS or _bad(h.title, today.year):
            continue
        try:
            got, _ = fetch([h.url], max_sources=1)
        except Exception as exc:  # noqa: BLE001
            log.warning("현재 금액 기사 읽기 실패: %r", exc)
            continue
        if not got:
            continue
        host = _host(got[0].url)          # 넘어간 주소 기준으로 언론사를 센다
        if host in per_host or host in PORTALS:
            continue
        info = {"url": got[0].url, "title": h.title, "date": when.date().isoformat(), "host": host}
        per_host[host] = (info, value_sentences(got[0].text, today.year, words))
        out.memo["articles"].append(info)
    # 항목 이름이 있는 금액은 (이름, 금액)이 다른 언론사 문장에도 같아야 하고, 이름 없는 금액은 금액만 같아도 된다
    labeled: dict[tuple[str, int], set[str]] = {}
    plain: dict[int, set[str]] = {}
    for host, (_, sents) in per_host.items():
        for s in sents:
            for label, a in facts(s) or []:
                plain.setdefault(a, set()).add(host)
                if label:
                    labeled.setdefault((label, a), set()).add(host)

    def agreed(label: str | None, a: int) -> bool:
        return len(labeled.get((label, a), ()) if label else plain.get(a, ())) >= MIN_HOSTS

    lines: list[tuple[str, dict]] = []
    used: set[str] = set()
    for host, (info, sents) in per_host.items():
        for s in sents:
            key, fs = _fact_norm(s), facts(s) or []
            if key in used or not fs or not all(agreed(lb, a) for lb, a in fs):
                continue
            used.add(key)
            lines.append((s, info))
    lines = lines[:MAX_LINES]
    if not lines:
        return out
    hosts = sorted({h for s, _ in lines for _, a in facts(s) or [] for h in plain.get(a, ())})
    text = [f"서로 다른 언론사 {len(hosts)}곳({', '.join(hosts)})이 같은 금액으로 적은 {today.year}년 기준 문장만 그대로 옮김"]
    for s, info in lines:
        text.append(f"- {s} ({info['date']} 기사, {info['title']})")
    first = lines[0][1]
    out.sources.append(Source(f"S{first_number}", first["url"], f"최근 기사 속 {today.year}년 기준 금액",
                              "\n".join(text), "current"))
    out.memo["lines"] = [{"sentence": s, **i} for s, i in lines]
    out.notes.append(f"최근 기사 {today.year}년 기준 금액 반영({len(hosts)}곳 일치): {lines[0][0][:60]}…")
    return out
