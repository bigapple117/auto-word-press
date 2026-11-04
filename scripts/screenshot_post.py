"""워드프레스 글(임시 글 포함)을 실제 테마·사이트 CSS 그대로 PC·휴대폰 크기로 캡처한다.

임시 글은 로그인해야 보이므로, 공개된 글 페이지를 열고 본문·제목 자리에 이 글 내용을 끼워 넣어 찍는다.
사용: .venv/bin/python scripts/screenshot_post.py <글 번호> [--out 폴더] [--template-post 1]
필요: tools/pw-browsers(헤드리스 Chromium), 한글 글꼴(~/.local/share/fonts)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path(__file__).resolve().parent.parent / "tools" / "pw-browsers"))

from playwright.sync_api import sync_playwright  # noqa: E402

from src.config import load_settings  # noqa: E402
from src.wp_client import WordPressClient  # noqa: E402

SIZES = {"pc": (1440, 900), "mobile": (390, 844)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("post_id", type=int)
    ap.add_argument("--out", type=Path, default=Path("data/screenshots"))
    ap.add_argument("--template-post", type=int, default=0, help="본문을 끼워 넣을 공개 글 번호(기본: 최신 공개 글)")
    args = ap.parse_args()
    settings = load_settings()
    wp = WordPressClient.from_settings(settings)
    post = wp._request("GET", f"/posts/{args.post_id}", params={"context": "edit",
                                                                "_fields": "id,title,content,featured_media"})
    template = args.template_post or wp._request("GET", "/posts", params={
        "status": "publish", "per_page": 1, "_fields": "id"})[0]["id"]
    featured = ""
    if post.get("featured_media"):
        m = wp._request("GET", f"/media/{post['featured_media']}", params={"_fields": "source_url"})
        featured = m.get("source_url", "")
    args.out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, (w, h) in SIZES.items():
            page = browser.new_page(viewport={"width": w, "height": h})
            page.goto(f"{settings.wp_base_url}/?p={template}", wait_until="networkidle")
            page.evaluate(
                """([title, html, featured]) => {
                    const t = document.querySelector('.wp-block-post-title');
                    if (t) t.textContent = title;
                    const c = document.querySelector('.wp-block-post-content');
                    if (c) c.innerHTML = html;
                    const f = document.querySelector('.wp-block-post-featured-image img');
                    if (f && featured) { f.src = featured; f.removeAttribute('srcset'); }
                }""",
                [post["title"]["raw"], post["content"]["rendered"], featured],
            )
            # 사진은 화면에 들어와야 불러오는 방식(지연 로딩)이라, 캡처 전에 모두 즉시 불러온다
            page.evaluate("""async () => {
                const imgs = [...document.images];
                imgs.forEach(i => { i.loading = 'eager'; i.removeAttribute('loading'); });
                await Promise.all(imgs.map(i => i.complete ? null :
                    new Promise(r => { i.addEventListener('load', r, {once: true});
                                       i.addEventListener('error', r, {once: true}); setTimeout(r, 8000); })));
            }""")
            page.wait_for_timeout(500)
            path = args.out / f"post{args.post_id}_{name}.png"
            page.screenshot(path=str(path), full_page=True)
            width = page.evaluate("(() => { const c = document.querySelector('.jc-article'); "
                                  "return c ? Math.round(c.getBoundingClientRect().width) : 0; })()")
            overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
            print(f"{name}: {path} 본문 폭 {width}px, 가로 넘침 {overflow}")
            page.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
