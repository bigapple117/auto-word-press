import json

import pytest

from src import stock_media as sm
from src.stock_media import MediaError, PixabayClient, StockMediaPicker, choose_image, choose_video


def video_hit(id_, w, h, dur=10, size=1_000_000, variant="medium"):
    return {"id": id_, "duration": dur, "pageURL": f"https://pixabay.com/videos/{id_}/", "user": f"u{id_}",
            "videos": {variant: {"url": f"https://cdn.pixabay.com/video/{id_}.mp4", "width": w,
                                 "height": h, "size": size}}}


def image_hit(id_, h=2000):
    return {"id": id_, "imageHeight": h, "largeImageURL": f"https://pixabay.com/get/{id_}.jpg",
            "pageURL": f"https://pixabay.com/photos/{id_}/", "user": f"p{id_}"}


def test_choose_video_prefers_portrait_and_skips_bad():
    hits = [
        video_hit(1, 1920, 1080),                  # 가로(후보)
        video_hit(2, 720, 1280, dur=2),            # 너무 짧음
        video_hit(3, 1080, 1920, size=99_000_000), # 너무 큼
        video_hit(4, 640, 360),                    # 가로인데 작음
        video_hit(5, 1080, 1920),                  # 세로(1순위)
    ]
    hit, variant = choose_video(hits, exclude=set())
    assert hit["id"] == 5 and variant == "medium"
    assert choose_video(hits, exclude={5})[0]["id"] == 1
    assert choose_video(hits, exclude={1, 5}) is None


def test_choose_image():
    assert choose_image([image_hit(1, 900), image_hit(2)], set())["id"] == 2
    assert choose_image([image_hit(2)], {2}) is None


class FakeResp:
    def __init__(self, status=200, data=None, chunks=(b"data",), text=""):
        self.status_code = status
        self._data = data
        self._chunks = chunks
        self.text = text

    def json(self):
        if self._data is None:
            raise ValueError
        return self._data

    def iter_content(self, n):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSession:
    def __init__(self, routes):
        self.routes = routes        # url 앞부분 → 응답(또는 응답 목록)
        self.calls = []

    def get(self, url, params=None, **kw):
        self.calls.append((url, params, kw))
        for prefix, resp in self.routes.items():
            if url.startswith(prefix):
                return resp.pop(0) if isinstance(resp, list) else resp
        raise AssertionError(f"예상 못 한 주소 {url}")


def make_client(tmp_path, routes, clock=lambda: 1000.0):
    return PixabayClient("KEY", tmp_path / "cache", session=FakeSession(routes), clock=clock)


def test_search_is_cached_for_24h(tmp_path):
    now = [1000.0]
    client = make_client(tmp_path, {sm.API_VIDEOS: FakeResp(data={"hits": [video_hit(1, 1080, 1920)]})},
                         clock=lambda: now[0])
    assert client.search_videos("money")[0]["id"] == 1
    assert client.search_videos("money")[0]["id"] == 1
    assert len(client.http.calls) == 1                         # 두 번째는 저장본
    url, params, _ = client.http.calls[0]
    assert params["key"] == "KEY" and params["safesearch"] == "true" and params["q"] == "money"
    now[0] += sm.CACHE_SECONDS + 1
    client.search_videos("money")
    assert len(client.http.calls) == 2                         # 24시간 지나면 다시 검색


@pytest.mark.parametrize("resp, msg", [
    (FakeResp(429), "한도"), (FakeResp(500, text="x"), "오류 500"), (FakeResp(200), "JSON"),
])
def test_search_errors(tmp_path, resp, msg):
    with pytest.raises(MediaError, match=msg):
        make_client(tmp_path, {sm.API_VIDEOS: resp}).search_videos("x")


def test_download_rules(tmp_path):
    client = make_client(tmp_path, {"https://cdn.pixabay.com/": [
        FakeResp(chunks=(b"ab", b"cd")), FakeResp(chunks=(b"x" * 11,)), FakeResp(status=302)]})
    dest = tmp_path / "m" / "v1.mp4"
    assert client.download("https://cdn.pixabay.com/a.mp4", dest, 100).read_bytes() == b"abcd"
    assert client.download("https://cdn.pixabay.com/a.mp4", dest, 100) == dest   # 이미 있으면 재사용
    with pytest.raises(MediaError, match="너무 큽니다"):
        client.download("https://cdn.pixabay.com/b.mp4", tmp_path / "b.mp4", 10)
    assert not (tmp_path / "b.mp4").exists() and not (tmp_path / "b.mp4.part").exists()
    with pytest.raises(MediaError, match="응답 코드 302"):
        client.download("https://cdn.pixabay.com/c.mp4", tmp_path / "c.mp4", 10)
    for bad in ("http://cdn.pixabay.com/a.mp4", "https://evil.com/a.mp4", "https://169.254.169.254/x"):
        with pytest.raises(MediaError, match="허용되지 않은"):
            client.download(bad, tmp_path / "z.mp4", 10)


