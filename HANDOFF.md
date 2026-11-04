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

## 2026-11-02 Gemini 키 등록·모델 확인 (지시: 사용자 / 작업: Claude / 검토: 없음)
- `scripts/set_secret.sh` 추가: .env 값을 화면에 안 보이게 입력(.env.example에 있는 이름만, 파일 권한 600)
- `.env` 생성(600, Git 제외), GEMINI_API_KEY 저장. 사용자가 채팅으로 키 전달(터미널 사용 어려움) → 결제 미연결 무료 키로 안내, 필요 시 AI Studio에서 교체
- gemini-2.5-flash는 "새 사용자에게 제공 안 함"(404) → 기본 모델을 gemini-3.8-flash로 변경(config, .env.example, .env)
- 실제 검증: 키로 모델 목록 조회 성공, gemini-3.8-flash 한국어 응답 성공(약 20초), gemini-3.5-flash-lite 응답 1.2초 — 글 생성은 3.8-flash, 빠른 보조 작업은 lite 후보
- 남은 일: templates/prompt_template.py, src/ai_generator.py

## 2026-11-02 자료 기반 글·쇼츠 대본 생성기 (지시: 사용자 / 작업: Claude / 검토: Codex 5회 — REVIEW.md)
- 결정: Gemini 구글 검색은 무료 키에서 막힘(3.x는 429, 2.5는 새 사용자 404 직접 확인) → "자료 찾기"와 "글쓰기" 분리. 자료 찾기는 NAVER API HUB(사용자 가입 진행 중) 또는 Tavily 예정, 시트 참고 주소 입력도 지원 예정
- 추가: `src/source_fetcher.py`(공식 페이지 본문 추출, 내부 주소 차단·IP 고정·시간 제한), `src/content_schema.py`(블로그+쇼츠 JSON 형식), `templates/prompt_template.py`(사실·문체 규칙), `src/ai_generator.py`(재시도·예비 모델 gemini-3.5-flash-lite·품질 검사·숫자 대조 경고), `src/blog_html.py`(안전한 HTML, 출처 목록), `scripts/dry_run_content.py`(발행 없이 결과 파일 생성)
- 실제 검증: 국세청 근로장려금 페이지 3개로 생성 성공 2회(55~72초, 1회차 통과). 본문 숫자·날짜·전화번호·조정률을 원문과 직접 대조해 일치. 자료 없이 물으면 신청기간을 틀리게 답함(5.31 vs 공식 6.1) → 자료 기반 필요성 확인
- 결과 파일: `data/dryrun/20261102-011815/`(Git 제외)
- 테스트: 86개 통과
- 남은 일: 검색 서비스 연결(NAVER API HUB 키 대기), 표에서 일부 행만 뽑을 때 원래 기호(가·나·라…) 빼도록 지시문 보완 검토, 다음 구현 tts_engine.py(.ass 자막) → stock_media.py → video_renderer.py → wp_client.py(작업 번호 표시로 중복 초안 방지 필수)

## 2026-11-02 네이버 검색 연결(공식 자료 자동 찾기) (지시: 사용자 / 작업: Claude / 검토: 없음 — 외부 검색 호출 추가, 보안 경계(source_fetcher)는 변경 없음)
- 사용자가 NAVER API HUB(console.ncloud.com)에서 웹문서·뉴스·검색어트렌드 신청, 키를 `.env`의 NAVER_CLIENT_ID/SECRET에 저장(config SECRET_FIELDS에 secret 추가)
- 공식 문서 확인: 호스트 `https://naverapihub.apigw.ntruss.com`, `/search/v1/webkr`, `/search/v1/news`(sort=date), `POST /search-trend/v1/search`, 헤더 `X-NCP-APIGW-API-KEY-ID`/`X-NCP-APIGW-API-KEY`. 한시 무료(유료 전환 시 사전 공지 예정), 한도 문서마다 일 25,000회/월 775,000회로 다름
- 추가: `src/source_finder.py`(웹문서 결과 중 .go.kr·gov.kr·korea.kr·주요 공공기관만 근거 후보, websquare 등 JS 페이지 제외, 뉴스·트렌드 조회), `fetch_sources`에 본문 중복 제거·max_sources
- 생성기 개선: 장면 수×장면당 글자 수 안내, 쇼츠 길이만 틀리면 대본만 고쳐 쓰기(블로그 재작성 없음), 최근 뉴스 제목을 "변경 신호"로 전달(숫자 근거 금지, 자료와 다르면 도입부에 확인 필요 명시)
- 실제 검증: 주제만으로 "청년도약계좌 가입 조건과 혜택" 생성 성공. 처음엔 2024~25년 자료로 현재 가입 가능한 것처럼 씀 → 뉴스 신호 반영 후 도입부에 2025년 12월 가입 종료 언급·최신 확인 안내, 기존 가입자 중심으로 바뀜. 테스트 105개 통과
- 알려진 한계: 서버가 일본(오라클)이라 서민금융진흥원(kinfa.or.kr) 등 일부 공공기관이 해외 접속 차단(연결 시간 초과). 청년센터 등 JS 페이지는 본문 수집 불가
- 남은 일: 주제 선정 시 종료·개편된 제도 걸러내기(검색어트렌드·뉴스 활용), tts_engine.py부터 영상 파이프라인

