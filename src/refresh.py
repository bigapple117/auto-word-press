"""이미 발행된 글을 같은 주소에서 고쳐 쓰기(글 유효기간 점검 3단계).

흐름: '갱신 80'(텔레그램) 또는 `jobctl refresh 80` → 갱신 작업 등록(쇼츠 없는 blog 모드)
→ 새 글과 똑같이 자료 찾기·글 쓰기·검토·사실 확인 → **따로 만든 임시 글**에 저장·캡처해 승인 요청
→ [발행]을 누르면 그 임시 글의 제목·본문·요약·사진·분류·태그를 원래 글에 덮어쓰고 임시 글은 휴지통으로.

- 원래 글의 주소(slug)·작성일은 보내지 않으므로 그대로다. 승인 전에는 원래 글을 전혀 건드리지 않는다.
- 아침 자동 발행·주제 자동 등록 대기 수에는 섞이지 않는다(사람이 직접 승인한 갱신만 반영).
- 하루 MAX_REFRESH_PER_DAY건까지만 등록(새 글 발행 상한과 별도).
"""
from __future__ import annotations

import fcntl
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from src import db

KST = ZoneInfo("Asia/Seoul")
PREFIX = "refresh:"
MAX_REFRESH_PER_DAY = 3
FINISHED = ("published", "canceled", "failed")
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d\d(?:년)?(?!\d)\s*")


def is_refresh(job: dict[str, Any]) -> bool:
    return str(job.get("source_ref") or "").startswith(PREFIX)


def target_of(job: dict[str, Any]) -> dict[str, Any] | None:
    """갱신 작업이면 원래 글 정보 {post_id, link, title}, 아니면 None."""
    if not is_refresh(job):
        return None
    return (job.get("checkpoint") or {}).get("target")


def topic_from_title(title: str) -> str:
    """원래 제목에서 연도를 뺀 것을 주제로(옛 연도로 자료를 찾지 않게)."""
    topic = re.sub(r"\s+", " ", _YEAR.sub("", title)).strip()
    return topic or title.strip()


def _created_kst_day(job: dict[str, Any]) -> str:
    return datetime.fromisoformat(job["created_at"]).astimezone(KST).date().isoformat()


@contextmanager
def lock(conn) -> Iterator[None]:
    """확인과 등록 사이에 다른 요청(텔레그램·명령줄)이 끼어들지 않게. 메모리 장부(시험)는 잠그지 않는다."""
    path = next((r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main"), "")
    if not path:
        yield
        return
    with open(Path(path).with_name("refresh.lock"), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _active_for(conn, post_id: int, exclude_job_id: int | None = None) -> dict[str, Any] | None:
    for j in db.list_jobs(conn, limit=-1):
        t = target_of(j) or {}
        if (j["id"] != exclude_job_id and int(t.get("post_id") or 0) == int(post_id)
                and j["status"] not in FINISHED):
            return j
    return None


def rework_blocker(conn, wp, job: dict[str, Any]) -> str | None:
    """갱신 작업을 다시 쓰기·재시도·취소해도 되는지. 안 되면 이유(되면 None).

    - 이미 원래 글에 반영됐으면 안 된다(다시 쓰기는 원래 글이 쓰는 사진을 지운다). 원래 글을 비공개로
      돌렸어도 반영 표시가 있으면 막는다.
    - 확인과 상태 변경 사이에 새 요청이 끼어들지 않게 부르는 쪽이 lock(conn) 안에서 부른다.
    - 같은 글에 다른 갱신 작업이 진행 중이면 다시 살리지 않는다."""
    target = target_of(job)
    if not target:
        return None
    if wp.refresh_marked(job["id"], target["post_id"]):
        return f"작업 {job['id']}번은 이미 글 {target['post_id']}번에 반영됐습니다. 새 갱신이 필요하면 '갱신 {target['post_id']}'로 새로 요청하세요"
    other = _active_for(conn, target["post_id"], exclude_job_id=job["id"])
    if other:
        return f"글 {target['post_id']}번은 갱신 작업 {other['id']}번이 진행 중입니다"
    return None


def request_refresh(conn, wp, post_id: int, source: str = "cli", now: datetime | None = None) -> tuple[int | None, str]:
    """(등록한 작업 번호 또는 None, 사용자에게 보일 안내)."""
    now = now or datetime.now(timezone.utc)
    post = wp.get_post(post_id)
    if post is None or post.status != "publish":
        return None, f"글 {post_id}번은 발행된 글이 아니라 갱신할 수 없습니다"
    title = wp.get_post_title(post_id)
    with lock(conn):
        other = _active_for(conn, post_id)
        if other:
            return None, f"글 {post_id}번은 이미 갱신 작업 {other['id']}번이 진행 중입니다"
        today = now.astimezone(KST).date().isoformat()
        if sum(is_refresh(j) and _created_kst_day(j) == today for j in db.list_jobs(conn, limit=-1)) >= MAX_REFRESH_PER_DAY:
            return None, f"오늘 갱신 요청은 {MAX_REFRESH_PER_DAY}건까지입니다. 내일 다시 보내 주세요"
        target = {"post_id": int(post_id), "link": post.link, "title": title}
        job_id = db.create_job(conn, topic_from_title(title), mode="blog", source=source,
                               source_ref=f"{PREFIX}{int(post_id)}:{now.isoformat(timespec='seconds')}",
                               checkpoint={"target": target})
    return job_id, (f"글 {post_id}번 갱신 작업 {job_id}번을 등록했습니다. 자료를 다시 찾아 고친 글로 승인 요청을 보내 드립니다"
                    "(보통 5~10분). [발행]을 눌러야 원래 글이 바뀝니다")


def approval_note(target: dict[str, Any]) -> str:
    return (f"[갱신] 발행하면 글 {target['post_id']}번({target['link']})의 내용이 이 글로 바뀝니다. "
            "주소·작성일은 그대로이고, 이 임시 글은 휴지통으로 옮깁니다")
