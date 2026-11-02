# HANDOFF

- 현재 상태: 기획 및 아키텍처 설계 완료 (`PLAN.md`), Claude 구현 착수 대기
- 지시자: 사용자
- 이전 작업자 (기획/설계): Gemini (Codex 자문 2회 완료)
- 차기 메인 작업자 (구현): Claude (Claude Code)
- 검토자: Codex (기능 단위 완료 후)

## 작업 히스토리 요약
1. 1단계 기획: 구글 시트 주제 입력 ➡️ AI 제목/본문(실용 위젯) 생성 ➡️ 워드프레스 REST API 자동 발행 ➡️ 텔레그램 승인/알림 파이프라인 수립.
2. 2단계 기획 확장: 무료 스톡 API(Pexels, Pixabay) 문단별 이미지/영상 자동 삽입 + AI 성우(Edge-TTS) + 자막 싱크 + FFmpeg 숏폼(1080x1920) 자동 렌더링 + 웹 대시보드(진행률 콘솔) 통합 기획 확정.
3. 기획 상세 문서: `/home/ubuntu/auto-word-press/PLAN.md`에 아키텍처, 디렉터리 구조, 열 구성, 4단계 렌더링 파이프라인, 테스트 계획을 완벽하게 작성 완료.

## Claude가 이어받을 구현 작업 (Next Steps)
1. Python 가상환경(`.venv`) 및 의존성 구성 (`requirements.txt`: `requests`, `gspread`, `google-genai`, `openai`, `edge-tts`, `pillow`, `pydantic`, `fastapi`, `uvicorn` 등)
2. ARM64 호환 FFmpeg 및 한글 폰트(`assets/fonts/`) 준비
3. 모듈별 구현 (`PLAN.md` 5절 파일 구조 준수):
   - `templates/prompt_template.py`: 블로그 HTML + 쇼츠 대본 마스터 JSON 프롬프트
   - `src/stock_media.py`: Pexels / Pixabay API 검색 및 다운로드
   - `src/tts_engine.py`: Edge-TTS 음성 합성 및 자막 타임스탬프(.ass) 생성
   - `src/video_renderer.py`: FFmpeg 필터그래프 기반 세로형 쇼츠 합성
   - `src/wp_client.py`: WordPress REST API 포스팅 및 미디어 업로드
   - `src/sheet_manager.py`: Google Sheets 연동
   - `src/web_ui.py`: 실시간 4단계 콘솔 UI (두 번째 레퍼런스 스타일)
   - `src/orchestrator.py`: 단일/배치 오케스트레이션
4. 단위 테스트 및 통합 드라이런 검증 (`tests/`)

## 2026-10-31 새 운영 서버 연결 (지시: 사용자 / 작업: Claude, PC 쪽 Claude 세션 / 검토: 없음, 코드 변경 없음)
- 결정: 워드프레스와 자동 생성 프로그램을 모두 새 오라클 서버에 둠 (사용자 선택 3번)
- 새 서버: Ubuntu 24.04 ARM64, CPU 1개, 메모리 5.8GiB, 디스크 41G 남음. 접속 주소는 이 서버 `~/.ssh/known_hosts`와 사용자에게 확인
- 이미 설치됨: nginx, PHP 8.3-fpm, MariaDB, 워드프레스 7.1.4(`/var/www/html`), python3, git, Node, Claude Code
- 설치 안 됨: ffmpeg, cloudflared, SSL(https), 도메인
- 워드프레스 웹 설치는 사용자가 직접 마침 (비밀번호는 사용자가 보관)
- 이 서버 공개키를 새 서버에 등록함 (사용자가 PC 세션에서 직접 승인)
- 실제 검증: 이 서버에서 키 파일 없이 SSH 접속 성공, `install.php`가 "이미 설치됨"으로 응답
- 남은 일: ffmpeg와 한글 폰트 설치, 도메인과 https 적용, CPU 증설 검토(무료 등급 여유분), PLAN.md 순서대로 구현

## 2026-11-01 운영 서버 접속 별명 등록 (지시: 사용자 / 작업: Claude / 검토: 없음, 코드 변경 없음)
- 운영 서버(워드프레스): 공인 IP 168.110.20.97, 사설 IP 10.0.0.118, 호스트 이름 instance-20261031-0906
- 이 서버 `~/.ssh/config`에 별명 `wp-server` 등록 → `ssh wp-server`로 접속
- 실제 검증: 키 접속 성공, 서버 안에서 워드프레스 첫 화면 응답 200, ffmpeg 아직 없음
- 참고: 사설 IP로도 연결은 되나 호스트 키 미등록 상태라 공인 IP 사용