## 2026-11-02 AI 성우·자막 엔진 (지시: 사용자 / 작업: Claude / 검토: 없음 — 외부 입력 경계 없음, 로컬 파일 생성만)
- 추가: `src/tts_engine.py` — 장면별 edge-tts 합성(단어별 시간), wav로 변환해 0.15초 간격으로 이어 붙임, 장면별 시작·끝 시간, 말하는 타이밍 자막(.ass, 문장부호·쉼·글자 수 기준, 숫자+단위·연속 숫자 안 끊음), 목표보다 12% 넘게 길면 말 속도 올려 재합성(최대 +25%), 네트워크 오류 재시도. 결과 narration.wav / captions.ass / timeline.json
- 보정: 실측 대본 200자 = 31.5초 → CHARS_PER_SECOND 5.0 → 6.2 (45초 쇼츠 대본 목표가 약 223~321자로 늘어남)
- 실제 검증: 근로장려금 대본 5장면 합성 6초, 단색 배경+자막+음성 1080x1920 시험 영상 렌더 17초(nice), 캡처에서 한글 굵은 글꼴·테두리·위치 정상. 테스트 124개 통과
- 결과 파일: `data/dryrun/20261102-011815/tts/`(Git 제외)
- 진행 중: 해외 접속 차단(kinfa 등) 해결책 조사를 포크 세션이 조사만 진행(코드 변경 없음)
- 남은 일: 자막 크기 미세 조정(필요 시), Pexels 키 받으면 stock_media.py → video_renderer.py

## 2026-11-02 해외 접속 차단 공공기관 자료 대신 읽기 (지시: 사용자 / 작업: Claude / 검토: Codex 2회, 최종 PASS — REVIEW.md)
- 문제: 서버가 일본이라 kinfa.or.kr(서민금융진흥원) 등 해외 차단 사이트 연결 시간 초과
- 해결: 직접 읽기 실패 시 Gemini url_context(구글 서버가 가져옴, 무료 lite 모델)로 본문을 옮겨 적게 함. `src/remote_reader.py`. 결과 Source.via="gemini", 생성 경고 "AI가 옮겨 적은 자료(원문 대조 권장)"
- 보안: 정책 거절(내부 주소·포트·형식·계정 URL)은 BlockedUrlError로 대신 읽기 안 함, 대신 읽기 전에도 공인 IP 확인, 대신 읽기 최대 3회·한도/인증 오류 시 중단
- dry run 결과 폴더에 `sources.json`(읽어 온 자료 원문) 저장 → 사람이 대조 가능
- 실제 검증: "청년도약계좌 가입 조건과 혜택" 재생성 → kinfa 2페이지 대신 읽기 성공, 글 도입부에 "신규 가입 2025.12.31 종료" 명시. 원문 "※ 운영 종료 상품 / 신규 가입 '25.12.31까지 운영"과 일치. 테스트 130개 통과
- 남은 한계: 청년센터(youthcenter.go.kr)처럼 Gemini도 본문을 못 찾는 페이지 있음
- 남은 일: tts_engine.py부터 영상 파이프라인(Pexels 키 필요: stock_media)

## 2026-11-02 쇼츠 영상 합성기 (지시: 사용자 / 작업: Claude / 검토: 없음 — 로컬 파일만 다룸, 외부 입력은 stock_media 단계에서 검토 예정)
- 이어받기: 포크 세션의 459e4e6(해외 차단 사이트 대신 읽기, Codex PASS)을 pull해 확인, 테스트 130개 통과 확인. 내 3e306c7에 포크의 중간 파일이 `git add -A`로 섞여 들어갔던 문제 → 이후 커밋은 내가 바꾼 파일만 지정해서 올림
- Pexels: 신규 API 키 발급 일시 중단 → Pixabay로 대체 예정(사용자 키 대기)
- 추가: `src/video_renderer.py` — 장면별 배경(영상: 화면 채우기·반복, 사진: 천천히 이동, 없음: 어두운 움직이는 그라데이션) + 성우 + 자막 + 위쪽 제목(노란 글씨 두 줄, `tts_engine.write_ass(title=...)`), 1080x1920 30fps H.264/AAC, nice 15, 서버 전체 한 번에 하나(파일 잠금), 임시 파일로 렌더 후 ffprobe로 크기·소리·길이 확인하고 교체
- 실제 검증: 근로장려금 쇼츠(그라데이션·시험 영상·시험 사진 배경) 32초 영상 렌더 67초, 캡처에서 제목·자막·배경 정상. 테스트 142개 통과(실제 소형 렌더 포함)
- 남은 일: Pixabay 키 받으면 `src/stock_media.py`(장면별 영상 검색·다운로드, 외부 다운로드라 Codex 검토), 그다음 wp_client.py

