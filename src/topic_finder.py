"""블로그 주제 후보를 찾는다 (네이버 검색 응답 + Gemini 짧은 호출 2번).

좋은 주제 = 사람들이 찾는다 + 지금 쓸 이유 + 공식 근거 + 아직 안 씀.

1. 후보 모으기: 기본 제도 목록(templates/topic_seeds.txt) + 최근 뉴스 제목에서 Gemini가 뽑은 새 제도
2. 걸러내기: 이미 쓴 글 장부와 비교(제도명 + 기준 연도). 공식 자료가 검색되지 않으면 제외
   - 안 쓴 제도 → 새 주제
   - 쓴 연도보다 새 연도 자료가 공식 사이트에 보이면 → 새 연도 글
   - 같은 연도인데 글 쓴 뒤 뉴스에 변경 소식 → 기존 글 고치기(확인 필요)
3. 순위: 검색어트렌드 관심도(기준어 대비) + 제철 + 새 제도 가점, 분야별 상한
4. 주제 문장: Gemini가 다듬고, 검사에 떨어지면 정해진 틀로 만든다

본문 크롤링·페이지 읽기는 하지 않는다(서버 부하·비용). 판단은 검색 결과 제목·요약만 쓴다.
검색 결과만으로는 마감·변경을 확정할 수 없어 '확인 필요'로만 표시한다.
"""
from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Sequence
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from src.config import ROOT
from src.source_finder import NaverSearch, SearchError, SearchHit, official_candidates

log = logging.getLogger(__name__)

SEEDS_PATH = ROOT / "templates" / "topic_seeds.txt"
CATEGORIES = ("지원금", "세금", "절약")
ANCHOR_QUERY = "자녀장려금"          # 관심도 비교 기준어(=100)
TREND_GROUP = 4                       # 검색어트렌드는 한 번에 5개까지: 후보 4 + 기준어 1
DEFAULT_LIMIT = 10
CATEGORY_CAP = 7
MAX_UPDATES = 3                       # 기존 글 고치기 후보 상한
MAX_DISCOVERED = 15
NEWS_DAYS = 21                        # 새 제도 찾기에 쓰는 뉴스 기간
DISCOVERY_QUERIES = (
    "지원금 신청 시작", "신청 접수 시작 지원", "새로 생긴 지원 제도", "지원 대상 확대 신청",
    "세액공제 확대", "감면 신청 접수", "바우처 신청 시작",
)
CHANGE_WORDS = ("개편", "변경", "인상", "확대", "신설", "개정", "상향", "완화", "축소", "달라진", "바뀐", "바뀌")
END_WORDS = ("종료", "폐지", "중단", "마감")
# 주장·검토 단계 기사(실제 변경 아님)
TALK_WORDS = ("필요", "검토", "주장", "촉구", "논의", "발의", "요구", "저울질", "만지작", "가능성", "?", "추진")
MIN_CHANGE_NEWS = 2
# 지역 사업 기사(같은 이름의 지자체 사업은 전국 제도 변경 근거가 아님)
REGIONS = ("서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원", "충북", "충남",
           "전북", "전남", "경북", "경남", "제주")
_LOCAL_HEAD = re.compile(r"^\s*[\[(]?[가-힣]{1,8}(시|군|구|도)(청)?[\],)·\s]")


def is_local_news(title: str) -> bool:
    t = title.strip()
    return bool(_LOCAL_HEAD.match(t)) or any(t.startswith(r) for r in REGIONS)

Kind = Literal["new", "new_year", "update"]
KIND_KO = {"new": "새 주제", "new_year": "새 연도 글", "update": "기존 글 고치기"}


def norm(text: str) -> str:
    """비교용: 공백·가운뎃점·하이픈을 빼고 소문자."""
    return re.sub(r"[\s·ㆍ\-_/]", "", text or "").lower()


@dataclass(frozen=True)
class Seed:
    name: str
    query: str
    months: tuple[int, ...] = ()
    category: str = "지원금"
    aliases: tuple[str, ...] = ()
    discovered: bool = False          # 뉴스에서 새로 찾은 제도
    news_titles: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)

    def matches(self, text: str) -> bool:
        t = norm(text)
        return any(norm(n) and norm(n) in t for n in self.names)


