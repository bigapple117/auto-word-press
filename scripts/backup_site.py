#!/usr/bin/env python3
"""사이트 전체를 암호화해 백업한다. 매일 jalcheck-backup.timer가 실행한다.

담는 것: 워드프레스 DB, 사진, 작업·주제 장부, 사이트 설정과 비밀값 파일.
담지 않는 것: 쇼츠 영상과 스톡 재료(크고 다시 만들 수 있음), 암호 파일 자체.
보관: 서버 안 ~/backups/auto에 최근 14개 + 월별 6개, BACKUP_GIT_REMOTE가 있으면 그 저장소에 최근 7개 + 월별 3개.
월별본은 각 달의 첫 백업이다. 데이터가 망가진 채 며칠이 지나도 망가지기 전 상태로 돌아갈 수 있게 남긴다.
암호는 ~/.backup_pass에 있고, 서버를 잃어도 복원하려면 서버 밖에도 보관해야 한다.
복원: gpg --decrypt 파일 | zstd -dc | tar -xf - -C 빈폴더
"""
from __future__ import annotations

import fcntl
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

log = logging.getLogger("backup")

BACKUP_DIR = Path.home() / "backups" / "auto"
PASS_FILE = Path.home() / ".backup_pass"
LOCK_FILE = ROOT / "data" / ".backup.lock"
KEEP_LOCAL = 14
KEEP_OFFSITE = 7
KEEP_LOCAL_MONTHLY = 6
KEEP_OFFSITE_MONTHLY = 3
MAX_OFFSITE_FILE = 95 * 1024 * 1024      # GitHub는 파일 하나에 100MB까지만 받는다
PREFIX, SUFFIX = "jalcheck-", ".tar.zst.gpg"
# 바깥 저장소는 매번 강제로 덮어쓰므로, 다른 저장소를 잘못 지정해도 지워지지 않게 주소 전체를 고정한다
ALLOWED_REMOTE = "git@github-backup:bigapple117/jalcheck-backup.git"
# 암호화 전 DB 내용은 전원이 나가도 디스크에 남지 않게 메모리 폴더에만 둔다
PLAIN_TEMP_ROOT = Path("/dev/shm")
PLAIN_TEMP_PREFIX = "jalcheck-backup-"
OFFSITE_TEMP_PREFIX = ".offsite-"

SYSTEM_PATHS = [
    "var/www/html/wp-config.php",
    "var/www/html/wp-content/uploads",
    "var/www/html/wp-content/mu-plugins",
    "home/ubuntu/auto-word-press/.env",
    "home/ubuntu/.wp_db_pass",
    "etc/nginx/sites-available/default",
    "etc/nginx/conf.d/cloudflare-realip.conf",
    "etc/ssl/certs/jalcheck.com.pem",
    "etc/ssl/private/jalcheck.com.key",
    "etc/systemd/system/jalcheck-worker.service",
]
REQUIRED_MEMBERS = {"./wordpress.sql", "./jobs.sqlite3", "./topics.sqlite3", "var/www/html/wp-config.php"}

README = """# jalcheck-backup

잘체크 사이트의 암호화 백업입니다. 매일 자동으로 덮어쓰며 최근 7개와 월별 3개만 남습니다. 직접 수정하지 마세요.

복원: gpg --decrypt 파일 | zstd -dc | tar -xf - -C 빈폴더
wordpress.sql은 DB, jobs.sqlite3·topics.sqlite3는 작업 장부, 나머지는 서버 경로 그대로입니다.
"""


class BackupError(Exception):
    def __init__(self, step: str, detail: str = ""):
        super().__init__(f"{step}: {detail}".rstrip(": "))
        self.step = step


def old_files(files: Sequence[Path], keep: int, monthly: int = 0) -> list[Path]:
    """이름에 시각이 들어 있으므로 이름순으로 정렬해, 최근 keep개와 월별본 monthly개를 뺀 나머지를 돌려준다."""
    ordered = sorted(files, key=lambda p: p.name)
    firsts: dict[str, Path] = {}
    for p in ordered:
        firsts.setdefault(p.name[len(PREFIX):len(PREFIX) + 6], p)
    kept = set(ordered[-keep:]) if keep > 0 else set()
    if monthly > 0:
        kept |= set(sorted(firsts.values(), key=lambda p: p.name)[-monthly:])
    return [p for p in ordered if p not in kept]


