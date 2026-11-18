"""발행된 글의 '다시 볼 날짜'를 정하고, 날짜가 지난 글을 텔레그램으로 알린다.

왜: 지원금·세금 글은 해가 바뀌거나 고시가 새로 나오면 숫자가 틀려진다. 구글·네이버 모두 오래되어 틀린 정보를
낮게 평가하므로, 고칠 때가 된 글을 사람이 놓치지 않게 알린다. 글은 여기서 고치지 않는다(알림만).

동작(하루 한 번, 한국 시간 RUN_HOUR시 이후, 실행기가 부름):
1. 워드프레스에서 발행된 글 목록을 읽는다.
2. 아직 날짜가 없거나 그 뒤 글이 수정된 글은 Gemini가 본문을 읽고 '기한이 있는 정보'와 다시 볼 날짜를 정한다
   (기존 글 채우기와 새 글 처리가 같은 길이다). 하루 MAX_PLANS_PER_RUN편까지만.
3. 다시 볼 날짜가 지난 글을 한 메시지로 알린다. 같은 글은 REMIND_DAYS일마다 한 번만 다시 알린다.

장부: data/freshness.sqlite3 (지워져도 다음 날 다시 만든다. Gemini 호출만 다시 든다).
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
import fcntl
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol, Sequence
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
CHECK_SECONDS = 600            # 실행기가 10분마다 확인
RUN_HOUR = 10                  # 오전 8시 자동 발행과 겹치지 않게
MAX_PLANS_PER_RUN = 30         # 하루 Gemini 호출 상한(글 수가 늘어도 사용량이 튀지 않게)
REMIND_DAYS = 7                # 고치지 않고 두면 일주일 뒤 다시 알림
MAX_NOTIFY = 10                # 한 메시지에 이만큼만. 나머지는 다음 날
MAX_MESSAGE = 3800             # 텔레그램은 4천 자에서 자르므로 이 길이 안에 들어가는 글만 싣는다
MAX_TRIES_PER_DAY = 3          # 하루 점검이 실패해도 이만큼만 다시 시도(Gemini·텔레그램 과다 호출 방지)
MAX_POST_FAILURES = 3          # 같은 글이 이만큼 실패하면 글이 고쳐질 때까지 건너뜀
DEFAULT_DAYS = 365             # 기한 있는 정보를 못 찾은 글도 1년에 한 번은 확인
MAX_DAYS = 400                 # AI가 너무 먼 날짜를 골라도 이 이상 미루지 않음
MAX_TEXT = 15000               # 본문을 이만큼만 AI에 보낸다(글 하나는 보통 4천 자 안팎)

SYSTEM_INSTRUCTION = """너는 한국 생활 제도(세금·지원금·복지·주거·금융) 블로그의 정보 유효기간 점검자다.
글 본문을 읽고, 시간이 지나면 틀려질 수 있는 정보를 골라 언제 다시 확인해야 하는지 정한다.

