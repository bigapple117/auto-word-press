import json

import pytest

from src.wp_client import WordPressClient, WPError, WPMedia, WPPost, job_marker, marker_comment


class FakeResp:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code = status
        self._data = data
        self.headers = headers or {}

    def json(self):
        if self._data is None:
            raise ValueError
        return self._data


class FakeWP:
    """아주 단순한 가짜 워드프레스: 글·미디어를 메모리에 둔다."""

    def __init__(self):
        self.posts = {}
        self.media = {}
        self.next_id = 100
        self.calls = []
        self.page_size = 100
        self.tags = [{"id": 1, "name": "자녀장려금 신청"}]

    def request(self, method, url, auth=None, params=None, json=None, data=None, headers=None, **kw):
        path = url.split("/wp-json/wp/v2", 1)[1]
        self.calls.append((method, path, params, json, headers, auth))
        if path == "/tags" and method == "GET":
            return self._page([t for t in self.tags if params["search"] in t["name"]], params)
        if path == "/categories" and method == "GET":
            cats = [{"id": 1, "slug": "benefits", "name": "지원금·복지", "description": ""},
                    {"id": 49, "slug": "youth-jobs", "name": "청년·일자리", "description": "빈 분류"}]
            return self._page(cats, params)
        if path == "/tags" and method == "POST":
            t = {"id": 700 + len(self.tags), "name": json["name"]}
            self.tags.append(t)
            return FakeResp(201, t)
        if path == "/users/me":
            return FakeResp(200, {"id": 1, "name": "관리자", "roles": ["administrator"],
                                  "capabilities": {"publish_posts": True}})
        if path == "/posts" and method == "GET":
            term = params["search"]
            found = [{"id": i, "status": p["status"], "link": f"https://jalcheck.com/?p={i}",
                      "content": {"raw": p["content"]}}
                     for i, p in self.posts.items() if term in p["content"] and p["status"] != "trash"]
            return self._page(found, params)
        if path == "/posts" and method == "POST":
            self.next_id += 1
            self.posts[self.next_id] = dict(json)
            return FakeResp(201, {"id": self.next_id, "status": json["status"],
                                  "link": f"https://jalcheck.com/?p={self.next_id}"})
        if path.startswith("/posts/"):
            pid = int(path.split("/")[2])
            if pid not in self.posts:
                return FakeResp(404, {"code": "rest_post_invalid_id", "message": "없음"})
            if method == "POST":
                self.posts[pid].update(json)
            p = self.posts[pid]
            return FakeResp(200, {"id": pid, "status": p["status"], "link": f"https://jalcheck.com/?p={pid}",
                                  "content": {"raw": p.get("content", "")}})
        if path == "/media" and method == "GET":
            term = params["search"]
            return self._page([{"id": i, "source_url": m["url"]} for i, m in self.media.items()
                               if term in m["url"]], params)
        if path == "/media" and method == "POST":
            self.next_id += 1
            name = headers["Content-Disposition"].split('filename="')[1].rstrip('"')
            body = data.read()
            self.media[self.next_id] = {"url": f"https://jalcheck.com/wp-content/uploads/2026/11/{name}",
                                        "size": len(body), "type": headers["Content-Type"]}
            return FakeResp(201, {"id": self.next_id, "source_url": self.media[self.next_id]["url"]})
        if path.startswith("/media/") and method == "DELETE":
            self.media.pop(int(path.split("/")[2]), None)
            return FakeResp(200, {"deleted": True})
        if path.startswith("/media/") and method == "POST":
            self.media[int(path.split("/")[2])].update(json)
            return FakeResp(200, {"id": int(path.split("/")[2])})
        raise AssertionError(f"예상 못 한 요청 {method} {path}")

    def _page(self, items, params, size=None):
        size = size or self.page_size
        page = params.get("page", 1)
        pages = max(1, -(-len(items) // size))
        return FakeResp(200, items[(page - 1) * size: page * size], {"X-WP-TotalPages": str(pages)})


@pytest.fixture
def wp(tmp_path):
    fake = FakeWP()
    return WordPressClient("https://jalcheck.com", "admin", "abcd efgh ijkl", session=fake,
                           lock_dir=tmp_path / "locks"), fake


def test_requires_https():
    with pytest.raises(WPError, match="https"):
        WordPressClient("http://jalcheck.com", "a", "b")


def test_auth_strips_spaces_and_whoami(wp):
    client, fake = wp
    me = client.whoami()
    assert me["can_publish"] and fake.calls[0][5] == ("admin", "abcdefghijkl")


def test_draft_is_created_once_then_updated(wp):
    client, fake = wp
    first = client.save_draft(7, "제목", "<p>본문</p>", slug="eitc", excerpt="요약")
    assert first.status == "draft"
    assert fake.posts[first.id]["content"].startswith(marker_comment(7))
    # 장부에 글 번호를 기록하기 전에 멈췄다고 가정: post_id 없이 다시 실행해도 같은 글을 고친다
    second = client.save_draft(7, "고친 제목", "<p>새 본문</p>")
    assert second.id == first.id and len(fake.posts) == 1
    assert fake.posts[first.id]["title"] == "고친 제목"
    # 다른 작업은 새 글
    assert client.save_draft(8, "다른 글", "<p>x</p>").id != first.id


def test_marker_must_match_exactly(wp):
    client, fake = wp
    fake.posts[1] = {"content": marker_comment(12) + "\nx", "status": "draft"}
    # 1번 작업을 찾을 때 'jcjob-1'이 'jcjob-12'에 포함되어도 다른 글로 착각하지 않음
    assert client.find_post_by_job(1) is None
    assert client.find_post_by_job(12).id == 1


def test_duplicate_markers_stop_for_manual_fix(wp):
    client, fake = wp
    fake.posts[1] = {"content": marker_comment(3), "status": "draft"}
    fake.posts[2] = {"content": marker_comment(3), "status": "draft"}
    with pytest.raises(WPError, match="2개"):
        client.find_post_by_job(3)


def test_published_post_is_never_overwritten(wp):
    client, fake = wp
    post = client.save_draft(5, "제목", "<p>x</p>")
    client.publish(5, post.id)
    with pytest.raises(WPError, match="이미 발행"):
        client.save_draft(5, "덮어쓰기", "<p>y</p>")
    with pytest.raises(WPError, match="이미 발행"):
        client.save_draft(5, "덮어쓰기", "<p>y</p>", post_id=post.id)


def test_publish_is_idempotent(wp):
    client, fake = wp
    post = client.save_draft(5, "제목", "<p>x</p>")
    assert client.publish(5, post.id).status == "publish"
    calls = len(fake.calls)
    assert client.publish(5, post.id).status == "publish"
    assert all(c[0] == "GET" for c in fake.calls[calls:])        # 두 번째는 상태만 확인
    with pytest.raises(WPError, match="없습니다"):
        client.publish(5, 9999)


def test_trashed_post_is_ignored(wp):
    client, fake = wp
    fake.posts[1] = {"content": marker_comment(4), "status": "trash"}
    assert client.find_post_by_job(4) is None
    assert client.get_post(1) is None


def test_media_uploaded_once_with_job_name(wp, tmp_path):
    client, fake = wp
    img = tmp_path / "Photo One.JPG"
    img.write_bytes(b"jpegdata")
    m1 = client.upload_media(img, 7, 0, alt="계산기", caption="Pixabay")
    assert m1.url.endswith(f"{job_marker(7)}-0.jpg")
    assert fake.media[m1.id]["type"] == "image/jpeg" and fake.media[m1.id]["alt_text"] == "계산기"
    assert client.upload_media(img, 7, 0).id == m1.id            # 다시 올리지 않음
    assert client.upload_media(img, 7, 1).id != m1.id
    bad = tmp_path / "x.exe"
    bad.write_bytes(b"x")
    with pytest.raises(WPError, match="형식"):
        client.upload_media(bad, 7, 2)


@pytest.mark.parametrize("resp, msg", [
    (FakeResp(401, {"code": "rest_not_logged_in", "message": "x"}), "인증 실패"),
    (FakeResp(500, None), "오류 500"),
    (FakeResp(302, None), "다른 주소"),
    (FakeResp(200, None), "JSON"),
])
def test_errors_do_not_leak_password(resp, msg):
    class S:
        def request(self, *a, **k):
            return resp

    client = WordPressClient("https://jalcheck.com", "admin", "SECRETPASS", session=S())
    with pytest.raises(WPError) as exc:
        client.whoami()
    assert msg in str(exc.value) and "SECRETPASS" not in str(exc.value)


def test_connection_error_hides_details():
    import requests

    class S:
        def request(self, *a, **k):
            raise requests.ConnectionError("boom with auth SECRETPASS")

    client = WordPressClient("https://jalcheck.com", "admin", "SECRETPASS", session=S())
    with pytest.raises(WPError) as exc:
        client.whoami()
    assert "SECRETPASS" not in str(exc.value) and exc.value.__cause__ is None


def test_update_does_not_send_status(wp):
    client, fake = wp
    post = client.save_draft(7, "제목", "<p>x</p>")
    fake.posts[post.id]["status"] = "draft"
    client.save_draft(7, "고침", "<p>y</p>")
    update = [c for c in fake.calls if c[0] == "POST" and c[1] == f"/posts/{post.id}"][-1]
    assert "status" not in update[3]          # 그사이 사람이 발행했어도 임시 글로 되돌리지 않음


def test_recorded_post_id_must_belong_to_job(wp):
    client, fake = wp
    other = client.save_draft(8, "다른 작업", "<p>x</p>")
    with pytest.raises(WPError, match="작업 7번의 글이 아닙니다"):
        client.save_draft(7, "제목", "<p>x</p>", post_id=other.id)
    # 장부의 글이 지워졌으면(404) 표시로 다시 찾고, 없으면 새로 만든다
    mine = client.save_draft(7, "제목", "<p>x</p>", post_id=999)
    assert client.save_draft(7, "제목2", "<p>x</p>", post_id=999).id == mine.id


def test_search_reads_all_pages(wp):
    client, fake = wp
    fake.page_size = 2
    for i in range(10, 15):                    # 'jcjob-1'로 검색하면 걸리는 다른 작업 글들
        fake.posts[i] = {"content": marker_comment(i * 10), "status": "draft"}
    fake.posts[50] = {"content": marker_comment(1), "status": "draft"}
    assert client.find_post_by_job(1).id == 50  # 세 번째 페이지에 있어도 찾음


def test_media_index_matches_exactly(wp, tmp_path):
    client, fake = wp
    fake.media[1] = {"url": "https://jalcheck.com/wp-content/uploads/jcjob-42-10.jpg"}
    fake.media[2] = {"url": "https://jalcheck.com/wp-content/uploads/jcjob-42-1-1.jpg"}  # 워드프레스가 붙인 꼬리
    assert client.find_media("jcjob-42-1").id == 2
    assert client.find_media("jcjob-42-2") is None


def test_publish_checks_job_ownership(wp):
    client, fake = wp
    other = client.save_draft(8, "다른 작업", "<p>x</p>")
    with pytest.raises(WPError, match="아닙니다"):
        client.publish(7, other.id)


def test_job_lock_serializes(tmp_path):
    import threading
    import time
    from src.wp_client import job_lock
    order = []

    def worker(name):
        with job_lock(1, tmp_path):
            order.append(name + "-in")
            time.sleep(0.2)
            order.append(name + "-out")

    ts = [threading.Thread(target=worker, args=(n,)) for n in "ab"]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert order[1].endswith("-out")


def test_delete_job_media_only_exact_names(wp, tmp_path):
    client, fake = wp
    fake.media[1] = {"url": "https://jalcheck.com/u/jcjob-7-0.jpg"}
    fake.media[2] = {"url": "https://jalcheck.com/u/jcjob-7-1-1.jpg"}
    fake.media[3] = {"url": "https://jalcheck.com/u/jcjob-70-0.jpg"}
    fake.media[4] = {"url": "https://jalcheck.com/u/jcjob-7-photo.jpg"}
    assert sorted(client.delete_job_media(7)) == [1, 2]
    assert sorted(fake.media) == [3, 4]


def test_ensure_tags_exact_match_and_create(wp):
    client, fake = wp
    ids = client.ensure_tags(["자녀장려금", "자녀장려금", " 홈택스 ", ""])
    assert len(ids) == 2 and [t["name"] for t in fake.tags] == ["자녀장려금 신청", "자녀장려금", "홈택스"]
    assert client.ensure_tags(["자녀장려금 신청"]) == [1]
    post = client.save_draft(3, "t", "<p>x</p>", tags=ids)
    assert fake.posts[post.id]["tags"] == ids


class FeaturedWP(FakeWP):
    """대표 사진 교체용: 글 목록·페이지 목록·대표 사진 번호까지 흉내 낸다."""

    def request(self, method, url, auth=None, params=None, json=None, data=None, headers=None, **kw):
        path = url.split("/wp-json/wp/v2", 1)[1]
        if path in ("/posts", "/pages") and method == "GET" and "search" not in (params or {}):
            self.calls.append((method, path, params, json, headers, auth))
            items = [{"id": i, "featured_media": p.get("featured_media", 0), "content": {"raw": p["content"]}}
                     for i, p in (self.posts.items() if path == "/posts" else [])]
            return self._page(items, params)
        if path.startswith("/posts/") and method == "GET":
            self.calls.append((method, path, params, json, headers, auth))
            p = self.posts[int(path.split("/")[2])]
            return FakeResp(200, {"id": int(path.split("/")[2]), "featured_media": p.get("featured_media", 0),
                                  "content": {"raw": p["content"]}})
        return super().request(method, url, auth, params, json, data, headers, **kw)


def _featured_setup(tmp_path):
    fake = FeaturedWP()
    client = WordPressClient("https://jalcheck.com", "a", "b", session=fake, lock_dir=tmp_path / "locks")
    old = client.upload_media(_img(tmp_path), 3, 1)
    fake.posts[57] = {"content": marker_comment(3) + "\n<p>본문</p>", "status": "publish", "featured_media": old.id}
    return client, fake, old


def _img(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"\xff\xd8jpg")
    return p


def test_replace_featured_switches_records_then_deletes(tmp_path):
    client, fake, old = _featured_setup(tmp_path)
    order = []
    fake_delete = fake.request

    def spy(method, url, **kw):
        if method == "DELETE":
            order.append("delete")
        return fake_delete(method, url, **kw)
    fake.request = spy
    new, deleted = client.replace_featured(3, 57, 1, _img(tmp_path), old.id,
                                           on_switched=lambda m: order.append(("record", m.id)))
    assert deleted and fake.posts[57]["featured_media"] == new.id and old.id not in fake.media
    assert order == [("record", new.id), "delete"]                 # 장부를 먼저 맞추고 옛 사진 삭제
    assert client.find_media("jcjob-3-1").id == new.id             # 화면 다시 만들기도 새 사진을 찾음


def test_replace_featured_keeps_old_when_used_elsewhere(tmp_path):
    client, fake, old = _featured_setup(tmp_path)
    fake.posts[60] = {"content": "<img src='/jcjob-3-1.jpg'>", "status": "publish"}
    new, deleted = client.replace_featured(3, 57, 1, _img(tmp_path), old.id)
    assert not deleted and old.id in fake.media and fake.posts[57]["featured_media"] == new.id


def test_replace_featured_stops_on_leftovers_or_mismatch(tmp_path):
    client, fake, old = _featured_setup(tmp_path)
    with pytest.raises(WPError, match="예상"):
        client.replace_featured(3, 57, 1, _img(tmp_path), old.id + 1)
    with pytest.raises(WPError, match="작업 4번 글이 아닙니다"):
        client.replace_featured(4, 57, 1, _img(tmp_path), old.id)
    client._upload(_img(tmp_path), "jcjob-3-1", "", "")            # 예전 교체가 멈춘 흔적
    before = dict(fake.media)
    with pytest.raises(WPError, match="흔적"):
        client.replace_featured(3, 57, 1, _img(tmp_path), old.id)
    assert fake.media == before and fake.posts[57]["featured_media"] == old.id


def test_list_categories_includes_empty_and_draft_sends_categories(wp):
    client, fake = wp
    cats = client.list_categories()
    assert [c["slug"] for c in cats] == ["benefits", "youth-jobs"]
    assert any(c[1] == "/categories" and c[2]["hide_empty"] == "false" for c in fake.calls)
    post = client.save_draft(4, "t", "<p>x</p>", categories=[49])
    assert fake.posts[post.id]["categories"] == [49]
    other = client.save_draft(5, "t", "<p>x</p>")
    assert "categories" not in fake.posts[other.id]
