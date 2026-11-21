"""발행 글이 근거로 쓴 공식 페이지가 바뀌었는지 감시한다(글 유효기간 점검 4단계).

왜: 날짜 점검(freshness)은 '언제 바뀔 것 같은지'를 미리 정해 두는 것이라, 예고 없이 바뀐 금액·기준·기간은
놓친다. 글을 쓸 때 읽은 공식 페이지를 주기적으로 다시 읽어, 글 내용에 영향이 있는 변화만 알린다.

동작(하루 한 번, 한국 시간 RUN_HOUR시 이후, 실행기가 부름):
1. 지금 발행 중인 글마다 가장 최근 발행 작업이 읽은 자료 중 서버가 직접 읽은 페이지(via=direct)를 모은다.
   여러 글이 같은 페이지를 쓰면 글마다 기준을 따로 두고 따로 판정한다(페이지는 한 번만 읽는다).
   처음 보는 페이지·글이 갱신돼 자료가 바뀐 페이지는 '그 글을 쓸 때 본 내용'을 기준으로 삼는다.
   (구글이 대신 읽은 페이지는 읽을 때마다 문장이 달라 비교할 수 없고, 법령 조문은 날짜 점검이 맡는다.)
2. 가장 오래 안 본 페이지부터 하루 MAX_PER_RUN곳(알림 실패로 다시 시도해도 합쳐서)을 다시 읽어 기준과 줄 단위로 비교한다.
   조회수·수정일 같은 사소한 줄과, 길이 제한으로 잘린 끝부분 차이는 버린다.
3. 바뀐 줄이 있을 때만 Gemini에게 '이 변화가 글 내용(금액·기간·대상·방법)에 영향을 주는가'를 묻는다.
4. 영향 있음만 하루 한 번 텔레그램으로 모아 알리고, 그 글마다 갱신 작업을 자동 등록한다(auto_refresh가 자동 반영 여부 판정). 비교한 변화는 모두 기록(changes 표, 기준 조정용)하고
   기준을 새 내용으로 바꿔 같은 변화를 다시 알리지 않는다.
5. 연속 MAX_FAILS번 못 읽은 페이지는 알림에 한 번 적는다(우리 쪽 중계 장애는 세지 않는다).

장부: data/source_watch.sqlite3 (지워지면 다음 날 '글 쓸 때 본 내용' 기준으로 다시 만든다).
"""
from __future__ import annotations

import fcntl
import json
import logging
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError

from src import db, freshness, refresh
from src.freshness import html_to_text
from src.source_fetcher import DEFAULT_MAX_CHARS, RelayUnavailableError, SourceError, fetch_source

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
CHECK_SECONDS = 600            # 실행기가 10분마다 확인
RUN_HOUR = 14                  # 오전 8시 발행·10시 날짜 점검과 겹치지 않게
MAX_PER_RUN = 20               # 하루에 다시 읽는 페이지 수(정부 사이트 접속 차단 방지, 100곳이면 약 5~6일 주기)
MAX_TRIES_PER_DAY = 3
MAX_FAILS = 3                  # 연속 이만큼 못 읽으면 알림에 적는다(한 번만)
PAUSE_SECONDS = 3.0            # 페이지 사이 쉬는 시간
MAX_DIFF_CHARS = 3000          # AI에 보내는 바뀐 줄(없어진 줄·새 줄 각각)
MAX_POST_CHARS = 8000          # AI에 보내는 글 본문
MAX_MESSAGE = 3800
TAIL_RATIO = 0.2               # 길이 제한에 걸린 본문은 끝 20%의 차이를 버린다(앞이 늘면 끝이 밀려 잘림)

# 바뀌어도 글과 상관없는 줄(조회수·수정일·담당자·만족도 조사 등)
# 짧은 줄(NOISE_MAX_LEN자 이하)에만 적용한다. 본문 문장('홈택스 로그인 후 조회')은 버리지 않게.
NOISE_MAX_LEN = 40
_NOISE = re.compile(
    r"조회\s*수|최종\s*수정|수정일|등록일|작성일|게시일|담당\s*부서|담당자|만족도|평가해\s*주|"
    r"방문자|접속자|copyright|all rights reserved|현재\s*페이지|이전\s*글|다음\s*글",
    re.I)
