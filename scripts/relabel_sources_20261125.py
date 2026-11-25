"""2026-11-25 일회성: 이미 올린 글의 참고 자료 링크 이름을 source_labels 기준으로 바꾼다(기본은 미리보기, --apply로 적용·백업)."""
import sqlite3, json, sys, re, os
from html import escape
sys.path.insert(0, '.')
from src.config import load_settings
from src.wp_client import WordPressClient
from src.pipeline import sources_from
from src.blog_html import source_labels
apply = "--apply" in sys.argv
wp = WordPressClient.from_settings(load_settings())
c = sqlite3.connect("data/jobs.sqlite3")
for jid, st, pid, cp in c.execute("select id,status,post_id,checkpoint from jobs where post_id is not null and status not in ('failed','cancelled')"):
    src = sources_from(((json.loads(cp or '{}').get('content') or {}).get('sources')) or [])
    if len(src) < 2:
        continue
    raw = wp.get_post_content(pid)
    m = re.search(r'<ul class="jc-sources">.*?</ul>', raw, re.S)
    if not m:
        print(jid, pid, "참고 자료 목록 없음 - 건너뜀"); continue
    order = re.findall(r'<a href="([^"]+)"', m.group(0))
    by_url = {escape(s.url): s for s in src}
    cited = [by_url[u] for u in order if u in by_url]
    # 렌더러와 같은 기준: source_labels 결과가 지금 화면 글자(s.title)와 다른 것만 바꾼다
    pairs = [(s, l) for s, l in zip(cited, source_labels(cited)) if l != s.title]
    if not pairs:
        continue
    block = new = m.group(0)
    done = 0
    for s, label in pairs:
        old = f'<a href="{escape(s.url)}" target="_blank" rel="noopener">{escape(s.title)}</a>'
        rep = f'<a href="{escape(s.url)}" target="_blank" rel="noopener">{escape(label)}</a>'
        if new.count(old) == 1:
            new = new.replace(old, rep); done += 1
    print(jid, st, pid, f"{done}/{len(pairs)}개", [l for _, l in pairs])
    if done != len(pairs):
        print(jid, pid, "일부 링크를 못 찾음 - 저장하지 않음"); continue
    if apply:
        backup = f"data/backups/source-labels-20261125/post-{pid}.html"
        if not os.path.exists(backup):      # 다시 돌려도 최초 원본 백업을 덮어쓰지 않는다
            open(backup, "w").write(raw)
        body = raw[:m.start()] + new + raw[m.end():]
        wp._request("POST", f"/posts/{int(pid)}", json={"content": body})
        assert new in wp.get_post_content(pid)