## 2026-11-01 작업 장소를 운영 서버로 이전 (지시: 사용자 / 작업: Claude / 검토: 없음, 코드 변경 없음)
- 결정: 앞으로 auto-word-press 개발·실행은 운영 서버(`wp-server`)의 `~/auto-word-press`에서 그 서버의 Claude로 진행
- 이 서버(ubuntu@instance-20260913-2037)의 `~/auto-word-press`는 보관용. 여기서는 코드를 고치지 않음 (두 곳 동시 수정 방지)
- 운영 서버에 서버 맞춤 공통 규칙 `~/.claude/CLAUDE.md` 작성 (원본은 이 서버 `~/.claude/CLAUDE.md`)
- 운영 서버에 `~/.local/bin/codex`, `~/.local/bin/gemini` 있음 → 위임 가능
- 남은 일: 운영 서버에서 `git pull` 후 `scripts/setup_media_tools.sh` 실행(FFmpeg·한글 폰트), 이후 PLAN.md 순서대로 구현

## 2026-11-01 FFmpeg·한글 폰트 설치 확인 (지시: 사용자 / 작업: Claude, 운영 서버 / 검토: 없음, 코드 변경 없음)
- `scripts/setup_media_tools.sh` 실행: 오늘 02:41에 이미 받아 둔 상태라 다시 받지 않고 넘어감 (정상 종료)
- 설치된 것: `tools/ffmpeg/{ffmpeg,ffprobe}` 7.0.2 정적 빌드(ARM64), `assets/fonts/NotoSansCJKkr-{Regular,Bold}.otf` (Git 제외 대상)
- 실제 검증: libx264·aac 인코더와 `ass`/`subtitles` 필터 있음. 1080x1920 2초 영상에 한글 .ass 자막을 입혀 렌더링 성공(약 1.5초 걸림), 캡처 화면에서 한글이 정상 표시됨
- 주의: 이 빌드에는 `drawtext` 필터가 없음 → 화면 글자는 모두 .ass 자막(libass)으로 넣어야 함. 자막 스타일 글꼴 이름은 `Noto Sans CJK KR`, ffmpeg에 `ass=파일:fontsdir=assets/fonts` 지정
- 남은 일: PLAN.md 순서대로 구현 (.venv와 requirements.txt부터)

## 2026-11-01 운영 서버 GitHub 연결 (지시: 사용자 / 작업: Claude / 검토: 없음, 코드 변경 없음)
- 운영 서버에 GitHub 인증 수단이 없어 push 실패 → 사용자 선택 1번(저장소 전용 Deploy key)
- 키: `~/.ssh/github_auto_word_press`, `~/.ssh/config` 별명 `github-awp`, GitHub 호스트 키 지문 공식값과 대조 후 `known_hosts` 등록
- 사용자가 GitHub 저장소 Deploy keys에 쓰기 권한으로 공개키 등록, 승인 후 origin을 `git@github-awp:bigapple117/auto-word-press.git`로 변경
- 저장소 전용 git 작성자 설정: bigapple117 (이전 커밋과 동일)
- 실제 검증: `38a932e` push 성공, `main...origin/main` 일치
- 남은 일: PLAN.md 순서대로 구현 (.venv와 requirements.txt부터)

