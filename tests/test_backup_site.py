from pathlib import Path

import pytest

from scripts import backup_site as b


def test_old_files_keeps_newest_by_name():
    files = [Path(f"jalcheck-2026110{i}-040000.tar.zst.gpg") for i in (3, 1, 2)]
    assert [p.name for p in b.old_files(files, 2)] == ["jalcheck-20261101-040000.tar.zst.gpg"]


def test_old_files_nothing_to_delete():
    assert b.old_files([Path("a")], 14) == []


def test_old_files_keeps_first_backup_of_each_month():
    names = ["20260901-040000", "20260902-040000", "20261001-040000", "20261002-040000", "20261101-040000", "20261102-040000"]
    files = [Path(f"jalcheck-{n}.tar.zst.gpg") for n in names]
    removed = {p.name[9:17] for p in b.old_files(files, 1, monthly=2)}
    assert removed == {"20260901", "20260902", "20261002"}


@pytest.mark.parametrize("remote", [
    "git@github-awp:bigapple117/auto-word-press.git", "", "x/jalcheck-backup",
    "git@github-backup:someone/jalcheck-backup.git", "/tmp/jalcheck-backup.git"])
def test_offsite_refuses_other_repositories(remote):
    with pytest.raises(b.BackupError):
        b.check_offsite_remote(remote)


def test_offsite_accepts_backup_repository():
    b.check_offsite_remote("git@github-backup:bigapple117/jalcheck-backup.git")


def test_pipeline_success_writes_output(tmp_path):
    out = tmp_path / "o"
    b.run_pipeline("t", [["printf", "abc"], ["cat"]], out)
    assert out.read_bytes() == b"abc"


def test_pipeline_detects_middle_failure(tmp_path):
    with pytest.raises(b.BackupError):
        b.run_pipeline("t", [["printf", "abc"], ["false"], ["cat"]], tmp_path / "o")


def test_pipeline_detects_first_failure(tmp_path):
    with pytest.raises(b.BackupError):
        b.run_pipeline("t", [["false"], ["cat"]], tmp_path / "o")


def _f(*names):
    return [Path(f"jalcheck-{n}.tar.zst.gpg") for n in names]


def test_old_files_counts_days_not_runs():
    # 하루 3번 실행해도 지난 날짜가 밀려 지워지지 않고, 그날 마지막 것만 남는다
    files = _f("20261107-040000", "20261108-040000", "20261109-210000", "20261109-213000", "20261109-213300")
    removed = {p.name[9:24] for p in b.old_files(files, 3)}
    assert removed == {"20261109-210000", "20261109-213000"}


def test_old_files_monthly_not_taken_by_current_month_reruns():
    files = _f("20260801-040000", "20260901-040000", "20261001-040000", "20261101-040000", "20261101-120000",
               *[f"202611{d:02d}-040000" for d in range(2, 8)])
    kept = set(files) - set(b.old_files(files, 7, 3))
    assert {p.name[9:17] for p in kept} == {"20260801", "20260901", "20261001", *[f"202611{d:02d}" for d in range(1, 8)]}
    assert len(kept) == 10


def test_guard_stops_when_local_backups_vanished(monkeypatch):
    remote = _f(*[f"202611{d:02d}-040000" for d in range(1, 8)])
    monkeypatch.setattr(b, "remote_names", lambda r: [p.name for p in remote])
    with pytest.raises(b.BackupError):
        b.guard_remote("x", _f("20261108-040000"))


def test_guard_passes_normal_day(monkeypatch):
    remote = _f(*[f"202611{d:02d}-040000" for d in range(1, 8)])
    monkeypatch.setattr(b, "remote_names", lambda r: [p.name for p in remote])
    b.guard_remote("x", _f(*[f"202611{d:02d}-040000" for d in range(1, 9)]))


def test_guard_passes_empty_repository(monkeypatch):
    monkeypatch.setattr(b, "remote_names", lambda r: [])
    b.guard_remote("x", _f("20261108-040000"))


def test_guard_after_month_first_day_reruns(monkeypatch):
    # 11-09에 3번 실행 → 예전 규칙이 GitHub에 그날 첫 백업을 남긴 상태. 서버 안 정리 뒤에도 보호가 잘못 막지 않아야 한다
    local = _f("20261109-213053", "20261109-213301", "20261109-213349",
               *[f"202611{d:02d}-040000" for d in range(10, 17)])
    local = [p for p in local if p not in b.old_files(local, b.KEEP_LOCAL, b.KEEP_LOCAL_MONTHLY)]
    remote = _f("20261109-213053", *[f"202611{d:02d}-040000" for d in range(10, 17)])
    monkeypatch.setattr(b, "remote_names", lambda r: [p.name for p in remote])
    b.guard_remote("x", local)
    kept = set(local) - set(b.old_files(local, b.KEEP_OFFSITE, b.KEEP_OFFSITE_MONTHLY))
    assert Path("jalcheck-20261109-213349.tar.zst.gpg") in kept   # 11월 월별본은 그날 마지막 백업
