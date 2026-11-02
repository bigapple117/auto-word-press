"""dry run 결과 폴더의 글을 워드프레스에 '임시 저장'으로 올린다(발행하지 않음).

사용: .venv/bin/python scripts/dry_run_publish.py data/dryrun/<폴더> --job-id 900001
- 소제목별 Pixabay 가로 사진을 골라 워드프레스에 올리고 본문에 넣는다(출처 표기).
- 같은 job-id로 다시 실행하면 새 글을 만들지 않고 같은 임시 글을 고친다.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.blog_html import SectionImage, render_blog_html  # noqa: E402
from src.config import load_settings  # noqa: E402
from src.content_schema import MasterContent  # noqa: E402
from src.source_fetcher import Source  # noqa: E402
from src.stock_media import BLOG_IMAGE_SIZE, StockMediaPicker, normalize_blog_image  # noqa: E402
from src.wp_client import WordPressClient  # noqa: E402


def load_sources(folder: Path) -> list[Source]:
    path = folder / "sources.json"
    if not path.exists():
        return []
    return [Source(s["id"], s["url"], s["title"], s.get("text", ""), s.get("via", "direct"))
            for s in json.loads(path.read_text(encoding="utf-8"))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--job-id", type=int, required=True)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    settings = load_settings()
    content = MasterContent.model_validate_json((args.folder / "content.json").read_text(encoding="utf-8"))
    sources = load_sources(args.folder)
    wp = WordPressClient.from_settings(settings)
    picker = StockMediaPicker.from_settings(settings)

    # 처음 고른 사진·출처를 저장해 두고 다시 실행할 때 그대로 쓴다(출처 표기가 바뀌지 않게)
    # (읽기 → 고르기 → 저장)은 폴더별 잠금 안에서, 저장은 임시 파일 후 교체. 업로드는 잠금 밖에서.
    saved_path = args.folder / "blog_media.json"
    with (args.folder / ".blog_media.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        saved = json.loads(saved_path.read_text(encoding="utf-8")) if saved_path.exists() else {}
        if str(args.job_id) not in saved:
            picks = picker.pick_blog_images([s.image_query for s in content.sections])
            saved[str(args.job_id)] = [None if p is None else {"file": str(p.path), "credit": p.credit,
                                                              "page": p.page_url} for p in picks]
            tmp = saved_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(saved_path)
        chosen = saved[str(args.job_id)]
    images: dict[int, SectionImage] = {}
    featured = None
    w, h = BLOG_IMAGE_SIZE
    for i, (section, pick) in enumerate(zip(content.sections, chosen)):
        if not pick:
            continue
        fitted = normalize_blog_image(Path(pick["file"]), settings.data_dir / "media_cache" / "blog")
        media = wp.upload_media(fitted, args.job_id, i, alt=section.heading, caption=pick["credit"])
        if featured is None:
            featured = media.id          # 대표 사진은 글 맨 위에 테마가 보여 주므로 본문에는 다시 넣지 않음
            continue
        images[i] = SectionImage(media.url, section.heading, f"사진: {pick['credit']}", pick["page"],
                                 width=w, height=h, media_id=media.id)

    html = render_blog_html(content, sources, images)
    post = wp.save_draft(args.job_id, content.title, html, slug=content.slug,
                         excerpt=content.meta_description, featured_media=featured)
    print(f"임시 글 {post.id}번 ({post.status}), 사진 {len(images)}장")
    print("미리보기(관리자 로그인 필요):", f"{settings.wp_base_url}/?p={post.id}&preview=true")
    print("편집 화면:", f"{settings.wp_base_url}/wp-admin/post.php?post={post.id}&action=edit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