def load_seeds(path: Path = SEEDS_PATH) -> list[Seed]:
    seeds: list[Seed] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        parts += [""] * (5 - len(parts))
        name, query, months, category, aliases = parts[:5]
        if not name or not query or category not in CATEGORIES:
            raise ValueError(f"{path.name} {lineno}번째 줄 형식이 틀렸습니다: {raw}")
        try:
            month_list = tuple(int(m) for m in months.split(",") if m.strip())
        except ValueError as exc:
            raise ValueError(f"{path.name} {lineno}번째 줄 달 형식이 틀렸습니다: {months}") from exc
        if any(not 1 <= m <= 12 for m in month_list):
            raise ValueError(f"{path.name} {lineno}번째 줄 달은 1~12: {months}")
        seeds.append(Seed(name, query, month_list, category,
                          tuple(a.strip() for a in aliases.split(",") if a.strip())))
    return seeds


@dataclass(frozen=True)
class Written:
    """이미 쓴(또는 쓰는 중인) 글 한 편."""
    name: str
    year: int
    job_id: int
    post_id: int | None
    written_on: date
    status: str = ""


_YEAR = re.compile(r"(20\d\d)년")


def written_from_jobs(jobs: Iterable[dict[str, Any]], seeds: Sequence[Seed]) -> list[Written]:
    """작업 장부에서 제도별로 쓴 글을 뽑는다(취소된 작업 제외). 연도는 주제의 'YYYY년', 없으면 등록 연도."""
    out: list[Written] = []
    for job in jobs:
        if job.get("status") == "canceled":
            continue
        topic = job.get("topic") or ""
        created = _parse_date(job.get("created_at")) or date.today()
        m = _YEAR.search(topic)
        year = int(m.group(1)) if m else created.year
        for seed in seeds:
            if seed.matches(topic):
                out.append(Written(seed.name, year, int(job["id"]), job.get("post_id"), created,
                                   job.get("status") or ""))
    return out


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def _news_date(hit: SearchHit) -> date | None:
    try:
        return parsedate_to_datetime(hit.published).date()
    except (TypeError, ValueError):
        return None


@dataclass
class Candidate:
    seed: Seed
    kind: Kind
    year: int
    official_urls: list[str] = field(default_factory=list)
    interest: float | None = None      # 기준어(자녀장려금 평균) = 100
    in_season: bool = False            # 목록에 적힌 신청·납부 달(이번 달·다음 달)
    trend_season: bool = False         # 작년 이맘때 검색이 평소보다 30% 이상 많았음
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    news_titles: list[str] = field(default_factory=list)
    written: Written | None = None     # 새 연도 글·고치기일 때 원래 글
    topic: str = ""
    score: float = 0.0

    @property
    def key(self) -> str:
        return f"{norm(self.seed.name)}:{self.year}:{self.kind}"

    @property
    def official_hosts(self) -> list[str]:
        hosts: list[str] = []
        for u in self.official_urls:
            h = (urlparse(u).hostname or "").removeprefix("www.")
            if h and h not in hosts:
                hosts.append(h)
        return hosts


@dataclass
class FindResult:
    candidates: list[Candidate]
    notes: list[str]                   # 사람에게 보여 줄 진행 안내(부족 이유 등)
    api_calls: int = 0


# ---- Gemini (짧은 호출 2번) ----

class Discovered(BaseModel):
    ids: list[int] = Field(description="근거가 된 뉴스 제목 번호")
    name: str = Field(description="제도 정식 이름(뉴스 제목에 나온 그대로)")
    query: str = Field(description="사람들이 검색할 짧은 말(15자 이내)")
    category: Literal["지원금", "세금", "절약"]
    national: bool = Field(description="전국 대상 제도면 true, 특정 시·군·구만이면 false")


class DiscoveredList(BaseModel):
    items: list[Discovered]


class Phrased(BaseModel):
    key: str
    topic: str


class PhrasedList(BaseModel):
    items: list[Phrased]


