"""사이트 고정 페이지(소개·문의·개인정보 처리방침)와 머리말·꼬리말을 deploy/wp 원본대로 맞춘다.

사용: .venv/bin/python scripts/apply_site_pages.py
- 페이지는 주소 이름(slug)으로 찾아 내용만 바꾸고, 없으면 새로 만들어 공개한다.
- 머리말·꼬리말과 홈·글 화면 틀(templates)은 테마 원본 파일은 그대로 두고 사이트 쪽 수정본으로 저장한다
  (되돌리려면 관리자 화면 > 디자인 > 패턴 > 템플릿 파트에서 '사용자 정의 초기화').
- 바꾸기 전 내용은 data/backups/site-pages-<시각>/ 에 저장한다.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_settings  # noqa: E402
from src.wp_client import WordPressClient  # noqa: E402

PAGES = [  # (slug, 제목, 원본 파일)
    ("about", "잘체크 소개와 편집 원칙", "about.html"),
    ("contact", "문의", "contact.html"),
    ("privacy-policy", "개인정보 처리방침", "privacy.html"),
]
PARTS = ["header", "footer"]
TEMPLATES = ["home", "single", "archive", "search"]  # deploy/wp/templates/<이름>.html
THEME = "twentytwentyfive"


def main() -> int:
    settings = load_settings()
    wp = WordPressClient.from_settings(settings)
    backup = settings.data_dir / "backups" / f"site-pages-{datetime.now():%Y%m%d-%H%M%S}"
    backup.mkdir(parents=True, exist_ok=True)
    src = ROOT / "deploy" / "wp"
    for slug, title, fname in PAGES:
        body = (src / "pages" / fname).read_text(encoding="utf-8")
        if "{{" in body:
            print(f"{slug}: 채워지지 않은 자리 표시가 있어 건너뜀")
            continue
        found = wp._request("GET", "/pages", params={"slug": slug, "status": "publish,draft,private,pending",
                                                     "context": "edit", "_fields": "id,status,content"})
        data = {"title": title, "content": body, "status": "publish"}
        if found:
            page = found[0]
            (backup / f"page-{slug}.html").write_text(page["content"]["raw"], encoding="utf-8")
            wp._request("POST", f"/pages/{page['id']}", json=data)
            print(f"{slug}: {page['id']}번 수정 ({page['status']} → publish)")
        else:
            made = wp._request("POST", "/pages", json={**data, "slug": slug})
            print(f"{slug}: {made['id']}번 새로 만듦")
    for part in PARTS:
        body = (src / "parts" / f"{part}.html").read_text(encoding="utf-8")
        pid = f"{THEME}//{part}"
        cur = wp._request("GET", f"/template-parts/{pid}", params={"context": "edit"})
        (backup / f"part-{part}.html").write_text(cur["content"]["raw"], encoding="utf-8")
        wp._request("POST", f"/template-parts/{pid}", json={"content": body})
        print(f"{part}: 저장")
    for tpl in TEMPLATES:
        body = (src / "templates" / f"{tpl}.html").read_text(encoding="utf-8")
        tid = f"{THEME}//{tpl}"
        cur = wp._request("GET", f"/templates/{tid}", params={"context": "edit"})
        (backup / f"template-{tpl}.html").write_text(cur["content"]["raw"], encoding="utf-8")
        wp._request("POST", f"/templates/{tid}", json={"content": body})
        print(f"template {tpl}: 저장")
    (backup / "done.json").write_text(json.dumps({"at": datetime.now().isoformat()}), encoding="utf-8")
    print("백업:", backup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