## 2026-11-02 도메인 jalcheck.com + https 적용 (지시: 사용자 / 작업: Claude / 검토: Codex 자문(순서 변경 권고 반영))
- 결정: 블로그 주제 "생활 돈 정보(지원금·제도·절약)", 도메인 jalcheck.com (Cloudflare Registrar, 연 $11.17 자동 연장, Free 요금제)
- DNS(Cloudflare, 프록시 켬): A `@` → 168.110.20.97, CNAME `www` → jalcheck.com
- 인증서: Cloudflare Origin CA(ECDSA, 2041-10-29 만료, jalcheck.com·*.jalcheck.com). 키는 서버에서 생성 `/etc/ssl/private/jalcheck.com.key`(root 600, 서버 밖 반출 없음), 인증서 `/etc/ssl/certs/jalcheck.com.pem`
- Cloudflare 설정(사용자): SSL/TLS Full (strict), Always Use HTTPS 켬
- nginx: 80 → `https://jalcheck.com` 301, 443 http2, www → 루트 도메인 301. 사본 `deploy/nginx/default.conf`
- 워드프레스: siteurl/home `http://168.110.20.97` → `https://jalcheck.com`, 기본 페이지 2개(ID 2 예제, 3 개인정보 처리방침 초안) 본문 IP 링크 치환. wp-config 주소 상수·객체 캐시 없음 확인
- 방화벽: 서버 iptables·오라클 Security List 모두 443 이미 허용(변경 없음)
- 백업: `~/backups/2026-11-02-https/` (nginx 원본, iptables 규칙, DB 전체 덤프)
- 되돌리기: 백업 nginx 설정 복원+reload, DB siteurl/home 원복(또는 덤프 복원), Cloudflare SSL 모드 원복. 301은 브라우저에 캐시될 수 있음
- 실제 검증: https 200, http/www/IP 접속 모두 https://jalcheck.com 으로 301, wp-admin 로그인 사용자 확인, 페이지 내 링크 전부 https://jalcheck.com, wp-json 200
- 남은 일: 개인정보 처리방침 실제 내용 작성(애드센스 승인 필요), 예제 페이지·샘플 글 정리, (선택) nginx에 Cloudflare 실제 방문자 IP 복원 설정, PLAN.md 구현(.venv부터)

## 2026-11-02 파이썬 실행 환경 구성 (지시: 사용자 / 작업: Claude / 검토: 없음, 의존성 구성만)
- 서버에 pip·python3-venv 패키지가 없어 sudo 설치 대신 `python3 -m venv --without-pip .venv` + 공식 get-pip.py로 프로젝트 안에만 pip 설치
- `requirements.txt`(버전 고정): requests, python-dotenv, pydantic, google-genai, gspread, edge-tts, pillow, fastapi, uvicorn / `requirements-dev.txt`: pytest
- openai는 유료 API라 이번엔 뺌. AI 글 생성 모듈 만들 때 Gemini 무료 등급으로 충분한지 보고 필요하면 추가
- 텔레그램은 별도 라이브러리 없이 requests로 호출 예정, 작업 상태 DB는 내장 sqlite3 사용
- `.gitignore`에 `.venv/`, `.env`, `__pycache__/` 추가
- 실제 검증: `pip check` 충돌 없음, 전체 import 성공, edge-tts 한국어 음성(ko-KR-SunHiNeural) 합성 성공(5.04초 mp3 + 문장별 시작·끝 시간 자막 생성)
- 참고: 한국어 성우 3종 사용 가능(SunHi 여, InJoon 남, HyunsuMultilingual 남)
- 남은 일: PLAN.md 5절 순서로 src/config.py, .env.example, src/db.py부터 구현

## 2026-11-02 설정 로더·작업 장부 구현 (지시: 사용자 / 작업: Claude / 검토: Codex 3회, 최종 PASS — REVIEW.md)
- 추가: `src/config.py`(.env 읽기, 기능별 require(), 비밀값 repr 가림), `.env.example`, `src/db.py`(SQLite 작업 장부), `tests/`(35개), `pytest.ini`, `.gitignore`에 `data/`
- db.py 핵심: 시트 행 등 같은 출처 중복 등록 방지(source+source_ref 고유), 상태 흐름 강제(queued→generating→tts→media→rendering→awaiting_approval→publishing→published, failed/canceled), 단계별 체크포인트 저장, 글 번호 1회 기록(record_post)·글 번호 중복 연결 금지, 발행 선점 begin_publishing(동시 승인·취소 경쟁 차단), 재시작 복구 recover_interrupted, 구조 버전(user_version=1) 불일치 DB 거부
- 고친 버그: `.env`에 빈 값(`TTS_VOICE=`)이면 기본값 대신 빈 값이 들어가던 문제
- 실제 검증: `.venv/bin/python -m pytest -q` 35 passed (동시 4~5개 연결 선점 시험 포함)
- Codex 자동 검토 스크립트(codex-review.py)는 이 서버에서 bwrap 오류로 BLOCKED → codex-consult.py에 코드 직접 전달 방식으로 대체
- 남은 일: wp_client에서 글에 작업 번호 표시 + 생성 전 조회(중복 초안 방지, 필수), 실행기 시작 시 recover_interrupted 1회 호출, GEMINI_MODEL 기본값(gemini-2.5-flash) 실제 사용 가능 모델로 확인, 다음 구현 templates/prompt_template.py·src/ai_generator.py
