#!/usr/bin/env python3
"""이미 올린 글의 대표 사진을 AI(Gemini)가 다시 보고, 더 나은 사진이 있으면 바꾼다.

새 글은 만들 때 AI가 대표 사진을 고른다(src/image_judge.py, 2026-11-21). 그 전에 올린 글은 검색 순서 첫 사진이라
주제와 어긋나거나 영어 큰 글씨·상표·얼굴이 보이는 사진이 섞여 있다(광고 달기 전 정리).

두 단계로 나눈다.
1) 기본(--plan 파일 경로): 글마다 '지금 사진 + 다른 글이 안 쓴 새 후보 3장'을 AI에게 보여 주고, 지금 사진보다 나은
   후보를 고른 글만 계획 파일(JSON)에 적는다. 워드프레스는 건드리지 않는다. 비교 그림도 만든다.
2) --apply 계획 파일: 계획에 적힌 그대로 대표 사진만 바꾼다(본문 글자·발행 상태는 그대로, 옛 사진은 다른 곳에서
   안 쓰면 지움). 판정을 다시 하지 않으므로 사람이 본 계획과 실제 교체가 같다.

대표 사진이 본문에도 들어 있는 글(2026-11-17 이후 만든 글)은 건너뛴다. 대표 사진만 바꾸면 본문의 같은 이름 사진이
남아 이름이 겹치기 때문이다(그런 글은 본문 사진까지 함께 바꾸는 별도 작업이 필요).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from src import db  # noqa: E402
from src.config import load_settings  # noqa: E402
from src.content_schema import MasterContent  # noqa: E402
from src.db import RUNNING_STATUSES  # noqa: E402
from src.image_judge import GeminiImageJudge  # noqa: E402
from src.pipeline import _pick_json  # noqa: E402
from src.refresh import is_refresh  # noqa: E402
from src.stock_media import MediaPick, StockMediaPicker, _same_series, normalize_blog_image  # noqa: E402
from src.wp_client import WordPressClient, marker_comment  # noqa: E402

TARGET_STATUSES = ("published", "awaiting_approval")


def featured_index(blog: list) -> int | None:
    """make_draft와 같은 규칙: 첫 번째 사진이 대표 사진."""
    return next((i for i, p in enumerate(blog) if p), None)


def small_copy(src: Path, dest: Path) -> Path:
    """AI에게 보여 줄 작은 사본(가로 640). 새 후보 미리보기와 같은 크기로 맞춘다."""
    if not dest.exists():
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((640, 640))
            im.save(dest, "JPEG", quality=85)
    return dest


def busy_jobs(conn) -> list[int]:
    return [j["id"] for j in db.list_jobs(conn, limit=10_000)
            if j["status"] in ("queued", "publishing", *RUNNING_STATUSES)]


def post_state(wp, job: dict, idx: int) -> tuple[str | None, int]:
    """(건너뛸 이유 또는 None, 지금 워드프레스 대표 사진 번호).
    휴지통 글, 이 작업이 쓴 글이 아닌 경우(갱신으로 다른 작업 글이 됨), 본문에 대표 사진이 든 글은 건너뛴다."""
    post = wp._request("GET", f"/posts/{int(job['post_id'])}",
                       params={"context": "edit", "_fields": "status,featured_media,content"})
    raw = (post.get("content") or {}).get("raw") or ""
    if post.get("status") == "trash":
        return "휴지통 글", 0
    if marker_comment(job["id"]) not in raw:
        return "이 작업의 글이 아님(갱신 등)", 0
    if f"jcjob-{job['id']}-{idx}" in raw:
        return "본문에도 대표 사진이 있음", 0
    return None, int(post.get("featured_media") or 0)


def make_plan(settings, conn, plan_path: Path) -> int:
    picker = StockMediaPicker.from_settings(settings)
    judge = GeminiImageJudge.from_settings(settings)
    wp = WordPressClient.from_settings(settings)
    avoid = db.used_blog_media_ids(conn)
    plan, sheets, skipped = [], [], []
    for job in sorted(db.list_jobs(conn, limit=10_000), key=lambda j: j["id"]):
        if job["status"] not in TARGET_STATUSES or not job.get("post_id") or is_refresh(job):
            continue
        blog = (job["checkpoint"].get("media") or {}).get("blog") or []
        idx = featured_index(blog)
        if idx is None:
            continue
        try:
            plan_one(job, blog, idx, wp, picker, judge, avoid, plan, sheets, skipped)
        except Exception as exc:  # noqa: BLE001 - 한 글 실패로 나머지를 멈추지 않는다
            print(f"작업 {job['id']}: 판정하지 못함 — {exc.__class__.__name__}: {exc}")
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=1))
    if sheets:
        draw_sheet(sheets, plan_path.with_suffix(".jpg"))
    print(f"\n바꿀 글 {len(plan)}개 → {plan_path}"
          + (f"\n건너뛴 작업: {skipped}" if skipped else ""))
    return 0


def plan_one(job, blog, idx, wp, picker, judge, avoid, plan, sheets, skipped) -> None:
    reason, featured_media = post_state(wp, job, idx)
    if reason:
        skipped.append(f"{job['id']}({reason})")
        return
    content = MasterContent.model_validate(job["checkpoint"]["content"]["content"])
    section = content.sections[idx]
    query = section.image_query or section.heading
    old = blog[idx]
    incumbent = small_copy(Path(old["file"]), picker.media_dir / f"u{old['id']}.jpg")
    seen: dict = {}

    def spy(topic, q, previews):
        seen["previews"] = list(previews)
        seen["best"] = judge(topic, q, previews)
        return seen["best"]

    pick = picker._judged_image(query, {p["id"] for p in blog if p}, avoid, spy, content.title,
                                incumbent=incumbent)
    verdict = f"바꿈 → {pick.media_id}" if pick else ("그대로(지금 사진이 가장 나음)" if seen.get("best") == 0
                                                     else "그대로(나은 후보 없음·판정 실패)")
    print(f"작업 {job['id']}(글 {job['post_id']}) {content.title}: {verdict}", flush=True)
    if pick:
        avoid.add(pick.media_id)
        plan.append({"job_id": job["id"], "post_id": job["post_id"], "index": idx, "title": content.title,
                     "old_pixabay": old["id"], "old_media": featured_media, "new": _pick_json(pick),
                     "alt": (section.image_alt or section.heading).strip()})
        sheets.append((content.title, seen.get("previews", []), seen.get("best")))


def draw_sheet(rows, out: Path, w: int = 300, h: int = 170) -> None:
    """한 줄에 글 하나: 맨 왼쪽이 지금 사진, 빨간 테두리가 AI가 고른 새 사진."""
    sheet = Image.new("RGB", (w * 4, h * len(rows)), "white")
    d = ImageDraw.Draw(sheet)
    for r, (_title, previews, best) in enumerate(rows):
        for k, p in enumerate(previews):
            with Image.open(p) as im:
                im = im.convert("RGB")
                im.thumbnail((w - 8, h - 8))
                sheet.paste(im, (k * w + 4, r * h + 4))
            if k == best:
                d.rectangle([k * w + 1, r * h + 1, k * w + w - 2, r * h + h - 2], outline="red", width=5)
    sheet.save(out, quality=82)


def apply_plan(settings, conn, plan_path: Path) -> int:
    plan = json.loads(plan_path.read_text())
    wp = WordPressClient.from_settings(settings)
    failed = 0
    for item in plan:
        if busy := busy_jobs(conn):
            print(f"진행 중이거나 대기 중인 작업이 있어 멈춥니다(작업 {busy}). 끝난 뒤 다시 실행하세요")
            return 1
        try:
            failed += apply_one(settings, conn, wp, item)
        except Exception as exc:  # noqa: BLE001 - 한 글 실패로 나머지를 멈추지 않는다
            print(f"작업 {item['job_id']}: 바꾸지 못함 — {exc.__class__.__name__}: {exc}")
            failed += 1
    return 1 if failed else 0


def apply_one(settings, conn, wp, item: dict) -> int:
    """계획 한 줄을 적용한다. 계획 뒤 상황이 바뀌었으면 건너뛰고 1을 돌려준다."""
    job = db.get_job(conn, item["job_id"])
    idx = item["index"]
    blog = ((job or {}).get("checkpoint", {}).get("media") or {}).get("blog") or []
    new = item["new"]
    why = None
    if not job or job["status"] not in TARGET_STATUSES or job.get("post_id") != item["post_id"]:
        why = "작업 상태나 글 번호가 바뀜"
    elif featured_index(blog) != idx or blog[idx]["id"] != item["old_pixabay"]:
        why = "장부의 대표 사진이 바뀜"
    elif _same_series(new["id"], db.used_blog_media_ids(conn, exclude_job_id=job["id"])):
        why = "새 사진을 그사이 다른 글이 씀"
    else:
        reason, featured_media = post_state(wp, job, idx)
        if reason or featured_media != item["old_media"]:
            why = reason or "워드프레스 대표 사진이 계획 때와 다름"
    if why:
        print(f"작업 {item['job_id']}: 계획 뒤 {why} — 건너뜀")
        return 1
    pick = MediaPick(Path(new["file"]), "image", "pixabay", new["id"], new["page"],
                     new["credit"].removesuffix(" / Pixabay"), "")
    fitted = normalize_blog_image(pick.path, settings.data_dir / "media_cache" / "blog")

    def record(_new, job=job, idx=idx, new=new):
        media = {**job["checkpoint"]["media"]}
        media["blog"] = [*media["blog"]]
        media["blog"][idx] = new
        db.save_checkpoint(conn, job["id"], "media", media)

    up, deleted = wp.replace_featured(job["id"], job["post_id"], idx, fitted, int(item["old_media"]),
                                      alt=item["alt"], caption=pick.credit, on_switched=record)
    print(f"작업 {job['id']}(글 {job['post_id']}): 워드프레스 사진 {item['old_media']} → {up.id}"
          + ("" if deleted else " (옛 사진은 다른 곳에서 쓰여 남김)"))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plan", type=Path, help="계획 파일(JSON) 경로")
    parser.add_argument("--apply", action="store_true", help="계획 파일대로 실제로 바꾼다(없으면 계획만 만듦)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    settings = load_settings()
    conn = db.connect(settings.db_path)
    return apply_plan(settings, conn, args.plan) if args.apply else make_plan(settings, conn, args.plan)


if __name__ == "__main__":
    raise SystemExit(main())
