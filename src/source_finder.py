"""주제로 공식 근거 자료 주소를 찾는다 (NAVER API HUB 검색).

- 웹문서 검색 결과 중 정부·공공기관 주소만 근거 후보로 쓴다.
- 뉴스는 최근 변경 소식을 사람이 확인하는 참고용이다(글의 숫자 근거로 쓰지 않음).
- 검색어 트렌드는 주제 고르기용.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence
from urllib.parse import urlparse

import requests

from src.config import Settings

API_HOST = "https://naverapihub.apigw.ntruss.com"

# .go.kr 전체 + 공공기관(.or.kr 등) 중 생활 돈 정보에 자주 쓰는 곳
OFFICIAL_SUFFIXES = (".go.kr",)
OFFICIAL_HOSTS = frozenset({
    "gov.kr", "korea.kr",          # 정부24, 대한민국 정책브리핑
    "nhis.or.kr", "nps.or.kr", "hira.or.kr", "kcomwel.or.kr", "kinfa.or.kr",
    "fss.or.kr", "lh.or.kr", "kosaf.go.kr", "hf.go.kr", "kodit.co.kr",
    "kibo.or.kr", "semas.or.kr", "khug.or.kr", "kead.or.kr", "kicox.or.kr",
    "mnuri.kr", "arko.or.kr",      # 문화누리카드 공식 누리집, 한국문화예술위원회(운영 기관)
    "energyv.or.kr",               # 에너지바우처 공식 누리집(한국에너지공단)
    "kotsa.or.kr",                 # 한국교통안전공단(K-패스 운영 기관, korea-pass.kr은 이 서버 접속을 끊음)
})
# 시·도청 누리집. 전국 제도 글에 지역 사업(경기패스·인천 i-패스 등) 기준이 섞이지 않게,
# 검색 제목에 그 제도 이름이 없는 페이지는 뒤로 미룬다(기후동행카드처럼 그 지역 제도면 제목에 이름이 있어 그대로)
LOCAL_HOSTS = frozenset({
    "seoul.go.kr", "busan.go.kr", "daegu.go.kr", "incheon.go.kr", "gwangju.go.kr", "daejeon.go.kr",
    "ulsan.go.kr", "sejong.go.kr", "gg.go.kr", "gwd.go.kr", "chungbuk.go.kr", "chungnam.go.kr",
    "jeonbuk.go.kr", "jb.go.kr", "jeonnam.go.kr", "gb.go.kr", "gyeongnam.go.kr", "jeju.go.kr",
})
# 주제 이름에서 제도 이름 뒤에 붙는 일반 낱말(여기서 끊는다)
_GENERIC = re.compile(r"^(신청|대상|지원|자격|방법|발급|환급|계산|조건|가입|사용|신규|요건|기간|기한|금액|안내|정리|총정리|혜택)")
# 자바스크립트로 그려서 본문을 읽을 수 없는 주소
UNREADABLE_PATTERNS = ("websquare", "/index_pp.xml")


class SearchError(RuntimeError):
    """검색 서비스 호출 실패."""


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    description: str
    published: str = ""     # 뉴스만


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def is_official(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if any(host.endswith(s) for s in OFFICIAL_SUFFIXES):
        return True
    return host in OFFICIAL_HOSTS or any(host.endswith("." + h) for h in OFFICIAL_HOSTS)


class NaverSearch:
    def __init__(self, client_id: str, client_secret: str,
                 session: requests.Session | None = None, timeout: float = 20) -> None:
        self.headers = {"X-NCP-APIGW-API-KEY-ID": client_id, "X-NCP-APIGW-API-KEY": client_secret}
        self.http = session or requests.Session()
        self.timeout = timeout

    @classmethod
    def from_settings(cls, settings: Settings) -> "NaverSearch":
        settings.require("naver_client_id", "naver_client_secret")
        return cls(settings.naver_client_id, settings.naver_client_secret)

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        try:
            resp = self.http.request(method, API_HOST + path, headers=self.headers,
                                     timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise SearchError(f"네이버 검색 접속 실패: {exc.__class__.__name__}") from exc
        if resp.status_code == 401:
            raise SearchError("네이버 검색 인증 실패: .env의 NAVER_CLIENT_ID/SECRET을 확인하세요")
        if resp.status_code == 429:
            raise SearchError("네이버 검색 호출 한도 초과")
        if resp.status_code != 200:
            raise SearchError(f"네이버 검색 오류 {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise SearchError("네이버 검색 응답이 JSON이 아닙니다") from exc

    def _search(self, kind: str, query: str, display: int, **params) -> list[SearchHit]:
        data = self._request("GET", f"/search/v1/{kind}",
                             params={"query": query, "display": display, "format": "json", **params})
        hits = []
        for item in data.get("items", []):
            url = item.get("originallink") or item.get("link") or ""
            if url:
                hits.append(SearchHit(_clean(item.get("title", "")), url,
                                      _clean(item.get("description", "")), item.get("pubDate", "")))
        return hits

    def web(self, query: str, display: int = 20) -> list[SearchHit]:
        return self._search("webkr", query, display)

    def news(self, query: str, display: int = 10) -> list[SearchHit]:
        return self._search("news", query, display, sort="date")

    def trend(self, keywords: Sequence[str], start: date, end: date,
              time_unit: str = "month") -> dict[str, list[tuple[str, float]]]:
        """{검색어: [(기간 시작일, 상대 검색량 0~100)]}. 한 번에 최대 5개."""
        body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "timeUnit": time_unit,
                "keywordGroups": [{"groupName": k, "keywords": [k]} for k in keywords[:5]]}
        data = self._request("POST", "/search-trend/v1/search", json=body)
        return {r["title"]: [(d["period"], d["ratio"]) for d in r.get("data", [])]
                for r in data.get("results", [])}


def official_candidates(hits: Sequence[SearchHit], limit: int = 8) -> list[str]:
    """검색 결과에서 읽을 수 있는 공식 주소만 순서대로 고른다(중복 제외)."""
    urls: list[str] = []
    for hit in hits:
        if not is_official(hit.url):
            continue
        if any(p in hit.url for p in UNREADABLE_PATTERNS):
            continue
        if hit.url not in urls:
            urls.append(hit.url)
        if len(urls) >= limit:
            break
    return urls


def core_keyword(topic: str) -> str:
    """'월세 세액공제 신청 대상과 …' → '월세 세액공제'. 주제에서 제도 이름만 뽑는다."""
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


def is_local(url: str) -> bool:
    """시·도청 누리집인지."""
    host = (urlparse(url).hostname or "").lower()
    return host in LOCAL_HOSTS or any(host.endswith("." + h) for h in LOCAL_HOSTS)


def _squash(text: str) -> str:
    return re.sub(r"[\s\-·]", "", text).lower()


def find_official_urls(search: NaverSearch, topic: str, limit: int = 8) -> list[str]:
    """주제로 웹문서를 검색해 공식 근거 후보 주소를 돌려준다. 시·도청 페이지 중 검색 제목에 제도 이름이 없는 것
    (전국 K-패스 글에 나온 경기패스·인천 i-패스 안내 등)은 중앙부처·공공기관 뒤로 미룬다(빼지는 않음)."""
    hits = search.web(topic, display=30)
    titles = {h.url: _squash(h.title) for h in hits}
    core = _squash(core_keyword(topic))
    urls = official_candidates(hits, limit=30)
    return sorted(urls, key=lambda u: is_local(u) and core not in titles.get(u, ""))[:limit]