## 2026-11-02 Pixabay 장면 배경 수집 (지시: 사용자 / 작업: Claude / 검토: Codex 3회, 최종 PASS — REVIEW.md)
- Pixabay 키 `.env` 저장(PIXABAY_API_KEY). Pexels는 신규 키 발급 중단이라 미사용
- 추가: `src/stock_media.py` — 장면별 세로 영상 우선 → 큰 가로 영상 → 세로 사진 → 없으면 그라데이션, 한 쇼츠 안 중복 금지, 검색 결과 24시간 저장, 다운로드는 Pixabay https만·크기/시간 제한·고유 임시 파일, ffprobe로 깨진 파일 거름, cropdetect로 검은 띠 영상 거름, 보관함 3GB 상한·디스크 여유 3GB 검사. 블로그용 `pick_blog_images()`
- `scripts/dry_run_short.py`: dry run 폴더 → 성우·자막 → 배경 → 렌더(발행 안 함)
- 검색어 규칙 보완(content_schema): 특정 나라 지폐·달력·국기·글자 보이는 장면 피하기(달러·March 달력이 나온 문제)
- 렌더 공용 잠금 `data/.render.lock`(Git 제외 폴더)
- 실제 검증: 근로장려금 쇼츠 실제 Pixabay 영상 5개로 32초 렌더(합성 87초, 배경 찾기 3~9초), 결과 7.8MB. 테스트 162개 통과
- 남은 일: 새 규칙으로 글을 새로 만들어 쇼츠 확인, wp_client.py(워드프레스 응용 프로그램 비밀번호 필요, 작업 번호 표시로 중복 초안 방지), telegram_notifier.py, orchestrator.py

## 2026-11-02 워드프레스 임시 글 저장·발행 클라이언트 (지시: 사용자 / 작업: Claude / 검토: Codex 3회, 최종 PASS — REVIEW.md)
- 사용자가 관리자 계정(bigapple117)의 애플리케이션 비밀번호 `auto-publisher` 발급 → `.env`의 WP_USERNAME/WP_APP_PASSWORD 저장(화면 출력 없음). 인증 확인: administrator, 발행 권한 있음
- 확인: REST API·애플리케이션 비밀번호 인증 켜짐, 업로드 한도 64MB(PHP·nginx). 사이트 이름 "테스트", 시간대 UTC → 사이트 정리 때 바꿀 것
- 추가: `src/wp_client.py`(https 강제, 리다이렉트 거부, 작업 표시 주석으로 중복 방지, 전 페이지 검색, 작업별 잠금, 발행 글 수정 금지, 발행 두 번 눌러도 안전, 미디어 jcjob-<id>-<n> 이름으로 재업로드 방지, 오류에 비밀번호 미노출), `scripts/dry_run_publish.py`(임시 글로만 올림, 사진·출처 기록 재사용)
- 실제 검증: 근로장려금 글을 임시 글 13번으로 저장(사진 6장·표 3개·참고 자료·대표 사진·요약·영문 주소). 4회 재실행해도 글 1개·사진 6장. 발행하지 않음. 테스트 182개 통과
- 남은 일: 사용자에게 임시 글 13번 미리보기 확인 요청, telegram_notifier.py(미리보기·승인), orchestrator.py(장부 db.py와 전체 연결, 시작 시 recover_interrupted), 사이트 기본 정리(이름·시간대·개인정보 처리방침·예제 글)

## 2026-11-02 블로그 사진 크기 문제 수정 (지시: 사용자 / 작업: Claude / 검토: 없음 — 화면 표시 개선, 보안 경계 변경 없음)
- 사용자 캡처: 사진 비율이 제각각이고 글보다 지나치게 커 보임, 대표 사진이 본문 첫 사진으로 한 번 더 나옴
- 수정: `normalize_blog_image()`로 본문 사진을 가운데 기준 16:9, 1200x675 JPEG(품질 82)로 통일(용량 68~140KB). img에 width/height·wp-image-<id> 클래스 → 워드프레스가 srcset(화면별 크기) 자동 적용. 대표 사진은 본문에 다시 넣지 않음
- 실제 검증: 새 시험 임시 글 22번(작업 900002) — 대표 사진 1장 + 본문 5장, srcset 5개. 테스트 184개 통과
- 정리 대기: 예전 시험 임시 글 13번과 그 사진 6장(jcjob-900001-*)은 사용자 확인 후 삭제