골라야 하는 정보:
- 'OOOO년 기준'처럼 해가 붙은 금액·소득 기준·지원 대상(매년 새 고시가 나오면 바뀜)
- 신청·납부 기간, 마감일(그날이 지나면 글이 '이미 끝난 일정'을 안내하게 됨)
- 법·제도 개편이 예고된 내용(예: '내년부터 폐지', '2027년 시행')
다시 볼 날짜 정하는 법:
- 해마다 바뀌는 기준·금액: 다음 해 기준이 보통 발표되는 무렵. 모르면 다음 해 1월 5일
- 기간·마감일: 마감 다음 날
- 예고된 개편: 시행일
- 마감이 이미 지난 정보도 고른다(마감 다음 날을 그대로 적는다)
- 글 작성일보다 이른 날짜, 본문에 근거 없는 날짜는 쓰지 않는다
바뀌지 않는 일반 설명(제도 정의, 신청 절차의 순서 등)은 고르지 않는다. 고를 것이 없으면 빈 목록."""


class FreshItem(BaseModel):
    what: str = Field(description="다시 확인할 정보 한 줄. 예: '2026년 아동수당 지급액 월 10만~13만 원'")
    recheck_on: str = Field(description="다시 확인할 날짜 YYYY-MM-DD")
    why: str = Field(description="그날 확인하는 이유 한 줄. 예: '2027년 지급액 고시 확인'")


class FreshPlan(BaseModel):
    items: list[FreshItem] = Field(default_factory=list, description="기한이 있는 정보 0~5개")


class Generator(Protocol):
    def structured(self, prompt: str, schema: type, system: str) -> tuple[str, str]: ...


@dataclass
class Deps:
    wp: Any                      # list_published_posts(), get_post_content(post_id)
    generator: Generator
    telegram: Any                # send_text(text)
    data_dir: Path


# ---------- 장부 ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    post_id       INTEGER PRIMARY KEY,
    link          TEXT NOT NULL,
    title         TEXT NOT NULL,
    modified      TEXT NOT NULL,      -- 날짜를 정할 때 본 워드프레스 수정 시각(바뀌면 다시 정함)
    recheck_on    TEXT NOT NULL,      -- YYYY-MM-DD
    items         TEXT NOT NULL,      -- FreshItem 목록(JSON)
    model         TEXT NOT NULL DEFAULT '',
    planned_at    TEXT NOT NULL,
    last_notified TEXT                -- YYYY-MM-DD
);
CREATE TABLE IF NOT EXISTS days (
    day         TEXT PRIMARY KEY,     -- 한국 날짜 YYYY-MM-DD
    tries       INTEGER NOT NULL,     -- 오늘 시작한 횟수(실패해도 MAX_TRIES_PER_DAY번까지만)
    done        INTEGER NOT NULL DEFAULT 0,
    planned     INTEGER NOT NULL DEFAULT 0,   -- 오늘 AI에게 물어본 글 수(재시도 포함 하루 MAX_PLANS_PER_RUN까지)
    note        TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS failures (
    post_id     INTEGER PRIMARY KEY,
    modified    TEXT NOT NULL,        -- 실패했을 때의 수정 시각(글이 고쳐지면 다시 시도)
    tries       INTEGER NOT NULL,
    last_error  TEXT NOT NULL
);
DROP TABLE IF EXISTS runs;
"""


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    if "planned" not in {r["name"] for r in conn.execute("PRAGMA table_info(days)")}:   # 11-18 초기 장부
        conn.execute("ALTER TABLE days ADD COLUMN planned INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    return conn


def store_path(data_dir: Path) -> Path:
    return Path(data_dir) / "freshness.sqlite3"


# ---------- 날짜 정하기 ----------

def html_to_text(html: str) -> str:
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"</(p|h[1-6]|li|tr|div|figcaption)>|<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"[ \t ]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def build_prompt(title: str, text: str, published: date, modified: date, today: date) -> str:
    return (f"[오늘] {today.isoformat()}\n[글 작성일] {published.isoformat()}\n[마지막 수정일] {modified.isoformat()}\n"
            f"[제목] {title}\n[본문]\n{text[:MAX_TEXT]}")


def decide(items: Sequence[FreshItem], published: date, modified: date, today: date) -> tuple[date, list[dict]]:
    """AI 답을 검사해 (다시 볼 날짜, 남긴 항목). 날짜 형식이 틀리거나 작성일보다 이른(근거 없는) 항목은 버린다.

    이미 지난 날짜는 '내일'로 당긴다 — 사진만 고친 글도 지난 마감·기준이 그대로면 바로 알려야 하므로
    수정일이 아니라 작성일로 거른다. 너무 먼 날짜는 '수정일+MAX_DAYS'로 자른다.
    남은 항목이 없으면 수정일+DEFAULT_DAYS(단, 내일 이상)."""
    low = today + timedelta(days=1)
    high = max(modified + timedelta(days=MAX_DAYS), low)
    kept: list[dict] = []
    for it in items:
        try:
            d = date.fromisoformat(it.recheck_on.strip())
        except ValueError:
            continue
        if d < published:
            continue
        d = min(max(d, low), high)
        kept.append({"what": it.what.strip()[:200], "recheck_on": d.isoformat(), "why": it.why.strip()[:200]})
    kept.sort(key=lambda x: x["recheck_on"])
    if kept:
        return date.fromisoformat(kept[0]["recheck_on"]), kept
    return max(modified + timedelta(days=DEFAULT_DAYS), low), []


def plan_post(generator: Generator, title: str, html: str, published: date, modified: date, today: date
              ) -> tuple[date, list[dict], str]:
    raw, model = generator.structured(build_prompt(title, html_to_text(html), published, modified, today),
                                      FreshPlan, SYSTEM_INSTRUCTION)
    try:
        plan = FreshPlan.model_validate_json(raw)
    except ValidationError as exc:
        raise ValueError(f"유효기간 응답 형식 오류: {exc.errors()[:1]}") from exc
    recheck, kept = decide(plan.items, published, modified, today)
    return recheck, kept, model


def _kst_date(gmt: str) -> date:
    """워드프레스 *_gmt 값(시간대 표시 없음)을 한국 날짜로."""
    return datetime.fromisoformat(gmt).replace(tzinfo=timezone.utc).astimezone(KST).date()


def sync_plans(deps: Deps, conn: sqlite3.Connection, today: date, limit: int = MAX_PLANS_PER_RUN,
               day: str | None = None) -> tuple[list[str], list[str]]:
    """(새로 날짜를 정한 글 제목, 문제 안내). 발행이 내려간 글은 장부에서 지운다.

    day를 주면 그날 이미 물어본 글 수(days.planned)를 limit에서 빼고, 물어볼 때마다 늘린다
    — 알림 실패로 재시도해도 하루 상한을 넘지 않게.

    같은 수정 상태에서 MAX_POST_FAILURES번 실패한 글은 건너뛴다(계속 실패하는 글이 다른 글을 막지 않게)."""
    posts = deps.wp.list_published_posts()
    live = {int(p["id"]) for p in posts}
    known = {r["post_id"]: r["modified"] for r in conn.execute("SELECT post_id, modified FROM posts")}
    fails = {r["post_id"]: r for r in conn.execute("SELECT * FROM failures")}
    gone = [pid for pid in set(known) | set(fails) if pid not in live]
    if gone:
        conn.executemany("DELETE FROM posts WHERE post_id = ?", [(pid,) for pid in gone])
        conn.executemany("DELETE FROM failures WHERE post_id = ?", [(pid,) for pid in gone])
        conn.commit()

    def blocked(p: dict) -> bool:
        f = fails.get(int(p["id"]))
        return bool(f and f["modified"] == p["modified_gmt"] and f["tries"] >= MAX_POST_FAILURES)

    todo = [p for p in posts if known.get(int(p["id"])) != p["modified_gmt"]]
    skipped = [p for p in todo if blocked(p)]
    todo = [p for p in todo if not blocked(p)]
    todo.sort(key=lambda p: p["modified_gmt"])           # 오래된 글부터
    if day is not None:
        used = conn.execute("SELECT planned FROM days WHERE day = ?", (day,)).fetchone()
        limit = max(0, limit - (used["planned"] if used else 0))
    planned, problems = [], []
    for p in todo[:limit]:
        pid, title = int(p["id"]), p["title"]
        if day is not None:
            conn.execute("UPDATE days SET planned = planned + 1 WHERE day = ?", (day,))
        try:
            html = deps.wp.get_post_content(pid)
            recheck, items, model = plan_post(deps.generator, title, html, _kst_date(p["date_gmt"]),
                                              _kst_date(p["modified_gmt"]), today)
        except Exception as exc:  # noqa: BLE001 - 한 글이 실패해도 나머지는 계속
            log.warning("글 %s번 유효기간 판단 실패: %s", pid, exc)
            problems.append(f"{title[:30]}: {exc.__class__.__name__}")
            f = fails.get(pid)
            tries = f["tries"] + 1 if f and f["modified"] == p["modified_gmt"] else 1
            conn.execute("INSERT OR REPLACE INTO failures (post_id, modified, tries, last_error) VALUES (?, ?, ?, ?)",
                         (pid, p["modified_gmt"], tries, str(exc)[:300]))
            conn.commit()
            continue
        conn.execute(
            "INSERT INTO posts (post_id, link, title, modified, recheck_on, items, model, planned_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(post_id) DO UPDATE SET link=excluded.link, "
            "title=excluded.title, modified=excluded.modified, recheck_on=excluded.recheck_on, "
            "items=excluded.items, model=excluded.model, planned_at=excluded.planned_at, last_notified=NULL",
            (pid, p["link"], title, p["modified_gmt"], recheck.isoformat(), json.dumps(items, ensure_ascii=False),
             model, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        conn.execute("DELETE FROM failures WHERE post_id = ?", (pid,))
        conn.commit()
        planned.append(title)
    if len(todo) > limit:
        problems.append(f"남은 {len(todo) - limit}편은 내일 이어서 판단")
    if skipped:
        problems.append(f"{MAX_POST_FAILURES}번 실패해 건너뛴 글 {len(skipped)}편(글 {', '.join(str(p['id']) for p in skipped[:5])}번)")
    return planned, problems


# ---------- 알림 ----------

def due_posts(conn: sqlite3.Connection, today: date) -> list[sqlite3.Row]:
    again = (today - timedelta(days=REMIND_DAYS)).isoformat()
    return conn.execute(
        "SELECT * FROM posts WHERE recheck_on <= ? AND (last_notified IS NULL OR last_notified <= ?) "
        "ORDER BY last_notified IS NOT NULL, last_notified, recheck_on, post_id",   # 안 알린 글·오래전 알린 글 먼저
        (today.isoformat(), again)).fetchall()


def due_message(rows: Sequence[sqlite3.Row], today: date, more: int = 0) -> str:
    lines = [f"[글 점검] 다시 확인할 때가 된 글 {len(rows) + more}편" + (f" 중 {len(rows)}편" if more else "")]
    for n, r in enumerate(rows, 1):
        items = [i for i in json.loads(r["items"]) if i["recheck_on"] <= today.isoformat()]
        lines.append(f"\n{n}. {r['title'][:60]} (글 {r['post_id']}번, {r['recheck_on']}부터)")
        for i in items[:3] or [{"why": "작성·수정 후 1년 — 숫자·기간이 그대로인지 확인"}]:
            lines.append(f" - {i['why'][:120]}")
        lines.append(f" {r['link']}")
    if more:
        lines.append(f"\n나머지 {more}편은 내일 이어서 알려 드립니다.")
    lines.append("\n고칠 글이 있으면 Claude에게 \"글 OO번 갱신\"이라고 말해 주세요. "
                 f"그대로 두면 {REMIND_DAYS}일 뒤 다시 알려 드립니다.")
    return "\n".join(lines)


def notify_due(deps: Deps, conn: sqlite3.Connection, today: date) -> int:
    """지난 글을 MAX_NOTIFY편·MAX_MESSAGE자 안에서 한 메시지로 알린다. 보낸 글만 '알림'으로 기록(전송 실패면 기록 취소)."""
    rows = due_posts(conn, today)
    if not rows:
        return 0
    shown = rows[:1]                                     # 한 편은 항상(한 편으로 4천 자를 넘지 않음)
    for n in range(2, min(MAX_NOTIFY, len(rows)) + 1):
        if len(due_message(rows[:n], today, more=len(rows) - n)) > MAX_MESSAGE:
            break
        shown = rows[:n]
    conn.executemany("UPDATE posts SET last_notified = ? WHERE post_id = ?",
                     [(today.isoformat(), r["post_id"]) for r in shown])
    try:
        deps.telegram.send_text(due_message(shown, today, more=len(rows) - len(shown)))
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return len(shown)


# ---------- 하루 한 번 ----------

@contextmanager
def _exclusive(data_dir: Path) -> Iterator[bool]:
    """실행기와 수동 실행이 겹치지 않게. 이미 누가 돌고 있으면 False."""
    path = Path(data_dir) / "locks" / "freshness.lock"
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


def _work(deps: Deps, conn: sqlite3.Connection, today: date, notify: bool, day: str | None = None) -> str:
    planned, problems = sync_plans(deps, conn, today, day=day)
    sent = notify_due(deps, conn, today) if notify else 0
    return f"날짜 정함 {len(planned)}편, 알림 {sent}편" + (f", 문제: {'; '.join(problems)}" if problems else "")


def run(deps: Deps, now: datetime | None = None, notify: bool = True) -> str:
    """날짜 정하기와 알림을 지금 한 번 한다(하루 한 번 제한 없음, 수동 실행용). 결과 안내 문장."""
    now = now or datetime.now(timezone.utc)
    with _exclusive(deps.data_dir) as ok:
        if not ok:
            return "다른 점검이 진행 중이라 건너뜀"
        conn = connect(store_path(deps.data_dir))
        try:
            return _work(deps, conn, now.astimezone(KST).date(), notify)
        finally:
            conn.close()


def run_if_due(deps: Deps, now: datetime | None = None) -> str | None:
    """실행기가 주기적으로 부른다. RUN_HOUR시 전, 오늘 이미 끝냈거나 MAX_TRIES_PER_DAY번 시도했으면 None.

    시작할 때 시도 횟수를 먼저 기록하므로, 중간에 실패해도 하루 시도 횟수가 넘치지 않는다.
    이미 날짜를 정한 글은 다시 묻지 않으므로 재시도는 남은 일만 한다."""
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(KST)
    if local.hour < RUN_HOUR:
        return None
    day = local.date().isoformat()
    with _exclusive(deps.data_dir) as ok:
        if not ok:
            return None
        conn = connect(store_path(deps.data_dir))
        try:
            row = conn.execute("SELECT tries, done FROM days WHERE day = ?", (day,)).fetchone()
            if row and (row["done"] or row["tries"] >= MAX_TRIES_PER_DAY):
                return None
            conn.execute("INSERT INTO days (day, tries) VALUES (?, 1) "
                         "ON CONFLICT(day) DO UPDATE SET tries = tries + 1", (day,))
            conn.commit()
            try:
                note = _work(deps, conn, local.date(), notify=True, day=day)
            except Exception as exc:  # noqa: BLE001 - 워드프레스·텔레그램 일시 오류: 10분 뒤 다시(하루 횟수 안에서)
                log.warning("글 점검 실패(10분 뒤 다시): %s", exc)
                conn.rollback()
                conn.execute("UPDATE days SET note = ? WHERE day = ?", (f"실패: {exc}"[:300], day))
                conn.commit()
                return None
            conn.execute("UPDATE days SET done = 1, note = ? WHERE day = ?", (note, day))
            conn.commit()
            return note
        finally:
            conn.close()


def schedule_rows(data_dir: Path) -> list[sqlite3.Row]:
    conn = connect(store_path(data_dir))
    try:
        return conn.execute("SELECT * FROM posts ORDER BY recheck_on, post_id").fetchall()
    finally:
        conn.close()
