"""국가법령정보 OPEN API로 주제에 맞는 현행 법령 조문을 근거 자료로 가져온다.

- 지능형 법령검색(target=aiSearch)은 주제 문장을 넣으면 관련 조문 본문을 돌려준다.
- 검색 결과에는 관련 없는 조문이 섞이므로(예: '전기요금' → 전기통신사업법) AI가 후보 목록 안에서만
  고르게 한다. 목록 밖 조문은 받지 않으므로 AI가 조문을 지어낼 수 없다.
- 지능형 검색 결과는 옛 버전 조문일 때가 있다(예: 2026.9 전부개정·이름이 바뀐 법의 개정 전 조문).
  그래서 고른 조문은 법령 본문 조회(lawService)로 현행 원문을 다시 받아 그 글자를 쓰고,
  현행 법령에 없는 조문(번호가 바뀌거나 삭제됨)은 뺀다.
- 조문 본문은 API 원문 그대로 쓴다(Source.via="law", AI가 옮겨 적은 자료가 아님).
- 인증값(LAW_OC)은 open.law.go.kr에 등록한 서버 IP에서만 통한다.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Sequence
from urllib.parse import quote

import requests

from src.source_fetcher import Source

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"
MAX_ARTICLES = 4            # 글 한 편에 넣는 조문 수
MAX_ARTICLE_CHARS = 6_000   # 조문 하나가 아주 길면(서식·위임 규정 등) 앞부분만
SEARCH_DISPLAY = 8

# 주제 문장에서 빼면 검색이 제도 이름에 집중되는 일반 낱말(조사 '과·와·및'은 떼고 비교)
_GENERIC = frozenset({"신청", "방법", "대상", "자격", "금액", "한도", "기한", "조건", "기준", "지원",
                      "계산법", "계산", "총정리", "알아보기", "정리", "안내", "및"})


class LawError(RuntimeError):
    """법령 API 호출 실패."""


@dataclass(frozen=True)
class LawArticle:
    key: str          # 후보 번호(법령ID-조문번호-가지번호). AI가 고를 때 쓴다
    law_id: str       # 법령ID(법 이름이 바뀌어도 같음)
    jo: str           # 조문 번호 6자리(조 4자리 + 가지 2자리): 제95조의2 → 009502
    law: str          # 조세특례제한법 시행령
    number: str       # 제95조의2
    text: str         # 조문 원문(제목 포함)
    effective: str    # 시행일 2026-09-18

    @property
    def heading(self) -> str:
        m = re.match(r"제\d+조(?:의\d+)?\([^)]*\)", self.text)
        return m.group(0) if m else self.number

    @property
    def url(self) -> str:
        return (f"https://www.law.go.kr/{quote('법령')}/{quote(self.law.replace(' ', ''))}/"
                f"{quote(self.number)}")


def core_query(topic: str) -> str:
    """'월세 세액공제 신청 대상과 공제 금액 계산법' → '월세 세액공제 공제'(제도 이름 위주)."""
    kept = []
    for word in topic.split():
        bare = re.sub(r"(과|와)$", "", word)
        if bare not in _GENERIC and word not in _GENERIC:
            kept.append(word)
    return " ".join(kept)


def _article_number(raw: dict) -> str:
    main = int(raw.get("조문번호") or 0)
    branch = int(raw.get("조문가지번호") or 0)
    return f"제{main}조" + (f"의{branch}" if branch else "")


def _jo(raw: dict) -> str:
    return f"{int(raw.get('조문번호') or 0):04d}{int(raw.get('조문가지번호') or 0):02d}"


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _str(v: Any) -> str:
    """XML을 JSON으로 바꾼 응답이라 글자가 목록으로 올 때도 있다."""
    return "\n".join(str(x).strip() for x in _as_list(v) if str(x).strip())


def _dicts(v: Any) -> list[dict]:
    return [x for x in _as_list(v) if isinstance(x, dict)]


def _article_text(unit: dict) -> str:
    """조문단위 JSON → 원문 글자(조 제목·본문, 항, 호, 목 순서). 항 없이 바로 호가 오는 조문도 있다."""
    lines = [_str(unit.get("조문내용"))]

    def add_ho(parent: dict) -> None:
        for ho in _dicts(parent.get("호")):
            lines.append(_str(ho.get("호내용")))
            for mok in _dicts(ho.get("목")):
                lines.append(_str(mok.get("목내용")))

    add_ho(unit)
    for hang in _dicts(unit.get("항")):
        lines.append(_str(hang.get("항내용")))
        add_ho(hang)
    return "\n".join(x for x in lines if x)


def _title(text: str) -> str:
    """'제95조의2(월세액에 대한 세액공제) ...' → '월세액에 대한 세액공제'(띄어쓰기 무시)."""
    m = re.match(r"\s*제\d+조(?:의\d+)?\(([^)]*)\)", text)
    return re.sub(r"\s+", "", m.group(1)) if m else ""


def _date(raw: str) -> str:
    d = re.sub(r"\D", "", raw or "")[:8]
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else ""


def _tidy(text: str) -> str:
    """항(①)·호(1.) 앞에서 줄을 바꿔 읽기 쉽게."""
    text = re.sub(r"\s*([①-⑳])", r"\n\1", text.strip())
    return re.sub(r"(?<=[다.>])\s*(\d{1,2}\.\s)", r"\n\1", text).strip()


class LawClient:
    def __init__(self, oc: str, session: requests.Session | None = None, timeout: float = 20) -> None:
        self.oc = oc
        self.http = session or requests.Session()
        self.timeout = timeout

    def search(self, query: str, display: int = SEARCH_DISPLAY) -> list[LawArticle]:
        """지능형 검색: 질문 문장과 관련된 현행 조문 목록."""
        try:
            resp = self.http.get(SEARCH_URL, timeout=self.timeout, params={
                "OC": self.oc, "target": "aiSearch", "type": "JSON", "query": query, "display": display})
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise LawError(f"법령 검색 접속 실패: {exc.__class__.__name__}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("aiSearch"), dict):
            # 인증 실패 등은 {"result": ..., "msg": ...}로 온다(값은 화면에 보여도 되는 안내문)
            raise LawError(f"법령 검색 실패: {_reason(data)}")
        out: list[LawArticle] = []
        for r in _dicts(data["aiSearch"].get("법령조문")):
            text, law = _str(r.get("조문내용")), _str(r.get("법령명"))
            if not text or not law or not str(r.get("조문번호") or "").isdigit():
                continue
            out.append(LawArticle(
                key=f"{r.get('법령ID', '')}-{r.get('조문번호', '')}-{r.get('조문가지번호', '')}",
                law_id=str(r.get("법령ID") or ""), jo=_jo(r),
                law=law, number=_article_number(r), text=_tidy(text),
                effective=_date(r.get("시행일자") or "")))
        return out

    def current(self, article: LawArticle, today: date | None = None) -> LawArticle | None:
        """같은 법령·같은 조문 번호의 현행 원문.

        None: 법령이 폐지됨 / 그 번호의 조문이 없음 / 아직 시행 전 조문 / 제목이 바뀌어 다른 조문이 됨
        (전부개정으로 같은 번호에 다른 내용이 들어선 경우. AI는 옛 제목을 보고 골랐으므로 쓰지 않는다)."""
        today_s = (today or date.today()).strftime("%Y%m%d")
        try:
            resp = self.http.get(SERVICE_URL, timeout=self.timeout, params={
                "OC": self.oc, "target": "law", "type": "JSON", "ID": article.law_id, "JO": article.jo})
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise LawError(f"법령 본문 조회 실패: {exc.__class__.__name__}") from exc
        law = data.get("법령") if isinstance(data, dict) else None
        if isinstance(data, dict) and "일치하는 법령이 없습니다" in str(data.get("Law") or ""):
            return None  # 폐지되거나 다른 법으로 바뀐 법령(지능형 검색이 옛 법령을 돌려준 경우)
        if not isinstance(law, dict):
            raise LawError(f"법령 본문 조회 실패: {_reason(data)}")
        info = law.get("기본정보") if isinstance(law.get("기본정보"), dict) else {}
        body = law.get("조문") if isinstance(law.get("조문"), dict) else {}

        def eff(u: dict) -> str:
            return re.sub(r"\D", "", str(u.get("조문시행일자") or info.get("시행일자") or ""))[:8]

        units = [u for u in _dicts(body.get("조문단위"))
                 if u.get("조문여부", "조문") == "조문" and str(u.get("조문번호") or "").isdigit()
                 and _jo(u) == article.jo]
        in_force = sorted((u for u in units if not eff(u) or eff(u) <= today_s), key=eff)
        if not in_force:
            return None
        unit = in_force[-1]
        text = _article_text(unit)
        if not text or (_title(article.text) and _title(text) != _title(article.text)):
            return None
        return LawArticle(key=article.key, law_id=article.law_id, jo=article.jo,
                          law=_str(info.get("법령명_한글")) or article.law, number=_article_number(unit),
                          text=text, effective=_date(eff(unit)))

    def candidates(self, topic: str) -> list[LawArticle]:
        """주제 문장 그대로와 제도 이름 위주 두 번 검색해 합친다(같은 조문은 한 번만).
        한쪽 검색만 실패하면 다른 쪽 결과로 진행하고, 둘 다 실패할 때만 오류."""
        seen: dict[str, LawArticle] = {}
        failure: LawError | None = None
        ok = False
        for q in dict.fromkeys([topic, core_query(topic)]):
            if not q:
                continue
            try:
                found = self.search(q)
            except LawError as exc:
                failure = exc
                continue
            ok = True
            for a in found:
                seen.setdefault(a.key, a)
        if not ok and failure is not None:
            raise failure
        return list(seen.values())


def _reason(data: Any) -> str:
    """오류 응답의 안내문만(응답 전체를 찍으면 요청값이 되돌아와 섞일 수 있다)."""
    if isinstance(data, dict):
        for k in ("result", "Law", "msg"):
            if data.get(k):
                return str(data[k])[:80]
    return "알 수 없는 응답 형식"


def to_source(article: LawArticle, source_id: str) -> Source:
    head = f"[현행 법령 원문 · 국가법령정보센터 · {article.law}" + \
        (f" · 시행일 {article.effective}]" if article.effective else "]")
    return Source(id=source_id, url=article.url, title=f"{article.law} {article.heading}",
                  text=f"{head}\n{article.text[:MAX_ARTICLE_CHARS]}", via="law")


Chooser = Callable[[str, Sequence[LawArticle]], Sequence[str]]


def find_law_sources(client: Any, choose: Chooser, topic: str, first_number: int,
                     max_articles: int = MAX_ARTICLES) -> tuple[list[Source], list[str]]:
    """(조문 자료, 실패 사유). 법령 근거가 없는 주제(공기업 약관 등)면 빈 목록."""
    # 법령은 덧붙이는 근거 자료라 어떤 실패도 글 작성을 멈추지 않는다(응답 형식이 예상과 달라도)
    try:
        cands = client.candidates(topic)
    except LawError as exc:
        return [], [str(exc)]
    except Exception as exc:  # noqa: BLE001
        log.warning("법령 검색 처리 실패: %r", exc)
        return [], [f"법령 검색 처리 실패: {exc.__class__.__name__}"]
    if not cands:
        return [], []
    try:
        keys = list(choose(topic, cands))
    except Exception as exc:  # noqa: BLE001 - 조문 고르기가 안 되면 법령 없이 진행
        log.warning("법령 조문 고르기 실패: %s", exc)
        return [], [f"법령 조문 고르기 실패: {exc.__class__.__name__}"]
    by_key = {a.key: a for a in cands}
    picked: list[LawArticle] = []
    errors: list[str] = []
    for k in dict.fromkeys(keys):
        if k not in by_key or len(picked) >= max_articles:
            continue
        found = by_key[k]
        try:
            now = client.current(found)
        except LawError as exc:
            errors.append(str(exc))
            continue
        except Exception as exc:  # noqa: BLE001
            log.warning("법령 본문 처리 실패: %r", exc)
            errors.append(f"법령 본문 처리 실패: {found.law} {found.number} ({exc.__class__.__name__})")
            continue
        if now is None:
            errors.append(f"현행 시행 중인 같은 조문이 없어 뺌(폐지·개정·시행 전): {found.law} {found.number}")
            continue
        picked.append(now)
    return [to_source(a, f"S{first_number + i}") for i, a in enumerate(picked)], errors