## 2026-11-02 사람이 쓴 블로그처럼 글·화면 개편 (지시: 사용자 / 작업: Claude / 자문: Gemini·Codex / 검토: Codex 2회 — REVIEW.md)
- 사용자 피드백: 글과 표 가독성 낮음, AI 블로그 같음 → Gemini(조사)·Codex(설계) 자문 후 종합, 사용자 승인(1번: 사이트 공통 CSS 포함)
- 스키마(`src/content_schema.py`): 소제목 = 블록 목록(paragraph/table/checklist/note, 순서 자유), 문단은 spans(strong 불리언으로만 강조), takeaway(핵심 한 줄, 2~3곳), image_query 선택(글 전체 최대 2곳), intro·closing도 spans
- 지시문: 사전식 도입 금지·독자 문제부터, 표=숫자 비교 / 본문=구분법·예외·다음 행동(표 행 재나열 금지), 문단 1~3문장 250자, 같은 종결 3연속 금지·안내형 섞기, 강조 소제목당 2곳, 법령 용어 풀어쓰기, 소제목 질문·행동·명사형 혼합, 마무리=다음 행동, 거짓 1인칭 금지
- 자동 검사(`check_style`): 표 반복(같은 문장 안 행 이름+숫자 토큰, 60%), 긴 문단(300자/4문장 초과 불합격, 250자 경고), 강조 과다, 사진 과다, 사전식 도입, 블록 불완전, 같은 어미 3연속·같은 구성 3연속(경고)
- 렌더러: `<div class="jc-article">` 래퍼, 표 caption 위·th scope·가로 스크롤 래퍼·4열 wide·짧은 첫 열 key, 주의/확인 요령 상자, 핵심 요약 상자, 마무리 구분선
- 사이트 CSS: 원본 `deploy/wp/jc-article.css`, `scripts/apply_site_css.py`로 워드프레스 전역 스타일(테마 twentytwentyfive, id 6) 추가 CSS에 `/* jalcheck:start~end */` 구간만 넣음. 적용 전 백업 `~/backups/2026-11-02-site-css/`. 되돌리기: 백업 JSON의 styles를 다시 넣거나 사이트 편집 → 스타일 → 추가 CSS에서 해당 구간 삭제
- 실제 검증: 근로장려금 글 재생성(도입부 독자 상황 중심, 표·본문 중복 없음, 주의·확인 요령 상자, 사진 2곳) → 임시 글 26번(작업 900003). 테스트 195개 통과
- 문제: Gemini 3.8 Flash 503/504가 잦아 이번 생성에 24분(504 한 번에 약 5분 대기). 요청 시간 제한·예비 모델 전환 시점 조정 필요
- 화면 캡처 도구: playwright(.venv, requirements 미포함)와 headless Chromium(`tools/pw-browsers`, 280MB, Git 제외) 받았으나 시스템 라이브러리(libatk 등) 없어 실행 불가 — 설치는 sudo 필요해 보류(사용자 승인 대기)
- 정리 대기: 시험 임시 글 13·22번과 그 사진(jcjob-900001-*, 900002-*)은 사용자 확인 후 삭제

## 2026-11-02 PC 본문 폭·모바일 표 수정, 화면 캡처 도구, 시험 글 정리 (지시: 사용자 / 작업: Claude / 검토: 없음 — 표시 CSS·도구·시험 데이터 정리)
- 사용자 피드백: 26번 글 내용은 괜찮으나 PC에서 좌우를 꽉 채워 읽기 힘듦
- 원인: jc-article.css의 `max-width: 100%`가 테마(Twenty Twenty-Five) 본문 폭 제한을 덮어씀(실측 본문 폭 1340px) → `min(100%, --wp--style--global--content-size)` + 가운데 정렬로 수정(실측 645px). 2~3열 표 min-width 0(휴대폰 화면에 맞춤), 4열 표는 가로 스크롤 유지. `scripts/apply_site_css.py`로 재적용(백업 자동)
- 화면 캡처: 사용자 승인으로 Chromium 필수 시스템 라이브러리 8개만 apt 설치(libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libasound2t64 libatk1.0-0t64 libatspi2.0-0t64 libgbm1). 한글 글꼴은 프로젝트 글꼴을 `~/.local/share/fonts`에 링크(계정 한정). `scripts/screenshot_post.py <글번호>`: 공개 글 페이지에 임시 글 본문을 끼워 실제 테마 그대로 PC(1440)·모바일(390) 캡처, 본문 폭·가로 넘침 출력 → `data/screenshots/`. playwright는 .venv에 설치(requirements-dev 성격, 브라우저는 tools/pw-browsers)
- 사용자 승인으로 시험 임시 글 13·22번과 사진 12장(jcjob-900001-*, 900002-*) 영구 삭제(삭제 전 상태·작업 표시 확인). 남은 임시 글은 26번(작업 900003)
- Gemini 대기 개선: 같은 모델에서 시간 초과(504) 2번이면 바로 예비 모델로(MAX_TIMEOUTS_PER_MODEL). 테스트 196개 통과
- 남은 일: 텔레그램 승인 알림, 오케스트레이터(전체 연결), 사이트 기본 정리(이름 "테스트"·시간대 UTC·개인정보 처리방침·예제 글)