def check_offsite_remote(remote: str) -> None:
    if remote != ALLOWED_REMOTE:
        raise BackupError("바깥 보관 설정", f"허용된 저장소 주소가 아닙니다: {ALLOWED_REMOTE}만 쓸 수 있습니다")


def run_pipeline(step: str, commands: Sequence[Sequence[str]], output: Path) -> None:
    """명령들을 파이프로 잇고, 중간 명령이 하나라도 실패하면 오류로 처리한다."""
    procs: list[subprocess.Popen] = []
    errs = [tempfile.TemporaryFile() for _ in commands]
    try:
        with open(output, "wb") as out:
            prev = None
            for i, cmd in enumerate(commands):
                last = i == len(commands) - 1
                p = subprocess.Popen(cmd, stdin=prev, stdout=out if last else subprocess.PIPE, stderr=errs[i])
                if prev is not None:
                    prev.close()          # 앞 명령이 받는 쪽 종료를 알 수 있게 부모 쪽 파이프를 닫는다
                prev = p.stdout
                procs.append(p)
        codes = [p.wait() for p in procs]
        for cmd, code, err in zip(commands, codes, errs):
            if code != 0:
                err.seek(0)
                raise BackupError(step, f"{Path(cmd[2] if cmd[0] == 'sudo' else cmd[0]).name} 종료코드 {code}: "
                                        f"{err.read().decode('utf-8', 'replace').strip()[-300:]}")
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.wait()
        for err in errs:
            err.close()


def dump_database(dest: Path) -> None:
    with open(dest, "wb") as out:
        r = subprocess.run(["sudo", "-n", "mysqldump", "--single-transaction", "--routines",
                            "--default-character-set=utf8mb4", "wordpress"],
                           stdout=out, stderr=subprocess.PIPE, timeout=600)
    if r.returncode != 0:
        raise BackupError("DB 내보내기", r.stderr.decode("utf-8", "replace").strip()[-300:])
    with open(dest, "rb") as f:
        f.seek(max(0, dest.stat().st_size - 200))
        if b"Dump completed" not in f.read():
            raise BackupError("DB 내보내기", "내보낸 파일이 끝까지 쓰이지 않았습니다")


def copy_sqlite(src: Path, dest: Path) -> None:
    """쓰는 중인 장부도 안전하게 복사되도록 SQLite 백업 기능을 쓰고 결과를 검사한다."""
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
    target = sqlite3.connect(dest)
    try:
        source.backup(target)
        if target.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise BackupError("장부 복사", f"{src.name} 검사 실패")
    finally:
        source.close()
        target.close()


def gpg_base() -> list[str]:
    return ["gpg", "--batch", "--yes", "--quiet", "--pinentry-mode", "loopback", "--passphrase-file", str(PASS_FILE)]


def build_archive(staging: Path, output: Path) -> None:
    tar = ["sudo", "-n", "tar", "-cf", "-", "-C", str(staging), ".", "-C", "/", *SYSTEM_PATHS]
    zstd = ["zstd", "-q", "-6", "-T1", "-c"]
    gpg = [*gpg_base(), "--symmetric", "--cipher-algo", "AES256", "--compress-algo", "none", "--output", "-"]
    run_pipeline("압축·암호화", [tar, zstd, gpg], output)


def verify_archive(archive: Path, listing: Path) -> None:
    """암호를 풀고 목록까지 읽어 본 뒤 필수 항목이 있는지 확인한다."""
    run_pipeline("백업 검사", [[*gpg_base(), "--decrypt", str(archive)], ["zstd", "-q", "-dc"], ["tar", "-tf", "-"]], listing)
    members = {line.rstrip("/") for line in listing.read_text(encoding="utf-8", errors="replace").splitlines()}
    missing = REQUIRED_MEMBERS - members
    if missing:
        raise BackupError("백업 검사", f"빠진 항목: {', '.join(sorted(missing))}")


def create_backup() -> Path:
    if not PASS_FILE.exists():
        raise BackupError("준비", f"암호 파일이 없습니다: {PASS_FILE}")
    stamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d-%H%M%S")
    final = BACKUP_DIR / f"{PREFIX}{stamp}{SUFFIX}"
    part = final.with_name(final.name + ".part")
    try:
        with tempfile.TemporaryDirectory(dir=PLAIN_TEMP_ROOT, prefix=PLAIN_TEMP_PREFIX) as tmp:
            staging = Path(tmp) / "content"
            staging.mkdir()
            dump_database(staging / "wordpress.sql")
            for name in ("jobs.sqlite3", "topics.sqlite3"):
                copy_sqlite(ROOT / "data" / name, staging / name)
            build_archive(staging, part)
            verify_archive(part, Path(tmp) / "listing.txt")
        part.replace(final)
    finally:
        part.unlink(missing_ok=True)
    return final


