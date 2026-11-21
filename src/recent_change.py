"""최근 변경(개편안) 찾기: 글을 쓰기 전에 뉴스·정책브리핑에서 그 제도의 최근 변경 소식을 읽는다.

- 공식 안내 페이지는 법이 바뀐 뒤에도 늦게 고쳐지므로, 이미 확정된 변경(국회 통과·공포·시행)을 글에 반영하려는 것.
- 기사 본문에서 AI가 '무엇이 바뀌는지' 문장과 '확정됐다는' 문장을 그대로 인용하고, 프로그램이 다시 확인한다:
  인용이 그 기사에 실제로 있는지, 확정 근거 문장에 '통과했다·공포됐다·시행 중' 같은 말이 있고
  '통과하면·예정·추진·검토' 같은 말이 없는지. 하나라도 어긋나면 논의 중으로 본다.
- 확정된 변경만 인용문 그대로 자료(Source.via="news")로 만들어 글쓰기·사실 확인에 넘긴다(AI가 쓴 요약은 넣지 않음).
  논의 중인 것은 글에 쓰지 않고 승인 알림에 참고로만 보인다.
- 덧붙이는 조사라 어떤 실패도 글 작성을 멈추지 않는다.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Sequence
from urllib.parse import urlparse

from src.ai_generator import _fact_norm, _quoted_in_sources
from src.source_fetcher import Source

log = logging.getLogger(__name__)

MAX_ARTICLES = 3            # 읽을 기사 수
MAX_CANDIDATES = 8          # 읽기 실패에 대비한 후보 수
MAX_NOTES = 4               # 승인 알림에 보일 줄 수
RECENT_DAYS = 365           # 이보다 오래된 기사는 안내 페이지에 이미 반영됐을 가능성이 높다
QUERY_WORDS = ("개편", "개정", "달라지는")

# 주제 이름에서 제도 이름 뒤에 붙는 일반 낱말(여기서 끊는다)
_GENERIC = re.compile(r"^(신청|대상|지원|자격|방법|발급|환급|계산|조건|가입|사용|신규|요건|기간|기한|금액|안내|정리|총정리|혜택)")
# 확정 근거: 국회·국무회의를 통과했다, 공포·고시됐다(최저임금·기준중위소득은 고시로 확정), 이미 시행 중
_FINAL = re.compile(
    r"(본회의|국회)[^.\n]{0,15}(통과|의결|가결)(했|됐|되었|된|돼|하면서|함에)"
    r"|(공포|고시)(했|됐|되었|된|돼)"
    r"|국무회의[^.\n]{0,15}(통과|의결)(했|됐|되었|된|돼)"
    r"|시행(됐|되었|되고 있|하고 있|\s*중)"
)
# 조건·계획·중간 단계 표현이 있으면 확정으로 보지 않는다(예: '국회를 통과하면', '시행할 예정',
# '상임위를 통과했다', '국무회의에서 의결돼 국회에 제출했다', '통과돼야')
_CONDITIONAL = re.compile(
    r"통과하면|통과될 경우|의결되면|확정되면|된다면|했다면|돼야|되어야|예정|전망|추진|검토|계획|방침|입법예고"
    r"|상임위|소위|위원회|제출|발의|심의|상정|정부안"
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")

Reader = Callable[[str, Sequence[Source], dict], Sequence[Any]]


@dataclass
class RecentChanges:
    sources: list[Source] = field(default_factory=list)     # 확정된 변경(글쓰기 자료)
    notes: list[str] = field(default_factory=list)          # 승인 알림 참고 줄
    errors: list[str] = field(default_factory=list)
    memo: dict = field(default_factory=dict)                # 체크포인트 기록용

    def as_dict(self) -> dict:
        return self.memo


def core_keyword(topic: str) -> str:
    """'월세 세액공제 신청 대상과 …' → '월세 세액공제'. 검색어와 기사 관련성 판단에 쓴다."""
    core: list[str] = []
    for w in topic.split():
        if core and _GENERIC.match(w):
            break
        if len(w) > 2 and w[-1] in "과와":          # '근로장려금과 자녀장려금' → 앞 제도만
            core.append(w[:-1])
            break
        core.append(w)
        if len(core) >= 3:
            break
    return " ".join(core) or topic.strip()


def is_final(proof: str) -> bool:
    return bool(_FINAL.search(proof)) and not _CONDITIONAL.search(proof)


def _whole_sentences(text: str, quote: str) -> list[str]:
    """인용 조각이 이어진 그대로 들어 있는 기사 문장 전체. AI가 '통과했다면 … 예정'에서 '통과했다'만
    잘라 와도 문장 전체로 다시 판단하려는 것. 이어진 그대로 없으면 빈 목록."""
    q = _fact_norm(quote)
    if len(q) < 6:
        return []
    return [s.strip() for s in _SENTENCE.split(text) if q in _fact_norm(s)]


def _published(hit: Any) -> datetime | None:
    try:
        return parsedate_to_datetime(hit.published)
    except (TypeError, ValueError, IndexError):
        return None


def pick_articles(search: Any, topic: str, today: date | None = None) -> list[tuple[Any, datetime]]:
    """제도 이름이 제목·요약에 들어 있는 최근 기사. 정책브리핑(korea.kr) → 제목에 제도 이름 → 최신순."""
    today = today or date.today()
    core = core_keyword(topic)
    words = [_fact_norm(w) for w in core.split() if _fact_norm(w)]
    found: dict[str, tuple[Any, datetime]] = {}
    for q in (f"{core} {k}" for k in QUERY_WORDS):
        try:
            hits = search.news(q, display=20)
        except Exception as exc:  # noqa: BLE001 - 검색 하나가 안 돼도 나머지로 진행
            log.warning("최근 변경 뉴스 검색 실패(%s): %s", q, exc)
            continue
        for h in hits:
            when = _published(h)
            if when is None or when.date() < today - timedelta(days=RECENT_DAYS) or h.url in found:
                continue
            text = _fact_norm(f"{h.title} {h.description}")
            if words and all(w in text for w in words):
                found[h.url] = (h, when)

    def rank(item: tuple[Any, datetime]):
        host = (urlparse(item[0].url).hostname or "").lower()
        in_title = all(w in _fact_norm(item[0].title) for w in words)
        return (0 if host.endswith("korea.kr") else 1, 0 if in_title else 1, -item[1].timestamp())
    return sorted(found.values(), key=rank)[:MAX_CANDIDATES]


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n] + "…"


def find_recent_changes(search: Any, fetch: Callable, read: Reader, topic: str, first_number: int,
                        skip_urls: Sequence[str] = (), today: date | None = None) -> RecentChanges:
    """기사를 읽어 확정된 변경만 자료로 돌려준다. first_number: 새 자료 id 번호(S{n}) 시작값."""
    out = RecentChanges(memo={"articles": [], "confirmed": [], "pending": []})
    try:
        skip = set(skip_urls)
        picked = [(h, w) for h, w in pick_articles(search, topic, today) if h.url not in skip]
    except Exception as exc:  # noqa: BLE001
        log.warning("최근 변경 기사 고르기 실패: %r", exc)
        return out
    # 한 주소씩 읽어야 넘어간 주소(리다이렉트)가 생겨도 어느 기사인지(날짜·제목) 알 수 있다
    info: dict[str, dict] = {}
    articles: list[Source] = []
    fetch_errors: list[str] = []
    for h, when in picked:
        if len(articles) >= MAX_ARTICLES:
            break
        try:
            got, errs = fetch([h.url], max_sources=1)
        except Exception as exc:  # noqa: BLE001
            fetch_errors.append(f"{exc.__class__.__name__}: {h.url}")
            continue
        fetch_errors += list(errs)
        if not got or got[0].url in skip:      # 넘어간 주소가 이미 읽은 공식 자료·기사면 건너뜀
            continue
        skip.add(got[0].url)
        sid = f"N{len(articles) + 1}"      # 기사 id는 N1.. (글 자료 S1..과 헷갈리지 않게)
        articles.append(Source(sid, got[0].url, h.title, got[0].text, "news"))
        info[sid] = {"url": got[0].url, "title": h.title, "date": when.date().isoformat()}
    out.memo["articles"] = list(info.values())
    out.memo["fetch_errors"] = fetch_errors[:3]
    if not articles:
        return out
    try:
        items = list(read(topic, articles, {k: v["date"] for k, v in info.items()}))
    except Exception as exc:  # noqa: BLE001 - AI가 안 되면 최근 변경 없이 진행
        log.warning("최근 변경 판정 실패: %s", exc)
        out.errors.append(f"최근 변경 판정 실패: {exc.__class__.__name__}")
        return out
    raw = {s.id: s.text for s in articles}
    texts = {s.id: _fact_norm(s.text) for s in articles}
    confirmed: dict[str, list[tuple[str, str]]] = {}
    for it in items:
        sid = (getattr(it, "source_id", "") or "").strip()
        change = (getattr(it, "change", "") or "").strip()
        proof = (getattr(it, "proof", "") or "").strip()
        if sid not in texts or not _quoted_in_sources(change, [texts[sid]]):
            continue        # 기사에 없는 문장(AI가 지어낸 요약)은 버린다
        # 확정 근거는 기사 문장 전체로 다시 보고, 변경 문장 자체에도 '검토·예정' 같은 말이 없어야 한다
        whole = _whole_sentences(raw[sid], proof) if proof else []
        ok = (getattr(it, "status", "") == "confirmed" and bool(whole) and all(is_final(w) for w in whole)
              and not _CONDITIONAL.search(change))
        if ok:
            proof = whole[0]
        entry = {"change": change, "proof": proof if ok else "", **info[sid]}
        if ok:
            confirmed.setdefault(sid, []).append((change, proof))
            out.memo["confirmed"].append(entry)
        else:
            out.memo["pending"].append(entry)
    n = first_number
    for sid, pairs in confirmed.items():
        lines = [f"기사 날짜: {info[sid]['date']} (언론 보도에서 확정된 변경 문장만 그대로 옮김)"]
        for change, proof in dict.fromkeys(pairs):
            lines += [f"- 변경: {change}", f"  확정 근거: {proof}"]
        out.sources.append(Source(f"S{n}", info[sid]["url"], info[sid]["title"], "\n".join(lines), "news"))
        n += 1
    for e in out.memo["confirmed"]:
        out.notes.append(f"최근 변경(확정) 반영: {_clip(e['change'], 70)} — {e['date']} 기사")
    noted: set[str] = set()
    for e in out.memo["pending"]:          # 논의 중은 참고라 기사마다 한 줄만
        if e["url"] in noted:
            continue
        noted.add(e["url"])
        out.notes.append(f"최근 변경(논의 중, 글에 안 씀): {_clip(e['change'], 70)} — {e['date']} 기사")
    out.notes = out.notes[:MAX_NOTES]
    return out