## 2026-11-03 텔레그램 승인 알림 (지시: 사용자 / 작업: Claude / 검토: Codex 2회 — REVIEW.md)
- 사용자가 봇 `@jalcheck_publish_bot`(잘체크 발행 알림) 생성, 토큰·개인 대화방 번호를 `.env`(TELEGRAM_BOT_TOKEN/CHAT_ID)에 저장
- `src/telegram_notifier.py`: 승인 요청(글 화면 캡처 파일 → 쇼츠 영상 → 제목·요약·확인할 것·워드프레스 편집 링크 + [발행][보류][취소]), getUpdates long polling(포트 안 엶), 버튼 = 행동:작업:요청nonce:HMAC, 개인 대화방·본인만 수락, 토큰 미노출
- 임시 글 미리보기 링크가 모바일 비로그인에서 404 → 글 화면 캡처(scripts/screenshot_post.py)를 파일로 함께 보내고 링크는 편집 화면(로그인 유도)으로 변경
- 실제 검증: 승인 요청 2회 전송, 사용자가 보류·취소·발행 버튼 눌러 모두 수신(첫 시험은 수신기가 꺼져 있어 버튼이 '로딩'으로 보였음 → 운영 시 상시 수신 필요). 시험이라 발행 안 함
- 남은 일: orchestrator.py — 장부(db)와 전체 연결: 작업 등록 → 자료 → 글 → 쇼츠 → 임시 글 → 승인 요청(nonce 저장) → 버튼 수신(현재 nonce·상태 확인, begin_publishing으로 원자 선점) → 발행/보류/취소, 시작 시 recover_interrupted, 상시 실행(systemd 등은 사용자 승인 필요)

## 2026-11-03 쇼츠 숫자 표기·신청/조회 바로가기 링크·사진 관련성 (지시: 사용자 / 작업: Claude / 검토: Codex 3회 — REVIEW.md)
- 쇼츠: 사용자가 숫자 그대로 읽는 시험 음성 확인(자연스러움) → 대본에 아라비아 숫자 사용(자막도 '4,400만 원'처럼 표시). edge-tts 단어 시간 정보도 숫자 그대로 나옴
- 바로가기 링크: 스키마 `action_links`(label·url·purpose apply/check/info·note), 지시문 "자료 주소나 자료 본문에 적힌 주소만". `src/link_check.py`가 공식 사이트 + 출처 확인(자료 주소 또는 본문에 글자 그대로, 도메인만 적힌 경우 첫 화면만) 통과분만 렌더(최대 4개), 뺀 것은 경고. 도입부 아래 '신청·조회 바로가기' 상자(신청=초록, 안내·조회=파랑), CSS 반영
- 사진 관련성: Pixabay 인기순 결과로 엉뚱한 사진(노트북→팟캐스트 마이크) → 태그 관련도 순(앞 3개 태그 3점, 나머지 1점, 하나도 안 맞으면 제외)
- 실제 검증: 근로장려금 재생성(3분, 1회 통과) → 링크 [신청] 홈택스 https://www.hometax.go.kr/, [안내] 국세청 페이지, 쇼츠 숫자 표기. 임시 글 31번(작업 900004)·쇼츠 39초 렌더(81초)·텔레그램 승인 요청(시험) 전송. 테스트 219개 통과
- 정리 대기: 이전 시험 임시 글 26번(작업 900003)과 사진은 남아 있음