DISCOVER_SYSTEM = (
    "너는 한국 생활 돈 정보 블로그(지원금·세금·절약 제도)의 편집자다. "
    "주어진 뉴스 제목에 실제로 나온 제도만 고른다. 제목에 없는 제도명·금액·날짜를 지어내지 않는다."
)
PHRASE_SYSTEM = (
    "너는 한국 생활 돈 정보 블로그의 편집자다. 검색하는 사람이 바로 알아볼 블로그 주제 문장을 만든다. "
    "주어진 정보에 없는 금액·날짜·숫자는 넣지 않는다(연도는 주어진 것만). 과장·낚시 표현, 이모지는 쓰지 않는다."
)


def build_discover_prompt(titles: Sequence[str], known: Sequence[str]) -> str:
    lines = "\n".join(f"{i}. {t}" for i, t in enumerate(titles))
    return (
        "아래 최근 뉴스 제목에서, 일반 국민이 신청하거나 혜택을 받는 전국 단위 '지원금·세금 공제·요금 할인' 제도를 "
        f"최대 {MAX_DISCOVERED}개 골라라.\n"
        "- 이미 아는 제도 목록에 있는 것은 빼라.\n"
        "- 기업 대상, 특정 지자체 한 곳만의 사업, 정치·사건 기사, 상품 광고는 빼라.\n"
        "- name은 뉴스 제목에 나온 표기 그대로, ids는 그 제도가 나온 제목 번호.\n\n"
        f"이미 아는 제도: {', '.join(known)}\n\n뉴스 제목:\n{lines}"
    )


def build_phrase_prompt(items: Sequence[Candidate]) -> str:
    rows = []
    for c in items:
        extra = f" / 참고 뉴스: {' | '.join(c.news_titles[:3])}" if c.news_titles else ""
        rows.append(f"- key={c.key} / 제도={c.seed.name} / 기준 연도={c.year} / 종류={KIND_KO[c.kind]} / "
                    f"분야={c.seed.category} / 이유={', '.join(c.reasons) or '-'}{extra}")
    return (
        "각 항목마다 블로그 글 주제 문장을 하나씩 만들어라.\n"
        "- 15~40자. 제도 이름을 그대로 넣고, 독자가 궁금한 것(대상·금액·신청 방법·기한·계산법 중 1~2개)을 붙인다.\n"
        "- 예: '고향사랑기부제 연말 세액공제 한도와 답례품 받는 방법', '2026년 자녀장려금 신청 자격과 지급액'\n"
        "- 연도는 꼭 필요할 때만 넣고, 넣는다면 기준 연도만. 신청이 다음 해에 열리는 제도에는 연도를 넣지 않는다.\n"
        "- 그 밖의 숫자·금액·날짜는 넣지 않는다.\n"
        "- key는 그대로 돌려준다.\n\n" + "\n".join(rows)
    )


def fallback_topic(c: Candidate) -> str:
    prefix = f"{c.year}년 " if c.kind == "new_year" else ""
    if c.seed.category == "세금":
        return f"{prefix}{c.seed.name} 대상과 신청 방법 정리"
    return f"{prefix}{c.seed.name} 신청 자격과 받는 방법"


def valid_topic(text: str, c: Candidate) -> bool:
    t = (text or "").strip()
    if not 12 <= len(t) <= 45:
        return False
    if not c.seed.matches(t):
        return False
    numbers = re.findall(r"\d+", t)
    return all(n == str(c.year) for n in numbers)


def validate_discovered(items: Sequence[Discovered], titles: Sequence[str],
                        seeds: Sequence[Seed]) -> list[Seed]:
    """제목에 실제로 나온 전국 제도만, 기본 목록과 겹치지 않게."""
    out: list[Seed] = []
    for d in items:
        name, query = d.name.strip(), d.query.strip()
        refs = [titles[i] for i in d.ids if 0 <= i < len(titles)]
        if not d.national or not name or not query or len(query) > 20 or len(name) > 30:
            continue
        if not refs or not any(norm(name) in norm(t) for t in refs):
            continue                                   # 제목에 없는 제도명(지어냄)
        if any(s.matches(name) or norm(s.name) in norm(name) or norm(name) in norm(s.name)
               for s in [*seeds, *out]):
            continue
        out.append(Seed(name, query, (), d.category, (), discovered=True, news_titles=tuple(refs[:3])))
        if len(out) >= MAX_DISCOVERED:
            break
    return out


