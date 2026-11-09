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