def all_backups() -> list[Path]:
    return [p for p in BACKUP_DIR.iterdir() if p.name.startswith(PREFIX) and p.name.endswith(SUFFIX)]


def prune_local() -> None:
    for p in old_files(all_backups(), KEEP_LOCAL, KEEP_LOCAL_MONTHLY):
        p.unlink()
        log.info("오래된 백업 삭제: %s", p.name)


def push_offsite(remote: str) -> None:
    """최근 백업만 담은 새 기록 하나로 덮어써, 저장소 크기가 계속 늘지 않게 한다."""
    check_offsite_remote(remote)
    backups = all_backups()
    remove = set(old_files(backups, KEEP_OFFSITE, KEEP_OFFSITE_MONTHLY))
    latest = sorted((p for p in backups if p not in remove), key=lambda p: p.name)
    for f in latest:
        if f.stat().st_size > MAX_OFFSITE_FILE:
            raise BackupError("바깥 보관", f"{f.name}이 GitHub 파일 크기 한도를 넘습니다")
    env = {**os.environ, "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=30"}

    def git(repo: Path, *args: str) -> None:
        r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=jalcheck-backup",
                            "-c", "user.email=backup@jalcheck.invalid", *args],
                           env=env, capture_output=True, timeout=1200)
        if r.returncode != 0:
            raise BackupError("바깥 보관", f"git {args[0]}: {r.stderr.decode('utf-8', 'replace').strip()[-300:]}")

    with tempfile.TemporaryDirectory(dir=BACKUP_DIR, prefix=OFFSITE_TEMP_PREFIX) as tmp:
        repo = Path(tmp)
        for f in latest:
            os.link(f, repo / f.name)
        (repo / "README.md").write_text(README, encoding="utf-8")
        git(repo, "init", "-q", "-b", "main")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", f"백업 {latest[-1].name}")
        git(repo, "push", "-q", "--force", remote, "main")


def notify(text: str) -> None:
    try:
        from src.config import load_settings
        from src.telegram_notifier import TelegramNotifier
        TelegramNotifier.from_settings(load_settings()).send_text(text)
    except Exception as exc:  # noqa: BLE001 - 알림 실패가 원래 오류를 가리지 않게
        log.error("텔레그램 알림 실패: %s", exc.__class__.__name__)


def offsite_remote() -> str:
    from src.config import load_settings
    load_settings()                 # .env를 환경 변수로 읽어 들인다
    return os.environ.get("BACKUP_GIT_REMOTE", "").strip()


def remove_stale_temp() -> None:
    """이전 실행이 강제 종료돼 남긴 임시 폴더를 지운다(잠금을 잡은 뒤에만 호출)."""
    for d in [*PLAIN_TEMP_ROOT.glob(PLAIN_TEMP_PREFIX + "*"), *BACKUP_DIR.glob(OFFSITE_TEMP_PREFIX + "*")]:
        shutil.rmtree(d, ignore_errors=True)
        log.warning("이전 실행의 임시 폴더 정리: %s", d.name)
    for p in BACKUP_DIR.glob("*.part"):
        p.unlink(missing_ok=True)


def run() -> int:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    remove_stale_temp()
    try:
        final = create_backup()
        prune_local()
    except Exception as exc:  # noqa: BLE001 - 어떤 실패든 사용자에게 알린다
        log.exception("백업 실패")
        notify(f"[백업 실패] 오늘 사이트 백업이 만들어지지 않았습니다.\n{str(exc)[:400]}")
        return 1
    log.info("백업 완료: %s (%.1fMB)", final.name, final.stat().st_size / 1024 / 1024)
    try:
        remote = offsite_remote()
        if not remote:
            log.info("바깥 보관 설정이 없어 서버 안에만 보관합니다")
            return 0
        push_offsite(remote)
        log.info("바깥 보관 완료")
    except Exception as exc:  # noqa: BLE001
        log.exception("바깥 보관 실패")
        notify(f"[백업 경고] 서버 안 백업은 됐지만 GitHub 보관에 실패했습니다.\n{str(exc)[:400]}")
        return 1
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.umask(0o077)
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.warning("다른 백업이 실행 중이라 건너뜁니다")
            return 0
        return run()


if __name__ == "__main__":
    sys.exit(main())