class TopicWriter:
    """Gemini 호출부. ContentGenerator의 재시도·모델 순서를 그대로 쓴다."""

    def __init__(self, generator: Any) -> None:
        self.gen = generator

    def discover(self, titles: Sequence[str], seeds: Sequence[Seed]) -> list[Seed]:
        if not titles:
            return []
        raw, _model = self.gen._call_with_retry(build_discover_prompt(titles, [s.name for s in seeds]),
                                                schema=DiscoveredList, system=DISCOVER_SYSTEM)
        return validate_discovered(DiscoveredList.model_validate_json(raw).items, titles, seeds)

    def phrase(self, items: Sequence[Candidate]) -> dict[str, str]:
        if not items:
            return {}
        raw, _model = self.gen._call_with_retry(build_phrase_prompt(items), schema=PhrasedList,
                                                system=PHRASE_SYSTEM)
        return {p.key: p.topic.strip() for p in PhrasedList.model_validate_json(raw).items}


# ---- 검색 ----

class _Budget:
    """호출 수·시간 제한. 넘으면 남은 후보는 건너뛴다."""

    def __init__(self, max_calls: int, seconds: float, clock: Callable[[], float]) -> None:
        self.max_calls, self.clock = max_calls, clock
        self.deadline = clock() + seconds
        self.calls = 0

    def ok(self) -> bool:
        return self.calls < self.max_calls and self.clock() < self.deadline

    def use(self) -> None:
        self.calls += 1


def _months_back(today: date, n: int) -> date:
    y, m = today.year, today.month - n
    while m <= 0:
        y, m = y - 1, m + 12
    return date(y, m, 1)


def measure_interest(search: NaverSearch, queries: Sequence[str], today: date,
                     budget: _Budget | None = None) -> dict[str, tuple[float | None, bool]]:
    """{검색어: (관심도, 작년 이맘때 올랐는지)}. 관심도는 같은 기간 기준어 평균을 100으로 환산.

    기준어 자체가 계절을 타므로 달마다 나누지 않고 기간 평균으로 한 번만 나눈다(계절 모양 유지).
    """
    start, end = _months_back(today, 13), today - timedelta(days=1)
    out: dict[str, tuple[float | None, bool]] = {}
    uniq = [q for q in dict.fromkeys(queries) if q != ANCHOR_QUERY]
    this_m = {f"{today.year - 1}-{today.month:02d}-01",
              _months_back(today, 11).isoformat()}   # 작년 이번 달, 작년 다음 달
    groups = [uniq[i:i + TREND_GROUP] for i in range(0, len(uniq), TREND_GROUP)] or [[]]
    for group in groups:
        if budget is not None:
            if not budget.ok():
                break
            budget.use()
        try:
            data = search.trend([ANCHOR_QUERY, *group], start, end)
        except SearchError as exc:
            log.warning("검색어트렌드 실패: %s", exc)
            continue
        anchor = [r for _p, r in data.get(ANCHOR_QUERY, [])]
        base = sum(anchor) / len(anchor) if anchor else 0
        if base <= 0:
            continue                                  # 기준어가 0이면 이 묶음은 비교 불가
        for q in [ANCHOR_QUERY, *group]:
            series = data.get(q, [])
            if not series:
                out.setdefault(q, (None, False))
                continue
            scaled = [(p, r * 100 / base) for p, r in series]
            recent = [v for _p, v in scaled[-3:]]
            mean_all = sum(v for _p, v in scaled) / len(scaled)
            season_vals = [v for p, v in scaled if p in this_m]
            seasonal = bool(season_vals) and mean_all > 0 and max(season_vals) >= mean_all * 1.3
            out[q] = (round(sum(recent) / len(recent), 1), seasonal)
    return out