def test_pick_scene_backgrounds_no_duplicates_and_fallbacks(tmp_path, monkeypatch):
    videos = {"money desk": [video_hit(1, 1080, 1920)], "office work": [video_hit(1, 1080, 1920)],
              "rare thing": [], "rare": []}
    images = {"rare thing": [image_hit(9)], "nothing at all": [], "nothing at": []}

    class Client:
        def search_videos(self, q):
            return videos.get(q, [])

        def search_images(self, q, orientation):
            return images.get(q, [])

        def download(self, url, dest, max_bytes):
            return dest

    picker = StockMediaPicker(Client(), tmp_path)
    picks = picker.pick_scene_backgrounds(["money desk", "office work", "rare thing", "nothing at all"])
    assert picks[0].kind == "video" and picks[0].media_id == 1
    assert picks[1] is None or picks[1].media_id != 1          # 같은 영상 다시 안 씀
    assert picks[2].kind == "image" and picks[2].media_id == 9
    assert picks[3] is None                                     # 끝내 없으면 그라데이션
    assert picks[0].credit == "u1 / Pixabay"


def test_letterboxed_video_is_skipped(tmp_path, monkeypatch):
    class Client:
        def search_videos(self, q):
            return [video_hit(1, 1080, 1920), video_hit(2, 1080, 1920)]

        def search_images(self, q, orientation):
            return []

        def download(self, url, dest, max_bytes):
            return dest

    monkeypatch.setattr(sm, "has_letterbox", lambda ff, path, w, h: "v1_" in path.name)
    monkeypatch.setattr(sm, "media_is_valid", lambda fp, path, kind: True)
    picker = StockMediaPicker(Client(), tmp_path, ffmpeg=tmp_path / "ffmpeg")
    assert picker.pick_scene_backgrounds(["x"])[0].media_id == 2


def test_errors_during_pick_become_none(tmp_path):
    class Client:
        def search_videos(self, q):
            raise MediaError("down")

        def search_images(self, q, orientation):
            raise MediaError("down")

    assert StockMediaPicker(Client(), tmp_path).pick_scene_backgrounds(["a b c"]) == [None]


def test_has_letterbox_parses_cropdetect(monkeypatch, tmp_path):
    class R:
        stderr = "[Parsed_cropdetect] crop=2560:1440:0:0\n[Parsed_cropdetect] crop=2560:1344:0:48"

    monkeypatch.setattr(sm.subprocess, "run", lambda *a, **k: R())
    assert sm.has_letterbox(tmp_path / "ff", tmp_path / "v.mp4", 2560, 1440) is True
    R.stderr = "crop=1920:1072:0:4"
    assert sm.has_letterbox(tmp_path / "ff", tmp_path / "v.mp4", 1920, 1080) is False
    R.stderr = "nothing"
    assert sm.has_letterbox(tmp_path / "ff", tmp_path / "v.mp4", 1920, 1080) is False


def test_broken_media_is_deleted_and_skipped(tmp_path, monkeypatch):
    class Client:
        def search_videos(self, q):
            return [video_hit(1, 1080, 1920), video_hit(2, 1080, 1920)]

        def search_images(self, q, orientation):
            return []

        def download(self, url, dest, max_bytes):
            dest.write_bytes(b"x")
            return dest

    monkeypatch.setattr(sm, "media_is_valid", lambda fp, path, kind: "v2_" in path.name)
    monkeypatch.setattr(sm, "has_letterbox", lambda *a: False)
    picker = StockMediaPicker(Client(), tmp_path, ffmpeg=tmp_path / "ffmpeg")
    pick = picker.pick_scene_backgrounds(["x"])[0]
    assert pick.media_id == 2 and not (tmp_path / "v1_medium.mp4").exists()


def test_api_key_not_in_exception_chain(tmp_path):
    import requests

    class Boom:
        def get(self, url, params=None, **kw):
            raise requests.ConnectionError(f"failed {url}?key={params['key']}")

    client = PixabayClient("SECRETKEY", tmp_path, session=Boom())
    with pytest.raises(MediaError) as exc:
        client.search_videos("x")
    assert "SECRETKEY" not in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


