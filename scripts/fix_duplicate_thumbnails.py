#!/usr/bin/env python3
"""다른 글과 대표 사진이 겹치는 글의 대표 사진을 새 사진으로 바꾼다.

먼저 쓴 글(작업 번호가 작은 글)은 그대로 두고, 뒤에 쓴 글만 바꾼다. 본문 글자·발행 상태는 건드리지 않는다.
기본은 바꿀 목록만 보여 주고(--apply 없으면 아무것도 바꾸지 않음), --apply를 붙이면 실제로 바꾼다.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db  # noqa: E402
from src.config import load_settings  # noqa: E402
from src.content_schema import MasterContent  # noqa: E402
from src.db import RUNNING_STATUSES  # noqa: E402
from src.pipeline import _pick_json  # noqa: E402
from src.stock_media import StockMediaPicker, _same_series, normalize_blog_image  # noqa: E402
from src.wp_client import WordPressClient  # noqa: E402


def featured_index(blog: list) -> int | None:
    """make_draft와 같은 규칙: 첫 번째 사진이 대표 사진."""
    return next((i for i, p in enumerate(blog) if p), None)


def find_duplicates(jobs: list[dict]) -> list[tuple[dict, int, int, int]]:
    """(작업, 대표 사진 순서, 사진 번호, 먼저 쓴 작업 번호). 작업 번호 순으로 먼저 쓴 글을 남긴다."""
    first_owner: dict[int, int] = {}
    dups = []
    for job in sorted(jobs, key=lambda j: j["id"]):
        blog = (job["checkpoint"].get("media") or {}).get("blog") or []
        idx = featured_index(blog)
        if idx is None or not job.get("post_id"):
            continue
        media_id = blog[idx]["id"]
        if media_id in first_owner:
            dups.append((job, idx, media_id, first_owner[media_id]))
        else:
            first_owner[media_id] = job["id"]
    return dups


def busy_jobs(conn) -> list[int]:
    """실행기가 손댈 수 있는 작업(대기·진행 중). 있으면 사진 교체와 엇갈릴 수 있어 멈춘다."""
    return [j["id"] for j in db.list_jobs(conn, limit=10_000) if j["status"] in ("queued", *RUNNING_STATUSES)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="실제로 바꾼다(없으면 목록만)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    settings = load_settings()
    conn = db.connect(settings.db_path)
    dups = find_duplicates(db.list_jobs(conn, limit=10_000))     # 겹침은 모든 작업으로 판단
    if not dups:
        print("대표 사진이 겹치는 글이 없습니다")
        return 0

    picker = StockMediaPicker.from_settings(settings)
    wp = WordPressClient.from_settings(settings) if args.apply else None
    avoid = db.used_blog_media_ids(conn)          # 모든 글이 쓴 사진(이번에 고른 새 사진도 계속 추가)
    failed = 0
    for job, idx, old_pixabay, owner in dups:
        if args.apply and (busy := busy_jobs(conn)):
            print(f"진행 중이거나 대기 중인 작업이 있어 멈춥니다(작업 {busy}). 끝난 뒤 다시 실행하세요")
            return 1
        job = db.get_job(conn, job["id"])                         # 최신 장부로 다시 읽기
        if job["status"] in RUNNING_STATUSES:
            print(f"작업 {job['id']}: 진행 중이라 이번에는 건너뜀")
            failed += 1
            continue
        blog_now = (job["checkpoint"].get("media") or {}).get("blog") or []
        if featured_index(blog_now) != idx or blog_now[idx]["id"] != old_pixabay:
            print(f"작업 {job['id']}: 그사이 사진이 바뀌어 건너뜀")
            continue
        content = MasterContent.model_validate(job["checkpoint"]["content"]["content"])
        section = content.sections[idx]
        blog = job["checkpoint"]["media"]["blog"]
        same_post = {p["id"] for p in blog if p}
        pick = picker.pick_blog_image(section.image_query or section.heading, used=set(same_post), avoid=avoid)
        if not pick or _same_series(pick.media_id, avoid):
            print(f"작업 {job['id']}(글 {job['post_id']}): 겹치지 않는 새 사진을 못 찾음 — 그대로 둠")
            failed += 1
            continue
        avoid.add(pick.media_id)
        print(f"작업 {job['id']}(글 {job['post_id']}) {job['title']}\n"
              f"  사진 {old_pixabay}(작업 {owner}과 겹침) → {pick.media_id} ({pick.page_url})")
        if not args.apply:
            continue
        current = wp._request("GET", f"/posts/{int(job['post_id'])}", params={"_fields": "featured_media"})
        fitted = normalize_blog_image(pick.path, settings.data_dir / "media_cache" / "blog")
        alt = (section.image_alt or section.heading).strip()

        def record(new, job=job, idx=idx, pick=pick):
            # 대표 사진이 바뀐 직후, 옛 사진을 지우기 전에 장부부터 맞춘다
            media = {**job["checkpoint"]["media"]}
            media["blog"] = [*media["blog"]]
            media["blog"][idx] = _pick_json(pick)
            # 장부 저장은 한 번(한 트랜잭션)만. draft.featured(처음 저장 때의 첨부 번호)는 어디서도 읽지 않아 그대로 둔다
            db.save_checkpoint(conn, job["id"], "media", media)

        new, deleted = wp.replace_featured(job["id"], job["post_id"], idx, fitted, int(current["featured_media"]),
                                           alt=alt, caption=pick.credit, on_switched=record)
        print(f"  바꿈: 워드프레스 사진 {current['featured_media']} → {new.id}"
              + ("" if deleted else " (옛 사진은 다른 곳에서 쓰여 남김: 화면 다시 만들기 전에 직접 정리 필요)"))
    if not args.apply:
        print("\n(목록만 보여 줬습니다. 실제로 바꾸려면 --apply)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