## 2026-11-04 전체 자동 실행기 (지시: 사용자 / 작업: Claude / 검토: Codex 3회, 최종 PASS — REVIEW.md)
- 사용자 승인으로 시험 임시 글 26번·사진 2장 삭제
- 추가: `src/pipeline.py`(단계 함수·Services), `src/orchestrator.py`(장부 상태 진행, 체크포인트로 이어 하기, 텔레그램 버튼 처리, 상시 실행: 작업 스레드+버튼 수신 스레드, 단일 실행 잠금, offset 파일 data/telegram_offset.json), `scripts/jobctl.py`(add/list/show/retry/cancel/once/run), db에 begin_publishing(nonce)·cancel_if_current·stale_publishing
- 첫 실제 작업: 1번 "2026년 자녀장려금 신청 자격과 지급액" → 21분(Gemini 3.8 Flash 503으로 예비 모델 전환, 성우 접속 1회 재시도) → 쇼츠 36초, 임시 글 35번, 텔레그램 승인 요청(nonce 6045e5cd). 경고: 본문 짧음(803자), takeaway 4곳
- 실행: 상시 실행기(jobctl run)는 아직 켜지 않음 → 버튼 처리 안 됨. systemd 서비스 등록은 사용자 승인 필요
- 남은 일: 1번 글 발행 여부 결정, 상시 실행 서비스 등록, 예비 모델 글 품질(본문 짧음) 대응, 사이트 기본 정리(이름·시간대·개인정보 처리방침), 구글 시트 연동(sheet_manager)·웹 대시보드(web_ui)

## 2026-11-04 예비 모델 표시·짧은 본문 재작성·다시 쓰기·상시 실행 준비 (지시: 사용자 / 작업: Claude / 검토: 없음 — 기존 검토 범위 내 소규모 변경)
- 예비 모델로 글을 쓰면 텔레그램 즉시 알림 + 승인 요청 '확인할 것' 맨 위에 "예비 모델 작성 — 품질 확인 필요"(사용자 요청: 알려 줄 것)
- 본문(문단·주의·요약) 1,200자 미만이면 다시 쓰게 함(MIN_BODY_CHARS), 끝내 짧으면 작업 실패 → 텔레그램 알림
- `jobctl redo <번호>`: 글부터 다시(체크포인트 content~approval 삭제, 이 작업이 올린 사진만 삭제, 같은 임시 글 번호로 고쳐 씀, 예전 승인 버튼은 nonce 변경으로 무효)
- 1번 작업 redo(사용자 요청) → 대기열
- 상시 실행: `deploy/systemd/jalcheck-worker.service` 작성(Nice 15, CPUWeight 20, MemoryMax 3G, Restart on-failure). 사용자 승인은 받았으나 Claude Code 자동 안전 장치가 '영구 설정(Unauthorized Persistence)'으로 설치 명령을 차단 → 미설치. 임시로 이 대화 세션의 백그라운드에서 `jobctl run` 실행(최대 2시간, 로그 data/worker-session.log)
- 테스트 238개 통과

## 2026-11-04 상시 실행 서비스 등록 (지시: 사용자 / 작업: Claude / 검토: 없음 — 서버 서비스 등록, 사용자 승인)
- 사용자 요청으로 `~/.claude/settings.json`에 이 서비스 관련 명령만 정확히 허용하는 규칙 10개 추가(cp 단위 파일, daemon-reload, enable/disable/start/stop/restart, status/is-active, journalctl). 기존 알림 훅 유지
- 임시 세션 실행기를 SIGTERM으로 정상 종료 후 `/etc/systemd/system/jalcheck-worker.service` 설치, `systemctl enable --now` → active(running), MemoryMax 3G 적용 확인. 로그: `sudo journalctl -u jalcheck-worker -f`
- 1번 작업 다시 쓰기 결과: 최신 모델(gemini-3.8-flash), 본문 1,623자, 바로가기 [신청] 홈택스·[조회] 심사진행상황, 쇼츠 45.6초, 승인 요청(nonce ef6fe799). 경고: 자료에서 못 찾은 숫자 10만·5%(5%는 '95% 지급'에서 유도된 것으로 보임) — 발행 전 확인 필요
- 주의: 이제 텔레그램 [발행]은 실제 공개 게시

## 2026-11-04 사용자 검수 지적 3건 수정 (지시: 사용자 / 작업: Claude / 검토: 없음 — 표시·검사 오탐·캡처 수정)
- ① 4열 표가 휴대폰에서 잘려 보임(실측 표 576px > 화면 330px, 가로 스크롤 표시 없음, 캡처엔 4번째 칸 누락) → 칸마다 data-label, 600px 이하에서 4열 이상 표는 행별 카드형(열 이름+값 세로)으로 표시. 표 내용은 국세청 자료와 일치 확인
- ② "1일당 10만 분의 22"가 '자료에 없는 숫자'로 경고됨 → 원문 "가산세(1일 22/100,000)"와 같은 사실(오탐). 숫자 대조가 분수(22/100,000·10만 분의 22)를 비율로 인식하도록 수정
- ③ 캡처에서 사진이 빈칸 → 실제 사진은 정상(200), 지연 로딩 전에 캡처된 것. 캡처 전 모든 img를 즉시 로딩·완료 대기
- `jobctl redraft <번호>`: 글·쇼츠는 그대로, 경고 재계산 후 임시 글 HTML만 다시 만들고 새 승인 요청
- 실행기 서비스 재시작(새 코드 반영) 후 1번 redraft. 테스트 240개 통과