# 날짜 하나만 있는 줄(게시일 등). '~'가 든 기간(신청 기간)은 버리지 않는다.
_DATE_ONLY = re.compile(r"^[\d\s.\-/:년월일시분()오전후AMP]+$")

SYSTEM_INSTRUCTION = """너는 한국 생활 제도(세금·지원금·복지·주거·금융) 블로그의 근거 자료 변경 판정자다.
글을 쓸 때 근거로 삼은 공식 페이지에서 없어진 줄과 새로 생긴 줄을 보고, 그 변화 때문에 블로그 글의 내용이
틀리거나 낡게 되는지 판단한다.

영향 있음(affects=true):
- 글에 적힌 금액·비율·소득 기준·지원 대상·신청 기간·마감일·신청 방법·필요 서류가 바뀜
- 제도가 끝나거나 새로 생김, 신청이 마감·재개됨, 새 해 기준이 발표됨
영향 없음(affects=false):
- 메뉴·안내 문구·디자인·공지 목록·순서만 바뀜, 글에 쓰이지 않은 다른 제도 내용, 오타 수정
summary: 영향 있음이면 '무엇이 어떻게 바뀌었는지' 한 줄(예: '청년미래적금 소득 기준 200%→180%').
영향 없음이면 왜 무관한지 짧게. 글에 없는 내용을 지어내지 않는다."""


class ChangeVerdict(BaseModel):
    affects: bool = Field(description="블로그 글 내용에 영향이 있으면 true")
    summary: str = Field(description="무엇이 어떻게 바뀌었는지(또는 왜 무관한지) 한 줄")


@dataclass
class Deps:
    wp: Any                      # list_published_posts(), get_post_content(post_id)
    generator: Any               # structured(prompt, schema, system) -> (raw, model)
    telegram: Any                # send_text(text)
    data_dir: Path
    jobs_conn_factory: Callable[[], sqlite3.Connection]
    fetch: Callable[[str, str], Any] = fetch_source
    pause: float = PAUSE_SECONDS
    auto_refresh: bool = True    # 영향 있는 글의 갱신 작업 자동 등록(시험에서 끔)