def _search(budget: _Budget, fn: Callable[..., list[SearchHit]], *args, **kw) -> list[SearchHit] | None:
    if not budget.ok():
        return None
    budget.use()
    try:
        return fn(*args, **kw)
    except SearchError as exc:
        log.warning("검색 실패(%s): %s", args[:1], exc)
        return []


def _official(hits: Sequence[SearchHit]) -> list[SearchHit]:
    keep = set(official_candidates(hits, limit=10))
    return [h for h in hits if h.url in keep]


def recent_headlines(search: NaverSearch, today: date, budget: _Budget) -> list[str]:
    titles: list[str] = []
    since = today - timedelta(days=NEWS_DAYS)
    for q in DISCOVERY_QUERIES:
        hits = _search(budget, search.news, q, display=20)
        for h in hits or []:
            d = _news_date(h)
            if d is not None and d < since:
                continue
            if h.title and h.title not in titles:
                titles.append(h.title)
    return titles[:80]


def assess(seed: Seed, written: Sequence[Written], search: NaverSearch, today: date,
           budget: _Budget) -> Candidate | None | Literal[False]:
    """후보 하나를 판정한다. None=제외, False=시간·호출 한도로 확인 못 함."""
    mine = [w for w in written if norm(w.name) == norm(seed.name)]
    latest = max(mine, key=lambda w: (w.year, w.written_on)) if mine else None

    news = _search(budget, search.news, seed.query, display=10)
    if news is None:
        return False
    if latest is None:
        kind: Kind = "new"
        year = today.year
    else:
        # 새 연도 자료가 공식 사이트에 보이는가. 해가 실제로 바뀐 뒤에만 본다
        # (연말엔 다음 해 계획 기사가 많아 막 쓴 글에 새 연도 후보가 붙는 것을 막음)
        next_year = latest.year + 1
        kind, year = "update", latest.year
        if next_year <= today.year:
            hits = _search(budget, search.web, f"{next_year}년 {seed.name}", display=30)
            if hits is None:
                return False
            fresh = [h for h in _official(hits) if str(next_year) in h.title and seed.matches(h.title)]
            if fresh:
                c = Candidate(seed, "new_year", next_year, [h.url for h in fresh[:5]], written=latest)
                c.reasons.append(f"공식 자료에 {next_year}년 내용이 올라옴({latest.year}년 글 있음)")
                _news_flags(c, news)
                return c
        changed = [h for h in news
                   if seed.matches(h.title) and any(w in h.title for w in CHANGE_WORDS)
                   and not any(w in h.title for w in TALK_WORDS) and not is_local_news(h.title)
                   and (_news_date(h) or date.min) > latest.written_on]
        if len(changed) < MIN_CHANGE_NEWS or latest.post_id is None:
            return None
        c = Candidate(seed, kind, year, written=latest, news_titles=[h.title for h in changed[:3]])
        c.reasons.append("글 쓴 뒤 변경 소식이 뉴스에 나옴(바뀌었는지 확인 필요)")
        return c

    hits = _search(budget, search.web, f"{year}년 {seed.name}", display=30)
    if hits is None:
        return False
    official = _official(hits)
    if not official:
        return None                                    # 공식 근거 없음
    c = Candidate(seed, kind, year, [h.url for h in official[:5]],
                  news_titles=list(seed.news_titles))
    _news_flags(c, news)
    return c


def _news_flags(c: Candidate, news: Sequence[SearchHit]) -> None:
    ends = [h.title for h in news if c.seed.matches(h.title) and any(w in h.title for w in END_WORDS)]
    if ends:
        c.warnings.append(f"확인 필요: 최근 뉴스에 종료·마감 언급('{ends[0][:40]}')")


def score(c: Candidate, today: date) -> float:
    s = math.log10(1 + (c.interest or 0))             # 0~약 4
    nxt = today.month % 12 + 1
    if c.seed.months and (today.month in c.seed.months or nxt in c.seed.months):
        s += 1.0
        c.in_season = True
    if c.trend_season:
        s += 0.5
    if c.seed.discovered:
        s += 1.0                                       # 새 제도는 작년 검색 자료가 없어도 불리하지 않게
    if c.kind == "new_year":
        s += 1.0
    return round(s, 2)