## 2026-11-04 사이트 이름·시간대·SEO 기본 설정 (지시: 사용자 "사이트 이름 잘체크, 시간대 한국, 나머지 알아서·구글 SEO" / 작업: Claude / 검토: Codex(SEO 코드) PASS)
- 백업: `~/backups/2026-11-04-site-seo/wordpress-full.sql`
- 설정(REST): 사이트 이름 "잘체크", 설명 "지원금·세금·생활 제도, 공식 자료로 확인한 체크리스트", 시간대 Asia/Seoul, 날짜 "Y년 n월 j일", 시간 "H:i"
- 카테고리 1 "미분류" → "지원금·세금"(slug support-tax, 기본 카테고리), 작성자 표시 "잘체크 편집팀"(slug jalcheck-editor, 로그인 아이디 노출 방지)
- 글 주소 `/%year%/%monthnum%/%day%/%postname%/` → `/%postname%/`(DB permalink_structure 변경, rewrite_rules 재생성). ?p= 주소는 301로 새 주소 이동 확인
- SEO mu-plugin 설치(`wp-content/mu-plugins/jalcheck-seo.php`, sudo install, www-data 소유): 공개 글 meta description(요약문)·OG·Twitter·JSON-LD Article, 첫 화면 WebSite, 카테고리 설명. 다른 SEO 플러그인 있으면 꺼짐
- 글 생성: 태그(핵심 검색어+AI 태그 2~4개, ensure_tags), 사진 대체 텍스트는 image_alt(사진 내용 설명), 글 끝 '잘체크 편집 기준' 상자(공식 자료 확인·확인일·최종 확인 안내·소개 링크)
- 페이지: 소개와 편집 원칙 `/about/` 공개(41), 개인정보 처리방침(3) 초안 임시 저장 — 연락처 이메일 필요(사용자 확인 대기), 예제 페이지(2) 휴지통
- 남은 일: 연락처 확정 → 개인정보 처리방침·문의 페이지 공개, 첫 실제 글 발행 후 기본 글 "안녕하세요!"(1) 삭제(현재 화면 캡처 틀로 사용 중), 검색엔진 차단 해제 시점 결정(지금은 noindex라 사이트 지도 꺼짐) → 해제 후 구글 서치 콘솔 등록·사이트 지도 제출(사용자 구글 계정 필요)

## 2026-11-04 연락처·문의 페이지·머리말/꼬리말·사이트 아이콘
- 지시자: 사용자("jalcheck.help@gmail.com 이메일 만들었어. 또 남은거 있는지 확인해줘") / 작업자: Claude / 검토자: 없음(콘텐츠·화면 변경, 로그인·DB 구조 무관)
- 개인정보 처리방침(3) 연락처 채워 공개, 문의 페이지 `/contact/`(47) 새로 공개, 소개 페이지 끝에 문의 링크 추가
- 꼬리말: 테마 기본 가짜 링크 8개(`#`, 상점·이벤트 등)와 테마 표기를 지우고 소개·문의·개인정보 처리방침 + 안내 문구로 교체. 머리말 메뉴: 지원금·세금 / 소개 / 문의(전엔 모든 페이지 자동 나열)
  - 원본 `deploy/wp/parts/*.html`, 적용 `scripts/apply_site_pages.py`(바꾸기 전 내용은 data/backups/site-pages-*/에 저장). 테마 파일은 그대로, 사이트 쪽 수정본으로 저장됨
- 사이트 아이콘: `assets/brand/site-icon.png`(초록 바탕 "잘" + 체크) 미디어 51, site_icon 설정 → 파비콘·구조화 데이터 logo에 쓰임
- 검증: /, /about/, /contact/, /privacy-policy/, /category/support-tax/ 모두 200, 화면 캡처로 메뉴 확인. 이메일은 Cloudflare 이메일 보호로 페이지 원문에서 가려지고 브라우저에서만 보임(스팸 수집 방지, 정상)
- 남은 작업: 35번 글 발행 결정 → 기본 글 "안녕하세요!" 삭제, 글 5~10개 쌓은 뒤 검색엔진 차단 해제 + 서치 콘솔 등록