# ---------- 장부 ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    url          TEXT NOT NULL,
    post_id      INTEGER NOT NULL,     -- 이 페이지를 근거로 쓴 글(여러 글이 같은 페이지를 쓰면 글마다 한 줄, 기준도 따로)
    title        TEXT NOT NULL,        -- 글 제목
    link         TEXT NOT NULL,        -- 글 주소
    job_id       INTEGER NOT NULL,     -- 기준을 가져온 작업(글이 갱신되면 바뀜)
    baseline     TEXT NOT NULL,        -- 비교 기준 본문
    last_checked TEXT,
    fails        INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT NOT NULL DEFAULT '',
    dead_noted   INTEGER NOT NULL DEFAULT 0,  -- '열리지 않는 페이지'로 알렸으면 1(다시 열리면 0)
    PRIMARY KEY (url, post_id)
);
CREATE TABLE IF NOT EXISTS changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT NOT NULL,
    post_id     INTEGER NOT NULL,
    job_id      INTEGER NOT NULL,      -- 그때 기준이던 작업(글을 갱신하면 달라져 힌트에서 빠진다)
    found_at    TEXT NOT NULL,
    affects     INTEGER NOT NULL,
    summary     TEXT NOT NULL,
    removed     TEXT NOT NULL,
    added       TEXT NOT NULL,
    model       TEXT NOT NULL DEFAULT '',
    notified    TEXT                   -- 알린 한국 날짜(영향 있음만 알림)
);
CREATE TABLE IF NOT EXISTS days (
    day   TEXT PRIMARY KEY,
    tries INTEGER NOT NULL,
    done  INTEGER NOT NULL DEFAULT 0,
    fetched INTEGER NOT NULL DEFAULT 0,   -- 오늘 읽으러 간 페이지 수(다시 시도해도 하루 MAX_PER_RUN곳을 넘지 않게)
    note  TEXT NOT NULL DEFAULT ''
);
"""


def store_path(data_dir: Path) -> Path:
    return Path(data_dir) / "source_watch.sqlite3"


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- 감시 대상 모으기 ----------

def watched_pages(jobs_conn: sqlite3.Connection, published: dict[int, dict[str, Any]]
                  ) -> dict[tuple[str, int], dict[str, Any]]:
    """{(주소, 글 번호): {title, link, job_id, text}}. 지금 발행 중인 글(published: WP 글 번호→{title, link})마다
    가장 최근 발행 작업이 직접 읽은 자료만."""
    latest: dict[int, dict[str, Any]] = {}
    for j in db.list_jobs(jobs_conn, status="published", limit=-1):
        target = refresh.target_of(j)
        pid = int(target["post_id"]) if target else int(j["post_id"] or 0)
        if pid in published and (pid not in latest or j["id"] > latest[pid]["id"]):
            latest[pid] = j
    pages: dict[tuple[str, int], dict[str, Any]] = {}
    for pid, j in latest.items():
        post = published[pid]
        for s in (j["checkpoint"].get("content") or {}).get("sources") or []:
            if s.get("via", "direct") == "direct" and s.get("url") and s.get("text"):
                pages[(s["url"], pid)] = {"title": post.get("title") or j["topic"], "link": post.get("link") or "",
                                          "job_id": j["id"], "text": s["text"]}
    return pages


def sync_pages(conn: sqlite3.Connection, pages: dict[tuple[str, int], dict[str, Any]]) -> tuple[int, int]:
    """장부를 감시 대상과 맞춘다. (새로 넣은 수, 뺀 수). 글이 갱신돼 작업이 바뀐 줄은 그 작업 내용을 새 기준으로."""
    known = {(r["url"], r["post_id"]): r["job_id"] for r in conn.execute("SELECT url, post_id, job_id FROM pages")}
    added = 0
    for (url, pid), p in pages.items():
        if known.get((url, pid)) != p["job_id"]:
            conn.execute(
                "INSERT INTO pages (url, post_id, title, link, job_id, baseline) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(url, post_id) DO UPDATE SET title=excluded.title, link=excluded.link, "
                "job_id=excluded.job_id, baseline=excluded.baseline, fails=0, last_error='', dead_noted=0",
                (url, pid, p["title"], p["link"], p["job_id"], p["text"]))
            added += 1
        else:
            conn.execute("UPDATE pages SET title=?, link=? WHERE url=? AND post_id=?",
                         (p["title"], p["link"], url, pid))
    gone = [k for k in known if k not in pages]
    conn.executemany("DELETE FROM pages WHERE url = ? AND post_id = ?", gone)
    conn.commit()
    return added, len(gone)


# ---------- 비교 ----------

def _lines(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) < 4 or _DATE_ONLY.match(line) or (len(line) <= NOISE_MAX_LEN and _NOISE.search(line)):
            continue
        out.append(line)
    return out


def _body(text: str, cap: int) -> list[str]:
    """길이 제한에 걸린 본문이면 끝 TAIL_RATIO를 비교에서 뺀다(앞부분이 늘어 밀려난 줄을 '삭제'로 보지 않게)."""
    lines = _lines(text)
    if len(text) >= cap - 50:
        lines = lines[: max(1, int(len(lines) * (1 - TAIL_RATIO)))]
    return lines


def diff_lines(old: str, new: str, cap: int = DEFAULT_MAX_CHARS) -> tuple[list[str], list[str]]:
    """(없어진 줄, 새 줄). 순서만 바뀐 줄·사소한 줄·잘린 끝부분은 무시."""
    old_l, new_l = _body(old, cap), _body(new, cap)
    old_all, new_all = set(_lines(old)), set(_lines(new))   # 잘린 끝부분에 있어도 '있는 줄'로 인정
    removed = [l for l in dict.fromkeys(old_l) if l not in new_all]
    added = [l for l in dict.fromkeys(new_l) if l not in old_all]
    return removed, added


def _clip(lines: Sequence[str], limit: int) -> str:
    out, size = [], 0
    for l in lines:
        if size + len(l) > limit:
            out.append(f"…(이하 {len(lines) - len(out)}줄 생략)")
            break
        out.append(l)
        size += len(l) + 1
    return "\n".join(out)


def build_prompt(title: str, post_text: str, url: str, removed: Sequence[str], added: Sequence[str]) -> str:
    return (f"[블로그 글 제목] {title}\n\n[블로그 글 본문]\n{post_text[:MAX_POST_CHARS]}\n\n"
            f"[근거 페이지] {url}\n\n[페이지에서 없어진 줄]\n{_clip(removed, MAX_DIFF_CHARS) or '(없음)'}\n\n"
            f"[페이지에 새로 생긴 줄]\n{_clip(added, MAX_DIFF_CHARS) or '(없음)'}")


def judge(generator: Any, title: str, post_text: str, url: str,
          removed: Sequence[str], added: Sequence[str]) -> tuple[ChangeVerdict, str]:
    raw, model = generator.structured(build_prompt(title, post_text, url, removed, added),
                                      ChangeVerdict, SYSTEM_INSTRUCTION)
    try:
        return ChangeVerdict.model_validate_json(raw), model
    except ValidationError as exc:
        raise ValueError(f"판정 형식 오류: {exc.errors()[:2]}") from exc


def check_pages(deps: Deps, conn: sqlite3.Connection, limit: int = MAX_PER_RUN) -> tuple[int, int, int, int]:
    """가장 오래 안 본 주소부터 limit곳을 한 번씩 읽어, 그 주소를 쓰는 글마다 자기 기준과 비교한다.
    (읽으러 간 주소 수, 읽은 수, 바뀐 글·페이지 수, 영향 있음 수)."""
    urls = [r["url"] for r in conn.execute(
        "SELECT url, MAX(last_checked IS NULL) AS fresh, MIN(last_checked) AS oldest FROM pages "
        "GROUP BY url ORDER BY fresh DESC, oldest, url LIMIT ?", (max(0, limit),))]
    post_texts: dict[int, str] = {}
    tried = checked = changed = affecting = 0
    for n, url in enumerate(urls):
        if n and deps.pause:
            time.sleep(deps.pause)
        tried += 1
        try:
            new = deps.fetch(url, "S1").text
        except RelayUnavailableError:
            raise                      # 우리 쪽 중계 장애: 페이지 탓이 아니므로 실패로 세지 않고 이번 실행을 멈춘다
        except SourceError as exc:
            conn.execute("UPDATE pages SET last_checked=?, fails=fails+1, last_error=? WHERE url=?",
                         (_now(), str(exc)[:300], url))
            conn.commit()
            continue
        checked += 1
        for r in conn.execute("SELECT * FROM pages WHERE url = ? ORDER BY post_id", (url,)).fetchall():
            removed, added = diff_lines(r["baseline"], new)
            if removed or added:
                changed += 1
                try:
                    if r["post_id"] not in post_texts:
                        post_texts[r["post_id"]] = html_to_text(deps.wp.get_post_content(r["post_id"]))
                    verdict, model = judge(deps.generator, r["title"], post_texts[r["post_id"]], url, removed, added)
                except Exception as exc:  # noqa: BLE001 - 판정 실패면 기준을 그대로 두고 다음 바퀴에 다시
                    log.warning("근거 페이지 변경 판정 실패(%s, 글 %s): %s", url, r["post_id"], exc)
                    conn.execute("UPDATE pages SET last_checked=?, fails=0, last_error=?, dead_noted=0 "
                                 "WHERE url=? AND post_id=?", (_now(), f"판정 실패: {exc}"[:300], url, r["post_id"]))
                    conn.commit()
                    continue
                affecting += verdict.affects
                conn.execute(
                    "INSERT INTO changes (url, post_id, job_id, found_at, affects, summary, removed, added, model) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (url, r["post_id"], r["job_id"], _now(), int(verdict.affects), verdict.summary[:300],
                     json.dumps(removed, ensure_ascii=False), json.dumps(added, ensure_ascii=False), model))
            conn.execute("UPDATE pages SET baseline=?, last_checked=?, fails=0, last_error='', dead_noted=0 "
                         "WHERE url=? AND post_id=?", (new, _now(), url, r["post_id"]))
            conn.commit()
    return tried, checked, changed, affecting


# ---------- 알림 ----------

def pending(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """알릴 변화: 영향 있음, 아직 안 알림, 그 글이 아직 같은 작업 기준(그사이 갱신했으면 이미 반영된 것)."""
    return conn.execute(
        "SELECT c.*, p.title, p.link FROM changes c JOIN pages p "
        "ON p.url = c.url AND p.post_id = c.post_id AND p.job_id = c.job_id "
        "WHERE c.affects = 1 AND c.notified IS NULL ORDER BY c.post_id, c.id").fetchall()


def unreachable(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """MAX_FAILS번 이상 연속 실패했고 아직 알리지 않은 페이지(다시 열릴 때까지 한 번만 알림)."""
    return conn.execute("SELECT * FROM pages WHERE fails >= ? AND dead_noted = 0 ORDER BY post_id, url",
                        (MAX_FAILS,)).fetchall()


FOOTER = ("\n바뀐 내용을 반영해 고친 글을 자동으로 만들어 승인 요청을 드립니다. 확실한 정보만 바뀐 글은 다음 날 아침 8시에 "
          "원래 글에 자동 반영하고, 아니면 직접 [발행]을 눌러야 합니다. 자동 등록을 못 한 글은 \"갱신 80\"처럼 글 번호를 보내 주세요.")


def message(changes: Sequence[sqlite3.Row], dead: Sequence[sqlite3.Row], status: dict[int, str] | None = None
            ) -> tuple[str, list[int], list[tuple[str, int]]]:
    """(보낼 글, 실린 변화 번호, 실린 불통 페이지). 길이 제한에 안 들어간 항목은 싣지 않고 다음 알림으로 미룬다."""
    budget = MAX_MESSAGE - len(FOOTER) - 120
    lines: list[str] = []
    sent_c: list[int] = []
    last = None
    for c in changes:
        block = []
        if c["post_id"] != last:
            block.append(f"\n글 {c['post_id']}번 {(c['title'] or '')[:50]}\n {c['link'] or ''}")
            if (status or {}).get(c["post_id"]):
                block.append(f" → {status[c['post_id']][:200]}")
        block.append(f" - {c['summary'][:150]}\n   페이지: {c['url']}")
        size = sum(len(b) + 1 for b in block)
        if sum(len(l) + 1 for l in lines) + size > budget:
            break
        lines += block
        sent_c.append(c["id"])
        last = c["post_id"]
    dead_lines: list[str] = []
    sent_d: list[tuple[str, int]] = []
    for d in dead:
        line = f" - 글 {d['post_id']}번: {d['url']}"
        if sum(len(l) + 1 for l in lines + dead_lines) + len(line) + 80 > budget:
            break
        dead_lines.append(line)
        sent_d.append((d["url"], d["post_id"]))
    out = []
    if sent_c:
        out.append(f"[근거 변경] 글이 근거로 쓴 공식 페이지에서 글 내용과 관련된 변화가 {len(sent_c)}건 있습니다")
        out += lines
        if len(sent_c) < len(changes):
            out.append(f"(나머지 {len(changes) - len(sent_c)}건은 다음 알림에)")
        out.append(FOOTER)
    if sent_d:
        out.append(f"\n[근거 변경] {MAX_FAILS}번 연속 열리지 않은 근거 페이지 {len(sent_d)}곳(주소가 바뀌었을 수 있음):")
        out += dead_lines
    return "\n".join(out).strip(), sent_c, sent_d


def _auto_entry(rows: Sequence[sqlite3.Row]) -> dict[str, Any]:
    """갱신 작업 target["auto"]: 바뀐 페이지 주소와 바뀐 줄(자동 반영 판정·꼭 읽을 자료)."""
    return {"urls": list(dict.fromkeys(r["url"] for r in rows)), "change_ids": [int(r["id"]) for r in rows],
            "changes": [{"url": r["url"], "summary": r["summary"],
                         "removed": json.loads(r["removed"] or "[]"), "added": json.loads(r["added"] or "[]")}
                        for r in rows]}


def register_refreshes(deps: Deps, changes: Sequence[sqlite3.Row]) -> dict[int, str]:
    """영향 있는 변화가 있는 글마다 갱신 작업을 자동 등록한다(2026-11-21 사용자 결정). {글 번호: 알림에 붙일 한 줄}.
    하루 상한·같은 글 진행 중이면 refresh.request_refresh가 거절하고 그 이유를 적는다. 실패해도 알림은 보낸다."""
    by_post: dict[int, list[sqlite3.Row]] = {}
    for c in changes:
        by_post.setdefault(int(c["post_id"]), []).append(c)
    if not by_post:
        return {}
    out: dict[int, str] = {}
    try:
        jobs_conn = deps.jobs_conn_factory()
    except Exception as exc:  # noqa: BLE001
        log.warning("갱신 자동 등록: 작업 장부를 열지 못함: %s", exc)
        return {pid: "자동 등록 못 함(작업 장부 오류)" for pid in by_post}
    try:
        # 이미 자동 등록에 쓴 변화(그 작업을 취소했어도)는 다시 등록하지 않는다(알림 재시도·취소 뒤 중복 방지)
        used = {int(i) for j in db.list_jobs(jobs_conn, limit=-1)
                for i in ((refresh.target_of(j) or {}).get("auto") or {}).get("change_ids") or []}
        for pid, rows in by_post.items():
            rows = [r for r in rows if int(r["id"]) not in used]
            if not rows:
                out[pid] = "이미 자동 등록한 변화입니다(다시 고치려면 '갱신 번호')"
                continue
            try:
                checks = freshness.checks_for(deps.data_dir, pid) + checks_for(deps.data_dir, pid)
                job_id, note = refresh.request_refresh(jobs_conn, deps.wp, pid, source="cli", checks=checks,
                                                       auto=_auto_entry(rows))
            except Exception as exc:  # noqa: BLE001 - 한 글 실패가 다른 글·알림을 막지 않게
                log.warning("갱신 자동 등록 실패(글 %s): %s", pid, exc)
                out[pid] = "자동 등록 못 함(오류)"
                continue
            out[pid] = f"고치는 작업 {job_id}번 자동 등록" if job_id else f"자동 등록 못 함: {note}"
    finally:
        jobs_conn.close()
    return out


def notify(deps: Deps, conn: sqlite3.Connection, today: str) -> int:
    """알릴 것이 있으면 한 통으로 보내고, 실린 항목만 '알림' 표시. 보내기 실패면 표시하지 않는다(예외 전파).
    보내기 전에 영향 있는 글마다 갱신 작업을 자동 등록하고 결과를 글별로 적는다."""
    changes = pending(conn)
    status = register_refreshes(deps, changes) if deps.auto_refresh else {}
    text, sent_c, sent_d = message(changes, unreachable(conn), status)
    if not text:
        return 0
    deps.telegram.send_text(text)
    conn.executemany("UPDATE changes SET notified = ? WHERE id = ?", [(today, i) for i in sent_c])
    conn.executemany("UPDATE pages SET dead_noted = 1 WHERE url = ? AND post_id = ?", sent_d)
    conn.commit()
    return len(sent_c) + len(sent_d)


def checks_for(data_dir: Path, post_id: int) -> list[str]:
    """갱신 글쓰기 힌트: 지금 글이 쓴 자료(같은 작업) 이후 근거 페이지에서 찾은 '영향 있는 변화'.
    글을 갱신하면 기준 작업이 바뀌어 예전 변화는 빠진다. 장부가 없거나 못 읽으면 빈 목록."""
    path = store_path(data_dir)
    if not path.exists():
        return []
    try:
        conn = connect(path)
        try:
            rows = conn.execute(
                "SELECT c.summary, c.url FROM changes c "
                "JOIN pages p ON p.url = c.url AND p.post_id = c.post_id AND p.job_id = c.job_id "
                "WHERE c.post_id = ? AND c.affects = 1 ORDER BY c.id DESC LIMIT 5", (int(post_id),)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        log.warning("근거 변경 장부에서 글 %s번 항목을 못 읽음: %s", post_id, exc)
        return []
    return [f"근거 페이지 변경: {r['summary']} ({r['url']})" for r in rows]


# ---------- 하루 한 번 ----------

@contextmanager
def _exclusive(data_dir: Path) -> Iterator[bool]:
    path = Path(data_dir) / "locks" / "source_watch.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _work(deps: Deps, conn: sqlite3.Connection, today: str, send: bool, limit: int) -> tuple[str, int]:
    """(결과 한 줄, 읽으러 간 주소 수)."""
    published = {int(p["id"]): p for p in deps.wp.list_published_posts()}
    jobs_conn = deps.jobs_conn_factory()
    try:
        added, gone = sync_pages(conn, watched_pages(jobs_conn, published))
    finally:
        jobs_conn.close()
    tried, checked, changed, affecting = check_pages(deps, conn, limit)
    sent = notify(deps, conn, today) if send else 0
    total = conn.execute("SELECT COUNT(DISTINCT url) FROM pages").fetchone()[0]
    return (f"감시 페이지 {total}곳(새로 {added}, 뺌 {gone}), 확인 {checked}/{tried}곳, 바뀜 {changed}건, "
            f"글에 영향 {affecting}건, 알림 {sent}건"), tried


def _counting(fetch: Callable[[str, str], Any], seen: dict[str, int]) -> Callable[[str, str], Any]:
    def wrapped(url: str, source_id: str) -> Any:
        seen["n"] += 1
        return fetch(url, source_id)
    return wrapped


def run(deps: Deps, send: bool = True, limit: int = MAX_PER_RUN, now: datetime | None = None) -> str:
    """지금 한 번(수동 실행용, 하루 한 번 제한 없음). send=False면 알림 없이 확인만(알릴 것은 남겨 둠)."""
    today = (now or datetime.now(timezone.utc)).astimezone(KST).date().isoformat()
    with _exclusive(deps.data_dir) as ok:
        if not ok:
            return "다른 감시가 진행 중이라 건너뜀"
        conn = connect(store_path(deps.data_dir))
        try:
            return _work(deps, conn, today, send, limit)[0]
        finally:
            conn.close()


def run_if_due(deps: Deps, now: datetime | None = None) -> str | None:
    """실행기가 주기적으로 부른다. RUN_HOUR시 전, 오늘 끝냈거나 MAX_TRIES_PER_DAY번 시도했으면 None."""
    local = (now or datetime.now(timezone.utc)).astimezone(KST)
    if local.hour < RUN_HOUR:
        return None
    day = local.date().isoformat()
    with _exclusive(deps.data_dir) as ok:
        if not ok:
            return None
        conn = connect(store_path(deps.data_dir))
        try:
            row = conn.execute("SELECT tries, done, fetched FROM days WHERE day = ?", (day,)).fetchone()
            if row and (row["done"] or row["tries"] >= MAX_TRIES_PER_DAY):
                return None
            conn.execute("INSERT INTO days (day, tries) VALUES (?, 1) "
                         "ON CONFLICT(day) DO UPDATE SET tries = tries + 1", (day,))
            conn.commit()
            budget = MAX_PER_RUN - (row["fetched"] if row else 0)   # 다시 시도해도 하루 MAX_PER_RUN곳까지만 읽는다
            seen = {"n": 0}
            counted = Deps(**{**deps.__dict__, "fetch": _counting(deps.fetch, seen)})
            try:
                note, _ = _work(counted, conn, day, True, budget)
            except Exception as exc:  # noqa: BLE001 - 일시 오류: 10분 뒤 다시(하루 횟수 안에서)
                log.warning("근거 페이지 감시 실패(10분 뒤 다시): %s", exc)
                conn.rollback()
                conn.execute("UPDATE days SET note = ?, fetched = fetched + ? WHERE day = ?",
                             (f"실패: {exc}"[:300], seen["n"], day))
                conn.commit()
                return None
            conn.execute("UPDATE days SET done = 1, note = ?, fetched = fetched + ? WHERE day = ?",
                         (note, seen["n"], day))
            conn.commit()
            return note
        finally:
            conn.close()