def test_download_uses_unique_temp_and_cleans_up(tmp_path):
    client = make_client(tmp_path, {"https://cdn.pixabay.com/": [FakeResp(chunks=(b"x" * 20,))]})
    with pytest.raises(MediaError):
        client.download("https://cdn.pixabay.com/a.mp4", tmp_path / "m" / "a.mp4", 10)
    assert list((tmp_path / "m").iterdir()) == []


def test_download_refuses_when_disk_low(tmp_path, monkeypatch):
    monkeypatch.setattr(sm.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    client = make_client(tmp_path, {})
    with pytest.raises(MediaError, match="여유 공간"):
        client.download("https://cdn.pixabay.com/a.mp4", tmp_path / "a.mp4", 10)


def test_prune_cache_removes_least_recently_used(tmp_path):
    import os
    for i, name in enumerate(["old.mp4", "mid.mp4", "new.mp4"]):
        f = tmp_path / name
        f.write_bytes(b"x" * 100)
        os.utime(f, (1000 + i, 1000 + i))
    freed = sm.prune_cache(tmp_path, max_bytes=150, keep={tmp_path / "old.mp4"})
    # 쓰는 중인 old는 지키고, 상한(150)을 맞출 때까지 오래 안 쓴 순서(mid → new)로 지움
    assert freed == 200
    assert sorted(p.name for p in tmp_path.iterdir()) == ["old.mp4"]
    (tmp_path / "x.mp4").write_bytes(b"x" * 10)
    assert sm.prune_cache(tmp_path, max_bytes=1000) == 0


def test_media_is_valid_with_real_ffprobe(tmp_path):
    from src.config import load_settings
    s = load_settings()
    if not s.ffprobe_bin.exists():
        pytest.skip("ffprobe 없음")
    import subprocess
    good = tmp_path / "g.mp4"
    subprocess.run([str(s.ffmpeg_bin), "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=s=160x120:r=10:d=1.5", str(good)], check=True)
    bad = tmp_path / "b.mp4"
    bad.write_bytes(b"not a video")
    assert sm.media_is_valid(s.ffprobe_bin, good, "video") is True
    assert sm.media_is_valid(s.ffprobe_bin, bad, "video") is False


def test_broken_image_tries_next_and_blog_images_prune(tmp_path, monkeypatch):
    calls = {}

    class Client:
        def search_videos(self, q):
            return []

        def search_images(self, q, orientation):
            return [image_hit(1), image_hit(2), image_hit(3)]

        def download(self, url, dest, max_bytes):
            dest.write_bytes(b"x")
            return dest

    monkeypatch.setattr(sm, "media_is_valid", lambda fp, path, kind: "i1" not in path.name)
    monkeypatch.setattr(sm, "prune_cache", lambda d, keep=None: calls.setdefault("keep", keep))
    picker = StockMediaPicker(Client(), tmp_path, ffmpeg=tmp_path / "ffmpeg")
    picks = picker.pick_blog_images(["office desk", "family home"])
    assert [p.media_id for p in picks] == [2, 3]                 # 깨진 1번 건너뛰고, 겹치지 않음
    assert calls["keep"] == {p.path for p in picks}


def test_normalize_blog_image_crops_to_16_9(tmp_path):
    from PIL import Image
    for name, size in [("tall.jpg", (800, 1200)), ("wide.png", (2000, 600))]:
        src = tmp_path / name
        Image.new("RGB", size, "red").save(src)
        out = sm.normalize_blog_image(src, tmp_path / "out")
        with Image.open(out) as im:
            assert im.size == sm.BLOG_IMAGE_SIZE and im.format == "JPEG"
        assert sm.normalize_blog_image(src, tmp_path / "out") == out      # 다시 만들지 않음
    assert not list((tmp_path / "out").glob("*.part"))


def test_blog_images_skip_empty_queries(tmp_path, monkeypatch):
    class Client:
        def search_images(self, q, orientation):
            return [image_hit(1), image_hit(2)]

        def download(self, url, dest, max_bytes):
            return dest

    monkeypatch.setattr(sm, "prune_cache", lambda d, keep=None: 0)
    picks = StockMediaPicker(Client(), tmp_path).pick_blog_images([None, "office desk", "  "])
    assert picks[0] is None and picks[1].media_id == 1 and picks[2] is None