def pick(cands: Sequence[Candidate], limit: int, cap: int = CATEGORY_CAP,
         max_updates: int = MAX_UPDATES) -> list[Candidate]:
    out: list[Candidate] = []
    per_cat: dict[str, int] = {}
    updates = 0
    for c in sorted(cands, key=lambda c: c.score, reverse=True):
        if len(out) >= limit:
            break
        if per_cat.get(c.seed.category, 0) >= cap:
            continue
        if c.kind == "update":
            if updates >= max_updates:
                continue
            updates += 1
        per_cat[c.seed.category] = per_cat.get(c.seed.category, 0) + 1
        out.append(c)
    return out


def find_candidates(
    search: NaverSearch,
    writer: TopicWriter | None,
    seeds: Sequence[Seed],
    written: Sequence[Written],
    today: date | None = None,
    limit: int = DEFAULT_LIMIT,
    skip_keys: Iterable[str] = (),
    max_calls: int = 400,
    seconds: float = 300,
    clock: Callable[[], float] = time.monotonic,
) -> FindResult:
    """주제 후보를 고른다. skip_keys: 최근에 보냈지만 안 고른 후보 key(잠시 쉼)."""
    today = today or date.today()
    budget = _Budget(max_calls, seconds, clock)
    notes: list[str] = []
    skip = set(skip_keys)

    pool = list(seeds)
    if writer is not None:
        titles = recent_headlines(search, today, budget)
        try:
            found = writer.discover(titles, seeds)
            pool += found
            if found:
                notes.append(f"뉴스에서 새 제도 {len(found)}개 발견")
        except Exception as exc:  # noqa: BLE001 - 새 제도 찾기 실패해도 기본 목록으로 계속
            log.warning("새 제도 찾기 실패: %s", exc)
            notes.append("뉴스에서 새 제도 찾기는 실패해 기본 목록만 봄")

    nxt = today.month % 12 + 1
    pool.sort(key=lambda s: (not s.discovered, not (today.month in s.months or nxt in s.months)))

    cands: list[Candidate] = []
    unchecked = 0
    for seed in pool:
        got = assess(seed, written, search, today, budget)
        if got is False:
            unchecked += 1
            continue
        if got is not None and got.key not in skip:
            cands.append(got)
    if unchecked:
        notes.append(f"시간·호출 한도로 {unchecked}개 제도는 확인 못 함")

    interest = measure_interest(search, [c.seed.query for c in cands], today, budget)
    for c in cands:
        val, seasonal = interest.get(c.seed.query, (None, False))
        c.interest = val
        c.trend_season = seasonal
        if seasonal:
            c.reasons.append("작년 이맘때 검색 증가")
        if val is None and c.kind != "update":
            c.warnings.append("관심도 확인 못 함")
        c.score = score(c, today)
        if c.in_season:
            c.reasons.insert(0, "신청·납부 시기")
        if c.seed.discovered:
            c.reasons.insert(0, "최근 뉴스에 나온 제도")

    chosen = pick(cands, limit)
    if len(chosen) < limit:
        notes.append(f"기준을 넘은 후보가 {len(chosen)}개뿐이라 그만큼만 추천")

    need_topic = [c for c in chosen if c.kind != "update"]
    phrased: dict[str, str] = {}
    if writer is not None and need_topic:
        try:
            phrased = writer.phrase(need_topic)
        except Exception as exc:  # noqa: BLE001 - 문장 다듬기 실패해도 틀 문장으로 계속
            log.warning("주제 문장 다듬기 실패: %s", exc)
            notes.append("주제 문장 다듬기에 실패해 기본 틀 문장을 씀")
    for c in chosen:
        if c.kind == "update":
            c.topic = f"{c.year}년 {c.seed.name} 글 내용 확인"
            continue
        t = phrased.get(c.key, "")
        c.topic = t if valid_topic(t, c) else fallback_topic(c)
    return FindResult(chosen, notes, budget.calls)
