import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "fix_dup", Path(__file__).resolve().parent.parent / "scripts" / "fix_duplicate_thumbnails.py")
fix_dup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fix_dup)


def job(i, blog, post_id=100):
    return {"id": i, "post_id": post_id, "checkpoint": {"media": {"blog": blog}}}


def test_find_duplicates_keeps_first_post():
    jobs = [job(3, [{"id": 5}]), job(1, [None, {"id": 5}, {"id": 7}]), job(2, [{"id": 7}]),
            job(4, [{"id": 5}], post_id=None)]
    dups = fix_dup.find_duplicates(jobs)
    # 작업 1의 대표 사진은 5, 본문 7은 대표가 아니라 작업 2의 대표 7은 겹침 아님
    assert [(j["id"], idx, mid, owner) for j, idx, mid, owner in dups] == [(3, 0, 5, 1)]