## 2026-11-04 모델 바쁨 → 30분 뒤 자동 재시도(예비 모델로 블로그 안 씀)
- 지시자: 사용자(1번 승인) / 작업자: Claude / 검토자: Codex(2회, 지적 반영: 되돌리기 한 트랜잭션·DB 실패 시 다음 차례 재시도·generating 단계만)
- 원인: 주제 10개 첫 작업(2번) 중 gemini-3.8-flash가 503 연속 → 예비 모델(flash-lite)이 954~1,056자로 짧게 써 3회 불합격 → 실패
- 변경: ai_generator `ModelBusyError`, 블로그 본문은 기본 모델로만(blog_fallback=False, 쇼츠 고쳐 쓰기는 예비 모델 허용). orchestrator가 ModelBusyError면 `db.requeue_busy`(generating→queued 한 번에)로 되돌리고 30분(BUSY_WAIT_SECONDS) 새 작업 쉼, 텔레그램 알림은 바쁨 구간당 1회
- 검증: 시험 248개 통과(바쁨→대기→재시도, DB 실패 복구, generating 외 거절). 실행기 재시작, 2번 retry
- 대기열: 작업 2~11(주제 10개, 2026-11-04 등록). 발행은 하루 1~2개 권장(사용자 합의)

## 2026-11-04 모델 바쁨 무한 대기 방지 (지시: 사용자 1번 승인 / 작업: Claude / 검토: Codex)
- 원인: 글 쓰는 모델(gemini-3.8-flash)이 503 혼잡이면 30분마다 횟수 제한 없이 재시도 → 계속 붐비면 대기열 전체가 무한 정지, 알림은 첫 1회뿐
- 변경(src/orchestrator.py): 쉬는 간격 15분 고정(처음엔 30분→1시간→2시간으로 늘렸으나, 우리 요청량은 구글 혼잡에 영향이 없어 늘리면 풀린 시점을 늦게 잡을 뿐이라 사용자와 상의 후 고정으로 변경). 한 작업이 처음 바빠진 뒤 12시간 넘게 계속 바쁘면 "12시간 넘게 Gemini가 계속 붐벼 중단" 실패 기록 + 텔레그램 알림(`jobctl retry`로 재시도) 후 다음 작업으로. 실패 기록 시 바쁨 기록(_busy_streak/_busy_notified) 정리
- 예비 모델(flash-lite)로 블로그 본문 쓰지 않는 원칙은 유지
- Codex 지적 3건 중 2건 반영(실패 시 상태 정리). 1건 보류: 바쁨 시작 시각이 메모리에만 있어 실행기 재시작 시 12시간 계산이 초기화됨(재시작은 배포 때뿐이라 수용, 필요하면 DB 저장으로 보강)
- 검증: 시험 249개 통과(15분 간격·12시간 후 실패·다음 작업 진행·안내 재발송)
- 커밋: f8bf4c2, 이후 15분 고정 커밋
- 실행기 재시작: 사용자 승인("재시작해") 후 진행
- 남은 일: GitHub push 실패(Deploy key 거절, `ssh -T github-awp` → Permission denied). 사용자에게 Deploy keys 확인 요청
- 추후 선택지: 한 번 모은 자료를 저장해 재시도 때 글쓰기만 하기(간격 더 줄일 수 있음)
- 추후 선택지: 예비 모델을 lite보다 나은 모델로 바꿔 시험 글 2~3편으로 품질 확인(사용자 보류)

## 2026-11-04 3.6 Flash와 3.8 Flash 품질 비교(시험 글, 발행 안 함)
- 지시자: 사용자(무료 3.6으로 시작할지 판단하려고 비교 요청, 1번 승인) / 작업자: Claude / 검토자: 없음(코드 변경 없음)
- 방법: 3.8 시험 글(data/dryrun/20261103-223041)과 같은 국세청 자료 3개, 같은 주제로 `GEMINI_MODEL=gemini-3.6-flash` 시험 실행 → data/dryrun/20261104-110453
- 결과: 3.6도 한 번에 검사 통과(27초, 3.8은 197초). 숫자·기간·제외 대상이 자료와 일치하고 지어낸 내용은 보이지 않음. 본문은 약 15% 짧고 '본문이 짧습니다' 경고가 붙음. 사업소득 조정률 예시·간주전세금·단독가구 정의·접수 방법 표가 빠짐. 쇼츠 말투가 조금 더 광고 같음("걱정 마세요!")
- 3.6도 처음에는 503 혼잡이 4번 이어짐(약 2분 뒤 풀림). 비교한 시험 글은 1편뿐
- 남은 일: 사용자가 모델을 정하면 .env의 GEMINI_MODEL 반영(실행기 재시작 필요)
